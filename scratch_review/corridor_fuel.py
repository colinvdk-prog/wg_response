"""scratch_review/corridor_fuel.py — the fuel picture of each Vail -> Aspen
corridor tile (docs/vail_aspen_response_program.md §2.5): fuel-class shares
of burnable ground (GR / GS / SH / TU / TL), the GR+GS fraction against the
extreme-spread-regime gate (0.60), developed / water / nodata shares, the
WUI developed-cell count (the client-placement band), transit cold-black
windows weighted over burnable cells, elevation and slope. Reads the
committed standard tiles only; prints a markdown table + a per-tile detail
block. Usage: py -3 scratch_review/corridor_fuel.py [key ...]
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.fetch_sim_tiles import tile_path, wui_developed_cells  # noqa: E402
from wgresponse.fuels import FUEL_TABLE, cold_black_minutes, is_burnable  # noqa: E402
from wgresponse.simui import LOCATIONS  # noqa: E402
from wgresponse.terrain import load_ascii_tile  # noqa: E402

CORRIDOR = ("vail_co", "beaver_creek_co", "eagle_co", "glenwood_springs_co",
            "basalt_carbondale_co", "aspen_co")
CLASSES = ("GR", "GS", "SH", "TU", "TL", "SB")


def picture(key: str) -> dict:
    tile = load_ascii_tile(tile_path(key))
    fuel = [int(v) for v in tile.fuel.data]
    n = len(fuel)
    counts: dict = {}
    for c in fuel:
        counts[c] = counts.get(c, 0) + 1
    burn = {c: k for c, k in counts.items() if is_burnable(c)}
    nb = sum(burn.values())
    cls = {c: 0 for c in CLASSES}
    for code, k in burn.items():
        fm = FUEL_TABLE.get(code)
        name = fm.name.split()[0] if fm else "?"
        for c in CLASSES:
            if name.startswith(c):
                cls[c] += k
                break
    cold: dict = {}
    for code, k in burn.items():
        w = cold_black_minutes(code)
        cold[w] = cold.get(w, 0) + k
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]

    def nm(code):
        fm = FUEL_TABLE.get(code)
        return fm.name.split()[0] if fm else ("nodata" if code == -9999 else str(code))

    return dict(
        key=key, name=LOCATIONS[key].name, cells=n,
        burnable_pct=100.0 * nb / n,
        developed_pct=100.0 * (counts.get(91, 0) + counts.get(93, 0)) / n,
        water_pct=100.0 * counts.get(98, 0) / n,
        nodata_pct=100.0 * counts.get(-9999, 0) / n,
        cls_pct={c: (100.0 * cls[c] / nb if nb else 0.0) for c in CLASSES},
        grass_frac=((cls["GR"] + cls["GS"]) / nb) if nb else 0.0,
        cold={w: 100.0 * k / nb for w, k in sorted(cold.items())} if nb else {},
        wui_cells=wui_developed_cells(tile),
        elev=(min(tile.elev_m.data), max(tile.elev_m.data)),
        slope_max=max(tile.slope_deg.data),
        top=[f"{nm(c)} {100.0 * k / n:.1f}%" for c, k in top],
    )


def main(keys):
    rows = []
    for key in keys:
        if not pathlib.Path(tile_path(key)).exists():
            print(f"- {key}: tile not fetched yet")
            continue
        rows.append(picture(key))
    print("| community | burnable | developed 91/93 | GR | GS | SH | TU | TL | **GR+GS of burnable** (gate 0.60) | WUI dev cells (r≤3) | elev m | slope max | top codes |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for p in rows:
        c = p["cls_pct"]
        print(f"| {p['name']} | {p['burnable_pct']:.1f}% | {p['developed_pct']:.1f}% "
              f"| {c['GR']:.1f}% | {c['GS']:.1f}% | {c['SH']:.1f}% | {c['TU']:.1f}% | {c['TL']:.1f}% "
              f"| **{100.0 * p['grass_frac']:.1f}%** | {p['wui_cells']:,} "
              f"| {p['elev'][0]:.0f}–{p['elev'][1]:.0f} | {p['slope_max']:.0f}° | {', '.join(p['top'][:4])} |")
    print()
    print("| community | cold-black ≤10 min (grass) | 20–30 min (shrub) | 45 min (TU) | 60 min (TL) | water | nodata |")
    print("|---|---|---|---|---|---|---|")
    for p in rows:
        cb = p["cold"]
        g = cb.get(5.0, 0.0) + cb.get(10.0, 0.0)
        s = cb.get(20.0, 0.0) + cb.get(30.0, 0.0)
        print(f"| {p['name']} | {g:.1f}% | {s:.1f}% | {cb.get(45.0, 0.0):.1f}% | {cb.get(60.0, 0.0):.1f}% "
              f"| {p['water_pct']:.1f}% | {p['nodata_pct']:.1f}% |")


if __name__ == "__main__":
    main(sys.argv[1:] or CORRIDOR)
