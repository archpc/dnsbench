#!/usr/bin/env python3
"""Shared DNS wire code for the dns-benchmark skill (stdlib only).
Full response validation per Pitfall #3: txID + question name + QTYPE/QCLASS(A/IN)
+ TC-bit rejection + source IP AND source port 53.
Run selftest() offline (9 crafted cases incl. truncation) before any measurement run.
"""
import random
import socket
import struct
import threading
import time

TIMEOUT = 2.0
SO_TIMESTAMP = 0x0400  # Darwin kernel rx-timestamp socket option
SCM_TIMESTAMP = 0x0002  # cmsg type macOS delivers for SO_TIMESTAMP (NOT 0x0400)


class QidGen:
    """Thread-safe unique transaction-ID source."""

    def __init__(self, start=100):
        self._n = start
        self._lock = threading.Lock()

    def next(self):
        with self._lock:
            self._n = (self._n + 1) % 65536
            return self._n


def build_query(name, qid):
    parts = name.rstrip('.').split('.')
    q = b''.join(bytes([len(l)]) + l.encode() for l in parts) + b'\x00'
    hdr = struct.pack('>HHHHHH', qid, 0x0100, 1, 0, 0, 0)
    return hdr + q + struct.pack('>HH', 1, 1)  # A / IN


def parse_reply(resp, want_qname, want_qid):
    """Return (rcode, ancount) only if the datagram is a fully matching response.
    Returns None for: wrong txid, non-response (QR=0), TC-marked, wrong question
    name, wrong QTYPE/QCLASS, malformed/truncated replies."""
    if len(resp) < 12:
        return None
    qid = struct.unpack('>H', resp[0:2])[0]
    flags = struct.unpack('>H', resp[2:4])[0]
    if qid != want_qid or not (flags & 0x8000):
        return None
    if flags & 0x0200:
        return None  # truncated reply
    qd, an = struct.unpack('>HH', resp[4:8])
    i = 12
    labels = []
    for _ in range(qd):
        while i < len(resp) and resp[i] != 0:
            if resp[i] & 0xC0:
                i += 2
                break
            labels.append(resp[i + 1:i + 1 + resp[i]].decode('ascii', 'replace'))
            i += 1 + resp[i]
        else:
            i += 1
        if i + 4 > len(resp):
            return None
        qtype, qclass = struct.unpack('>HH', resp[i:i + 4])
        i += 4
        break  # first question only
    qname = '.'.join(labels).lower().rstrip('.')
    if qname != want_qname.lower().rstrip('.'):
        return None
    if qtype != 1 or qclass != 1:
        return None
    return (flags & 0xF, an)


def parse_kts(control):
    """Parse a Darwin SO_TIMESTAMP cmsg payload (struct timeval: long tv_sec,
    int tv_usec, padded to 16 B on LP64) -> float epoch seconds, or None."""
    if not control or len(control) < 12:
        return None
    if len(control) >= 16:
        sec = struct.unpack_from('@l', control, 0)[0]
        usec = struct.unpack_from('@i', control, 8)[0]
    else:
        sec, usec = struct.unpack('@li', control[:12])
    return sec + usec / 1e6


def kdelta_ms(anc):
    """Extract the kernel->userspace receive delta (ms) from recvmsg ancdata,
    or None when no SCM_TIMESTAMP cmsg is present. Shared by both engines."""
    for lvl, typ, cdata in anc:
        if lvl == socket.SOL_SOCKET and typ == SCM_TIMESTAMP:
            kts = parse_kts(cdata)
            if kts is not None:
                return (time.time_ns() - kts * 1e9) / 1e6
            return None
    return None


def q_validated(sock, ip, name, qid, timeout=None, kdelta=None, port=53):
    """Send and recv until a fully validated response or timeout.
    Returns (ms, rcode, an, strays). rcode -2 = timeout. Strays counted, never timed.
    timeout=None uses the module-level TIMEOUT (read at call time so jobs can reconfigure).
    kdelta: optional list. When given (caller must have enabled SO_TIMESTAMP on
    sock), the kernel->userspace receive delta in ms is appended for every
    validated response (never for strays or timeouts)."""
    if timeout is None:
        timeout = TIMEOUT
    pkt = build_query(name, qid)
    t0 = time.perf_counter()
    sock.sendto(pkt, (ip, port))
    deadline = t0 + timeout
    strays = 0
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return (timeout * 1000, -2, 0, strays)
        sock.settimeout(remaining)
        try:
            if kdelta is None:
                resp, addr = sock.recvfrom(4096)
            else:
                resp, anc, _flags, addr = sock.recvmsg(4096, 512)
        except socket.timeout:
            return (timeout * 1000, -2, 0, strays)
        if addr[0] != ip or addr[1] != port:
            strays += 1
            continue
        r = parse_reply(resp, name, qid)
        if r is None:
            strays += 1
            continue
        if kdelta is not None:
            kd = kdelta_ms(anc)
            if kd is not None:
                kdelta.append(kd)
        return ((time.perf_counter() - t0) * 1000.0, r[0], r[1], strays)


def rand_label(n=14):
    return ''.join(random.choice('abcdefghijklmnopqrstuvwxyz0123456789') for _ in range(n))


def selftest():
    """Offline parser suite: 9 crafted cases incl. truncation. Returns (passed, total)."""
    name, qid = "www.google.com", 42

    def mkresp(qid_, qtype=1, qclass=1, tc=False, qr=True, rcode=0, an=1, trim=0):
        flags = (0x8000 if qr else 0) | (0x0200 if tc else 0) | rcode
        hdr = struct.pack('>HHHHHH', qid_, flags, 1, an, 0, 0)
        qname = b'\x03www\x06google\x03com\x00'
        pkt = hdr + qname + struct.pack('>HH', qtype, qclass)
        return pkt[:len(pkt) - trim] if trim else pkt

    cases = [
        ("valid A/IN", mkresp(qid), name, True),
        ("wrong qtype (AAAA)", mkresp(qid, qtype=28), name, False),
        ("wrong qclass (CH)", mkresp(qid, qclass=3), name, False),
        ("TC-marked", mkresp(qid, tc=True), name, False),
        ("not a response (QR=0)", mkresp(qid, qr=False), name, False),
        ("wrong txid", mkresp(qid + 1), name, False),
        ("wrong name", mkresp(qid), "other.example.com", False),
        ("SERVFAIL passes (timed, flagged later)", mkresp(qid, rcode=2), name, True),
        ("truncated question", mkresp(qid, trim=4), name, False),
    ]
    passed = 0
    for label, resp, want_name, expect in cases:
        got = parse_reply(resp, want_name, qid)
        if (got is not None) == expect:
            passed += 1
        else:
            print(f"SELFTEST FAIL: {label} -> {got}")
    return passed, len(cases)
