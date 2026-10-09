"""tests/test_spot_fire_size.py — EMBERS FLY AS FAR AS THE FIRE THAT THROWS
THEM (contracts SAFETY SEMANTICS 19; cfg.spot_fire_size, default OFF).

Founder 2026-10-09: "begin the calibration of the ember distance and size of
fire calibration." Measured on the Park City one-click scenario
(docs/review/ember_spotting_calibration_2026-10-09.md): the legacy SEMANTICS 11
spotting throws every burning cell's ember from the full Sardoy lognormal from
the first minute of a fire's life, and a cell an ember ignites throws its own
ember the moment it burns, so a fire of a few acres leap-frogs kilometres in
minutes (a 6-hop spot-from-spot chain covered 3.2 km in ~7 minutes, 12 of 18
burned cells spot-ignited). The rule, armed by cfg.spot_fire_size:
  * FIRE SIZE: a burning cell's fire is the connected burned area it joins
    when it ignites (8-neighbour connectivity; kept / perimeter cells count);
    a spot fire is a new, small fire until it joins another;
  * REACH: the ember (the unchanged legacy draw, same seed) flies at most
    Albini's (1983) maximum spotting distance from a wind-driven surface fire
    of the cell's Byram intensity in the 20-ft wind, times
    (fire size / cfg.spot_fire_size_ref_ha) ** cfg.spot_fire_size_exp
    (Storey et al. 2020's source-fire-area scaling); a longer draw burns out
    in flight;
  * DELAY: a spot fire starts cfg.spot_ignition_delay_min after its ember
    lands.
Pinned here: OFF is byte-identical (goldens computed on commit 05cf1d6,
before the rule existed, on worlds where it bites: a high-wind corridor, a
mixed-fuel tile with a perimeter, a re-anchor with embers aloft, an ensemble
and the committed Park City tile); ON obeys the reach / size / delay law
ember by ember (spot_trace), is deterministic, member-seeded and never
wall-clock, keeps the ROS memo exact and the re-anchor continuous, and stops
the young-fire leap-frog on the real tile.
"""
from __future__ import annotations

import bisect
import dataclasses
import hashlib
import math
import struct

import pytest

from wgresponse import geo, spread
from wgresponse.contracts import (GridSpec, LatLon, ResponseConfig,
                                  TerrainTile, WeatherForecast, WeatherSample,
                                  validate_config)

T0 = 1_750_000_000.0
NORTH = 39.5
WEST = -121.5
HOUR = 3600.0


def _fp(values) -> str:
    """SHA-256 over little-endian float64 values (byte identity)."""
    return hashlib.sha256(
        b"".join(struct.pack("<d", v) for v in values)).hexdigest()


def _wx(loc, wind_mps, wind_dir_deg, rh_pct, temp_c, hours=120, t0=T0):
    return WeatherForecast(loc=loc, source="test", samples=[
        WeatherSample(t_s=t0 + i * HOUR, wind_speed_mps=wind_mps,
                      wind_gust_mps=wind_mps * 1.3, wind_dir_deg=wind_dir_deg,
                      rh_pct=rh_pct, temp_c=temp_c) for i in range(hours)])


def _corridor():
    """Flat GR2 downwind corridor (21 x 140 cells of 90 m), 25 m/s FROM 270:
    the high-wind, high-intensity geometry where ember jumps dominate."""
    spec = GridSpec(north_lat=NORTH, west_lon=WEST, cell_m=90.0,
                    nrows=21, ncols=140)
    tile = TerrainTile(
        spec=spec, fuel=geo.make_raster(spec, fill=102.0),
        slope_deg=geo.make_raster(spec, fill=0.0),
        aspect_deg=geo.make_raster(spec, fill=-1.0),
        elev_m=geo.make_raster(spec, fill=100.0), source="test-sz-corridor")
    ign = geo.rc_to_latlon(spec, 10, 5)
    return tile, ign, _wx(ign, 25.0, 270.0, 8.0, 38.0)


