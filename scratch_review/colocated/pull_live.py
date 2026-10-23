"""Record one live WFIGS + FIRIS + CAL FIRE pull over the CONUS AOI (the live
watcher's feed stack minus FIRMS) for the co-located fusion study
(contracts SAFETY SEMANTICS 24).

Every raw HTTP response is written verbatim to
out/colocated/raw_<UTC stamp>.json ({"pulled_at_s", "bbox", "responses":
[{"url", "status", "body"}]}) so count_live.py and make_fixture.py replay the
exact pull offline. Keyless public GETs only; nothing else is touched.

    py -3 scratch_review/colocated/pull_live.py
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from wgresponse import feeds  # noqa: E402
from wgresponse.contracts import BBox  # noqa: E402

OUT = os.path.join(ROOT, "out", "colocated")
CONUS = BBox(west=-125.0, south=24.0, east=-66.0, north=50.0)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    recorded = []

    def rec_get(url, headers=None):
        status, body = feeds._default_http_get(url, headers, timeout_s=90.0)
        recorded.append({"url": url, "status": status,
                         "body": body.decode("utf-8", "replace")})
        return status, body

    t0 = time.time()
    counts = {}
    for f in (feeds.WfigsFeed(http_get=rec_get), feeds.FirisFeed(http_get=rec_get),
              feeds.CalFireFeed(http_get=rec_get)):
        batch = f.fetch_active(CONUS)
        counts[type(f).__name__] = [len(batch), f.last_fetch_ok]
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(t0))
    path = os.path.join(OUT, f"raw_{stamp}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"pulled_at_s": t0, "bbox": [CONUS.west, CONUS.south,
                                                CONUS.east, CONUS.north],
                   "counts": counts, "responses": recorded}, fh)
    print(path, counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
