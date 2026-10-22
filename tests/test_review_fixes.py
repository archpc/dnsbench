#!/usr/bin/env python3
"""Offline regression tests for the 2026-10-21 review findings.

Every test drives real engine/helper code; network-facing pieces are replaced
only at the socket boundary (local UDP responder on 127.0.0.1) or at the
measurement-function boundary (stubbed deep_all_kqueue / provider_label), so
no packets leave the host.

Usage: python3 tests/test_review_fixes.py   (exit 0 iff all pass)
"""
import io
import json
import os
import queue
import struct
import sys
import tempfile
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "..", "engine")
sys.path.insert(0, ENGINE)
_TMP = tempfile.mkdtemp(prefix="dnsbench-review-")
os.environ["DNSBENCH_DATA_DIR"] = _TMP

import dnslib    # noqa: E402
import dnslib2   # noqa: E402
import helper    # noqa: E402
import measure   # noqa: E402
import stability  # noqa: E402
import test_engine  # noqa: E402  (local Responder)

results = []


def test(fn):
    try:
        fn()
        results.append((fn.__name__, True, ""))
        print(f"PASS  {fn.__name__}")
    except Exception as e:  # noqa: BLE001
        results.append((fn.__name__, False, repr(e)))
        print(f"FAIL  {fn.__name__}: {e!r}")
        traceback.print_exc()
    return fn


# ---------------------------------------------------------------- helpers

class HelperHarness:
    """Runs the real helper job worker in-process with captured emits."""

    def __init__(self):
        self.out = []
        self.lock = threading.Lock()
        helper.emit = self._emit
        self.jobs = queue.Queue()
        threading.Thread(target=helper._job_worker, args=(self.jobs,),
                         daemon=True).start()

    def _emit(self, obj):
        json.dumps(obj, allow_nan=False)  # same strictness as the real emit
        with self.lock:
            self.out.append(obj)

    def call(self, req, timeout=20):
        with helper.idle_cv:
            helper._pending += 1
        self.jobs.put(req)
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                for o in self.out:
                    if o.get("id") == req["id"] and "ok" in o:
                        return o
            time.sleep(0.01)
        raise TimeoutError(req)


def fake_record(ip, u=20.0, c=5.0):
    return {"ip": ip, "uncached": [u] * 50, "cached": [c] * 50, "to_u": 0,
            "to_c": 0, "bad_c": 0, "strays": 0, "unreach": 0, "kdeltas": []}


def setup_pool(ips):
    with open(os.path.join(_TMP, "pool.json"), "w") as f:
        json.dump({"meta": {"eligible": len(ips)},
                   "pool": [{"ip": ip, "score": 1.0} for ip in ips]}, f)


measure.provider_label = lambda ip: "?"   # no reverse-DNS traffic in tests
_H = None


def harness():
    global _H
    if _H is None:
        _H = HelperHarness()
    return _H


# ---------------------------------------------------------------- #1 / #10

@test
def cancel_does_not_poison_later_jobs():
    setup_pool(["192.0.2.1"])
    measure.deep_all_kqueue = lambda ips, fw, netsvc=None, **kw: [
        fake_record(ip) for ip in ips]
    h = harness()
    helper.cmd_cancel({})          # cancel while idle (or after a finished job)
    r = h.call({"id": 101, "cmd": "measure", "n": 1, "windows": 1})
    assert r["ok"], r
    assert r["data"]["cancelled"] is False, r["data"]
    assert len(r["data"]["run_dirs"]) == 1


@test
def kqueue_engine_honours_cancel_mid_window():
    rsp = test_engine.Responder(delay_ms=20)
    rsp.start()
    try:
        ev = threading.Event()
        threading.Timer(0.3, ev.set).start()
        specs = [("127.0.0.1", test_engine.uncached_tasks(200))]
        t0 = time.monotonic()
        recs = dnslib2.run_interleaved(specs, test_engine._Sink(), timeout=1.0,
                                       port=rsp.port, cancel=ev)
        dt = time.monotonic() - t0
    finally:
        rsp.stop.set()
    assert dt < 1.5, f"cancel ignored: window ran {dt:.2f}s"
    done = len(recs[0]["uncached"]) + recs[0]["to_u"]
    assert done < 200, done


@test
def cancelled_window_is_partial_not_ranked():
    setup_pool(["192.0.2.1", "192.0.2.2"])

    def cancelling_engine(ips, fw, netsvc=None, **kw):
        helper._cancel.set()       # user presses Cancel mid-window
        return [fake_record(ips[0])]
    measure.deep_all_kqueue = cancelling_engine
    r = harness().call({"id": 102, "cmd": "measure", "n": 2, "windows": 1})
    d = r["data"]
    assert d["cancelled"] is True, d
    run = d["run_dirs"][0]
    assert not os.path.exists(os.path.join(run, "ranked.json")), \
        "cancelled window wrote ranked.json (shows as Complete)"
    assert os.path.exists(os.path.join(run, "deepbench-results.jsonl")), \
        "partial samples not preserved"
    assert os.path.exists(os.path.join(run, "cancelled.json"))


