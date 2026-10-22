# DNSBench web app (v0.1.0, frozen)

Local web interface for the GRC-style DNS benchmark experiment: 62,608 public
resolver candidates, server-rendered SVG charts, live SSE streaming during
runs, historical run comparison. Plain stdlib Python server + hand-rolled JS.
No frameworks, no dependencies, no wasm.

## Components

    server.py               stdlib HTTP + SSE (Server-Sent Events), 127.0.0.1 only
    engine/adapter.py       adapter — the ONLY module that touches the benchmark
                            engine; imports the validated dns-benchmark skill
                            scripts (dnslib/discover/measure/stability) as-is,
                            never mutates them
    static/                 index.html / app.js / styles.css — no frameworks;
                            rankings bar chart, resolver detail, explore
                            scatter, across-runs history, coverage summary
    tests/verify_dnsbench.py  automated end-to-end verification suite;
                            launches real jobs — run only when a benchmark is wanted
    datasets/               benchmark datasets + run history — NOT in this repo
                            (kept separate from application source; .gitignored)

## Install & run

Requirements:

- Python 3.x (tested with Homebrew python3.15; stdlib only, no pip installs)
- The benchmark engine: this app imports the validated skill scripts from
  `~/.hermes/skills/macos-diagnostics/dns-benchmark/scripts/` (dnslib.py,
  discover.py, measure.py, stability.py). If installed elsewhere, adjust
  `SKILL_SCRIPTS` at the top of `engine/adapter.py`.
- Bench datasets under `datasets/` (master-ipv4.txt, phase2b-results.jsonl,
  pool.json, ...). They are intentionally not committed — generate them with
  the discovery pipeline of the dns-benchmark skill, or copy from an existing
  scratch dir. The server refuses to run a job without `datasets/pool.json`.

Run:

    /opt/homebrew/bin/python3.15 server.py
    # binds 127.0.0.1:8461 only; open http://127.0.0.1:8461/

To stop a server you started yourself, terminate the exact process you launched
(e.g. Ctrl-C in its foreground shell, or kill the recorded PID). Never kill by
port enumeration: `lsof -ti -iTCP:<port>` on this host drops the port filter
and returns unrelated PIDs (see tests/verify_dnsbench.py header comment and the
dns-benchmark skill, Pitfall #15).

## Verification

    /opt/homebrew/bin/python3.15 tests/verify_dnsbench.py
    # checks: dnslib 10-case parser selftest, engine adapter, dataset invariants,
    # live server boot, every HTTP endpoint, static assets, SSE stream, run
    # lifecycle (accept / confirm-gate / decline / cancel / run_complete),
    # run-dir isolation, checkpoint preservation, compare endpoint.
    # Exits non-zero on any failure. Spawns and tracks its own server on an
    # ephemeral port; terminates only that tracked subprocess.

Engine and native-app helper suites:

    python3 tests/test_review_fixes.py   # offline: local UDP responder + stubbed engine
    python3 engine/test_engine.py        # offline: kqueue engine regression gate
    python3 tests/test_aggregate.py      # offline: multi-window aggregate math
    # Helper lifecycle suite: its cancel/crash cases run a real n=2 measure, so it
    # needs a data dir holding a pool.json (default: <repo>/datasets). Example:
    mkdir -p /tmp/dnsbench-suite && cp <a pool.json> /tmp/dnsbench-suite/
    python3 tests/test_helper.py --data-dir /tmp/dnsbench-suite
    # App: (cd app && xcodebuild -scheme DNSBench test); snapshot tests skip
    # unless <repo>/datasets holds real runs.

The Rust WASM renderer (viz/) was removed 2026-10-16 as unreachable after the
chart redesign; recover it from tag v0.1.0 if ever needed.

## Boundaries frozen at v0.1.0

- Engine scripts are imported as-is; correctness rules (validated-response
  checks, NXDOMAIN-valid scoring, prime logging, bad_c counting, reference
  interleaving, 10-case selftest before every job) belong to the skill.
- Every run writes an isolated `datasets/runs/<UTC-timestamp>/` directory;
  checkpoints are copied aside, never deleted.
- Cancel terminates only the running benchmark job (cooperative, via a
  threading.Event); nothing enumerates processes by port to kill them.
- `datasets/` is data, not source: never committed, never deleted by the app.
