#!/usr/bin/env python3
"""kqueue single-thread DNS probe engine for the dns-benchmark skill (macOS).

Low-level optimizations (all optional or additive; wire semantics unchanged):
- ONE thread with selectors.KqueueSelector over nonblocking CONNECTED UDP
  sockets: no per-packet thread wakeup, no GIL handoffs, no E-core bounce.
  Also interleaves probes ACROSS servers (<=1 outstanding query per server,
  rotating send passes) so transient network events spread evenly over the
  whole pool instead of landing inside one server's measurement block.
- QOS_CLASS_USER_INTERACTIVE via pthread_set_qos_class_self_np: steers the
  loop thread to the P-cluster high-DVFS tier. THREAD_AFFINITY_POLICY is
  KERN_NOT_SUPPORTED on Apple Silicon; QoS is the supported lever.
- SO_TIMESTAMP kernel rx timestamps: per-query kernel->userspace delta
  ("kdelta") quantifies the scheduler-jitter noise floor.
- gc.freeze() + gc.disable() around the timed loop (GC pauses are ms-scale
  outlier injectors).
- Optional SO_NET_SERVICE_TYPE marking for SIDE EXPERIMENTS ONLY (it
  benchmarks a priority lane real resolvers/clients don't get). OFF default.

Response validation is dnslib.parse_reply unchanged (Pitfall #3).
"""
import ctypes
import gc
import json
import random
import selectors
import socket
import time

import dnslib

SO_NET_SERVICE_TYPE = 0x1116
NET_SERVICE_TYPE = {"be": 0, "bk": 1, "sig": 2, "vi": 3,
                    "vo": 4, "rv": 5, "av": 6, "oam": 7, "rd": 8}
QOS_CLASS_USER_INTERACTIVE = 0x21


def set_qos_user_interactive():
    """Put the calling thread in QOS_CLASS_USER_INTERACTIVE (P-cluster tier).
    Raises OSError with errno on failure."""
    lib = ctypes.CDLL("libSystem.dylib", use_errno=True)
    lib.pthread_set_qos_class_self_np.argtypes = [ctypes.c_uint, ctypes.c_int]
    lib.pthread_set_qos_class_self_np.restype = ctypes.c_int
    if lib.pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0) != 0:
        raise OSError(ctypes.get_errno(), "pthread_set_qos_class_self_np failed")


class _St:
    __slots__ = ("ip", "sock", "tasks", "ti", "t0", "qid", "res", "dead")

    def __init__(self, ip, sock, tasks):
        self.ip = ip
        self.sock = sock
        self.tasks = tasks
        self.ti = 0          # index of next task
        self.t0 = None       # send ns (CLOCK_UPTIME_RAW) while outstanding
        self.qid = None      # txid of outstanding query
        self.res = {"ip": ip, "uncached": [], "cached": [], "to_u": 0, "to_c": 0,
                    "bad_c": 0, "strays": 0, "unreach": 0, "kdeltas": []}
        self.dead = False


