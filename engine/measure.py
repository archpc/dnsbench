#!/usr/bin/env python3
"""MEASUREMENT operation: deep-bench the top N resolvers from pool.json.
Cheap (~3-5 min for 150 servers, scalable with --n). Run any time against the
persistent pool. Each invocation writes its own timestamped run dir, so nothing
is ever silently reused (Pitfall #14 does not apply to measurements).

Usage (run from a scratch dir containing pool.json):
    ulimit -n 10240 && python3 measure.py [--pool pool.json] [--n 150] [--runs runs]

Outputs (runs/<UTC-timestamp>/):
    deepbench-results.jsonl   per-server aggregates
    deepbench-perquery.jsonl  full audit trail incl. prime records
    ranked.json               ranked + PTR-labeled, completeness flags
    custom-list-top50.txt     bare top-50 IPs
    DNSBench-custom-list.txt  annotated final artifact
"""
import argparse
import json
import random
import socket
import statistics
import sys
import threading
import time

sys.path.insert(0, __import__('os').path.dirname(__file__))
from dnslib import (QidGen, SO_TIMESTAMP, q_validated, rand_label, selftest,
                    TIMEOUT)  # noqa: E402
import dnslib2  # noqa: E402

WORKERS = 60
N_UNCACHED = 50
N_CACHED = 50
FORCED_REFS = ["1.0.0.1", "8.8.8.8", "9.9.9.9", "208.67.222.222"]
POPULAR = ["google.com", "wikipedia.org", "github.com", "apple.com", "cloudflare.com",
           "amazon.com", "microsoft.com", "reddit.com", "nytimes.com", "mit.edu",
           "yahoo.com", "ebay.com", "netflix.com", "bbc.co.uk", "wordpress.com"]

qid = QidGen(100)


def deep(ip, fw, flock):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(TIMEOUT)
        s.setsockopt(socket.SOL_SOCKET, SO_TIMESTAMP, 1)  # kernel rx timestamps
    except OSError as e:
        return {"ip": ip, "error": repr(e)}
    r = {"ip": ip, "uncached": [], "cached": [], "to_u": 0, "to_c": 0, "fail_u": 0,
         "bad_c": 0, "strays": 0, "kdeltas": []}

    def rec(kind, name, ms, rc, an, st, kd=None):
        r["strays"] += st
        entry = {"ip": ip, "kind": kind, "name": name,
                 "ms": round(ms, 1), "rcode": rc, "an": an}
        if kd is not None:
            entry["kd"] = round(kd, 3)
        with flock:
            fw.write(json.dumps(entry) + "\n")

    def probe(kind, name):
        kd = []
        ms, rc, an, st = q_validated(s, ip, name, qid.next(), kdelta=kd)
        kd_ms = kd[0] if kd else None
        rec(kind, name, ms, rc, an, st, kd_ms)
        return ms, rc, an, kd_ms

    ms, rc, an, _ = probe("liveness", "www.google.com")
    if not (rc == 0 and an > 0):
        s.close()
        r["dead"] = True
        return r
    for i in range(N_UNCACHED):
        name = f"{rand_label()}.{POPULAR[i % len(POPULAR)]}"
        ms, rc, an, kd = probe("uncached", name)
        if rc == -2:
            r["to_u"] += 1
        elif rc in (0, 3):  # NOERROR / NXDOMAIN: a real resolution
            r["uncached"].append(round(ms, 1))
            if kd is not None:
                r["kdeltas"].append(kd)
        else:  # SERVFAIL, REFUSED, other: failure, never a latency sample
            r["fail_u"] += 1
    for d in POPULAR:
        probe("prime", d)
    for i in range(N_CACHED):
        d = POPULAR[i % len(POPULAR)]
        ms, rc, an, kd = probe("cached", d)
        if rc == -2:
            r["to_c"] += 1
        elif rc == 0 and an > 0:
            r["cached"].append(round(ms, 1))
            if kd is not None:
                r["kdeltas"].append(kd)
        else:
            r["bad_c"] += 1  # zero-answer NOERROR etc.: recorded, never silently dropped
            if kd is not None:
                r["kdeltas"].append(kd)
    s.close()
    return r


def build_tasks(ip):
    """Ordered per-server task list: liveness -> uncached -> primes -> cached."""
    tasks = [("liveness", "www.google.com")]
    tasks += [("uncached", f"{rand_label()}.{POPULAR[i % len(POPULAR)]}")
              for i in range(N_UNCACHED)]
    tasks += [("prime", d) for d in POPULAR]
    tasks += [("cached", POPULAR[i % len(POPULAR)]) for i in range(N_CACHED)]
    return (ip, tasks)


