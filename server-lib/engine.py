#!/usr/bin/env python3
"""Engine adapter: the ONLY module that touches the validated dns-benchmark skill.

Isolation guarantees (per user directive: preserve the validated engine):
- imports the skill scripts (dnslib/discover/measure/stability) as-is
- never mutates skill files; configuration changes go through dnslib module attrs
- every run gets an isolated directory under RUNS/<UTC-timestamp>/ (never deletes
  checkpoints; fresh screening COPIES checkpoints aside into the run dir instead
  of deleting them, satisfying 'never delete checkpoints to restart')
- enrichment helpers (pool emission from copied screening data) replicate the
  skill's documented scoring EXACTLY (formula copied from discover.emit_pool)
"""
import io
import json
import os
import re
import shutil
import statistics
import sys
import threading
import time

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_SCRIPTS = os.path.expanduser(
    "~/.hermes/skills/macos-diagnostics/dns-benchmark/scripts")
if SKILL_SCRIPTS not in sys.path:
    sys.path.insert(0, SKILL_SCRIPTS)

import dnslib   # noqa: E402
import discover  # noqa: E402
import measure   # noqa: E402

RUNS = os.path.join(APP_DIR, "datasets", "runs")
DATASETS = os.path.join(APP_DIR, "datasets")
SCRATCH = "/Users/sein/.hermes/cache/scratch"


class Tee(io.TextIOBase):
    """Forward discover.py progress lines to a callback."""

    def __init__(self, on_progress):
        self.on_progress = on_progress

    def write(self, s):
        line = s.strip()
        if not line:
            return len(s)
        m = re.match(r"^(?:liveness|screening) (\d+)/(\d+)(?:\s|$)", line)
        if m:
            done, total = int(m.group(1)), int(m.group(2))
            if line.startswith("liveness"):
                alive = None
                am = re.search(r"alive=(\d+)", line)
                if am:
                    alive = int(am.group(1))
                self.on_progress("liveness", done, total, alive)
            else:
                self.on_progress("screening", done, total, None)
        return len(s)

    def flush(self):
        pass


def ensure_datasets():
    """Copy skill scratch artifacts into datasets/ once. Originals preserved."""
    os.makedirs(DATASETS, exist_ok=True)
    os.makedirs(RUNS, exist_ok=True)
    copied = []
    for name in ("master-ipv4.txt", "live-phase1.jsonl", "phase2b-results.jsonl",
                 "phase2b-perquery.jsonl", "pool.json", "phase3c-results.jsonl"):
        src = os.path.join(SCRATCH, name)
        dst = os.path.join(DATASETS, name)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
            copied.append(name)
    if os.path.isdir(os.path.join(SCRATCH, "runs")):
        for ts in os.listdir(os.path.join(SCRATCH, "runs")):
            s = os.path.join(SCRATCH, "runs", ts)
            d = os.path.join(RUNS, ts)
            if os.path.isdir(s) and not os.path.exists(d):
                shutil.copytree(s, d)
                copied.append(f"runs/{ts}")
    return copied


def new_run_dir():
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    d = os.path.join(RUNS, ts)
    if os.path.exists(d):  # collision protection beyond one-second timestamps
        k = 2
        while os.path.exists(f"{d}-{k}"):
            k += 1
        d, ts = f"{d}-{k}", f"{ts}-{k}"
    os.makedirs(d, exist_ok=True)
    return d, ts


def preserve_checkpoints(run_dir):
    """Snapshot current screening/pool state into the run dir (audit trail)."""
    for name in ("pool.json", "phase2b-results.jsonl"):
        src = os.path.join(DATASETS, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(run_dir, f"checkpoint-{name}"))


def selftest():
    return dnslib.selftest()


def set_timeout(t):
    """Reconfigure the engine timeout (module attr; read at call time by dnslib)."""
    dnslib.TIMEOUT = float(t)
    measure.TIMEOUT = float(t)


def _emit_pool_from_screening(run_dir, on_progress):
    """Score phase2b-results.jsonl exactly like discover.emit_pool -> pool.json.

    Discovery's own scoring logic is documented in the skill (Pitfall history);
    formula copied verbatim from discover.emit_pool so numbers match the CLI.
    Writes pool.json in the RUN dir (isolated), promoting to datasets/ only after
    a completed fresh screen.
    """
    rows = []
    hijacked = 0
    src = os.path.join(run_dir, "phase2b-results.jsonl")
    with open(src) as f:
        for line in f:
            r = json.loads(line)
            if "error" in r or r.get("dead") or not r["uncached"]:
                continue
            if r["hijack"] > 0:
                hijacked += 1
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
    meta = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "eligible": len(rows), "hijacked_excluded": hijacked}
    json.dump({"meta": meta, "pool": rows},
              open(os.path.join(run_dir, "pool.json"), "w"), indent=1)