class _Engine:
    def __init__(self, timeout, netsvc=None, collect_krx=True, port=53):
        self.timeout_ns = int(timeout * 1e9)
        self.timeout = timeout
        self.netsvc = netsvc
        self.collect_krx = collect_krx
        self.port = port
        self.sel = selectors.KqueueSelector()

    def add(self, ip, tasks):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setblocking(False)
        s.connect((ip, self.port))  # kernel-level peer filtering
        s.setsockopt(socket.SOL_SOCKET, dnslib.SO_TIMESTAMP, 1)
        if self.netsvc is not None:
            s.setsockopt(socket.SOL_SOCKET, SO_NET_SERVICE_TYPE, self.netsvc)
        st = _St(ip, s, tasks)
        self.sel.register(s, selectors.EVENT_READ, st)
        return st

    def _drop(self, st):
        if st.dead:
            return
        st.dead = True
        try:
            self.sel.unregister(st.sock)
        except (KeyError, ValueError):
            pass
        try:
            st.sock.close()
        except OSError:
            pass

    def _send(self, st, fw, qid):
        kind, name = st.tasks[st.ti]
        q = qid.next()
        st.qid = q
        st.t0 = time.clock_gettime_ns(time.CLOCK_UPTIME_RAW)
        try:
            st.sock.send(dnslib.build_query(name, q))
        except OSError:
            # ICMP-driven failure on the connected socket (port unreach etc.)
            st.t0 = None
            st.res["unreach"] += 1
            self._complete(st, fw, kind, name, 0.0, -3, 0, None)

    def _timeout(self, st, fw):
        kind, name = st.tasks[st.ti]
        st.t0 = None
        self._complete(st, fw, kind, name, self.timeout * 1000.0, -2, 0, None)

    def _on_readable(self, key, fw, batch=1):
        st = key.data
        try:
            data, anc, _flags, _addr = st.sock.recvmsg(4096, 512)
        except ConnectionRefusedError:
            if st.t0 is not None:
                kind, name = st.tasks[st.ti]
                st.t0 = None
                st.res["unreach"] += 1
                self._complete(st, fw, kind, name, 0.0, -3, 0, None)
            else:
                st.res["strays"] += 1
            return
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            st.res["strays"] += 1
            return
        now = time.clock_gettime_ns(time.CLOCK_UPTIME_RAW)
        if st.t0 is None:
            st.res["strays"] += 1  # late reply for an already-timed-out query
            return
        kind, name = st.tasks[st.ti]
        r = dnslib.parse_reply(data, name, st.qid)
        if r is None:
            st.res["strays"] += 1
            return
        ms = (now - st.t0) / 1e6
        kd = None
        if self.collect_krx:
            kd = dnslib.kdelta_ms(anc)
        # only latency-bearing phases contribute to the server kdeltas list
        # (matches the thread engine; per-query "kd" stays in the JSONL for all)
        if kd is not None and kind in ("uncached", "cached"):
            st.res["kdeltas"].append(kd)
        self._complete(st, fw, kind, name, ms, r[0], r[1], kd, batch)

    def _complete(self, st, fw, kind, name, ms, rc, an, kd, batch=1):
        res = st.res
        st.t0 = None  # clear outstanding state (send pass gates on this)
        st.qid = None
        entry = {"ip": st.ip, "kind": kind, "name": name, "ms": round(ms, 1),
                 "rcode": rc, "an": an}
        if kd is not None:
            entry["kd"] = round(kd, 3)
        if batch > 1:
            entry["nb"] = batch  # events co-delivered in this select() batch
        fw.write(json.dumps(entry) + "\n")
        st.ti += 1
        if kind == "liveness":
            if not (rc == 0 and an > 0):
                res["dead"] = True
                self._drop(st)
            return
        if kind == "uncached":
            if rc == -2 or rc == -3:
                res["to_u"] += 1
            else:
                res["uncached"].append(round(ms, 1))
        elif kind == "cached":
            if rc == -2 or rc == -3:
                res["to_c"] += 1
            elif rc == 0 and an > 0:
                res["cached"].append(round(ms, 1))
            else:
                res["bad_c"] += 1  # zero-answer NOERROR etc.: recorded, not dropped

    def run(self, states, fw, qid):
        order = list(states)
        random.shuffle(order)
        n = len(order)
        si = 0
        try:
            while True:
                for k in range(n):
                    st = order[(si + k) % n]
                    if st.dead or st.t0 is not None or st.ti >= len(st.tasks):
                        continue
                    self._send(st, fw, qid)
                si = (si + 1) % n
                outstanding = [st for st in order if st.t0 is not None]
                if not outstanding:
                    break
                now = time.clock_gettime_ns(time.CLOCK_UPTIME_RAW)
                dl = min(st.t0 + self.timeout_ns for st in outstanding)
                wait = min((dl - now) / 1e9, 0.05)
                events = list(self.sel.select(wait if wait > 0 else 0))
                for key, _mask in events:
                    self._on_readable(key, fw, batch=len(events))
                now = time.clock_gettime_ns(time.CLOCK_UPTIME_RAW)
                for st in outstanding:
                    if not st.dead and st.t0 is not None \
                            and now >= st.t0 + self.timeout_ns:
                        self._timeout(st, fw)
        finally:
            for st in states:
                if not st.dead:
                    self._drop(st)


def run_interleaved(specs, fw, timeout=None, netsvc=None, collect_krx=True,
                    qid=None, port=53):
    """Run the deep-bench task lists of all servers in ONE kqueue event loop.

    specs: list of (ip, tasks); tasks = [(kind, name), ...] in strict per-server
    order (liveness -> uncached -> primes -> cached; cached phase stays fully
    primed, Pitfall #5). At most one outstanding query per server; send passes
    rotate through the server list so probes interleave across servers.
    fw: per-query JSONL sink (single-threaded, no lock needed).
    Returns measure.deep()-shaped records, plus "unreach" and "kdeltas" keys
    (measure.deep() itself has no "unreach"; only the kqueue engine tracks it).
    """
    if timeout is None:
        timeout = dnslib.TIMEOUT
    if qid is None:
        qid = dnslib.QidGen(100)
    records = []
    eng = _Engine(timeout, netsvc=netsvc, collect_krx=collect_krx, port=port)
    states = []
    for ip, tasks in specs:
        try:
            states.append(eng.add(ip, tasks))
        except OSError as e:
            records.append({"ip": ip, "error": repr(e)})
    gc.collect()
    gc.freeze()
    gc.disable()
    try:
        eng.run(states, fw, qid)
    finally:
        gc.enable()
    records.extend(st.res for st in states)
    return records