def deep_all_kqueue(ips, fw, netsvc=None, cancel=None):
    """Kqueue engine: all servers in one event loop, probes interleaved across
    servers (<=1 outstanding per server). Same task order and semantics as
    deep(); records additionally carry "unreach" and per-query "kd"."""
    specs = [build_tasks(ip) for ip in ips]
    return dnslib2.run_interleaved(specs, fw, timeout=TIMEOUT, netsvc=netsvc,
                                   cancel=cancel)


def provider_label(ip):
    try:
        return socket.gethostbyaddr(ip)[0].rstrip(".")
    except Exception:
        return "?"


def categorize(ip, ptr):
    n = ptr.lower()
    labels = set(n.split("."))
    if "akamai" in n:
        return "Akamai"
    if n == "?":
        if ip.startswith("172.64."):
            return "Cloudflare"
        if ip.startswith("76.76."):
            return "ControlD"
        return "other-noPTR"
    if "ntt" in labels:
        return "NTT"
    if "gblx" in labels:
        return "Level3/Savvis"
    if "windstream" in n or "paetec" in n or "dejazzd" in n:
        return "Windstream-family"
    if "verisign" in n or "vercara" in n:
        return "Verisign"
    if "quad9" in n:
        return "Quad9"
    if "opendns" in n or "cisco" in n:
        return "OpenDNS"
    if "google" in n:
        return "Google"
    if "cloudflare" in n:
        return "Cloudflare"
    return "other:" + n[:28]


def rank(results, outdir):
    from collections import Counter
    import datetime
    good = []
    for r in results:
        if r.get("dead") or "error" in r or not r["uncached"] or not r["cached"]:
            continue
        u, c = r["uncached"], r["cached"]
        kd = r.get("kdeltas") or []
        good.append({"ip": r["ip"],
                     "u_med": round(statistics.median(u), 1),
                     "c_med": round(statistics.median(c), 1),
                     "u_p90": round(sorted(u)[int(0.9 * len(u))], 1),
                     "u_max": max(u),
                     "kd_med": round(statistics.median(kd), 3) if kd else None,
                     "to_u": r["to_u"], "to_c": r["to_c"], "bad_c": r["bad_c"],
                     "fail_u": r.get("fail_u", 0),
                     "unreach": r.get("unreach", 0),
                     "strays": r["strays"]})
    good.sort(key=lambda x: (x["u_med"], x["c_med"]))
    for g in good:
        ptr = provider_label(g["ip"])
        g["ptr"] = ptr
        g["category"] = categorize(g["ip"], ptr)
    incomplete = [g["ip"] for g in good if g["to_c"] + g["bad_c"] > 0]
    cats = Counter(g["category"] for g in good[:50])
    json.dump({"generated": datetime.datetime.now(datetime.timezone.utc)
               .strftime("%Y%m%dT%H%M%SZ"),
               "n_deepbenched": len(results),
               "n_ranked": len(good),
               "incomplete_cached": incomplete,
               "ranked": good}, open(f"{outdir}/ranked.json", "w"), indent=1)
    open(f"{outdir}/custom-list-top50.txt", "w").write(
        "\n".join(g["ip"] for g in good[:50]) + "\n")

    hdr = (f"{'rk':>3} {'ip':>16} {'u_med':>6} {'u_p90':>6} {'c_med':>6} "
           f"{'toU':>3} {'toC':>3} {'badC':>4} {'str':>4}  provider")
    lines = [hdr]
    for i, g in enumerate(good[:50], 1):
        lines.append(f"{i:>3} {g['ip']:>16} {g['u_med']:>6} {g['u_p90']:>6} {g['c_med']:>6} "
                     f"{g['to_u']:>3} {g['to_c']:>3} {g['bad_c']:>4} {g['strays']:>4}  "
                     f"{g['ptr']}")
    engine = results[0].get("engine", "?") if results else "?"
    kds = sorted(d for r in results for d in (r.get("kdeltas") or []))
    unreach = sum(r.get("unreach", 0) for r in results)
    meta = (f"# run {outid(outdir)} | deep-benched {len(results)}, ranked {len(good)}\n"
            f"# incomplete cached samples: {len(incomplete)} servers"
            f"{(' -> ' + ', '.join(incomplete[:8])) if incomplete else ''}\n"
            f"# strays={sum(r['strays'] for r in results)} "
            f"to_u={sum(r['to_u'] for r in results)} to_c={sum(r['to_c'] for r in results)} "
            f"fail_u={sum(r.get('fail_u', 0) for r in results)} "
            f"unreach={unreach}\n"
            f"# composition (recounted from stored PTR): {dict(cats)}\n")
    if kds:
        meta += (f"# engine={engine} netsvc={results[0].get('netsvc', 'off')} "
                 f"kdelta(kernel->user) med={statistics.median(kds):.3f}ms "
                 f"p90={kds[int(0.9 * len(kds))]:.3f}ms n={len(kds)}\n")
    else:
        meta += f"# engine={engine} netsvc={results[0].get('netsvc', 'off') if results else '?'} (no kernel timestamps)\n"
    with open(f"{outdir}/DNSBench-custom-list.txt", "w") as f:
        f.write(meta + "\n" + "\n".join(lines) + "\n")
    print(meta.rstrip())
    print(f"ranked {len(good)} -> {outdir}")


