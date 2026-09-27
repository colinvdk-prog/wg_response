"""The corridor-veto reach of the LARGEST live fires on the recorded snapshot
(2026-09-27; NOT product code). None of them builds a domain today (no demo
home within 60 km), so the fail-closed bound never sees them; if one did,
its veto radius for a pass NOW is at most
    R <= (age of the seeding time + 56 min) * 1.7 m/s / 0.99
(reached when the modeled front hits its domain edge right after seeding —
Willow's anchored front did in 0.9 h; the far-side domain pad is 2 km).
The seeding time is the polygon's own time (legacy) or the confirming
record's (--confirmed-perimeters); a removed fire has no domain at all."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, WT)
sys.path.insert(0, HERE)

import feedsnap  # noqa: E402
from wgresponse import feeds, threat  # noqa: E402
from wgresponse.contracts import BBox  # noqa: E402

CONUS = BBox(west=-125.0, south=24.0, east=-66.0, north=50.0)
now = float(feedsnap.ReplayHttp().manifest["recorded_at_s"])
batches = [f(http_get=feedsnap.ReplayHttp()).fetch_active(CONUS)
           for f in (feeds.WfigsFeed, feeds.FirisFeed, feeds.CalFireFeed)]
fresh = [i for i in feeds.fuse_incidents(batches)
         if i.active and now - i.updated_at_s <= 168 * 3600.0 and i.perimeter]
fresh.sort(key=lambda i: -(i.acreage or 0))
K = 1.7 * 3600.0 / 0.99 / 1000.0          # km per hour of age
print(f"{'fire':<22} {'acres':>9} {'cont%':>5} {'poly_h':>7} {'rec_h':>6} "
      f"{'legacy_R_km':>11} {'new_R_km':>9}  new rule")
for inc in fresh[:12]:
    m = inc.meta
    poly_h = (now - m.get("perimeter_at_s", inc.updated_at_s)) / 3600.0
    rep_at = m.get("reported_at_s")
    rec_h = None if rep_at is None else (now - rep_at) / 3600.0
    signals, contra = threat.contained_signals(inc, now)
    legacy = (poly_h + 56 / 60.0) * K
    if signals and not contra:
        rule, new = "REMOVED (no domain)", 0.0
    else:
        rep, poly = m.get("reported_acres"), m.get("perimeter_acres")
        newest = m.get("perimeter_newest_at_s")
        pat = m.get("perimeter_at_s", inc.updated_at_s)
        ok = (rep_at is not None and rep_at > pat and rep is not None and poly
              and abs(rep - poly) <= 0.02 * poly
              and not (newest is not None and newest > pat + 1.0))
        if ok:
            rule, new = "anchored at the record", (rec_h + 56 / 60.0) * K
        else:
            rule, new = "legacy (not confirmed)", legacy
    print(f"{inc.name[:22]:<22} {inc.acreage or 0:>9,.0f} "
          f"{inc.containment_pct if inc.containment_pct is not None else -1:>5.0f} "
          f"{poly_h:>7.1f} {rec_h if rec_h is not None else -1:>6.1f} "
          f"{legacy:>11,.0f} {new:>9,.0f}  {rule}")
