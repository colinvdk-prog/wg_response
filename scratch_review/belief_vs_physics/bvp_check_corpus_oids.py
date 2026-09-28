"""Read-only check: do the harness corpora's snapshots union SEVERAL
OBJECTIDs under one capture time (tools/hindcast_harness._fetch_incident),
and what does that do to the snapshot area? fire_belief.rings_area_m2
counts equal-area stacked copies once EACH (ratios ~2 here), while the
spread / scoring rasterizers CANCEL them (bvp_check_stacked_rings.py).
Feeds the report's 'noticed along the way' list; nothing is changed."""
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO)
from wgresponse.contracts import LatLon  # noqa: E402
from wgresponse.fire_belief import rings_area_m2  # noqa: E402

ACRE = 4046.8564224
out = []
for d in ("tools/hindcast_data", "scratch_review/hindcast_oos_data",
          "scratch_review/hindcast_aug_data"):
    inc_dir = os.path.join(_REPO, d, "incidents")
    if not os.path.isdir(inc_dir):
        continue
    for fn in sorted(os.listdir(inc_dir)):
        inc = json.load(open(os.path.join(inc_dir, fn)))
        for s in inc["snapshots"]:
            n = len(s.get("oids") or [])
            if n > 1:
                rings = [[LatLon(v[0], v[1]) for v in r] for r in s["rings"]]
                a = rings_area_m2(rings) / ACRE
                out.append((d, inc["name"], s["t_s"], n, s.get("acres"), a))
tot = sum(1 for _ in out)
print(f"snapshots with > 1 OBJECTID unioned: {tot}")
for d, name, t, n, ac, a in out:
    print(f"  {d:34s} {name:22s} t={t:.0f} oids={n} GISacres={ac:,.0f} "
          f"rings_area_m2={a:,.0f} ac  ratio={a / ac if ac else 0:.2f}")
