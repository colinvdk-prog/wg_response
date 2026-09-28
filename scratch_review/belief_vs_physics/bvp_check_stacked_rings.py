"""Read-only side check (reported, not fixed): what the harness's multi-
OBJECTID ring union does to a pinned-corpus snapshot once it is rasterized
the way the replay seeds and scores it.

tools/hindcast_harness._fetch_incident unions the rings of EVERY OBJECTID
that shares a capture time. On WFIGS_Daily_Perimeters_Public those rows are
re-captures / in-place revisions of ONE polygon (bvp_fetch.py docstring),
so the union stacks near-duplicate rings. Both rasterizers are even-odd
(spread._perimeter_interior_cells seeds the engine; hindcast_lib.
polygon_interior_mask builds the scoring T1 mask), so two copies of a ring
cancel and near-copies leave slivers. For every multi-OBJECTID snapshot of
the pinned corpus (tools/hindcast_data) this prints the GIS acreage and the
interior the scoring rasterizer actually produces from the stored rings.
(The stored rings cannot be split back into their OBJECTIDs, so the
corrected area is not reconstructed here -- the GIS acreage is the
reference.)"""
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO)
import tools.hindcast_harness as HH  # noqa: E402
from tools import hindcast_lib as H  # noqa: E402
from wgresponse.contracts import ResponseConfig  # noqa: E402
from wgresponse.terrain import LfpsTerrain, load_ascii_tile  # noqa: E402

ACRE = 4046.8564224
DATA = os.path.join(_REPO, "tools", "hindcast_data")
for fn in sorted(os.listdir(os.path.join(DATA, "incidents"))):
    inc = HH._load_incident(os.path.join(DATA, "incidents", fn))
    raw = json.load(open(os.path.join(DATA, "incidents", fn)))
    bbox, cell_m = HH._incident_domain(inc, ResponseConfig())
    cp = LfpsTerrain(cache_dir=os.path.join(DATA, "lfps")).cache_path(bbox, cell_m)
    if not os.path.exists(cp):
        continue
    tile = load_ascii_tile(cp)
    cell_ac = tile.spec.cell_m ** 2 / ACRE
    for s_raw, s in zip(raw["snapshots"], inc.snapshots):
        if len(s_raw.get("oids") or []) < 2:
            continue
        stacked = H.polygon_interior_mask(tile.spec, s.rings)
        print(f"{inc.name:10s} t={s.t_s:.0f} oids={len(s_raw['oids'])} "
              f"GIS {s.acres:,.0f} ac | {len(s.rings)} stacked rings -> "
              f"rasterized interior {len(stacked) * cell_ac:,.0f} ac")
