#!/usr/bin/env python3
"""STABILITY operation: offline comparison of measurement runs. Zero new benchmark
traffic (PTR labels are stored in each run's ranked.json by measure.py).

Usage:
    python3 stability.py runs/<ts1> runs/<ts2> [runs/<ts3> ...]
    python3 stability.py --aggregate runs/windows-<ts>.json [--out path]

--aggregate: repeated-window analysis (measure.py --windows N). Median of
window medians is the primary latency estimate (window-level median, NOT a
pooled sample median: samples within a window are correlated). Between-window
dispersion, bootstrap CI over window medians (fixed seed, documented),
overlapping-CI rank bands (statistically unresolved comparisons are ties),
provider clustering from stored PTR-derived categories (robust median center;
member count never boosts a provider), reference-drift noise floor for
material-regression flags, and per-phase completion rates. Wire-latency
estimate u_med - kd_med (send -> kernel-rx) reported alongside u_med to
separate engine-induced userspace delay from network latency (kd only exists
for runs recorded with kernel timestamps).

Reports per run: composition recount, completeness flags, reference positions.
Reports pairwise: top-50 overlap; optional --pools for finalist-pool overlap
(compare pool.json files from different discovery runs).

Claims allowed (Pitfall #13): cluster dominance reproduction and overlap percentages.
Individual ranks are snapshots; say so.
"""
import json
import random
import statistics
import sys
from collections import Counter, defaultdict
from itertools import combinations

REFS = ["1.0.0.1", "8.8.8.8", "9.9.9.9", "208.67.222.222", "100.100.100.100"]
# NOTE: REFS is intentionally NOT measure.FORCED_REFS: it adds 100.100.100.100
# (Tailscale MagicDNS) which historical runs benched when Tailscale was up; it
# exists for cross-run drift tracking only, never as a bench target.
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 12345  # fixed: reproducible CIs, no hidden randomness
KNOWN_CATEGORIES = {"Akamai", "NTT", "Level3/Savvis", "Windstream-family",
                    "Verisign", "Quad9", "OpenDNS", "Google", "Cloudflare",
                    "ControlD"}


def provider_of(category):
    """Explicit provider identity from stored category. Unknowns are explicit,
    never inferred from IP adjacency."""
    if category in KNOWN_CATEGORIES:
        return category
    if category == "other-noPTR":
        return "Unknown (no PTR)"
    return "Unverified PTR: " + category[6:] if category.startswith("other:") else category


def load(rundir):
    d = json.load(open(f"{rundir}/ranked.json"))
    top50 = [r["ip"] for r in d["ranked"][:50]]
    try:
        cats = Counter(r["category"] for r in d["ranked"][:50])
    except KeyError:
        # pre-restructure runs lack category; derive from stored PTR
        sys.path.insert(0, __import__('os').path.dirname(__file__))
        from measure import categorize
        cats = Counter(categorize(r["ip"], r.get("ptr", "?")) for r in d["ranked"][:50])
    refs = {}
    for i, r in enumerate(d["ranked"], 1):
        if r["ip"] in REFS:
            refs[r["ip"]] = (i, r["u_med"], r["c_med"])
    return {"id": d.get("generated", rundir), "dir": rundir,
            "n_ranked": d["n_ranked"], "incomplete": d.get("incomplete_cached", []),
            "top50": top50, "cats": cats, "refs": refs}


def load_results(rundir):
    """ip -> results record from deepbench-results.jsonl (completion counts)."""
    out = {}
    with open(f"{rundir}/deepbench-results.jsonl") as f:
        for line in f:
            r = json.loads(line)
            out[r["ip"]] = r
    return out


def bootstrap_ci(meds, n=BOOTSTRAP_N, seed=BOOTSTRAP_SEED):
    """Percentile CI of the median-of-window-medians. Resamples windows
    (preserving window-level dependency), never individual probes."""
    rng = random.Random(seed)
    meds = list(meds)
    if len(meds) < 2:
        m = meds[0] if meds else 0.0
        return (m, m)
    stats = []
    for _ in range(n):
        stats.append(statistics.median(rng.choice(meds) for _ in meds))
    stats.sort()
    return (stats[int(0.025 * n)], stats[int(0.975 * n)])


MIN_CONFIRMATORY_WINDOWS = 4


def _notes(nw):
    """Plain-string caveats the UI must show next to rank bands."""
    notes = {}
    if nw < MIN_CONFIRMATORY_WINDOWS:
        notes["exploratory"] = (
            f"{nw} window(s): bootstrap CIs over <{MIN_CONFIRMATORY_WINDOWS} "
            "window medians are degenerate (1 window = point, 2 = range of two "
            "values). Rank bands are exploratory, not established ties or "
            "separations.")
    return notes


