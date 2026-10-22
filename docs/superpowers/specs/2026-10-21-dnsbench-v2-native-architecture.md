# DNSBench v2 — Native Swift Engine in a Supervised Worker (Architecture Option D)

Status: REV 2 · 2026-10-22 · Approved for Phase 0 planning only (E-boundaries below) · Author: lead architect (Hermes)

Authorization state (user decisions 2026-10-22):
* **E1** Developer ID distribution, hardened runtime, notarization; assume no
  certificate exists until verified; ad-hoc signing during development.
* **E2** Methodology changes deferred until reference parity passes; each judged
  separately with evidence. Parser hardening differences are documented, never
  weakened to match Python defects.
* **E3** APPROVED and DONE: dnspython 2.7.0 installed in the isolated oracle venv
  `~/.hermes/artifacts/dnsbench-v2/oracle/` (uv 0.13.1, Python 3.13.15,
  `requirements.lock` written, `dns/message.py` sha256
  `40eb5d14110039185328dd2e420f3ab9236fb61771271e341e13105d80811e78`). No global
  changes.
* **E4** No public-network testing without approval. A rate-limited pilot design
  is presented in the Phase 0 plan; pilot execution and the full 285,824-query
  experiment each require separate approval.
* **E5** APPROVED and DONE: branch `v2/swift-engine` in worktree
  `/Users/sein/Code/dnsbench-v2` based on `c136a63` (spec copied in with matching
  sha256, then removed from `main`'s tree; `main` retains only unrelated
  `.gitignore`/`icon/` changes). This revision is authored in the worktree.
* **E6** APPROVED, bounded static REA of `/Users/sein/Downloads/DNSBench.exe`
  (sha256 `da7da812…f12195`), time-boxed, read-only, no execution — COMPLETE.
  Report: `docs/research/2026-10-22-grc-v2-rea-report.md`; raw evidence (with
  purchaser data redacted) in `~/.hermes/artifacts/dnsbench-v2/grc-research/`.
  §7 rows updated only where the report supports them; unknowns stay unknown.

Reported-evidence labeling: the prior reviewer's MT19937 `choice` check
(14,950 draws) and its four parser cases are **reported evidence** until
independently reproduced; reproduction is a Phase 0 task (plan P0.4, P0.5).
Reference baseline: `python-reference-v1` → commit `033b995` (repaired Python engine + app)
Current `main`: `c136a63` (adds Timer-and-Resolver icon on top of the reference)

This document is the design of record. Implementation does not begin until it is
approved. After approval, a task-level plan is written per phase
(`docs/superpowers/plans/`) and executed with TDD and per-phase verification.

---

## 0. Corrections to the brief, verified facts, assumptions

**Correction.** The brief says "nothing is committed yet". That is no longer true:
`033b995` (tag `python-reference-v1`) and `c136a63` (icon) are committed on `main`.
Uncommitted and unrelated: `icon/` (design workspace) and a `.gitignore` line
(`history/`). They stay untouched.

**Verified constraints** (evidence in parentheses):

| # | Fact | Evidence |
|---|------|----------|
| V1 | macOS SDK exposes `SO_TIMESTAMP_MONOTONIC` (0x0800) delivering `SCM_TIMESTAMP_MONOTONIC` (0x04, `uint64_t`). | `MacOSX27.2.sdk/usr/include/sys/socket.h:139,683` |
| V2 | That timestamp is in `mach_absolute_time()` ticks: 2000/2000 loopback samples fell inside a `mach_absolute_time()` bracket taken before `sendto` and after `recvmsg`; kernel-rx→user-recv delay p50 5.8 µs, p99 15.5 µs (Python, single socket). `mach_absolute_time` (converted) minus `CLOCK_UPTIME_RAW` stayed in [−2.2, −0.1] µs, which is call-order offset, so it is the same clock as the reference's send timestamp. | throwaway probe on Honk, macOS 27.2, loopback only, **not yet committed**; Phase 0 re-runs and commits it under `docs/spikes/` |
| V3 | kqueue provides `EVFILT_USER`+`NOTE_TRIGGER` (cross-thread wake), `EVFILT_PROC`+`NOTE_EXIT` (parent death), `NOTE_CRITICAL` (no timer coalescing). | `sys/event.h:72-77,205,261,309` |
| V4 | Toolchain: Swift 6.4, Xcode 27.1, macOS 27.2 host. | `swift --version`, `xcodebuild -version` |
| V5 | An XPC service embedded in an app is private to that app; launchd launches it on demand and restarts it after a crash. | Apple, *Creating XPC Services* (BPSystemStartup) |
| V6 | An embedded command-line helper is a supported pattern; in a sandboxed app it needs only `app-sandbox` + `inherit`. An inherit-signed tool cannot be launched standalone (Stack Overflow report, secondary). Outside the Mac App Store a child inherits the parent's sandbox without the entitlements. | Apple, *Embedding a command-line tool in a sandboxed app* (primary); Apple DTS reply in developer forums thread 678819 (seen as a search excerpt only, page not re-fetched) |
| V7 | Compression pointers must point strictly backwards (this prevents loops), must not be followed more than once per hop, labels ≤63, names ≤255, RDATA length and record counts must be validated. | RFC 9267 §2–6; RFC 1035 §4.1.4 |
| V8 | GRC v2: 250 query sets default (1×–100×), 20 ms query spacing, sidelining after 10 tries within 50 ms, all queries weighted equally in ranking, IPv4/IPv6/DoH/DoT, ≤500 resolvers, CSV/text export. | grc.com/dns/benchmark.htm (fetched 2026-10-21) |
| V9 | GRC status taxonomy (working; never replies to bad domains; three intercept statuses; not answered; actively rejected; no recursion; invalid replies), rebinding ring, DNSSEC toggle, cached/uncached/dotcom bars. | grc.com/dns/operation.htm (fetched 2026-10-21) |
| V10 | Only one historical dataset survives: `~/.hermes/artifacts/dnsbench-starlink-2026-10-19` (pool + 3 windows + legacy shared `runs/aggregate.json`). `datasets/` is empty. | filesystem scan |
| V11 | No independent DNS parser is installed (no dnspython/ldns/tshark; `dig` only). | `command -v`, import probe |

**Assumptions (not yet verified; each has a risk-register entry):** kernel monotonic
timestamps behave the same on Wi-Fi/Ethernet as on loopback (R2); `kevent`
timeouts are not coalesced enough to distort timeout accounting (R1); raw UDP
sockets work under App Sandbox with `network.client` (R6); a Developer ID
certificate is available for notarization testing (R7).

**Unknown, and not claimed:** GRC's custom-list selection rule and its
Conclusions logic internals. Previously-unknown GRC behaviors (uncached and
dotcom construction, significance-test shape) were resolved by the static
research (report §3.1-3.2, §3.7) and are reflected in §7; the report's own [?]
items remain unknown.

---

## 1. Architecture specification

### 1.1 Process boundaries

```
┌──────────────────────── DNSBench.app ──────────────────────────────┐
│  Contents/MacOS/DNSBench        GUI process (SwiftUI + AppKit)      │
│     BenchmarkCoordinator ── WorkerSupervisor ──┐                    │
│     LibraryReader (read-only SQLite, off-main) │ stdin/stdout pipes │
│                                                │ framed, typed IPC  │
│  Contents/Helpers/dnsbench  ◄──────────────────┘                    │
│     one executable, two modes:                                      │
│       `dnsbench worker`  supervised measurement process             │
│       `dnsbench <cmd>`   first-class CLI (same binary)              │
└─────────────────────────────────────────────────────────────────────┘
          │ links (static)                         │ writes (single writer)
          ▼                                        ▼
   DNSBenchCore (SwiftPM)               Library/Benchmarks/<uuid>.dnsbench (SQLite)
```

The CLI is the worker. One binary means GUI benchmarks and CLI benchmarks run the
exact same code path. The only difference is that the IPC adapter replaces the
terminal renderer. "Install Command Line Tool…" (in the app menu) symlinks
`~/.local/bin/dnsbench` (or a user-chosen directory) to `Contents/Helpers/dnsbench`.

### 1.1.1 Deployment target and runtime assumptions

* Deployment target: **macOS 27.0** (the existing app project builds with
  `MACOSX_DEPLOYMENT_TARGET = 27.0`); Swift 6 language mode, Xcode 27.1.
* Runtime-library assumptions: system libsqlite3 (SDK), libSystem kqueue/socket
  APIs (`SO_TIMESTAMP_MONOTONIC` requires the macOS 27-era SDK header set, V1),
  SystemConfiguration for resolver enumeration. No third-party package
  dependencies.
* If a deployment target below 27 is ever needed, V1/V3 must be re-verified
  against that SDK before relying on kernel timestamps.

### 1.2 Worker choice: supervised bundled executable, not XPC

| Criterion | Bundled executable (chosen) | XPC service |
|---|---|---|
| Crash containment | Separate process; GUI survives. | Separate process; GUI survives. |
| Lifecycle | Explicit: app spawns, owns and terminates. Nothing restarts without a decision. | launchd relaunches on the next message (V5). Relaunch does not itself replay a measurement — the client would have to re-submit state — but it does create a second auto-restart path the design must reason about. |
| CLI reuse | Same binary is the CLI. | Engine code could be shared between an XPC service and a CLI (they are separate processes, not separate runtimes of the engine), but two IPC/supervision designs would be maintained. |
| IPC | Our framed protocol, testable from any process (CLI tests, Python harness). | NSXPC/xpc dictionaries; testing needs the app's bundle context. |
| Cancellation | Message + SIGTERM/SIGKILL fallback on a pid we own. | Message; forced kill is indirect. |
| Signing/notarization | Standard nested-code signing (`Contents/Helpers`), hardened runtime (V6). | Standard. |
| Sandbox | `inherit` model (V6). A sandboxed App Store build would need a separately signed CLI. | Own sandbox profile. |
| Debugging | `dnsbench worker` runs in a terminal, and you can attach lldb. | Needs attach-to-launchd workflow. |
| Maintenance | One protocol and one binary. | Two process-supervision and IPC designs. XPC also buys privilege separation, which this design does not need: the worker holds no privileges the app lacks. |

Decision: **bundled supervised executable**. XPC only helps with launchd-managed
relaunch, and the product explicitly forbids silent relaunch.

### 1.3 Package structure (`DNSBenchCore/`, SwiftPM, no third-party dependencies)

