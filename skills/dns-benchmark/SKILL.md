---
name: dns-benchmark
description: Benchmark DNS resolvers and compare recorded runs.
version: 0.1.0
author: Sein Bunnell (archpc), Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [network, dns, benchmark, udp]
    related_skills: []
---

# DNS Benchmark

Use the DNSBench checkout's stdlib Python engine for discovery, measurement,
and offline analysis. No personal Hermes installation or dataset is required.
This skill is guidance, not a second copy of the engine.

## When to Use

- Benchmark public UDP/53 DNS resolvers from a specified network vantage.
- Compare recorded runs, coverage, and resolver latency distributions.
- Demonstrate DNSBench without sending external traffic.
- Not for DoH/DoT tests or comprehensive recursive-resolver correctness testing.

## Prerequisites

Keep a checkout of https://github.com/archpc/dnsbench and use its repository root
as the `terminal` working directory. Require Python 3.9+; no pip dependencies.
The commands below assume a POSIX shell. The interleaved kqueue engine requires
macOS; use the thread engine elsewhere. Native app builds require Xcode.
Installing this SKILL.md alone does not install the checkout or datasets.

## How to Run

Use `terminal(command="python3 tests/test_offline.py", workdir=<checkout>)`
first. Require exit 0. This command uses synthetic fixtures and rejects external
network access. It does not validate real resolver performance.

## Procedure

1. Read README.md with `read_file`. Confirm the checkout and data directory.
   Never search a user's Hermes scratch space to import data automatically.
2. For offline browsing, follow README's synthetic-demo commands. Copy
   `tests/fixtures/synthetic/` to a disposable directory and select it with
   `DNSBENCH_DATA_DIR`. Browse existing runs only. Completion: coverage,
   rankings, comparison, history and raw samples load.
3. Before live work, get approval for network scope. Discovery can contact tens
   of thousands of addresses. Confirm the actual egress/vantage instead of
   assuming a requested geographic label. Keep distinct vantages separate.
4. For real discovery, use `terminal` from the checkout to run:

       mkdir -p datasets
       (cd datasets && ulimit -n 10240 && python3 ../engine/discover.py)

   Completion: master list, liveness and screening records, and pool.json exist.
   Inspect checkpoint reuse before claiming a fresh screen. Preserve prior data.
5. For live measurement, run via `terminal`:

       (cd datasets && ulimit -n 10240 && python3 ../engine/measure.py --engine thread --n 25 --pool pool.json)

   On macOS, `--engine kqueue` selects interleaved probes. Mandatory public
   reference resolvers are added to the selected pool; include them in scope.
   Completion: a new run directory contains ranked.json and per-query records.
6. For offline stability, run via `terminal`:

       python3 engine/stability.py datasets/runs/<a> datasets/runs/<b>

   Report actual overlap denominators, missing samples, timeouts and failures.
   Recount stored provider labels; do not infer provider identity from memory.

## Pitfalls

- The demo server is not a network-disabled mode. Do not start benchmarks from
  the synthetic demo. Its documentation IPs are not usable resolvers; forced
  references would still cause real traffic. Only test_offline.py enforces
  isolation. Synthetic latencies are invented, never benchmark evidence.
- NXDOMAIN is a valid uncached timing. SERVFAIL/REFUSED are failures, not fast
  responses. Cached probes require full priming. Preserve per-query evidence.
- Random subdomains test negative/wildcard paths, not all positive recursion.
  Anycast routing and the source network affect results. Rankings describe the
  experiment's path and window, not universal resolver quality.
- Overlapping confidence intervals mean unresolved order, not equivalence.
  Repeat windows before recommending a resolver based on small gaps.
- The standalone scripts use cwd-relative datasets; DNSBENCH_DATA_DIR applies
  to the web server/helper, not discovery or measurement CLI inputs.
- Stop only tracked processes created by the task. Never kill by enumerating
  ports. Do not delete checkpoints to force a refresh.

## Verification

- Offline smoke: `terminal(command="python3 tests/test_offline.py", workdir=<checkout>)`.
- Statistics: `terminal(command="python3 tests/test_aggregate.py", workdir=<checkout>)`.
- Before/after macOS engine changes, run `python3 engine/test_engine.py` through
  `terminal`; its local-responder detector must catch the historical defect.
- Live runs: inspect raw logs and sample completeness, not just ranked output.
- tests/verify_dnsbench.py and tests/test_helper.py run live jobs; never label
  them offline or launch them without network approval.
