#!/usr/bin/env python3
"""DNSBench helper: JSON-lines IPC bridge between the native macOS app and the
validated dns-benchmark engine scripts (dnslib/dnslib2/measure/discover/
stability). stdlib only, self-contained: imports the vendored engine scripts
via the sibling adapter module.

Protocol v1 (one JSON object per line):
  request  stdin : {"v":1,"id":N,"cmd":"<name>", ...args}
  response stdout: {"v":1,"id":N,"ok":true,"data":{...}}
                   {"v":1,"id":N,"ok":false,"error":"..."}
  event    stdout: {"v":1,"event":"<name>", ...}   (interleaved, from jobs)
stderr is diagnostics only; stdout is pure protocol.

Lifecycle contract:
- Every request the helper reads is answered with EXACTLY ONE terminal
  response, even during shutdown.
- shutdown: replies immediately, stops accepting NEW long jobs (later long
  requests are rejected with ok:false, which IS their terminal response),
  lets already-queued short requests finish, cooperatively cancels the
  running job, waits a bounded grace period, then exits 0. os._exit is used
  only for the abnormal fallback when a job refuses to stop in time.
- cancel: cooperative; long jobs stop at the next server/window boundary.
  A hard cancel (SIGKILL from the app) leaves partial run dirs intact.
- EOF on stdin behaves like shutdown.
Commands:
  ping | selftest | discovery {mode} | measure {n, workers, timeout, windows,
  engine, netsvc} | aggregate {manifest} | cancel | shutdown
"""
import adapter
import json
import os
import queue
import resource
import signal
import sys
import threading
import time

measure = adapter.measure
import dnslib2    # noqa: E402  (vendored dir on sys.path via adapter)
import stability  # noqa: E402

PROTOCOL_VERSION = 1
HELPER_VERSION = 2

# shutdown grace period for the running job after cooperative cancel
SHUTDOWN_GRACE_S = 20.0
LONG_JOBS = ("discovery", "measure", "aggregate")

_out_lock = threading.Lock()
_job_lock = threading.Lock()
_cancel = threading.Event()
_shutting_down = threading.Event()


def emit(obj):
    obj["v"] = PROTOCOL_VERSION
    with _out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def reply(req_id, data=None, error=None):
    if error is not None:
        emit({"id": req_id, "ok": False, "error": error})
    else:
        emit({"id": req_id, "ok": True, "data": data or {}})


class _EventFw:
    """Intercepts per-query JSONL writes: persists to the run file unchanged
    (line-buffered so partial data survives cancellation) and streams each
    record to the app as an event."""

    def __init__(self, path):
        self._fh = open(path, "w", buffering=1)
        self.lock = threading.Lock()

    def write(self, s):
        self._fh.write(s)
        try:
            r = json.loads(s)
        except ValueError:
            return len(s)
        emit({"event": "query", "ip": r.get("ip"), "kind": r.get("kind"),
              "ms": r.get("ms"), "rcode": r.get("rcode"), "an": r.get("an"),
              "kd": r.get("kd")})
        return len(s)

    def flush(self):
        self._fh.flush()

    def close(self):
        self._fh.close()


def _phase(name, done, total, **extra):
    emit({"event": "phase", "name": name, "done": done, "total": total,
          **extra})


# ---- command implementations (run on the job worker thread) ----

def cmd_ping(_req):
    pool_meta = adapter.load_pool()
    return {"helper": HELPER_VERSION, "protocol": PROTOCOL_VERSION,
            "python": sys.version.split()[0],
            "data_dir": adapter.DATA_DIR,
            "pool_eligible": pool_meta["meta"]["eligible"] if pool_meta else None}


def cmd_selftest(_req):
    p, t = adapter.selftest()
    return {"passed": p, "total": t}


def cmd_discovery(req):
    mode = req.get("mode", "auto")
    if mode not in ("fresh", "auto"):
        raise ValueError(f"unknown discovery mode: {mode!r}")

    def on_progress(phase, done, total, alive=None):
        _phase(phase, done, total, **({"alive": alive} if alive is not None else {}))

    pool, run_dir = adapter.run_discovery(mode, on_progress, _cancel)
    if pool is None:
        return {"cancelled": True, "run_dir": run_dir}
    return {"cancelled": False, "pool": pool, "run_dir": run_dir}