| Module | Responsibility | Depends on |
|---|---|---|
| `DNSWire` | Message encode/decode, names, RR walk, RFC 1035/9267 validation, reply classification. Pure, no I/O. | — |
| `DNSProbe` | UDP sockets, kqueue driver, timestamps, `ProbeScheduler` (pure state machine), cancellation, transports (real/scripted). | DNSWire |
| `DNSAnalysis` | Sample eligibility, per-window stats, ranking, aggregation, bootstrap (Python-compatible MT19937), rank bands, findings inputs. Pure. | — |
| `DNSStore` | `.dnsbench` SQLite schema, single-writer session, recovery, readers, export (JSON/CSV/text), legacy import. | DNSAnalysis (types) |
| `DNSBenchKit` | Orchestration (`BenchmarkRunner`, `Discovery`, `ResolverInventory`, `SystemResolvers`), IPC message types and framing (`WorkerProtocol`). | all above |

Executables: `dnsbench` (CLI + worker mode, ArgumentParser is **not** used; a
small hand-written parser keeps the binary dependency-free) and the app target.
Tests: `swift test` for the package; Xcode test target for the app.

Rationale: five modules map to five things that change independently. Wire and
analysis are pure, so they are the most testable. The probe module is the only
one that touches sockets. Storage is the only one that touches disk. No
"protocols for everything": protocol abstraction appears only at `Transport` and
`Clock`, which tests must substitute.

### 1.4 Concurrency model

* **Measurement thread.** One dedicated `Thread` per run; QoS class is
  **`.userInitiated` until a Phase 0 measurement says otherwise** — the class is
  chosen by benchmark, not by assumption. The Phase 0 QoS comparison (plan
  P0.9, execution gated behind the Phase 0 review) measures `.userInitiated`
  vs `.userInteractive` for: timing perturbation of loopback probes,
  cancel-to-exit latency, UI responsiveness under load, CPU use, and storage
  interference. Higher priority is not assumed better.
  Never a Swift-concurrency executor (the thread blocks in `kevent`). The
  Python reference pins its measurement threads to `QOS_CLASS_USER_INTERACTIVE`
  (`dnslib2.py:35-45`), so reference-mode runs (L3/L7) pin the Swift engine to
  the same class to avoid a QoS confound, and the reference setting is P0.9's
  baseline arm.
  It owns every socket, the kqueue fd and all scheduler state. No locks on the
  hot path.
* **Output.** The measurement thread appends `ProbeRecord`s to a
  `Mutex`-protected batch (Synchronization). A storage thread drains it every
  200 ms (or at 2,000 records) into one SQLite transaction. An IPC writer thread
  sends coalesced progress. The measurement thread never waits on disk or IPC.
  The batch is bounded **by records and by bytes** (200,000 records or 32 MiB,
  whichever first). At the bound the scheduler **pauses issuing new queries**
  (outstanding ones complete normally; reserved headroom keeps in-flight
  replies receivable) and logs a `storage_backpressure` event with the pause
  duration. Records are never dropped. Anomaly storage (strays, malformed
  datagrams) is separately capped at 10,000 entries per run with per-server
  counts; overflow increments a counter instead of storing bytes. A window that
  paused is flagged in the store so its timing can be judged. "No hot-path
  locks" means the **socket query path** never takes a lock: the one
  mutex-protected batch append is the only synchronized handoff, is off the
  socket path, and is never held across I/O.
* **Cancellation.** `BenchmarkRunner.run(...) async throws` uses
  `withTaskCancellationHandler`. The handler sets an atomic flag and fires
  `EVFILT_USER/NOTE_TRIGGER`, so the loop wakes immediately rather than at its
  next timeout. Target: cancel → loop exit p99 ≤ 20 ms (measured gate, §9).
* **GUI.** The main actor only mutates view models. All SQLite reads and
  decoding run in a `LibraryReader` actor with its own read-only connection (WAL
  allows concurrent readers). Results reach the main actor in batches of at most
  10 Hz.
* **Strict concurrency.** Swift 6 language mode, complete checking, no
  `@unchecked Sendable` except two audited types (the batch buffer and the
  socket-owning driver), each with a comment stating its confinement rule.

### 1.5 Ownership summary

| State | Owner | Mutated by |
|---|---|---|
| Job lifecycle, worker generation | `BenchmarkCoordinator` (MainActor) | IPC events, user intents |
| Worker process | `WorkerSupervisor` (inside coordinator) | spawn/terminate |
| Benchmark file during a run | worker (single writer, `flock` on `<file>.lock`) | storage thread |
| Benchmark file at rest | nobody (immutable after `completed/cancelled/interrupted`) | `dnsbench recover` only for `running`-with-no-writer |
| Library listing | `LibraryReader` | file system scan + cache |

---

## 2. Worker and IPC design

### 2.1 Supervision

```
App                                   Worker (dnsbench worker --gen 7)
 │ posix_spawn (argv: --gen 7, --proto 1) ─────────►│
 │◄────────────── hello_ack {proto 1, engine, build, caps}
 │ start_benchmark {job, config, store_path} ───────►│ accepted {job, benchmark_id}
 │◄────────────── progress … (≤10 Hz, latest-wins)   │
 │◄────────────── heartbeat (1 Hz, independent thread)
 │  cancel {job} ────────────────────────────────────►│ cancel_ack {job}
              (ack due within 10 s; expiry escalates to SIGTERM)
 │◄────────────── finished {job, outcome: cancelled, windows_complete: 2, partial: 1}
 │ shutdown {grace_ms} ─────────────────────────────►│ exits 0
```

* Generation: a `UInt64` the app increments per spawn and passes in argv. Every
  frame echoes it.
* Parent death: the worker watches `EVFILT_PROC/NOTE_EXIT` on its parent pid and
  stdin EOF. Either one cancels the job, finalizes the file as `interrupted`
  (reason `parent_gone`) and exits.
* Heartbeat path: the worker's heartbeat thread enqueues into the same single
  IPC writer thread as every other frame. The app reads the pipe on a dedicated
  background reader (never the main actor), so a busy UI cannot stall the pipe
  and trigger a false "unresponsive". Main-actor delivery is a separate, bounded
  hop.
* Heartbeat is a **liveness signal only**. It can stay healthy while
  measurement or storage is stuck, so the coordinator tracks three independent
  progress signals: `heartbeat` (1 Hz), `progress` (measurement counters,
  phase-specific), and `storage` (last committed sequence + backlog). Phase-
  specific stall detection: a job whose `progress` counter stops advancing for
  60 s while heartbeats continue is `stalled(measurement)`; a job whose
  committed sequence stops advancing while measurement counters advance for
  60 s is `stalled(storage)`. Both surface in the UI with a "Stop" path; neither
  auto-kills. Finalization is bounded: after `cancel_ack` the worker has 10 s to
  produce `finished`; on expiry it is SIGTERMed and the job becomes
  `interrupted`.
* Cancellation acknowledgement is bounded: a `cancel` that receives no
  `cancel_ack` within 10 s escalates to SIGTERM (then SIGKILL 3 s later), and
  the job ends `interrupted (worker_unresponsive)`. A worker may not sit in
  `cancel_requested` forever, even while heartbeating.
* Hung worker (no heartbeat): if no heartbeat arrives for 5 s while busy, the
  state is `unresponsive`. The UI shows it with "Terminate worker". The
  coordinator sends SIGTERM automatically only after 30 s without heartbeat,
  then SIGKILL 3 s later. In both cases the job ends
  `interrupted (worker_unresponsive)`.
* No automatic relaunch. After a crash the coordinator returns to `idle` and
  offers "Run again". A new worker (new generation) is spawned only on a user
  action.

### 2.2 Framing and envelope

Frames are a 4-byte big-endian length followed by UTF-8 JSON. The maximum is
1 MiB; anything larger is a protocol error that terminates the worker. JSON
keeps every frame inspectable in a terminal and in failure logs.

```json
{ "proto": 1, "gen": 7, "seq": 42, "job": "3F2A…-UUID", "type": "progress",
  "body": { "phase": "window", "window": 2, "windows": 4,
            "queries_done": 34112, "queries_total": 75000 } }
```

### 2.3 Messages (protocol 1)

| Direction | Type | Body (key fields) | Notes |
|---|---|---|---|
| A→W | `hello` | `proto_min`, `proto_max`, `app_version` | first frame |
| W→A | `hello_ack` | `proto`, `engine_version`, `build`, `capabilities[]` | or `incompatible` then exit 64 |
| A→W | `start_benchmark` | `job`, `config`, `store_path` | |
| A→W | `start_discovery` | `job`, `config`, `store_path` | |
| W→A | `accepted` / `rejected` | `job`, `benchmark_id` / `error` | exactly one per start |
| W→A | `progress` | phase, counters | latest-wins, droppable |
| W→A | `resolver_update` | `resolver_id`, window, running medians, counts | coalesced per resolver, droppable |
| A→W | `cancel` | `job`, `reason` | idempotent |
| W→A | `cancel_ack` | `job`, `at_mono_ns` | the loop has stopped issuing queries |
| W→A | `finished` | `job`, `outcome` (`completed`/`cancelled`/`failed`), `error?`, `windows_complete`, `windows_partial`, `records_persisted` | exactly one per accepted job |
| W→A | `heartbeat` | `busy_job?` | 1 Hz |
| W→A | `log` | `level`, `message` | |
| A→W | `ping` → W→A `pong` | | liveness probe |
| A→W | `shutdown` | `grace_ms` | worker cancels any job, finishes, exits 0 |

Structured errors: `{code, message, detail?}` with stable codes:
`config_invalid`, `store_unwritable`, `no_eligible_resolvers`, `pool_missing`,
`network_unavailable`, `selftest_failed`, `internal`, `protocol_violation`.

**Backpressure.** Raw samples never cross IPC; they go straight to the store.
Droppable message types use latest-wins slots. Terminal messages (`accepted`,
`rejected`, `cancel_ack`, `finished`, `incompatible`) go through a queue that
blocks only the IPC writer thread. **Compatibility.** The app accepts
`proto ∈ [min,max]`. Unknown message types from a newer worker are logged and
ignored unless they are terminal-class, in which case it is a protocol
violation. Unknown fields are ignored.

**Sequencing and stale-message rejection.** `seq` is assigned by the worker's
single IPC writer thread **at write time**, so the wire order equals the `seq`
order for every frame, droppable or not. The app applies a frame only if
`gen == current generation` and `job` is the active job (or absent for
worker-scoped types). Droppable types additionally require `seq > last applied
seq`. Terminal-class types are **never** rejected for seq. They are deduplicated
on `(gen, job, type)`, and a duplicate terminal is a logged protocol violation.
Everything rejected is counted in a diagnostic. Protocol test: force a
`progress` frame to be enqueued while a `finished` is pending, and assert that
`finished` is applied exactly once.