def run_discovery(mode, on_progress, cancel_event):
    """Full liveness+screening inside an isolated run dir.

    mode: "fresh" = copy-aside checkpoints then re-screen all alive candidates;
          "auto"  = screen only what's missing (reuse datasets/ checkpoints as-is).
    Returns (pool, run_dir). Never deletes any checkpoint file.
    """
    run_dir, _ = new_run_dir()
    preserve_checkpoints(run_dir)
    old_stdout = sys.stdout
    sys.stdout = Tee(on_progress)
    try:
        # discover.py works on cwd-relative files; chdir into datasets for liveness
        # (which needs master-ipv4.txt + live-phase1.jsonl), then into run_dir for
        # screening output so every fresh screen leaves its own audit trail.
        os.chdir(DATASETS)
        if not os.path.exists("master-ipv4.txt"):
            discover.fetch_master()
        discover.liveness()
        if cancel_event.is_set():
            return None, run_dir
        # screening: run in the isolated run dir. discover.screening() reads
        # live-phase1.jsonl from cwd, so symlink datasets liveness data in.
        os.chdir(run_dir)
        for name in ("live-phase1.jsonl", "master-ipv4.txt"):
            src = os.path.join(DATASETS, name)
            if os.path.exists(src) and not os.path.exists(name):
                os.symlink(src, name)
        discover.screening()
        if cancel_event.is_set():
            # Cancelled during screening: emit nothing, promote nothing.
            return None, run_dir
        _emit_pool_from_screening(run_dir, on_progress)
        main_pool = os.path.join(DATASETS, "pool.json")
        if mode == "fresh" or not os.path.exists(main_pool):
            # fresh: always promote (additive update, old files preserved by
            # timestamp inside run_dir). auto: promote only when bootstrapping
            # a missing main pool, so auto refreshes never clobber dataset state.
            for name in ("pool.json", "phase2b-results.jsonl",
                         "phase2b-perquery.jsonl"):
                if os.path.exists(name):
                    shutil.copy2(name, os.path.join(DATASETS, name))
        pool = json.load(open(os.path.join(run_dir, "pool.json")))
        return pool, run_dir
    finally:
        sys.stdout = old_stdout
        os.chdir(APP_DIR)


def run_measure(pool_path, n, workers, on_server, cancel_event, run_dir=None):
    """Deep-bench top n of pool; identical logic to measure.main minus argparse.

    Writes all outputs into run_dir (new isolated dir if not given).
    Calls on_server(dict) per completed server (includes idx for viz).
    Returns (ranked, run_dir).
    """
    if run_dir is None:
        run_dir, _ = new_run_dir()
    os.makedirs(run_dir, exist_ok=True)
    preserve_checkpoints(run_dir)
    pool = json.load(open(pool_path))
    ips = [r["ip"] for r in pool["pool"][:n]]
    for ref in measure.FORCED_REFS:
        if ref not in ips:
            ips.append(ref)
    import random
    random.shuffle(ips)  # interleave refs with finalists (Pitfall #12)
    # master index for viz point updates
    ip_idx = {ip: i for i, ip in enumerate(master_ips())}
    fw = open(os.path.join(run_dir, "deepbench-perquery.jsonl"), "w")

    class AuditWriter:
        """Per-query audit: skill format (one JSON per line) + progress counter."""

        def __init__(self):
            self.lock = threading.Lock()
            self.count = 0
            self.total = len(ips) * 116

        def write(self, s):
            with self.lock:
                fw.write(s)
                self.count += 1
                if self.count % 200 == 0:
                    fw.flush()
                    on_progress = None  # progress driven by server_done instead
            return len(s)

        def flush(self):
            fw.flush()

    audit = AuditWriter()
    flock = threading.Lock()
    results = []
    chunks = [ips[i::max(1, min(workers, 128))] for i in range(max(1, min(workers, 128)))]

    def worker(chunk):
        for ip in chunk:
            if cancel_event.is_set():
                return
            r = measure.deep(ip, audit, flock)
            results.append(r)
            if r.get("dead") or "error" in r:
                on_server({"ip": ip, "dead": True, "idx": ip_idx.get(ip, -1)})
            else:
                u, c = r["uncached"], r["cached"]
                on_server({"ip": ip, "idx": ip_idx.get(ip, -1),
                           "u_med": round(statistics.median(u), 1) if u else None,
                           "c_med": round(statistics.median(c), 1) if c else None,
                           "to_u": r["to_u"], "to_c": r["to_c"],
                           "bad_c": r["bad_c"], "strays": r["strays"]})

    threads = [threading.Thread(target=worker, args=(ch,)) for ch in chunks if ch]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    fw.close()
    if cancel_event.is_set():
        return None, run_dir
    with open(os.path.join(run_dir, "deepbench-results.jsonl"), "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    measure.rank(results, run_dir)  # skill ranking (ranked.json + artifacts)
    ranked = json.load(open(os.path.join(run_dir, "ranked.json")))
    return ranked, run_dir


def load_pool():
    p = os.path.join(DATASETS, "pool.json")
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p))
    except ValueError:
        return None


