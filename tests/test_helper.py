#!/usr/bin/env python3
"""Deterministic lifecycle tests for engine/helper.py (protocol v1).

Covers: rapid ping/shutdown (exactly-one-response), long-job rejection during
shutdown, cooperative cancellation with preserved outputs, SIGKILL crash
recovery with partial data, malformed-request resilience, EOF shutdown.

Usage: python3 tests/test_helper.py [--python <interpreter>] [--data-dir <dir>]
Exits 0 iff every check passes. No network needed except the cancel/crash
cases which run a real small measure (n=2) against the live pool.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(HERE, "..", "engine", "helper.py")
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


class Helper:
    """Test harness: drives one helper process with line-framed JSON."""

    def __init__(self, python, data_dir=None):
        env = dict(os.environ)
        if data_dir:
            env["DNSBENCH_DATA_DIR"] = data_dir
        self.p = subprocess.Popen(
            [python, os.path.abspath(HELPER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, text=True, bufsize=1)
        self.lines = []
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()

    def _reader(self):
        for line in self.p.stdout:
            self.lines.append(line.strip())

    def send(self, obj):
        self.p.stdin.write(json.dumps(obj) + "\n")
        self.p.stdin.flush()

    def responses_for(self, req_id, timeout=30.0):
        """All response lines with this id (should be exactly one)."""
        deadline = time.monotonic() + timeout
        out = []
        while time.monotonic() < deadline:
            for ln in list(self.lines):
                try:
                    o = json.loads(ln)
                except ValueError:
                    continue
                if o.get("id") == req_id and "ok" in o:
                    if o not in out:
                        out.append(o)
            if out:
                return out
            time.sleep(0.05)
        return out

    def wait_event(self, event, timeout=60.0, **match):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for ln in list(self.lines):
                try:
                    o = json.loads(ln)
                except ValueError:
                    continue
                if o.get("event") == event and all(
                        o.get(k) == v for k, v in match.items()):
                    return o
            time.sleep(0.05)
        return None

    def stop(self, grace=10.0):
        try:
            self.p.stdin.close()
        except (BrokenPipeError, ValueError):
            pass
        try:
            self.p.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait(timeout=5)
        return self.p.returncode


def rsp(req_id, helper, timeout=30.0):
    out = helper.responses_for(req_id, timeout)
    return (out[0] if len(out) == 1 else None), out


def test_rapid_ping_shutdown(python, data_dir):
    h = Helper(python, data_dir)
    try:
        for i in range(1, 11):
            h.send({"v": 1, "id": i, "cmd": "ping"})
        h.send({"v": 1, "id": 100, "cmd": "shutdown"})
        ok, allr = rsp(100, h, timeout=15)
        check("shutdown replies exactly once", len(allr) == 1 and ok is not None
              and ok.get("ok") is True, f"{len(allr)} responses")
        pings_ok = 0
        dup = 0
        for i in range(1, 11):
            r = h.responses_for(i, timeout=10)
            if r and r[0].get("ok"):
                pings_ok += 1
            dup += max(0, len(r) - 1)
        check("all 10 rapid pings answered", pings_ok == 10, f"{pings_ok}/10")
        check("no duplicate responses", dup == 0, f"{dup} duplicates")
        rc = h.stop(grace=15)
        check("orderly exit code 0 after rapid shutdown", rc == 0, f"rc={rc}")
    finally:
        if h.p.poll() is None:
            h.p.kill()


def test_reject_after_shutdown(python, data_dir):
    h = Helper(python, data_dir)
    try:
        h.send({"v": 1, "id": 1, "cmd": "shutdown"})
        h.send({"v": 1, "id": 2, "cmd": "measure", "n": 2, "windows": 1})
        r, allr = rsp(2, h, timeout=15)
        check("long job after shutdown rejected (terminal response)",
              r is not None and r.get("ok") is False
              and "shutting down" in (r.get("error") or ""), repr(allr))
        rc = h.stop(grace=15)
        check("exit 0 with rejected job", rc == 0, f"rc={rc}")
    finally:
        if h.p.poll() is None:
            h.p.kill()


def test_cooperative_cancel(python, data_dir):
    runs_before = set(os.listdir(os.path.join(data_dir, "runs"))) \
        if os.path.isdir(os.path.join(data_dir, "runs")) else set()
    h = Helper(python, data_dir)
    try:
        h.send({"v": 1, "id": 1, "cmd": "measure", "n": 2, "windows": 2,
                "engine": "kqueue"})
        # cancel once the first window is underway
        started = h.wait_event("window", timeout=90)
        check("measure window started", started is not None,
              str(started and started.get("dir")))
        time.sleep(2.0)
        h.send({"v": 1, "id": 2, "cmd": "cancel"})
        r, allr = rsp(1, h, timeout=120)
        check("cancelled measure answers exactly once",
              len(allr) == 1 and r is not None and r.get("ok") is True
              and r.get("data", {}).get("cancelled") is True, repr(allr)[:200])
        rc = h.stop(grace=30)
        check("exit 0 after cancel", rc == 0, f"rc={rc}")
        runs_after = set(os.listdir(os.path.join(data_dir, "runs")))
        new_dirs = sorted(runs_after - runs_before)
        check("partial run dirs preserved on cancel", len(new_dirs) >= 1,
              str(new_dirs))
        # cancelled window dirs may lack ranked.json; they must not be deleted
        for d in new_dirs:
            perq = os.path.join(data_dir, "runs", d, "deepbench-perquery.jsonl")
            check(f"per-query audit exists in {d}", os.path.exists(perq))
    finally:
        if h.p.poll() is None:
            h.p.kill()


def test_crash_recovery(python, data_dir):
    runs_before = set(os.listdir(os.path.join(data_dir, "runs")))
    h = Helper(python, data_dir)
    try:
        h.send({"v": 1, "id": 1, "cmd": "measure", "n": 2, "windows": 1,
                "engine": "thread"})
        started = h.wait_event("query", timeout=90)
        check("queries streaming before crash", started is not None)
        time.sleep(1.0)
        h.p.send_signal(signal.SIGKILL)
        rc = h.p.wait(timeout=10)
        check("SIGKILL kills helper (nonzero)", rc == -int(signal.SIGKILL),
              f"rc={rc}")
        runs_after = set(os.listdir(os.path.join(data_dir, "runs")))
        new_dirs = sorted(runs_after - runs_before)
        check("crash leaves partial run dir", len(new_dirs) == 1, str(new_dirs))
        if new_dirs:
            perq = os.path.join(data_dir, "runs", new_dirs[0],
                                "deepbench-perquery.jsonl")
            with open(perq) as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
            parsed = 0
            for l in lines:
                try:
                    json.loads(l)
                    parsed += 1
                except ValueError:
                    pass
            check("partial per-query data parseable", parsed == len(lines)
                  and parsed > 0, f"{parsed}/{len(lines)} lines")
    finally:
        if h.p.poll() is None:
            h.p.kill()


def test_malformed_line(python, data_dir):
    h = Helper(python, data_dir)
    try:
        h.p.stdin.write("this is not json\n")
        h.p.stdin.flush()
        r, _ = rsp(None, h, timeout=10)
        check("malformed line gets error response", r is not None
              and r.get("ok") is False and "malformed" in (r.get("error") or ""))
        h.send({"v": 1, "id": 7, "cmd": "selftest"})
        r2, _ = rsp(7, h, timeout=15)
        check("helper alive after malformed input",
              r2 is not None and r2.get("ok") is True
              and r2.get("data", {}).get("passed") == r2["data"].get("total") == 10)
        rc = h.stop(grace=15)
        check("exit 0 after malformed input", rc == 0, f"rc={rc}")
    finally:
        if h.p.poll() is None:
            h.p.kill()


def test_eof_shutdown(python, data_dir):
    h = Helper(python, data_dir)
    try:
        h.send({"v": 1, "id": 1, "cmd": "ping"})
        r, _ = rsp(1, h, timeout=10)
        check("ping before EOF", r is not None and r.get("ok") is True)
        rc = h.stop(grace=10)
        check("EOF (stdin closed) -> orderly exit 0", rc == 0, f"rc={rc}")
    finally:
        if h.p.poll() is None:
            h.p.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--data-dir", default=None,
                    help="defaults to repo datasets/")
    a = ap.parse_args()
    data_dir = a.data_dir or os.path.join(HERE, "..", "datasets")

    test_rapid_ping_shutdown(a.python, data_dir)
    test_reject_after_shutdown(a.python, data_dir)
    test_malformed_line(a.python, data_dir)
    test_eof_shutdown(a.python, data_dir)
    test_cooperative_cancel(a.python, data_dir)
    test_crash_recovery(a.python, data_dir)

    fails = [n for n, ok in results if not ok]
    print(f"\nhelper lifecycle: {len(results) - len(fails)}/{len(results)} passed")
    if fails:
        print("FAILED:", fails)
        sys.exit(1)
    print("HELPER LIFECYCLE PASS")


if __name__ == "__main__":
    main()
