"""Session-2 verifier attack regressions — spread.py BLOCKING fixes.

Promoted from scratch_review/attack_s2_ensemble.py and
scratch_review/attack_s2_perimeter.py (adversarial verification of Sprint-2
Session-2 tickets A4 + A5); probes scratch_review/attack_s2_probe_idscan.py
and attack_s2_probe_knightseed.py hold the measurement evidence.

Finding 1 (BLOCKING, fixed — nominal-bounded p10): Hazen p10 over 6 members
is s0 + 0.1*(s1 - s0), strictly ABOVE the sample minimum; whenever the
Latin-hypercube pairing (a pure function of incident_id) leaves the NOMINAL
member the strict minimum along some bearing (1944/4000 ids in the analytic
screen), p10 landed LATER than the engine's own best-estimate arrival at
exactly the head-axis clients the fire is charging at (+4.1 min @3 km,
+8.2 min @6 km, +10.8 min @8 km), and the A4 cycle-stable seeding froze the
anti-safe bias for the incident's whole life. Every crew deadline
(latest_depart = p10 - 30 min, SAFETY SEMANTICS 2), the eroded-p10 corridor
rasters (SEMANTICS 3), and the extreme_p10_h ladder trigger inherit p10.
Fix: per-cell p10 = min(Hazen p10, nominal arrival).

Finding 2 (BLOCKING, fixed — 16-offset burned-mask boundary seeding):
compute_arrival seeded only mask cells with a non-final 8-NEIGHBOR, but the
lattice relaxes 16 offsets — cells one ring INSIDE the mask own legal knight
edges that cross the boundary and were never relaxed, so exterior arrival
fields were LATE by up to 862.8 s (14.4 min at 100 m cells / 14 m/s wind) —
the worst polarity for an alerting product. Proof by contradiction: carving
an interior hole (strictly LESS burned area, which can only DELAY a correct
solver) made 1,185 exterior cells EARLIER. Fix: detect the seed boundary
over the same 16-offset set as edge relaxation; with spotting enabled every
mask cell seeds (the teleport edge has unbounded reach, so no lattice-local
boundary test covers it).

Deterministic; no network; stdlib + wgresponse only.
"""
from __future__ import annotations

import math

from wgresponse import geo, spread
from wgresponse.contracts import (
    GridSpec,
    Raster,
    ResponseConfig,
    TerrainTile,
)
from wgresponse.weather import MockWeather

T0 = 1_780_000_000.0


def _flat_tile(north, west, cell_m, nrows, ncols, fuel=102.0) -> TerrainTile:
    spec = GridSpec(north_lat=north, west_lon=west, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)
    n = nrows * ncols
    return TerrainTile(
        spec=spec,
        fuel=Raster(spec, [float(fuel)] * n),
        slope_deg=Raster(spec, [0.0] * n),
        aspect_deg=Raster(spec, [-1.0] * n),
        elev_m=Raster(spec, [100.0] * n),
        source="attack-s2",
    )


def _rect_ring(spec, r0, c0, r1, c1):
    """Ring through the CENTERS of the rectangle corners (r0..r1, c0..c1)."""
    return [geo.rc_to_latlon(spec, r0, c0), geo.rc_to_latlon(spec, r0, c1),
            geo.rc_to_latlon(spec, r1, c1), geo.rc_to_latlon(spec, r1, c0)]


# ---------------------------------------------------------------------------
# Finding 1 — ensemble p10 must never trail the nominal member
# ---------------------------------------------------------------------------

