# DNSBench

DNSBench is a DNS resolver benchmarking project with a working v1 Python
measurement engine, a local web interface, and a native SwiftUI/AppKit macOS
app. The [v2 design](https://github.com/archpc/dnsbench/tree/v2/docs) proposes a
native Swift engine in a supervised worker and a standalone CLI sharing the
same core; it is architecture and planning work, not an implemented replacement.
The checked-in `engine/` remains authoritative for the current implementation.
No Hermes installation, pip packages, personal datasets, or Homebrew-specific
Python path is needed for the web interface and offline smoke test.

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

## Build the native macOS app

The current app targets macOS 27.0+ and uses Xcode 27 (verified with Xcode
27.1). The runtime bootstrap currently downloads an Apple Silicon (arm64)
Python build; Intel Mac distribution is not provided by this script.

From the repository root:

    bash app/scripts/fetch-python.sh
    xcodebuild -project app/DNSBench.xcodeproj -scheme DNSBench \
      -configuration Debug -destination 'platform=macOS' \
      -derivedDataPath app/build CODE_SIGNING_ALLOWED=NO test
    open app/build/Build/Products/Debug/DNSBench.app

If command-line tools are selected instead of Xcode, prefix xcodebuild with
`DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer` (adjust to your
installed Xcode). Use a normal build environment, not an OS sandbox around
Xcode's build service. The commands build locally without distribution signing
or notarization; they do not install the app into /Applications.

The bootstrap downloads the pinned python-build-standalone runtime into
`app/Resources/python/bin/python3.13`, verifies it can execute and import its
standard library, and reuses a valid install on subsequent runs. Runtime files
are gitignored. An incomplete existing runtime is preserved and rejected:
move that directory aside before retrying. Older bootstrap versions could
produce an extra `python/python/` directory level.

The built app uses only its bundled Python and helper, never a hardcoded
Homebrew interpreter or original source checkout. Missing resources produce an
explicit diagnostic. Native tests include a real bundled-helper ping and
synthetic run-store tests; legacy snapshots skip without real run history.
The bootstrap's download needs Internet access; the native tests do not run
public DNS measurements. The offline bootstrap regression uses a synthetic
archive and a fake downloader:

    python3 tests/test_native_bootstrap.py

## Other verification

    python3 tests/test_aggregate.py      # offline statistical regression tests
    python3 tests/test_review_fixes.py   # macOS: local UDP + stubbed engine
    python3 engine/test_engine.py       # macOS: kqueue/local UDP regression gate

The following suites are NOT offline smoke tests:

    python3 tests/verify_dnsbench.py
    python3 tests/test_helper.py --data-dir <real-data-directory>

They exercise real benchmark jobs and require real datasets. The native suite
is `cd app && xcodebuild -scheme DNSBench test`; snapshots require real runs.

## Performance

The web API originally rebuilt the visualization payload on every request:
`points_payload()` reread the candidate list and JSONL checkpoints, then
recomputed status, category, and latency for roughly 62,000 resolver candidates.
Both `/api/points` and `/api/coverage` paid that cost even when the data had not
changed. Caching the derived payload by each input file's identity (name,
modification time, and size) removed repeated parsing and computation while
invalidating the cache when a dataset changed. The recorded local measurements
in [the fix commit](https://github.com/archpc/dnsbench/commit/92aae59e063d7b808ca0f3067757269b0a2af9e8)
show `/api/points` falling from 376 ms to 10 ms and `/api/coverage` from 416 ms
to 8 ms on warm requests. These are historical web API response times, not DNS
query latencies or a guarantee for other machines; cold requests still build
the payload. The cache now lives in `engine/adapter.py`.

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
