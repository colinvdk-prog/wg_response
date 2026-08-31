"""RV2-CODE R1: corridor safety FAILS OPEN off-raster (safety.py ~:353).

    v = _raster_value_at(eroded_p10, p)
    if v is None or math.isinf(v):   # outside raster or UNREACHED
        continue                     # <- sample imposes NO bound

Off-raster ("I have no fire information here") and UNREACHED ("I computed this
cell and the fire never gets there") are collapsed into the same SAFE verdict.
A leg with zero in-raster samples gets latest_safe_depart_s = +inf.

This script:
  A. Direct runtime repro: lethal raster, leg outside it -> judged SAFE.
  B. Partial-coverage repro: the DANGEROUS realistic case — the leg starts
     inside the raster and exits it; the off-raster tail is unconstrained.
  C. Live-pipeline reachability: threat.py builds the domain as
     bbox(ignition + near clients) + 2 km (_DOMAIN_PAD_M). Safe zones, crew
     bases and road geometry are NOT in that bbox. Measure how much of a
     realistic egress leg is off-raster, and whether a leg can be 100% off.
  D. The bigger hole found while checking C: an incident with NO protected
     client inside the threat radius is SKIPPED ENTIRELY (threat.py ~:677) —
     it produces no ensemble, so it contributes NO raster to the corridor
     check at all. Its fire is invisible to every crew route.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import T0, cfg_default

from wgresponse import geo, safety, threat
from wgresponse.contracts import (BBox, GridSpec, LatLon, Raster,
                                  ResponseConfig, SafeZone, UNREACHED)

CFG = cfg_default()


def lethal_raster(center: LatLon, half_km: float, cell_m: float = 90.0,
                  arrival_s: float = T0):
    """A raster where EVERY cell is already burned at `arrival_s`."""
    half_m = half_km * 1000.0
    n = int(2 * half_m / cell_m)
    nw = geo.destination(geo.destination(center, 0.0, half_m), 270.0, half_m)
    spec = GridSpec(north_lat=nw.lat, west_lon=nw.lon, cell_m=cell_m,
                    nrows=n, ncols=n)
    return Raster(spec, [arrival_s] * (n * n))


def part_a():
    print("=" * 78)
    print("A. DIRECT REPRO — lethal raster, leg outside it")
    print("=" * 78)
    fire = LatLon(34.20, -117.60)
    ras = lethal_raster(fire, 3.0)            # 6x6 km of already-burned ground
    spec = ras.spec
    print(f"  raster: {spec.nrows}x{spec.ncols} @ {spec.cell_m} m, "
          f"EVERY cell arrival = T0 (already burning)")
    print(f"  raster bbox lat [{spec.north_lat - spec.nrows*spec.cell_m/geo.M_PER_DEG_LAT:.4f}, "
          f"{spec.north_lat:.4f}]")

    # leg INSIDE the lethal raster
    a_in = geo.destination(fire, 270.0, 2000.0)
    b_in = geo.destination(fire, 90.0, 2000.0)
    v = safety.corridor_safe(a_in, b_in, T0, 10.0, ras, CFG)
    o = safety.LegOracle(a_in, b_in, 10.0, ras, CFG)
    print(f"\n  leg INSIDE  : safe={v.safe}  samples_used={len(o.samples)}/{o.n_pts} "
          f"T*={'+inf' if math.isinf(o.latest_safe_depart_s) else f'{o.latest_safe_depart_s-T0:.0f}'}")

    # SAME leg translated 50 km east: entirely outside the raster
    a_out = geo.destination(a_in, 90.0, 50_000.0)
    b_out = geo.destination(b_in, 90.0, 50_000.0)
    v2 = safety.corridor_safe(a_out, b_out, T0, 10.0, ras, CFG)
    o2 = safety.LegOracle(a_out, b_out, 10.0, ras, CFG)
    print(f"  leg OUTSIDE : safe={v2.safe}  samples_used={len(o2.samples)}/{o2.n_pts} "
          f"T*={'+inf (UNBOUNDED)' if math.isinf(o2.latest_safe_depart_s) else o2.latest_safe_depart_s}")
    print(f"  worst_margin_h outside = {v2.worst_margin_h}  (UNREACHED sentinel)")
    print(f"\n  -> identical verdict shape as a genuinely fire-free leg: "
          f"safe={v2.safe}, reason={v2.reason!r}")
    print("  -> the caller CANNOT distinguish 'no fire here' from 'no data here'.")


def part_b():
    print()
    print("=" * 78)
    print("B. PARTIAL COVERAGE — the realistic case: leg exits the raster")
    print("=" * 78)
    fire = LatLon(34.20, -117.60)
    # raster covers 3 km around the fire and is lethal 40 min out
    ras = lethal_raster(fire, 3.0, arrival_s=T0 + 2400.0)
    a = geo.destination(fire, 270.0, 2500.0)       # inside, west edge
    b = geo.destination(fire, 90.0, 12000.0)       # 12 km east: exits at ~3 km
    o = safety.LegOracle(a, b, 10.0, ras, CFG)
    print(f"  leg length {geo.haversine_m(a,b)/1000:.1f} km, "
          f"sample step {CFG.corridor_sample_m} m")
    print(f"  samples total {o.n_pts}, samples WITH fire data {len(o.samples)} "
          f"({100*len(o.samples)/o.n_pts:.0f}%), SKIPPED {o.n_pts-len(o.samples)} "
          f"({100*(o.n_pts-len(o.samples))/o.n_pts:.0f}%)")
    print(f"  T* = {o.latest_safe_depart_s - T0:+.0f} s relative to T0 "
          f"(bounded only by the in-raster head of the leg)")
    print("  -> the 9 km of leg beyond the raster edge is checked against NOTHING.")


class _MockTerrain:
    def __init__(self):
        self.calls = []

    def tile(self, bbox, cell_m):
        from wgresponse.contracts import TerrainTile
        ctr = bbox.center()
        w_m = (bbox.east - bbox.west) * geo.m_per_deg_lon(ctr.lat)
        h_m = (bbox.north - bbox.south) * geo.M_PER_DEG_LAT
        self.calls.append((bbox, cell_m, w_m, h_m))
        nr = max(2, int(h_m / cell_m))
        nc = max(2, int(w_m / cell_m))
        spec = GridSpec(north_lat=bbox.north, west_lon=bbox.west, cell_m=cell_m,
                        nrows=nr, ncols=nc)
        n = nr * nc
        return TerrainTile(spec=spec, fuel=Raster(spec, [102.0] * n),
                           slope_deg=Raster(spec, [0.0] * n),
                           aspect_deg=Raster(spec, [-1.0] * n),
                           elev_m=Raster(spec, [100.0] * n), source="rv2")


def part_c():
    print()
    print("=" * 78)
    print("C. LIVE-PIPELINE REACHABILITY — how big is the raster, really?")
    print("=" * 78)
    cfg = cfg_default()
    print(f"  threat radius (client search) = "
          f"{min(cfg.horizon_h*3600.0*threat._PLAUSIBLE_MAX_ROS_MPS, threat._MAX_THREAT_RADIUS_M)/1000:.0f} km")
    print(f"  _DOMAIN_PAD_M                 = {threat._DOMAIN_PAD_M/1000:.1f} km")
    print("  domain = bbox(ignition geometry + NEAR CLIENTS) + pad")
    print("  NOT in that bbox: safe zones, crew bases/staging, road geometry,")
    print("                    non-client ground the fire will actually reach.")

    fire = LatLon(34.20, -117.60)
    for label, client_bearing, client_km in (
            ("single client 2 km E of fire", 90.0, 2.0),
            ("single client 8 km E of fire", 90.0, 8.0)):
        client = geo.destination(fire, client_bearing, client_km * 1000.0)
        dom = geo.bbox_around([fire, client], threat._DOMAIN_PAD_M)
        w_km = (dom.east - dom.west) * geo.m_per_deg_lon(dom.center().lat) / 1000
        h_km = (dom.north - dom.south) * geo.M_PER_DEG_LAT / 1000
        print(f"\n  {label}: domain {w_km:.1f} x {h_km:.1f} km")
        # Where do the sim's own safe zones sit?  5-12 km from ignition.
        for zb, zkm, zname in ((270.0, 5.0, "Z-WEST 5 km"),
                               (180.0, 7.0, "Z-SOUTH 7 km"),
                               (140.0, 12.0, "Z-SE 12 km")):
            z = geo.destination(fire, zb, zkm * 1000.0)
            inside = dom.contains(z)
            # fraction of the client->zone egress leg that is off-raster
            pts = safety._sample_polyline(client, z, cfg.corridor_sample_m)
            off = sum(1 for p in pts if not dom.contains(p))
            print(f"    egress {zname:12s}: zone in domain={str(inside):5s}  "
                  f"leg samples off-domain {off}/{len(pts)} "
                  f"({100*off/len(pts):5.1f}%)"
                  f"{'   <-- 100% OFF: T* = +inf' if off == len(pts) else ''}")

        # crew base / staging outside the domain -> INGRESS leg also exits
        base = geo.destination(fire, 300.0, 25_000.0)
        pts = safety._sample_polyline(base, client, cfg.corridor_sample_m)
        off = sum(1 for p in pts if not dom.contains(p))
        print(f"    ingress from base 25 km NW: off-domain {off}/{len(pts)} "
              f"({100*off/len(pts):5.1f}%)")

    # How far can the fire actually run vs how far the domain extends?
    print(f"\n  Fire reach vs domain: at the engine's own _PLAUSIBLE_MAX_ROS_MPS "
          f"({threat._PLAUSIBLE_MAX_ROS_MPS} m/s)")
    for h in (6, 12, 24, 72, 96):
        print(f"    {h:3d} h -> {threat._PLAUSIBLE_MAX_ROS_MPS*3600*h/1000:6.1f} km "
              f"of possible run;  domain pad beyond the client set is "
              f"{threat._DOMAIN_PAD_M/1000:.1f} km")


def part_d():
    print()
    print("=" * 78)
    print("D. NO-CLIENT INCIDENTS PRODUCE NO RASTER AT ALL (threat.py ~:677)")
    print("=" * 78)
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "wgresponse", "threat.py")).read()
    i = src.index("if not near:")
    print("  " + "\n  ".join(src[i:i + 260].splitlines()[:6]))
    print("\n  -> no ensemble entry, so dispatch's `rasters` list has no entry for")
    print("     this incident, so corridor_ok() never sees its fire. A crew can be")
    print("     routed straight through a fire that has no WG client near it.")
    print("     (This is NOT the off-raster skip — it is a missing raster entirely,")
    print("      and it fails open through the same +inf T*.)")


if __name__ == "__main__":
    part_a()
    part_b()
    part_c()
    part_d()
