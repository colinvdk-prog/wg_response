"""ADVERSARIAL VERIFIER (Session 2) — attack #2: perimeter seeding (ticket A5).

Claims under attack (contracts compute_arrival perimeter clause; verifier brief):
  2a. Client inside the perimeter -> IMPACTED path: eta hours_out ~ 0 (never
      negative), interior arrival == perimeter_at_s EXACTLY (no re-burn), in
      every member and every quantile.
  2b. Perimeter + hotspot union seeding (hotspots newer than the perimeter
      still seed at t0).
  2c. Stale perimeter advances the front (perimeter_at_s in the past -> the
      exterior front is AHEAD of a seeded-at-now run).
  2d. Degenerate polygons (< 3 verts, all-collinear, self-touching ring,
      sub-cell sliver / antimeridian-thin) never crash and fall back safely
      (vertex seeding at minimum).
  2e. Perimeter arrival in the PAST yields hours_out 0 (not negative) and the
      alert ladder reaches IMPACTED which stays terminal.

Run from repo root:
    py -3 -m pytest scratch_review/attack_s2_perimeter.py -v
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo, spread
from wgresponse.alerts import AlertEngine
from wgresponse.contracts import (AlertState, GridSpec, LatLon, Raster,
                                  ResponseConfig, TerrainTile,
                                  ThreatAssessment, UNREACHED)
from wgresponse.weather import MockWeather

T0 = 1_780_000_000.0
PERIM_T = T0 - 4 * 3600.0            # 4 h stale perimeter timestamp


def _tile(nrows=60, ncols=110, cell_m=100.0, north=34.62, west=-119.30):
    spec = GridSpec(north_lat=north, west_lon=west, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)
    n = nrows * ncols
    return TerrainTile(
        spec=spec,
        fuel=Raster(spec, [102.0] * n),
        slope_deg=Raster(spec, [0.0] * n),
        aspect_deg=Raster(spec, [-1.0] * n),
        elev_m=Raster(spec, [100.0] * n),
        source="attack-s2-perim",
    )


def _wx(loc, wind=5.0, wdir=270.0):
    return MockWeather("attack-s2-perim", base_wind_mps=wind, dir_deg=wdir,
                       rh_pct=25.0, temp_c=30.0, t0_s=T0).forecast(loc, 110)


def _square(center, half_m, n_per_side=12):
    """Square ring around center, closed implicitly (no repeated last vertex)."""
    nw = geo.destination(geo.destination(center, 0.0, half_m), 270.0, half_m)
    side = 2.0 * half_m
    pts = []
    for k in range(n_per_side):
        pts.append(geo.destination(nw, 90.0, k * side / n_per_side))
    ne = geo.destination(nw, 90.0, side)
    for k in range(n_per_side):
        pts.append(geo.destination(ne, 180.0, k * side / n_per_side))
    se = geo.destination(ne, 180.0, side)
    for k in range(n_per_side):
        pts.append(geo.destination(se, 270.0, k * side / n_per_side))
    sw = geo.destination(se, 270.0, side)
    for k in range(n_per_side):
        pts.append(geo.destination(sw, 0.0, k * side / n_per_side))
    return pts


CFG = ResponseConfig(ensemble_n=4, horizon_h=96.0)


# ---------------------------------------------------------------------------
# 2a — interior == perimeter_at_s exactly, in every member/quantile; no re-burn
# ---------------------------------------------------------------------------

def test_interior_exactly_perimeter_time_every_member_no_reburn():
    tile = _tile()
    center = geo.rc_to_latlon(tile.spec, 30, 30)
    ring = _square(center, 1200.0)
    ens = spread.ensemble_arrival(tile, [], _wx(center), T0, CFG,
                                  incident_id="perim-A", perimeter=[ring],
                                  perimeter_at_s=PERIM_T)
    mask = spread._perimeter_interior_cells(tile.spec, [ring])
    assert len(mask) > 50, "probe degenerate: interior did not rasterize"
    for k, member in enumerate(ens.members):
        for idx in mask:
            assert member.data[idx] == PERIM_T, (
                f"member {k} cell {idx}: interior arrival "
                f"{member.data[idx]!r} != perimeter_at_s {PERIM_T!r} "
                f"(re-burn or missed seed)")
    for name, q in (("p10", ens.p10), ("p50", ens.p50), ("p90", ens.p90)):
        for idx in mask:
            assert q.data[idx] == PERIM_T, (name, idx, q.data[idx])
    # interior client ETA: hours_out clamps to 0, never negative
    eta = spread.eta_at(ens, center)
    assert eta.eta_p10_s == PERIM_T == eta.eta_p50_s
    h10, h50, _ = eta.hours_out(T0)
    assert h10 == 0.0 and h50 == 0.0


def test_exterior_seed_cannot_relax_interior_below_perimeter_time():
    """A hotspot seeded at t0 EARLIER than perimeter_at_s (feed clock skew:
    updated_at_s ahead of now) must not rewrite the interior: contracts pin
    interior cells as FINAL at perimeter_at_s."""
    tile = _tile()
    center = geo.rc_to_latlon(tile.spec, 30, 30)
    ring = _square(center, 1000.0)
    future_t = T0 + 2 * 3600.0         # perimeter stamped 2 h in the future
    hotspot = geo.destination(center, 270.0, 2500.0)   # west, outside ring
    arr = spread.compute_arrival(tile, [hotspot], _wx(center), T0, CFG,
                                 perimeter=[ring], perimeter_at_s=future_t)
    mask = spread._perimeter_interior_cells(tile.spec, [ring])
    assert mask
    for idx in mask:
        assert arr.data[idx] == future_t, (
            "interior relaxed below perimeter_at_s by an exterior seed "
            "(contract: interior cells are FINAL)")


# ---------------------------------------------------------------------------
# 2b — union seeding: hotspots still seed at t0 alongside a perimeter
# ---------------------------------------------------------------------------

def test_perimeter_and_hotspot_union_seeding():
    tile = _tile()
    center = geo.rc_to_latlon(tile.spec, 30, 25)
    ring = _square(center, 900.0)
    hotspot = geo.destination(center, 90.0, 5000.0)    # 5 km downwind, outside
    wx = _wx(center)
    with_hs = spread.compute_arrival(tile, [hotspot], wx, T0, CFG,
                                     perimeter=[ring], perimeter_at_s=PERIM_T)
    without_hs = spread.compute_arrival(tile, [], wx, T0, CFG,
                                        perimeter=[ring],
                                        perimeter_at_s=PERIM_T)
    rc = geo.latlon_to_rc(tile.spec, hotspot)
    assert rc is not None
    hs_idx = rc[0] * tile.spec.ncols + rc[1]
    assert with_hs.data[hs_idx] == T0, "hotspot did not seed at t0"
    assert without_hs.data[hs_idx] > T0 + 600.0, (
        "control broken: front reached the hotspot cell essentially "
        "immediately without the hotspot seed")
    # union = elementwise min: nothing in the union run may be LATER than the
    # perimeter-only run, and cells near the hotspot must be strictly earlier
    later = sum(1 for a, b in zip(with_hs.data, without_hs.data)
                if a > b + 1e-6)
    assert later == 0, f"{later} cells got LATER when a hotspot seed was added"
    earlier = sum(1 for a, b in zip(with_hs.data, without_hs.data)
                  if b - a > 1.0 or (math.isinf(b) and math.isfinite(a)))
    assert earlier > 10, "hotspot seed had no effect (union seeding broken)"


# ---------------------------------------------------------------------------
# 2c — stale perimeter advances the front
# ---------------------------------------------------------------------------

def test_stale_perimeter_front_is_ahead():
    tile = _tile()
    center = geo.rc_to_latlon(tile.spec, 30, 25)
    ring = _square(center, 900.0)
    wx = _wx(center)
    stale = spread.compute_arrival(tile, [], wx, T0, CFG, perimeter=[ring],
                                   perimeter_at_s=T0 - 6 * 3600.0)
    fresh = spread.compute_arrival(tile, [], wx, T0, CFG, perimeter=[ring],
                                   perimeter_at_s=T0)
    probe = geo.destination(center, 90.0, 4000.0)      # downwind exterior
    v_stale = geo.raster_sample(stale, probe)
    v_fresh = geo.raster_sample(fresh, probe)
    assert math.isfinite(v_stale) and math.isfinite(v_fresh)
    assert v_stale < v_fresh - 3600.0, (
        f"6 h stale perimeter advanced the downwind front only "
        f"{(v_fresh - v_stale) / 3600.0:.2f} h ahead of a fresh one")
    # and the stale front must never be LATER anywhere
    later = sum(1 for a, b in zip(stale.data, fresh.data) if a > b + 1e-6)
    assert later == 0, f"{later} cells later under the STALE perimeter"


# ---------------------------------------------------------------------------
# 2d — degenerate polygons: no crash, safe fallback
# ---------------------------------------------------------------------------

def _run_perim(tile, rings, wx, ignition=()):
    return spread.compute_arrival(tile, list(ignition), wx, T0, CFG,
                                  perimeter=rings, perimeter_at_s=PERIM_T)


def test_degenerate_polygons_never_crash_and_seed_vertices():
    tile = _tile()
    center = geo.rc_to_latlon(tile.spec, 30, 40)
    wx = _wx(center)
    east = lambda m: geo.destination(center, 90.0, m)
    north = lambda m: geo.destination(center, 0.0, m)

    cases = {
        "two_verts": [[center, east(800.0)]],
        "one_vert": [[center]],
        "collinear": [[center, east(700.0), east(1400.0), east(2100.0)]],
        "sliver_subcell": [[center, east(3000.0),
                            geo.destination(east(3000.0), 0.0, 8.0),
                            north(8.0)]],           # 8 m wide, 3 km long
        "bowtie": [[center, geo.destination(east(1500.0), 0.0, 1500.0),
                    east(1500.0), north(1500.0)]],  # self-crossing figure-8
        "self_touching": [[center, east(900.0),
                           geo.destination(east(900.0), 0.0, 900.0),
                           north(900.0), center,     # touches start mid-ring
                           geo.destination(center, 270.0, 900.0),
                           geo.destination(geo.destination(center, 270.0, 900.0),
                                           180.0, 900.0),
                           geo.destination(center, 180.0, 900.0)]],
        "empty_ring_list": [],
        "ring_of_empty": [[]],
        "far_outside_grid": [[LatLon(10.0, -60.0), LatLon(10.01, -60.0),
                              LatLon(10.01, -59.99)]],
        "antimeridian_thin": [[LatLon(34.4, 179.95), LatLon(34.4, -179.95),
                               LatLon(34.4005, -179.95),
                               LatLon(34.4005, 179.95)]],
    }
    for name, rings in cases.items():
        arr = _run_perim(tile, rings, wx)             # must not raise
        assert len(arr.data) == tile.spec.nrows * tile.spec.ncols, name
        finite = [v for v in arr.data if math.isfinite(v)]
        if name in ("two_verts", "one_vert", "collinear", "sliver_subcell"):
            # no interior cell centers -> vertex-cell fallback must seed
            assert finite, f"{name}: degenerate ring produced NO seeds at all " \
                           f"(vertex fallback failed — silent no-coverage)"
            assert min(finite) == PERIM_T, name
        if name in ("bowtie", "self_touching"):
            assert finite and min(finite) == PERIM_T, (
                f"{name}: self-intersecting ring produced no burned cells")
        if name in ("empty_ring_list", "ring_of_empty", "far_outside_grid",
                    "antimeridian_thin"):
            # nothing seedable in-grid: all-UNREACHED, and NOT a crash
            assert not finite, name
    # degenerate perimeter + real ignition still spreads from the ignition
    arr = _run_perim(tile, cases["one_vert"], wx, ignition=[east(4000.0)])
    rc = geo.latlon_to_rc(tile.spec, east(4000.0))
    assert arr.get(*rc) == T0


def test_vertex_fallback_marks_cells_under_ring_vertices():
    """TRUE sub-cell sliver (no cell CENTER inside): every ring vertex inside
    the grid must be burned at perimeter_at_s (the documented minimum
    fallback). The sliver is offset 30..35 m north of the row-30 cell-center
    latitude so no scanline can cross it (cell pitch 100 m).

    (Construction note, kept as evidence: anchoring the sliver's south edge
    EXACTLY on a cell-center latitude instead makes one scanline rasterize
    the row — the mask is then non-empty, the fallback correctly stays off,
    and the anchor vertex cell itself can fall out by the half-open rounding
    rule. That is inherent even-odd rasterization boundary behavior on a
    measure-zero alignment, not a fallback defect.)"""
    tile = _tile()
    anchor = geo.rc_to_latlon(tile.spec, 30, 40)
    base = geo.destination(anchor, 0.0, 30.0)          # between center rows
    sliver = [base, geo.destination(base, 90.0, 2500.0),
              geo.destination(geo.destination(base, 90.0, 2500.0), 0.0, 5.0),
              geo.destination(base, 0.0, 5.0)]
    assert spread._perimeter_interior_cells(tile.spec, [sliver]) == [], \
        "construction failed: sliver rasterized an interior after all"
    arr = _run_perim(tile, [sliver], _wx(anchor))
    for p in sliver:
        rc = geo.latlon_to_rc(tile.spec, p)
        if rc is None:
            continue
        assert arr.get(*rc) == PERIM_T, (
            f"vertex cell {rc} not seeded by the degenerate-polygon fallback")


# ---------------------------------------------------------------------------
# 2f — boundary seeding must cover the FULL 16-neighbor edge set
# ---------------------------------------------------------------------------

def _rect_ring(spec, r0, c0, r1, c1):
    return [geo.rc_to_latlon(spec, r0, c0), geo.rc_to_latlon(spec, r0, c1),
            geo.rc_to_latlon(spec, r1, c1), geo.rc_to_latlon(spec, r1, c0)]


def test_exterior_front_not_late_from_8_neighbor_boundary_seeding():
    """FINDING (2026-07-09, scratch_review/attack_s2_probe_knightseed.py):
    compute_arrival seeds the burned mask's boundary using an 8-NEIGHBOR
    test, but the lattice has 16 neighbors — knight edges from cells one
    ring INSIDE the mask legally cross the boundary and are never relaxed
    ("interior-of-interior cells can never improve any label" is false on
    the 16-neighbor graph). Removing burned area can only delay a correct
    solver, yet carving an interior hole that forces ring-2 seeding makes
    1,185 exterior cells EARLIER, by up to 862.8 s (14.4 min) at 100 m
    cells / 14 m/s wind — i.e. the shipped solid-perimeter arrival field is
    LATE (anti-safe: late p10 -> later latest_depart; late p50 -> later
    EXTREME) immediately downwind of every live perimeter incident.

    This test asserts exterior-arrival invariance (<= 1 s) under the hole
    carving; it FAILS on current code and is the regression gate for the
    fix (seed rings 1 AND 2, or detect boundary over the 16-neighborhood)."""
    spec = GridSpec(north_lat=34.60, west_lon=-119.20, cell_m=100.0,
                    nrows=80, ncols=80)
    n = 80 * 80
    tile = TerrainTile(spec=spec, fuel=Raster(spec, [102.0] * n),
                       slope_deg=Raster(spec, [0.0] * n),
                       aspect_deg=Raster(spec, [-1.0] * n),
                       elev_m=Raster(spec, [100.0] * n), source="attack-2f")
    wx = MockWeather("attack-2f", base_wind_mps=14.0, dir_deg=333.0,
                     rh_pct=15.0, temp_c=35.0, t0_s=T0).forecast(
                         geo.rc_to_latlon(spec, 20, 40), 110)
    cfg = ResponseConfig(horizon_h=96.0)
    outer = _rect_ring(spec, 10, 10, 30, 60)
    hole = _rect_ring(spec, 12, 12, 28, 58)   # forces ring-2 cells to seed
    solid = spread.compute_arrival(tile, [], wx, T0, cfg, perimeter=[outer],
                                   perimeter_at_s=T0 - 2 * 3600.0)
    holed = spread.compute_arrival(tile, [], wx, T0, cfg,
                                   perimeter=[outer, hole],
                                   perimeter_at_s=T0 - 2 * 3600.0)
    mask = set(spread._perimeter_interior_cells(spec, [outer]))
    late = []
    for i in range(n):
        if i in mask:
            continue
        a, b = solid.data[i], holed.data[i]
        if math.isfinite(b) and (not math.isfinite(a) or a > b + 1.0):
            late.append((i, (a - b) if math.isfinite(a) else math.inf))
    worst = max((d for _i, d in late), default=0.0)
    assert not late, (
        f"{len(late)} exterior cells arrive LATE from the solid perimeter "
        f"vs the identical fire with ring-2 seeding forced (worst "
        f"{worst / 60.0:.1f} min): 8-neighbor boundary seeding drops "
        f"knight-edge relaxations")


# ---------------------------------------------------------------------------
# 2e — past perimeter arrival through the ladder: 0 hours, IMPACTED, terminal
# ---------------------------------------------------------------------------

def test_past_arrival_ladder_impacted_and_terminal():
    tile = _tile()
    center = geo.rc_to_latlon(tile.spec, 30, 30)
    ring = _square(center, 1200.0)
    ens = spread.ensemble_arrival(tile, [], _wx(center), T0, CFG,
                                  incident_id="perim-lad", perimeter=[ring],
                                  perimeter_at_s=PERIM_T)
    eta = spread.eta_at(ens, center)
    assert eta.eta_p50_s == PERIM_T                   # 4 h in the past
    h10, h50, h90 = eta.hours_out(T0)
    assert h10 == 0.0 and h50 == 0.0 and h90 == 0.0   # clamped, never negative

    cfg = ResponseConfig()
    eng = AlertEngine(cfg)
    mk = lambda t: [ThreatAssessment(client_id="C-in", incident_id="perim-lad",
                                     eta=eta, computed_at_s=t)]
    ev1 = eng.evaluate(mk(T0), T0)
    assert [e.state for e in ev1] == [AlertState.EXTREME_24H]
    assert ev1[0].eta_p50_h == 0.0 and ev1[0].eta_p10_h == 0.0, (
        "ladder event carries negative/weird hours for a past arrival")
    ev2 = eng.evaluate(mk(T0 + 900.0), T0 + 900.0)
    assert [e.state for e in ev2] == [AlertState.IMPACTED]
    # terminality: nothing ever transitions out of IMPACTED, with or without
    # further assessments, even a clear-looking one
    clear_eta = spread.eta_at(ens, geo.destination(center, 0.0, 50_000.0))
    for k in range(3, 9):
        t = T0 + k * 900.0
        a = mk(t) if k % 2 else [ThreatAssessment(
            client_id="C-in", incident_id="perim-lad", eta=clear_eta,
            computed_at_s=t)]
        assert eng.evaluate(a, t) == [], "IMPACTED is not terminal"
    rec = eng.states()[("C-in", "perim-lad")]
    assert rec.state is AlertState.IMPACTED


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