def _ring_probe(incident_id, radii_km=(3.0, 6.0, 8.0), n_bearings=16):
    """(ring_violations, checked, grid_cells_late, ens) for one incident id:
    clients on rings at radii_km x n_bearings bearings around a flat-grass
    fire under engine defaults (ensemble_n=6, dir_perturb 30 deg), plus a
    full-grid sweep (every cell is a bearing)."""
    tile = _flat_tile(34.70, -119.60, 150.0, 121, 121)
    ign = geo.rc_to_latlon(tile.spec, 60, 60)
    wx = MockWeather("attack-s2-ens", base_wind_mps=6.0, dir_deg=270.0,
                     rh_pct=25.0, temp_c=30.0, t0_s=T0).forecast(ign, 110)
    cfg = ResponseConfig(ensemble_n=6, horizon_h=96.0)   # engine defaults
    ens = spread.ensemble_arrival(tile, [ign], wx, T0, cfg,
                                  incident_id=incident_id)
    nominal = ens.members[0]
    violations = []
    checked = 0
    for r_km in radii_km:
        for k in range(n_bearings):
            brg = k * (360.0 / n_bearings)
            loc = geo.destination(ign, brg, r_km * 1000.0)
            v10 = geo.raster_sample(ens.p10, loc)
            vno = geo.raster_sample(nominal, loc)
            if v10 is None or vno is None:
                continue
            checked += 1
            if math.isinf(vno):
                continue          # nominal never reaches: finite p10 is fine
            if v10 > vno + 1e-3:  # 1 ms guard band on unix seconds
                violations.append((r_km, brg, (v10 - vno) / 60.0))
    n_cells_late = sum(
        1 for p10v, nomv in zip(ens.p10.data, nominal.data)
        if not math.isinf(nomv) and p10v > nomv + 1e-3)
    return violations, checked, n_cells_late, ens


def test_ring_p10_never_later_than_nominal():
    """THE Finding-1 regression gate (verifier brief 1a): p10(client) <=
    nominal-member arrival at EVERY bearing/radius AND every grid cell.

    The two ids pinned here are the screened ADVERSARIAL ids (att-10 and
    att-124: the LHS pairing hands the dir=0 stratum to a slow wind/wet
    moisture member, making the nominal run the strict head-axis minimum);
    benign ids pass even unfixed, which is why single-id spot checks missed
    the defect. Pre-fix: 6-9 ring violations and 372-395 late grid cells per
    id, worst +10.8 min at 8 km."""
    for iid in ("att-10", "att-124", "attack-ring-1"):
        viol, checked, n_late, _ens = _ring_probe(iid)
        assert checked >= 40, f"probe degenerate: only {checked} ring clients"
        assert not viol and n_late == 0, (
            f"[{iid}] p10 is LATER than the nominal member (anti-pessimistic) "
            f"at {len(viol)} ring clients / {n_late} grid cells "
            f"(worst +{max((v[2] for v in viol), default=0.0):.1f} min) — "
            f"every crew-safety deadline (latest_depart = p10 - 30 min) "
            f"trails the engine's own best-estimate arrival")


def test_p10_clamp_is_nominal_bounded_pessimism_not_min_collapse():
    """Pin the EXACT clamp semantics on an adversarial id: per cell,
    p10 == min(raw Hazen sample p10, nominal arrival) — recomputed here from
    the returned members — so (a) the clamp binds ONLY where the nominal
    member is the strict minimum (bytes untouched elsewhere), (b) p10 never
    drops below the earliest member (pessimism stays nominal-bounded, it is
    not replaced by a min-member collapse), and (c) quantile ordering
    p10 <= p50 <= p90 survives the clamp. On the pinned adversarial id the
    clamp must actually bind somewhere (else the regression gate above tests
    nothing)."""
    _viol, _checked, _n_late, ens = _ring_probe("att-10", radii_km=(3.0,))
    cols = [m.data for m in ens.members]
    nom = cols[0]
    n_bound = 0
    for i in range(len(nom)):
        vals = sorted(col[i] for col in cols)
        raw = spread._quantile(vals, 0.10)
        expect = raw if raw <= nom[i] else nom[i]
        assert ens.p10.data[i] == expect, (
            f"cell {i}: p10 {ens.p10.data[i]!r} != min(hazen {raw!r}, "
            f"nominal {nom[i]!r})")
        if nom[i] < raw:
            n_bound += 1
            assert ens.p10.data[i] == nom[i]
        # never below the most extreme member; ordering intact
        assert ens.p10.data[i] >= vals[0]
        assert ens.p10.data[i] <= ens.p50.data[i] <= ens.p90.data[i]
    assert n_bound > 0, ("adversarial id att-10 no longer exercises the "
                         "clamp — probe geometry drifted")