def _mixed():
    """61 x 61 cells of 90 m: diagonal bands of GR2 / SH5 / TU5 / TL3 / GS2
    with a 2-cell NB road and gentle terrain, 18 m/s FROM 225 — intensities
    from litter to chaparral under one wind."""
    spec = GridSpec(north_lat=NORTH, west_lon=WEST, cell_m=90.0,
                    nrows=61, ncols=61)
    bands = (102.0, 145.0, 165.0, 183.0, 122.0)
    fuel = geo.make_raster(spec, fill=102.0)
    slope = geo.make_raster(spec, fill=0.0)
    aspect = geo.make_raster(spec, fill=-1.0)
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            fuel.set(r, c, bands[((r + c) // 7) % len(bands)])
            slope.set(r, c, float((r * 7 + c * 3) % 23))
            aspect.set(r, c, float((r * 37 + c * 11) % 360))
    for r in range(spec.nrows):
        fuel.set(r, 38, 91.0)
        fuel.set(r, 39, 91.0)
    tile = TerrainTile(spec=spec, fuel=fuel, slope_deg=slope,
                       aspect_deg=aspect,
                       elev_m=geo.make_raster(spec, fill=1500.0),
                       source="test-sz-mixed")
    ign = geo.rc_to_latlon(spec, 50, 10)
    return tile, ign, _wx(ign, 18.0, 225.0, 6.0, 39.0)


def _ring(center: LatLon, half_m: float) -> list:
    """Square ring of 4 x 6 vertices around `center`."""
    nw = geo.destination(geo.destination(center, 0.0, half_m), 270.0, half_m)
    pts = []
    for brg in (90.0, 180.0, 270.0, 0.0):
        for k in range(6):
            pts.append(geo.destination(nw, brg, k * 2.0 * half_m / 6.0))
        nw = geo.destination(nw, brg, 2.0 * half_m)
    return pts


#: the committed Park City tile, the scenario's ignition cell (the server's
#: search for park_city_windstorm) and the "extreme" preset's numbers spelled
#: out (40 mph sustained x 1.3 gusts, 6 % RH, 39 C, FROM 225) — explicit here
#: so a weather-preset change elsewhere cannot move these pins.
PARK_CITY_IGN = LatLon(40.67547104361434, -111.60869369341107)


def _park_city():
    from wgresponse.simui import load_tile
    tile, synthetic = load_tile("park_city_ut")
    if synthetic:
        pytest.skip("committed tile data/tiles/park_city_ut.wgt not present")
    return tile, PARK_CITY_IGN, _wx(PARK_CITY_IGN, 17.8816, 225.0, 6.0, 39.0,
                                    hours=97)


def _cfg(**kw) -> ResponseConfig:
    kw.setdefault("ensemble_n", 1)
    kw.setdefault("horizon_h", 24.0)
    kw.setdefault("spotting_enabled", True)
    return ResponseConfig(**kw)


def _reanchor(tile, ign, wx, cfg, t_a, incident_id="sz-re"):
    """(continuous field, re-anchored field at t_a): the simulator's
    progress-preserving re-anchor under unchanged weather (kept + kept_wx +
    the embers still aloft from the continuous run's ember_log)."""
    log = []
    cont = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                  incident_id=incident_id, ember_log=log)
    kept = {i: v for i, v in enumerate(cont.data) if v <= t_a}
    aloft = sorted({(i, land) for i, thrown, land in log
                    if thrown <= t_a < land and i not in kept})
    re = spread.compute_arrival(tile, [], wx, t_a, cfg,
                                incident_id=incident_id, seed_spotting=False,
                                kept=kept, kept_wx=[(T0, wx.at(T0))],
                                embers_aloft=aloft)
    return cont, re


# ---------------------------------------------------------------------------
# golden worlds (spotting ON, the rule OFF) — fingerprints computed on
# commit 05cf1d6 with this module's builders, before SEMANTICS 19 existed
# (worktree HEAD 05cf1d6, spread.py unmodified; 1.3 s on the dev box)
# ---------------------------------------------------------------------------

GOLDEN = {
    "corridor": "31e77a561a7ae74490b69f17bfdf0ae95e19832c8d1f17c49ec89e534bc71531",
    "corridor_reanchor": "15406c0eee33dfbb77c9cf864f6e59b2e50cd6cc7ab680ebc2050d341d659aae",
    "mixed": "b05104538d262f3f8ad184491d598f1c3d3aa63e88f2ecc2ed7ada7f17ee5155",
    "mixed_perimeter": "ea1f069c2f58f86eada41532ba13257f8117681ed6c3c6d783ab42324794a179",
    "mixed_ensemble": "c2f348317a9acd2fbda284e36a056451cc2c9fbec78093af7607c9498960087d",
    "park_city_grass_only": "286a4a6e5fc99dbd3e38941349029c19cbeff62fb553da65ec7637a201d4e08c",
    "park_city_as_deployed": "d11c7a3637884b7e6815a3ed0a471efdf1ada30c5b6884bcddc9c49a71ef1f12",
}


def golden_fields(**extra) -> dict:
    """name -> list of floats whose fingerprint is pinned (the rule OFF;
    `extra` cfg fields ride on every run)."""
    out = {}
    tile, ign, wx = _corridor()
    out["corridor"] = spread.compute_arrival(
        tile, [ign], wx, T0, _cfg(**extra), incident_id="sz-corridor").data
    cont, re = _reanchor(tile, ign, wx, _cfg(**extra), T0 + HOUR)
    out["corridor_reanchor"] = re.data
    tile, ign, wx = _mixed()
    out["mixed"] = spread.compute_arrival(
        tile, [ign], wx, T0, _cfg(**extra), incident_id="sz-mixed").data
    ring = _ring(geo.rc_to_latlon(tile.spec, 45, 15), 400.0)
    out["mixed_perimeter"] = spread.compute_arrival(
        tile, [ign], wx, T0, _cfg(**extra), incident_id="sz-perim",
        perimeter=[ring], perimeter_at_s=T0 - 2 * HOUR).data
    ens = spread.ensemble_arrival(tile, [ign], wx, T0,
                                  _cfg(ensemble_n=4, **extra),
                                  incident_id="sz-ens")
    out["mixed_ensemble"] = ([v for m in ens.members for v in m.data]
                             + ens.p10.data + ens.p50.data + ens.p90.data)
    return out


def golden_real_fields(**extra) -> dict:
    """The committed Park City tile, page physics (floor 0.10), both floor
    class sets — the rule OFF."""
    tile, ign, wx = _park_city()
    out = {}
    for name, grass_only in (("park_city_grass_only", True),
                             ("park_city_as_deployed", False)):
        cfg = _cfg(horizon_h=96.0, grass_ros_floor_frac=0.10,
                   ros_floor_grass_only=grass_only, **extra)
        out[name] = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                           incident_id="sim-f1").data
    return out


# ---------------------------------------------------------------------------
# OFF = byte-identical
# ---------------------------------------------------------------------------

def test_knob_defaults_off_and_valid():
    cfg = ResponseConfig()
    assert cfg.spot_fire_size is False
    assert cfg.spot_fire_size_ref_ha > 0.0
    assert 0.0 < cfg.spot_fire_size_exp <= 2.0
    assert 0.0 <= cfg.spot_ignition_delay_min <= 120.0
    assert validate_config(cfg) == []


def test_off_is_byte_identical_to_goldens():
    """The rule OFF (the default) moves no bit of any golden world — spotting
    ON everywhere, so every SEMANTICS 19 branch would bite if it ran."""
    got = golden_fields()
    got.update(golden_real_fields())
    for name, values in got.items():
        assert _fp(values) == GOLDEN[name], name


def test_off_parameters_are_inert():
    """With the master OFF the three parameters are never read: extreme
    values leave every golden untouched (and a spot_trace changes nothing)."""
    extra = {"spot_fire_size": False, "spot_fire_size_ref_ha": 1.0,
             "spot_fire_size_exp": 2.0, "spot_ignition_delay_min": 120.0}
    got = golden_fields(**extra)
    for name, values in got.items():
        assert _fp(values) == GOLDEN[name], name
    tile, ign, wx = _corridor()
    trace = []
    arr = spread.compute_arrival(tile, [ign], wx, T0, _cfg(),
                                 incident_id="sz-corridor", spot_trace=trace)
    assert _fp(arr.data) == GOLDEN["corridor"]
    assert trace and all(row[5] == math.inf and row[6] == 0 for row in trace)


def test_spotting_off_makes_the_rule_inert():
    """SEMANTICS 19 lives inside SEMANTICS 11: with spotting disabled the
    armed rule is a no-op (no ember, no union-find, no trace)."""
    tile, ign, wx = _mixed()
    base = spread.compute_arrival(tile, [ign], wx, T0,
                                  _cfg(spotting_enabled=False))
    trace = []
    armed = spread.compute_arrival(
        tile, [ign], wx, T0, _cfg(spotting_enabled=False, spot_fire_size=True),
        spot_trace=trace)
    assert armed.data == base.data
    assert trace == []


# ---------------------------------------------------------------------------
# Albini's surface-fire maximum
# ---------------------------------------------------------------------------

def test_albini_surface_maximum_reference_values():
    """BehavePlus surface-fire spotting (Albini 1983; Morris 1987 eq. 3),
    open flat ground, hand-computed: 1 MW/m in a 15.55 m/s 20-ft wind (the
    40 mph 10-m "extreme" preset) lofts brands to ~78 ft and reaches
    0.4724 mi = 760 m; 10 MW/m ~1.6 km; 60 MW/m ~2.8 km."""
    alb = spread._albini_surface_max_spot_m
    assert alb(1000.0, 15.55) == pytest.approx(760.3, rel=2e-3)
    assert alb(10_000.0, 15.55) == pytest.approx(1595.0, rel=5e-3)
    assert alb(60_000.0, 15.55) == pytest.approx(2840.0, rel=1e-2)
    # nothing lofted: no fire, no wind, or a fire too weak for the fit
    assert alb(0.0, 15.55) == 0.0
    assert alb(1000.0, 0.0) == 0.0
    assert alb(1.0, 15.55) == 0.0
    # monotone in intensity and in wind over the operational range
    vals = [alb(i, 10.0) for i in (100.0, 300.0, 1000.0, 3000.0, 10_000.0)]
    assert vals == sorted(vals) and vals[0] > 0.0
    vals = [alb(3000.0, u) for u in (3.0, 6.0, 10.0, 15.0, 20.0)]
    assert vals == sorted(vals)


def test_spot_reach_scales_with_fire_size():
    """REACH = Albini x (fire / reference) ** exponent: a fire at the
    reference size reaches exactly Albini's maximum, a tenth of it
    10 ** -0.43 = 0.37 of it, a single 90 m cell (0.81 ha) far less."""
    cfg = ResponseConfig(spot_fire_size=True, spot_fire_size_ref_ha=300.0,
                         spot_fire_size_exp=0.43)
    alb = spread._albini_surface_max_spot_m(5000.0, 15.55)
    assert spread.spot_reach_m(5000.0, 15.55, 300.0, cfg) == alb
    assert spread.spot_reach_m(5000.0, 15.55, 30.0, cfg) == \
        pytest.approx(alb * 10 ** -0.43)
    one_cell = spread.spot_reach_m(5000.0, 15.55, 0.81, cfg)
    assert one_cell < 0.1 * alb
    assert spread.spot_reach_m(5000.0, 15.55, 3000.0, cfg) > alb


# ---------------------------------------------------------------------------
# ON: the law, ember by ember
# ---------------------------------------------------------------------------

LAW = {"spot_fire_size": True, "spot_fire_size_ref_ha": 300.0,
       "spot_fire_size_exp": 0.43, "spot_ignition_delay_min": 5.0}


def _intensity(mean_m, u20):
    return (mean_m / (spread._SPOT_A * u20 ** spread._SPOT_WS_EXP)) \
        ** (1.0 / spread._SPOT_FLIN_EXP)


def _fire_sizes_replay(data, spec):
    """Cell -> cell count of its 8-connected burned area when it ignites,
    replayed over the FINAL field in (arrival, index) order (an independent
    implementation of the engine's union-find)."""
    nc = spec.ncols
    comp = {}
    members = {}
    out = {}
    for _v, i in sorted((v, i) for i, v in enumerate(data)
                        if math.isfinite(v)):
        r0, c0 = divmod(i, nc)
        roots = set()
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                rr, cc = r0 + dr, c0 + dc
                if (dr or dc) and 0 <= rr < spec.nrows and 0 <= cc < nc \
                        and rr * nc + cc in comp:
                    roots.add(comp[rr * nc + cc])
        new = {i}
        for rt in roots:
            new |= members.pop(rt)
        for j in new:
            comp[j] = i
        members[i] = new
        out[i] = len(new)
    return out


def test_law_holds_for_every_ember_on_the_corridor():
    """Every ember the armed engine throws obeys SEMANTICS 19: its fire size
    is the connected burned area it joined (checked against an independent
    replay), its reach is spot_reach_m of its intensity, wind and size, a
    draw beyond the reach never lands, a draw within it lands (on the grid,
    at least a cell away), and a landed ember ignites delay minutes after
    its flight."""
    tile, ign, wx = _corridor()
    cfg = _cfg(**LAW)
    trace = []
    data = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                  incident_id="sz-law", spot_trace=trace).data
    spec = tile.spec
    cell_ha = spec.cell_m ** 2 / 1e4
    sizes = _fire_sizes_replay(data, spec)
    landed = burnt_out = 0
    for (idx, t_u, mean_m, u20, drawn, reach, cells, land, t_v) in trace:
        assert t_u == data[idx]
        assert cells == sizes[idx]
        want = spread.spot_reach_m(_intensity(mean_m, u20), u20,
                                   cells * cell_ha, cfg)
        assert reach == pytest.approx(want, rel=1e-9)
        if drawn > reach:
            assert land == -1 and t_v == math.inf
            burnt_out += 1
        elif drawn >= spec.cell_m:
            # wind FROM 270: the ember flies due east (off the grid: none)
            rc = geo.latlon_to_rc(spec, geo.destination(
                geo.rc_to_latlon(spec, *divmod(idx, spec.ncols)), 90.0,
                drawn))
            if rc is None:
                assert land == -1
                continue
            assert land == rc[0] * spec.ncols + rc[1]
            assert t_v == (t_u + drawn / max(u20, 0.1)) + 5.0 * 60.0
            landed += 1
        else:
            assert land == -1                    # sub-cell: a self-cell no-op
    assert landed > 50 and burnt_out > 50, (landed, burnt_out)