**Pipe break.** If the worker dies before writing `finished`, the app never
synthesizes one. The job goes to `interrupted` (§2.4) and the file is recovered
(§5.3). **Committed vs delivered:** an IPC failure does not imply the benchmark
was lost — the worker may have committed the terminal status transaction before
dying. Distinguishing "interrupted IPC delivery" from "already committed
completed benchmark" is a required test case (P-4 below): after any transport
failure the coordinator inspects the file's status under the writer-lock rule
(§5.3) and reports the on-disk truth. Terminal-frame guarantees therefore
acknowledge transport failure: exactly-once applies to frames the app actually
applies; the file remains the authority.

### 2.4 Coordinator state machines

Worker:

```
absent ─spawn─► launching ─hello_ack─► ready ◄──finished── busy
   ▲                │ timeout 5s           │ start→accepted ▲
   │                ▼                      └────────────────┘
   └──────── exited(code) / crashed(signal) / incompatible ◄── any (process end)
```

Job (one active job at a time; the worker also enforces this):

```
draft ─submit─► submitted ─accepted─► running ─user cancel─► cancel_requested
                   │ rejected            │                      │ cancel_ack
                   ▼                     │ finished             ▼
                rejected                 ▼                 cancel_acknowledged
                                     finalizing ◄───────────────┘ finished(cancelled)
                                         ▼
                completed │ cancelled │ failed(code) │ interrupted(worker_lost|unresponsive|parent_gone)
```

`finished` carries the terminal outcome, and `finalizing` is the app-side state
between `cancel_ack` and `finished` (the worker is flushing). There is no other
`finalizing`. The complete job table (rows: state, columns: event; `—` =
illegal → logged, ignored):

| state \ event | accepted | rejected | progress | cancel (user) | cancel_ack | finished(o) | worker_exit | hb_timeout 30 s | shutdown (quit/dir) |
|---|---|---|---|---|---|---|---|---|---|
| submitted | running | rejected | — | cancel_requested¹ | — | — | interrupted(worker_lost)² | interrupted(unresponsive)² | cancel_requested¹ |
| running | — | — | running | cancel_requested | — | terminal(o) | interrupted(worker_lost) | interrupted(unresponsive) | cancel_requested |
| cancel_requested | cancel_requested | rejected | cancel_requested | (no-op) | finalizing | terminal(o)³ | interrupted(worker_lost) | interrupted(unresponsive) | (no-op) |
| finalizing | — | — | finalizing | (no-op) | (no-op) | terminal(o) | interrupted(worker_lost) | interrupted(unresponsive) | (no-op, wait ≤10 s then SIGTERM → interrupted) |
| terminal states | — | — | — | — | — | dup → violation | — | — | — |

1. A `cancel` for a job the worker has received is legal even before
   `accepted` reaches the app. The worker processes frames in order, so it
   accepts and then cancels, giving `finished(cancelled)` with zero windows.
2. No file may exist yet. If one does, it is recovered.
3. `finished(cancelled)` without a preceding `cancel_ack` is logged as a protocol
   violation, and the outcome is still taken from `finished`.

**Who writes the file status:**
* `completed`/`cancelled`/`failed`: the worker, inside the transaction that ends
  the job, before it sends `finished`.
* `interrupted`: written by the worker only when it can still act (the
  `parent_gone` case: the worker is alive, holds the writer lock, and finalizes
  before exiting). When the worker is already dead or is SIGTERMed mid-drain,
  nobody writes it: the UI shows the job as interrupted immediately, the file
  stays `running`, and readers derive "interrupted (not finalized)" from the
  free writer lock (§5.3).
* The next worker the coordinator spawns runs `recover` on that file during its
  handshake and records `status_reason` (`worker_lost` with the exit signal, or
  `unresponsive`).

This is status finalization, not a measurement restart: no query is re-issued.

Additional required adversarial cases (protocol tests):
* **P-1** terminal transaction committed on disk, worker dies before `finished`
  reaches the app → job `interrupted`, file shows `completed`; UI must present
  the file's status, not the transport outcome;
* **P-2** cancel racing normal completion (`cancel` and `finished(completed)`
  cross in flight) → the first terminal wins, the other is a logged violation;
  file status decides;
* **P-3** storage stalled (backlog grows) while heartbeat continues →
  `stalled(storage)` surfaced, no auto-kill, Stop still works;
* **P-4** IPC blocked during cancellation (reader thread stalled by the UI
  thread) → dedicated reader keeps draining (§2.1), bounded finalization still
  applies, and after transport recovery the committed-vs-delivered rule decides;
* **P-5** `shutdown` during `finalizing` → bounded wait, then terminate;
  file ends `completed` or `cancelled` per what was committed.

Worker table: `absent → launching` (spawn), `launching → ready` (`hello_ack`),
`launching → crashed` (exit before ack, or 5 s timeout → SIGKILL),
`ready ↔ busy` (`accepted` / terminal), `busy → unresponsive` (5 s without
heartbeat; returns to busy on the next heartbeat), and any state `→ exited(code)
| crashed(signal) | incompatible` on process end. Nothing goes from
`exited`/`crashed` to `launching` without a user action.

Legal transitions are encoded as a `switch` over `(state, event)` that returns
the next state or `.illegal`. Illegal pairs are logged and ignored, never
crashed on. The three cancellation facts are distinct and visible in the UI:

* **requested:** "Stopping…"
* **acknowledged:** "Stopped issuing queries; saving partial data…"
* **confirmed terminal:** "Cancelled: 2 complete windows, 1 partial window kept"

A worker crash in `cancel_requested` or `finalizing` ends as `interrupted`,
not `cancelled`, because no `finished` arrived.

**Conflicts.** Start is refused while a job exists. Changing the data directory
or quitting during a run asks "Stop benchmark and …?". It then cancels, waits for
the terminal state (up to 10 s), sends `shutdown`, and proceeds. View switching
never touches the coordinator.

---

## 3. DNSBenchCore design

### 3.1 DNSWire

Parsing is split into two layers so hardening never masquerades as parity:

* **Structural parse** (`DNSMessage.parse`): well-formedness of the message as a
  DNS message — header, name compression (RFC 1035 §4.1.4), RR framing
  (RDLENGTH within bounds), RDATA shapes for typed records, and the counts
  actually walked. Standards basis: RFC 1035 (normative) with RFC 9267 treated
  as **informational security guidance** informing conservative choices, not as
  acceptance requirements.
* **Benchmark acceptance policy** (`ReplyValidator`, §3.1): which structurally
  valid messages count as usable measurement replies for this benchmark — txid,
  QR, opcode, TC, question echo, answer presence. A NOERROR reply with a
  nonempty answer section is **not** by itself a verified usable answer for the
  requested name/type; policy-level checks (question echo, expected rrtype in
  the answer where applicable) provide that, and the distinction is carried into
  findings/eligibility wording.

The Python reference's parser walks only the question and answer sections
(`dnslib.parse_reply`: `qd` and `an` loops; NSCOUNT/ARCOUNT are neither checked
nor walked), requires names ≤ 255 octets via `total > 255` rejection (so exactly
256 pass), validates A RDLENGTH == 4 and CNAME RDATA as one valid name, and
rejects forward/self compression pointers. Known acceptance differences
(reference accepts, Swift rejects) — each gets a named fixture and a
standards-backed disposition:

| ID | Case | Reference behavior | Swift behavior | Disposition |
|---|---|---|---|---|
| F1 | reply advertising NSCOUNT≥1 but carrying no authority RR | accepted (NS section never walked) | rejected: advertised RRs must be present | RFC 9267 §6 anti-pattern (count/content mismatch); hardening, documented |
| F2 | same for the additional section (ARCOUNT≥1, no additional RRs) | accepted | rejected | as F1 |
| F3 | zero-answer reply advertising a missing authority RR | accepted | rejected | as F1; the zero-answer shape stays eligible otherwise |
| F4 | expanded answer-owner name of exactly 256 octets | accepted (`total > 255` off-by-one) | rejected (names ≤ 255 octets) | RFC 1035 §2.3.4/§4.1.4 name limit; hardening, documented |
| F5 | opcode ≠ 0 | accepted (opcode ignored) | rejected | policy hardening (E2 note) |
| F6 | AAAA/NS/PTR RDATA shape checks | only A and CNAME validated | typed validation | hardening; reference-mode validator omits them |

The reference source is preserved untouched on `python-reference-v1`; the Swift
reference-mode validator reproduces the reference's accept/reject surface
**including** F1–F4 acceptance where parity requires it, and the hardened
behavior is the native-mode default. Every F-case appears in the differential
corpus both ways. Reviewer reproduction note (2026-10-22): F1-F4 and F5 were
independently reproduced in scratch against `python-reference-v1` — recorded as
reproduced evidence; P0.4 re-verifies in-repo.

Structural parse performs: header; counts walked exactly (RFC 9267 §6); names
with backward-only pointers, one hop at a time, a hop limit of 127, labels ≤63
and names ≤255 (§2–4); RR fixed parts and RDLENGTH within bounds (§5); typed
RDATA checks for A (4), AAAA (16), CNAME/NS/PTR (exactly one name); OPT/EDNS
parsed but not required.
* `ReplyValidator.validate(reply:for:) -> ReplyVerdict` implements the probe
  contract:
  * transaction ID match;
  * QR=1, opcode 0 (an **intentional addition** over the Python reference,
    which ignores opcode; see §8);
  * TC=0;
  * QDCOUNT=1;
  * question name, qtype and qclass match;
  * every advertised answer RR is present and well-formed.
  The verdict is an `outcome` (§3.3) plus `ancount` and `rcode`.
* `DNSQuery.build(name:type:id:flags:)`: deterministic bytes, so tests can
  compare them byte-for-byte with Python's `build_query`.

### 3.2 DNSProbe

* **`ProbeScheduler`** (a pure struct) holds the reference scheduling semantics:
  * at most one outstanding query per server;
  * rotating send passes over a per-window shuffled order;
  * per-server task list liveness → uncached ×N → prime ×D → cached ×M;
  * timeout per query;
  * cancellation stops new sends and abandons outstanding queries.

  Inputs: `.tick(now)`, `.datagram(server, bytes, rxKernel?, rxUser)`,
  `.sendFailed(server, errno)`, `.portUnreachable(server)`, `.cancel`. Outputs:
  `[.send(server, bytes, txid)]` and `[ProbeRecord]`. The scheduler is the unit
  under differential test. Everything around it is a driver.