# ---------------------------------------------------------------------------
# Finding 2 — burned-mask seeding must cover the full 16-offset edge set
# ---------------------------------------------------------------------------

def _hole_carving_late_cells(cell_m, wind_mps, wdir, hole_inset,
                             spotting=False):
    """Exterior late-cell list for the hole-carving oracle: carving an
    interior hole out of the burned mask REMOVES burned area, which can only
    DELAY a correct solver — so any exterior cell arriving EARLIER under the
    holed mask proves the solid-mask run dropped a legal relaxation (late
    bias). Returns [(idx, lateness_s), ...] where solid > holed + 1 s."""
    spec = GridSpec(north_lat=34.60, west_lon=-119.20, cell_m=cell_m,
                    nrows=80, ncols=80)
    n = 80 * 80
    tile = TerrainTile(spec=spec, fuel=Raster(spec, [102.0] * n),
                       slope_deg=Raster(spec, [0.0] * n),
                       aspect_deg=Raster(spec, [-1.0] * n),
                       elev_m=Raster(spec, [100.0] * n), source="attack-2f")
    wx = MockWeather("attack-2f", base_wind_mps=wind_mps, dir_deg=wdir,
                     rh_pct=15.0, temp_c=35.0, t0_s=T0).forecast(
                         geo.rc_to_latlon(spec, 20, 40), 110)
    cfg = ResponseConfig(horizon_h=96.0, spotting_enabled=spotting)
    outer = _rect_ring(spec, 10, 10, 30, 60)
    hole = _rect_ring(spec, 10 + hole_inset, 10 + hole_inset,
                      30 - hole_inset, 60 - hole_inset)
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
    return late


def test_exterior_front_not_late_from_8_neighbor_boundary_seeding():
    """THE Finding-2 regression gate: exterior-arrival invariance under an
    interior hole that forces ring-2 cells of the mask to seed. Narrow
    ellipse (14 m/s), head (~153 deg) oblique to the boundary so a knight
    bearing is the fast edge. Pre-fix: 1,185 exterior cells EARLIER under
    the holed mask, worst 862.8 s (14.4 min) — i.e. the shipped
    solid-perimeter field was that LATE immediately downwind of every live
    perimeter incident."""
    late = _hole_carving_late_cells(cell_m=100.0, wind_mps=14.0, wdir=333.0,
                                    hole_inset=2)
    worst = max((d for _i, d in late), default=0.0)
    assert not late, (
        f"{len(late)} exterior cells arrive LATE from the solid perimeter "
        f"vs the identical fire with ring-2 seeding forced (worst "
        f"{worst / 60.0:.1f} min): 8-neighbor boundary seeding drops "
        f"knight-edge relaxations")


def test_exterior_front_not_late_with_spotting_interior_sources():
    """Spotting branch of the Finding-2 fix: the teleport edge has UNBOUNDED
    reach, so no lattice-local boundary test can cover it — with
    cfg.spotting_enabled every mask cell must seed. Same hole-carving oracle,
    hole 3 rings in (ring-3 cells seed only in the holed run unless the
    solid run pushes them too): GR2 at 25 m/s / 60 m cells spots ~589 m
    (~10 cells), so a dropped ring-3 spot source leaves its landing cell to
    the ~6x-slower lattice front. Verified discriminating (2026-07-09): with
    the 16-offset lattice fix alone (seed_all disabled) this fails with
    1,620 exterior cells late, worst 199.6 s (3.3 min); the no-spotting gate
    above stays green in that configuration, so this test alone guards the
    spotting branch."""
    late = _hole_carving_late_cells(cell_m=60.0, wind_mps=25.0, wdir=0.0,
                                    hole_inset=3, spotting=True)
    worst = max((d for _i, d in late), default=0.0)
    assert not late, (
        f"{len(late)} exterior cells arrive LATE from the solid perimeter "
        f"with spotting enabled (worst {worst / 60.0:.1f} min): interior "
        f"burned cells' spot edges were dropped by boundary-only seeding")
