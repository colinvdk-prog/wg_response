"""Live LFPS terrain smoke test (READ-ONLY GETs to lfps.usgs.gov, keyless).

Runs LfpsTerrain end-to-end ONCE for a ~10x10 km Malibu AOI: submit, poll,
download, decode; verifies georegistration against known coordinates
(ocean -> FBFM 98 water; inland Santa Monica Mtns -> burnable code) and that
spread.compute_arrival accepts the tile. Records wall-clock per stage.

Cache dir is scratch_review/lfps_cache so a re-run is offline.
"""
import collections
import sys
import time

sys.path.insert(0, r"C:\Users\colin\wg_response")

from wgresponse.contracts import BBox, LatLon, ResponseConfig
from wgresponse.terrain import LfpsTerrain
from wgresponse.weather import MockWeather
from wgresponse import spread

# ~10x10 km AOI over Malibu: coastline crosses the box (ocean S, mountains N)
AOI = BBox(west=-118.75, south=33.99, east=-118.64, north=34.08)
CELL_M = 90.0

OCEAN_PT = LatLon(34.000, -118.700)    # Pacific, ~3.5 km off Malibu
INLAND_PT = LatLon(34.070, -118.700)   # Santa Monica Mtns interior

CACHE = r"C:\Users\colin\wg_response\scratch_review\lfps_cache"


class TimedHttp:
    """Wrap the default http_get to log per-request wall-clock."""

    def __init__(self):
        from wgresponse.terrain import _default_http_get
        self._inner = _default_http_get
        self.calls = []

    def __call__(self, url, headers):
        t0 = time.perf_counter()
        status, body = self._inner(url, headers)
        dt = time.perf_counter() - t0
        kind = ("submit" if "job/submit" in url
                else "status" if "job/status" in url else "download")
        self.calls.append((kind, dt, len(body), status))
        if kind != "status" or len(self.calls) < 4 or dt > 2.0:
            print(f"    http {kind:<8} {dt:6.1f}s  status={status} bytes={len(body):,}")
        return status, body


def fuel_at(tile, pt):
    spec = tile.spec
    import math
    m_lon = 111_320.0 * max(0.05, math.cos(math.radians((AOI.north + AOI.south) / 2)))
    r = int((spec.north_lat - pt.lat) * 111_320.0 / spec.cell_m)
    c = int((pt.lon - spec.west_lon) * m_lon / spec.cell_m)
    r = min(max(r, 0), spec.nrows - 1)
    c = min(max(c, 0), spec.ncols - 1)
    return tile.fuel.get(r, c), tile.elev_m.get(r, c), tile.slope_deg.get(r, c)


def main():
    http = TimedHttp()
    terr = LfpsTerrain(cache_dir=CACHE, http_get=http,
                       email="ops@thewildfireguardian.com")
    print(f"[LFPS] AOI {AOI.west},{AOI.south} -> {AOI.east},{AOI.north} @ {CELL_M} m")
    t0 = time.perf_counter()
    try:
        tile = terr.tile(AOI, CELL_M)
    except Exception as e:
        print(f"[LFPS] FAILED after {time.perf_counter()-t0:.1f}s: "
              f"{type(e).__name__}: {e}")
        raise
    total = time.perf_counter() - t0
    n_status = sum(1 for k, *_ in http.calls if k == "status")
    print(f"[LFPS] tile OK in {total:.1f}s total "
          f"({n_status} status polls)  grid={tile.spec.nrows}x{tile.spec.ncols} "
          f"cell={tile.spec.cell_m}m  source={tile.source}")

    # fuel code census
    counts = collections.Counter(int(v) for v in tile.fuel.data)
    top = counts.most_common(8)
    print(f"[LFPS] fuel code census (top 8): {top}")

    of, oe, osl = fuel_at(tile, OCEAN_PT)
    inf_, ie, isl = fuel_at(tile, INLAND_PT)
    print(f"[GEOREG] ocean  ({OCEAN_PT.lat},{OCEAN_PT.lon}): fuel={of:.0f} "
          f"elev={oe:.0f}m slope={osl:.1f}  -> expect 98 (water), elev ~0")
    print(f"[GEOREG] inland ({INLAND_PT.lat},{INLAND_PT.lon}): fuel={inf_:.0f} "
          f"elev={ie:.0f}m slope={isl:.1f} -> expect burnable (100-204), elev >100 m")
    georeg_ok = (of == 98.0) and (100.0 <= inf_ <= 204.0) and (ie > 50.0)
    print(f"[GEOREG] {'PASS' if georeg_ok else 'FAIL'}")

    # spread.compute_arrival accepts the tile?
    cfg = ResponseConfig(horizon_h=12.0)
    wx = MockWeather("smoke", base_wind_mps=9.0, dir_deg=350.0, rh_pct=15.0,
                     t0_s=1_780_000_000.0)
    ign = [LatLon(34.075, -118.72)]   # NW corner, inland
    fc = wx.forecast(ign[0], cfg.horizon_h)
    t0 = time.perf_counter()
    arr = spread.compute_arrival(tile, ign, fc, 1_780_000_000.0, cfg)
    dt = time.perf_counter() - t0
    finite = sum(1 for v in arr.data if v != float("inf"))
    print(f"[SPREAD] compute_arrival on live tile: {dt:.1f}s, "
          f"{finite}/{len(arr.data)} cells reached in {cfg.horizon_h:.0f}h "
          f"-> {'PASS' if finite > 10 else 'SUSPICIOUS (almost nothing burns)'}")


if __name__ == "__main__":
    main()