* **`KqueueDriver`** owns:
  * nonblocking connected UDP sockets with `SO_TIMESTAMP_MONOTONIC`;
  * one kqueue;
  * `EVFILT_READ` per socket;
  * `EVFILT_USER` for cancellation;
  * the `kevent` timeout from the scheduler's next deadline (or `EVFILT_TIMER`
    with `NOTE_CRITICAL`, chosen by the R1 spike).

  It raises `RLIMIT_NOFILE` to `min(10240, hard)` and refuses configurations it
  cannot open sockets for, with a named error rather than silently dropping
  servers. The same class of defect occurred in the Python reference (Pitfall
  #2).
* **Timing boundaries (defined for parity, correction 3).**
  * Construction vs send: the reference (`dnslib2._send`) reads t0 **after
    `qid.next()` and immediately before `build_query`** — the parity window
    includes query construction. In reference mode the scheduler emits the
    query bytes first, then the driver reads t0 immediately before handing them
    to `send()`, mirroring the reference ordering exactly. (Native mode may time at the socket
    boundary; that is an E2-class change, not a parity behavior.)
  * GRC v2, by contrast, stamps t0 **after `sendto` returns** and truncates
    elapsed to integer ms, dropping zero-ms samples (report §3.4) — a
    documented difference from both our engines, never copied.
  * Receive timestamp: `rx_user` is taken immediately after `recvmsg` returns;
    `rx_kernel` from `SCM_TIMESTAMP_MONOTONIC` when present. **Missing
    ancillary timestamp** (kernel dropped it): `rx_kernel` is null for that
    sample, the sample stays valid, and `kd_ref_ms` (wall-clock, §3.2 note) is
    also omitted for it — matching the reference, which yields no `kd` when the
    cmsg is absent. Wall-clock data for `kd_ref_ms` is captured at the same
    point (`time.time_ns`-equivalent); monotonic timestamps cannot reconstruct
    it.
  * Actual-send feedback: the driver writes the **observed** pre-send clock
    value back into the `ProbeRecord` (`t0_observed_ns`); nonblocking sockets
    make this a single attempt, and a `send` error is fed back as an event so
    the scheduler records it — the scheduler never assumes its requested send
    happened.
  * Event-batch ordering: when one `kevent` batch contains both readable
    sockets and an expiry, **readable events are processed first** (read
    precedence), then expiries — the same order the reference implements
    (`dnslib2.py:207-213`: select events drained before the timeout sweep), so
    the parity fixtures pin the shared read-precedence order on scripted
    scenarios. Order differences surface as trace diffs, not as silent
    tolerance.
  * Clock conversion: the timebase is read once per process; ticks→ns is
    integer conversion rounded to nearest (ties up), and the 0.1 ms
    quantization difference vs the reference's float arithmetic is covered by
    the L4 rounding differential.
  * Supported-host assumption: kernel monotonic timestamps are relied on only
    where the SDK/platform provides them (macOS 27-era, V1/V2); on any host
    without them the engine runs user-latency-only with a logged startup note,
    and reference parity is restricted to user latency.
  * Kernel latency stays **secondary** (E2a decides promotion later).
  * `latency_user = rx_user − t0_observed` equals the Python reference metric
    (same clock domain, V2; same construction-inclusive boundary). It is the
    **primary metric during migration**.
  * `latency_kernel = rx_kernel − t0` removes event-loop read delay.
  * Promoting `latency_kernel` to primary is a methodology change, escalated
    after parity (§8, E2a).
* **Reference-compatible randomness.** In reference mode, all per-window
  randomness comes from one `CPythonRandom` stream (MT19937, `init_by_array`
  seeding, `getrandbits(k) = genrand_uint32() >> (32−k)`, `_randbelow` rejection
  loop, `choice`, and `shuffle` as CPython's Fisher-Yates). It is consumed in
  the reference's exact order:
  1. `shuffle(ips)` (helper.py:238);
  2. `build_tasks` per ip in shuffled order, with 50 × `rand_label(14)` drawing
     `choice` over `a–z0–9` (dnslib.py:188);
  3. `shuffle(order)` inside the engine (dnslib2.py:187).

  The reference itself is unseeded, so differential harnesses call
  `random.seed(N)` before each window. Test vectors cover `choice`, `shuffle`
  and a 2,000-draw bootstrap stream against CPython. TXIDs come from
  `QidGen(100)` (sequential, deterministic). Native mode may use a different
  RNG, but reference mode is the default until E2 decisions.
* **Golden engine:** the reference's **kqueue** engine (`deep_all_kqueue`). The
  thread engine (`deep()`) is not a parity target. Its unreach and kd behaviour
  differ.
* **Exact fixture parity vs live tolerance.** Fixture (L2/L3-simulated) runs
  require bit-exact record equality including timing values derived from the
  simulated clock; live comparison (L7) is tolerance-based and never treats
  timing equality as required. The two are never mixed.
* **Reference `kd`** (kept separately from the new monotonic metric):
  * wall-clock `SO_TIMESTAMP` delivered as `SCM_TIMESTAMP`;
  * `kd = (time.time_ns() − kts·1e9)/1e6`;
  * appended for uncached replies with rcode ∈ {0,3} and for **all** cached
    replies regardless of rcode;
  * `kd_med` feeds `wire_med`.

  Swift reproduces this exactly as `kd_ref_ms` and stores the monotonic
  `rx_kernel_ns` as a separate non-reference field. Note that the reference
  `ms` includes kqueue batch co-delivery delay (`nb`), so `latency_user` is not
  a pure wire interval. That is precisely what `latency_kernel` (E2a) would fix.
* **`ScriptedTransport` + `SimulatedClock`.** Used for fully deterministic
  fixture runs: a scenario file defines, per server, the reply behaviour per
  query index (delay, rcode, answers, drop, duplicate, late, malformed bytes,
  ICMP unreachable).

### 3.3 Outcome classification (one enum, persisted)

`answer` (NOERROR, ancount>0) · `nodata` (NOERROR, 0 answers) · `nxdomain` ·
`servfail` · `refused` · `rcode_other(n)` · `timeout` · `unreachable` (ICMP) ·
`send_error(errno)` · and `stray`/`malformed` for non-accepted datagrams, which
are counted per server and never treated as replies.

Reference-mode merges and rules:
* any `send()` OSError is recorded as `unreachable` (`rcode −3`, counts in
  `unreach` and `to_u`). In native mode `send_error(errno)` is kept distinct;
  this is listed as an expected difference.
* Liveness: a server is `dead` (and excluded) unless its liveness query returns
  rcode 0 with ancount > 0.

Sample eligibility matches the reference exactly:
* the term for the rank metric is the **random-subdomain workload**: a resolver
  answering random labels from cache or authority (RFC 8198 synthesis, wildcard
  domains) cannot be distinguished from an authoritative resolution by timing
  alone, and priming does not guarantee a subsequent cache hit. Historical
  per-domain latency patterns can *flag possible* cache synthesis; they cannot
  prove authoritative lookup behavior;
* uncached latency samples: `answer|nodata|nxdomain` (rcode 0 or 3);
* `servfail|refused|rcode_other` → `fail_u`;
* `timeout|unreachable|send_error` → `to_u`;
* cached samples: `answer` only;
* other replies → `bad_c`; timeouts → `to_c`.

### 3.4 DNSAnalysis

Reference-exact re-implementation of `measure.rank` and `stability.aggregate`:

* `u_med`/`c_med` = `round(median(samples), 1)` where samples are already
  `round(ms, 1)`. Even-count medians land on half-steps, so the **second**
  rounding decides the value. Rounding uses correctly rounded decimal
  conversion, the same as Python `round(x, 1)` on a binary double; this is
  verified by a 10⁶-value differential including half-step medians. Ties in the
  sort key keep input order (Python's sort is stable), which depends on the
  shuffled window order. That is why the RNG stream above matters for parity.
* `u_p90 = sorted(u)[int(0.9·n)]`, `u_max`, `kd_med`.
* Eligibility and ordering:
  * a server is ranked only if it has at least one uncached and one cached
    sample;
  * sort key is `(u_med, c_med)`;
  * `incomplete_cached` = servers with `to_c + bad_c > 0`.
* Aggregate:
  * median of window medians;
  * bootstrap over windows: 2000 resamples, seed 12345, **MT19937 plus CPython's
    `init_by_array` seeding and `_randbelow`/`choice`**, so CIs are bit-identical
    to the reference;
  * noise floor = median worst-minus-best window-median delta of
    `stability.REFS` (the four forced references **plus `100.100.100.100`**; note
    this differs from `measure.FORCED_REFS`, which has four), default 2.0 ms when
    none qualify; regression threshold `max(2, 2×floor)`;
  * bands, reproduced exactly. Servers are sorted by `u_med`; a new band starts
    when `ci95.lo > prev_hi`, and then `prev_hi = max(prev_hi or ci95.hi,
    ci95.hi)`. The `or` treats a 0.0 upper bound as missing. That is a quirk that
    cannot trigger for positive latencies; it is reproduced and documented, not
    changed silently.
  * Bootstrap quirk, documented: `bootstrap_ci` re-creates `Random(12345)` for
    every server, so all servers with the same window count draw the same index
    stream. CIs are therefore correlated across servers, and band overlaps are
    **not** independent pairwise comparisons. The UI and findings never present
    them as such.
  * provider medians;
  * `success_u` including `fail_u`;
  * `notes.exploratory` for fewer than 4 windows.
* Wording rule (enforced by a lint test over UI strings and CLI output): bands
  are "not statistically resolved". The words "tied", "equivalent" and "same
  speed" are banned for overlapping CIs.
* `Findings`: typed statements with an evidence level (`resolved`,
  `unresolved`, `exploratory`, `insufficient_data`) that the UI and the CLI
  render. Example: system resolver vs fastest band, failure outliers, material
  regressions.

### 3.5 Test interfaces

* `swift test` covers the pure modules (wire corpus, scheduler traces, analysis
  goldens).
* `dnsbench selftest` runs the wire corpus in the shipped binary.
* `dnsbench run --fixture <scenario> --clock simulated --seed N` gives
  bit-reproducible end-to-end runs.
* `Tests/Differential/` holds Python↔Swift harnesses (§8).

---

## 4. CLI specification (`dnsbench`)

```
dnsbench run        [--resolvers FILE | --pool FILE --top N] [--include-system]
                    [--windows N=4] [--uncached N=50] [--cached N=50] [--timeout S=2.0]
                    [--seed N] [--fixture SCENARIO [--clock simulated|real]]
                    [--library DIR | --out FILE.dnsbench] [--format human|json|ndjson] [--quiet]
dnsbench discover   [--master URL ...] [--resume | --fresh] [--out FILE.dnsbench-pool] [--format …]
dnsbench aggregate  FILE.dnsbench [--format human|json]      # recompute; never mutates raw data
dnsbench show       FILE.dnsbench [--resolver ADDR] [--findings] [--format …]
dnsbench export     FILE.dnsbench --format json|csv|text [--legacy-layout DIR] [-o PATH]
dnsbench import     LEGACY_RUNS_DIR --library DIR           # non-destructive, copies data
dnsbench validate   FILE.dnsbench                           # schema, invariants, recompute & compare
dnsbench recover    FILE.dnsbench                           # finalize running-without-writer → interrupted
dnsbench resolvers  system | builtin                        # SystemConfiguration DNS state
dnsbench selftest                                           # wire corpus + clock + socket option checks
dnsbench fixture serve SCENARIO [--port P]                  # scripted loopback responder for tests
dnsbench worker     --gen N --proto P                       # hidden; app IPC mode
```

**Defaults:** windows=4 (fewer gives `exploratory`); per-window task counts match
the reference (1 liveness, 50 uncached over 15 popular domains, 15 primes, 50
cached).

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | completed, results persisted and valid |
| 1 | internal error |
| 2 | usage / invalid configuration |
| 3 | cancelled (SIGINT/SIGTERM); partial data persisted, status `cancelled` |
| 4 | precondition: no eligible resolvers / pool missing |
| 5 | validation failed (`validate`, `selftest`) |
| 6 | network unavailable / path changed mid-run (window marked invalid) |
| 7 | storage error (unwritable, corrupt, locked by another writer) |
| 64 | IPC protocol incompatibility (worker mode) |

**SIGINT behaviour:** first ^C cancels cooperatively and finalizes. A second ^C
within 2 s aborts immediately; the file is then recovered on next open.

**JSON output** (`--format json`, final document; `ndjson` streams events using
the same `type`/`body` objects as IPC):

```json
{ "schema": "dnsbench.run-summary/1", "benchmark_id": "…uuid…", "file": "…/x.dnsbench",
  "status": "completed", "engine": {"version": "2.0.0", "build": "c136a63+dirty"},
  "windows": {"complete": 4, "partial": 0},
  "resolvers": {"configured": 152, "ranked": 149, "dead": 3},
  "fastest_band": {"members": ["1.1.1.1", "…"], "evidence": "unresolved"},
  "findings": [{"evidence": "resolved", "text": "System resolver 192.168.1.1 is 9.4 ms slower than the fastest band"}] }
```

Human output is a fixed-width table sorted by rank, with band separators and an
evidence line. It never prints a ranking without the evidence label.

**Fixtures.** Scenario files (`Tests/Fixtures/scenarios/*.json`) are shared by
Swift tests, CLI fixture runs and the Python harness. A full fixture benchmark
with `--clock simulated --seed 1` is byte-identical across runs, and it is CI's
primary integration test.

---

## 5. Storage design

### 5.1 Decision: one SQLite file per benchmark (`.dnsbench`), no global database

| Need | Structured files (today) | SQLite per benchmark (chosen) |
|---|---|---|
| Crash during sampling | torn JSONL tails; manifest/samples consistency is by convention | WAL: committed batches survive; file always consistent |
| Atomic status + data | multiple files, rename tricks | one transaction |
| Inspector reads one resolver out of ~70k rows (reference: 116 queries × 150 resolvers × 4 windows; ~310k with 250 uncached + 250 cached per window) | full scan + decode | indexed query |
| Identity | timestamp folder names (collision bugs happened) | UUID in the file + filename |
| Inspectability | `cat`/`jq` | `sqlite3`, `dnsbench export/show`; schema documented |
| Dependencies | none | libsqlite3 in the SDK, none added |
| Portability | folder | single file: double-click, AirDrop, attach to a bug |

The discovery pool file (`.dnsbench-pool`) uses the same conventions: `meta`,
`candidate(address, source_list, first_seen)`, `probe(...)` with the screening
outcome classes, and `pool(rank, address, score, u_med, c_med, servfail, refused,
timeouts, hijack)` plus the master-list URLs and fetch timestamps. Screening
checkpoints are rows, so resume is a query rather than torn-JSONL parsing.

A global database was rejected. It is a single point of corruption, and listing
is cheap: open each file read-only and read `benchmark` + `meta`. The listing is
cached in memory and rebuilt on scan. The discovery pool uses the same mechanism
(`.dnsbench-pool`).

### 5.2 Schema v1 (key columns)

```sql
meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)
  -- format='dnsbench.benchmark', schema_version=1, engine_version, engine_build,
  -- app_version, created_by ('cli'|'worker'|'import')
benchmark(id TEXT PRIMARY KEY,                -- UUIDv4
  name TEXT, created_at TEXT, started_at TEXT, ended_at TEXT,
  status TEXT NOT NULL CHECK(status IN ('created','running','completed','cancelled','interrupted','failed')),
  status_reason TEXT, config_json TEXT NOT NULL, host_json TEXT NOT NULL,
  method TEXT NOT NULL)                          -- 'reference-v1' etc.
resolver(id INTEGER PRIMARY KEY, address TEXT, port INTEGER, transport TEXT DEFAULT 'udp',
  family INTEGER, label TEXT, source TEXT CHECK(source IN ('system','builtin','pool','user','forced_ref','imported')),
  ptr TEXT, provider TEXT, provider_basis TEXT, UNIQUE(address, port, transport))
window(idx INTEGER PRIMARY KEY, status TEXT CHECK(status IN ('running','complete','cancelled','interrupted','invalid')),
  started_at TEXT, ended_at TEXT, order_seed INTEGER, label_seed INTEGER, invalid_reason TEXT,
  backpressure_paused_ms INTEGER DEFAULT 0)
  -- meta additionally carries last_committed_seq, backlog_records,
  -- backlog_bytes (updated per commit; surfaced for stalled/interrupted files)
  -- and anomaly_overflow_count when the anomaly cap is hit
query(window_idx INTEGER, resolver_id INTEGER, seq INTEGER, kind TEXT, qname TEXT, qtype INTEGER,
  txid INTEGER, sent_ns INTEGER, t0_observed_ns INTEGER, rx_kernel_ns INTEGER, rx_user_ns INTEGER,
  outcome TEXT NOT NULL, rcode INTEGER, ancount INTEGER, batch INTEGER, kd_ref_ms REAL,
  PRIMARY KEY(window_idx, resolver_id, seq)) WITHOUT ROWID
  -- sent_ns = scheduler-requested send; t0_observed_ns = driver's actual
  -- pre-construction clock read (parity boundary); kd_ref_ms = wall-clock
  -- kernel delta, null when the ancillary timestamp was missing (matching the
  -- reference kd behavior)
anomaly(window_idx INTEGER, resolver_id INTEGER, rx_user_ns INTEGER, kind TEXT, bytes BLOB)  -- strays, malformed
window_result(window_idx, resolver_id, …)      -- derived per-window stats, tagged with method + engine_version
aggregate(method TEXT, engine_version TEXT, computed_at TEXT, result_json TEXT)   -- derived, recomputable
event(ts TEXT, kind TEXT, detail_json TEXT)    -- audit: start, cancel_requested, cancel_ack, errors, worker gen
import_source(path TEXT, sha256 TEXT, kind TEXT) -- provenance for imported legacy data
```

Raw facts (`query`, `anomaly`) are immutable. Derived tables carry `method` and
`engine_version`, so recomputation is explicit and auditable. `dnsbench
aggregate` writes a new row and never overwrites one.

### 5.3 Durability and recovery

* Settings: `journal_mode=WAL`, `synchronous=FULL`, `foreign_keys=ON`. Sample
  batches commit every 200 ms or 2,000 rows. Status transitions commit
  individually.
* **Guarantee as stated:** committed transactions survive a **process crash**
  (worker or app). They do not necessarily survive **power loss**: on macOS,
  `fsync` does not flush the drive cache. `PRAGMA fullfsync=ON` (F_FULLFSYNC)
  closes that gap, and its cost is measured in the R5 spike. Phase 0 decides
  between fullfsync for status transitions only and for every batch. The spec
  never claims power-loss durability without it.
* **Loss claim, corrected:** "≤200 ms of samples" holds only for healthy
  operation. A storage stall (slow disk, checkpoint) can leave much older
  batches queued. The store therefore tracks and exposes the **last committed
  sequence number** and the **actual unpersisted backlog** (records and bytes);
  the worker reports both in `progress`, the UI shows them when a stall is
  detected, and `dnsbench show` prints them for interrupted files. 200 ms is a
  **healthy-operation target**, never a durability promise.
* **Single-file export of a live/WAL database:** sharing a running or crashed
  WAL database by copying the file is unsafe. Export uses either (a) a
  coordinated **checkpoint-and-close** on the writer (quiesce →
  `wal_checkpoint(TRUNCATE)` → close → copy → reopen), or (b) the SQLite
  **backup API** to a fresh file for a consistent snapshot while a reader is
  open. Both are verified in the Phase 0 crash/open-reader tests: export while
  the GUI reader holds the file, and export after `kill -9` recovery, must both
  produce a file that passes `validate`.
* **Immutability vs recomputation:** raw tables (`query`, `anomaly`) and a
  terminal file's status are immutable. Derived recomputation (`aggregate`,
  `window_result`) appends new method-tagged rows. Recomputation and recovery
  run only under the **exclusive writer lock** (`LOCK_EX`, after the status
  recheck below); a reader that observes `status='running'` with a free writer
  lock must **re-check status after acquiring `LOCK_EX`** before finalizing —
  the "no writer" inference can go stale between check and lock.
* **WAL retention:** long-running readers can pin a WAL; the writer runs
  `wal_checkpoint(PASSIVE)` at window boundaries and `TRUNCATE` at terminal
  states so committed files shrink to a minimal WAL.
* Writer lock: the worker takes `flock(LOCK_EX|LOCK_NB)` on `<file>.lock` (fd
  opened `O_CLOEXEC`, so it is never inherited by children) and holds it for the
  whole run. If the lock is busy, the start fails fast with `store_unwritable`.
  Readers only probe with `LOCK_SH|LOCK_NB` and release immediately, so they
  never block a writer. A reader that sees `status='running'` and can take `LOCK_SH|LOCK_NB` knows
  there is no live writer. It shows "Interrupted (not finalized)", and the next
  worker or `dnsbench recover` finalizes it as `interrupted`. The data up to the
  last committed batch is retained, and the loss window (≤200 ms of samples) is
  stated in the UI.
* Creation without WAL sidecar hazards:
  1. create `Library/.staging/<uuid>.dnsbench` in rollback-journal mode
     (`journal_mode=DELETE`);
  2. write schema + `benchmark` row (`status='created'`);
  3. close, so no `-wal`/`-shm` exists;
  4. `rename()` into `Library/Benchmarks/` (same volume, atomic);
  5. reopen in WAL.

  The WAL is only ever created under the final name. A crash at any step leaves
  either a closed staging file (swept to `Recovered/` on launch, never deleted)
  or a valid final file. A crash-injection case covers "between rename and first
  WAL commit". The app never deletes benchmark files.
* Crash-injection tests (§8, L6): `kill -9` at random points during a fixture
  run, 200 iterations. Every file must open, pass `validate`, and show
  `interrupted` with a monotone record prefix.

### 5.4 Legacy import (non-destructive)

`dnsbench import` reads the legacy run layouts:
* `runs/<ts>[-k][_wN]/` with `ranked.json`, `deepbench-results.jsonl`,
  `deepbench-perquery.jsonl`, `cancelled.json`, `checkpoint-pool.json`,
  `DNSBench-custom-list.txt`;
* `windows-<id>.json` manifests;
* per-benchmark and legacy shared `aggregate.json`;
* old runs without `category`/`kd`.

It groups windows exactly as the current app does and writes one `.dnsbench`
per legacy benchmark with `method='reference-v1'`, `created_by='import'` and a
sha256 for each source file. Originals are only read, so the import is
reversible by deleting the output.

Per-query timestamps don't exist in legacy data (only rounded ms). They are
imported as `latency_ms_legacy`, with `sent_ns`/`rx_*` null. Analysis uses the
legacy values, so imported aggregates must reproduce the legacy `ranked.json`
and aggregate exactly (gate).

---

## 6. Native UI design

### 6.1 What the user needs to decide

1. Which resolver should I use from where I am, and is the difference real?
2. Is my current (system) resolver healthy, slow, or misbehaving?
3. Which resolvers are unreliable, and why?
4. Has anything changed since last time or at another location?

Every screen element serves one of these, or it is cut.

### 6.2 Information architecture

The app is one main window: a library, not a document-based app. Benchmarks
accumulate and are compared, and `.dnsbench` files opened from Finder are
imported into the library or shown read-only.

```
┌ Sidebar ─────────────┬ Content ───────────────────────────────────────────┬ Inspector ─────────┐
│ ▶ New Benchmark  ⌘N  │  Finding (one sentence + evidence label)           │ 1.1.1.1            │
│ BENCHMARKS           │  ─────────────────────────────────────────────     │ one.one.one.one    │
│   Today              │  [Usable ▾] [Find…]          Windows 4 · 152 · 3✕  │ Cloudflare (PTR)   │
│   • Home – 21:40  ✓  │  BAND 1 · order not statistically resolved (6)     │ ──────────────     │
│   • Home – 18:02  ◐  │   1  ● 1.1.1.1     ├─●─┤  ·────●──·    11.8   0.0% │ Uncached  11.8 ms  │
│   Earlier            │   2  ● 9.9.9.9     ├─●──┤ ·───●───·    12.1   0.2% │   95% CI 11.2–12.5 │
│ RESOLVERS            │  BAND 2 (14)                                       │ Cached     1.9 ms  │
│   System (2)         │   7  ◉ 192.168.1.1   ├──●──┤ …         21.2   0.0% │ Loss 0.0%  Fail 0  │
│   Built-in · Pool    │  … ladder rows: uncached track (primary), cached   │ per-window ●●●●    │
│   My List            │     track (secondary), numbers right-aligned        │ histogram          │
│ DISCOVERY            │                                                    │ Show 312 queries…  │
└──────────────────────┴────────────────────────────────────────────────────┴────────────────────┘
```

Core view: **the latency ladder.** It is a native `Table` with one custom
`Canvas` column on a shared axis.
* The left columns hold the rank, a band marker, a status glyph and the
  address/name.
* The track shows a CI bar and median dot per channel, with the uncached lane
  first, the cached lane below it, and a dotcom lane once supported. A faint
  p10–p90 whisker shows within-window spread.
* The right columns show medians and loss.
* Rows are grouped under band headers that say "order not statistically
  resolved". Bands are **descriptive interval groups**: members of one band can
  have disjoint intervals (the reference merges overlapping chains), so band
  membership never implies pairwise unresolved order, and `resolved` findings or
  "95% significance" support are never derived from interval overlap alone.
  Four consecutive windows make a band **confirmatory-eligible**, not
  automatically confirmatory (the `exploratory` note still applies to method
  strength). The bootstrap's per-server seed reuse (§3.4) is surfaced in the
  methodology note, without implying that independent seeds would remove shared
  network dependence.
* Two vertical reference rules mark the fastest band's median and the system
  resolver's median. That comparison is the one most users came for.
* Loss columns show **accepted/attempted counts** (not only percentages), and
  "incomplete coverage" is prominent: a resolver ranked from the minimum
  surviving sample count never yields an unqualified "fastest" recommendation —
  the findings engine withholds or qualifies it (§3.4 evidence rules).
* The axis is linear in ms, auto-scaled to the 95th percentile of medians, with
  an explicit "›" clip marker for outliers.

**Why not GRC's stacked bars:** bars show only a mean-like length. The ladder
shows the estimate, its uncertainty and the band structure in the same row, so
the question "is the difference real?" is answered visually without a separate
statistics tab.

**Live run** uses the same view in a measuring state.
* A progress line sits under the toolbar: "Window 2 of 4 · 34,112 / 75,000
  queries · 2:14 · about 3 min left", with a Stop button.
* Rows fill in as data arrives. Rows re-order **only at window boundaries**, so
  continuous reshuffling never invites false conclusions.
* Stopping shows the three cancellation states from §2.4.

**Findings** (the Conclusions equivalent) occupy the top band.
* One headline sentence, plus "All findings (5)" expanding inline.
* Each finding carries an evidence tag and selects the relevant rows when
  clicked.
* Findings come from `DNSAnalysis.Findings`, not view code.

**Inspector** sections, top to bottom:
1. Identity: address, PTR, provider and its basis ("from PTR, provisional").
2. Status checks (when characterization lands).
3. Per-channel stats (median, p90, CI, n, loss).
4. Per-window medians as a dot strip (stability).
5. Latency histogram.
6. Failure breakdown: timeouts, SERVFAIL, REFUSED, malformed, strays.
7. "Show queries…", opening a sheet with the per-query table.
8. Actions: Copy Address, Add to My List, Pin, Exclude.

**Resolvers** (inventory). SystemConfiguration exposes a default resolver plus
domain-scoped resolvers (this host: `resolver #1` default on en0, then
`local`, `254.169.in-addr.arpa`, and several `ip6.arpa` scoped entries, per
`scutil --dns`). Rules:
* a table with source, last status and pin;
* system resolvers come from SystemConfiguration (`State:/Network/Global/DNS`),
  marked with a filled dot as in GRC, and are always included unless excluded;
* **default resolvers** (no `domain` key) are benchmarked against public names;
* **scoped/supplemental resolvers** (domain-specific, e.g. mDNS, VPN split-DNS,
  Tailscale MagicDNS) are listed and labelled, and benchmarked **only on explicit
  opt-in**, because they legitimately refuse or answer differently for public
  names; their opt-in runs use their own match domains, not the public workload;
* multiple system resolvers are shown and ranked individually (never averaged
  into one "system" row);
* Add (validated IPv4 now, IPv6 later) and Remove (⌫);
* import/export of plain lists in "IP comment" format, which also reads
  GRC-style `.ini` lists.

**New Benchmark** sheet:
* the resolver set as source checkboxes with counts;
* a preset:
  * Quick: 1 window, marked exploratory;
  * Standard: 4 windows, the default;
  * Thorough: 8 windows;
* each preset states its expected duration and its evidence strength;
* "Advanced" reveals timeout and query counts.
Defaults are chosen so the honest outcome ("resolved"/"unresolved") is possible.
Quick is labelled as unable to resolve rank.

### 6.3 Commands and keyboard

| Menu | Items |
|---|---|
| File | New Benchmark ⌘N · Open… ⌘O (import `.dnsbench`) · Import Legacy Runs… · Export ▸ CSV / JSON / Text ⌘E · Install Command Line Tool… |
| Benchmark | Run ⌘R · Stop ⌘. · Run Again with Same Settings ⇧⌘R |
| View | Show Inspector ⌃⌘I (standard) · Show Cached Lane · Filter ▸ All/Usable/Problems · Find ⌘F |
| Resolvers | Add ⌘+ · Remove ⌫ · Pin ⌘P |

Full keyboard navigation: ↑/↓ through rows, ⌘↑/⌘↓ between bands, Return opens
the inspector, and Space opens a Quick Look-style query preview.

### 6.4 Visual system

* **Tone:** a precise instrument; calm, plain and evidence-qualified copy.
* **Type:**
  * SF Pro throughout, with monospaced digits for all numbers;
  * SF Mono only for addresses and query names;
  * Finding headline in `.title3` semibold; everything else at native table
    density.
* **Color:**
  * system semantic backgrounds;
  * one brand accent, **Signal Amber**, derived from the icon's gold and tuned
    per appearance for ≥4.5:1 contrast on text. It is used only for live
    measurement, the Run control and chart selection.
  * Channels: uncached = indigo, cached = teal, dotcom (later) = gray-violet.
    They are also told apart by lane position and labels, never by color alone.
  * Status: green `checkmark.circle`, orange `exclamationmark.triangle`, red
    `xmark.octagon`; shape and color always together.
* **Charts:** hairline axis, no gridlines, two reference rules, no 3D, no
  gradients, no gauges.
* **Motion:** none except progress and state transitions; honours Reduce Motion.
* **Layout:** an 8-pt grid; the inspector at 320 pt ideal; light and dark
  appearance via semantic colors only; Increase Contrast supported.
* **Accessibility:**
  * every ladder cell has an `accessibilityLabel` ("Uncached median 11.8
    milliseconds, 95 percent interval 11.2 to 12.5");
  * the ladder provides an `AXChartDescriptor` (audio graphs);
  * the band headers are headings;
  * VoiceOver rotor jumps between bands;
  * Dynamic Type applies where macOS supports it (text styles, no fixed sizes).
* **Identity:** "DNSBench" with the Timer-and-Resolver icon. No GRC name, logo or
  red "G" affordance.

---

## 7. GRC feature-parity matrix

Legend:
* **S** supported in the reference
* **P** partial
* **M** missing
* **≠** intentionally different
* **D** deferred
* **?** GRC detail unknown

| GRC feature | Today (ref) | v2 | Phase | Notes |
|---|---|---|---|---|
| Cached lookups | S (15 domains, primed, 50/window) | S | 2 | GRC's list is 50 domains per grc.com/dns/resources.htm as reported by the research subagent (not re-fetched); domain-set size change is methodology (E2d). |
| Uncached lookups | S (random subdomain of popular domain) | S | 2 | GRC v2 (verified): 26-char lowercase label from a UuidCreate GUID [V] under a top-50 list whose contents are 2009-era [I, from list contents]; SERVFAIL latency recorded as a sample [V]; cached step requires AA=0 on a second consecutive query [V] (report §3.1–3.2). R13 risk stands. |
| Dotcom lookups | M | S | 7 | GRC v2 (verified): random 26-char label + `.com`, any rcode timed — resolver→.com-zone path (report §3.2). Independently implementable; methodology addition (E2b). |
| 250 query sets / multipliers | ≠ windows × 50 | ≠ presets 1/4/8 windows | 5 | Repetition across windows supports window-level uncertainty. |
| 20 ms query spacing | ≠ ≤1 outstanding/server, interleaved | ≠ (+ optional pacing knob) | 7 | GRC paces **chain starts** 20 ms [V] with a 97-outstanding cap [V constant; event-gating semantics [I]] (report §3.4). Reference for our knob design. |
| Slow-resolver sidelining | M | S | 7 | 50 ms default **verified** (µs-minimum > threshold at 0x424c8a); attempt count "10 opportunities" still [?] (report §3.5). |
| Reliability (lost queries) | S (to_u/to_c, unreach, fail_u, bad_c) | S | 2/5 | Loss % column + breakdown. |
| Status: not answered / rejected / no recursion / invalid | P (dead, refused counted) | S | 7 | GRC probes verified: bad-WEB `www.<rand>.com`, bad-COM `<rand>.com`, DNSSEC `isc.org`, rebinding `net*.rebindtest.com` (GRC-controlled zones), connectivity `<rand>.isc.org` vs hardcoded roots (report §3.8). RA-bit check stays ours. |
| Redirect (bad domain / bad COM / bad WEB) | P (discovery hijack exclusion) | S | 7 | Per-benchmark characterization. |
| Never replies to bad domains | M | S | 7 | |
| Rebinding protection ring | M | D | — | GRC uses its own `rebindtest.com` zones, one per private range, IPv4+IPv6 [V names; per-range mapping [I]] (report §3.8). DNSBench equivalent needs a controlled zone; deferred. |
| DNSSEC test | M | D | 8 | DO-bit queries; AD-flag status. |
| System resolver detection, pin to top | M | S | 5 | SCDynamicStore, incl. scoped/VPN resolvers labelled and opt-in (§6.2); not a measurement change. |
| Built-in resolver list | P (4 forced refs + pool) | S curated | 5 | Sourced from provider documentation, versioned. |
| Custom list builder | ≠ (public-dns.info ~62k → pool) | S | 3 | Kept; larger master list. |
| Add/remove, list import/export (.ini) | M | S | 5 | |
| Live response-time chart | M | ≠ latency ladder | 5 | §6.2 rationale. |
| Tabular data | S (rankings table) | S | 5 | |
| Conclusions | M | ≠ evidence-tagged findings | 5 | GRC logic **?**; own design. |
| CSV / text export | M | S (+JSON) | 3 CLI / 5 GUI | |
| 95% significance | ≠ bootstrap over windows, CI bands | S | 1 | GRC: two-sided ~95% z-test on integer-ms means via 16.16 fixed-point sqrt (report §3.7) — shape [V], unit bookkeeping [I]. Ours differs by design. |
| Ranking order | ≠ uncached median, then cached | S (reference) | 1 | GRC v2 (verified): equally-weighted **mean** of all integer-ms truncated samples, zeros dropped, 95% z-test for "faster" claims (report §3.6–3.7). Deliberately different; E2c. |
| IPv6 | M | D | 8 | Wire layer AAAA-ready from Phase 1. |
| DoH / DoT | M | D | 9 | Separate transport; not timed with the UDP engine's precision claims. |
| Up to 500 resolvers | untested | S | 2 | GRC allocation guard 501 verified (report §3.4); our scale gate unchanged. |
| Progress monitor (counts/%/time) | P | S | 5 | |
| Page cloning | M | ≠ export image / multiple benchmarks | — | |
| Command line | S (Python scripts) | S first-class | 3 | |
| Disconnect detection | M | S | 4 | `NWPathMonitor`; window marked `invalid`. GRC: 10 s last-reply watchdog + suspend dialog [V]; its changelog claims 20 s [?] — unresolved mismatch (report §3.4); ours differs deliberately. |

Verified GRC behaviors that DNSBench deliberately does **not** copy: TXID-only
reply matching on unconnected sockets (spoofable; we validate source + question +
answers), integer-ms truncation with zero-sample skip, t0 after `sendto`, and
third-party DNS TXT ownership lookups that leak queried IPs (report §3.3, §3.4,
§3.8, §5). Nothing in this document claims GRC parity where GRC's implementation
is unknown.

---

## 8. Differential-testing strategy

Python reference: tag `python-reference-v1`, run from a pinned worktree. A
failed comparison is adjudicated by: (1) RFC 1035/9267 for wire behaviour,
(2) this spec's stated methodology, (3) a third oracle (dnspython, needs install
approval, E3). The two implementations never adjudicate each other.

| Layer | Input | Python side | Swift side | Agreement required |
|---|---|---|---|---|
| L1 wire | Corpus: RFC-derived cases (each RFC 9267 anti-pattern), the 10 selftest cases, the 18 review tests, captured real replies, 10⁵ mutations of valid replies (bit flips, truncation, count inflation, pointer rewrites) | `parse_reply` | `ReplyValidator` (reference mode) | Exact accept/reject + (rcode, ancount). Expected-difference register (named fixtures F1–F6, §3.1; each tested both ways): F1 missing authority RR, F2 missing additional RR, F3 zero-answer with missing authority RR, F4 256-octet expanded owner name — accepted by the reference, rejected by the hardened parser, reproduced by reference mode for parity; F5 opcode ≠ 0; F6 AAAA/NS/PTR typed RDATA. Native-mode `send_error` additionally. dnspython 2.7.0 (isolated oracle, E3) compares equivalent parser operations with its configuration recorded; it is an independent implementation, **not** the final authority — RFC 1035 (normative) and RFC 9267 (informational) adjudicate. Everything else must agree exactly. |
| L2 scheduler | Harness calls `random.seed(N)` per window so the reference's shuffles and labels are reproducible (§3.2). Scenario traces with simulated clock (delay, reorder, drop, duplicate, late-after-timeout, wrong txid, ICMP unreachable, SERVFAIL/REFUSED/NXDOMAIN, TC, malformed) at concurrency 1…64 | `_Engine` driven by a fake selector/socket/clock harness (test-only) | `ProbeScheduler` | Exact per-query records (kind, qname, outcome, rcode, ancount, ms) and per-server results. |
| L3 local integration | Real sockets, loopback scripted responder, ladder 1…64, 500 servers | `run_interleaved` | `KqueueDriver` | Outcome counts exact. Latency: per-scenario median difference ≤0.25 ms and p95 difference ≤1.0 ms (loopback). Pre-registered. |
| L4 analysis | Starlink dataset (3 windows), `test_aggregate.py` synthetic cases, generated edge cases (empty, single window, missing kd/category, ties in medians) | `measure.rank`, `stability.aggregate` | `DNSAnalysis` | Exact JSON equality after canonical key order, including bootstrap CIs. PTR lookups are injected from a fixture map (no live `gethostbyaddr`), and `generated`/timestamp fields are excluded by name. `stdev`: relative error ≤1e-12. Rounding differential over 10⁶ values: exact. |
| L5 persistence | L2 fixture runs | legacy layout | `dnsbench export --legacy-layout` | Exact files (`ranked.json`, results JSONL) after canonicalization. |
| L6 robustness | `kill -9` injection (200×), corrupted WAL, torn staging file, kill between rename and first WAL commit, stale IPC frames, rapid start/cancel/start (100×), dir change mid-run, colliding IDs, empty pool, 500 resolvers, all-nonresponding pool | n/a | CLI, worker, coordinator | Invariants: no GUI crash; exactly one terminal state; file validates; partial data truthfully labelled. |
| L7 live parity | 44 resolvers (40 + 4 forced refs); 6 run pairs, order counterbalanced **PS SP PS SP PS SP** (P=Python, S=Swift), one session, 4 windows per run | helper | CLI | Pre-registered before any run (see L7 protocol below). |

**L7 protocol v2 (pre-registered; corrects the v1 design per review).**
The v1 protocol collapsed six pair-level differences into 44 resolver means
(df=43) while resolvers share network conditions; it is replaced.

* **Primary estimand:** for run pair j (j=1…6), D_j = the mean over the 44
  resolvers of (Swift run median − Python run median), a run median being the
  median of its 4 window medians. Each D_j is one aggregate difference from one
  independent run pair; the pair is the replication unit. Resolvers within a
  pair are treated as one weighted sample set (equal weights); dependence
  across resolvers (shared anycast PoPs, shared last mile, shared operator) is
  acknowledged: it inflates no test's df because the test is across pairs, but
  it means D_j inherits whatever resolver-level bias that pair's shared
  conditions introduced — stated as an assumption, and diagnosed per resolver
  below.
* **Primary test:** paired t-based TOST on D_1…D_6 (df = 5), margin ±δ with
  δ = max(0.5 ms, 2 × Phase-0 noise floor). The noise floor is the median
  absolute per-resolver difference between two same-engine Python runs on the
  same resolvers — a **single-pair, per-resolver scale**. δ is applied at the
  D_j scale (44-resolver means) **without** √44 shrinkage: shared network
  conditions make that averaging optimism unsafe, so the margin is
  deliberately conservative; the scale relationship is stated here so the two
  numbers are never confused. **Maximum acceptable margin: 2.0 ms** — if the noise floor
  exceeds 1.5 ms (δ > 2.0), the experiment is declared **inconclusive** before
  running, and the extension rule below applies once. Equivalence at the pair
  level must not hide per-resolver regressions:
* **Per-resolver diagnostics (prespecified, required for pass):**
  * per-resolver mean difference d_r (across the 6 pairs) with its spread;
  * **discrepancy limits:** pass requires ≥ 90% of resolvers with |d_r| ≤ δ and
    no resolver with |d_r| > 2δ; every violation is listed with its per-window
    detail;
  * **cancellation guard:** the sign pattern of d_r is reported; a pass with
    roughly cancelling positive/negative biases (mean |d_r| > δ while mean d̄ ≈ 0)
    is reported as **mixed**, not passed.
* **Secondary:**
  * (b) loss rates: pooled absolute difference |Δloss| ≤ 0.5 pp and per-resolver
    |Δloss| ≤ 2 pp. A nonsignificant Fisher test is **not** an equivalence
    result; it is reported only as "no detectable difference" and never cited
    as passing;
  * (c) dead/eligible agreement: exact binomial lower 95% bound ≥ 0.90, with
    every disagreement itemized;
  * (d) ordering contradictions: counted only among pairs whose CIs do not
    overlap in **both** engines. If **no comparison qualifies**, (d) is reported
    "not evaluable — no qualifying comparisons" and does **not** count as a
    pass. Otherwise zero contradictions are required and the qualifying count
    is reported.
* **Extension rule (capped, prespecified, separately approved):** an
  inconclusive primary result triggers exactly one extension: +2 pairs (8
  pairs, 16 runs total) under the same margin — **+81,664 additional queries
  beyond the E4 budget, requiring its own explicit approval**. Still
  inconclusive → **inconclusive**, reported as such. No margin relaxation,
  ever.
* **Pilot first:** the full protocol runs only after a small rate-limited pilot
  (plan P0.10) validates targeting, rate limits, and duration; pilot results may
  adjust n and duration but never the margins or acceptance rules.

**Red→green discipline.** Every Swift defect found by a differential layer gets
a regression case in the corpus or trace set before its fix. The report
separates defect-reproducing tests from coverage tests.

**Acceptance criterion (from the brief).** Met when the Swift CLI completes a
full 4-window benchmark, persists it, passes `validate`, matches L4/L5 exactly
under fixture input, and **L7 has been executed and reported with its outcome
stated** — `inconclusive` is an honest outcome, not a failure; it escalates to
the capped extension approval (E4), never to a relaxed margin.

---

## 9. Phased implementation plan

Branching:
* work happens on `v2/swift-engine` in a separate worktree (`~/Code/dnsbench-v2`)
  branched from `c136a63`, so the current app keeps working from `main`;
* each phase ends with a commit series plus an annotated tag `v2-phase-N`, which
  is its rollback point;
* `main` is untouched until Phase 6;
* the spec and per-phase plans are committed on the branch.

| Phase | Deliverable | Depends | Completion criteria (all must hold) |
|---|---|---|---|
| 0 Baseline | Reference worktree at `python-reference-v1`; `reference/` harness (seeded RNG, injected PTR) that emits golden corpora (wire, traces, analysis outputs) from Python; loopback timing baseline of the Python engine; parser-difference reproduction (F1–F6, plus the reviewer's four cases); MT19937/`shuffle` vector verification; QoS comparison (gated); spikes R1 (timer precision), R2 (monotonic rx timestamps on en0; clock-domain proof vs `CLOCK_UPTIME_RAW`), R5 (fsync and fullfsync jitter), L7 noise floor (2 Python runs); rate-limited pilot design | — | Goldens reproducible by one command; spike reports with numbers and scripts committed in `docs/spikes/`; parser register independently reproduced (or corrected); δ for L7 fixed in writing before Phase 3. Detailed tasks: `docs/superpowers/plans/2026-10-22-dnsbench-phase0.md` |
| 1 Pure core | SwiftPM package with `DNSWire` + `DNSAnalysis`; MT19937/CPython seeding; rounding | 0 | L1 and L4 exact; RFC 9267 cases all rejected; 10⁶ rounding differential exact; `swift test` green, 0 warnings |
| 2 Probe engine | `ProbeScheduler`, `KqueueDriver`, timestamps, cancellation, scripted transport | 1 | L2 exact; L3 within tolerances; 500-resolver run OK; cancel→stop p99 ≤20 ms; fd-limit error named, not silent |
| 3 Store + CLI | `DNSStore`, legacy import, `dnsbench` commands, fixtures, export | 2 | L5 exact; Starlink import reproduces legacy aggregate exactly; L6 crash-injection 200/200; **CLI acceptance criterion met; L7 executed and reported with its stated outcome** (inconclusive allowed; triggers the capped E4 extension decision) |
| 4 Worker + coordinator | `dnsbench worker`, IPC, supervisor, state machines; the app gets an engine setting (Python reference / Swift) | 3 | Protocol tests (handshake, versioning, stale gen/seq/job rejection, backpressure, terminal-exactly-once); L6 GUI cases; app usable with either engine |
| 5 UI redesign | IA, ladder, inspector, inventory + system resolvers, New Benchmark, findings, export, menus, accessibility | 4 | Hermetic snapshot tests on fixture stores (closes the skipped-snapshot gap); XCUITest flows for inspector close/no-samples, run/stop, dir change (closes #11/#12); VoiceOver and keyboard audit checklist |
| 6 Python retirement | Remove `Resources/python` + `engine/` from the bundle; keep `reference/` for differential CI; merge to `main`; tag `v2.0.0` | 1–5 + your approval | All gates green on a clean checkout; notarization (if R7 resolved) |
| 7+ | Escalated methodology features: characterization statuses, dotcom, sidelining, pacing, ranking metric, kernel-rx primary metric | 6 + per-item approval | Each with its own spec delta and before/after evidence |

The app stays operational throughout. Until Phase 6 the Python helper remains
the default engine, and the Swift engine is opt-in from Phase 4.

---

## 10. Risk register

| ID | Risk | Likelihood / impact | Confidence in current understanding | Mitigation |
|---|---|---|---|---|
| R1 | `kevent` timeout or timer coalescing distorts timeouts/pacing | M / M | Low | Phase-0 spike: measured wake error for kevent timeout vs `EVFILT_TIMER`+`NOTE_CRITICAL`; pick by data |
| R2 | `SCM_TIMESTAMP_MONOTONIC` differs on real interfaces | L / M | Medium (loopback only, V2) | Phase-0 spike on en0; user-latency stays primary regardless |
| R3 | CPython MT19937/`choice` replication error | L / M | High | CPython test vectors; L4 exact |
| R4 | Float rounding mismatch vs `round(x,1)` | L / M | Medium | 10⁶-value differential in Phase 1 |
| R5 | SQLite fsync perturbs timing | M / M | Low | Phase-0 jitter spike; degraded-mode fallback (only if the spike forces it): buffer the window in memory, commit at window end + every 2 s — a **labeled degraded mode** with larger, honestly-reported loss windows, never presented as equivalent durability |
| R6 | Raw UDP under App Sandbox | M / L now | Low | Ship Developer ID (non-sandboxed) first; spike before any App Store plan |
| R7 | No Developer ID cert → notarization unverified | ? / M | Unknown | Ask (E1); ad-hoc signing until then; never claim notarization untested |
| R8 | Live parity inconclusive on Starlink | M / M | Medium | More pairs; report inconclusive honestly; optionally a wired vantage |
| R9 | Python trace harness diverges from real Python behaviour | M / H | Medium | Harness validated by replaying `test_engine.py` scenarios and reproducing the historical phantom-timeout defect (detector self-check) |
| R10 | Scope creep from parity wishlist | M / M | High | Phases 7+ require per-item approval |
| R11 | public-dns.info availability for discovery | M / L | High | Cache master list with date; resume |
| R12 | Window-level bootstrap with 4 windows remains weak | H / M | High | Labelled exploratory <4; Thorough preset; method change only by escalation |
| R13 | **Aggressive NSEC/wildcard synthesis (RFC 8198) makes "uncached" probes under DNSSEC-signed or wildcard domains cache-fast**, biasing resolvers that implement it | M / H | Medium | Phase-0 analysis of the Starlink data per domain (bimodality by domain), the same per-domain split in the ladder's inspector; propose domain-set revision (E2d) with evidence |
| R14 | Dotcom design may not match GRC | H / L | High (that it is unknown) | Document as our method; never claim parity |
| R15 | 500 resolvers × sockets: fd limits, ICMP rate limiting | M / M | Medium | Scale gate; unreachable classification separate from timeout |
| R16 | Shared CLI/worker binary blocks a future App Store build (inherit-signed tool cannot run standalone, V6) | L / L | High | Accept; App Store would ship a separately signed CLI |

### Decisions requiring your approval (escalations)

* **E1 Distribution and signing:** Developer ID + notarization, non-sandboxed,
  hardened runtime (recommended), vs Mac App Store (sandbox). Do you have a
  Developer ID certificate? Without one, notarization stays unverified.
* **E2 Methodology changes, after parity only, each with evidence:**
  * (a) kernel-rx latency as the primary metric;
  * (b) dotcom, characterization statuses and sidelining;
  * (c) ranking metric: uncached-only (reference) vs combined (GRC v2);
  * (d) domain set 15 → ~50, informed by R13.
* **E3 Independent oracle:** approve installing dnspython into an isolated uv
  environment under `~/.hermes/artifacts/dnsbench-v2/oracle/` (no system
  changes).
* **E4 Live traffic for L7:** 12 runs × 4 windows × 44 resolvers × 116 queries
  = 244,992 queries to public resolvers from your network. The Phase-0 noise
  floor runs add 2 × 4 × 44 × 116 = 40,832. Total 285,824. The capped L7
  extension (16 runs total) would add 81,664 more and is **a separate approval
  decision, only if L7 lands inconclusive**.
* **E5 Branch/worktree plan** in §9, including committing this spec on
  `v2/swift-engine`.

---

## 11. Unresolved verification gaps carried forward

* UI behaviours #11 (inspector close) and #12 (no-samples state): build-only,
  closed by Phase 5 XCUITests.
* Three snapshot tests skip without real data: closed by Phase 5 fixture stores.
* Notarization of bundled Python: never tested, and irrelevant after Phase 6.
  Not claimed either way.

---

## 12. Review log

| Date | Reviewer | Result | Disposition |
|---|---|---|---|
| 2026-10-22 | Independent review of Rev 2 (read-only) + user-directed correction round (9 areas) + GRC v2 static research (E6) | 8 P1 + P2 findings, all applied (t0 wording; L7 extension split out of E4; cancel-ack bound; interrupted-writer rule; Phase 3 gate wording; reference QoS pin; plan tasks added; 36-char alphabet; read-before-timeout parity; noise-floor scale; schema fields; §7 label honesty; stale §0; §3.1 structure; §12 wording; REA label/privacy fixes). Spec revised: L7 redesign (df=5 pair-level estimand, diagnostics, capped extension), parser register F1–F6 with structural/policy split, timing boundaries (construction-inclusive t0, feedback, batch ordering, missing-ancillary rules), storage corrections (backlog/last-committed seq, export via checkpoint/backup, lock recheck), QoS measured not assumed, descriptive-band wording, heartbeat/progress/stall separation + P-1…P-5, resource bounds by records+bytes, XPC rationale fix, deployment target 27.0, scoped-resolver rules. GRC v2 rows updated from the verified static report (`docs/research/2026-10-22-grc-v2-rea-report.md`). |: unseeded global RNG and its consumption order (helper.py:238, dnslib.py:188, dnslib2.py:187); wall-clock `kd` (dnslib.py:143); send-error → unreach (dnslib2.py:96–107); band `prev_hi or` (stability.py:195–200); per-server bootstrap reseed (stability.py:88). Incorporated in §0, §1.4, §2.1, §2.3, §2.4, §3.1-3.4, §5.1-5.3, §8 (L1, L2, L4, L7), §9 Phase 0, E4. The reviewer's CPython MT19937 `choice` check (14,950 draws) remains **reported evidence, not yet reproduced in-repo** (P0.5 does so). |
