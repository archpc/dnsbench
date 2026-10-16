#!/usr/bin/env python3
"""DNSBench local server: stdlib-only HTTP + SSE, bound to 127.0.0.1.

Architecture (per approved design):
- transport: Server-Sent Events (EventSource) for server->client stream;
  plain fetch for commands. No hand-rolled WebSocket.
- engine: server-lib/engine.py is the ONLY module touching the validated
  dns-benchmark skill scripts; the skill is imported as-is, never mutated.
- runs: every run gets an isolated directory datasets/runs/<UTC-ts>/.
  Checkpoints are never deleted; fresh screening copies them aside first.
"""
import json
import os
import queue
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
if os.path.join(APP_DIR, "server-lib") not in sys.path:
    sys.path.insert(0, os.path.join(APP_DIR, "server-lib"))

import engine  # noqa: E402

PORT = int(os.environ.get("DNSBENCH_PORT", "8461"))
HOST = "127.0.0.1"
MAX_N = 150
DEFAULT_N = 25
CONFIRM_THRESHOLD = 50


class Broker:
    """Fan-out SSE event streams + job serialization."""

    def __init__(self):
        self.subs = []            # list[queue.Queue]
        self.lock = threading.Lock()
        self.job_thread = None
        self.current_job = None   # live Job; its .cancel is the job's kill switch
        self.admit_lock = threading.Lock()  # serializes admission (P1 race fix)
        self.pending = None       # params awaiting confirmation

    def subscribe(self):
        q = queue.Queue(maxsize=1000)
        with self.lock:
            self.subs.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def emit(self, event, data):
        payload = (json.dumps(data, separators=(",", ":")) + "\n").encode()
        with self.lock:
            subs = list(self.subs)
        for q in subs:
            try:
                q.put_nowait((event, payload))
            except queue.Full:
                pass  # slow client: drop rather than block the job


broker = Broker()


def on_progress(stage, done, total, alive):
    broker.emit("progress", {"stage": stage, "done": done, "total": total,
                             "alive": alive})


class Job:
    def __init__(self, params):
        self.params = params
        self.cancel = threading.Event()

    def run(self):
        try:
            self._run()
        except Exception as e:
            import traceback
            traceback.print_exc()
            broker.emit("job_error", {"error": str(e)})
        broker.emit("job_done", {})
        with broker.lock:
            broker.job_thread = None
            broker.current_job = None

    def _run(self):
        p = self.params
        n = min(int(p.get("n", DEFAULT_N)), MAX_N)
        workers = max(1, min(int(p.get("workers", 60)), 128))
        engine.set_timeout(p.get("timeout_s", 2.0))
        screening = p.get("screening", "skip")
        broker.emit("stage", {"stage": "starting", "screening": screening})

        p, t = engine.selftest()
        if p != t:
            broker.emit("job_error", {"error": f"dnslib selftest failed {p}/{t}"})
            return
        have_pool = engine.load_pool() is not None
        if screening in ("fresh", "auto"):
            if screening == "fresh" or not have_pool:
                broker.emit("stage", {"stage": "discovery",
                                      "mode": screening})
                pool, run_dir = engine.run_discovery(screening, on_progress,
                                                     self.cancel)
                if pool is None or self.cancel.is_set():
                    broker.emit("cancelled", {})
                    return
                # use the pool this job generated, wherever it lives: in an
                # auto bootstrap the main datasets/pool.json may not exist yet
                broker.emit("pool_updated",
                            {"meta": pool["meta"],
                             "run_dir": os.path.basename(run_dir)})
                pool_path = os.path.join(run_dir, "pool.json")
            else:
                broker.emit("stage", {"stage": "discovery_skipped"})
                pool_path = os.path.join(engine.DATASETS, "pool.json")
        else:
            pool_path = os.path.join(engine.DATASETS, "pool.json")
        if not os.path.exists(pool_path):
            broker.emit("job_error", {"error": "no pool.json — run discovery first"})
            return

        broker.emit("stage", {"stage": "deepbench",
                              "n_servers": n + 4, "pool": pool_path})
        ranked, run_dir = engine.run_measure(
            pool_path, n, workers,
            lambda d: broker.emit("server_done", d),
            self.cancel)
        if ranked is None:
            broker.emit("cancelled", {})
            return
        broker.emit("run_complete", {
            "run_dir": os.path.basename(run_dir),
            "n_ranked": ranked["n_ranked"],
            "incomplete": ranked.get("incomplete_cached", []),
            "top10": [{"ip": r["ip"], "u_med": r["u_med"], "c_med": r["c_med"],
                       "ptr": r.get("ptr"),
                       "category": engine.relabel_category(r["ip"], r.get("category"))}
                      for r in ranked["ranked"][:10]]})


