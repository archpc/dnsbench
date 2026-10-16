#!/usr/bin/env python3
"""Offline regression tests for stability.py --aggregate (no network, no server).

Constructs synthetic multi-window run dirs and asserts the statistical
invariants: median-of-window-medians as primary estimate, bootstrap CI
determinism (fixed seed), overlapping-CI rank bands, provider aggregation
without member-count advantage, explicit unknown-provider handling,
material-regression flagging against the reference noise floor, and
historical-format compatibility (windows without kd_med/engine fields).
Edge cases: single window, partial server coverage, empty aggregate output.
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server-lib"))
sys.path.insert(0, os.path.expanduser(
    "~/.hermes/skills/macos-diagnostics/dns-benchmark/scripts"))
import stability  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def make_window(base, name, ranked, results_rows):
    d = os.path.join(base, name)
    os.makedirs(d, exist_ok=True)
    json.dump({"n_ranked": len(ranked), "incomplete_cached": [],
               "generated": name, "ranked": ranked}, open(f"{d}/ranked.json", "w"))
    with open(f"{d}/deepbench-results.jsonl", "w") as f:
        for r in results_rows:
            f.write(json.dumps(r) + "\n")
    return d


def entry(ip, u_med, c_med=40.0, p90=None, ptr="pool-1.example.com",
          category="Akamai", kd=None, to_u=0, to_c=0):
    return {"ip": ip, "u_med": u_med, "c_med": c_med,
            "u_p90": p90 if p90 is not None else u_med + 4.0, "u_max": u_med + 9,
            "ptr": ptr, "category": category,
            "kd_med": kd, "to_u": to_u, "to_c": to_c, "bad_c": 0, "strays": 0}


def results_row(ip, unc=50, to_u=0, cached=50, to_c=0, bad_c=0, dead=False):
    return {"ip": ip, "uncached": [30.0] * unc, "cached": [30.0] * cached,
            "to_u": to_u, "to_c": to_c, "bad_c": bad_c, "strays": 0,
            "kdeltas": [0.1] * unc, "dead": dead}


def main():
    tmp = tempfile.mkdtemp(prefix="stability-test-")
    try:
        w1 = make_window(tmp, "w1", [
            entry("10.0.0.1", 30.0, category="Akamai", kd=0.1),
            entry("10.0.0.2", 31.0, category="Akamai", kd=0.1),
            entry("10.0.0.3", 40.0, category="Google", kd=0.2, ptr="dns.google"),
            entry("10.0.0.4", 50.0, category="other-noPTR", kd=0.2),
            entry("10.0.0.5", 55.0, category="NTT", kd=0.2),  # partial: missing in w3
            entry("1.0.0.1", 38.0, category="Cloudflare", kd=0.2),
            entry("8.8.8.8", 35.0, category="Google", kd=0.2),
            entry("9.9.9.9", 36.0, category="Quad9", kd=0.2),
        ], [results_row("10.0.0.1"), results_row("10.0.0.2"),
            results_row("10.0.0.3"), results_row("10.0.0.4"),
            results_row("10.0.0.5"), results_row("1.0.0.1"),
            results_row("8.8.8.8"), results_row("9.9.9.9")])
        w2 = make_window(tmp, "w2", [
            entry("10.0.0.1", 32.0, category="Akamai", kd=0.1),
            entry("10.0.0.2", 30.0, category="Akamai", kd=0.1),
            entry("10.0.0.3", 41.0, category="Google", kd=0.2),
            entry("10.0.0.4", 51.0, category="other-noPTR", kd=0.2),
            entry("10.0.0.5", 55.5, category="NTT", kd=0.2),
            entry("1.0.0.1", 44.0, category="Cloudflare", kd=0.2),  # drift up
            entry("8.8.8.8", 35.2, category="Google", kd=0.2),
            entry("9.9.9.9", 36.1, category="Quad9", kd=0.2),
        ], [results_row("10.0.0.1"), results_row("10.0.0.2"),
            results_row("10.0.0.3"), results_row("10.0.0.4"),
            results_row("10.0.0.5"), results_row("1.0.0.1", to_c=10),
            results_row("8.8.8.8"), results_row("9.9.9.9")])
        w3 = make_window(tmp, "w3", [
            entry("10.0.0.1", 31.0, category="Akamai", kd=0.1),
            entry("10.0.0.2", 30.5, category="Akamai", kd=0.1),
            entry("10.0.0.3", 40.5, category="Google", kd=0.2),
            entry("10.0.0.4", 50.5, category="other-noPTR", kd=0.2),
            entry("1.0.0.1", 46.0, category="Cloudflare", kd=0.2),
            entry("8.8.8.8", 35.1, category="Google", kd=0.2),
            entry("9.9.9.9", 36.2, category="Quad9", kd=0.2),
        ], [results_row("10.0.0.1"), results_row("10.0.0.2"),
            results_row("10.0.0.3"), results_row("10.0.0.4"),
            results_row("1.0.0.1", to_u=25),
            results_row("8.8.8.8"), results_row("9.9.9.9")])
        man = os.path.join(tmp, "windows.json")
        json.dump({"windows": [w1, w2, w3], "engine": "kqueue", "netsvc": "off"},
                  open(man, "w"))
        out = os.path.join(tmp, "aggregate.json")
        rec = stability.aggregate(man, out)
        s = {x["ip"]: x for x in rec["servers"]}

        # primary estimate = median of window medians
        check("median-of-medians primary",
              abs(s["10.0.0.1"]["u_med"] - 31.0) < 1e-9,
              f"got {s['10.0.0.1']['u_med']}")
        # bootstrap CI deterministic (fixed seed)
        lo, hi = stability.bootstrap_ci([30.0, 32.0, 31.0])
        lo2, hi2 = stability.bootstrap_ci([30.0, 32.0, 31.0])
        check("bootstrap CI deterministic", (lo, hi) == (lo2, hi2), f"{lo}..{hi}")
        check("ci95 stored", s["10.0.0.1"]["ci95"] == [round(lo, 2), round(hi, 2)],
              str(s["10.0.0.1"]["ci95"]))
        # rank bands: unresolved comparisons tie (band of Akamai pair)
        check("rank band recorded", all("rank_band" in x for x in rec["servers"]))
        # provider aggregation: Akamai estimate = median of member med-of-meds
        ak = [p for p in rec["providers"] if p["provider"] == "Akamai"][0]
        expect = (31.0 + 30.5) / 2
        check("provider median center", abs(ak["u_med"] - expect) < 0.01,
              f"est={ak['u_med']} expect={expect}")
        check("provider member count stored", ak["members"] == 2)
        # unknown provider explicit
        unknowns = [p["provider"] for p in rec["providers"]
                    if p["provider"].startswith("Unknown")]
        check("unknown provider explicit", len(unknowns) == 1, str(unknowns))
        # success rates reflect injected failures: 1.0.0.1 w3 has 50 ok + 25 to
        check("success_u computed", abs(s["1.0.0.1"]["success_u"] - (1 + 1 + 50/75) / 3) < 0.01,
              str(s["1.0.0.1"]["success_u"]))
        # wire_med = u_med - kd (kd present in all windows)
        check("wire_med derived",
              abs(s["10.0.0.1"]["wire_med"] - (31.0 - 0.1)) < 0.02,
              str(s["10.0.0.1"]["wire_med"]))
        # regression flag: 1.0.0.1 drifts 38->46 (+8 > threshold from noise floor)
        check("material regression flagged", "1.0.0.1" in rec["material_regressions"],
              str(rec["material_regressions"]))
        # noise floor measured from refs (only 1.0.0.1 is a REF here and it IS
        # the drifting one... it contributes its own delta to the floor; verify
        # threshold is finite and applied)
        # noise floor now from STABLE refs 8.8.8.8/9.9.9.9 (tiny deltas);
        # 1.0.0.1 is a ref too but its drift is the flagged regression
        check("regression threshold ~2ms", rec["regression_threshold_ms"] <= 3.0,
              str(rec["regression_threshold_ms"]))
        # partial coverage: 10.0.0.5 missing from w3
        check("partial coverage excluded", "10.0.0.5" not in s, f"servers={sorted(s)}")

        # single-window manifest: degenerate but must not crash
        man1 = os.path.join(tmp, "windows1.json")
        json.dump({"windows": [w1], "engine": "thread", "netsvc": "off"}, open(man1, "w"))
        rec1 = stability.aggregate(man1, os.path.join(tmp, "aggregate1.json"))
        check("single window works", len(rec1["servers"]) == 8,
              str(len(rec1["servers"])))

        # historical format compatibility: no kd_med/engine keys at all
        d_old = make_window(tmp, "old", [
            entry("10.1.0.1", 33.0), entry("10.1.0.2", 34.0)],
            [results_row("10.1.0.1"), results_row("10.1.0.2")])
        ranked_old = json.load(open(f"{d_old}/ranked.json"))
        for r in ranked_old["ranked"]:
            r.pop("kd_med", None)
            r.pop("category", None)
            r["ptr"] = "resolver.example.net"
        json.dump(ranked_old, open(f"{d_old}/ranked.json", "w"))
        man_old = os.path.join(tmp, "old.json")
        json.dump({"windows": [d_old]}, open(man_old, "w"))
        rec_old = stability.aggregate(man_old, os.path.join(tmp, "aggregate-old.json"))
        check("historical format (no kd/category)",
              rec_old["servers"][0]["wire_med"] is None
              and rec_old["servers"][0]["provider"] == "Unverified PTR: resolver.example.net",
              str(rec_old["servers"][0]["provider"]))

        # empty result set: no servers -> aggregates empty, no crash
        d_empty = make_window(tmp, "empty", [], [])
        man_e = os.path.join(tmp, "empty.json")
        json.dump({"windows": [d_empty]}, open(man_e, "w"))
        rec_e = stability.aggregate(man_e, os.path.join(tmp, "aggregate-empty.json"))
        check("empty windows handled", rec_e["n_servers"] == 0
              and rec_e["providers"] == [], str(rec_e["n_servers"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    fails = [n for n, ok in results if not ok]
    print(f"\naggregate regression: {len(results) - len(fails)}/{len(results)} passed")
    if fails:
        print("FAILED:", fails)
        return 1
    print("AGGREGATE REGRESSION PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