def _measure_one_window(ips, outdir, req):
    """One deep-bench window into outdir; returns results list.
    Mirrors measure.main's per-window loop (engine paths identical) and adds
    per-query event streaming + cooperative cancellation."""
    fw = _EventFw(os.path.join(outdir, "deepbench-perquery.jsonl"))
    results = []
    t0 = time.time()
    engine = req.get("engine", "kqueue")
    try:
        if engine == "kqueue":
            try:
                dnslib2.set_qos_user_interactive()
            except OSError:
                pass
            results = measure.deep_all_kqueue(ips, fw, netsvc=req.get("netsvc"))
        else:
            workers = max(1, min(int(req.get("workers", measure.WORKERS)), 128))
            flock = threading.Lock()
            rl = threading.Lock()

            def worker(chunk):
                for ip in chunk:
                    if _cancel.is_set():
                        return
                    r = measure.deep(ip, fw, flock)
                    with rl:
                        results.append(r)

            chunks = [ips[i::workers] for i in range(workers)]
            threads = [threading.Thread(target=worker, args=(c,))
                       for c in chunks if c]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        fw.close()
        fw = None
        for r in results:
            r["engine"] = engine
            r["netsvc"] = req.get("netsvc", "off")
        with open(os.path.join(outdir, "deepbench-results.jsonl"), "w") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")
        measure.rank(results, outdir)
        emit({"event": "window_done", "dir": outdir,
              "elapsed_s": round(time.time() - t0, 1)})
        return results
    finally:
        if fw is not None:
            fw.close()


def cmd_measure(req):
    n = int(req.get("n", 150))
    windows = max(1, min(int(req.get("windows", 1)), 10))
    timeout = req.get("timeout")
    if timeout is not None:
        adapter.set_timeout(float(timeout))
    pool = json.load(open(os.path.join(adapter.DATASETS, "pool.json")))["pool"]
    base_ips = [r["ip"] for r in pool[:n]]
    for ref in measure.FORCED_REFS:
        if ref not in base_ips:
            base_ips.append(ref)
    passed, total = measure.selftest()
    if passed != total:
        raise RuntimeError(f"parser selftest failed {passed}/{total}")
    dirs = []
    import datetime
    import random
    ranked_last = None
    for w in range(windows):
        if _cancel.is_set():
            return {"cancelled": True, "run_dirs": dirs}
        ips = list(base_ips)
        random.shuffle(ips)  # fresh order per window (independent windows)
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        outdir = (f"{adapter.RUNS}/{ts}" if windows == 1
                  else f"{adapter.RUNS}/{ts}_w{w + 1}")
        os.makedirs(outdir, exist_ok=True)
        adapter.preserve_checkpoints(outdir)
        dirs.append(outdir)
        emit({"event": "window", "index": w + 1, "total": windows, "dir": outdir,
              "n_servers": len(ips)})
        results = _measure_one_window(ips, outdir, req)
        if _cancel.is_set():
            return {"cancelled": True, "run_dirs": dirs}
        ranked_last = json.load(open(os.path.join(outdir, "ranked.json")))
    manifest = None
    aggregate = None
    if windows > 1:
        manifest = f"{adapter.RUNS}/windows-{os.path.basename(dirs[0]).rsplit('_w', 1)[0]}.json"
        json.dump({"windows": dirs, "engine": req.get("engine", "kqueue"),
                   "netsvc": req.get("netsvc", "off"), "n_requested": n,
                   "generated": datetime.datetime.now(datetime.timezone.utc)
                   .strftime("%Y-%m-%dT%H:%M:%SZ")},
                  open(manifest, "w"), indent=1)
        _phase("aggregate", 1, 1)
        aggregate = stability.aggregate(manifest)
    return {"cancelled": False, "run_dirs": dirs, "manifest": manifest,
            "ranked": ranked_last, "aggregate": aggregate}