def aggregate(manifest_path, out=None):
    """Repeated-window analysis. See module docstring for methodology."""
    man = json.load(open(manifest_path))
    dirs = man["windows"]
    ranked = []
    results = []
    for d in dirs:
        ranked.append({r["ip"]: r for r in json.load(open(f"{d}/ranked.json"))["ranked"]})
        results.append(load_results(d))
    # historical-format compat: pre-restructure runs lack category; derive from
    # stored PTR exactly like load() does
    for m in ranked:
        for ip, e in m.items():
            if e.get("category") is None:
                if "measure" not in sys.modules:
                    sys.path.insert(0, __import__('os').path.dirname(__file__))
                    import measure  # noqa: F401
                e["category"] = sys.modules["measure"].categorize(ip, e.get("ptr", "?"))
    nw = len(dirs)
    common = set(ranked[0])
    for m in ranked[1:]:
        common &= set(m)
    common = sorted(common)
    label = lambda d: d.rsplit("/", 1)[-1]

    # noise floor from forced references: their worst-best window median delta
    ref_deltas = []
    for ip in REFS:
        if all(ip in ranked[i] for i in range(nw)):
            meds = [ranked[i][ip]["u_med"] for i in range(nw)]
            ref_deltas.append(max(meds) - min(meds))
    noise_floor = statistics.median(ref_deltas) if ref_deltas else 2.0
    reg_threshold = max(2.0, 2.0 * noise_floor)

    servers = []
    for ip in common:
        meds = [ranked[i][ip]["u_med"] for i in range(nw)]
        kds = [ranked[i][ip].get("kd_med") for i in range(nw)
               if ranked[i][ip].get("kd_med") is not None]
        med = statistics.median(meds)
        wire = round(med - statistics.median(kds), 2) if kds else None
        lo, hi = bootstrap_ci(meds)
        ranks = []
        for i in range(nw):
            order = sorted((e["u_med"], eip) for eip, e in ranked[i].items())
            ranks.append(1 + [eip for _, eip in order].index(ip))
        suc_u = suc_c = n_w = 0
        for i in range(nw):
            r = results[i].get(ip)
            if not r or r.get("dead"):
                continue
            n_w += 1
            tot_u = len(r.get("uncached", [])) + r.get("to_u", 0) + r.get("fail_u", 0)
            suc_u += len(r.get("uncached", [])) / tot_u if tot_u else 0.0
            tot_c = len(r.get("cached", [])) + r.get("to_c", 0) + r.get("bad_c", 0)
            suc_c += len(r.get("cached", [])) / tot_c if tot_c else 0.0
        servers.append({
            "ip": ip, "ptr": ranked[0][ip].get("ptr", "?"),
            "provider": provider_of(ranked[0][ip].get("category", "?")),
            "window_medians": meds,
            "u_med": med,
            "wire_med": wire,
            "kd_med": round(statistics.median(kds), 3) if kds else None,
            "between_range_ms": round(max(meds) - min(meds), 2),
            "between_stdev_ms": round(statistics.stdev(meds), 2) if nw > 1 else 0.0,
            "within_spread_ms": round(statistics.mean(
                ranked[i][ip]["u_p90"] - ranked[i][ip]["u_med"] for i in range(nw)), 2),
            "ci95": [round(lo, 2), round(hi, 2)],
            "rank_per_window": ranks,
            "rank_spread": max(ranks) - min(ranks),
            "success_u": round(suc_u / n_w, 3) if n_w else None,
            "success_c": round(suc_c / n_w, 3) if n_w else None,
            "regression_ms": round(max(meds) - min(meds), 2),
        })
    servers.sort(key=lambda s: s["u_med"])
    for s in servers:
        if s["regression_ms"] > reg_threshold:
            s["material_regression"] = True
    # overlapping-CI rank bands: unresolved comparisons are ties
    band = 0
    prev_hi = None
    for s in servers:
        if prev_hi is None or s["ci95"][0] > prev_hi:
            band += 1
        s["rank_band"] = band
        prev_hi = max(prev_hi or s["ci95"][1], s["ci95"][1])
    # provider clustering: robust median center, count-independent
    providers = defaultdict(list)
    for s in servers:
        providers[s["provider"]].append(s)
    prov = []
    for name, members in providers.items():
        members.sort(key=lambda s: s["u_med"])
        est = statistics.median(m["u_med"] for m in members)
        prov.append({"provider": name, "members": len(members), "u_med": round(est, 2),
                     "ips": [m["ip"] for m in members]})
    prov.sort(key=lambda p: p["u_med"])
    band1 = [s for s in servers if s["rank_band"] == 1]
    out_rec = {
        "manifest": manifest_path, "windows": [label(d) for d in dirs],
        "n_windows": nw, "n_servers": len(servers),
        "noise_floor_ms": round(noise_floor, 2),
        "regression_threshold_ms": round(reg_threshold, 2),
        "methodology": {
            "primary_estimate": "median of per-window medians (window-level, not pooled)",
            "ci": f"bootstrap over windows, {BOOTSTRAP_N} resamples, seed {BOOTSTRAP_SEED}",
            "rank_bands": "servers merged into one band while 95% CIs overlap "
                          "(order not statistically resolved; overlap is not equivalence)",
            "providers": "PTR-derived category as identity; unknowns explicit; "
                         "provider estimate = median of member estimates (no count advantage)",
            "regression_flag": f"worst-best window median delta > {reg_threshold:.1f} ms "
                               f"(= max(2 ms, 2x reference noise floor {noise_floor:.2f} ms))",
            "wire_med": "u_med - median(kd_med): send->kernel-rx estimate, excludes "
                        "event-loop userspace delay; only when kernel timestamps present",
            "window_cadence_caveat": "back-to-back windows share one network state; "
                                     "they quantify short-timescale noise only. For "
                                     "diurnal claims, space windows hours apart and "
                                     "aggregate with the same command.",
        },
        "fastest_observed": [s["ip"] for s in servers[:10]],
        "rank_band_1": [s["ip"] for s in band1],
        "most_stable_band1": [s["ip"] for s in
                              sorted(band1, key=lambda s: s["between_range_ms"])[:10]],
        "best_overall": [s["ip"] for s in sorted(
            band1, key=lambda s: (s["between_range_ms"], s["u_med"]))[:10]],
        "material_regressions": [s["ip"] for s in servers if s.get("material_regression")],
        "providers": prov, "servers": servers,
        "notes": _notes(nw),
    }
    out = out or manifest_path.rsplit("/", 1)[0] + "/aggregate.json"
    json.dump(out_rec, open(out, "w"), indent=1)
    print(f"aggregate: {nw} windows, {len(servers)} common servers -> {out}")
    if not servers:
        print("no common servers across windows; aggregate.json written (empty)")
        return out_rec
    print(f"noise floor (ref worst-best): {noise_floor:.2f} ms; "
          f"regression threshold {reg_threshold:.2f} ms")
    print(f"rank bands: {servers[-1]['rank_band']} bands over {len(servers)} servers; "
          f"band 1 = {len(band1)} servers (fastest band; order not statistically resolved)")
    print("top 12 by median-of-window-medians:")
    for s in servers[:12]:
        print(f"  {s['ip']:>16} med={s['u_med']:>6.1f} wire={s['wire_med'] if s['wire_med'] is not None else '-':>7} "
              f"between={s['between_range_ms']:>5.1f} band={s['rank_band']:>2} "
              f"suc_u={s['success_u']} suc_c={s['success_c']} {s['provider']}")
    print("providers (median of member estimates; count-independent):")
    for p in prov[:12]:
        print(f"  {p['provider']:<28} est={p['u_med']:>7.1f} members={p['members']}")
    if out_rec["material_regressions"]:
        print(f"MATERIAL REGRESSIONS: {out_rec['material_regressions']}")
    return out_rec