def test_young_fire_cannot_leapfrog_the_corridor():
    """The legacy engine spots kilometres from a fire of a few hectares in
    its first minutes; armed, an ember thrown by a fire under 10 ha lands
    within a few hundred metres, and isolated spot fires (a single cell)
    throw no ember farther than one cell."""
    tile, ign, wx = _corridor()
    spec = tile.spec
    cell_ha = spec.cell_m ** 2 / 1e4

    def spots(cfg):
        trace = []
        data = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                      incident_id="sz-young",
                                      spot_trace=trace).data
        sizes = _fire_sizes_replay(data, spec)
        out = []
        for (idx, t_u, _m, _u, drawn, _reach, _c, land, t_v) in trace:
            if land >= 0 and data[land] == t_v:
                out.append((sizes[idx] * cell_ha, drawn, t_u - T0))
        return out

    legacy = spots(_cfg())
    law = spots(_cfg(**LAW))
    small_legacy = [d for ha, d, _t in legacy if ha < 10.0]
    small_law = [d for ha, d, _t in law if ha < 10.0]
    assert max(small_legacy) > 1000.0, max(small_legacy)
    assert not small_law or max(small_law) < 600.0, max(small_law)
    single_law = [d for ha, d, _t in law if ha < 1.0]
    assert not single_law or max(single_law) < 2 * spec.cell_m
    # the first spot beyond 1 km waits for the fire to grow
    first_km = min((t for _ha, d, t in law if d > 1000.0), default=math.inf)
    first_km_legacy = min(t for _ha, d, t in legacy if d > 1000.0)
    assert first_km > first_km_legacy + 15 * 60.0


