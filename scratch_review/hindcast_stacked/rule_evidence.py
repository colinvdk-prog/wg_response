"""Evidence for hindcast_lib's SNAPSHOT RULE, per rebuilt snapshot.

For every snapshot `repair` rebuilt (the 42 that stacked same-time rows):
  - the stamp, the kept first publication's poly_DateCurrent lag after it,
    and the dropped versions' lags (how long after the stamp they were
    edited), their acreage relative to the first publication, and how many
    source records the rows came from (daily_rows_2026-09-27/);
  - the GIS acreage, what the PRE-FIX stacked rings rasterized to, and what
    the repaired polygon rasterizes to — both on the replay's own grid (the
    incident's cached terrain tile, the scoring mask's rasterizer).
Pre-fix rings come from the worktree at 0bbd430 (../hindcast-before).
Writes rule_evidence.json and rule_evidence.md next to this script. Offline.
"""
import json
import os
import statistics
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BEFORE = os.path.join(os.path.dirname(ROOT), "hindcast-before")
sys.path.insert(0, ROOT)
from tools import hindcast_lib as H  # noqa: E402
from tools.hindcast_harness import _load_incident, _incident_domain  # noqa: E402
from wgresponse.contracts import LatLon, ResponseConfig  # noqa: E402
from wgresponse.terrain import LfpsTerrain, load_ascii_tile  # noqa: E402

CORPORA = (("pinned", "tools/hindcast_data"),
           ("oos", "scratch_review/hindcast_oos_data"),
           ("aug", "scratch_review/hindcast_aug_data"))
ACRE = 4046.8564224


def ep(v):
    if v is None:
        return None
    v = float(v)
    return v / 1000.0 if v > 1e11 else v


def rings_of(snap):
    return [[LatLon(v[0], v[1]) for v in ring] for ring in snap["rings"]]


def main() -> int:
    rows = []
    for corpus, rel in CORPORA:
        inc_dir = os.path.join(ROOT, rel, "incidents")
        for fn in sorted(os.listdir(inc_dir)):
            path = os.path.join(inc_dir, fn)
            with open(path, "r", encoding="utf-8") as f:
                new = json.load(f)
            rebuilt = (new.get("repair") or {}).get("rebuilt_snapshots") or []
            if not rebuilt:
                continue
            with open(os.path.join(BEFORE, rel, "incidents", fn), "r", encoding="utf-8") as f:
                old = json.load(f)
            with open(os.path.join(HERE, "daily_rows_2026-09-27", fn), "r", encoding="utf-8") as f:
                daily = {ft["attributes"]["OBJECTID"]: ft["attributes"]
                         for ft in json.load(f)["features"]}
            inc = _load_incident(path)
            bbox, cell_m = _incident_domain(inc, ResponseConfig())
            cp = LfpsTerrain(cache_dir=os.path.join(ROOT, rel, "lfps")).cache_path(bbox, cell_m)
            spec = load_ascii_tile(cp).spec
            cell_ac = spec.cell_m ** 2 / ACRE
            old_by_t = {s["t_s"]: s for s in old["snapshots"]}
            new_by_t = {s["t_s"]: s for s in new["snapshots"]}
            for c in rebuilt:
                t = c["t_s"]
                kept, dropped = c["kept_oids"], c["dropped_oids"]
                lag = lambda o: (ep(daily[o]["poly_DateCurrent"]) - t) / 3600.0
                first_ac = sum(daily[o]["poly_GISAcres"] or 0 for o in kept)
                dac = [daily[o]["poly_GISAcres"] or 0 for o in dropped]
                records = {daily[o].get("poly_SourceGlobalID") for o in kept + dropped}
                rows.append({
                    "corpus": corpus, "incident": new["name"],
                    "stamp_utc": datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "rows": len(kept) + len(dropped), "records": len(records),
                    "first_pub_lag_h": round(lag(kept[0]), 1),
                    "last_edit_lag_h": round(max(lag(o) for o in dropped), 1),
                    "first_ac": round(first_ac),
                    "max_version_ac": round(max(dac + [first_ac])),
                    "last_version_ac": round(daily[max(dropped)]["poly_GISAcres"] or 0),
                    "prefix_recorded_ac": round(c["acres_before"] or 0),
                    "stacked_raster_ac": round(len(H.polygon_interior_mask(spec, rings_of(old_by_t[t]))) * cell_ac),
                    "repaired_raster_ac": round(len(H.polygon_interior_mask(spec, rings_of(new_by_t[t]))) * cell_ac),
                })
    with open(os.path.join(HERE, "rule_evidence.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(rows, f, indent=1)

    lines = ["| corpus | incident | stamp (UTC) | rows / records | first pub. lag h | last edit lag h "
             "| first pub. ac | largest version ac | pre-fix recorded ac | stacked raster ac | repaired raster ac |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['corpus']} | {r['incident']} | {r['stamp_utc']} | {r['rows']} / {r['records']} "
                     f"| {r['first_pub_lag_h']} | {r['last_edit_lag_h']} | {r['first_ac']:,} "
                     f"| {r['max_version_ac']:,} | {r['prefix_recorded_ac']:,} | {r['stacked_raster_ac']:,} "
                     f"| {r['repaired_raster_ac']:,} |")
    fl = [r["first_pub_lag_h"] for r in rows]
    ll = [r["last_edit_lag_h"] for r in rows]
    grow = [r for r in rows if r["max_version_ac"] > 1.10 * r["first_ac"]]
    zero = [r for r in rows if r["stacked_raster_ac"] == 0]
    half = [r for r in rows if r["stacked_raster_ac"] < 0.5 * r["first_ac"]]
    multi = [r for r in rows if r["records"] > 1]
    rel_err = [abs(r["repaired_raster_ac"] - r["first_ac"]) / r["first_ac"] for r in rows if r["first_ac"] > 0]
    summary = [
        f"- snapshots rebuilt: {len(rows)}",
        f"- first publication's edit time after its stamp: median {statistics.median(fl):.1f} h, max {max(fl):.1f} h",
        f"- latest dropped version's edit time after the stamp: median {statistics.median(ll):.1f} h, max {max(ll):.1f} h",
        f"- a later version larger than the first publication by > 10 %: {len(grow)} of {len(rows)}",
        f"- rows from more than one source record (re-uploads under a new id): {len(multi)} of {len(rows)}",
        f"- pre-fix stacked snapshot rasterized to 0 cells: {len(zero)}; to under half the first publication's area: {len(half)}",
        f"- repaired snapshot's raster vs its GIS acreage: median |error| {100 * statistics.median(rel_err):.1f} %, max {100 * max(rel_err):.1f} %",
    ]
    text = "\n".join(summary + [""] + lines) + "\n"
    with open(os.path.join(HERE, "rule_evidence.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
