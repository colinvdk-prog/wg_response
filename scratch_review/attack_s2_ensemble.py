"""ADVERSARIAL VERIFIER (Session 2) — attack #1: ensemble v2 safety claims.

Claims under attack (Sprint 2 ticket A4, Council-1 model F2+F6; verifier brief):
  1a. p10 must be the EARLIEST/pessimistic arrival across members including
      direction offsets: for a ring of clients around a fire at multiple radii,
      p10(client) <= nominal-member arrival at EVERY bearing. Any client where
      p10 > nominal is a BLOCKING finding (the crew-safety chain keys every
      deadline off p10; a p10 later than the engine's own best-estimate run is
      anti-pessimistic exactly where the head of the fire is coming).
  1b. Static-fire jitter < 5% across 8 consecutive 15-min cycles (independent
      rerun, different geometry from tests/test_spread.py: diagonal wind).
  1c. Seeding law: draws come ONLY from (incident_id, member_k) — two
      different incidents on identical geometry get DIFFERENT member draws;
      the same incident on consecutive cycles gets IDENTICAL draws; t0 never
      enters.

Run from repo root:
    py -3 -m pytest scratch_review/attack_s2_ensemble.py -v
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo, spread
from wgresponse.contracts import (GridSpec, LatLon, Raster, ResponseConfig,
                                  TerrainTile)
from wgresponse.weather import MockWeather

T0 = 1_780_000_000.0


def _flat_tile(north, west, cell_m, nrows, ncols, fuel=102.0):
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


def _wx(loc, wind=6.0, wdir=270.0, rh=25.0, temp=30.0, hours=110):
    return MockWeather("attack-s2-ens", base_wind_mps=wind, dir_deg=wdir,
                       rh_pct=rh, temp_c=temp, t0_s=T0).forecast(loc, hours)


def _hours(v, t0=T0):
    return math.inf if v is None or math.isinf(v) else (v - t0) / 3600.0


# ---------------------------------------------------------------------------
# 1a — ring probe: p10 <= nominal at every bearing / radius
# ---------------------------------------------------------------------------

def _ring_probe(incident_id, radii_km=(3.0, 6.0, 8.0), n_bearings=16):
    """Returns (violations, diagnostics) for the ring probe.

    violations: list of (radius_km, bearing_deg, p10_h, nominal_h, exceed_min)
    where p10 > nominal + 1 ms at a client with a finite nominal arrival.
    """
    tile = _flat_tile(34.70, -119.60, 150.0, 121, 121)
    ign = geo.rc_to_latlon(tile.spec, 60, 60)
    wx = _wx(ign)
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
                # nominal never reaches: p10 may be finite (earlier) — fine.
                continue
            if v10 > vno + 1e-3:          # 1 ms guard band on unix seconds
                violations.append((r_km, brg, _hours(v10), _hours(vno),
                                   (v10 - vno) / 60.0))
    # full-grid sweep (diagnostic strength: every cell is a bearing)
    worst_cell = (0.0, None)
    n_cells_late = 0
    for i, (p10v, nomv) in enumerate(zip(ens.p10.data, nominal.data)):
        if math.isinf(nomv):
            continue
        if p10v > nomv + 1e-3:
            n_cells_late += 1
            if p10v - nomv > worst_cell[0]:
                worst_cell = (p10v - nomv, i)
    return violations, checked, n_cells_late, worst_cell, ens


def test_ring_p10_never_later_than_nominal():
    """Verifier brief 1a: p10(client) <= nominal arrival for ALL bearings.

    Probes a 16-bearing ring at 3/6/8 km around a flat-grass fire (defaults:
    ensemble_n=6, dir_perturb 30 deg). ANY client where p10 > nominal is
    blocking per the brief.

    FINDING (2026-07-09): FAILS for ~49% of incident ids (1944/4000 in the
    analytic screen, scratch_review/attack_s2_probe_idscan.py). Hazen p10
    with 6 members is s0 + 0.1*(s1 - s0); whenever the Latin-hypercube
    pairing (a pure function of incident_id) hands the dir=0 stratum to a
    slow wind/moisture member, the NOMINAL run is strictly earliest along
    the head axis and p10 lands ~1.9% of ETA LATER than nominal: measured
    +4.1 min @3 km, +8.2 min @6 km, +10.8 min @8 km (ids att-10, att-124;
    grows with lead — ~27 min at a 24 h P10). Every crew deadline
    (latest_depart = p10 - 30 min) and the eroded corridor raster inherit
    the bias at exactly the bearings the head is charging toward.
    The two ids pinned below are screened ADVERSARIAL ids; benign ids (e.g.
    'attack-ring-1..3') pass, which is why a single-id spot check misses it."""
    all_viol = {}
    for iid in ("att-10", "att-124"):
        viol, checked, n_late, worst, _ens = _ring_probe(iid)
        assert checked >= 40, f"probe degenerate: only {checked} ring clients sampled"
        all_viol[iid] = (viol, n_late, worst)
    msgs = []
    for iid, (viol, n_late, worst) in all_viol.items():
        if viol or n_late:
            worst_min = max((v[4] for v in viol), default=0.0)
            msgs.append(
                f"[{iid}] ring violations={len(viol)} "
                f"(worst p10-nominal = +{worst_min:.1f} min); "
                f"grid cells with p10 later than nominal: {n_late} "
                f"(worst +{worst[0] / 60.0:.1f} min at cell {worst[1]}); "
                f"examples: " + "; ".join(
                    f"r={v[0]:.0f}km brg={v[1]:.0f} p10={v[2]:.2f}h "
                    f"nom={v[3]:.2f}h (+{v[4]:.1f}min)" for v in viol[:6]))
    assert not msgs, (
        "p10 is LATER than the nominal member (anti-pessimistic) at these "
        "ring clients — every crew-safety deadline (latest_depart = p10 - "
        "30 min) is computed from a p10 that trails the engine's own "
        "best-estimate arrival:\n" + "\n".join(msgs))


# ---------------------------------------------------------------------------
# 1b — independent jitter rerun (diagonal wind, my own geometry)
# ---------------------------------------------------------------------------

def test_static_fire_jitter_under_5pct_independent_rerun():
    """Verifier brief 1b: independent 8-cycle rerun of the static-fire jitter
    claim on DIFFERENT geometry than tests/test_spread.py (diagonal wind FROM
    225 so the head runs NE; clients off-grid-axis; cell 120 m)."""
    tile = _flat_tile(34.80, -119.80, 120.0, 100, 100)
    ign = geo.rc_to_latlon(tile.spec, 75, 25)
    wx = MockWeather("attack-jitter", base_wind_mps=4.0, dir_deg=225.0,
                     rh_pct=30.0, temp_c=28.0, t0_s=T0).forecast(ign, 110)
    cfg = ResponseConfig(ensemble_n=6, horizon_h=96.0)
    clients = {"NE5": geo.destination(ign, 45.0, 5000.0),
               "NE8": geo.destination(ign, 45.0, 8000.0),
               "N4":  geo.destination(ign, 0.0, 4000.0)}
    series = {(n, q): [] for n in clients for q in ("p10", "p50")}
    for k in range(8):
        t0 = T0 + k * 900.0
        ens = spread.ensemble_arrival(tile, [ign], wx, t0, cfg,
                                      incident_id="attack-jitter-fire")
        for name, loc in clients.items():
            eta = spread.eta_at(ens, loc)
            series[(name, "p10")].append((eta.eta_p10_s - t0) / 3600.0)
            series[(name, "p50")].append((eta.eta_p50_s - t0) / 3600.0)
    for (name, q), vals in series.items():
        finite = [v for v in vals if math.isfinite(v)]
        assert len(finite) == 8, (name, q, vals)
        mean = sum(finite) / len(finite)
        swing_pct = (max(finite) - min(finite)) / mean * 100.0
        assert swing_pct < 5.0, (
            f"jitter claim broken: {name}/{q} swings {swing_pct:.2f}% "
            f"(> 5%) across 8 static cycles: {vals}")


# ---------------------------------------------------------------------------
# 1c — seeding law
# ---------------------------------------------------------------------------

def test_seed_law_incidents_differ_cycles_repeat():
    """Verifier brief 1c at the draw level AND the arrival level."""
    # Draw level: same id twice -> identical; different ids -> different.
    for m in (3, 5, 7):
        base = spread._stratified_member_draws("inc-alpha", m)
        again = spread._stratified_member_draws("inc-alpha", m)
        assert base == again, "draws are not a pure function of (incident, k)"
        for other in ("inc-beta", "inc-gamma", "IRWIN-2026-000042"):
            alt = spread._stratified_member_draws(other, m)
            assert alt != base, (
                f"cross-incident correlation: {other!r} reproduces "
                f"inc-alpha's member draws at m={m}")
    # Every dimension uses each stratum exactly once (Latin hypercube), so a
    # 'different draw' must be a different PAIRING, never a different value set.
    m = 5
    strata = sorted(2.0 * (j + 0.5) / m - 1.0 for j in range(m))
    for iid in ("inc-alpha", "inc-beta"):
        draws = spread._stratified_member_draws(iid, m)
        for d in range(3):
            assert sorted(t[d] for t in draws) == strata

    # Arrival level: same incident, consecutive cycles -> identical relative
    # fields; different incident -> different perturbed members.
    tile = _flat_tile(34.90, -119.30, 200.0, 45, 60)
    ign = geo.rc_to_latlon(tile.spec, 22, 8)
    wx = _wx(ign, wind=5.0, wdir=270.0)
    cfg = ResponseConfig(ensemble_n=5, horizon_h=96.0)
    e1 = spread.ensemble_arrival(tile, [ign], wx, T0, cfg, incident_id="A")
    e2 = spread.ensemble_arrival(tile, [ign], wx, T0 + 900.0, cfg,
                                 incident_id="A")
    e3 = spread.ensemble_arrival(tile, [ign], wx, T0, cfg, incident_id="B")
    probe_cells = [(22, 30), (10, 40), (35, 50)]
    for k, (m1, m2) in enumerate(zip(e1.members, e2.members)):
        for rc in probe_cells:
            a = m1.get(*rc) - T0
            b = m2.get(*rc) - (T0 + 900.0)
            if math.isinf(a) or math.isinf(b):
                assert math.isinf(a) and math.isinf(b), (k, rc)
                continue
            assert abs(a - b) < 1e-2, (
                f"member {k} not cycle-stable at {rc}: {a} vs {b} "
                f"(draws leaked t0 into the seed)")
    diff = 0
    for m1, m3 in zip(e1.members[1:], e3.members[1:]):
        for rc in probe_cells:
            a, b = m1.get(*rc), m3.get(*rc)
            if math.isfinite(a) and math.isfinite(b) and abs(a - b) > 1.0:
                diff += 1
    assert diff > 0, ("two different incidents produced identical perturbed "
                      "members on the same geometry — cross-incident "
                      "correlation")
    # nominal member must be identical across incident ids (unperturbed)
    assert e1.members[0].data == e3.members[0].data


if __name__ == "__main__":
    # Script mode: print the ring-probe diagnostics for the report.
    for iid in ("attack-ring-1", "attack-ring-2", "attack-ring-3"):
        viol, checked, n_late, worst, ens = _ring_probe(iid)
        print(f"incident {iid}: ring clients checked={checked}, "
              f"violations={len(viol)}, grid cells p10>nominal={n_late}, "
              f"worst=+{worst[0] / 60.0:.2f} min")
        for v in viol:
            print(f"   r={v[0]:.0f}km brg={v[1]:5.1f} p10={v[2]:7.3f}h "
                  f"nom={v[3]:7.3f}h  (+{v[4]:5.2f} min)")