def test_law_is_deterministic_member_seeded_and_never_wall_clock():
    tile, ign, wx = _mixed()
    cfg = _cfg(**LAW)
    a = spread.compute_arrival(tile, [ign], wx, T0, cfg, incident_id="sz-A")
    b = spread.compute_arrival(tile, [ign], wx, T0, cfg, incident_id="sz-A")
    assert a.data == b.data
    tile_c, ign_c, wx_c = _corridor()
    base = spread.compute_arrival(tile_c, [ign_c], wx_c, T0, cfg,
                                  incident_id="sz-A")
    other = spread.compute_arrival(tile_c, [ign_c], wx_c, T0, cfg,
                                   incident_id="sz-B")
    member = spread.compute_arrival(tile_c, [ign_c], wx_c, T0, cfg,
                                    incident_id="sz-A", member_k=3)
    assert base.data != other.data and base.data != member.data
    shifted = spread.compute_arrival(
        tile_c, [ign_c], _wx(ign_c, 25.0, 270.0, 8.0, 38.0, t0=T0 + 900.0),
        T0 + 900.0, cfg, incident_id="sz-A")
    pairs = [(x, y) for x, y in zip(base.data, shifted.data)
             if math.isfinite(x)]
    assert len(pairs) > 100
    for x, y in pairs:
        assert y - x == pytest.approx(900.0, abs=1e-6)


