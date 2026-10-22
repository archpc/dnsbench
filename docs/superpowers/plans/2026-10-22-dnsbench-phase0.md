# DNSBench Phase 0 — Reference Baseline and Spikes: Implementation Plan

Status: DRAFT for review (planning only; nothing in this plan has been executed)
Date: 2026-10-22 · Branch: `v2/swift-engine` · Worktree: `/Users/sein/Code/dnsbench-v2`
Spec: `docs/superpowers/specs/2026-10-21-dnsbench-v2-native-architecture.md` (REV 2)
GRC research: `docs/research/2026-10-22-grc-v2-rea-report.md` (complete)

## Ground rules

* **Execution boundary:** this plan is presented, not executed. Phase 0 work
  starts only after the plan is approved. Every task below is classified:
  **[OFF]** offline (no network, no installs), **[INST]** install-dependent
  (E3-approved only), **[GATE]** approval-gated (network, or experiment-class
  execution, or app-level testing).
* **Preservation invariants (all tasks):** never modify `main`'s checkout;
  never touch the uncommitted `.gitignore`/`icon/` in `~/Code/dnsbench`; never
  modify the reference implementation, its tag, or files under
  `~/.hermes/artifacts/dnsbench-starlink-2026-10-19/`; never delete or rewrite
  GRC evidence; never record the purchaser download URL.
* Every task records: exact commands, exit codes, tool versions, output hashes
  (`shasum -a 256`), and a `docs/spikes/<task>.md` summary. Failure handling is
  per-task; **rollback boundary** = the commit/tag each task's outputs land in.
* Reviewer: one independent read-only reviewer at Phase 0 exit, per the
  phase-boundary rule. Reported evidence (reviewer claims) is treated as
  unverified until tasks P0.4/P0.5 reproduce it.

## P0.1 [OFF] Reference worktree and golden harness scaffolding

* Commands: `git -C ~/Code/dnsbench worktree add ~/.hermes/artifacts/dnsbench-v2/reference python-reference-v1`
  (read-only reference checkout); in the v2 worktree create `reference/`
  containing: `harness_wire.py` (emits wire corpus JSONL: each RFC 9267 §2–6
  anti-pattern, the 10 selftest cases, F1–F6 fixtures, plus mutations of valid
  replies), `harness_traces.py` (scenario traces + `random.seed(N)` per window,
  driving `run_interleaved` through the fake-selector/socket/clock harness),
  `harness_analysis.py` (Starlink + synthetic edge cases → canonical analysis
  JSON), each with `--out` and deterministic output ordering.
* Artifacts: `reference/harness_*.py` + `reference/README.md` (one-command
  reproduction: `python3 reference/harness_all.py --out artifacts/goldens/`).
* Acceptance: harnesses run offline against the pinned reference checkout;
  outputs are byte-stable across two runs (hash equal); no reference file
  modified (`git -C ~/.hermes/artifacts/dnsbench-v2/reference status --short`
  empty).
* Failure handling: if the fake-selector harness needs to touch reference
  internals, the harness copies the engine modules into `reference/_harness/`
  and patches the copies; the real reference tree stays pristine.
* Rollback: delete `reference/` + the reference worktree; nothing else changes.

## P0.2 [OFF] Wire-corpus golden generation (reference side)

* Commands: `python3 reference/harness_wire.py --out artifacts/goldens/wire.jsonl`
* Artifacts: `wire.jsonl` — one record per case:
  `{id, bytes_hex, parse_reply_result (accept + rcode/ancount or reject), notes}`.
  Includes F1–F6 (accept, per reference) and the RFC 9267 anti-pattern set
  (reject, per reference).
* Acceptance: ≥ 1,000 cases; every RFC 9267 §2–6 anti-pattern present with an
  explicit id (`ap-compression-loop`, `ap-count-mismatch`, `ap-rdlen-overrun`,
  `ap-label-63`, `ap-name-255`, `ap-reserved-label`, …); file hash recorded.
* Failure handling: a selftest regression in the reference blocks everything
  (run the reference selftest first; it must be 10/10).
* Rollback: regenerate at will; corpus is committed with the task.

## P0.3 [OFF] Trace and analysis goldens

* Commands: `python3 reference/harness_traces.py --out artifacts/goldens/traces.jsonl`;
  `python3 reference/harness_analysis.py --out artifacts/goldens/analysis/`
