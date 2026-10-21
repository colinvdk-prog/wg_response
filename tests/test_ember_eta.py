"""SAFETY SEMANTICS 35 — EMBER ETA FOR DEVELOPED HOMES (2026-10-21; the Eagle
River valley review's iteration 3 — the propagation review's R4; contracts
ember_eta_landings; docs/SIMULATOR_DESIGN.md "Far-side refuges, ember ETAs and
afternoon starts").

A home on a developed (or water) cell, which the surface model never reaches,
took its ETA from the earliest BURNABLE arrival within ember_threat_radius_m
(law 6); an ember landing next to it ahead of the front gave it nothing (the
spread kept only embers that land on burnable cells). With the knob the
nominal member records EVERY ember it lands inside the horizon
(compute_arrival land_log), the simulator keeps the landings across
re-anchors and attaches their first landing per cell to the field
(attach_ember_landings), and eta_at's law-6 fallback takes the earlier of the
front within the radius and the first landing within it.

These tests pin: the spread itself unchanged with the log (single solve,
ensemble), the log's reach (a non-burnable road the ember_log never names),
eta_at's rule on a built ensemble, the knob (default off, armed by the
simulator), the simulator's history across a re-anchor, and a valley home that
gets an ETA only from an ember."""
from __future__ import annotations

import hashlib
import math
import struct

import pytest

from wgresponse import geo, server, simui, spread
from wgresponse.contracts import (UNREACHED, EnsembleArrival, GridSpec,
                                  Raster, ResponseConfig)

from tests.test_spot_fire_size import HOUR, T0, _cfg, _mixed


def _fp(values) -> str:
    return hashlib.sha256(
        b"".join(struct.pack("<d", v) for v in values)).hexdigest()


def test_knob_is_off_by_default_and_the_simulator_arms_it():
    assert ResponseConfig().ember_eta_landings is False
    s = simui.SimSession("boulder_co", 2, 1, seed="ee", roads_module=None)
    assert s._cfg.ember_eta_landings is True
    s = simui.SimSession("boulder_co", 2, 1, seed="ee", roads_module=None,
                         cfg_overrides={"ember_eta_landings": False})
    assert s._cfg.ember_eta_landings is False


def test_the_land_log_leaves_the_spread_unchanged_and_names_every_landing():
    """The mixed-fuel world (a 2-cell non-burnable road down column 38-39):
    the arrival raster is bit-identical with and without the log; the log
    holds every ember the ember_log holds (burnable, non-final landings) and
    the ones that fall on the road too."""
    tile, ign, wx = _mixed()
    cfg = _cfg()
    plain = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                   incident_id="ee-mixed")
    elog, llog = [], []
    logged = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                    incident_id="ee-mixed", ember_log=elog,
                                    land_log=llog)
    assert _fp(plain.data) == _fp(logged.data)
    assert elog and set(elog) <= set(llog)
    nc = tile.spec.ncols
    on_road = [e for e in llog if e[0] % nc in (38, 39)]
    assert on_road
    assert not [e for e in elog if e[0] % nc in (38, 39)]
    horizon = T0 + cfg.horizon_h * HOUR
    assert all(t <= horizon for _i, _t0, t in llog)


def test_the_ensemble_quantiles_are_unchanged_and_the_log_is_the_nominal_s():
    tile, ign, wx = _mixed()
    cfg = _cfg(ensemble_n=4)
    a = spread.ensemble_arrival(tile, [ign], wx, T0, cfg, incident_id="ee-e")
    llog = []
    b = spread.ensemble_arrival(tile, [ign], wx, T0, cfg, incident_id="ee-e",
                                land_log=llog)
    for ra, rb in ((a.p10, b.p10), (a.p50, b.p50), (a.p90, b.p90)):
        assert _fp(ra.data) == _fp(rb.data)
    nom = []
    spread.compute_arrival(tile, [ign], wx, T0, _cfg(ensemble_n=4),
                           incident_id="ee-e", member_k=0, land_log=nom)
    assert sorted(llog) == sorted(nom)


def _ensemble(spec, front):
    ras = Raster(spec, list(front))
    return EnsembleArrival(spec=spec, members=[ras], p10=ras, p50=ras,
                           p90=ras, t0_s=T0, incident_id="ee")


