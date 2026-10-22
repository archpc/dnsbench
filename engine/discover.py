#!/usr/bin/env python3
"""DISCOVERY operation: master candidate list -> liveness -> screening -> pool.json.
Expensive (~62k liveness probes + screening of ~6.7k survivors). Rerun only when the
pool is stale (weeks) or explicitly wanted. Both stages checkpoint (resume on rerun);
delete the checkpoint files for a genuinely fresh screen (Pitfall #14).

Usage (run from a scratch dir holding outputs):
    ulimit -n 10240 && python3 discover.py

Inputs:  master-ipv4.txt (fetched if missing)
Outputs: live-phase1.jsonl, phase2b-results.jsonl, phase2b-perquery.jsonl, pool.json
"""
import json
import random
import socket
import statistics
import sys
import threading
import time
import urllib.request

sys.path.insert(0, __import__('os').path.dirname(__file__))
from dnslib import QidGen, q_validated, rand_label, TIMEOUT  # noqa: E402

WORKERS_LIVE = 256
WORKERS_SCREEN = 256
MASTER_URLS = [
    "https://public-dns.info/nameservers.txt",
    "https://raw.githubusercontent.com/blacklanternsecurity/public-dns-servers/master/nameservers.txt",
]
REFS = ["1.0.0.1", "8.8.8.8", "9.9.9.9", "208.67.222.222"]
POPULAR = ["google.com", "wikipedia.org", "github.com", "apple.com", "cloudflare.com",
           "amazon.com", "microsoft.com", "reddit.com", "nytimes.com", "mit.edu"]
CACHE_SET = POPULAR[:6]
N_UNCACHED = 6
N_CACHED = 6
N_HIJACK = 2

qid = QidGen(1000)


def fetch_master(path="master-ipv4.txt"):
    import re
    seen = set()
    for url in MASTER_URLS:
        raw = urllib.request.urlopen(url, timeout=60).read().decode()
        for line in raw.splitlines():
            ip = line.strip()
            if re.fullmatch(r'(?:\d{1,3}\.){3}\d{1,3}', ip):
                if all(int(o) <= 255 for o in ip.split('.')):
                    seen.add(ip)
    seen.update(REFS)
    open(path, "w").write('\n'.join(sorted(seen)) + '\n')
    return len(seen)


def load_checkpoint(path):
    """Records of an append-only JSONL checkpoint. A torn final line (process
    killed mid-write) is truncated off, so the record is simply re-probed and
    the next append starts on a fresh line. Raises FileNotFoundError."""
    with open(path, "rb") as f:
        data = f.read()
    good_end = data.rfind(b"\n") + 1
    if good_end != len(data):
        with open(path, "r+b") as f:
            f.truncate(good_end)
    out = []
    for line in data[:good_end].splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def liveness():
    """Stage 1: one A query per candidate; checkpointed, resumable."""
    ips = [l.strip() for l in open("master-ipv4.txt") if l.strip()]
    done = set()
    try:
        done = {r["ip"] for r in load_checkpoint("live-phase1.jsonl")}
    except FileNotFoundError:
        pass
    todo = [ip for ip in ips if ip not in done]
    print(f"liveness: master={len(ips)} done={len(done)} todo={len(todo)}", flush=True)
    fw = open("live-phase1.jsonl", "a")

    def probe(ip):
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(TIMEOUT)
            ms, rc, an, st = q_validated(s, ip, "www.google.com", qid.next())
            return {"ip": ip, "ms": round(ms, 1) if rc != -2 else None,
                    "rcode": rc, "an": an, "alive": rc == 0 and an > 0,
                    "strays": st}
        except OSError as e:
            return {"ip": ip, "ms": None, "rcode": -2, "an": 0, "alive": False,
                    "err": type(e).__name__}
        finally:
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass

    lock = threading.Lock()
    counts = {"n": 0, "alive": 0}
    t0 = time.time()

    def worker(chunk):
        for ip in chunk:
            r = probe(ip)
            with lock:
                fw.write(json.dumps(r) + "\n")
                counts["n"] += 1
                counts["alive"] += r["alive"]
                if counts["n"] % 2000 == 0:
                    fw.flush()
                    rate = counts["n"] / (time.time() - t0)
                    print(f"liveness {counts['n']}/{len(todo)} alive={counts['alive']} "
                          f"{rate:.0f}/s eta={(len(todo)-counts['n'])/max(rate,0.01)/60:.1f}min",
                          flush=True)

    random.shuffle(todo)
    chunks = [todo[i::WORKERS_LIVE] for i in range(WORKERS_LIVE)]
    threads = [threading.Thread(target=worker, args=(c,)) for c in chunks if c]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    fw.close()
    alive = sum(1 for r in load_checkpoint("live-phase1.jsonl") if r["alive"])
    print(f"LIVENESS COMPLETE: {alive} alive", flush=True)