@pytest.mark.parametrize("world", ["corridor", "mixed"])
def test_law_keeps_the_ros_memo_exact(world, monkeypatch):
    """Albini's maximum rides in the memoised spot tuple and the size is
    applied at the teleport-edge site, so the memo and the
    WGRESP_NO_ROS_MEMO=1 reference path stay byte-identical with the rule
    armed (rotation and a stale perimeter included)."""
    tile, ign, wx = _corridor() if world == "corridor" else _mixed()
    cfg = _cfg(**LAW)
    kw = [{}, {"dir_offset_deg": 17.3}]
    if world == "mixed":
        kw.append({"perimeter": [_ring(geo.rc_to_latlon(tile.spec, 45, 15),
                                       400.0)],
                   "perimeter_at_s": T0 - 2 * HOUR})
    for k in kw:
        monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
        memo = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                      incident_id="sz-memo", **k)
        monkeypatch.setenv("WGRESP_NO_ROS_MEMO", "1")
        ref = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                     incident_id="sz-memo", **k)
        monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
        assert _fp(memo.data) == _fp(ref.data), k
        assert sum(1 for v in memo.data if math.isfinite(v)) > 100


def test_law_keeps_a_noop_reanchor_continuous():
    """RE-ANCHOR HISTORY under the rule: the kept burn joins its fires first
    and the embers aloft carry their delayed landing, so a same-weather
    re-anchor reproduces the continuous run cell for cell (below the
    continuous run's horizon)."""
    tile, ign, wx = _corridor()
    cfg = _cfg(**LAW)
    for t_a in (T0 + 0.5 * HOUR, T0 + 2 * HOUR):
        cont, re = _reanchor(tile, ign, wx, cfg, t_a)
        horizon = T0 + cfg.horizon_h * HOUR
        diff = [i for i, (x, y) in enumerate(zip(cont.data, re.data))
                if (x <= horizon or y <= horizon) and x != y]
        assert diff == [], (t_a - T0, len(diff))
        assert sum(1 for v in cont.data if v <= t_a) > 10


