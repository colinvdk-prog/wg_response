"""Lane L2 (build): catalog the S3 terrain mirror READ-ONLY.

For every decoded tile (.wgtile) in Lane C's listing, a ranged GET of its
first 400 bytes gives the ascii header (nrows ncols cell_m north west) and
its source line. The domain bbox is rebuilt (south/east snapped to a
0.05 / 0.01 / 0.001-degree grid) and VERIFIED by recomputing the tile's own
cache key (sha256 of layers|bbox|cell) — only verified rows carry a bbox.
Each verified bbox then names its zip / canopy / canopy-zip keys, looked up
in the listing. Writes catalog.json beside this script.

Read-only: s3 GetObject (with a Range header) under watch-conus/terrain/.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW = r"C:\Users\colin\wg_response\scratch_review\fire_alert_latency_review_2026-11-18"
LISTING = os.path.join(REVIEW, "C_alerting", "s3_terrain_listing.txt")
BUCKET = "wg-response-state-296920174396"
PREFIX = "watch-conus/terrain/"
LAYERS = "LF2024_FBFM40;LF2020_SlpD;LF2020_Asp;LF2020_Elev"
CANOPY = "LF2024_CBH;LF2024_CBD;LF2024_CC;LF2024_CH"
M_LAT = 111_320.0


def key_tile(layers, w, s, e, n, cell):
    k = f"{layers}|{w:.6f},{s:.6f},{e:.6f},{n:.6f}|{cell:.1f}"
    return "lfps_" + hashlib.sha256(k.encode()).hexdigest()[:16]


def key_zip(layers, w, s, e, n):
    k = f"{layers}|{w:.6f},{s:.6f},{e:.6f},{n:.6f}"
    return "lfps_" + hashlib.sha256(k.encode()).hexdigest()[:16] + ".zip"


def m_lon(lat):
    return M_LAT * max(0.05, math.cos(math.radians(lat)))


def main():
    import boto3
    s3 = boto3.client("s3")
    names = {}
    with open(LISTING, encoding="utf-8") as f:
        for ln in f:
            p = ln.split()
            if len(p) == 4:
                names[p[3]] = int(p[2])
    tiles = sorted(n for n in names if n.endswith(".wgtile"))

    def head(name):
        r = s3.get_object(Bucket=BUCKET, Key=PREFIX + name,
                          Range="bytes=0-399")
        return name, r["Body"].read().decode("utf-8", "replace")

    rows = []
    with cf.ThreadPoolExecutor(16) as ex:
        for name, txt in ex.map(head, tiles):
            lines = txt.split("\n")
            h = lines[0].split()
            nrows, ncols = int(h[0]), int(h[1])
            cell, north, west = float(h[2]), float(h[3]), float(h[4])
            src = lines[1] if len(lines) > 1 else ""
            south_raw = north - nrows * cell / M_LAT
            found = None
            for step in (0.05, 0.01, 0.001):
                for ds in range(-3, 4):
                    s = round(round(south_raw / step) * step + ds * step, 6)
                    lat_c = (north + s) / 2.0
                    e_raw = west + ncols * cell / m_lon(lat_c)
                    for de in range(-3, 4):
                        e = round(round(e_raw / step) * step + de * step, 6)
                        if key_tile(LAYERS, west, s, e, north, cell) + \
                                ".wgtile" == name:
                            found = (s, e)
                            break
                    if found:
                        break
                if found:
                    break
            row = {"tile": name, "nrows": nrows, "ncols": ncols,
                   "cell_m": cell, "north": north, "west": west,
                   "source": src[:80], "bytes": names[name]}
            if found:
                s, e = found
                row.update(south=s, east=e, verified=True)
                z = key_zip(LAYERS, west, s, e, north)
                cz = key_zip(CANOPY, west, s, e, north)
                can = key_tile(CANOPY, west, s, e, north, cell) + ".wgcanopy"
                row.update(zip=z if z in names else None,
                           zip_bytes=names.get(z),
                           canopy=can if can in names else None,
                           canopy_zip=cz if cz in names else None,
                           canopy_zip_bytes=names.get(cz))
            else:
                row.update(south=round(south_raw, 6), verified=False)
            rows.append(row)
    out = os.path.join(HERE, "catalog.json")
    with open(out, "w") as f:
        json.dump(rows, f, indent=0)
    v = [r for r in rows if r["verified"]]
    print(f"{len(rows)} tiles, {len(v)} verified; with zip "
          f"{sum(1 for r in v if r['zip'])}, with canopy "
          f"{sum(1 for r in v if r['canopy'])}, with canopy zip "
          f"{sum(1 for r in v if r['canopy_zip'])}")


if __name__ == "__main__":
    sys.exit(main())
