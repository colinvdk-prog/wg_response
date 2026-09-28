"""Feed-sanity study (2026-09-27) -- STEP 1: record the live fire feeds.

READ-ONLY. Records exactly what the watch daemon's live feed stack reads
(wgresponse.cli._live_feed_stack: WfigsFeed + FirisFeed + CalFireFeed over the
deployed CONUS AOI, data/aoi_conus.json) by running the REAL adapters through
a recording http_get, plus two attribute-only side queries (outFields=*,
returnGeometry=false) on the WFIGS perimeter and incident-point layers for the
fields the adapters do not request (poly_PolygonDateTime, poly_CreateDate,
CreatedOnDateTime_dt, ...). Every request is a public, keyless GET.

Output: out/feed_sanity/rec_<UTC stamp>/ (gitignored):
    manifest.json   {"t_s", "bbox", "requests": [{url, status, file, bytes,
                     t_s, role}]}  -- role: adapter | side
    NNN_<hash>.json the raw response bodies, byte for byte

fs_measure.py replays a recording through the same adapters (an http_get that
serves the recorded bodies by URL), so the measurement exercises the live code
path on frozen data.

Usage (repo root):
    py -3 scratch_review/feed_sanity/fs_record.py            # one recording
    py -3 scratch_review/feed_sanity/fs_record.py --tag late # named suffix
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO)

from wgresponse import feeds  # noqa: E402
from wgresponse.contracts import BBox  # noqa: E402

OUT_ROOT = os.path.join(_REPO, "out", "feed_sanity")
MAX_REQUESTS = 40          # hard budget per invocation (the adapters need ~5)


def conus_bbox() -> BBox:
    with open(os.path.join(_REPO, "data", "aoi_conus.json"), encoding="utf-8") as fh:
        aoi = json.load(fh)
    zones = [z["bbox"] for z in aoi.get("zones") or [] if isinstance(z, dict)]
    w = min(z[0] for z in zones)
    s = min(z[1] for z in zones)
    e = max(z[2] for z in zones)
    n = max(z[3] for z in zones)
    return BBox(w, s, e, n)


class Recorder:
    """http_get(url, headers) -> (status, body) that performs the GET and
    stores the body. HTTP errors are recorded (status + body) and returned,
    never raised, so the adapters see exactly what a live cycle would."""

    def __init__(self, outdir: str) -> None:
        self.outdir = outdir
        self.rows: list = []
        self.role = "adapter"

    def __call__(self, url: str, headers: dict) -> "tuple[int, bytes]":
        if len(self.rows) >= MAX_REQUESTS:
            raise RuntimeError(f"request budget {MAX_REQUESTS} exhausted")
        t = time.time()
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=60.0) as resp:  # nosec: fixed feed URLs
                status, body = resp.getcode(), resp.read()
        except urllib.error.HTTPError as exc:
            status, body = exc.code, exc.read() or b""
        name = f"{len(self.rows):03d}_{hashlib.sha1(url.encode()).hexdigest()[:10]}.json"
        with open(os.path.join(self.outdir, name), "wb") as fh:
            fh.write(body)
        self.rows.append({"url": url, "status": status, "file": name,
                          "bytes": len(body), "t_s": t, "role": self.role})
        print(f"  {status} {len(body):>10,} B  {self.role:7s} {url[:110]}")
        return status, body


def side_query(rec: Recorder, layer_url: str, bbox: BBox) -> None:
    """Attribute-only query (every field, no geometry) over the AOI."""
    params = {"where": "1=1", "outFields": "*", "f": "json",
              "returnGeometry": "false", "outSR": "4326"}
    params.update(feeds._envelope_params(bbox))
    offset = 0
    for _page in range(5):
        p = dict(params)
        if offset:
            p["resultOffset"] = str(offset)
        status, body = rec(layer_url + "?" + urllib.parse.urlencode(p),
                           {"Accept": "application/json"})
        if status != 200:
            return
        payload = json.loads(body.decode("utf-8"))
        page = payload.get("features") or []
        if not payload.get("exceededTransferLimit") or not page:
            return
        offset += len(page)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", default="", help="suffix for the recording dir")
    args = ap.parse_args(argv)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    outdir = os.path.join(OUT_ROOT, f"rec_{stamp}" + (f"_{args.tag}" if args.tag else ""))
    os.makedirs(outdir, exist_ok=True)
    bbox = conus_bbox()
    rec = Recorder(outdir)
    t0 = time.time()
    print(f"recording to {outdir} (AOI {bbox})")
    counts = {}
    for cls in (feeds.WfigsFeed, feeds.FirisFeed, feeds.CalFireFeed):
        adapter = cls(http_get=rec)
        incs = adapter.fetch_active(bbox)
        counts[cls.__name__] = {"incidents": len(incs),
                                "last_fetch_ok": adapter.last_fetch_ok}
    rec.role = "side"
    side_query(rec, feeds.WfigsFeed.PERIMETERS_URL, bbox)
    side_query(rec, feeds.WfigsFeed.POINTS_URL, bbox)
    manifest = {"t_s": t0, "t_utc": stamp,
                "bbox": [bbox.west, bbox.south, bbox.east, bbox.north],
                "adapters": counts, "requests": rec.rows}
    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