def cmd_aggregate(req):
    manifest = req["manifest"]
    _phase("aggregate", 1, 1)
    return stability.aggregate(manifest)


def cmd_cancel(_req):
    _cancel.set()
    return {"cancelled": True}


COMMANDS = {
    "ping": cmd_ping,
    "selftest": cmd_selftest,
    "discovery": cmd_discovery,
    "measure": cmd_measure,
    "aggregate": cmd_aggregate,
    "cancel": cmd_cancel,
}


def _job_worker(jobs):
    """Single worker: serial job execution; every dequeued request gets
    exactly one terminal response before the worker asks for the next."""
    global _pending
    while True:
        req = jobs.get()
        req_id = req.get("id")
        try:
            emit({"event": "job", "cmd": req["cmd"], "state": "started"})
            data = COMMANDS[req["cmd"]](req)
            state = ("cancelled" if isinstance(data, dict) and data.get("cancelled")
                     else "done")
            emit({"event": "job", "cmd": req["cmd"], "state": state})
            reply(req_id, data)
        except Exception as e:  # error propagation: never die silently
            emit({"event": "job", "cmd": req.get("cmd", "?"), "state": "error"})
            reply(req_id, error=f"{type(e).__name__}: {e}")
        finally:
            with idle_cv:
                _pending -= 1
                idle_cv.notify_all()


def _reader(jobs):
    """Blocking stdin reader (own thread): dispatches requests. Inline handles
    cancel/shutdown/rejections so they never wait on the job worker."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            reply(None, error="malformed request line")
            continue
        cmd = req.get("cmd")
        if cmd not in COMMANDS and cmd != "shutdown":
            reply(req.get("id"), error=f"unknown command: {cmd!r}")
            continue
        if cmd == "cancel":
            reply(req.get("id"), cmd_cancel(req))
            continue
        if cmd == "shutdown":
            # terminal response first; orderly exit after bounded drain
            reply(req.get("id"), {"bye": True})
            _shutting_down.set()
            _cancel.set()
            continue
        if _shutting_down.is_set() and cmd in LONG_JOBS:
            reply(req.get("id"), error="helper is shutting down; job rejected")
            continue
        with idle_cv:
            global _pending
            _pending += 1
            jobs.put(req)
    # EOF: app closed the pipe -> orderly shutdown
    _shutting_down.set()
    _cancel.set()


_pending = 0
idle_cv = threading.Condition()


def _drain_and_exit(jobs):
    """Orderly shutdown: wait bounded for queued+running work to finish after
    cooperative cancel, then exit 0. Falls back to abnormal exit if a job
    refuses to stop in time (partial outputs remain on disk either way)."""
    deadline = time.monotonic() + SHUTDOWN_GRACE_S
    with idle_cv:
        while _pending > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print("helper: shutdown grace expired; aborting job",
                      file=sys.stderr)
                os._exit(1)  # abnormal fallback only (documented)
            idle_cv.wait(timeout=min(0.2, remaining))
    sys.stdout.flush()
    sys.stderr.flush()
    sys.exit(0)


def main():
    # engine needs headroom for thread-engine sockets (Pitfall: Errno 24)
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(10240, hard), hard))
    except (ValueError, OSError):
        pass

    global _pending
    jobs = queue.Queue()
    worker = threading.Thread(target=_job_worker, args=(jobs,),
                              daemon=True, name="helper-jobs")
    worker.start()
    reader = threading.Thread(target=_reader, args=(jobs,),
                              daemon=True, name="helper-reader")
    reader.start()

    def request_signal(_sig, _frm):
        # runs on the main thread; flags only, drain happens in the main loop
        _shutting_down.set()
        _cancel.set()

    signal.signal(signal.SIGTERM, request_signal)
    signal.signal(signal.SIGINT, request_signal)

    while not _shutting_down.is_set():
        time.sleep(0.1)
    _drain_and_exit(jobs)


if __name__ == "__main__":
    main()
