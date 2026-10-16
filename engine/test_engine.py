#!/usr/bin/env python3
"""Regression test for the dnslib2 kqueue engine (offline, deterministic).

Born from a validated engine defect (2026-10-16): _complete() never cleared
st.t0/st.qid after a successful reply. The send pass gates on "t0 is None",
so the server looked still-busy and got skipped; the timeout scan then fired
a PHANTOM timeout against the NEXT task without it ever being sent. Symptom:
strict alternating success/phantom-timeout per server, identical across
independent resolvers, zero strays (half the queries were never sent).

This test runs the engine against a local in-process DNS responder: no
network, no root, port 53 NOT required (port parameter, default 53 kept).
Responder modes exercised:
  - immediate responses
  - deterministic DELAY (every response held delay_ms before sending)
  - deterministic REORDER (each due batch sent in REVERSED arrival order;
    guarantees response inversions whenever 2+ responses come due together)
Any engine state-management defect shows up as false timeouts, strays,
missing completions, or leaked outstanding queries (non-termination).

Detector self-check: re-runs scenarios with the historical defect
monkeypatched back in and ASSERTS the harness flags them, proving the test
catches the defect it was written for.

Run: python3 test_engine.py   (prints ENGINE REGRESSION PASS, exit 0)
"""
import json
import random
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, __import__("os").path.dirname(__file__))
import dnslib
import dnslib2

TIMEOUT = 1.0


class Responder(threading.Thread):
    """Answers every query with a valid A response echoing txid+question.

    delay_ms: hold each response this many ms before sending (deterministic).
    reorder: within a due batch, send in REVERSED arrival order.
    """

    def __init__(self, delay_ms=0.0, reorder=False):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.002)
        self.port = self.sock.getsockname()[1]
        self.delay_ms = delay_ms
        self.reorder = reorder
        self.received = []          # txids of queries actually received, in order
        self.due = []               # (due_monotonic, seq, addr, resp)
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                pass
            except ConnectionRefusedError:
                continue            # client socket already closed; keep serving
            except OSError:
                continue
            else:
                txid = struct.unpack(">H", data[:2])[0]
                self.received.append(txid)
                i = 12
                while data[i] != 0:
                    i += 1 + data[i]
                i += 1
                qend = i + 4
                resp = (struct.pack(">HHHHHH", txid, 0x8180, 1, 1, 0, 0)
                        + data[12:qend]
                        + struct.pack(">HHHIH", 0xC00C, 1, 1, 60, 4)
                        + b"\x01\x02\x03\x04")
                self.due.append((time.monotonic() + self.delay_ms / 1000.0,
                                 len(self.received), addr, resp))
            # flush everything whose delay has elapsed
            now = time.monotonic()
            ready = [d for d in self.due if d[0] <= now]
            if ready:
                for d in ready:
                    self.due.remove(d)
                ready.sort(key=lambda d: d[1])
                if self.reorder:
                    ready.reverse()  # deterministic inversion of arrival order
                for _due, _seq, addr, resp in ready:
                    try:
                        self.sock.sendto(resp, addr)
                    except OSError:
                        pass

    def queries_seen_from(self, baseline):
        return list(self.received[baseline:])


def uncached_tasks(n):
    return [("uncached", f"{dnslib.rand_label(20)}.regression.test") for _ in range(n)]


def mixed_tasks():
    return ([("liveness", "www.google.com")]
            + uncached_tasks(3)
            + [("prime", "example.com")] * 2
            + [("cached", "example.com")] * 2)


class _Sink:
    """fw-compatible per-query JSONL sink that parses lines for inspection."""

    def __init__(self):
        self.lines = []

    def write(self, s):
        self.lines.append(json.loads(s))


def run_scenario(rsp, server_count, tasks_fn, timeout=TIMEOUT):
    """Returns (fw_lines, records, sent_txids). Sequential txids (QidGen(100)).
    Each server gets a FRESH task list (unique random labels per server)."""
    sink = _Sink()
    baseline = len(rsp.received)
    specs = [("127.0.0.1", tasks_fn()) for _ in range(server_count)]
    records = dnslib2.run_interleaved(specs, sink, timeout=timeout,
                                      port=rsp.port)
    return sink.lines, records, rsp.queries_seen_from(baseline)