def test_law_feeds_the_ensemble_in_order():
    tile, ign, wx = _mixed()
    ens = spread.ensemble_arrival(tile, [ign], wx, T0,
                                  _cfg(ensemble_n=4, **LAW),
                                  incident_id="sz-ens")
    for x, y, z in zip(ens.p10.data, ens.p50.data, ens.p90.data):
        assert x <= y <= z
    again = spread.ensemble_arrival(tile, [ign], wx, T0,
                                    _cfg(ensemble_n=4, **LAW),
                                    incident_id="sz-ens")
    assert [m.data for m in ens.members] == [m.data for m in again.members]


def test_law_ignition_delay_lets_the_front_overrun_spots():
    """A longer ignition delay can only make spot fires start later: the
    armed field with a 10-minute delay is never earlier than with none
    where the ember draws are the same (they are — same seeds, same
    reach — until the fields diverge), and burns no more by 2 h."""
    tile, ign, wx = _corridor()
    burned = {}
    for d in (0.0, 10.0):
        cfg = _cfg(**dict(LAW, spot_ignition_delay_min=d))
        data = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                      incident_id="sz-delay").data
        burned[d] = sum(1 for v in data if v <= T0 + 2 * HOUR)
    assert burned[10.0] <= burned[0.0]


# ---------------------------------------------------------------------------
# config law
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field,bad", [
    ("spot_fire_size_ref_ha", 0.0), ("spot_fire_size_ref_ha", -5.0),
    ("spot_fire_size_ref_ha", math.inf), ("spot_fire_size_ref_ha", math.nan),
    ("spot_fire_size_exp", 0.0), ("spot_fire_size_exp", 2.5),
    ("spot_fire_size_exp", math.nan),
    ("spot_ignition_delay_min", -1.0), ("spot_ignition_delay_min", 121.0),
    ("spot_ignition_delay_min", math.nan),
])
def test_validate_config_bounds_the_rule(field, bad):
    cfg = dataclasses.replace(ResponseConfig(), **{field: bad})
    assert any(field in p for p in validate_config(cfg)), field