# ---------------------------------------------------------------- #2 / #3 / #21

@test
def windows_share_benchmark_id_and_aggregate_is_per_benchmark():
    setup_pool(["192.0.2.1", "192.0.2.2"])
    measure.deep_all_kqueue = lambda ips, fw, netsvc=None, **kw: [
        fake_record(ip, u=20.0 + i) for i, ip in enumerate(sorted(ips))]
    r = harness().call({"id": 103, "cmd": "measure", "n": 2, "windows": 3},
                       timeout=60)
    d = r["data"]
    assert not d["cancelled"], d
    names = [os.path.basename(p) for p in d["run_dirs"]]
    bases = {n.rsplit("_w", 1)[0] for n in names}
    assert len(bases) == 1, f"windows not grouped under one ID: {names}"
    bid = bases.pop()
    assert names == [f"{bid}_w1", f"{bid}_w2", f"{bid}_w3"], names
    runs = os.path.join(_TMP, "runs")
    agg = os.path.join(runs, f"windows-{bid}.aggregate.json")
    assert os.path.exists(agg), os.listdir(runs)
    assert not os.path.exists(os.path.join(runs, "aggregate.json")), \
        "shared runs/aggregate.json is overwritten by every benchmark"
    rec = json.load(open(agg))
    assert "exploratory" in rec["notes"], rec["notes"].keys()


@test
def aggregate_caveat_absent_with_enough_windows():
    d = tempfile.mkdtemp(dir=_TMP)
    wins = []
    for w in range(4):
        wd = os.path.join(d, f"20990101T000000Z_w{w + 1}")
        os.makedirs(wd)
        with open(os.path.join(wd, "deepbench-results.jsonl"), "w") as f:
            f.write(json.dumps(fake_record("192.0.2.1", u=20 + w)) + "\n")
        json.dump({"ranked": [{"ip": "192.0.2.1", "u_med": 20 + w,
                               "u_p90": 21 + w, "c_med": 5,
                               "category": "X"}]},
                  open(os.path.join(wd, "ranked.json"), "w"))
        wins.append(wd)
    m = os.path.join(d, "windows-20990101T000000Z.json")
    json.dump({"windows": wins}, open(m, "w"))
    rec = stability.aggregate(m)
    assert "exploratory" not in rec["notes"], rec["notes"]


# ---------------------------------------------------------------- #7

def _reply(an_count, answers):
    q = dnslib.build_query("x.example.com", 0x1234)
    hdr = struct.pack(">HHHHHH", 0x1234, 0x8180, 1, an_count, 0, 0)
    return hdr + q[12:] + answers


@test
def parse_reply_rejects_missing_answer_records():
    assert dnslib.parse_reply(_reply(1, b""), "x.example.com", 0x1234) is None


@test
def parse_reply_rejects_truncated_answer_rdata():
    rr = struct.pack(">HHHIH", 0xC00C, 1, 1, 60, 4) + b"\x01\x02"  # 2 of 4
    assert dnslib.parse_reply(_reply(1, rr), "x.example.com", 0x1234) is None


@test
def parse_reply_accepts_complete_answers():
    rr = struct.pack(">HHHIH", 0xC00C, 1, 1, 60, 4) + b"\x01\x02\x03\x04"
    cname = (b"\x03www\x07example\x03com\x00" + struct.pack(">HHIH", 5, 1, 60, 2)
             + b"\xc0\x0c")
    assert dnslib.parse_reply(_reply(2, rr + cname), "x.example.com",
                              0x1234) == (0, 2)


@test
def parse_reply_rejects_out_of_bounds_compression_pointer():
    rr = struct.pack(">HHHIH", 0xFFFF, 1, 1, 60, 4) + b"\x01\x02\x03\x04"
    assert dnslib.parse_reply(_reply(1, rr), "x.example.com", 0x1234) is None


@test
def parse_reply_rejects_compression_loop():
    # owner name at offset of itself: pointer to its own position (cycle)
    base = len(_reply(0, b""))
    rr = struct.pack(">H", 0xC000 | base) + struct.pack(">HHIH", 1, 1, 60, 4) \
        + b"\x01\x02\x03\x04"
    assert dnslib.parse_reply(_reply(1, rr), "x.example.com", 0x1234) is None


@test
def parse_reply_rejects_a_record_with_wrong_rdlength():
    for n in (0, 3, 5):
        rr = struct.pack(">HHHIH", 0xC00C, 1, 1, 60, n) + b"\x01" * n
        assert dnslib.parse_reply(_reply(1, rr), "x.example.com", 0x1234) is None, n