def test_eta_at_takes_the_earlier_of_the_front_and_the_first_landing():
    """A developed home (its cell UNREACHED): the front reaches a burnable
    cell 180 m away at T0 + 5 h; an ember lands 90 m away at T0 + 1 h. Law 6
    alone gives T0 + 5 h; with the landings attached every quantile is
    T0 + 1 h; a landing beyond the radius changes nothing; a home whose own
    cell is reached keeps its own arrival."""
    spec = GridSpec(north_lat=39.5, west_lon=-121.5, cell_m=90.0, nrows=11,
                    ncols=11)
    n = spec.nrows * spec.ncols
    front = [UNREACHED] * n
    front[5 * 11 + 7] = T0 + 5 * HOUR          # 2 cells east of the home
    home = geo.rc_to_latlon(spec, 5, 5)
    ens = _ensemble(spec, front)
    eta = spread.eta_at(ens, home, fallback_radius_m=200.0)
    assert eta.eta_p10_s == T0 + 5 * HOUR
    land = [UNREACHED] * n
    land[5 * 11 + 6] = T0 + HOUR                # 1 cell east
    land[0] = T0 + 0.5 * HOUR                   # far corner: out of reach
    spread.attach_ember_landings(ens, Raster(spec, land))
    eta = spread.eta_at(ens, home, fallback_radius_m=200.0)
    assert (eta.eta_p10_s, eta.eta_p50_s, eta.eta_p90_s) == (T0 + HOUR,) * 3
    # the live path (radius 0) never reads the landings
    assert math.isinf(spread.eta_at(ens, home).eta_p10_s)
    # a reached home keeps its own cell's arrival
    burn = geo.rc_to_latlon(spec, 5, 7)
    assert spread.eta_at(ens, burn, 200.0).eta_p10_s == T0 + 5 * HOUR
    with pytest.raises(ValueError):
        other = Raster(GridSpec(north_lat=40.0, west_lon=-121.5,
                                cell_m=90.0, nrows=11, ncols=11), land)
        spread.attach_ember_landings(ens, other)


def _vail_canonical(cfg):
    if not simui.tile_file("vail_co").exists():
        pytest.skip("LOUD SKIP: vail_co tile not cached")
    mgr = server.SimSessionManager()
    code, out = mgr.new_session({"scenario": "vail_westerly", "cfg": cfg})
    assert code == 200, out
    return mgr._entry(out["session_id"]).session


def test_off_attaches_nothing_and_on_keeps_the_history_across_a_reanchor():
    """Off: the field carries no landing raster. On: the first field's
    landings are attached; a world change (the wind dial, same wind)
    re-anchors the field — every ember thrown before the re-anchor stays in
    the history, and the new field's landing raster holds them."""
    s = _vail_canonical({"ember_eta_landings": False})
    s.tick()
    assert getattr(s._fires["sim-f1"].ens, spread._LANDING_ATTR,
                   None) is None
    s = _vail_canonical({})
    for _ in range(12):                       # a 3-h fire throws embers
        s.tick()
    f = s._fires["sim-f1"]
    land = getattr(f.ens, spread._LANDING_ATTR)
    assert f._land_hist and land is not None
    before = set(f._land_hist)
    now = s._now_s
    s.set_wind(f.wind_dir_deg)
    s.tick()
    assert f.anchor_t0_s == now                   # re-anchored at the step
    kept = {e for e in before if e[1] <= now}
    assert kept and kept <= set(f._land_hist)
    land = getattr(f.ens, spread._LANDING_ATTR)
    for i, _thrown, t in kept:
        assert land.data[int(i)] <= t


def _harness(run_id, cfg):
    import os
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, "..", "scratch_review",
                                    "eagle_valley"))
    try:
        import run_matrix as rm
    finally:
        sys.path.pop(0)
    loc, ign, inten = run_id.split("|")
    if not simui.tile_file(loc).exists():
        pytest.skip(f"LOUD SKIP: {loc} tile not cached")
    _mgr, s = rm.build(loc, ign, inten, cfg)
    return s


def test_a_developed_home_near_an_ember_gets_an_eta_sooner():
    """The valley harness's Vail northwest fire at extreme, its first field:
    four homes on developed cells have an ember landing within 200 m before
    the pessimistic front comes within 200 m — sim-c9, c12 and c19 get their
    ETA 2.4, 0.6 and 7.8 h sooner, and sim-c22, which the front never comes
    near inside the horizon, gets one at all; no home's ETA is later."""
    off = _harness("vail_co|northwest|extreme", {"ember_eta_landings": False})
    on = _harness("vail_co|northwest|extreme", {})
    off.tick()
    on.tick()
    earlier = {}
    for cid, e_on in on._client_etas.items():
        e_off = off._client_etas[cid]
        assert e_on.eta_p10_s <= e_off.eta_p10_s
        assert e_on.eta_p10_s <= e_on.eta_p50_s <= e_on.eta_p90_s
        if e_on.eta_p10_s < e_off.eta_p10_s:
            earlier[cid] = (e_off.eta_p10_s - e_on.eta_p10_s) / 3600.0
    assert sorted(earlier) == ["sim-c12", "sim-c19", "sim-c22", "sim-c9"]
    assert math.isinf(earlier["sim-c22"])
    assert math.isfinite(on._client_etas["sim-c22"].eta_p10_s)
    assert earlier["sim-c9"] == pytest.approx(2.36, abs=0.01)