def master_ips():
    p = os.path.join(DATASETS, "master-ipv4.txt")
    if not os.path.exists(p):
        return []
    return [l.strip() for l in open(p) if l.strip]


def points_payload():
    """62k visualization points. Position/mapping lives server-side (single source
    of truth: datasets order). Returns (cat_names, points) where points[i] matches
    master-ipv4.txt line i: [cat_id, lat_ms, status].
    status: 0 unknown, 1 alive, 2 dead, 3 hijacker. Latency: screening uncached
    median if screened, else liveness ms."""
    master = [l.strip() for l in open(os.path.join(DATASETS, "master-ipv4.txt"))
              if l.strip()]
    by_ip = {}
    lp = os.path.join(DATASETS, "live-phase1.jsonl")
    if os.path.exists(lp):
        with open(lp) as f:
            for line in f:
                r = json.loads(line)
                by_ip[r["ip"]] = r
    screened = {}
    sp = os.path.join(DATASETS, "phase2b-results.jsonl")
    if os.path.exists(sp):
        with open(sp) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    screened[r["ip"]] = r
                except ValueError:
                    continue
    cat_names = CATEGORIES
    ids = {name: i for i, name in enumerate(cat_names)}
    points = []
    for ip in master:
        lat, status = 0.0, 0
        r = screened.get(ip)
        if r is not None and "uncached" in r:
            if r.get("hijack", 0) > 0:
                status = 3
            elif r.get("dead"):
                status = 2
            else:
                status = 1
            if r.get("uncached"):
                lat = sorted(r["uncached"])[len(r["uncached"]) // 2]
        else:
            lr = by_ip.get(ip)
            if lr is not None:
                status = 1 if lr["alive"] else 2
                lat = lr.get("ms") or 0.0
        points.append([ids[categorize(ip)], round(lat, 1), status])
    return cat_names, points


# ---- prefix categorization (validated composition source: PTR recount method) ----
PREFIX_TABLE = [
    (("1.1.1.", "1.0.0.", "172.64.", "172.65."), "Cloudflare"),
    (("8.8.8.", "8.8.4."), "Google"),
    (("9.9.9.", "149.112."), "Quad9"),
    (("208.67.222.", "208.67.220."), "OpenDNS"),
    (("76.76.2.", "76.76.10."), "ControlD"),
    (("94.140.",), "AdGuard"),
    (("185.228.",), "CleanBrowsing"),
    (("64.6.", "156.154.", "199.45."), "Verisign"),
    (("100.100.100.",), "MagicDNS"),
    (("23.", "88.221.", "96.7.", "173.223."), "Akamai"),
]
CATEGORIES = ["Akamai", "Cloudflare", "Google", "Quad9", "OpenDNS", "ControlD",
              "AdGuard", "CleanBrowsing", "Verisign", "MagicDNS", "Other"]


def categorize(ip):
    for prefixes, name in PREFIX_TABLE:
        if any(ip.startswith(p) for p in prefixes):
            return name
    return "Other"


def relabel_category(ip, category):
    """Rankings store PTR-derived categories from measure.rank; PTR strings
    like 'one.one.one.one' land in 'other:*' even when the IP is a known
    provider (1.0.0.1 -> Cloudflare). Prefer the IP-range category whenever
    it identifies a known provider, so rankings, providers table and the
    field agree. Unknown stays 'other:<ptr>' (honest, no invention)."""
    if not category or category.startswith("other"):
        known = categorize(ip)
        if known != "Other":
            return known
    return category or "Other"