def needs_confirmation(params):
    n = int(params.get("n", DEFAULT_N))
    screening = params.get("screening", "skip")
    needs = []
    if n > CONFIRM_THRESHOLD:
        needs.append(f"deep bench of {n} resolvers (>{CONFIRM_THRESHOLD})")
    if screening == "fresh":
        needs.append("fresh screening: re-screens ~6,744 live resolvers "
                     "(checkpoints copied aside, nothing deleted)")
    elif screening == "auto" and engine.load_pool() is None:
        needs.append("full liveness+screening of 62,608 candidates")
    return needs


def validate_params(p):
    """Server-side range validation; returns error string or None.

    Runs before confirmation or job creation so a bad parameter can never
    reach the engine (e.g. negative n would slice pool[:-k] = almost the
    entire pool).
    """
    try:
        n = int(p.get("n", DEFAULT_N))
        workers = int(p.get("workers", 60))
        timeout = float(p.get("timeout_s", 2.0))
    except (TypeError, ValueError):
        return "n, workers, timeout_s must be numeric"
    if not 1 <= n <= MAX_N:
        return f"n out of range 1..{MAX_N}"
    if not 1 <= workers <= 128:
        return "workers out of range 1..128"
    if not 0.05 <= timeout <= 10.0:
        return "timeout_s out of range 0.05..10"
    if p.get("screening", "skip") not in ("skip", "auto", "fresh"):
        return "screening must be skip|auto|fresh"
    return None


def start_job(params):
    """Returns ('started',None) | ('confirm',needs) | (None,error)."""
    err = validate_params(params)
    if err:
        return None, err
    with broker.admit_lock:  # admission + job assignment are atomic
        if broker.job_thread and broker.job_thread.is_alive():
            return None, "job already running"
        needs = needs_confirmation(params)
        if needs and not params.get("confirmed"):
            broker.pending = params
            return "confirm", needs
        broker.pending = None
        job = Job(params)
        broker.current_job = job
        broker.job_thread = threading.Thread(target=job.run, daemon=True)
        broker.job_thread.start()
    return "started", None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/events":
            self._sse()
        elif path == "/":
            self._file(os.path.join(STATIC_DIR, "index.html"),
                       "text/html; charset=utf-8")
        elif path == "/app.js":
            self._file(os.path.join(STATIC_DIR, "app.js"),
                       "text/javascript; charset=utf-8")
        elif path == "/styles.css":
            self._file(os.path.join(STATIC_DIR, "styles.css"), "text/css; charset=utf-8")
        elif path == "/api/state":
            self._json(api_state())
        elif path == "/api/points":
            self._points()
        elif path == "/api/points-meta":
            self._json({"ips": engine.master_ips()})
        elif path == "/api/datasets":
            self._json(api_datasets())
        elif path == "/api/runs":
            self._json(api_runs())
        elif path.startswith("/api/runs/") and "/queries/" in path:
            _, _, rest = path.partition("/api/runs/")
            ts, _, ip = rest.partition("/queries/")
            self._json(api_queries(ts, ip))
        elif path.startswith("/api/runs/"):
            ts = urlparse(self.path).path.rsplit("/", 1)[-1]
            self._json(api_run(ts))
        elif path.startswith("/api/compare/"):
            parts = path.split("/")  # ['', 'api', 'compare', <a>, <b>]
            if len(parts) == 5:
                self._json(api_compare(parts[3], parts[4]))
            else:
                self.send_error(404)
        elif path == "/api/coverage":
            self._json(api_coverage())
        elif path == "/api/history":
            qs = urlparse(self.path).query
            ip = (parse_qs(qs).get("ip") or [""])[0]
            self._json(api_history(ip))
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/run":
            length = int(self.headers.get("Content-Length", 0))
            params = json.loads(self.rfile.read(length) or b"{}")
            status, info = start_job(params)
            if status == "started":
                self._json({"started": True})
            elif status == "confirm":
                self._json({"confirm_required": info})
            else:
                self._json({"error": info}, 409)
        elif path == "/api/confirm":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if body.get("confirmed") and broker.pending:
                params = dict(broker.pending)
                params["confirmed"] = True
                broker.pending = None
                status, info = start_job(params)
                self._json({"started": status == "started", "error": info})
            else:
                broker.pending = None
                self._json({"started": False})
        elif path == "/api/cancel":
            cancelled = False
            job = broker.current_job
            if job is not None and broker.job_thread and broker.job_thread.is_alive():
                job.cancel.set()
                cancelled = True
            self._json({"cancelled": cancelled})
        else:
            self.send_error(404)

    # ---------- SSE ----------
    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "null")
        self.end_headers()
        q = broker.subscribe()
        try:
            # initial state event
            hello = json.dumps(api_state(), separators=(",", ":")).encode()
            self.wfile.write(b"event: state\ndata: " + hello + b"\n\n")
            self.wfile.flush()
            while True:
                try:
                    event, payload = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(b"event: " + event.encode() +
                                 b"\ndata: " + payload + b"\n\n")
                self.wfile.flush()
        except (OSError, BrokenPipeError):
            pass
        finally:
            broker.unsubscribe(q)

    # ---------- data ----------
    def _points(self):
        cat_names, points = engine.points_payload()
        self._json({"cat_names": cat_names, "points": points})