def screening():
    """Stage 2: corrected screening (NXDOMAIN valid, hijack exclusion, per-query log)."""
    alive = [r["ip"] for r in load_checkpoint("live-phase1.jsonl") if r["alive"]]
    done = set()
    try:
        done = {r["ip"] for r in load_checkpoint("phase2b-results.jsonl")}
    except FileNotFoundError:
        pass
    todo = [ip for ip in alive if ip not in done]
    todo += [r for r in REFS if r not in done and r not in todo]
    print(f"screening: alive={len(alive)} done={len(done)} todo={len(todo)}", flush=True)
    fw = open("phase2b-results.jsonl", "a")
    fq = open("phase2b-perquery.jsonl", "a")
    plock = threading.Lock()

    def characterize(ip):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(TIMEOUT)
        except OSError as e:
            return {"ip": ip, "error": repr(e)}
        r = {"ip": ip, "uncached": [], "cached": [], "to_u": 0, "to_c": 0,
             "servfail_u": 0, "refused_u": 0, "other_u": 0, "hijack": 0,
             "nxdomain_ok": 0, "nxdomain_noerror": 0, "strays": 0}

        def rec(kind, name, ms, rc, an, st):
            r["strays"] += st
            with plock:
                fq.write(json.dumps({"ip": ip, "kind": kind, "name": name,
                                     "ms": round(ms, 1), "rcode": rc, "an": an}) + "\n")

        ms, rc, an, st = q_validated(s, ip, "www.google.com", qid.next())
        rec("liveness", "www.google.com", ms, rc, an, st)
        if not (rc == 0 and an > 0):
            s.close()
            r["dead"] = True
            return r
        for i in range(N_UNCACHED):
            name = f"{rand_label()}.{POPULAR[i % len(POPULAR)]}"
            ms, rc, an, st = q_validated(s, ip, name, qid.next())
            rec("uncached", name, ms, rc, an, st)
            if rc == -2:
                r["to_u"] += 1
            else:
                r["uncached"].append(round(ms, 1))
                if rc == 2:
                    r["servfail_u"] += 1
                elif rc == 5:
                    r["refused_u"] += 1
                elif rc not in (0, 3):
                    r["other_u"] += 1
        for d in CACHE_SET:
            ms, rc, an, st = q_validated(s, ip, d, qid.next())
            rec("prime", d, ms, rc, an, st)
        for i in range(N_CACHED):
            d = CACHE_SET[i % len(CACHE_SET)]
            ms, rc, an, st = q_validated(s, ip, d, qid.next())
            rec("cached", d, ms, rc, an, st)
            if rc == -2 or not (rc == 0 and an > 0):
                r["to_c"] += 1
            else:
                r["cached"].append(round(ms, 1))
        for _ in range(N_HIJACK):
            name = f"{rand_label()}.{rand_label()}.com"
            ms, rc, an, st = q_validated(s, ip, name, qid.next())
            rec("nxdomain", name, ms, rc, an, st)
            if rc == 0 and an > 0:
                r["hijack"] += 1
            elif rc == 3:
                r["nxdomain_ok"] += 1
            elif rc == 0 and an == 0:
                r["nxdomain_noerror"] += 1
        s.close()
        return r

    fw_lock = threading.Lock()
    counts = {"n": 0}
    t0 = time.time()

    def worker(chunk):
        for ip in chunk:
            try:
                r = characterize(ip)
            except Exception as e:
                r = {"ip": ip, "error": repr(e)}
            with fw_lock:
                fw.write(json.dumps(r) + "\n")
                counts["n"] += 1
                if counts["n"] % 500 == 0:
                    fw.flush()
                    fq.flush()
                    rate = counts["n"] / (time.time() - t0)
                    print(f"screening {counts['n']}/{len(todo)} {rate:.0f}/s "
                          f"eta={(len(todo)-counts['n'])/max(rate,0.01)/60:.1f}min", flush=True)

    random.shuffle(todo)
    chunks = [todo[i::WORKERS_SCREEN] for i in range(WORKERS_SCREEN)]
    threads = [threading.Thread(target=worker, args=(c,)) for c in chunks if c]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    fw.close()
    fq.close()
    print("SCREENING COMPLETE", flush=True)


def emit_pool(path="pool.json"):
    """Stage 3: score screened results into the persistent ranked pool."""
    rows = []
    hijacked = 0
    for line in open("phase2b-results.jsonl"):
        r = json.loads(line)
        if "error" in r:
            continue
        if r["hijack"] > 0:
            # hijack check FIRST: a hijacker is a hijacker even if it also
            # timed out or returned no uncached samples; must be counted and
            # never silently fall into another exclusion bucket (validated:
            # 113.177.27.140 was dropped as no-uncached instead of counted).
            hijacked += 1
            continue
        if r.get("dead") or not r["uncached"]:
            continue
        u_med = statistics.median(r["uncached"])
        c_med = statistics.median(r["cached"]) if r["cached"] else None
        score = u_med + (0.3 * c_med if c_med else 150) \
            + 40 * (r["servfail_u"] + r["refused_u"] + r["other_u"]) \
            + 30 * (r["to_u"] + r["to_c"])
        rows.append({"ip": r["ip"], "u_med": round(u_med, 1),
                     "c_med": round(c_med, 1) if c_med else None,
                     "rel_u": round(100.0 * len(r["uncached"]) /
                                    (len(r["uncached"]) + r["to_u"]), 1),
                     "rel_c": round(100.0 * len(r["cached"]) /
                                    (len(r["cached"]) + r["to_c"]), 1),
                     "sf": r["servfail_u"], "rf": r["refused_u"],
                     "to_u": r["to_u"], "to_c": r["to_c"],
                     "strays": r.get("strays", 0), "score": round(score, 1)})
    rows.sort(key=lambda x: x["score"])
    import datetime
    meta = {"generated": datetime.datetime.now().isoformat(timespec="seconds"),
            "eligible": len(rows), "hijacked_excluded": hijacked}
    json.dump({"meta": meta, "pool": rows}, open(path, "w"), indent=1)
    print(f"POOL COMPLETE: {meta}", flush=True)


if __name__ == "__main__":
    t0 = time.time()
    import os
    passed, total = __import__('dnslib').selftest()
    print(f"parser selftest: {passed}/{total}", flush=True)
    if passed != total:
        sys.exit("selftest failed - aborting")
    if not os.path.exists("master-ipv4.txt"):
        print(f"master list: {fetch_master()} candidates", flush=True)
    liveness()
    screening()
    emit_pool()
    print(f"DISCOVERY COMPLETE in {(time.time()-t0)/60:.1f} min", flush=True)
