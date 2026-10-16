#!/usr/bin/env python3
"""Ad-hoc verification for the DNSBench web app (changed: dnslib.py, engine.py,
server.py, static/*, viz). NOT a test suite — exercises the changed behavior
end-to-end against a live server instance on an ephemeral port.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request

PY = "/opt/homebrew/bin/python3.15"
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _retry(fn, tries=4, delay=0.5):
    """Retry transport flakes only. An HTTPError means the server ANSWERED —
    that status is the result, never retried (POST retries would double-fire)."""
    for i in range(tries):
        try:
            return fn()
        except urllib.error.HTTPError:
            raise
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(delay)


def get_json(url, tries=4, timeout=5):
    """GET with retry: a busy ThreadingHTTPServer can transiently drop a poll."""
    return _retry(lambda: json.load(urllib.request.urlopen(url, timeout=timeout)),
                  tries=tries)


def post_json(base, path, obj, timeout=10):
    req = urllib.request.Request(
        f"{base}{path}", data=json.dumps(obj).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return _retry(lambda: json.load(urllib.request.urlopen(req, timeout=timeout)))


def get_body(base, path, timeout=10):
    return _retry(lambda: urllib.request.urlopen(f"{base}{path}", timeout=timeout).read())


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---- 1. dnslib selftest (validation correctness) ----
sys.path.insert(0, os.path.join(APP, "server-lib"))
sys.path.insert(0, os.path.expanduser(
    "~/.hermes/skills/macos-diagnostics/dns-benchmark/scripts"))
import dnslib  # noqa: E402

p, t = dnslib.selftest()
check("dnslib selftest", p == t, f"{p}/{t}")

# timeout=None reads module attr at call time (server reconfigures per job)
import threading  # noqa: E402
dnslib.TIMEOUT = 0.05
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(0.05)
t0 = time.perf_counter()
ms, rc, an, st = dnslib.q_validated(s, "127.0.0.1", "test.example", 5)
dt = time.perf_counter() - t0
check("q_validated honors runtime TIMEOUT attr", 0.03 < dt < 0.30 and rc == -2,
      f"dt={dt*1000:.0f}ms rc={rc}")
dnslib.TIMEOUT = 2.0

import engine  # noqa: E402

# ---- 2. engine adapter ----
p, t = engine.selftest()
check("engine.selftest passthrough", p == t)

pool = engine.load_pool()
check("pool loaded from datasets", pool is not None and pool["meta"]["eligible"] > 5000,
      str(pool["meta"]) if pool else "none")

ips = engine.master_ips()
check("master_ips count", len(ips) == 62608, str(len(ips)))

cat_names, pts = engine.points_payload()
check("points payload", len(pts) == 62608 and len(cat_names) == 11,
      f"pts={len(pts)} cats={len(cat_names)}")
alive = sum(1 for x in pts if x[2] == 1)
dead = sum(1 for x in pts if x[2] == 2)
hij = sum(1 for x in pts if x[2] == 3)
pool_meta = engine.load_pool()["meta"]
# Source-of-truth consistency: the payload must paint exactly the screening's
# hijacker rows, and the pool must exclude every one of them. Pool META is
# derived at pool-generation time; datasets/pool.json predates the counting
# fix (2026-10-16) where a hijacker with zero uncached samples was dropped
# under the no-uncached bucket instead of hijacked_excluded (26 vs 27), so
# meta can lag by that historical artifact on legacy pools. Never compare
# meta directly; the invariants below are the ones that matter.
screened_hij = 0
hij_ips = set()
with open(os.path.join(APP, "datasets", "phase2b-results.jsonl")) as f:
    for line in f:
        r = json.loads(line)
        if r.get("hijack", 0) > 0:
            screened_hij += 1
            hij_ips.add(r["ip"])
pool_ips = {r["ip"] for r in engine.load_pool()["pool"]}
check("points status distribution",
      alive > 5000 and hij == screened_hij and not (hij_ips & pool_ips),
      f"alive={alive} hij={hij} screened_hij={screened_hij} "
      f"hij_in_pool={len(hij_ips & pool_ips)} meta={pool_meta}")
check("categorize sanity", engine.categorize("1.1.1.1") == "Cloudflare"
      and engine.categorize("8.8.8.8") == "Google"
      and engine.categorize("9.9.9.9") == "Quad9"
      and engine.categorize("96.7.1.2") == "Akamai"
      and engine.categorize("192.0.2.99") == "Other")

# isolated run dir creation + checkpoint preservation
run_dir, ts = engine.new_run_dir()
check("run dir isolated by timestamp", os.path.isdir(run_dir) and ts in run_dir)
cp = os.path.join(run_dir, "checkpoint-pool.json")
engine.preserve_checkpoints(run_dir)
check("checkpoints copied aside (never deleted)", os.path.exists(cp))
shutil.rmtree(run_dir)  # clean up our probe dir

# ---- 3. live server: HTTP API + SSE + run lifecycle ----
port = free_port()
runs_dir = os.path.join(APP, "datasets", "runs")
runs_before = set(os.listdir(runs_dir)) if os.path.isdir(runs_dir) else set()
env = dict(os.environ, DNSBENCH_PORT=str(port))
proc = subprocess.Popen([PY, os.path.join(APP, "server.py")], env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
base = f"http://127.0.0.1:{port}"
try:
    # wait for boot
    up = False
    for _ in range(40):
        try:
            urllib.request.urlopen(f"{base}/api/state", timeout=1)
            up = True
            break
        except Exception:
            time.sleep(0.25)
    check("server boots on ephemeral port", up)

    state = get_json(f"{base}/api/state")
    check("state reports candidates", state["n_candidates"] == 62608)
    check("confirm threshold present", state["confirm_threshold"] == 50)

    ds = get_json(f"{base}/api/datasets")
    check("datasets endpoint", ds["candidates"] == 62608 and ds["liveness_rows"] == 62608
          and ds["screened"] == 6744, f"screened={ds['screened']}")

    pts_resp = get_json(f"{base}/api/points", timeout=30)
    check("points endpoint", len(pts_resp["points"]) == 62608)
    meta = get_json(f"{base}/api/points-meta", timeout=30)
    check("points-meta endpoint", len(meta["ips"]) == 62608)

    runs = get_json(f"{base}/api/runs")
    check("runs endpoint lists history", len(runs["runs"]) >= 1,
          f"{len(runs['runs'])} runs")

    # static assets (markers current as of the 2026-10-16 visualization
    # redesign: server-rendered SVG/tables, no wasm)
    for path, marker in (("/", b"coverage-stats"), ("/app.js", b"renderProviderTable"),
                         ("/styles.css", b"--accent")):
        body = get_body(base, path)
        check(f"static {path}", marker in body, f"{len(body)} bytes")

    # SSE: subscribe and trigger a run; collect events
    sse_events = []

    def sse_reader():
        try:
            r = urllib.request.urlopen(f"{base}/events", timeout=60)
            buf = b""
            while len(sse_events) < 400:
                chunk = r.read1(4096) if hasattr(r, "read1") else r.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n\n" in buf:
                    frame, buf = buf.split(b"\n\n", 1)
                    ev = None
                    data = b""
                    for line in frame.split(b"\n"):
                        if line.startswith(b"event: "):
                            ev = line[7:].decode()
                        elif line.startswith(b"data: "):
                            data = line[6:]
                    if ev:
                        sse_events.append((ev, json.loads(data) if data else {}))
        except Exception:
            pass

    threading.Thread(target=sse_reader, daemon=True).start()
    time.sleep(1.0)

    # run lifecycle: n=12 (below confirm threshold) skip screening
    resp = post_json(base, "/api/run", {"n": 12, "screening": "skip", "workers": 20})
    check("run accepted", resp.get("started") is True, str(resp))

    # confirm gate: n=150 without confirmed. Wait for the n=12 job to finish
    # first — under load a 12-server deep bench can exceed a few seconds, and
    # posting while a job is live answers 409 (Pitfall #17 tolerance).
    time.sleep(1)  # let job start
    deadline = time.time() + 90
    while time.time() < deadline:
        st = get_json(f"{base}/api/state")
        if not st["running"]:
            break
        time.sleep(0.5)
    resp = post_json(base, "/api/run", {"n": 150, "screening": "skip"})
    check("confirm gate for n=150", "confirm_required" in resp, str(resp)[:80])
    # decline
    post_json(base, "/api/confirm", {"confirmed": False})
    st = get_json(f"{base}/api/state")
    check("decline clears pending", st["pending_confirm"] is False)

    # cancel path: start a 150-run confirmed then cancel immediately
    req = urllib.request.Request(
        f"{base}/api/confirm",
        data=json.dumps({"confirmed": True}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    # pending was cleared by decline; start fresh with confirmed inline
    resp = post_json(base, "/api/run",
                     {"n": 150, "screening": "skip", "confirmed": True,
                      "workers": 40})
    check("confirmed run starts", resp.get("started") is True)
    time.sleep(2)
    resp = post_json(base, "/api/cancel", {})
    check("cancel accepted", resp.get("cancelled") is True)

    # wait for the n=12 job (or its cancel) to finish; verify events
    deadline = time.time() + 90
    while time.time() < deadline:
        st = get_json(f"{base}/api/state")
        if not st["running"]:
            break
        time.sleep(1)
    time.sleep(1.5)
    kinds = {ev for ev, _ in sse_events}
    check("SSE stream carried events", len(sse_events) > 0, f"{len(sse_events)} events: {sorted(kinds)}")
    check("SSE has state + stage + server_done",
          {"state", "stage", "server_done"} <= kinds, str(sorted(kinds)))
    run_completes = [d for ev, d in sse_events if ev == "run_complete"]
    n12_done = any(d.get("n_ranked", 0) >= 12 for d in run_completes)
    check("run_complete with ranked servers", n12_done,
          str([d.get("run_dir") for d in run_completes]))
    cancelled_seen = any(ev == "cancelled" for ev, _ in sse_events)
    check("cancelled event delivered", cancelled_seen)

    # new run dirs exist with required artifacts
    new_dirs = sorted(set(os.listdir(runs_dir)) - runs_before)
    ok_artifacts = []
    for d in new_dirs:
        rd = os.path.join(APP, "datasets", "runs", d)
        have = os.path.exists(os.path.join(rd, "ranked.json"))
        if have:
            ok_artifacts.append(d)
    check("new run dirs persisted with ranked.json", len(ok_artifacts) >= 1,
          str(new_dirs))

    # compare endpoint between two stored runs
    if len(runs["runs"]) >= 2:
        a, b = runs["runs"][0]["ts"], runs["runs"][1]["ts"]
        cmp = get_json(f"{base}/api/compare/{a}/{b}", timeout=15)
        check("compare endpoint", "overlap" in cmp, f"overlap={cmp.get('overlap')}")
finally:
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    # NOTE: no port-enumeration cleanup. lsof -ti -iTCP:<port> is unreliable on
    # this host (fused -ti silently drops the port filter and returns unrelated
    # pids), and enumerating by port is never authority to terminate a process.
    # The server is our own tracked child; SIGTERM/kill on `proc` is the only
    # termination path.

# ---- summary ----
fails = [n for n, ok, _ in results if not ok]
print(f"\n{'='*50}")
print(f"ad-hoc verification: {len(results) - len(fails)}/{len(results)} passed")
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("ALL PASS (ad-hoc — not a substitute for a committed test suite)")