def api_state():
    job_alive = bool(broker.job_thread and broker.job_thread.is_alive())
    pool = engine.load_pool()
    runs = sorted(os.listdir(engine.RUNS)) if os.path.isdir(engine.RUNS) else []
    master_path = os.path.join(engine.DATASETS, "master-ipv4.txt")
    n_candidates = sum(1 for _ in open(master_path)) \
        if os.path.exists(master_path) else 0
    return {"running": job_alive, "host": HOST, "port": PORT,
            "n_candidates": n_candidates,
            "pool": pool["meta"] if pool else None,
            "n_runs": len(runs),
            "confirm_threshold": CONFIRM_THRESHOLD,
            "pending_confirm": bool(broker.pending)}


def api_coverage():
    """Screening-side candidate coverage summary (distinct from deep-bench
    runs): status counts over the master candidate list."""
    _, pts = engine.points_payload()
    c = {"unknown": 0, "alive": 0, "dead": 0, "hijack": 0}
    for x in pts:
        c[("unknown", "alive", "dead", "hijack")[x[2]]] += 1
    c["total"] = len(pts)
    pool = engine.load_pool()
    if pool:
        c["pool_eligible"] = pool["meta"].get("eligible")
        c["pool_generated"] = pool["meta"].get("generated")
    return c


IP_RE = re.compile(r"^(?:\d{1,2}|1\d{2}|2[0-4]\d|25[0-5])(?:\.(?:\d{1,2}|1\d{2}|2[0-4]\d|25[0-5])){3}$")


def api_queries(ts, ip):
    """Per-resolver raw samples from an existing run's per-query audit log.
    Read-only summarization of data measure.py already wrote."""
    if not IP_RE.match(ip or ""):
        return {"error": "invalid ip"}
    pf = os.path.join(engine.RUNS, ts, "deepbench-perquery.jsonl")
    if not os.path.exists(pf):
        return {"error": "no per-query log for this run"}
    out = {"ip": ip,
           "uncached": {"ok": [], "timeouts": 0, "invalid": 0},
           "cached": {"ok": [], "timeouts": 0, "invalid": 0},
           "prime": {"ok": [], "timeouts": 0, "invalid": 0},
           "liveness": None}
    try:
        with open(pf) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("ip") != ip:
                    continue
                k = d.get("kind")
                rc, an, ms = d.get("rcode"), d.get("an"), d.get("ms")
                if k == "liveness":
                    out["liveness"] = {"ms": ms, "rcode": rc, "an": an}
                    continue
                b = out.get(k)
                if b is None:
                    continue
                if rc == -2:
                    b["timeouts"] += 1
                elif k == "uncached" or (rc == 0 and an > 0):
                    b["ok"].append(ms)
                else:
                    b["invalid"] += 1
    except OSError as e:
        return {"error": f"unreadable per-query log: {e}"}
    return out


