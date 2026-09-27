"""tools/gen_aoi_conus.py — (re)generate data/aoi_conus.json, the W6 thin
viewer's CONUS AOI (docs/LIVE_WORLD_PLAN.md "W6 thin viewer (2026-09-26)").

One bbox over the contiguous US (W -125, S 24, E -66, N 50) plus the UNION
of every simulator preset's safe zones (wgresponse.simui.LOCATIONS, in
declaration order), each labelled UNVERIFIED exactly as the Vail -> Aspen
corridor AOI labels its zones. tests/test_world_viewer.py pins the file
against LOCATIONS, so a new preset fails that test until this is re-run.

Usage: py -3 tools/gen_aoi_conus.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wgresponse.simui import LOCATIONS  # noqa: E402

CONUS_BBOX = [-125.0, 24.0, -66.0, 50.0]


def build() -> dict:
    zones = []
    for key in LOCATIONS:                       # preset declaration order
        for z in LOCATIONS[key].safe_zones:
            zones.append({"zone_id": z.zone_id, "lat": z.loc.lat,
                          "lon": z.loc.lon, "name": f"UNVERIFIED {z.name}"})
    return {
        "_comment": (
            "CONUS AOI (W6 thin viewer, docs/LIVE_WORLD_PLAN.md section 3 'what "
            "to build first': set the watcher's AOI to CONUS and put a zoomable "
            "map over its state). One bbox over the contiguous United States "
            "(W -125, S 24, E -66, N 50; Alaska and Hawaii are outside it). Safe "
            "zones are the UNION of every simulator preset's zones (wgresponse."
            "simui.LOCATIONS: the five originals, Rancho Santa Fe, the six Vail "
            "-> Aspen corridor communities and the whole-valley Eagle -> Vail "
            "tile, whose six I-70 anchors share coordinates with the corridor "
            "presets' zones under their own ids): PLANNING-GRADE highway / "
            "arterial nodes, NOT ground-verified -- per HOA_40_PLAYBOOK a real "
            "deployment requires ground-verified zones before any crew dispatch. "
            "Shadow / demo use only (--registry mock-presets, --dry-notify) until "
            "real clients and crews are enrolled. Regenerate with py -3 "
            "tools/gen_aoi_conus.py."),
        "zones": [{"name": "conus", "bbox": list(CONUS_BBOX)}],
        "safe_zones": zones,
    }


def main() -> int:
    out = ROOT / "data" / "aoi_conus.json"
    doc = build()
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"wrote {out} ({len(doc['safe_zones'])} safe zones)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