@test
def parse_reply_rejects_cname_with_malformed_target():
    rr = struct.pack(">HHHIH", 0xC00C, 5, 1, 60, 2) + b"\xff\xff"
    assert dnslib.parse_reply(_reply(1, rr), "x.example.com", 0x1234) is None


@test
def helper_run_ids_are_parseable_run_ids():
    """Every ID the helper can generate (incl. collision suffixes) must match
    the grammar the app parses: <YYYYMMDDTHHMMSSZ>[-k][_wN]."""
    import re
    grammar = re.compile(r"^\d{8}T\d{6}Z(-\d+)?(_w\d+)?$")
    os.makedirs(os.path.join(_TMP, "runs"), exist_ok=True)
    a = helper._new_benchmark_id()
    os.makedirs(os.path.join(_TMP, "runs", a), exist_ok=True)
    b = helper._new_benchmark_id()
    for rid in (a, b, f"{b}_w1"):
        assert grammar.match(rid), rid
    assert a != b


# ---------------------------------------------------------------- #13

@test
def checkpoint_loader_tolerates_torn_last_line():
    import discover
    d = tempfile.mkdtemp(dir=_TMP)
    p = os.path.join(d, "live-phase1.jsonl")
    with open(p, "w") as f:
        f.write('{"ip": "192.0.2.1", "alive": true}\n{"ip": "192.0.2.')
    done = discover.load_checkpoint(p)
    assert [r["ip"] for r in done] == ["192.0.2.1"], done
    with open(p) as f:
        assert f.read().endswith("\n"), "torn tail not repaired before append"
    discover.load_checkpoint(p)  # idempotent
    with open(p) as f:
        lines = f.read().splitlines()
    assert lines == ['{"ip": "192.0.2.1", "alive": true}'], lines


# ---------------------------------------------------------------- #14

@test
def categorize_matches_labels_not_substrings():
    assert measure.categorize("192.0.2.1", "mantto.example.net") != "NTT"
    assert measure.categorize("192.0.2.1", "anttech-resolver.example") != "NTT"
    assert measure.categorize("192.0.2.1", "dns.gblx-like.example") != \
        "Level3/Savvis"
    assert measure.categorize("192.0.2.1", "ns1.ntt.net") == "NTT"
    assert measure.categorize("192.0.2.1", "res1.dns.gblx.net") == \
        "Level3/Savvis"
    assert measure.categorize("192.0.2.1", "a23-1.deploy.static.akamaitechnologies.com") == "Akamai"
    assert measure.categorize("192.0.2.1", "dns9.quad9.net") == "Quad9"
    assert measure.categorize("192.0.2.1", "dns.google") == "Google"


# ---------------------------------------------------------------- #15

class RcodeResponder(test_engine.Responder):
    """Answers with a fixed rcode and no answers."""

    def __init__(self, rcode):
        super().__init__()
        self.rcode = rcode

    def run(self):
        while not self.stop.is_set():
            try:
                data, addr = self.sock.recvfrom(4096)
            except OSError:
                continue
            i = 12
            while data[i] != 0:
                i += 1 + data[i]
            qend = i + 5
            resp = (struct.pack(">HHHHHH", struct.unpack(">H", data[:2])[0],
                                0x8180 | self.rcode, 1, 0, 0, 0) + data[12:qend])
            self.sock.sendto(resp, addr)


@test
def servfail_and_refused_are_failures_not_latency_samples():
    for rc in (2, 5):
        rsp = RcodeResponder(rc)
        rsp.start()
        try:
            tasks = test_engine.uncached_tasks(5)
            recs = dnslib2.run_interleaved([("127.0.0.1", tasks)],
                                           test_engine._Sink(), timeout=1.0,
                                           port=rsp.port)
        finally:
            rsp.stop.set()
        r = recs[0]
        assert r["uncached"] == [], f"rcode {rc} counted as latency: {r}"
        assert r["fail_u"] == 5, r


@test
def nxdomain_still_counts_as_latency_sample():
    rsp = RcodeResponder(3)
    rsp.start()
    try:
        recs = dnslib2.run_interleaved([("127.0.0.1",
                                         test_engine.uncached_tasks(5))],
                                       test_engine._Sink(), timeout=1.0,
                                       port=rsp.port)
    finally:
        rsp.stop.set()
    assert len(recs[0]["uncached"]) == 5, recs[0]
    assert recs[0]["fail_u"] == 0


@test
def rank_reports_uncached_failures():
    d = tempfile.mkdtemp(dir=_TMP)
    a = fake_record("192.0.2.1")
    a["fail_u"] = 7
    measure.rank([a], d)
    g = json.load(open(os.path.join(d, "ranked.json")))["ranked"][0]
    assert g["fail_u"] == 7, g


if __name__ == "__main__":
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} passed")
    sys.exit(1 if bad else 0)