def api_history(ip):
    """Selected resolver's measurements across ALL stored runs, plus the
    mandatory references as context. Runs with different candidate
    populations are labeled as-is; absence is explicit null, never zero."""
    if not IP_RE.match(ip or ""):
        return {"error": "invalid ip"}
    runs = []
    refs = {r: [] for r in engine.measure.FORCED_REFS}
    if os.path.isdir(engine.RUNS):
        for ts in sorted(os.listdir(engine.RUNS)):
            rp = os.path.join(engine.RUNS, ts, "ranked.json")
            if not os.path.exists(rp):
                continue  # partial/cancelled runs excluded
            try:
                r = json.load(open(rp))
            except ValueError:
                continue
            ranked = r.get("ranked", [])
            me = None
            rank = None
            for i, row in enumerate(ranked, 1):
                if row["ip"] == ip:
                    me = {"u_med": row.get("u_med"), "c_med": row.get("c_med"),
                          "u_p90": row.get("u_p90"), "to_u": row.get("to_u"),
                          "to_c": row.get("to_c"), "bad_c": row.get("bad_c")}
                    rank = i
                    break
            runs.append({"ts": ts, "generated": r.get("generated"),
                         "n_ranked": r.get("n_ranked"), "me": me, "rank": rank})
            for ref in refs:
                row = next((x for x in ranked if x["ip"] == ref), None)
                refs[ref].append({"u_med": row.get("u_med")} if row else None)
    return {"ip": ip, "runs": runs, "refs": refs}


def api_datasets():
    def count_lines(p):
        return sum(1 for _ in open(p)) if os.path.exists(p) else 0
    pool = engine.load_pool()
    return {
        "candidates": count_lines(os.path.join(engine.DATASETS, "master-ipv4.txt")),
        "liveness_rows": count_lines(os.path.join(engine.DATASETS, "live-phase1.jsonl")),
        "screened": count_lines(os.path.join(engine.DATASETS, "phase2b-results.jsonl")),
        "perquery_records": count_lines(os.path.join(
            engine.DATASETS, "phase2b-perquery.jsonl")),
        "pool": pool["meta"] if pool else None,
        "runs": sorted(os.listdir(engine.RUNS))[::-1]
        if os.path.isdir(engine.RUNS) else [],
    }


def api_runs():
    runs = []
    for ts in sorted(os.listdir(engine.RUNS))[::-1] if os.path.isdir(engine.RUNS) else []:
        rp = os.path.join(engine.RUNS, ts, "ranked.json")
        if os.path.exists(rp):
            try:
                r = json.load(open(rp))
                runs.append({"ts": ts, "generated": r.get("generated"),
                             "n_ranked": r.get("n_ranked"),
                             "incomplete": len(r.get("incomplete_cached", []))})
            except ValueError:
                continue
    return {"runs": runs}


def api_run(ts):
    rp = os.path.join(engine.RUNS, ts, "ranked.json")
    if not os.path.exists(rp):
        return {"error": "no such run"}
    try:
        r = json.load(open(rp))
    except ValueError:
        return {"error": "corrupt ranked.json"}
    for row in r.get("ranked", []):
        row["category"] = engine.relabel_category(row["ip"], row.get("category"))
    return r


def api_compare(ts_a, ts_b):
    pa = os.path.join(engine.RUNS, ts_a, "ranked.json")
    pb = os.path.join(engine.RUNS, ts_b, "ranked.json")
    if not (os.path.exists(pa) and os.path.exists(pb)):
        return {"error": "run not found"}
    try:
        ra = json.load(open(pa))["ranked"]
        rb = json.load(open(pb))["ranked"]
    except (ValueError, KeyError):
        return {"error": "corrupt run"}
    top_a = [r["ip"] for r in ra[:50]]
    top_b = [r["ip"] for r in rb[:50]]
    def cats(rs):
        c = {}
        for r in rs[:50]:
            k = engine.relabel_category(r["ip"], r.get("category"))
            c[k] = c.get(k, 0) + 1
        return c
    refs = {}
    for ref in engine.measure.FORCED_REFS:
        va = next(((i, r["u_med"]) for i, r in enumerate(ra, 1) if r["ip"] == ref), None)
        vb = next(((i, r["u_med"]) for i, r in enumerate(rb, 1) if r["ip"] == ref), None)
        if va and vb:
            refs[ref] = {"a": {"rank": va[0], "u_med": va[1]},
                         "b": {"rank": vb[0], "u_med": vb[1]}}
    return {"overlap": len(set(top_a) & set(top_b)),
            "n_a": len(ra), "n_b": len(rb),
            "cats_a": cats(ra), "cats_b": cats(rb), "refs": refs}


def main():
    copied = engine.ensure_datasets()
    if copied:
        print(f"imported datasets from scratch: {len(copied)} items")
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print(f"DNSBench on http://{HOST}:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
