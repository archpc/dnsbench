# DNSBench

Local DNS resolver benchmark with a stdlib Python web interface and a native
macOS app. The checked-in `engine/` is authoritative. No Hermes installation,
pip packages, personal datasets, or Homebrew-specific Python path is needed
for the web interface and offline smoke test.

## Fresh-clone offline test

From the repository root, with Python 3.9 or newer:

    python3 tests/test_offline.py

This starts a child interpreter with an isolated HOME and no user site packages
or PYTHONPATH. It tests the vendored DNS wire parser, rejects personal scratch
imports, loads the synthetic fixture, and starts a real HTTP server on an
ephemeral loopback port. It checks static assets, dataset coverage, rankings,
comparison, resolver history, and raw samples. A Python audit hook rejects
non-loopback socket connections, sends, binds, and DNS lookups. A negative test
proves the guard blocks both TCP and UDP traffic. No live benchmark runs.

This is a smoke test, not the live measurement or native-app suite. The fixture
contains invented values, not evidence about resolver performance.

## Browse the synthetic demo

POSIX shell commands, from the repository root:

    DEMO_DIR="$(mktemp -d)"
    cp -R tests/fixtures/synthetic/. "$DEMO_DIR/"
    DNSBENCH_DATA_DIR="$DEMO_DIR" python3 server.py

Open http://127.0.0.1:8461/ and browse the two historical runs. Stop with Ctrl-C.
Set `DNSBENCH_PORT` to use another port. The source fixture stays unchanged.

Important: this is a browsing example, NOT a network-disabled server mode.
Do not press Run or request discovery/measurement against the synthetic pool.
Its RFC 5737 documentation addresses are not resolvers, and measurement also
adds real public reference resolvers. Only the offline test command enforces
network isolation. Synthetic metadata and `synthetic-*.example` labels identify
invented data; the UI is otherwise the normal interface.

## Generate real data (network access, explicitly opt in)

Real resolver datasets are intentionally not committed. Startup creates output
directories only; it never imports files from a personal Hermes installation.
You can explicitly copy your own compatible datasets into `datasets/`, or run
the included discovery pipeline. Full discovery can probe tens of thousands of
addresses and take many minutes. Check your network policy before starting.

From the repository root, in a POSIX shell:

    mkdir -p datasets
    (cd datasets && ulimit -n 10240 && python3 ../engine/discover.py)

Discovery fetches public candidate lists when `master-ipv4.txt` is missing,
then writes liveness and screening checkpoints plus `pool.json`. Existing
checkpoints resume old work; a rerun is not necessarily a fresh measurement.
Do not mix synthetic and real data. Preserve old checkpoints separately if
starting a new discovery experiment.

After discovery, launch the web interface:

    python3 server.py

Or measure directly (this sends real UDP/53 traffic and performs PTR lookups):

    (cd datasets && ulimit -n 10240 && python3 ../engine/measure.py --engine thread --n 25 --pool pool.json)

The thread engine avoids the macOS-only kqueue requirement. On macOS,
`--engine kqueue` selects the interleaved engine. Run history is written under
`datasets/runs/`. `DNSBENCH_DATA_DIR` configures the web server/native helper;
the standalone engine scripts use their working directory and CLI arguments.

Offline comparison of existing real run directories:

    python3 engine/stability.py datasets/runs/<first-run> datasets/runs/<second-run>

## Components

    server.py                       loopback HTTP + SSE server
    engine/                         vendored wire, discovery, measurement, analysis
    static/                         framework-free browser interface
    app/                            native macOS app, separate Xcode build
    skills/dns-benchmark/SKILL.md    portable Hermes workflow using this checkout
    tests/fixtures/synthetic/       small hand-authored offline demonstration
    datasets/                       local real datasets and history, gitignored

The skill is optional operator guidance, not a runtime dependency. When using
it in Hermes, retain a checkout and run commands from its root; no scripts are
loaded from the installed skill directory. Native app builds require macOS and
Xcode; the offline command above neither builds nor validates the native app.

## Other verification

    python3 tests/test_aggregate.py      # offline statistical regression tests
    python3 tests/test_review_fixes.py   # macOS: local UDP + stubbed engine
    python3 engine/test_engine.py       # macOS: kqueue/local UDP regression gate

The following suites are NOT offline smoke tests:

    python3 tests/verify_dnsbench.py
    python3 tests/test_helper.py --data-dir <real-data-directory>

They exercise real benchmark jobs and require real datasets. The native suite
is `cd app && xcodebuild -scheme DNSBench test`; snapshots require real runs.

## Measurement boundaries

NXDOMAIN is a valid uncached response; SERVFAIL/REFUSED are failures, not fast
latency samples. Cached queries are primed first. Rank and reliability claims
must be supported by raw per-query records. Random-subdomain timings measure
negative/wildcard paths, not every kind of positive recursion. Small rank gaps
and overlapping confidence intervals do not establish equivalence or a winner.

Each measurement writes an isolated timestamped run directory. Checkpoints are
copied aside, not deleted. Cancellation targets only the active job. Stop only
processes you launched and track; never terminate processes by port enumeration.

The original web release is tagged `v0.1.0`; its removed Rust WASM renderer can
be recovered from that tag. Current charts do not need Rust or WASM.
