"""Snapshot statistics for the 2026-09-27 doc section (NOT product code):
over the recorded national feeds, fused, after the staleness gate — how many
incidents carry a polygon, how many of those polygons a fresher record
re-confirms at the same size (the anchor), how many records report MORE
acres than their polygon (growing-fire guard), how many fused groups hold a
NEWER polygon than the one they carry, and the containment signals."""
import collections
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
fused = feeds.fuse_incidents(batches)
fresh = [i for i in fused if i.active and now - i.updated_at_s <= 168 * 3600.0]
c = collections.Counter()
tol = 0.02
for inc in fresh:
    signals, contra = threat.contained_signals(inc, now)
    if signals and not contra:
        c["declared_contained"] += 1
    if contra and signals:
        c["contradicted"] += 1
    if not inc.perimeter:
        continue
    c["with_perimeter"] += 1
    m = inc.meta
    pat = m.get("perimeter_at_s", inc.updated_at_s)
    rep, rep_at, poly = (m.get("reported_acres"), m.get("reported_at_s"),
                         m.get("perimeter_acres"))
    newest = m.get("perimeter_newest_at_s")
    if newest is not None and newest > pat + 1.0:
        c["newer_polygon_elsewhere"] += 1
    if rep_at is None or min(rep_at, now) <= min(pat, now):
        c["record_not_fresher"] += 1
        continue
    c["record_fresher"] += 1
    if (now - pat) - (now - rep_at) > 24 * 3600:
        c["record_fresher_by_24h+"] += 1
    if rep is None or not poly:
        c["no_size"] += 1
        continue
    rel = (rep - poly) / poly
    if rel > tol:
        c["growing_guard"] += 1
    elif rel < -tol:
        c["smaller_disagree"] += 1
    elif newest is not None and newest > pat + 1.0:
        pass
    else:
        c["anchorable"] += 1
        c["anchor_hours_saved_sum"] += int((rep_at - pat) / 3600)
print("fused", len(fused), "fresh", len(fresh))
print(dict(c))