* Artifacts: per-scenario per-query records (kind, qname, outcome, rcode,
  ancount, ms) and per-server results; analysis goldens from the Starlink
  dataset (read-only) plus synthetic edge cases (empty, single window, missing
  kd/category, median ties).
* Acceptance: scenarios cover delay/reorder/drop/duplicate/late-after-timeout/
  wrong-txid/ICMP-unreachable/SERVFAIL/REFUSED/NXDOMAIN/TC/malformed at
  concurrency 1…64; RNG seeding: `random.seed(N)` recorded per window in the
  golden header; the historical phantom-timeout defect scenario is included as
  the harness self-check (per spec R9).
* Failure handling: any nondeterminism (two runs differ) is a harness bug — fix
  the harness, never the reference.
* Rollback: same as P0.2.

## P0.4 [OFF] Parser-difference reproduction (F1–F6 + reviewer's four cases)

* Commands: `python3 reference/harness_wire.py --cases F --out artifacts/goldens/wire-F.jsonl`
* What: hand-build the six register fixtures (spec §3.1) and confirm the
  reference's accept/reject each way; then independently reproduce the prior
  reviewer's four reported cases (missing authority RR; missing additional RR;
  zero-answer with missing authority RR; 256-octet expanded owner name) against
  `engine/dnslib.py` at `python-reference-v1`.
* Acceptance: every F-case has a recorded reference result with the exact input
  bytes; the reviewer's claims are either reproduced (marked verified) or
  refuted (register corrected, reviewer note appended to the spec review log).
* Failure handling: disagreement between reviewer claim and reproduction → the
  reproduction wins; spec §3.1 table is patched with the observed behavior.
* Rollback: spec-table edit only.

## P0.5 [OFF] CPython RNG replication vectors

* What: emit reference vectors for `random.Random(12345)`: 10,000 × `choice`
  over a 38-char alphabet, 100 × `shuffle(list)` for n = 8…512, and the exact
  index stream of a 2,000-resample bootstrap (`rng.choice(meds) for _ in meds`)
  from `stability.py`'s code path. These vectors make the reviewer's 14,950-draw
  claim independently checkable and give Swift's `CPythonRandom` its test set.
* Acceptance: vectors generated twice, byte-identical; documented seed/algorithm
  (MT19937 + `init_by_array`, `getrandbits(k)=genrand>>(32-k)`, `_randbelow`
  rejection loop, Fisher-Yates `shuffle`) written into `reference/README.md`.
* Failure handling: none expected (pure stdlib). Any divergence between two
  runs blocks the task.
* Rollback: regenerate.

## P0.6 [INST→done] Independent parser oracle

* **Already executed under E3** (recorded in the spec header): dnspython 2.7.0
  in `~/.hermes/artifacts/dnsbench-v2/oracle/.venv` (uv 0.13.1, Python 3.13.15;
  `requirements.lock`; `dns/message.py` sha256 40eb5d14…). Remaining work: write
  `reference/oracle_compare.py` that runs the same wire corpus through
  dnspython's `dns.message.from_wire` with a recorded configuration
  (`one_rr_per_rrset=False`, default permissive flags) and emits a three-way
  comparison table (reference / dnspython / expected-Swift).
* Acceptance: comparison table committed; each disagreement classified as
  (a) RFC-required reject, (b) RFC-optional hardening, or (c) oracle
  configuration artifact. dnspython is evidence, never the authority.
* Failure handling: dnspython raising on a malformed input is a valid oracle
  result (recorded as reject-with-exception).
* Rollback: oracle script removal only.

## P0.7 [OFF] Loopback timing baseline of the Python engine

* What: run the reference engine (kqueue path) against a local scripted UDP
  responder at ladders 1/8/64 with the harness clock (real), recording per-query
  user-latency distribution and loop overhead. No network.
* Acceptance: baseline report `docs/spikes/p07-loopback-baseline.md` with p50/p99
  user latency and kernel-rx→user delay (reusing the V2 probe method); establishes
  the L3 tolerance sanity floor (≤0.25 ms median).
* Failure handling: responder port collisions → retry on an OS-assigned port.
* Rollback: report only.

## P0.8 [GATE] Spike R2 — kernel timestamps on a real interface

* What: repeat the V2 probe against a public resolver (one query burst ≤ 200
  packets over en0, rate ≤ 10/s) verifying `SCM_TIMESTAMP_MONOTONIC` presence and
  clock-domain equality vs `CLOCK_UPTIME_RAW` off-loopback.
