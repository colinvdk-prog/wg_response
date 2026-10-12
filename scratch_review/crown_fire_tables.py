"""Crown-fire study: markdown tables for docs/review/crown_fire_model_
2026-10-12.md from the probes' JSON (NOT product code).

Usage: py -3 scratch_review/crown_fire_tables.py > out/crown/tables.md
"""
from __future__ import annotations

import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load(name):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def fmt(v, nd=0):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def canopy_table():
    with open(os.path.join(ROOT, "data", "tiles", "provenance_canopy.json"),
              encoding="utf-8") as fh:
        prov = json.load(fh)
    print("| Tile | Forest canopy (% of tile) | Canopy on TU / TL / SH / GS / GR (%) | Burnable canopy CBH P10 / P50 / P90 (m) | CBD P10 / P50 / P90 (kg/m³) | LFPS requests (submit / polls / download) |")
    print("|---|---|---|---|---|---|")
    ext = {k: r for k, r in prov["tiles"].items() if k.endswith("_ext")}
    for key, rec in sorted(prov["tiles"].items()):
        if key in ext:
            continue
        s = rec.get("summary") or {}
        by = s.get("canopy_pct_by_fuel_class", {})
        q = s.get("burnable_canopy_p10_p50_p90", {})
        r = rec.get("requests") or {}
        print(f"| {key} | {fmt(s.get('forest_canopy_pct_of_tile'), 1)} | "
              f"{by.get('TU', '—')} / {by.get('TL', '—')} / {by.get('SH', '—')} / "
              f"{by.get('GS', '—')} / {by.get('GR', '—')} | "
              f"{' / '.join(fmt(v, 1) for v in q.get('cbh_m', []))} | "
              f"{' / '.join(fmt(v, 2) for v in q.get('cbd_kgm3', []))} | "
              f"{r.get('submit', '—')} / {r.get('status-poll', '—')} / {r.get('zip-download', '—')} |")
    if ext:
        pc = sorted((r.get("summary") or {}).get("forest_canopy_pct_of_tile",
                                                 0.0) for r in ext.values())
        print(f"\nThe {len(ext)} extended fire-zone tiles (`<key>_ext.wgt`): "
              f"forest canopy on {pc[0]:.1f}-{pc[-1]:.1f} % of the tile "
              f"({', '.join(sorted(k[:-4] for k in ext))}).")