def test_validate_config_types_the_master():
    cfg = dataclasses.replace(ResponseConfig(), spot_fire_size=1)
    assert any("spot_fire_size must be a bool" in p
               for p in validate_config(cfg))


# ---------------------------------------------------------------------------
# the committed tiles: the founder's scenarios
# ---------------------------------------------------------------------------

#: the boulder_chinook one-click ignition cell (the server's search) and the
#: "extreme" preset's numbers spelled out, wind FROM 270 (Marshall-style).
BOULDER_IGN = LatLon(39.95497501821048, -105.23625840685632)


def test_real_tiles_young_fire_spotting():
    """Boulder (grass) under the extreme preset: the legacy engine spots
    ~1.6 km within the first minute from a 2.4 ha fire; armed, no spot
    ignition lands farther than 600 m while the whole fire is under 10 ha,
    nor farther than 1 km while it is under 50 ha."""
    from wgresponse.simui import load_tile
    tile, synthetic = load_tile("boulder_co")
    if synthetic:
        pytest.skip("committed tile data/tiles/boulder_co.wgt not present")
    wx = _wx(BOULDER_IGN, 17.8816, 270.0, 6.0, 39.0, hours=97)
    spec = tile.spec
    cell_ha = spec.cell_m ** 2 / 1e4

    def spots(extra):
        trace = []
        cfg = _cfg(horizon_h=96.0, grass_ros_floor_frac=0.10,
                   ros_floor_grass_only=True, **extra)
        data = spread.compute_arrival(tile, [BOULDER_IGN], wx, T0, cfg,
                                      incident_id="sim-f1",
                                      spot_trace=trace).data
        order = sorted(v for v in data if math.isfinite(v))
        out = []
        for (idx, t_u, _m, _u, drawn, _r, _c, land, t_v) in trace:
            if land >= 0 and data[land] == t_v:
                n = bisect.bisect_right(order, t_u)
                out.append((n * cell_ha, drawn))
        return out

    legacy = spots({})
    law = spots(LAW)
    assert max(d for ha, d in legacy if ha < 10.0) > 1000.0
    assert max((d for ha, d in law if ha < 10.0), default=0.0) < 600.0
    assert max((d for ha, d in law if ha < 50.0), default=0.0) < 1000.0
    assert max(d for _ha, d in law) > 1000.0     # big fires still spot far