def outid(outdir):
    return outdir.rsplit("/", 1)[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="pool.json")
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--runs", default="runs")
    ap.add_argument("--engine", choices=["thread", "kqueue"], default="kqueue",
                    help="thread = legacy thread-per-server engine (baseline); "
                         "kqueue = single-thread event-loop engine with probe "
                         "interleaving, QoS steering, GC control")
    ap.add_argument("--netsvc", choices=["off", "be", "sig", "vo"], default="off",
                    help="SO_NET_SERVICE_TYPE marking; SIDE EXPERIMENTS ONLY")
    ap.add_argument("--windows", type=int, default=1,
                    help="independent measurement windows run back-to-back; "
                         "each window gets its own run dir (order re-shuffled "
                         "per window); manifest runs/windows-<ts>.json written "
                         "for windows > 1; aggregate with stability.py --aggregate")
    a = ap.parse_args()
    if a.windows < 1:
        sys.exit("--windows must be >= 1")

    passed, total = selftest()
    print(f"parser selftest: {passed}/{total}", flush=True)
    if passed != total:
        sys.exit("selftest failed - aborting")

    pool = json.load(open(a.pool))["pool"]
    base_ips = [r["ip"] for r in pool[:a.n]]
    for ref in FORCED_REFS:
        if ref not in base_ips:
            base_ips.append(ref)
    import datetime
    import os
    netsvc = None if a.netsvc == "off" else dnslib2.NET_SERVICE_TYPE[a.netsvc]
    dirs = []
    for w in range(a.windows):
        ips = list(base_ips)
        random.shuffle(ips)  # fresh order per window (independent windows)
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        outdir = f"{a.runs}/{ts}" if a.windows == 1 else f"{a.runs}/{ts}_w{w + 1}"
        os.makedirs(outdir, exist_ok=True)
        dirs.append(outdir)
        print(f"deep benching {len(ips)} servers ({a.n} pool + {len(FORCED_REFS)} refs, "
              f"interleaved, engine={a.engine}, netsvc={a.netsvc}, "
              f"window {w + 1}/{a.windows}) -> {outdir}", flush=True)

        fw = open(f"{outdir}/deepbench-perquery.jsonl", "w")
        results = []
        t0 = time.time()
        if a.engine == "kqueue":
            try:
                dnslib2.set_qos_user_interactive()
                print("QoS: user-interactive set (P-cluster steer)", flush=True)
            except OSError as e:
                print(f"WARNING: QoS steer failed ({e}); running unsteered", flush=True)
            results = deep_all_kqueue(ips, fw, netsvc=netsvc)
        else:
            flock = threading.Lock()
            rl = threading.Lock()

            def worker(chunk):
                for ip in chunk:
                    r = deep(ip, fw, flock)
                    with rl:
                        results.append(r)

            chunks = [ips[i::WORKERS] for i in range(WORKERS)]
            threads = [threading.Thread(target=worker, args=(c,)) for c in chunks if c]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        fw.close()
        for r in results:
            r["engine"] = a.engine
            r["netsvc"] = a.netsvc
        with open(f"{outdir}/deepbench-results.jsonl", "w") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")
        print(f"DEEP BENCH COMPLETE in {time.time()-t0:.0f}s", flush=True)
        rank(results, outdir)
    if a.windows > 1:
        manifest = f"{a.runs}/windows-{os.path.basename(dirs[0]).rsplit('_w', 1)[0]}.json"
        json.dump({"windows": dirs, "engine": a.engine, "netsvc": a.netsvc,
                   "n_requested": a.n,
                   "generated": datetime.datetime.now(datetime.timezone.utc)
                   .strftime("%Y-%m-%dT%H:%M:%SZ")},
                  open(manifest, "w"), indent=1)
        print(f"windows manifest -> {manifest}", flush=True)


if __name__ == "__main__":
    main()
