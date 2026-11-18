"""BUILD-3 (feeds), item 7: exercise the sentinel's "new record" queries on
the LIVE layers (WFIGS `IrwinID IN (...)`, FIRIS objectIds with a
generalized polygon): baseline poll, then forget three seen ids per ArcGIS
source so the next poll treats them as new and asks for their details.
Read-only GETs. Prints the records it built (names, places, nearest-home
distance against one dummy home in central California)."""
import os
import sys

sys.path.insert(0, os.getcwd())
from wgresponse import sentinel  # noqa: E402
from wgresponse.contracts import BBox, LatLon, ResponseConfig  # noqa: E402

AOI = BBox(west=-125.0, south=24.0, east=-66.0, north=50.0)
cfg = ResponseConfig(new_fire_sentinel_s=60.0)
s = sentinel.NewFireSentinel(cfg, clients_fn=lambda: [("dummy",
                                                       LatLon(37.0, -120.0))],
                             aoi=AOI)
s.poll_once()
for src in ("wfigs", "firis"):
    for k in list(s._seen[src])[:3]:
        del s._seen[src][k]
s.poll_once()
st = s.status()
for src, v in st["sources"].items():
    print(src, {k: v["last"].get(k) for k in ("ok", "new", "s", "requests",
                                              "bytes", "error")})
for r in st["records"]:
    print(r["source"], r["id"], repr(r["name"])[:40], r["lat"], r["lon"],
          "nearest", r["nearest_km"], "km", "created->seen",
          r.get("created_to_seen_s"))
