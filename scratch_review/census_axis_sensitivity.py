"""Axis-sensitivity tables + wind-speed curve (2026-08-01), recorded data
only for part (a); part (b)'s low/moderate points are read from the
census_wind_*.json files written by scratch_review/census_windregime.py
(run that script first -- this script does not simulate anything itself).

Part (a): pools opt_ember_*.json rows (each row = one scenario, each row's
"homes" = 40 per-home dicts with gelled/front/dist) into "belt survivability"
-- the same front-OR-dist<=1000m criterion opt_ember.pool_stats calls
"e1000", i.e. among homes within EMBER RANGE of the fire (front-reached OR
<=1000m of a burned cell), what fraction got gelled -- grouped by (i) map,
(ii) wind_off, (iii) ignition distance, (iv) intensity, for:
  opt_ember_v_base_a.json   full suite (60 rows), 4 crews, baseline stack
  opt_ember_v_c8d10.json    full suite (60 rows), 8 crews, recommended stack
  opt_ember_flip_c4.json    screen suite (20 rows, wind_off=0 only), 4 crews
  opt_ember_flip_c8.json    screen suite (20 rows, wind_off=0 only), 8 crews
Each file's own recorded stats.e1000 is cross-checked against this script's
independently-recomputed pooled (all-homes) belt tuple as a self-check.

Part (b): assembles the full wind-speed belt-survivability curve --
low(8mph)/moderate(18mph)/high(28mph)/extreme(40mph) x crews(4/8) -- using:
  low/moderate  scratch_review/census_wind_{low,moderate}_c{4,8}.json
                (pooled stats.e1000, written by census_windregime.py)
  high/extreme  opt_ember_flip_c{4,8}.json rows split by intensity (a
                by-intensity slice of the part-(a) grouping above) -- SAME
                config (deploy=10, egress=30, ember_radius=200, hold=24,
                suite screen/wind_off=0) as the low/moderate runs, so the
                four points are directly comparable.

Usage: py -3 scratch_review/census_axis_sensitivity.py
Writes scratch_review/census_axis_sensitivity.json; prints grouped tables
and the wind-speed curve to stdout.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def rel(p):
    return os.path.relpath(p, ROOT).replace(os.sep, "/")


def load(name):
    with open(os.path.join(HERE, name), "r", encoding="utf-8") as f:
        return json.load(f)


def parse_seed(seed):
    """'map|+off|dist|intensity' -> dict. Sign-prefixed wind_off and plain
    integer dist are both float()-safe as written by bench_dispatch.scenarios
    / opt_ember / census_windregime (all three use this exact convention)."""
    m, off, dist, inten = seed.split("|")
    return dict(map=m, wind_off=float(off), dist=float(dist), inten=inten)


def flatten(fname):
    """opt_ember_<tag>.json -> list of per-home dicts tagged with the
    scenario axes (map/wind_off/sc_dist/inten) pulled from the row's seed
    string (opt_ember rows do not carry wind_off/dist as separate fields --
    only inside the seed, per the task brief)."""
    d = load(fname)
    recs = []
    for row in d["rows"]:
        if row.get("error"):
            continue
        meta = parse_seed(row["seed"])
        assert meta["map"] == row["map"] and meta["inten"] == row["inten"], \
            f"seed/row field mismatch in {fname}: {row['seed']!r} vs " \
            f"map={row.get('map')} inten={row.get('inten')}"
        for h in row["homes"]:
            recs.append(dict(map=meta["map"], wind_off=meta["wind_off"],
                             sc_dist=meta["dist"], inten=meta["inten"],
                             gelled=bool(h.get("gelled")),
                             front=bool(h.get("front")),
                             burn_dist_m=h.get("dist")))
    return d, recs


def in_belt(h):
    """opt_ember.pool_stats' e1000 criterion: front-reached OR within 1000m
    of a ground-truth-burned cell -- "ember range of actual burn"."""
    return h["front"] or (h["burn_dist_m"] is not None and h["burn_dist_m"] <= 1000.0)


def belt_tuple(recs):
    n = sum(1 for h in recs if in_belt(h))
    g = sum(1 for h in recs if in_belt(h) and h["gelled"])
    return dict(gelled=g, total=n, pct=round(100.0 * g / n, 1) if n else None)


def group_table(recs, keyfn, order=None):
    groups = {}
    for h in recs:
        groups.setdefault(keyfn(h), []).append(h)
    keys = order if order is not None else sorted(groups.keys())
    return {k: belt_tuple(groups[k]) for k in keys if k in groups}


def wind_off_label(v):
    return f"{v:+.0f}"


def print_table(title, table):
    print(f"  {title}")
    for k, v in table.items():
        pct = f"{v['pct']:5.1f}%" if v["pct"] is not None else "  n/a"
        print(f"    {str(k):<14} gelled={v['gelled']:>4} total={v['total']:>4}  {pct}")


def axis_tables_for(fname):
    d, recs = flatten(fname)
    pooled = belt_tuple(recs)
    recorded = d["stats"].get("e1000")
    if recorded is not None:
        ok = (pooled["gelled"], pooled["total"]) == (recorded[0], recorded[1])
        check = "OK" if ok else "MISMATCH"
    else:
        check = "n/a (no stats.e1000 in file)"
    tables = dict(
        by_map=group_table(recs, lambda h: h["map"]),
        by_wind_off=group_table(recs, lambda h: wind_off_label(h["wind_off"]),
                                order=["-40", "+0", "+40"]),
        by_dist=group_table(recs, lambda h: int(h["sc_dist"]), order=[6000, 8000]),
        by_intensity=group_table(recs, lambda h: h["inten"],
                                 order=["low", "moderate", "high", "extreme"]),
    )
    return dict(file=rel(os.path.join(HERE, fname)), tag=d.get("tag"),
               suite=d.get("suite"), params=d.get("params"),
               pooled_belt=pooled, self_check_vs_recorded_e1000=check,
               tables=tables)


def main():
    print("=" * 78)
    print("AXIS SENSITIVITY -- belt survivability (front OR dist<=1000m), "
          "gelled fraction")
    print("=" * 78)

    files = dict(v_base_a="opt_ember_v_base_a.json",
                 v_c8d10="opt_ember_v_c8d10.json",
                 flip_c4="opt_ember_flip_c4.json",
                 flip_c8="opt_ember_flip_c8.json")
    results = {}
    for label, fname in files.items():
        r = axis_tables_for(fname)
        results[label] = r
        print(f"\n[{label}]  {r['file']}  suite={r['suite']} "
              f"tag={r['tag']}  self-check vs recorded stats.e1000: "
              f"{r['self_check_vs_recorded_e1000']}")
        print(f"  POOLED belt: gelled={r['pooled_belt']['gelled']} "
              f"total={r['pooled_belt']['total']} pct={r['pooled_belt']['pct']}%")
        print_table("by map:", r["tables"]["by_map"])
        print_table("by wind_off:", r["tables"]["by_wind_off"])
        print_table("by dist (m):", r["tables"]["by_dist"])
        print_table("by intensity:", r["tables"]["by_intensity"])

    print("\n" + "=" * 78)
    print("PRIMARY GROUPED TABLES (as requested): v_base_a (4 crews "
          "baseline) vs v_c8d10 (recommended stack)")
    print("=" * 78)
    for label in ("v_base_a", "v_c8d10"):
        r = results[label]
        print(f"\n[{label}] pooled belt survivability: "
              f"{r['pooled_belt']['gelled']}/{r['pooled_belt']['total']} "
              f"({r['pooled_belt']['pct']}%)")
        print_table("by map:", r["tables"]["by_map"])
        print_table("by wind_off (+0 vs +40 vs -40):", r["tables"]["by_wind_off"])
        print_table("by dist (6000 vs 8000):", r["tables"]["by_dist"])
        print_table("by intensity (high vs extreme):", r["tables"]["by_intensity"])

    # ---------------------------------------------------- wind-speed curve
    print("\n" + "=" * 78)
    print("WIND-SPEED CURVE -- pooled belt survivability at low/moderate/"
          "high/extreme x c4/c8  (deploy=10 egress=30 ember_radius=200 "
          "hold=24, wind_off=0, 5 maps x dist 6000/8000)")
    print("=" * 78)
    wind_mph = dict(low=8.0, moderate=18.0, high=28.0, extreme=40.0)
    curve = {}
    for crews in (4, 8):
        curve[crews] = {}
        for level in ("low", "moderate"):
            fn = f"census_wind_{level}_c{crews}.json"
            path = os.path.join(HERE, fn)
            if os.path.exists(path):
                dd = load(fn)
                e1 = dd["stats"]["e1000"]
                curve[crews][level] = dict(gelled=e1[0], total=e1[1],
                                           pct=round(e1[2], 1),
                                           source=rel(path))
            else:
                curve[crews][level] = None
        flip = results[f"flip_c{crews}"]
        for level in ("high", "extreme"):
            t = flip["tables"]["by_intensity"].get(level)
            curve[crews][level] = dict(gelled=t["gelled"], total=t["total"],
                                       pct=t["pct"], source=flip["file"]) \
                if t else None

    for crews in (4, 8):
        print(f"\n  crews={crews}")
        for level in ("low", "moderate", "high", "extreme"):
            c = curve[crews][level]
            if c is None:
                print(f"    {level:<9} ({wind_mph[level]:>4.0f} mph)  MISSING")
                continue
            print(f"    {level:<9} ({wind_mph[level]:>4.0f} mph)  "
                  f"gelled={c['gelled']:>4} total={c['total']:>4}  "
                  f"{c['pct']:5.1f}%   <- {c['source']}")

    out = dict(axis_tables=results, wind_speed_curve=curve, wind_mph=wind_mph)
    out_path = os.path.join(HERE, "census_axis_sensitivity.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print("\nwrote", rel(out_path))


if __name__ == "__main__":
    main()