* Requires: network approval (counts toward E4 budget: ≤ 200 queries).
* Acceptance: `docs/spikes/r2-en0-timestamps.md` with the same bracket statistics
  as V2. Absence of ancillary data on en0 → user-latency-only fallback is
  confirmed as the design default and the spec note updated.
* Failure handling: resolver unreachable → use any responsive public resolver
  from the pool; still blocked → report and keep V2 (loopback) as the only
  evidence.

## P0.9 [GATE] QoS comparison (spec §1.4 correction 5)

* What: with the Phase 1 skeleton (minimal kqueue driver + responder), measure
  `.userInitiated` vs `.userInteractive`: loopback probe timing perturbation,
  cancel-to-exit latency, main-thread responsiveness (hitch sampler), CPU,
  storage interference — under a representative load (UI spinning + export +
  reader).
* Requires: Phase 0 review gate (approval to build the minimal driver skeleton —
  the first engine code, still not "Swift engine implementation" beyond a
  measurement probe).
* Acceptance: `docs/spikes/r9-qos.md` with a chosen class and the measured
  deltas; the spec's default changes only if the data says so.
* Failure handling: if the skeleton isn't approvable in Phase 0, run the
  comparison with the Python engine's thread QoS adjusted — weaker evidence,
  labeled as such.

## P0.10 [GATE] Spike R5 — fsync/fullfsync jitter + L7 noise floor + pilot design

* R5 [OFF for the measurement harness itself; uses local disk only]: a
  fixture SQLite writer commits batches every 200 ms while the timing loop runs;
  compare `synchronous=FULL` vs `PRAGMA fullfsync=ON` for timing perturbation and
  commit latency. Acceptance: `docs/spikes/r5-fsync.md` + the storage-mode
  decision (spec §5.3).
* L7 noise floor [GATE — network]: two same-engine Python runs against 44
  resolvers (40 public + 4 forced refs; ≤ 40,832 queries total), computing the
  per-resolver absolute-difference noise floor and δ = max(0.5 ms, 2×floor).
  Inconclusive-by-design if floor > 1.5 ms.
* Pilot [GATE — separate approval, presented before any L7 run]: one Python/Swift
  pair cannot run until Phase 3, so the pilot validates only the Python side:
  1 run × 4 windows × 44 resolvers with rate limiting (≤ 4 outstanding queries,
  ≥ 20 ms spacing, ≈ 4.6 k queries), duration ≈ 15 min, stop conditions (loss > 5%
  → abort and report; no auto-retry), and the exact per-resolver target list
  committed as `reference/pilot-resolvers.txt`. Acceptance: pilot report with
  achieved rates, loss, and per-resolver medians; no protocol changes to margins
  or acceptance rules ever come from pilot data.

## P0.11 [OFF] Spike R1 — kevent timer precision

* What: microbenchmark kevent timeout wake error and `EVFILT_TIMER` vs
  `kevent`-timeout for 1–50 ms deadlines under load (only after P0.9's skeleton
  is approved — else use a standalone SwiftPM scratch target, which is not engine
  code).
* Acceptance: `docs/spikes/r1-timer.md`; the driver design picks one mechanism
  with the numbers attached.

## P0.12 [OFF] Phase 0 exit

* Aggregate: run all harnesses from one command; commit `artifacts/goldens/`
  (or their hashes if large), all `docs/spikes/*.md`, this plan's status notes.
* Independent read-only reviewer (phase boundary): reviews goldens, F-register
  reproduction, RNG vectors, spike reports, and spec consistency.
* Acceptance: reviewer findings triaged (REQUIRED/ADJACENT/SPECULATIVE/
  CLEANUP); spec review log updated; Phase 1 planning may then be drafted.
* Rollback boundary: tag `v2-phase-0` on the commit series after review
  triage.

## Unresolved decisions carried into the review

1. QoS class choice (data pending, P0.9) — spec default `.userInitiated`.
2. Storage mode FULL vs fullfsync per commit class (P0.10/R5).
3. δ and feasibility of L7 (P0.10 noise floor; pilot design approval).
4. Whether the four reviewer parser claims reproduce (P0.4) — spec table may
   change.
5. Pacing-knob defaults (Phase 7) informed by GRC's verified 20 ms/97-cap
   reference points; no commitment yet.
6. E4 accounting: spikes/pilot query counts above are additive to the
   285,824-query full experiment and each stays separately approved.