def check_scenario(fw_lines, records, expected_tasks, sent_txids, label,
                   exp_unc, exp_cac):
    """All acceptance criteria, scoped to this scenario. Returns violations.
    Per-server completeness comes from records (authoritative); fw_lines are
    used for global timeout/stray/alternation checks (ips collide for N>1
    local servers by design)."""
    bad = []
    completions = [l for l in fw_lines if l["rcode"] not in (-2, -3)]
    timeouts = [l for l in fw_lines if l["rcode"] == -2]
    # exactly-once: every task completed exactly once
    if len(completions) != expected_tasks * len(records):
        bad.append(f"{label}: {len(completions)} completions != "
                   f"{expected_tasks * len(records)} expected")
    # no false timeouts, no lost datagrams
    if timeouts:
        bad.append(f"{label}: {len(timeouts)} false timeouts: "
                   f"{[(l['ip'], l['name'], l['ms']) for l in timeouts[:4]]}")
    # no leaked outstanding queries: engine terminates only when the
    # outstanding set drains, and responder saw exactly one query per task
    if len(sent_txids) != expected_tasks * len(records):
        bad.append(f"{label}: responder saw {len(sent_txids)} queries != "
                   f"{expected_tasks * len(records)} (leak or unsent)")
    if len(sent_txids) != len(set(sent_txids)):
        bad.append(f"{label}: duplicate query txids (unexpected retransmit)")
    for r in records:
        if "error" in r:
            bad.append(f"{label}: {r['ip']} socket error {r['error']}")
            continue
        if r.get("dead"):
            bad.append(f"{label}: {r['ip']} marked dead (false liveness failure)")
        if r["strays"]:
            bad.append(f"{label}: {r['ip']} {r['strays']} strays "
                       f"(valid responses discarded or unmatched)")
        if r["unreach"]:
            bad.append(f"{label}: {r['ip']} {r['unreach']} unreachable")
        # per-server task completeness: exactly-once, nothing leaked
        if len(r["uncached"]) + r["to_u"] != exp_unc:
            bad.append(f"{label}: {r['ip']} uncached {len(r['uncached'])}+"
                       f"to_u={r['to_u']} != {exp_unc}")
        if len(r["cached"]) + r["to_c"] + r["bad_c"] != exp_cac:
            bad.append(f"{label}: {r['ip']} cached {len(r['cached'])}+"
                       f"to_c={r['to_c']}+bad_c={r['bad_c']} != {exp_cac}")
        if r["to_u"] or r["to_c"]:
            bad.append(f"{label}: {r['ip']} to_u={r['to_u']} to_c={r['to_c']}")
    # strict alternation detector (the historical signature) — applicable when
    # the fw sequence belongs to a single server (ips are ambiguous otherwise)
    ips = {l["ip"] for l in fw_lines}
    if len(ips) == 1:
        seq = [l["rcode"] == -2 for l in fw_lines]
        for a, b in zip(seq, seq[1:]):
            if a != b:
                bad.append(f"{label}: alternating timeout pattern {seq}")
                break
    return bad


def detector_self_check(label, delay_ms=0.0, reorder=False, timeout=0.3):
    """Reintroduce the historical defect (missing t0/qid reset) and assert the
    harness detects it. Proves the test catches the defect it targets."""
    rsp = Responder(delay_ms=delay_ms, reorder=reorder)
    rsp.start()
    original = dnslib2._Engine._complete

    def buggy_complete(self, st, fw, kind, name, ms, rc, an, kd, batch=1):
        entry = {"ip": st.ip, "kind": kind, "name": name, "ms": round(ms, 1),
                 "rcode": rc, "an": an}
        fw.write(json.dumps(entry) + "\n")
        st.ti += 1
        # DEFECT: st.t0 / st.qid never cleared (the validated root cause)

    dnslib2._Engine._complete = buggy_complete
    try:
        fw_lines, records, sent = run_scenario(rsp, 1, lambda: uncached_tasks(20),
                                               timeout=timeout)
        bad = check_scenario(fw_lines, records, 20, sent, label, 20, 0)
        n_to = sum(1 for l in fw_lines if l["rcode"] == -2)
        n_ok = len(fw_lines) - n_to
        if not bad:
            return [f"{label}: detector FAILED to flag reintroduced defect "
                    f"(sent={len(sent)} ok={n_ok} timeouts={n_to})"]
        print(f"  {label}: defect reintroduced -> flagged "
              f"(sent={len(sent)} ok={n_ok} false-timeouts={n_to}); detector OK")
        return []
    finally:
        dnslib2._Engine._complete = original
        rsp.stop.set()


def main():
    p, t = dnslib.selftest()
    if p != t:
        print(f"dnslib selftest {p}/{t} FAILED")
        return 1
    print(f"dnslib selftest {p}/{t}")
    violations = []

    # 1) detector self-checks FIRST: buggy implementation must FAIL the harness
    violations += detector_self_check("selfcheck/immediate")
    violations += detector_self_check("selfcheck/delay+reorder", delay_ms=30, reorder=True)

    # 2) fixed implementation: concurrency ladder with delay + reorder active
    for delay, reorder, tag in ((0.0, False, "immediate"),
                                (30.0, True, "delay30+reorder")):
        rsp = Responder(delay_ms=delay, reorder=reorder)
        rsp.start()
        for n in (1, 2, 4, 8, 16, 32, 64):
            random.seed(42 + n)  # deterministic engine send-order shuffle
            fw_lines, records, sent = run_scenario(rsp, n, mixed_tasks)
            bad = check_scenario(fw_lines, records, len(mixed_tasks()), sent,
                                 f"ladder[{tag}] n={n}", 3, 2)
            violations += bad
            print(f"  ladder[{tag}] n={n}: servers={len(records)} "
                  f"{'OK' if not bad else 'FAIL: ' + bad[0]}")
        # pure uncached sequence, 20 tasks, sequential txids (original repro shape)
        random.seed(7)
        fw_lines, records, sent = run_scenario(rsp, 1, lambda: uncached_tasks(20))
        bad = check_scenario(fw_lines, records, 20, sent, f"uncached-seq[{tag}]", 20, 0)
        violations += bad
        print(f"  uncached-seq[{tag}]: {'OK' if not bad else 'FAIL: ' + bad[0]}")
        rsp.stop.set()

    if violations:
        print("ENGINE REGRESSION FAIL:")
        for v in violations:
            print("  " + v)
        return 1
    print("ENGINE REGRESSION PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())