def r91_table():
    d = load("crown_fire_uniform.json")
    if not d or "r91" not in d:
        return
    print("| Run (Rothermel 1991, table 1) | 1-h moisture | 20-ft wind (mi/h) | Observed average (max), mi/h | R10 published / engine | Rothermel 1991 (3.34 R10) | Cruz 2005 at CBD 0.05 / 0.08 / 0.12 / 0.20 | Engine, uniform tile (CBD 0.12) | Engine surface fire alone |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in d["r91"]:
        c = r["cruz2005_mph"]
        u = r["u20_mph"]
        uw = f"{u[0]}" if u[0] == u[1] else f"{u[0]}-{u[1]}"
        print(f"| {r['fire']} {r['period']} | {r['m1h_pct']} % | {uw} | "
              f"{r['observed_mph']:.2f} ({fmt(r['observed_max_mph'], 2)}) | "
              f"{r['r10_published_mph']:.2f} / {r['r10_engine_mph']:.2f} | "
              f"{r['rothermel1991_mph']:.2f} | "
              f"{c['0.05']['mph']:.2f}{'p' if c['0.05']['cac'] < 1 else ''} / "
              f"{c['0.08']['mph']:.2f} / {c['0.12']['mph']:.2f} / "
              f"{c['0.2']['mph']:.2f} | {fmt(r['engine_crown_mph'], 2)} | "
              f"{fmt(r['engine_surface_mph'], 2)} |")


def wind_table():
    d = load("crown_fire_uniform.json")
    if not d or "wind" not in d:
        return
    rows = d["wind"]
    presets = ["low", "moderate", "high", "extreme"]
    print("| Surface fuel, canopy | " + " | ".join(
        f"{p} ({int(next(r['wind_mph'] for r in rows if r['preset'] == p))} mi/h)"
        for p in presets) + " |")
    print("|---|" + "---|" * len(presets))
    seen = []
    for r in rows:
        k = (r["fuel"], r["canopy"])
        if k in seen:
            continue
        seen.append(k)
        cells = []
        for p in presets:
            x = next(rr for rr in rows if (rr["fuel"], rr["canopy"]) == k
                     and rr["preset"] == p)
            kinds = x["kinds"]
            tag = ("A" if "2" in kinds else "P" if "1" in kinds else "S")
            if tag == "S":
                cells.append(f"{fmt(x['head_kmh_surface'], 2)} km/h "
                             f"({fmt(x['pct_of_u10_surface'], 1)} %) S")
            else:
                cells.append(f"{fmt(x['head_kmh_crown'], 1)} km/h "
                             f"({fmt(x['pct_of_u10_crown'], 1)} %) {tag}")
        print(f"| {k[0]}, {k[1]} | " + " | ".join(cells) + " |")


def scen_table():
    d = load("crown_fire_scenarios.json")
    if not d:
        return
    by = {(r["scenario"], r["variant"]): r for r in d}
    scens = [s for s in ORDER if any(k[0] == s for k in by)]
    wind = load("crown_fire_city_wind.json") or {}
    print("| Scenario (city wind, mean 0-6 h) | Variant | Acres 1 h / 3 h / 6 h | Fastest 1-h head gain, km/h (% of the wind) | First home reached | Crowned at 6 h (active / passive acres) | Departure violations / overrun rows | Homes served |")
    print("|---|---|---|---|---|---|---|---|")
    for sc in scens:
        for v in ("opt1", "grass_law", "crown", "crown_d10", "crown_aa",
                  "opt1_crown"):
            r = by.get((sc, v))
            if not r or "demo" not in r:
                continue
            dm = r["demo"]
            mk = dm["marks"]
            au = dm["audit"]
            c6 = r["crown"]["6h"]
            w = wind.get(sc, {}).get("mean")
            print(f"| {sc} ({'—' if w is None else f'{w:.0f} mi/h'}) | {v} | "
                  f"{mk['1.0']['acres']:,} / {mk['3.0']['acres']:,} / "
                  f"{mk['6.0']['acres']:,} | {wind_share(sc, dm['max_head_gain_km_per_h'])} | "
                  f"{fmt(dm['first_home_reached_h'], 2)} h | "
                  f"{c6['active']:,} / {c6['passive']:,} | "
                  f"{au['departure_violations_final']} / {au['crew_overrun_rows_sum']} | "
                  f"{au['served']} |")


ORDER = ["park_city_windstorm", "vail_westerly", "beaver_creek_westerly",
         "glenwood_springs_westerly", "basalt_carbondale_southwesterly",
         "aspen_southwesterly", "eagle_vail_corridor_westerly",
         "eagle_gypsum_westerly", "boulder_chinook",
         "rancho_santa_fe_santa_ana", "malibu_santa_ana",
         "paradise_ne_wind", "santa_rosa_diablo"]


def start_label(row):
    """The ignition cell's surface fuel model and canopy, in words."""
    import sys
    sys.path.insert(0, ROOT)
    from wgresponse.fuels import FUEL_TABLE
    ig = row["crown"]["ignition_cell"]
    cp = ig.get("canopy") or {}
    fm = FUEL_TABLE.get(ig["fuel"])
    start = fm.name.split()[0] if fm else str(ig["fuel"])
    if cp.get("cbd_kgm3", 0) == 0.01 and cp.get("cbh_m") == 10.0:
        return start + " under aspen / oak (LANDFIRE hardwood code)"
    if cp.get("cbd_kgm3", 0) >= 0.02 and cp.get("ch_m", 0) > cp.get("cbh_m", 0):
        return (start + f" under conifer (CBH {cp['cbh_m']:g} m, CBD "
                f"{cp['cbd_kgm3']:g})")
    return start + ", no canopy"


def wind_share(sc, kmh):
    w = (load("crown_fire_city_wind.json") or {}).get(sc)
    if not w:
        return f"{kmh:.1f}"
    return f"{kmh:.1f} ({100 * kmh / (w['mean'] * 1.609344):.0f} %)"


def founder_table():
    d = load("crown_fire_scenarios.json")
    if not d:
        return
    by = {(r["scenario"], r["variant"]): r for r in d}
    print("| Scenario (where it starts) | Acres at 1 h / 3 h / 6 h: option 1 → crown fire | Fastest hour, head km/h (% of the wind): option 1 → crown fire | First home reached (h): option 1 → crown fire | Crown fire with the 5-min spot delay: acres at 6 h; fastest hour | Grass floor alone: acres at 6 h |")
    print("|---|---|---|---|---|---|")
    for sc in ORDER:
        a, b, c, g = (by.get((sc, v)) for v in ("opt1", "crown_d10", "crown",
                                                "grass_law"))
        if not (a and b and c and g):
            print(f"| {sc} | (pending) | | | | |")
            continue
        ma, mb = a["demo"]["marks"], b["demo"]["marks"]
        fa = a["demo"]["first_home_reached_h"]
        fb = b["demo"]["first_home_reached_h"]
        print(f"| {sc} ({start_label(b)}) | "
              f"{ma['1.0']['acres']:,} / {ma['3.0']['acres']:,} / {ma['6.0']['acres']:,} → "
              f"{mb['1.0']['acres']:,} / {mb['3.0']['acres']:,} / {mb['6.0']['acres']:,} | "
              f"{wind_share(sc, a['demo']['max_head_gain_km_per_h'])} → "
              f"{wind_share(sc, b['demo']['max_head_gain_km_per_h'])} | "
              f"{fmt(fa, 1)} → {fmt(fb, 1)} | "
              f"{c['demo']['marks']['6.0']['acres']:,}; "
              f"{wind_share(sc, c['demo']['max_head_gain_km_per_h'])} | "
              f"{g['demo']['marks']['6.0']['acres']:,} |")


def torch_table():
    d = load("crown_fire_torch_floor.json")
    if not d:
        return
    print("| Scenario | Minimum fire size for the torching reach | Acres 1 h / 3 h / 6 h | Fastest hour | Spot ignitions (6 h) | of which thrown by a one-cell fire | Spot-from-spot chain depth | First spot > 0.5 km (age, fire size) |")
    print("|---|---|---|---|---|---|---|---|")
    for r in d:
        sp = r["spotting"]
        f5 = sp.get("first_spot_over_0.5km")
        fl = r["torch_min_fire_ha"]
        print(f"| {r['scenario']} | {'none (no floor)' if fl is None else f'{fl:g} ha'} | "
              f"{r['acres']['1h']:,} / {r['acres']['3h']:,} / {r['acres']['6h']:,} | "
              f"{r['fastest_1h_head_gain_kmh']:.1f} km/h | {sp['spot_ignitions_6h']} | "
              f"{sp['thrown_by_one_cell_fire']} | {sp['max_chain_depth']} | "
              f"{'—' if not f5 else str(f5['age_min']) + ' min, ' + str(f5['fire_ha']) + ' ha'} |")


def sens_table():
    d = load("crown_fire_sensitivity.json")
    if not d:
        return
    print("| Scenario | Change | Acres 1 h / 3 h / 6 h | Fastest hour | Crowned at 6 h (active / passive acres) |")
    print("|---|---|---|---|---|")
    for r in d:
        a = r["acres"]
        c = r["crown_6h"]
        print(f"| {r['scenario']} | {r['row']} | {a['1.0']:,} / {a['3.0']:,} / {a['6.0']:,} | "
              f"{r['fastest_kmh']:.1f} km/h | {c['active']:,} / {c['passive']:,} |")


def hind_table():
    d = load("crown_fire_hindcast.json")
    if not d:
        return
    arms = ("deployed", "grassonly", "crown", "crown_dep")
    print("| Phase (steps) | P50 area / burned area, median: deployed / grass-only / grass-only + crown / deployed + crown | P10 adequacy | P50 hit | False alarm (% of fire area) |")
    print("|---|---|---|---|---|")
    for ph in ("all", "growing", "moderate", "stalled"):
        s = d["by_phase"][ph]
        n = s["crown"]["steps"] or s["deployed"]["steps"]

        def g(key, scale=1.0, nd=2, suf=""):
            vals = []
            for a in arms:
                v = s[a].get(key)
                vals.append("—" if v is None else f"{v * scale:.{nd}f}{suf}")
            return " / ".join(vals)
        print(f"| {ph} ({n}) | {g('median_area_ratio_p50', 1, 2, 'x')} | "
              f"{g('p10_adequacy', 100, 2, ' %')} | {g('p50_hit', 100, 1, ' %')} | "
              f"{g('median_false_alarm_frac', 100, 0, ' %')} |")


def hind_fire_table():
    d = load("crown_fire_hindcast.json")
    if not d:
        return
    arms = ("deployed", "grassonly", "crown", "crown_dep")
    print("| Fire (steps) | P50 area / burned (median): deployed / grass-only / grass-only + crown / deployed + crown | P10 adequacy |")
    print("|---|---|---|")
    for key, s in d["per_fire"].items():
        n = s["crown"]["steps"]
        ar = " / ".join("—" if s[a]["median_area_ratio_p50"] is None
                        else f"{s[a]['median_area_ratio_p50']:.2f}x" for a in arms)
        p10 = " / ".join("—" if s[a]["p10_adequacy"] is None
                         else f"{100 * s[a]['p10_adequacy']:.2f} %" for a in arms)
        print(f"| {key} ({n}) | {ar} | {p10} |")
    k = d.get("crown_kinds_new_cells_by_phase", {})
    print("\nNominal forecast's new cells by fire type (grass-only + crown):")
    for ph, v in k.items():
        tot = sum(v.values()) or 1
        print(f"- {ph}: surface {100 * v.get('surface', 0) / tot:.0f} %, passive "
              f"{100 * v.get('passive', 0) / tot:.0f} %, active "
              f"{100 * v.get('active', 0) / tot:.0f} %")
    low = d.get("steps_p10_adequacy_lower") or {}
    if low:
        c, cd = low.get("crown", {}), low.get("crown_dep", {})
        print(f"\nSteps whose P10 held less of the new ground than another "
              f"arm's (of {d.get('steps')}): grass-only + crown, "
              f"{c.get('vs_grassonly')} against the grass-only floor and "
              f"{c.get('vs_deployed')} against deployed; deployed + crown, "
              f"{cd.get('vs_grassonly')} and {cd.get('vs_deployed')}.")
    tail = d.get("p10_tail")
    if tail:
        print("\n| Arm | Lowest step's P10 adequacy (fire, dates) | Steps under 90 % |")
        print("|---|---|---|")
        names = {"deployed": "deployed (timber floor)",
                 "grassonly": "grass-only floor",
                 "crown": "grass-only + crown",
                 "crown_dep": "deployed + crown"}
        for arm in arms:
            t = tail[arm]
            m = t["min"]
            print(f"| {names[arm]} | {100 * m['p10_adequacy']:.1f} % "
                  f"({m['fire']}, {m['dates']}) | {t['steps_under_90']} |")
        print("\nSteps where an arm other than grass-only held under 90 %:")
        for u in d.get("p10_steps_under_90_excl_grassonly", []):
            v = u["p10_adequacy"]
            print(f"- {u['fire']} {u['dates']} ({u['phase']}): " + " / ".join(
                f"{names[a]} {100 * v[a]:.1f} %" for a in arms if a in v))


def generic_table():
    rows = []
    for p in ("crown_fire_scenarios_generic.json",):
        d = load(p)
        if d:
            rows.extend(d)
    if not rows:
        return
    by = {(r["scenario"], r["variant"]): r for r in rows}
    scens = [s for s in ORDER if any(k[0] == s for k in by)]
    print("| Scenario (preset wind) | Fastest 1-h head gain: option 1 / grass-only / grass-only + crown / the same with the 10-min spot delay (share of the 10-m wind) | Acres at 6 h: option 1 / grass-only / grass-only + crown / 10-min delay |")
    print("|---|---|---|")
    for sc in scens:
        cells_f, cells_a = [], []
        mph = None
        for v in ("opt1@generic", "grass_law@generic", "crown@generic",
                  "crown_d10@generic"):
            r = by.get((sc, v))
            if not r:
                cells_f.append("—")
                cells_a.append("—")
                continue
            mph = r["weather0"]["wind_mph"]
            g = r["nominal"]["max_head_gain_km_per_h"]
            cells_f.append(f"{g:.1f} km/h ({100 * g / (mph * 1.609344):.0f} %)")
            cells_a.append(f"{r['nominal']['marks']['6.0']['acres']:,}")
        print(f"| {sc} ({mph:.0f} mi/h) | {' / '.join(cells_f)} | "
              f"{' / '.join(cells_a)} |")


if __name__ == "__main__":
    for name, fn in (("canopy", canopy_table), ("r91", r91_table),
                     ("wind", wind_table), ("generic", generic_table),
                     ("founder", founder_table),
                     ("scenarios", scen_table),
                     ("torch", torch_table), ("sensitivity", sens_table),
                     ("hindcast", hind_table),
                     ("hindcast per fire", hind_fire_table)):
        print(f"\n### {name}\n")
        fn()