def main():
    args = sys.argv[1:]
    if "--aggregate" in args:
        i = args.index("--aggregate")
        man = args[i + 1]
        rest = args[i + 2:]
        out = rest[rest.index("--out") + 1] if "--out" in rest else None
        aggregate(man, out)
        return
    if "--pools" in args:
        i = args.index("--pools")
        pools = args[i + 1:]
        args = args[:i]
        sets = [set(r["ip"] for r in json.load(open(p))["pool"][:150]) for p in pools]
        print("finalist-pool overlap (top150 each):")
        for x in range(len(pools)):
            for y in range(x + 1, len(pools)):
                print(f"  {pools[x]} vs {pools[y]}: "
                      f"{len(sets[x] & sets[y])}/150")
    runs = [load(d) for d in args]
    if len(runs) < 1:
        sys.exit("need at least one run dir")

    for r in runs:
        flags = f" | {len(r['incomplete'])} incomplete-cached" if r["incomplete"] else ""
        print(f"\nrun {r['dir']} ({r['id']}): {r['n_ranked']} ranked{flags}")
        print(f"  composition: {dict(r['cats'])}")
        for ip, (rank_, um, cm) in sorted(r["refs"].items(), key=lambda kv: kv[1][0]):
            print(f"  ref {ip:>16} rank {rank_:>3} u_med={um:>5} c_med={cm:>5}")

    if len(runs) >= 2:
        print("\ntop-N overlap matrix (denominator = smaller run's ranked count):")
        for a, b in combinations(runs, 2):
            na = min(50, a["n_ranked"])
            nb = min(50, b["n_ranked"])
            ov = len(set(a["top50"][:na]) & set(b["top50"][:nb]))
            print(f"  {a['dir']} vs {b['dir']}: {ov}/{min(na, nb)}")
        refdrift = {}
        for ip in REFS:
            vals = [(r["dir"], r["refs"].get(ip)) for r in runs if ip in r["refs"]]
            if len(vals) >= 2:
                meds = [v[1][1] for v in vals if v[1]]
                if len(set(meds)) > 1:
                    refdrift[ip] = vals
        if refdrift:
            print("\nreference drift across runs (rank, u_med):")
            for ip, vals in refdrift.items():
                pretty = ", ".join(f"{d.rsplit('/',1)[-1]}: r{v[0]}, {v[1]}ms" for d, v in vals)
                print(f"  {ip:>16} {pretty}")
        print("\nClaim template: 'X-category endpoints dominate the top-50 in N/N runs; "
              "individual ranks are window-local snapshots (top-50 overlap: see matrix).'")


if __name__ == "__main__":
    main()
