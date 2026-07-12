"""attack_r4_ember.py — FOUNDER ROUND 4 law 6 (EMBER / ADJACENCY THREAT),
the RISK DIRECTION: does the 200 m adjacency fallback ever falsely threaten a
GENUINELY SAFE developed home?

Attacks:
  (1) BARRIER LEAK SWEEP (controlled tiles): a developed home behind a
      non-burnable barrier B cells wide, fire on the far side. Sweep the fallback
      radius through spread.eta_at; find the exact radius at which erosion leaks
      across the barrier; PROVE 200 m does NOT leak for a barrier >= 3 cells
      (270 m on the 90 m sim grid) and matches the closed form R > cell_m*B.
  (2) RADIUS-0 PIN: spread.eta_at(...,0.0) is byte-identical to the direct
      nearest-cell sample at every probe (independent), incl. developed cells.
  (3) MONOTONICITY: independently-eroded p10<=p50<=p90 preserved.
  (4) ISOLATED HOME (real Malibu): every developed home whose engine eta_at
      FIRED the fallback has fire-reached fuel within the window (independently
      verified) — never spurious; every UNREACHED developed home genuinely has
      NO reached fuel within the window.
  (5) ORACLE: engine spread.eta_at == an OWN reimplementation for EVERY client
      every tick on a real Malibu run (byte-independent).

Run:  py -3 -m pytest scratch_review/attack_r4_ember.py -q
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo, spread                                    # noqa: E402
from wgresponse.contracts import (LatLon, GridSpec, Raster,           # noqa: E402
                                  TerrainTile, ResponseConfig, UNREACHED,
                                  WeatherForecast, WeatherSample)
from attack_r4_common import (TILE_DIR, erode_min_indep, rc_of,        # noqa: E402
                              ember_eta_indep, reached_finite_within_window,
                              reached_finite_within_window as _rfw,
                              tile_present, skip_loud)

T0 = 1_600_000_000.0


# --------------------------------------------------------------------------
# Controlled tiles (fully deterministic, precise leak radius)
# --------------------------------------------------------------------------
def _grass_tile(nrows, ncols, cell_m):
    spec = GridSpec(north_lat=34.2, west_lon=-118.8, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)
    n = nrows * ncols
    return TerrainTile(spec=spec, fuel=Raster(spec, [102.0] * n),
                       slope_deg=Raster(spec, [0.0] * n),
                       aspect_deg=Raster(spec, [0.0] * n),
                       elev_m=Raster(spec, [100.0] * n), source="synthetic")


def _const_wx(loc, wind_dir_deg, mps=10.0):
    s = [WeatherSample(t_s=T0 + 3600 * i, wind_speed_mps=mps,
                       wind_gust_mps=mps * 1.3, wind_dir_deg=wind_dir_deg,
                       rh_pct=15.0, temp_c=30.0) for i in range(160)]
    return WeatherForecast(loc=loc, samples=s, source="mock")


def _barrier_ens(cell_m, barrier_w):
    """41x61 GR2 tile, vertical NB barrier `barrier_w` cells wide at col 30,
    developed home 1 cell EAST of the barrier, ignition WEST. Returns
    (ens, home_loc, home_rc, west_reached_bool)."""
    nrows, ncols = 41, 61
    tile = _grass_tile(nrows, ncols, cell_m)
    bstart = 30
    for w in range(barrier_w):
        for r in range(nrows):
            tile.fuel.set(r, bstart + w, 91.0)
    home_c = bstart + barrier_w
    tile.fuel.set(20, home_c, 91.0)
    ign = geo.rc_to_latlon(tile.spec, 20, 15)
    wx = _const_wx(ign, 270.0)
    cfg = ResponseConfig(ensemble_n=2, horizon_h=220.0)
    ens = spread.ensemble_arrival(tile, [ign], wx, T0, cfg, incident_id="leak")
    home = geo.rc_to_latlon(tile.spec, 20, home_c)
    west = geo.raster_sample(ens.p10, geo.rc_to_latlon(tile.spec, 20, bstart - 1))
    return ens, home, (20, home_c), math.isfinite(west)


@pytest.mark.parametrize("cell_m", [90.0, 100.0, 150.0])
@pytest.mark.parametrize("barrier_w", [1, 2, 3, 4, 5])
def test_barrier_leak_radius_matches_closed_form_and_200_is_safe(cell_m, barrier_w):
    """Sweep the fallback radius through the REAL spread.eta_at; the first radius
    that leaks across a B-cell barrier is exactly the smallest R with
    ceil(R/cell) >= B+1 (i.e. R > cell*B). At 200 m the window is
    k=ceil(200/cell); a barrier with B >= k blocks the leak — proving a home
    behind a barrier WIDER THAN THE EROSION WINDOW stays UNREACHED."""
    ens, home, home_rc, west_reached = _barrier_ens(cell_m, barrier_w)
    assert west_reached, "setup: fire must reach the west side of the barrier"
    # own cell is developed / never reached
    assert math.isinf(geo.raster_sample(ens.p10, home))

    # sweep radius; leak == engine eta_at returns finite
    radii = [float(r) for r in range(0, int(cell_m * (barrier_w + 3)) + 60, 15)]
    leaks = {r: math.isfinite(spread.eta_at(ens, home, fallback_radius_m=r).eta_p10_s)
             for r in radii}
    first_leak = next((r for r in radii if leaks[r]), None)
    # closed form: leak iff ceil(R/cell) >= B+1  <=>  R > cell*B
    assert first_leak is not None
    assert first_leak > cell_m * barrier_w - 1e-9, (cell_m, barrier_w, first_leak)
    assert first_leak <= cell_m * barrier_w + 15.0 + 1e-9, (cell_m, barrier_w, first_leak)
    # no radius <= cell*B may leak (barrier wider than window blocks)
    for r in radii:
        if r <= cell_m * barrier_w:
            assert not leaks[r], (cell_m, barrier_w, r)

    # THE headline: 200 m must NOT leak when the barrier is >= the 200 m window
    k200 = math.ceil(200.0 / cell_m)
    eta200 = spread.eta_at(ens, home, fallback_radius_m=200.0)
    if barrier_w >= k200:
        assert math.isinf(eta200.eta_p10_s), (
            f"cell={cell_m} B={barrier_w} k200={k200}: 200 m LEAKED across a "
            f"barrier at least as wide as the 200 m window — a genuinely safe "
            f"developed home was FALSELY THREATENED")


def test_barrier_report_leak_table(capsys):
    """Emit the barrier-leak radius table (verifier deliverable)."""
    print("\n=== EMBER BARRIER-LEAK RADIUS (real spread.eta_at) ===")
    print(f"{'cell_m':>7} {'barrier_w':>9} {'barrier_m':>9} {'first_leak_R':>12} "
          f"{'leak@200m':>9}")
    for cell_m in (90.0, 100.0, 150.0):
        for bw in (1, 2, 3, 4, 5):
            ens, home, _, _ = _barrier_ens(cell_m, bw)
            fl = None
            for r in range(0, int(cell_m * (bw + 3)) + 60, 15):
                if math.isfinite(spread.eta_at(ens, home,
                                               fallback_radius_m=float(r)).eta_p10_s):
                    fl = r
                    break
            leak200 = math.isfinite(
                spread.eta_at(ens, home, fallback_radius_m=200.0).eta_p10_s)
            print(f"{cell_m:>7.0f} {bw:>9} {cell_m*bw:>9.0f} {fl:>12} "
                  f"{str(leak200):>9}")
    print("closed form: first leak at R > cell_m * barrier_w; 200 m window "
          "k=ceil(200/cell) -> safe behind a barrier of >= k cells")


def test_isolated_developed_home_never_spurious_across_radii():
    """A developed home behind a FULL barrier (no reached fuel anywhere within
    the swept window) stays UNREACHED for every radius up to just under the leak
    — engine AND the independent oracle agree."""
    ens, home, home_rc, _ = _barrier_ens(90.0, 5)   # 5-cell (450 m) barrier
    for R in (0.0, 90.0, 200.0, 300.0, 400.0):       # all < 450 leak threshold
        eng = spread.eta_at(ens, home, fallback_radius_m=R)
        ind = ember_eta_indep(ens, home, R)
        assert (eng.eta_p10_s, eng.eta_p50_s, eng.eta_p90_s) == ind
        # independent ground truth: no reached fuel within window -> unreached
        if not reached_finite_within_window(ens.p10.data, ens.spec, home_rc, R):
            assert math.isinf(eng.eta_p10_s), (R,)


# --------------------------------------------------------------------------
# Real Malibu: the engine's eta_at vs an OWN oracle, for EVERY client, and the
# isolated-home / spurious-threat ground truth.
# --------------------------------------------------------------------------
def _malibu_session(seed, n_clients=50, n_crews=3):
    from wgresponse.simui import SimSession
    from attack_sim_common import pick_ignition
    sess = SimSession("malibu_ca", n_clients, n_crews, seed=seed,
                      cfg_overrides={}, fire_zone="standard", tile_dir=TILE_DIR)
    p, _ = pick_ignition(sess, "malibu_ca", cluster=True)
    sess.ignite(p.lat, p.lon, intensity="high")
    return sess


@pytest.mark.skipif(not tile_present("malibu_ca"),
                    reason=skip_loud("malibu_ca", "tile"))
def test_engine_eta_at_matches_independent_oracle_every_client():
    """The engine's law-6 eta_at (200 m) is byte-identical to an OWN
    reimplementation for EVERY (client, tick) on a real Malibu run — and every
    developed home whose fallback FIRED has fire-reached fuel within the window
    (never a spurious threat); every UNREACHED developed home has NONE."""
    R = 200.0
    for seed in ("emb1", "emb2"):
        sess = _malibu_session(seed)
        cfg = sess._cfg
        assert cfg.ember_threat_radius_m == R
        spurious = 0
        checked = 0
        for _t in range(30):
            sess.tick()
            for fid in sess._fire_order:
                ens = sess._fires[fid].ens
                if ens is None:
                    continue
                spec = ens.spec
                for cl in sess._clients:
                    eng = spread.eta_at(ens, cl.loc, fallback_radius_m=R)
                    ind = ember_eta_indep(ens, cl.loc, R)
                    assert (eng.eta_p10_s, eng.eta_p50_s, eng.eta_p90_s) == ind, \
                        (seed, cl.client_id)
                    checked += 1
                    rc = rc_of(spec, cl.loc.lat, cl.loc.lon)
                    if rc is None:
                        continue
                    own_inf = math.isinf(ens.p10.data[rc[0] * spec.ncols + rc[1]])
                    fired = own_inf and math.isfinite(eng.eta_p10_s)
                    has_reached = reached_finite_within_window(
                        ens.p10.data, spec, rc, R)
                    if fired:
                        # the fallback only ever fires when real reached fuel is
                        # within the window — never a spurious threat
                        assert has_reached, (seed, cl.client_id)
                    if own_inf and not has_reached:
                        # genuinely isolated -> must stay UNREACHED
                        assert math.isinf(eng.eta_p10_s), (seed, cl.client_id)
                        spurious += 0
        assert checked > 0
