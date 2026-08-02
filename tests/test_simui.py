"""tests/test_simui.py — SIM-2: SimSession interactive-simulator engine harness.

Logic tests run on SYNTHETIC tiles only (tile_dir points at a tmp dir, so the
concurrently-fetched real tiles under data/tiles/ can never change results or
runtimes) and with roads PINNED OFF (roads_module=None) unless the test is
about roads — the same isolation discipline, so the concurrently-built
wgresponse.roads + data/roads/ caches can never change pinned kinematics.
Road behavior is tested against an in-test fake conforming exactly to the
contracts wgresponse.roads seam, plus conditional-skip integration tests
that use the real module/graphs when they land. Everything is deterministic
(contracts.seeded_rng; fixed sim epoch).
"""
from __future__ import annotations

import hashlib
import json
import math
import re

import pytest

from wgresponse import geo
from wgresponse.contracts import GridSpec, LatLon
from wgresponse.simui import (
    DEFAULT_TILE_DIR,
    DEVELOPED_CODES,
    EXT_MIN_HORIZON_H,
    GUST_FACTOR,
    LOCATIONS,
    MPH_TO_MPS,
    OUTCOME_TAGS,
    SIM_EMBER_THREAT_RADIUS_M,
    SYNTHETIC_BANNER,
    SimSession,
    _client_outcome,
    _threshold_rings,
    central_window_bbox,
    load_tile,
    place_clients,
    wui_cells,
)
from wgresponse.terrain import SyntheticTerrain, save_ascii_tile

# Fast-engine overrides shared by the scripted-session tests: small ensemble,
# coarse grid, short horizon — config only, never forked physics.
FAST = {"ensemble_n": 3, "grid_cell_m": 500.0, "horizon_h": 24.0}


def _controlled_tile_dir(tmp_path, key, size_km=20.0, cell_m=500.0,
                         hamlet_half=2):
    """Write a controlled uniform-grass tile with a POROUS (checkerboard)
    developed hamlet at the grid center as a data/tiles-style <key>.wgt in a
    tmp dir, so engine tests have exact, tile-agent-independent geometry.
    The checkerboard matters: a solid urban block is a fire break the spread
    model must flank around for hours; interleaved homes/fuel let the head
    fire run through the interface the way real intermix WUI burns."""
    loc = LOCATIONS[key]
    bbox = geo.bbox_around([loc.center], pad_m=size_km * 500.0)
    tile = SyntheticTerrain(seed=f"simui-test|{key}", kind="grass").tile(bbox, cell_m)
    spec = tile.spec
    r0 = spec.nrows // 2 - hamlet_half
    c0 = spec.ncols // 2 - hamlet_half
    for r in range(r0, r0 + 2 * hamlet_half):
        for c in range(c0, c0 + 2 * hamlet_half):
            if (r + c) % 2 == 0:
                tile.fuel.set(r, c, 91.0)
    d = tmp_path / f"tiles_{key}"
    d.mkdir(exist_ok=True)
    save_ascii_tile(tile, str(d / f"{key}.wgt"))
    return d


def _upwind_ignition(key, dist_m):
    """Ignition point dist_m upwind (toward where the hazard wind blows FROM)
    of the location center — the fire then runs back across the center."""
    loc = LOCATIONS[key]
    return geo.destination(loc.center, loc.hazard_wind_dir_deg, dist_m)


def _state_hash(state):
    """JSON-safety guard + deterministic digest of one state snapshot."""
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _burnable_point(session):
    """First burnable cell center (row-major scan) of the session tile —
    a deterministic always-valid ignition point."""
    from wgresponse.fuels import is_burnable
    tile = session._tile
    spec = tile.spec
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            if is_burnable(int(round(tile.fuel.get(r, c)))):
                return geo.rc_to_latlon(spec, r, c)
    raise AssertionError("tile has no burnable cell")


# ---------------------------------------------------------------------------
# LOCATIONS registry
# ---------------------------------------------------------------------------

def test_locations_registry_shape():
    assert sorted(LOCATIONS) == ["boulder_co", "malibu_ca", "paradise_ca",
                                 "park_city_ut", "rancho_santa_fe_ca",
                                 "santa_rosa_ca"]
    for key, loc in LOCATIONS.items():
        assert loc.key == key
        assert loc.tile_path == f"data/tiles/{key}.wgt"
        assert 0.0 <= loc.hazard_wind_dir_deg < 360.0
        assert 2 <= len(loc.safe_zones) <= 3
        for z in loc.safe_zones:
            d_km = geo.haversine_m(loc.center, z.loc) / 1000.0
            assert 3.0 <= d_km <= 13.0, (key, z.zone_id, d_km)


def test_design_doc_centers_pinned():
    assert LOCATIONS["malibu_ca"].center == LatLon(34.055, -118.72)
    assert LOCATIONS["paradise_ca"].center == LatLon(39.775, -121.60)
    assert LOCATIONS["santa_rosa_ca"].center == LatLon(38.50, -122.65)
    assert LOCATIONS["boulder_co"].center == LatLon(39.955, -105.16)
    assert LOCATIONS["park_city_ut"].center == LatLon(40.72, -111.55)
    assert LOCATIONS["boulder_co"].hazard_wind_dir_deg == 270.0  # Marshall W
    assert LOCATIONS["rancho_santa_fe_ca"].center == LatLon(33.020, -117.200)
    assert LOCATIONS["rancho_santa_fe_ca"].hazard_wind_dir_deg == 60.0  # Witch Creek NE


# ---------------------------------------------------------------------------
# Tiles: synthetic fallback + cached-file path
# ---------------------------------------------------------------------------

def test_load_tile_synthetic_fallback_flagged(tmp_path):
    tile, synthetic = load_tile("malibu_ca", cell_m=500.0, tile_dir=tmp_path,
                                synth_size_km=6.0)
    assert synthetic is True
    assert tile.source.startswith("synthetic:")
    # the fallback must carry a WUI band (stamped developed hamlets)
    assert wui_cells(tile), "synthetic fallback has no WUI cells"


def test_load_tile_prefers_cached_file(tmp_path):
    d = _controlled_tile_dir(tmp_path, "boulder_co", size_km=6.0, cell_m=500.0)
    tile, synthetic = load_tile("boulder_co", cell_m=250.0, tile_dir=d)
    assert synthetic is False
    assert tile.spec.cell_m == 500.0          # the file's grid governs
    assert "simui-test" in tile.source          # our file, not the fallback


def test_session_synthetic_banner_in_state(tmp_path):
    s = SimSession("park_city_ut", 4, 1, seed="s", cfg_overrides=dict(FAST),
                   tile_dir=tmp_path, synth_size_km=6.0)
    st = s.state()
    assert st["terrain"]["synthetic"] is True
    assert st["terrain"]["banner"] == SYNTHETIC_BANNER
    assert SYNTHETIC_BANNER in st["notes"]


def test_session_real_tile_no_banner(tmp_path):
    d = _controlled_tile_dir(tmp_path, "boulder_co")
    s = SimSession("boulder_co", 4, 1, seed="s", cfg_overrides=dict(FAST),
                   tile_dir=d)
    st = s.state()
    assert st["terrain"]["synthetic"] is False
    assert st["terrain"]["banner"] == ""
    assert SYNTHETIC_BANNER not in st["notes"]


# ---------------------------------------------------------------------------
# WUI band + placement
# ---------------------------------------------------------------------------

def test_wui_band_definition_exact():
    # 1x9 strip: burnable(102) x4, developed(91), burnable x4 -> the developed
    # cell + burnable cells within 1 of it are in-band; far cells are not.
    from wgresponse.contracts import BBox
    tile = SyntheticTerrain(seed="wui", kind="grass").tile(
        BBox(west=-105.0, south=39.99, east=-104.95, north=40.0), 400.0)
    spec = tile.spec
    mid_r, mid_c = spec.nrows // 2, spec.ncols // 2
    tile.fuel.set(mid_r, mid_c, 91.0)
    band = set(wui_cells(tile))
    assert (mid_r, mid_c) in band                        # developed near fuel
    assert (mid_r, mid_c + 1) in band                    # burnable within 1
    assert (mid_r + 1, mid_c + 1) in band                # diagonal within 1
    assert (mid_r, mid_c + 2) not in band                # burnable 2 away: out
    assert (0, 0) not in band


def test_placement_in_band_and_deterministic(tmp_path):
    tile, _ = load_tile("santa_rosa_ca", cell_m=300.0, tile_dir=tmp_path,
                        synth_size_km=6.0)
    band = set(wui_cells(tile))
    n = 40
    notes = []
    clients = place_clients(tile, n, seed="layout-1", notes=notes)
    assert len(clients) == n
    assert [c.client_id for c in clients] == [f"sim-c{k}" for k in range(1, n + 1)]
    in_band = 0
    for c in clients:
        rc = geo.latlon_to_rc(tile.spec, c.loc)
        assert rc is not None
        if rc in band:
            in_band += 1
        assert 600_000.0 <= c.value_usd <= 5_000_000.0
        assert c.graphgel_enrolled and c.priority_weight == 1.0
    assert in_band / n >= 0.95, f"only {in_band}/{n} clients in the WUI band"
    # distinct cells
    cells = {geo.latlon_to_rc(tile.spec, c.loc) for c in clients}
    assert len(cells) == n
    # determinism: same seed -> byte-identical layout; new seed -> different
    again = place_clients(tile, n, seed="layout-1")
    assert [(c.client_id, c.loc, c.value_usd) for c in again] == \
           [(c.client_id, c.loc, c.value_usd) for c in clients]
    other = place_clients(tile, n, seed="layout-2")
    assert [(c.loc, c.value_usd) for c in other] != \
           [(c.loc, c.value_usd) for c in clients]
    # values: lognormal around a ~$1.2M median (loose sanity band)
    med = sorted(c.value_usd for c in clients)[n // 2]
    assert 700_000.0 <= med <= 2_500_000.0


def test_placement_overflow_note(tmp_path):
    d = _controlled_tile_dir(tmp_path, "boulder_co", size_km=5.0, cell_m=500.0,
                             hamlet_half=1)
    tile, _ = load_tile("boulder_co", tile_dir=d)
    pool = len(wui_cells(tile))
    assert pool >= 4
    notes = []
    clients = place_clients(tile, pool + 3, seed="x", notes=notes)
    assert len(clients) == pool + 3
    assert any("burnable-adjacent" in n for n in notes)


# ---------------------------------------------------------------------------
# Intensity presets -> scripted MockWeather
# ---------------------------------------------------------------------------

def test_intensity_mapping_numbers(tmp_path):
    expected = {
        "low": (8.0 * MPH_TO_MPS, 35.0),
        "moderate": (18.0 * MPH_TO_MPS, 22.0),
        "high": (28.0 * MPH_TO_MPS, 12.0),
        "extreme": (40.0 * MPH_TO_MPS, 6.0),
    }
    assert expected["low"][0] == pytest.approx(3.57632, abs=1e-9)
    assert expected["extreme"][0] == pytest.approx(17.8816, abs=1e-9)
    s = SimSession("boulder_co", 2, 1, seed="wx", cfg_overrides=dict(FAST),
                   tile_dir=tmp_path, synth_size_km=6.0)
    pt = _burnable_point(s)
    for intensity, (wind, rh) in expected.items():
        s.ignite(pt.lat, pt.lon, intensity=intensity)
        for sample in (s._wx.samples[0], s._wx.samples[-1]):
            assert sample.wind_speed_mps == wind
            assert sample.wind_gust_mps == wind * GUST_FACTOR   # +30% gusts
            assert sample.rh_pct == rh
            assert sample.wind_dir_deg == LOCATIONS["boulder_co"].hazard_wind_dir_deg
    with pytest.raises(ValueError):
        s.ignite(pt.lat, pt.lon, intensity="apocalyptic")


def test_ignite_wind_override_and_outside_tile(tmp_path):
    s = SimSession("boulder_co", 2, 1, seed="w2", cfg_overrides=dict(FAST),
                   tile_dir=tmp_path, synth_size_km=6.0)
    pt = _burnable_point(s)
    s.ignite(pt.lat, pt.lon, intensity="low", wind_dir_deg=123.0)
    assert s._wx.samples[0].wind_dir_deg == 123.0
    assert s.state()["wind_dir_deg"] == 123.0
    with pytest.raises(ValueError):
        s.ignite(0.0, 0.0, intensity="low")      # off-tile: fail loud


# ---------------------------------------------------------------------------
# Scripted engine runs (REAL cycle path)
# ---------------------------------------------------------------------------

def _high_session(tmp_path, key="boulder_co", seed="run", buffer_min=None,
                  n_clients=8, n_crews=2):
    d = _controlled_tile_dir(tmp_path, key)
    cfg = dict(FAST)
    if buffer_min is not None:
        cfg["egress_buffer_min"] = float(buffer_min)
    # roads_module=None: kinematics-pinned logic tests stay straight-leg even
    # after wgresponse.roads + data/roads/ land (module docstring discipline)
    return SimSession(key, n_clients, n_crews, seed=seed, cfg_overrides=cfg,
                      tile_dir=d, roads_module=None)


def test_audit_zero_violations_scripted_12_ticks_high(tmp_path):
    s = _high_session(tmp_path, seed="audit")
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    s.set_speed(2.0)                       # 1800 s sim per tick
    for _ in range(12):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        mm = st["scoreboard"]["min_margin_h"]
        assert mm is None or mm >= 0.0
    assert st["scoreboard"]["served"] >= 1, "scenario should service someone"
    assert st["terrain"]["burned"]["runs"] and len(st["terrain"]["burned"]["runs"]) > 1
    assert st["tick"] == 12
    # every serviced stop departed >= buffer before the final p10 arrival
    for entry in st["audit"]:
        assert entry["violation"] is False


def test_buffer_monotonicity_property(tmp_path):
    """Raising the safety buffer must never increase the served count
    (acceptance 3) — property across seeds x locations, synthetic tiles."""
    served = {}
    for key in ("boulder_co", "santa_rosa_ca"):
        for seed in ("m1", "m2"):
            counts = {}
            for buf in (30.0, 90.0):
                s = _high_session(tmp_path, key=key, seed=seed, buffer_min=buf)
                ign = _upwind_ignition(key, 8_000.0)
                s.ignite(ign.lat, ign.lon, intensity="high")
                s.set_speed(2.0)
                for _ in range(3):
                    st = s.tick()
                    assert st["scoreboard"]["violations"] == 0
                counts[buf] = st["scoreboard"]["served"]
            served[(key, seed)] = counts
            assert counts[90.0] <= counts[30.0], (key, seed, counts)
    assert any(c[30.0] >= 1 for c in served.values()), \
        f"property vacuous — nothing served anywhere: {served}"


def test_buffer_binds_unserved_reasons(tmp_path):
    """The slider measurably changes dispatch semantics: with a 30-min buffer
    the marginal stop fails only the slack floor (infeasible_slack, SAFETY
    SEMANTICS 7); at 90 min the hard departure deadline itself fails
    (infeasible_deadline, SAFETY SEMANTICS 2). Queue-pressure scenario:
    16 clients, 1 crew."""
    reasons = {}
    for buf in (30.0, 90.0):
        cfg = dict(FAST)
        cfg["egress_buffer_min"] = buf
        d = _controlled_tile_dir(tmp_path, "boulder_co", hamlet_half=3)
        s = SimSession("boulder_co", 16, 1, seed="m1", cfg_overrides=cfg,
                       tile_dir=d, roads_module=None)
        ign = _upwind_ignition("boulder_co", 6_000.0)
        s.ignite(ign.lat, ign.lon, intensity="high")
        s.set_speed(2.0)
        for _ in range(10):
            st = s.tick()
            assert st["scoreboard"]["violations"] == 0
        reasons[buf] = st["scoreboard"]["unserved_by_reason"]
    assert "infeasible_slack" in reasons[30.0]
    assert "infeasible_deadline" in reasons[90.0]
    assert "infeasible_slack" not in reasons[90.0]


def test_buffer_change_triggers_replan_next_tick(tmp_path):
    # 1 crew staged ~11 km out; travel > one 900 s tick, so it is still
    # EN_ROUTE when the slider moves -> revoke -> re-plan on the next tick.
    s = _high_session(tmp_path, seed="replan", n_clients=6, n_crews=1)
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="moderate")
    st1 = s.tick()
    assert st1["plan"] is not None
    t_plan1 = st1["plan"]["computed_at_s"]
    stops1 = {r["client_id"]: r for r in st1["plan"]["stops"]}
    assert stops1, "first tick should adopt a plan"
    crew = st1["crews"][0]
    assert crew["status"] == "en_route"

    s.set_buffer(90.0)
    st2 = s.tick()
    assert st2["scoreboard"]["buffer_min"] == 90.0
    assert st2["plan"]["computed_at_s"] > t_plan1          # re-planned next tick
    assert st2["plan"]["computed_at_s"] == st1["sim_now_s"]
    # the new rule tightened every shared stop's departure deadline by 60 min
    stops2 = {r["client_id"]: r for r in st2["plan"]["stops"]}
    for cid in set(stops1) & set(stops2):
        assert stops2[cid]["latest_depart_s"] < stops1[cid]["latest_depart_s"]
    assert st2["scoreboard"]["violations"] == 0


def test_byte_reproducibility_same_script(tmp_path):
    def run():
        s = _high_session(tmp_path, seed="repro", n_clients=6, n_crews=2)
        ign = _upwind_ignition("boulder_co", 8_000.0)
        hashes = []
        s.ignite(ign.lat, ign.lon, intensity="high")
        hashes.append(_state_hash(s.tick()))
        hashes.append(_state_hash(s.tick()))
        s.set_buffer(45.0)
        hashes.append(_state_hash(s.tick()))
        second = geo.destination(LOCATIONS["boulder_co"].center, 0.0, 3_000.0)
        s.ignite(second.lat, second.lon, intensity="extreme",
                 wind_dir_deg=300.0)
        hashes.append(_state_hash(s.tick()))
        s.set_slack(20.0)
        hashes.append(_state_hash(s.tick()))
        return hashes

    a, b = run(), run()
    assert a == b, "same (seed, command script) must replay byte-identically"


def test_multiple_fires_and_alert_feed(tmp_path):
    s = _high_session(tmp_path, seed="multi", n_clients=6, n_crews=2)
    ign = _upwind_ignition("boulder_co", 8_000.0)
    f1 = s.ignite(ign.lat, ign.lon, intensity="high")
    second = geo.destination(LOCATIONS["boulder_co"].center, 0.0, 3_000.0)
    f2 = s.ignite(second.lat, second.lon, intensity="high")
    assert (f1, f2) == ("sim-f1", "sim-f2")
    st = s.tick()
    assert [f["incident_id"] for f in st["fires"]] == ["sim-f1", "sim-f2"]
    # the fire sitting on the hamlet must raise real ladder states quickly
    assert any(c["state"] != "none" for c in st["clients"])
    assert st["alert_feed"], "AlertEngine should have emitted events"
    for ev in st["alert_feed"]:
        assert ev["incident_id"] in ("sim-f1", "sim-f2")
    # STATE VISUAL CONTRACT: absolute-hour isochrones are gone; the UI
    # renders the sim-now-anchored projection bands instead
    assert "isochrones" not in st
    assert st["fire_bands"]["h24"], "p50 projection bands expected"


def test_pause_speed_reset_and_cmd_log(tmp_path):
    s = _high_session(tmp_path, seed="clock", n_clients=4, n_crews=1)
    t0 = s.state()["sim_now_s"]
    step = s._cfg.replan_period_s
    s.tick()
    assert s.state()["sim_now_s"] == t0 + step
    s.pause()
    s.tick()                                   # frozen clock
    assert s.state()["sim_now_s"] == t0 + step
    assert s.state()["paused"] is True
    s.resume()
    s.set_speed(4.0)
    s.tick()
    assert s.state()["sim_now_s"] == t0 + step + 4.0 * step
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="low")
    s.reset()
    st = s.state()
    assert st["sim_now_s"] == t0 and st["fires"] == [] and st["tick"] == 0
    assert st["scoreboard"]["served"] == 0
    cmds = [c["cmd"] for c in st["cmd_log"]]
    assert cmds[0] == "configure" and cmds[-1] == "reset"
    assert {"tick", "pause", "resume", "set_speed", "ignite"} <= set(cmds)
    with pytest.raises(ValueError):
        s.set_speed(0.0)
    with pytest.raises(ValueError):
        s.set_buffer(-5.0)


def test_state_is_json_safe_before_any_tick(tmp_path):
    s = _high_session(tmp_path, seed="json", n_clients=4, n_crews=1)
    json.dumps(s.state(), sort_keys=True, allow_nan=False)
    s.ignite(*_upwind_ignition("boulder_co", 8_000.0), intensity="low")
    json.dumps(s.state(), sort_keys=True, allow_nan=False)


# ---------------------------------------------------------------------------
# STATE VISUAL CONTRACT: growing burned rings + advancing projection bands
# ---------------------------------------------------------------------------

def _ring_pts(rings):
    return [p for ring in rings for p in ring]


def _rings_bbox(rings):
    """(min_lat, min_lon, max_lat, max_lon) over all ring points, or None."""
    pts = _ring_pts(rings)
    if not pts:
        return None
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    return (min(lats), min(lons), max(lats), max(lons))


def _rings_bbox_area(rings):
    bb = _rings_bbox(rings)
    if bb is None:
        return 0.0
    return (bb[2] - bb[0]) * (bb[3] - bb[1])


def _rings_bbox_extent(rings):
    """lat-range + lon-range (degrees). A wind-driven head fire on a coarse
    test grid is a 1-row 'needle' for hours (bbox AREA stays 0 while the
    fire very much grows) — extent is the honest scalar growth measure."""
    bb = _rings_bbox(rings)
    if bb is None:
        return 0.0
    return (bb[2] - bb[0]) + (bb[3] - bb[1])


def _grow_session(tmp_path, seed="grow"):
    """Growth demo scenario for the visual-contract tests (roads pinned
    off): high-intensity fire 8 km upwind, offset 1.5 km OFF the wind axis
    so the wind-driven head runs PAST the developed hamlet (a head aimed
    dead-center dies on the non-burnable checkerboard) at 2x clock."""
    s = _high_session(tmp_path, seed=seed)
    base = _upwind_ignition("boulder_co", 8_000.0)
    ign = geo.destination(base, 0.0, 1_500.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    s.set_speed(2.0)
    return s


def test_visual_fields_shape_before_any_fire(tmp_path):
    s = _high_session(tmp_path, seed="vis0", n_clients=4, n_crews=1)
    st = s.state()
    assert st["burned"] == []
    assert st["fire_bands"] == {"h1": [], "h5": [], "h24": []}
    assert "isochrones" not in st, "old absolute-hour isochrones must be gone"
    assert st["roads_on"] is False


def test_burned_rings_grow_monotonically(tmp_path):
    """state.burned = p50-arrival<=sim-now rings; bbox area non-decreasing
    tick over tick and bbox extent strictly growing across the run (the
    founder's 'fire visibly grows' requirement, 9 ticks > 6)."""
    s = _grow_session(tmp_path)
    areas = []
    extents = []
    for _ in range(9):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        for ring in st["burned"]:
            assert len(ring) >= 4 and ring[0] == ring[-1], "rings are closed"
            for p in ring:
                assert len(p) == 2
        areas.append(_rings_bbox_area(st["burned"]))
        extents.append(_rings_bbox_extent(st["burned"]))
    for a, b in zip(areas, areas[1:]):
        assert b >= a, f"burned bbox area shrank: {areas}"
    for a, b in zip(extents, extents[1:]):
        assert b >= a, f"burned bbox extent shrank: {extents}"
    assert extents[0] > 0.0, "no burned ring by tick 1 — re-pin scenario"
    assert extents[-1] > extents[0] * 3.0, (
        f"burned did not visibly grow: {extents}")


def test_fire_bands_present_ordered_and_advancing(tmp_path):
    """fire_bands = {h1,h5,h24} rings at sim-now +1/+5/+24 h from the CURRENT
    p50 raster: keys always present, h1 inside h5 inside h24 (bbox
    containment), and the +1 h band 3 ticks later must be strictly farther
    out (the bands advance with the fire, never a static forecast)."""
    s = _grow_session(tmp_path, seed="bands")
    snaps = []
    for _ in range(9):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        assert sorted(st["fire_bands"]) == ["h1", "h24", "h5"]
        snaps.append(st["fire_bands"])
    last = snaps[-1]
    for key in ("h1", "h5", "h24"):
        assert last[key], f"band {key} empty after 4.5 h of growing fire"
    eps = 1e-4
    b1 = _rings_bbox(last["h1"])
    b5 = _rings_bbox(last["h5"])
    b24 = _rings_bbox(last["h24"])
    for inner, outer, name in ((b1, b5, "h1 in h5"), (b5, b24, "h5 in h24")):
        assert outer[0] <= inner[0] + eps and outer[1] <= inner[1] + eps \
            and outer[2] >= inner[2] - eps and outer[3] >= inner[3] - eps, \
            f"band ordering violated ({name}): inner={inner} outer={outer}"
    # advancing: the +1 h band at tick 9 is strictly farther out than at
    # tick 6 (the same band recomputed 3 ticks later)
    e_t = _rings_bbox_extent(snaps[5]["h1"])
    e_t3 = _rings_bbox_extent(snaps[8]["h1"])
    assert e_t > 0.0, "h1 band not established by tick 6 — re-pin scenario"
    assert e_t3 > e_t, (
        f"+1h band did not advance tick 6 -> 9: {e_t} -> {e_t3}")


# ---------------------------------------------------------------------------
# Roads seam (contracts wgresponse.roads): unit tests against an in-test
# fake conforming EXACTLY to the contracted signatures, so none of this
# requires the concurrently-built real module.
# ---------------------------------------------------------------------------

class _FakeRoute:
    """RoadRoute-shaped record per the contracts seam:
    polyline/time_s/dist_m/on_roads."""

    def __init__(self, polyline, time_s, dist_m, on_roads=True):
        self.polyline = polyline
        self.time_s = time_s
        self.dist_m = dist_m
        self.on_roads = on_roads


class _FakeRoadGraph:
    def __init__(self):
        self.nodes = {"n1": None, "n2": None}


class _FakeRoads:
    """In-test stand-in for wgresponse.roads (module-shaped: exposes
    load_road_graph(key) and route(g, a, b, cfg)). Every route is an
    L-shaped dog-leg a -> (a.lat, b.lon) -> b at a fixed road speed, so the
    road path measurably differs from the chord. slow_to inflates time_s for
    routes ENDING near the given points (slow escape roads) without touching
    the geometry; graph=False simulates a missing cached graph."""

    def __init__(self, speed_mps=15.0, slow_to=(), slow_time_s=40 * 3600.0,
                 graph=True):
        self.speed_mps = float(speed_mps)
        self.slow_to = list(slow_to)
        self.slow_time_s = float(slow_time_s)
        self._graph = graph

    def load_road_graph(self, key):
        return _FakeRoadGraph() if self._graph else None

    def route(self, g, a, b, cfg):
        corner = LatLon(a.lat, b.lon)
        dist = geo.haversine_m(a, corner) + geo.haversine_m(corner, b)
        time_s = dist / self.speed_mps
        if any(geo.haversine_m(b, z) < 200.0 for z in self.slow_to):
            time_s = self.slow_time_s
        return _FakeRoute([a, corner, b], time_s, dist, on_roads=True)


def _pt_seg_dist_m(p, a, b):
    """Planar point-to-segment distance in meters (fine at km scales)."""
    cosl = math.cos(math.radians((a.lat + b.lat) / 2.0))
    px = (p.lon - a.lon) * cosl * 111_320.0
    py = (p.lat - a.lat) * 111_320.0
    bx = (b.lon - a.lon) * cosl * 111_320.0
    by = (b.lat - a.lat) * 111_320.0
    l2 = bx * bx + by * by
    t = 0.0 if l2 == 0.0 else max(0.0, min(1.0, (px * bx + py * by) / l2))
    return math.hypot(px - t * bx, py - t * by)


def _dist_to_polyline_m(p, pts):
    return min(_pt_seg_dist_m(p, a, b) for a, b in zip(pts, pts[1:]))


def _road_session(tmp_path, fake, seed="road1", n_clients=8, n_crews=1):
    d = _controlled_tile_dir(tmp_path, "boulder_co")
    s = SimSession("boulder_co", n_clients, n_crews, seed=seed,
                   cfg_overrides=dict(FAST), tile_dir=d, roads_module=fake)
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    return s


def test_crew_follows_road_polyline_not_chord(tmp_path):
    """Crew kinematics follow the ROUTE POLYLINE point-to-point: after k
    ticks the crew sits ON the dog-leg (within 1 cell = 500 m), measurably
    OFF the straight chord to its target."""
    s = _road_session(tmp_path, _FakeRoads(speed_mps=4.0), seed="road2")
    st = s.tick()
    crew = st["crews"][0]
    assert crew["status"] == "en_route", crew["status"]
    assert crew["route_on_roads"] is True
    poly = crew["route_polyline"]
    assert len(poly) >= 3, "dog-leg route expected (pos + corner + target)"
    pts = [LatLon(p[0], p[1]) for p in poly]
    start, target = pts[0], pts[-1]
    # the fake's corner really is off the chord (the test is not vacuous)
    assert _pt_seg_dist_m(pts[1], start, target) > 500.0
    st2 = s.tick()
    crew2 = st2["crews"][0]
    assert crew2["status"] == "en_route"
    pos = LatLon(crew2["lat"], crew2["lon"])
    assert _dist_to_polyline_m(pos, pts) <= 500.0, (
        "crew position left the route polyline")
    assert _pt_seg_dist_m(pos, start, target) > 500.0, (
        "crew is tracking the chord, not the road route")
    assert crew2["eta_s"] is not None and crew2["eta_s"] > st2["sim_now_s"]


def test_egress_polyline_present_while_deploying(tmp_path):
    """The precomputed emergency escape route for the CURRENT stop is always
    present while on-site/deploying (STATE VISUAL CONTRACT)."""
    s = _road_session(tmp_path, _FakeRoads(speed_mps=15.0))
    crew = None
    for _ in range(8):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        c = st["crews"][0]
        if c["status"] == "deploying":
            crew = c
            break
    assert crew is not None, "crew never reached a deploy — re-pin scenario"
    ep = crew["egress_polyline"]
    assert len(ep) >= 3, "road egress dog-leg expected"
    assert crew["egress_on_roads"] is True
    # escape route runs from the crew's on-site position to a safe zone
    assert geo.haversine_m(LatLon(*ep[0]),
                           LatLon(crew["lat"], crew["lon"])) < 5.0
    zone_locs = [LatLon(z["lat"], z["lon"]) for z in st["safe_zones"]]
    assert min(geo.haversine_m(LatLon(*ep[-1]), z) for z in zone_locs) < 5.0
    # remaining ingress is consumed; eta_s is the projected departure
    assert crew["route_polyline"] == []
    assert crew["eta_s"] is not None and crew["eta_s"] > st["sim_now_s"]


def test_withdrawal_follows_road_egress(tmp_path):
    """A safety withdrawal turns the crew onto its ROAD egress leg: the
    marker then moves along the egress polyline, not the chord to the zone."""
    s = _road_session(tmp_path, _FakeRoads(speed_mps=15.0))
    st = None
    for _ in range(8):
        st = s.tick()
        if st["crews"][0]["status"] == "deploying":
            break
    assert st["crews"][0]["status"] == "deploying", "never armed a deploy"
    s.set_buffer(1500.0)                     # brutal tightening: must withdraw
    now = s.state()
    assert now["scoreboard"]["withdrawn"] == 1
    w = now["withdrawn"][0]
    assert w["cause"] == "policy_change"
    assert w["road_eff_egress_min"] >= 0.0
    crew = now["crews"][0]
    assert crew["status"] == "egressing"
    ep = crew["egress_polyline"]
    assert len(ep) >= 3 and crew["egress_on_roads"] is True
    pts = [LatLon(p[0], p[1]) for p in ep]
    start, zone = pts[0], pts[-1]
    assert max(_pt_seg_dist_m(p, start, zone)
               for p in pts[1:-1]) > 500.0              # real dog-leg
    s.set_speed(0.05)                        # sample the egress mid-leg
    off_chord = 0.0
    for _ in range(6):
        st = s.tick()
        c = st["crews"][0]
        if c["status"] != "egressing":
            break
        pos = LatLon(c["lat"], c["lon"])
        assert _dist_to_polyline_m(pos, pts) <= 500.0, (
            "withdrawing crew left the road egress polyline")
        off_chord = max(off_chord, _pt_seg_dist_m(pos, start, zone))
    assert off_chord > 500.0, "egress tracked the chord, not the road route"


def test_road_egress_guard_withdraws_before_unsafe_deploy(tmp_path):
    """THE ROADS NUANCE: when the road egress is materially slower than
    tier-0 (40 h escape roads vs a <=24 h fire horizon), the serviced-stop
    completion decision must key off max(tier-0, road) egress time — the
    crew withdraws (cause road_egress) instead of finishing a deploy it
    could never safely depart from. violations stays 0 LEGITIMATELY: the
    unsafe completion never happens."""
    zones = [z.loc for z in LOCATIONS["boulder_co"].safe_zones]
    fake = _FakeRoads(speed_mps=15.0, slow_to=zones, slow_time_s=40 * 3600.0)
    s = _road_session(tmp_path, fake)
    st = None
    for _ in range(8):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        if st["scoreboard"]["withdrawn"]:
            break
    assert st["scoreboard"]["withdrawn"] >= 1, (
        "slow road egress never triggered the departure guard")
    assert st["scoreboard"]["served"] == 0, (
        "a deploy completed although its road egress could never clear")
    w = st["withdrawn"][0]
    assert w["cause"] == "road_egress"
    assert w["road_eff_egress_min"] == pytest.approx(40 * 60.0)
    assert w["deficit_min"] > 0.0
    assert any("SAFETY WITHDRAWAL (road_egress)" in n for n in st["notes"])
    victim = w["client_id"]
    assert victim not in {sv["client_id"] for sv in st["serviced"]}
    assert any(u["client_id"] == victim and u["reason"] == "withdrawn_unsafe"
               for u in st["unserved"])
    # audit semantics untouched: nothing serviced, nothing to re-label
    assert st["audit"] == []


def test_straight_leg_fallback_when_no_graph(tmp_path):
    """No roads module / no cached graph -> straight-leg mode: loud note,
    roads_on False, chord polylines with on_roads False (contract: NEVER
    silently pretend a road route)."""
    d = _controlled_tile_dir(tmp_path, "boulder_co")
    ign = _upwind_ignition("boulder_co", 8_000.0)

    s1 = SimSession("boulder_co", 6, 1, seed="noroad", tile_dir=d,
                    cfg_overrides=dict(FAST), roads_module=None)
    st1 = s1.state()
    assert st1["roads_on"] is False
    assert any("ROADS OFFLINE" in n and "disabled" in n for n in st1["notes"])

    s2 = SimSession("boulder_co", 6, 1, seed="noroad", tile_dir=d,
                    cfg_overrides=dict(FAST),
                    roads_module=_FakeRoads(graph=False))
    st2 = s2.state()
    assert st2["roads_on"] is False
    assert any("ROADS OFFLINE" in n and "no cached road graph" in n
               for n in st2["notes"])
    s2.ignite(ign.lat, ign.lon, intensity="high")
    s2.set_speed(0.5)
    crew = None
    for _ in range(6):
        st2 = s2.tick()
        c = st2["crews"][0]
        if c["status"] == "en_route":
            crew = c
            break
    assert crew is not None, "crew never dispatched — re-pin scenario"
    assert crew["route_on_roads"] is False
    assert len(crew["route_polyline"]) == 2, (
        "straight-leg fallback must be the chord (pos + target)")


def test_road_session_replays_byte_identically(tmp_path):
    """Determinism law holds through road-following kinematics + the road
    departure guard: same (seed, command script, fake roads) -> identical
    state hashes."""
    def run():
        s = _road_session(tmp_path, _FakeRoads(speed_mps=15.0), seed="repro")
        hashes = [_state_hash(s.tick()) for _ in range(3)]
        s.set_buffer(1500.0)
        hashes.extend(_state_hash(s.tick()) for _ in range(2))
        return hashes, s.state()["scoreboard"]["withdrawn"]

    (ha, wa), (hb, wb) = run(), run()
    assert ha == hb
    assert wa == wb >= 1, "script must actually exercise the withdraw path"


# ---------------------------------------------------------------------------
# Real cached tile (conditional-skip integration; the tile agent commits
# data/tiles/<key>.wgt concurrently)
# ---------------------------------------------------------------------------

def test_real_tile_integration_when_present():
    from wgresponse.fuels import is_burnable

    key = next((k for k in sorted(LOCATIONS)
                if (DEFAULT_TILE_DIR / f"{k}.wgt").exists()), None)
    if key is None:
        pytest.skip("no real cached tile under data/tiles/ yet")
    s = SimSession(key, 10, 2, seed="live",
                   cfg_overrides={"ensemble_n": 2, "horizon_h": 24.0})
    st = s.state()
    assert st["terrain"]["synthetic"] is False
    assert st["terrain"]["banner"] == ""
    assert len(st["clients"]) == 10
    # every client sits in the tile's real WUI band
    band = set(wui_cells(s._tile))
    in_band = sum(1 for c in s._clients
                  if geo.latlon_to_rc(s._tile.spec, c.loc) in band)
    assert in_band / 10 >= 0.95
    # ignite 1-4 km upwind of a burnable-cell client so the real terrain
    # actually threatens someone inside the 24 h horizon (deterministic scan
    # for a burnable ignition cell — real tiles have urban gaps)
    def _fuel_at(p):
        rc = geo.latlon_to_rc(s._tile.spec, p)
        return None if rc is None else int(round(s._tile.fuel.get(*rc)))

    target = next(c for c in s._clients if is_burnable(_fuel_at(c.loc)))
    hazard = LOCATIONS[key].hazard_wind_dir_deg
    ign = next(p for p in (geo.destination(target.loc, hazard, d)
                           for d in range(1000, 4001, 250))
               if _fuel_at(p) is not None and is_burnable(_fuel_at(p)))
    s.ignite(ign.lat, ign.lon, intensity="high")
    for _ in range(2):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
    # the real alert ladder fired on real terrain...
    assert any(c["state"] != "none" for c in st["clients"])
    # ...and the real dispatcher was consulted about the threatened client
    tracked = {u["client_id"] for u in st["unserved"]} | \
              {r["client_id"] for r in (st["plan"] or {"stops": []})["stops"]} | \
              {sv["client_id"] for sv in st["serviced"]}
    assert target.client_id in tracked
    json.dumps(st, sort_keys=True, allow_nan=False)


def _real_roads_key():
    """First location with BOTH a real cached tile and a real cached road
    graph, or a LOUD pytest.skip while the concurrently-built roads module /
    data/roads caches are absent (the final suite run should have them)."""
    import importlib.util

    if importlib.util.find_spec("wgresponse.roads") is None:
        pytest.skip("LOUD SKIP: wgresponse/roads.py has not landed — "
                    "real-road integration not exercised this run")
    roads_dir = DEFAULT_TILE_DIR.parent / "roads"
    key = next((k for k in sorted(LOCATIONS)
                if (DEFAULT_TILE_DIR / f"{k}.wgt").exists()
                and (roads_dir / f"{k}.wgr").exists()), None)
    if key is None:
        pytest.skip("LOUD SKIP: no data/roads/<key>.wgr cached graph paired "
                    "with a cached tile yet — real-road integration not "
                    "exercised this run")
    return key


def test_real_roads_real_tile_integration_when_present():
    """Real tile + REAL wgresponse.roads graph: the graph loads through the
    session, the exact leg seam crews use (session _leg_route -> roads.route)
    resolves ON-ROAD routes between real safe zones and placed clients, and
    the full engine loop stays violation-free with the visual contract
    intact."""
    from wgresponse.fuels import is_burnable

    key = _real_roads_key()
    s = SimSession(key, 10, 2, seed="live",
                   cfg_overrides={"ensemble_n": 2, "horizon_h": 24.0})
    st = s.state()
    if not st["roads_on"]:
        pytest.skip(f"LOUD SKIP: road graph for {key} present but did not "
                    f"load — notes: {st['notes']}")
    assert any("road graph loaded" in n for n in st["notes"])
    # the seam crews resolve every leg through, on real data
    zone = LOCATIONS[key].safe_zones[0]
    cl = s._clients[0]
    rr = s._leg_route(zone.loc, cl.loc)
    assert rr is not None
    assert rr.on_roads is True, (
        f"straight-leg fallback between {zone.zone_id} and {cl.client_id} "
        f"on the real {key} graph")
    assert len(rr.polyline) >= 2 and rr.dist_m > 0.0 and rr.time_s > 0.0

    def _fuel_at(p):
        rc = geo.latlon_to_rc(s._tile.spec, p)
        return None if rc is None else int(round(s._tile.fuel.get(*rc)))

    target = next(c for c in s._clients if is_burnable(_fuel_at(c.loc)))
    hazard = LOCATIONS[key].hazard_wind_dir_deg
    ign = next(p for p in (geo.destination(target.loc, hazard, d)
                           for d in range(1000, 4001, 250))
               if _fuel_at(p) is not None and is_burnable(_fuel_at(p)))
    s.ignite(ign.lat, ign.lon, intensity="high")
    for _ in range(3):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        for c in st["crews"]:
            for poly, flag in ((c["route_polyline"], c["route_on_roads"]),
                               (c["egress_polyline"], c["egress_on_roads"])):
                if poly:
                    assert isinstance(flag, bool)
                    assert len(poly) >= 2
                    for p in poly:
                        assert len(p) == 2
                else:
                    # tri-state contract: no polyline -> null flag (False is
                    # reserved for a REAL straight-line leg)
                    assert flag is None
    # the visual contract fields hold on real terrain too
    assert sorted(st["fire_bands"]) == ["h1", "h24", "h5"]
    assert "isochrones" not in st
    json.dumps(st, sort_keys=True, allow_nan=False)


def test_real_roads_crews_drive_on_roads_when_present(tmp_path):
    """REAL road graph + the controlled dispatch geometry: a crew actually
    DRIVES a real road polyline (position pinned to it tick over tick), the
    on-site escape route is a real road egress, deploys complete under the
    road departure guard, and the audit stays at 0."""
    key = _real_roads_key()
    if key != "boulder_co":
        pytest.skip(f"LOUD SKIP: controlled drive test is pinned to the "
                    f"boulder_co geometry; first tile+graph pair is {key}")
    d = _controlled_tile_dir(tmp_path, key)
    s = SimSession(key, 8, 1, seed="road1", cfg_overrides=dict(FAST),
                   tile_dir=d)                      # roads resolve for real
    st = s.state()
    if not st["roads_on"]:
        pytest.skip(f"LOUD SKIP: road graph for {key} present but did not "
                    f"load — notes: {st['notes']}")
    ign = _upwind_ignition(key, 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    s.set_speed(0.25)                # 225 s ticks: catch the crew mid-drive
    poly = None
    for _ in range(20):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        c = st["crews"][0]
        if poly is None:
            if c["status"] == "en_route" and len(c["route_polyline"]) >= 3:
                assert c["route_on_roads"] is True
                # planned escape for the CURRENT stop rides along already
                assert c["egress_polyline"] and c["egress_on_roads"] is True
                poly = [LatLon(p[0], p[1]) for p in c["route_polyline"]]
            continue
        if c["status"] != "en_route":
            break
        pos = LatLon(c["lat"], c["lon"])
        assert _dist_to_polyline_m(pos, poly) <= 500.0, (
            "crew left the real road polyline")
    assert poly is not None, "crew never seen driving — re-pin scenario"
    # ride on to the first deploy: real road escape route on-site
    s.set_speed(2.0)
    for _ in range(30):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        c = st["crews"][0]
        if c["status"] == "deploying":
            assert len(c["egress_polyline"]) >= 2
            assert c["egress_on_roads"] is True
            break
        if st["scoreboard"]["served"] >= 1:
            break                    # deploy completed inside one coarse tick
    else:
        pytest.fail("crew never reached a deploy on the controlled geometry")
    json.dumps(st, sort_keys=True, allow_nan=False)


# ---------------------------------------------------------------------------
# Tri-state road flags (2026-07-10 UI-sibling nit): route_on_roads /
# egress_on_roads are None when NO corresponding polyline exists, so False
# is reserved for a REAL straight-line leg (the UI's honesty chip trigger).
# ---------------------------------------------------------------------------

def test_idle_crew_road_flags_are_null_until_leg_exists(tmp_path):
    s = _high_session(tmp_path, seed="null-flags", n_clients=6, n_crews=2)
    st = s.state()
    for c in st["crews"]:
        assert c["route_polyline"] == [] and c["route_on_roads"] is None
        assert c["egress_polyline"] == [] and c["egress_on_roads"] is None
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    st = s.tick()
    moving = [c for c in st["crews"] if c["status"] == "en_route"]
    assert moving, "no crew dispatched — re-pin scenario"
    for c in moving:
        # a REAL straight-line leg: points present, flag exactly False
        assert len(c["route_polyline"]) >= 2
        assert c["route_on_roads"] is False
        assert len(c["egress_polyline"]) >= 2
        assert c["egress_on_roads"] is False
    for c in st["crews"]:
        if not c["route_polyline"]:
            assert c["route_on_roads"] is None
        if not c["egress_polyline"]:
            assert c["egress_on_roads"] is None
    json.dumps(st, sort_keys=True, allow_nan=False)


# ---------------------------------------------------------------------------
# Band ring quality (2026-07-10 UI-sibling finding): _threshold_rings must
# order ring points by TRUE BOUNDARY FOLLOWING — the old centroid-angle sort
# starburst-zigzagged on concave fronts (h5 band).
# ---------------------------------------------------------------------------

def _seg_intersects(a, b, c, d):
    """True iff closed segments ab and cd share ANY point (inclusive of
    endpoints; collinear overlaps count). Standard orientation test."""
    def orient(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        if abs(v) < 1e-18:
            return 0
        return 1 if v > 0 else -1

    def on_seg(p, q, r):
        return (min(p[0], q[0]) - 1e-15 <= r[0] <= max(p[0], q[0]) + 1e-15
                and min(p[1], q[1]) - 1e-15 <= r[1] <= max(p[1], q[1]) + 1e-15)

    o1, o2 = orient(a, b, c), orient(a, b, d)
    o3, o4 = orient(c, d, a), orient(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and on_seg(a, b, c):
        return True
    if o2 == 0 and on_seg(a, b, d):
        return True
    if o3 == 0 and on_seg(c, d, a):
        return True
    if o4 == 0 and on_seg(c, d, b):
        return True
    return False


def _assert_simple_ring(ring):
    """No self-crossing: non-adjacent segments of the closed ring must not
    touch at all (adjacent segments share exactly their common vertex)."""
    segs = list(zip(ring[:-1], ring[1:]))           # ring is closed
    n = len(segs)
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue                             # adjacent (incl. wrap)
            assert not _seg_intersects(*segs[i], *segs[j]), (
                f"ring self-intersects: segment {i} {segs[i]} x "
                f"segment {j} {segs[j]}")


def test_threshold_rings_concave_boundary_following():
    """A C-shaped (crescent) burned region traces as ONE clean closed
    outline: consecutive ring points are boundary-adjacent (within 2 cell
    diagonals) and no two non-adjacent segments intersect — the exact
    failure mode of the old centroid-angle ordering (points from the inner
    and outer arcs interleave at equal angles -> starburst)."""
    cell_m = 100.0
    nr = nc = 36
    spec = GridSpec(north_lat=40.0, west_lon=-105.0, cell_m=cell_m,
                    nrows=nr, ncols=nc)
    vals = [1e30] * (nr * nc)
    cy = cx = 18
    n_mask = 0
    for r in range(nr):
        for c in range(nc):
            dr, dc = r - cy, c - cx
            dist = math.hypot(dr, dc)
            in_annulus = 7.5 <= dist <= 11.5
            in_mouth = dc > 0 and abs(dr) <= 2       # east-opening gap
            if in_annulus and not in_mouth:
                vals[r * nc + c] = 0.0
                n_mask += 1
    assert n_mask > 60, "crescent construction degenerated — re-pin"
    rings = _threshold_rings(spec, vals, 1.0)
    assert len(rings) == 1, (
        f"crescent outline must be ONE closed ring, got {len(rings)}")
    ring = rings[0]
    assert ring[0] == ring[-1], "ring must be closed"
    assert len(ring) >= 30, "outline suspiciously short — re-pin shape"
    # boundary-adjacency: consecutive points within 2 cell diagonals
    max_step_m = 2.0 * math.sqrt(2.0) * cell_m * 1.05
    for p, q in zip(ring, ring[1:]):
        d = geo.haversine_m(LatLon(*p), LatLon(*q))
        assert d <= max_step_m, (
            f"consecutive ring points {p} -> {q} are {d:.0f} m apart "
            f"(> 2 cell diagonals) — ordering is not boundary-following")
    # no starburst: the polygon's segments must not cross each other
    _assert_simple_ring(ring)
    # both arms present: the outline spans the whole crescent, not one arc
    lats = [p[0] for p in ring]
    lons = [p[1] for p in ring]
    lat_span_cells = (max(lats) - min(lats)) * geo.M_PER_DEG_LAT / cell_m
    lon_span_cells = ((max(lons) - min(lons))
                      * geo.m_per_deg_lon(40.0) / cell_m)
    assert lat_span_cells >= 18.0 and lon_span_cells >= 15.0, (
        f"outline bbox too small ({lat_span_cells:.1f} x "
        f"{lon_span_cells:.1f} cells) — an arc went missing")


def test_threshold_rings_convex_blob_still_clean():
    """Regression guard for the rewrite: a plain convex blob still yields a
    simple, adjacent-ordered closed ring (the pre-fix easy case)."""
    cell_m = 100.0
    nr = nc = 24
    spec = GridSpec(north_lat=40.0, west_lon=-105.0, cell_m=cell_m,
                    nrows=nr, ncols=nc)
    vals = [1e30] * (nr * nc)
    for r in range(nr):
        for c in range(nc):
            if math.hypot(r - 12, c - 12) <= 7.2:
                vals[r * nc + c] = 0.0
    rings = _threshold_rings(spec, vals, 1.0)
    assert len(rings) == 1
    ring = rings[0]
    assert ring[0] == ring[-1] and len(ring) >= 12
    max_step_m = 2.0 * math.sqrt(2.0) * cell_m * 1.05
    for p, q in zip(ring, ring[1:]):
        assert geo.haversine_m(LatLon(*p), LatLon(*q)) <= max_step_m
    _assert_simple_ring(ring)


# ---------------------------------------------------------------------------
# FIRE ZONE (contracts wgresponse.simui FIRE ZONE block, founder request
# 2026-07-10): extended wide-area tiles, central-window client placement,
# horizon floor, loud missing-tile fallback, determinism.
# ---------------------------------------------------------------------------

def _controlled_zone_tile_dir(tmp_path, key="boulder_co", far_hamlet_km=15.0):
    """Controlled STANDARD (20 km @ 500 m) + EXTENDED (48 km @ 1200 m) tile
    pair in one tmp dir. The extended tile carries a porous hamlet at the
    location center (inside the central community window) AND a second one
    ~far_hamlet_km east (outside the window but inside the tile) so the
    window restriction is testable non-vacuously."""
    d = _controlled_tile_dir(tmp_path, key, size_km=20.0, cell_m=500.0)
    loc = LOCATIONS[key]
    bbox = geo.bbox_around([loc.center], pad_m=24_000.0)
    tile = SyntheticTerrain(seed=f"simui-test-ext|{key}",
                            kind="grass").tile(bbox, 1200.0)
    spec = tile.spec

    def _stamp(rc, half=2):
        r0, c0 = rc
        for r in range(r0 - half, r0 + half):
            for c in range(c0 - half, c0 + half):
                if (0 <= r < spec.nrows and 0 <= c < spec.ncols
                        and (r + c) % 2 == 0):
                    tile.fuel.set(r, c, 91.0)

    _stamp(geo.latlon_to_rc(spec, loc.center))
    far = geo.destination(loc.center, 90.0, far_hamlet_km * 1000.0)
    _stamp(geo.latlon_to_rc(spec, far))
    save_ascii_tile(tile, str(d / f"{key}_ext.wgt"))
    return d


def test_fire_zone_rejects_unknown_value(tmp_path):
    d = _controlled_zone_tile_dir(tmp_path)
    with pytest.raises(ValueError, match="fire_zone"):
        SimSession("boulder_co", 4, 1, seed="z", cfg_overrides=dict(FAST),
                   fire_zone="huge", tile_dir=d, roads_module=None)


def test_fire_zone_extended_places_clients_in_central_window(tmp_path):
    """Extended tile loads; clients ALL land inside the central ~18 km
    community window (max distance from the location center <= ~13 km =
    the window's corner) even though the tile's WUI band extends 15 km out;
    the terrain bbox is ~2-3x the standard session's; the requested zone is
    serialized into the repro cmd_log."""
    d = _controlled_zone_tile_dir(tmp_path)
    center = LOCATIONS["boulder_co"].center

    s_std = SimSession("boulder_co", 12, 1, seed="fz", tile_dir=d,
                       cfg_overrides=dict(FAST), roads_module=None)
    s_ext = SimSession("boulder_co", 12, 1, seed="fz", tile_dir=d,
                       cfg_overrides=dict(FAST), fire_zone="extended",
                       roads_module=None)
    st_std, st_ext = s_std.state(), s_ext.state()
    assert st_std["fire_zone"] == "standard"
    assert st_ext["fire_zone"] == "extended"
    assert st_ext["cmd_log"][0]["fire_zone"] == "extended"
    assert st_ext["terrain"]["synthetic"] is False

    # bbox ~2-3x wider both ways (48 km vs 20 km controlled tiles)
    b_s, b_e = st_std["terrain"]["bbox"], st_ext["terrain"]["bbox"]
    for lo, hi in (("south", "north"), ("west", "east")):
        ratio = (b_e[hi] - b_e[lo]) / (b_s[hi] - b_s[lo])
        assert 2.0 <= ratio <= 3.0, f"extent ratio {ratio} out of ~2-3x"

    # the far hamlet's WUI cells exist on the tile (window test not vacuous)
    band = wui_cells(s_ext._tile)
    window = central_window_bbox(center)
    outside = [rc for rc in band
               if not window.contains(geo.rc_to_latlon(s_ext._tile.spec, *rc))]
    assert outside, "no WUI cells outside the window — far hamlet missing"

    # every client sits in the central community window
    dists_km = [geo.haversine_m(center, c.loc) / 1000.0
                for c in s_ext._clients]
    assert len(dists_km) == 12
    assert max(dists_km) <= 13.0, (
        f"client escaped the central window: max {max(dists_km):.2f} km")


def test_fire_zone_missing_ext_tile_falls_back_loud(tmp_path):
    """No <key>_ext.wgt cached -> LOUD note + the standard tile loads;
    state.fire_zone reflects what ACTUALLY loaded and the extended horizon
    floor does NOT apply (it is not an extended session)."""
    d = _controlled_tile_dir(tmp_path, "boulder_co")      # standard only
    s = SimSession("boulder_co", 4, 1, seed="fb", tile_dir=d,
                   cfg_overrides={"ensemble_n": 3}, fire_zone="extended",
                   roads_module=None)
    st = s.state()
    assert st["fire_zone"] == "standard"
    assert st["terrain"]["synthetic"] is False            # the standard tile
    assert any("FIRE ZONE" in n and "_ext" in n for n in st["notes"]), \
        st["notes"]
    assert st["cmd_log"][0]["fire_zone"] == "extended"    # what was ASKED
    assert s._cfg.horizon_h == 96.0                       # no floor applied


def test_fire_zone_extended_horizon_default_and_override(tmp_path):
    """HORIZON PRECEDENCE (configure docstring): extended sessions floor
    horizon_h at 120 by default; an explicit horizon_h in cfg_overrides
    always wins (even below 120); standard sessions are untouched."""
    d = _controlled_zone_tile_dir(tmp_path)
    s = SimSession("boulder_co", 4, 1, seed="h", tile_dir=d,
                   cfg_overrides={"ensemble_n": 3}, fire_zone="extended",
                   roads_module=None)
    assert s._cfg.horizon_h == EXT_MIN_HORIZON_H == 120.0
    s2 = SimSession("boulder_co", 4, 1, seed="h", tile_dir=d,
                    cfg_overrides={"ensemble_n": 3, "horizon_h": 24.0},
                    fire_zone="extended", roads_module=None)
    assert s2._cfg.horizon_h == 24.0                      # explicit wins
    s3 = SimSession("boulder_co", 4, 1, seed="h", tile_dir=d,
                    cfg_overrides={"ensemble_n": 3}, roads_module=None)
    assert s3._cfg.horizon_h == 96.0                      # standard default
    # a horizon override above the floor is also untouched
    s4 = SimSession("boulder_co", 4, 1, seed="h", tile_dir=d,
                    cfg_overrides={"ensemble_n": 3, "horizon_h": 200.0},
                    fire_zone="extended", roads_module=None)
    assert s4._cfg.horizon_h == 200.0


def test_fire_zone_extended_far_ignition_and_determinism(tmp_path):
    """Ignition ~20 km from center is REJECTED on the standard tile (off
    tile) and ACCEPTED on the extended tile; the full engine loop runs
    violation-free with bands present; same (seed, script) twice replays
    byte-identically (design rule 3 holds through the fire zone)."""
    d = _controlled_zone_tile_dir(tmp_path)
    far = geo.destination(LOCATIONS["boulder_co"].center, 0.0, 20_000.0)

    s_std = SimSession("boulder_co", 6, 1, seed="fzr", tile_dir=d,
                       cfg_overrides=dict(FAST), roads_module=None)
    with pytest.raises(ValueError, match="outside"):
        s_std.ignite(far.lat, far.lon, intensity="high")

    def run():
        s = SimSession("boulder_co", 6, 1, seed="fzr", tile_dir=d,
                       cfg_overrides=dict(FAST), fire_zone="extended",
                       roads_module=None)
        assert s.ignite(far.lat, far.lon, intensity="high") == "sim-f1"
        s.set_speed(8.0)
        hashes = [_state_hash(s.tick()) for _ in range(3)]
        return hashes, s.state()

    (ha, sta), (hb, stb) = run(), run()
    assert ha == hb, "extended session must replay byte-identically"
    assert sta["scoreboard"]["violations"] == 0
    assert sta["fires"][0]["incident_id"] == "sim-f1"
    assert sorted(sta["fire_bands"]) == ["h1", "h24", "h5"]
    assert sta["fire_bands"]["h24"], "projection bands expected"
    json.dumps(sta, sort_keys=True, allow_nan=False)


def _far_burnable_ignition(session, min_km=20.0, max_km=23.5):
    """First burnable cell (row-major) of the session tile whose center is
    min_km..max_km from the location center — 'near the tile edge'."""
    from wgresponse.fuels import is_burnable
    tile = session._tile
    spec = tile.spec
    center = session._loc.center
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            if not is_burnable(int(round(tile.fuel.get(r, c)))):
                continue
            p = geo.rc_to_latlon(spec, r, c)
            if min_km * 1000.0 <= geo.haversine_m(center, p) <= max_km * 1000.0:
                return p
    pytest.fail(f"no burnable cell {min_km}-{max_km} km out on "
                f"{session._loc.key} — re-pin the scan")


@pytest.mark.parametrize("key", sorted(LOCATIONS))
def test_fire_zone_extended_real_tile_integration(key):
    """REAL committed extended tile per location (loud per-tile skip while
    the tile agent's fetch is incomplete): the wide tile loads, its bbox is
    ~2-3x the standard tile's, all clients stay in the central community
    window (<= ~13 km of center), a ~20+ km-out ignition is accepted, the
    engine ticks violation-free with bands present, the horizon floor holds,
    and (when the real road graph is cached) a leg to the far ignition
    falls back per the roads seam (on_roads=False straight leg — graphs
    were fetched on the STANDARD bbox)."""
    ext_path = DEFAULT_TILE_DIR / f"{key}_ext.wgt"
    std_path = DEFAULT_TILE_DIR / f"{key}.wgt"
    if not ext_path.exists():
        pytest.skip(f"LOUD SKIP: no extended tile data/tiles/{key}_ext.wgt "
                    f"yet — fire-zone integration not exercised for {key} "
                    f"this run (py -3 tools/fetch_sim_tiles.py --extended)")
    if not std_path.exists():
        pytest.skip(f"LOUD SKIP: standard tile data/tiles/{key}.wgt missing "
                    f"— cannot compare fire-zone extents for {key}")

    s = SimSession(key, 12, 2, seed="fz-live",
                   cfg_overrides={"ensemble_n": 2}, fire_zone="extended")
    st = s.state()
    assert st["fire_zone"] == "extended"
    assert st["terrain"]["synthetic"] is False
    assert s._cfg.horizon_h >= 120.0                     # extended floor

    # bbox ~2-3x the standard tile's extent, both axes
    from wgresponse.terrain import load_ascii_tile as _load
    std_spec = _load(str(std_path)).spec
    ext_spec = s._tile.spec
    for span_ext, span_std in (
            (ext_spec.nrows * ext_spec.cell_m,
             std_spec.nrows * std_spec.cell_m),
            (ext_spec.ncols * ext_spec.cell_m,
             std_spec.ncols * std_spec.cell_m)):
        assert 2.0 <= span_ext / span_std <= 3.0, (
            f"{key}: extended span {span_ext/1000.0:.1f} km vs standard "
            f"{span_std/1000.0:.1f} km outside the ~2-3x law")

    # clients: all inside the central community window
    center = LOCATIONS[key].center
    dists_km = [geo.haversine_m(center, c.loc) / 1000.0 for c in s._clients]
    assert len(dists_km) == 12
    assert max(dists_km) <= 13.0, (
        f"{key}: client {max(dists_km):.2f} km from center — outside the "
        f"central window")

    # ignition near the tile edge (~20+ km out) is accepted
    ign = _far_burnable_ignition(s)
    assert geo.haversine_m(center, ign) >= 20_000.0
    assert s.ignite(ign.lat, ign.lon, intensity="high") == "sim-f1"

    # roads seam on the STANDARD-bbox graph: a leg to the far ignition point
    # falls back to the straight tier-0 leg, honestly flagged
    if st["roads_on"]:
        rr = s._leg_route(LOCATIONS[key].safe_zones[0].loc, ign)
        assert rr is not None
        assert rr.on_roads is False, (
            f"{key}: a 20+ km-out point snapped onto the standard-bbox road "
            f"graph — expected the seam's straight-leg fallback")
        assert len(rr.polyline) == 2

    st = s.tick()
    assert st["scoreboard"]["violations"] == 0
    assert sorted(st["fire_bands"]) == ["h1", "h24", "h5"]
    assert st["fire_bands"]["h24"], f"{key}: no h24 band after a tick"
    json.dumps(st, sort_keys=True, allow_nan=False)


# ---------------------------------------------------------------------------
# Seed-time anchoring (founder-visible frozen-fire bug, 2026-07-10)
# ---------------------------------------------------------------------------

def test_subcell_fire_grows_bands_advance_and_burned_appears(tmp_path):
    """REGRESSION (simui.py incident-snapshot seeding): while a fire is
    younger than its first traceable ring (sub-cell footprint — slow fuel
    and/or coarse cells), the point seed must stay anchored at the TRUE
    ignition time. The pre-fix code re-seeded at `now` every tick, so the
    arrival field never fell behind the clock: bands froze byte-identically
    and the burned area stayed empty FOREVER (observed live on the extended
    Malibu tile: 15 simulated hours, zero growth).

    Coarse 1000 m cells + 'low' intensity keep the fire sub-cell for several
    900 s ticks, exactly the deadlock window. With the fix: the +24 h band
    must CHANGE between early ticks (thresholds sweep a fixed arrival field
    as `now` advances) and the burned mask must appear within 6 sim-hours.
    """
    key = "boulder_co"
    d = _controlled_tile_dir(tmp_path, key, size_km=20.0, cell_m=1000.0)
    s = SimSession(key, 4, 1, seed="frozen-fire",
                   cfg_overrides={"ensemble_n": 3, "grid_cell_m": 1000.0,
                                  "horizon_h": 48.0},
                   tile_dir=d, roads_module=None)
    ign = _burnable_point(s)
    s.ignite(ign.lat, ign.lon, intensity="low")

    def band_sig(st):
        return json.dumps(st["fire_bands"], sort_keys=True)

    st1 = s.tick()
    sig1 = band_sig(st1)
    assert st1["fire_bands"]["h24"], "h24 band missing on the first tick"

    st4 = None
    for _ in range(3):
        st4 = s.tick()
    assert band_sig(st4) != sig1, (
        "fire_bands byte-identical after 3 ticks — the arrival field is "
        "frozen relative to the clock (seed re-stamped at `now`); bands "
        "must advance every tick per the STATE VISUAL CONTRACT")

    burned_pts = 0
    last = st4
    for _ in range(20):                     # through sim-hour ~6
        last = s.tick()
        burned_pts = sum(len(r) for r in last["burned"])
        if burned_pts:
            break
    assert burned_pts > 0, (
        "no burned ring after ~6 simulated hours of a live fire — the "
        "sub-cell bootstrap deadlock is back")
    assert last["scoreboard"]["violations"] == 0

    # RING-PHASE freeze guard (the bug's second face, observed live on the
    # extended Malibu tile: growth stalled at a 4-cell blob from tick 12 to
    # 36): once rings exist, growth must CONTINUE — the persisted-field
    # architecture sweeps thresholds against sim-now instead of re-basing
    # the front at `now` every tick (which discarded sub-tick progress
    # whenever one cell takes longer to cross than one tick).
    sig_ring = json.dumps({"b": last["burned"], "f": last["fire_bands"]},
                          sort_keys=True)
    later = None
    for _ in range(16):                     # ~4 more sim-hours
        later = s.tick()
    sig_later = json.dumps({"b": later["burned"], "f": later["fire_bands"]},
                           sort_keys=True)
    assert sig_later != sig_ring, (
        "burned area + bands byte-identical 4 sim-hours after the first "
        "ring — the ring-phase re-base freeze is back")
    assert (sum(len(r) for r in later["burned"])
            >= sum(len(r) for r in last["burned"]))
    assert later["scoreboard"]["violations"] == 0


# ---------------------------------------------------------------------------
# LAW 1 TIME SCRUB (contracts FOUNDER ROUND 3 #1): SimSession.state_at(k)
# replays the frame served live at tick k VERBATIM; the ring evicts at
# cfg.history_ticks; reset() clears it. (The server half — GET /state?tick=k
# and POST ctl goto — is in tests/test_server.py.)
# ---------------------------------------------------------------------------

def test_state_at_scrub_byte_identical_in_process(tmp_path):
    """state_at(k) is byte-identical to what tick k emitted live, for every
    retained tick; future / tick-0 / negative / non-integer ticks return None;
    a recorded snapshot is frozen (a later tick never mutates it)."""
    s = _high_session(tmp_path, seed="scrub", n_clients=6, n_crews=2)
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    live = {}
    for _ in range(8):
        st = s.tick()
        live[st["tick"]] = json.dumps(st, sort_keys=True, allow_nan=False)
    assert s.state()["history"] == {"first_tick": 1, "last_tick": 8,
                                    "current_tick": 8}
    for k in range(1, 9):
        snap = s.state_at(k)
        assert json.dumps(snap, sort_keys=True, allow_nan=False) == live[k], (
            f"state_at({k}) is not byte-identical to the live tick-{k} frame")
        # a scrubbed frame carries the tick-k picture verbatim
        assert snap["tick"] == k and snap["history"]["current_tick"] == k
    # out of range / non-integer ticks -> None (server maps to 404)
    assert s.state_at(9) is None and s.state_at(0) is None
    assert s.state_at(-1) is None
    assert s.state_at("3") is None and s.state_at(3.0) is None
    assert s.state_at(True) is None                     # bool is not a tick
    # immutability: a snapshot severed from live state stays byte-stable
    snap3 = json.dumps(s.state_at(3), sort_keys=True, allow_nan=False)
    for _ in range(4):
        s.tick()
    assert json.dumps(s.state_at(3), sort_keys=True, allow_nan=False) == snap3


def test_state_at_history_eviction_and_reset(tmp_path):
    """The DEEP-snapshot ring is bounded by cfg.history_ticks (oldest evicted);
    reset() clears it with the scenario; history_ticks=0 disables scrub."""
    d = _controlled_tile_dir(tmp_path, "boulder_co")
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s = SimSession("boulder_co", 4, 1, seed="ev",
                   cfg_overrides={**FAST, "history_ticks": 3},
                   tile_dir=d, roads_module=None)
    s.ignite(ign.lat, ign.lon, intensity="high")
    for _ in range(6):
        s.tick()
    # after 6 ticks the ring holds exactly the last 3 emitted ticks
    assert s.state()["history"] == {"first_tick": 4, "last_tick": 6,
                                    "current_tick": 6}
    assert s.state_at(3) is None                         # evicted
    for k in (4, 5, 6):
        assert s.state_at(k) is not None
    assert s.state_at(7) is None                         # future
    # reset clears the ring
    s.reset()
    assert s.state()["history"] == {"first_tick": None, "last_tick": None,
                                    "current_tick": 0}
    assert s.state_at(4) is None and s.state_at(1) is None
    # history disabled entirely (history_ticks=0)
    s2 = SimSession("boulder_co", 4, 1, seed="off",
                    cfg_overrides={**FAST, "history_ticks": 0},
                    tile_dir=d, roads_module=None)
    s2.ignite(ign.lat, ign.lon, intensity="high")
    s2.tick()
    assert s2.state()["history"]["first_tick"] is None
    assert s2.state_at(1) is None


# ---------------------------------------------------------------------------
# LAW 3 FULL UTILIZATION (contracts FOUNDER ROUND 3 #3) — the founder defect-2
# fix, MEASURED end-to-end: under heavy simultaneous queue pressure the
# planner exhausts the eligible fleet instead of dumping every stop on one
# crew, and no eligible crew idles while a feasible pending job waits.
# ---------------------------------------------------------------------------

def _utilization_run(tmp_path, n_crews, n_clients, hamlet_half, seed,
                     ticks=28):
    """Forcing high-pressure run: a MODERATE fire 8 km upwind (slow enough
    that many homes stay solo-feasible at once -> real queue pressure) over a
    large porous hamlet, 2x clock. Returns (final state, stops-per-crew from
    the serviced ledger, count of ticks an AVAILABLE idle crew coexisted with
    a capacity/dropped-feasible unserved job)."""
    d = _controlled_tile_dir(tmp_path, "boulder_co", hamlet_half=hamlet_half)
    s = SimSession("boulder_co", n_clients, n_crews, seed=seed,
                   cfg_overrides=dict(FAST), tile_dir=d, roads_module=None)
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="moderate")
    s.set_speed(2.0)
    idle_with_capacity_work = 0
    st = None
    for _ in range(ticks):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, st["audit"]
        avail_idle = [c for c in st["crews"]
                      if c["status"] == "available" and not c["itinerary"]]
        cap_unserved = [u for u in st["unserved"]
                        if u["reason"] in ("capacity", "dropped_by_optimizer")]
        if avail_idle and cap_unserved:
            idle_with_capacity_work += 1
    per_crew = {}
    for sv in st["serviced"]:
        per_crew[sv["crew_id"]] = per_crew.get(sv["crew_id"], 0) + 1
    return st, per_crew, idle_with_capacity_work


@pytest.mark.parametrize("n_crews,n_clients,hamlet_half,seed", [
    (4, 40, 5, "util4"),
    (8, 80, 6, "util8"),
])
def test_full_utilization_spreads_work_across_crews(tmp_path, n_crews,
                                                    n_clients, hamlet_half,
                                                    seed):
    """The defect-2 fix, empirically: every crew is put to work and the load
    is balanced. (A) NO eligible crew sits at ZERO stops for the whole run
    while feasible work waits -> every crew services >= 1 stop. (B) work is
    NOT all-on-one -> no crew hoards more than ~2x its fair share (all-on-one
    would leave `served` stops on a single crew). The per-tick idle-crew-with-
    feasible-work signal is 0 throughout. Derivation runs 2026-07-10 (moderate
    fire 8 km upwind, 2x, 28 ticks, controlled porous hamlet; deterministic):
    4 crews/40 clients -> served 17, per-crew {4,4,4,5}; 8 crews/80 clients ->
    served 16, per-crew all 2 (perfectly balanced)."""
    st, per_crew, idle_ticks = _utilization_run(tmp_path, n_crews, n_clients,
                                                hamlet_half, seed)
    served = st["scoreboard"]["served"]
    active = len(per_crew)
    max_stops = max(per_crew.values()) if per_crew else 0
    fair = served / n_crews
    detail = (
        f"[conceptual all-on-one would be 1 crew x {served} stops; measured] "
        f"served={served} active_crews={active}/{n_crews} max_stops={max_stops} "
        f"per_crew={dict(sorted(per_crew.items()))} "
        f"idle_with_capacity_work_ticks={idle_ticks}")
    # non-vacuous: real, heavy work happened — enough for every crew
    assert served >= n_crews, f"utilization test vacuous — {detail}"
    # (A) no crew idled at zero stops while queue pressure persisted
    assert active == n_crews, (
        f"a crew sat at ZERO stops the whole run under sustained queue "
        f"pressure — LAW 3 full-utilization (defect 2) regressed. {detail}")
    # (B) not all-on-one: load spread within ~2x of the fair per-crew share
    assert max_stops <= math.ceil(2.0 * fair), (
        f"work concentrated on one crew instead of spreading. {detail}")
    # the direct per-tick LAW 3 signal held every tick
    assert idle_ticks == 0, (
        f"an eligible crew idled while capacity-feasible work waited. {detail}")


# ---------------------------------------------------------------------------
# LAW 5 GEL RING (contracts FOUNDER ROUND 3 #5): a serviced home carries
# gel_applied=true + gelled_at_s, keyed off the append-only serviced ledger,
# so the flag PERSISTS through later alert-state changes.
# ---------------------------------------------------------------------------

_ALERT_RANK = {"none": 0, "cleared": 1, "notice_72h": 2, "watch": 3,
               "extreme_24h": 4, "impacted": 5}


def test_gel_applied_persists_through_alert_escalation(tmp_path):
    """A home gelled at one alert state keeps gel_applied=true + the same
    gelled_at_s even as the fire drives it to a STRICTLY HIGHER alert state
    afterward (here a gelled EXTREME_24H home is overrun to IMPACTED). The UI's
    blue 'done' ring must never blink off when the alert climbs."""
    s = _high_session(tmp_path, seed="gel", n_clients=16, n_crews=2)
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    s.set_speed(2.0)
    at_service = {}          # client_id -> (alert state, gelled_at_s) at 1st gel
    escalated = set()
    st = None
    for _ in range(45):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        for c in st["clients"]:
            if not c["gel_applied"]:
                continue
            assert c["serviced"] is True
            if c["client_id"] not in at_service:
                assert c["gelled_at_s"] is not None
                at_service[c["client_id"]] = (c["state"], c["gelled_at_s"])
            else:
                s0, stamp0 = at_service[c["client_id"]]
                # LAW 5: flag + stamp are immutable once set
                assert c["gelled_at_s"] == stamp0, "gelled_at_s changed"
                if _ALERT_RANK[c["state"]] > _ALERT_RANK[s0]:
                    escalated.add(c["client_id"])
    assert at_service, "nothing gelled — re-pin scenario"
    assert escalated, (
        "no gelled home's alert state rose after service — the escalation that "
        "exercises LAW 5 persistence never happened (re-pin scenario)")
    # final frame: every home ever gelled still carries the flag + its stamp
    final = {c["client_id"]: c for c in st["clients"]}
    for cid, (_s0, stamp0) in at_service.items():
        assert final[cid]["gel_applied"] is True
        assert final[cid]["gelled_at_s"] == stamp0


# ---------------------------------------------------------------------------
# LAW 4 TRIAGE (contracts FOUNDER ROUND 3 #4): in the real 'homes' default a
# job feasible on its own but whose service would cost strictly MORE savable
# homes elsewhere is skipped and surfaced 'triaged' in the scoreboard.
# ---------------------------------------------------------------------------

def _triage_scatter_dir(tmp_path, seed, key="boulder_co", size_km=24.0,
                        cell_m=500.0, n_hamlets=12):
    """Uniform grass with ~12 small developed hamlets scattered ACROSS the
    whole tile (deterministic from `seed`), so placed clients are far apart —
    the spatial spread homes-mode triage keys off (inserting a lone far home
    costs a NET home vs a tight cluster). Distinct from the central-hamlet
    geometry the other tests use."""
    from wgresponse.contracts import seeded_rng
    loc = LOCATIONS[key]
    bbox = geo.bbox_around([loc.center], pad_m=size_km * 500.0)
    tile = SyntheticTerrain(seed=f"simui-test|{key}", kind="grass").tile(
        bbox, cell_m)
    spec = tile.spec
    rng = seeded_rng("triage-scatter", seed)
    for _ in range(n_hamlets):
        r0 = rng.randrange(4, spec.nrows - 4)
        c0 = rng.randrange(4, spec.ncols - 4)
        for r in range(r0, r0 + 3):
            for c in range(c0, c0 + 3):
                if (r + c) % 2 == 0:
                    tile.fuel.set(r, c, 91.0)
    d = tmp_path / f"scatter_{seed}"
    d.mkdir(exist_ok=True)
    save_ascii_tile(tile, str(d / f"{key}.wgt"))
    return d


def _nudge_to_burnable(session, p, max_m=4000, step=250):
    """Nearest burnable cell to p (returns p itself when already burnable) —
    scatter tiles have developed gaps that would reject ignition."""
    from wgresponse.fuels import is_burnable
    spec = session._tile.spec

    def burn(q):
        rc = geo.latlon_to_rc(spec, q)
        return rc is not None and is_burnable(
            int(round(session._tile.fuel.get(*rc))))

    if burn(p):
        return p
    for dd in range(step, max_m + 1, step):
        for brg in (0.0, 90.0, 180.0, 270.0, 45.0, 135.0, 225.0, 315.0):
            q = geo.destination(p, brg, dd)
            if burn(q):
                return q
    raise AssertionError("no burnable cell near the ignition point")


def test_homes_mode_triage_reason_surfaces(tmp_path):
    """'triaged' surfaces in scoreboard.unserved_by_reason under the real
    'homes' default, produced by the FULL engine loop (not a hand-built
    dispatch call): a long-horizon moderate fire (generous, all-solo-feasible
    deadlines) over homes SPREAD across the tile with a single crew, so the
    count-maximal plan must abandon the expensive-to-insert far homes. Distinct
    from the safety-withdrawal vocabulary and from capacity/deadline misses.
    Derivation run 2026-07-10 (seed C, 50 clients / 1 crew, horizon 120 h,
    moderate, 5 km upwind, 1x): 'triaged' appears from tick 1 and holds;
    violations 0 throughout."""
    d = _triage_scatter_dir(tmp_path, seed="C")
    s = SimSession("boulder_co", 50, 1, seed="C",
                   cfg_overrides={"ensemble_n": 3, "grid_cell_m": 500.0,
                                  "horizon_h": 120.0},
                   tile_dir=d, roads_module=None)
    assert s._cfg.dispatch_objective == "homes", "test must run the real default"
    ign = _nudge_to_burnable(s, _upwind_ignition("boulder_co", 5_000.0))
    s.ignite(ign.lat, ign.lon, intensity="moderate")

    saw_triaged = False
    triaged_max = 0
    st = None
    for _ in range(12):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, st["audit"]
        reasons = st["scoreboard"]["unserved_by_reason"]
        if "triaged" in reasons:
            saw_triaged = True
            triaged_max = max(triaged_max, reasons["triaged"])
            # each 'triaged' count is backed by real still-unserved jobs on the
            # unserved list carrying that exact reason (never serviced)
            tri = [u for u in st["unserved"] if u["reason"] == "triaged"]
            assert len(tri) == reasons["triaged"]
            serviced_ids = {sv["client_id"] for sv in st["serviced"]}
            for u in tri:
                assert u["client_id"] not in serviced_ids
    assert saw_triaged, (
        f"'triaged' never surfaced in homes mode — LAW 4 objective-side "
        f"abandonment is not reaching the scoreboard (re-pin scenario). "
        f"final reasons={st['scoreboard']['unserved_by_reason']}")
    assert triaged_max >= 1
    # triage is an OBJECTIVE-side skip, NOT a safety event: no withdrawals, and
    # the safety-withdrawal reason never appears for a triaged home
    assert st["scoreboard"]["withdrawn"] == 0
    assert not any(u["reason"] == "withdrawn_unsafe" for u in st["unserved"])


# ---------------------------------------------------------------------------
# FOUNDER ROUND 4 law 6 (EMBER / ADJACENCY THREAT): the simulator threads
# cfg.ember_threat_radius_m (default 200 m) into EVERY spread.eta_at call, so a
# developed (non-burnable) home the fire has reached the edge of inherits the
# earliest adjacent burnable arrival -> finite ETA -> alert -> dispatch job,
# while isolated developed homes stay unthreatened. Reached (burnable) homes
# are byte-identical to the radius-off path.
# ---------------------------------------------------------------------------

def _nb_cell(session, c):
    """True iff client-state dict `c` sits on a non-burnable cell of the
    session tile."""
    from wgresponse.fuels import is_burnable
    rc = geo.latlon_to_rc(session._tile.spec, LatLon(c["lat"], c["lon"]))
    return rc is not None and not is_burnable(
        int(round(session._tile.fuel.get(*rc))))


def test_ember_radius_default_and_precedence(tmp_path):
    """The SimSession sets cfg.ember_threat_radius_m to the 200 m sim default
    UNLESS the caller pins it in cfg_overrides (same precedence as the
    road-spur ceiling / extended-zone horizon floor). ResponseConfig's own
    default stays 0.0 (the live product is untouched)."""
    from wgresponse.contracts import ResponseConfig
    assert ResponseConfig().ember_threat_radius_m == 0.0     # product default
    assert SIM_EMBER_THREAT_RADIUS_M == 200.0
    d = _controlled_tile_dir(tmp_path, "boulder_co")
    s = SimSession("boulder_co", 4, 1, seed="er", cfg_overrides=dict(FAST),
                   tile_dir=d, roads_module=None)
    assert s._cfg.ember_threat_radius_m == 200.0             # sim default
    s2 = SimSession("boulder_co", 4, 1, seed="er",
                    cfg_overrides={**FAST, "ember_threat_radius_m": 90.0},
                    tile_dir=d, roads_module=None)
    assert s2._cfg.ember_threat_radius_m == 90.0             # explicit wins
    s3 = SimSession("boulder_co", 4, 1, seed="er",
                    cfg_overrides={**FAST, "ember_threat_radius_m": 0.0},
                    tile_dir=d, roads_module=None)
    assert s3._cfg.ember_threat_radius_m == 0.0              # may pin OFF


def _ember_run(tmp_path, radius, seed="ember", ticks=20):
    """Synthetic-fallback boulder session (stamped urban hamlets -> NB cells),
    high fire 8 km upwind run for `ticks` at 2x. Returns the session + final
    state; the tile + client placement are byte-identical across radii (radius
    only changes eta_at), so ON/OFF runs are directly comparable."""
    cfg = dict(FAST)
    cfg["ember_threat_radius_m"] = radius
    s = SimSession("boulder_co", 16, 3, seed=seed, cfg_overrides=cfg,
                   tile_dir=tmp_path, synth_size_km=12.0, roads_module=None)
    ign = _nudge_to_burnable(s, _upwind_ignition("boulder_co", 8_000.0))
    s.ignite(ign.lat, ign.lon, intensity="high")
    s.set_speed(2.0)
    st = None
    for _ in range(ticks):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
    return s, st


def test_ember_wiring_developed_homes_alert_and_dispatch(tmp_path):
    """PAIRED on/off (law 6). With the radius OFF every developed-cell home
    stays UNREACHED (no ETA, never jobbed) — byte-identical to the raw surface
    model. With the radius ON at least one fire-ADJACENT developed home gains a
    finite ETA and enters the dispatch job pool; isolated developed homes stay
    not_threatened. Reached (burnable) homes are byte-identical either way."""
    s_off, st_off = _ember_run(tmp_path / "off", 0.0)
    s_on, st_on = _ember_run(tmp_path / "on", 200.0)

    nb_off = [c for c in st_off["clients"] if _nb_cell(s_off, c)]
    nb_on = [c for c in st_on["clients"] if _nb_cell(s_on, c)]
    assert nb_off, "no client landed on a developed (NB) cell — re-pin geometry"
    assert len(nb_off) == len(nb_on)     # same tile + placement across radii

    # radius OFF: the surface model can never reach a developed cell -> no ETA,
    # never jobbed, outcome not_threatened (the founder's c34 gap)
    for c in nb_off:
        assert c["eta_p50_s"] is None and c["eta_p10_s"] is None
        assert c["client_id"] not in s_off._jobbed
        assert c["outcome"] == "not_threatened"

    # radius ON: fire-adjacent developed homes DO alert + dispatch...
    ember = [c for c in nb_on if c["eta_p50_s"] is not None]
    assert ember, "ember fallback gave no developed home an ETA — re-pin"
    for c in ember:
        assert c["client_id"] in s_on._jobbed          # entered the job pool
        assert c["state"] != "none"                    # real ladder state
        assert c["outcome"] != "not_threatened"        # actioned
    # ...tracked by the real engine (assigned / serviced / unserved-with-reason)
    tracked = ({u["client_id"] for u in st_on["unserved"]}
               | {sv["client_id"] for sv in st_on["serviced"]}
               | {s["client_id"] for cr in st_on["crews"] for s in cr["itinerary"]})
    assert any(c["client_id"] in tracked for c in ember)

    # isolated developed homes (no burnable arrival in range) stay unthreatened
    isolated = [c for c in nb_on if c["eta_p50_s"] is None]
    for c in isolated:
        assert c["outcome"] == "not_threatened"

    # REACHED homes are byte-identical ON vs OFF: the fallback fires ONLY when
    # the own cell is UNREACHED, so every home whose own cell the fire reached
    # (finite ETA with the radius OFF) is untouched — the fallback only ADDS
    # threat to previously-UNREACHED homes (strictly more homes threatened =
    # conservative; the live product-safety guarantee at the simulator seam).
    off_by_id = {c["client_id"]: c for c in st_off["clients"]}
    reached_checked = 0
    for c in st_on["clients"]:
        o = off_by_id[c["client_id"]]
        if o["eta_p10_s"] is not None:          # own cell reached -> no fallback
            assert c["eta_p10_s"] == o["eta_p10_s"]
            assert c["eta_p50_s"] == o["eta_p50_s"]
            reached_checked += 1
    assert reached_checked, "no reached home to compare — re-pin scenario"


def _water_tile_dir(tmp_path, key="boulder_co", size_km=20.0, cell_m=500.0):
    """Uniform grass with a central checkerboard of WATER (98) + BARREN (99)
    cells (adjacent to grass, so in the burnable-adjacent overflow pool) and NO
    developed 91/93 cells: the WUI band is empty, every client is drawn from
    the overflow pool, so some land on 98/99 — a home on a non-91/93 NB code."""
    loc = LOCATIONS[key]
    bbox = geo.bbox_around([loc.center], pad_m=size_km * 500.0)
    tile = SyntheticTerrain(seed=f"nb-note|{key}", kind="grass").tile(bbox, cell_m)
    spec = tile.spec
    r0, c0 = spec.nrows // 2, spec.ncols // 2
    for r in range(r0 - 2, r0 + 3):
        for c in range(c0 - 2, c0 + 3):
            if (r + c) % 2 == 0:
                tile.fuel.set(r, c, 98.0 if (r + c) % 4 == 0 else 99.0)
    d = tmp_path / f"nb_{key}"
    d.mkdir(exist_ok=True)
    save_ascii_tile(tile, str(d / f"{key}.wgt"))
    return d


def test_n_nb_note_counts_all_non_burnable_codes(tmp_path):
    """The configure-time census counts ALL non-burnable codes (91/92/93/98/99),
    not just developed 91/93 (eta_gap_report latent finding: the overflow pool
    can place clients on 92/98/99, which the old 91/93-only note undercounted —
    it would have emitted NO note here). The note's N equals the independently
    computed count of clients on any NB cell and names the ember radius."""
    from wgresponse.fuels import is_burnable
    d = _water_tile_dir(tmp_path)
    s = SimSession("boulder_co", 12, 1, seed="nb", cfg_overrides=dict(FAST),
                   tile_dir=d, roads_module=None)
    spec = s._tile.spec
    codes = [int(round(s._tile.fuel.get(*geo.latlon_to_rc(spec, c.loc))))
             for c in s._clients]
    nb_all = [x for x in codes if not is_burnable(x)]
    non_9193 = [x for x in nb_all if x not in DEVELOPED_CODES]
    # non-vacuous: a client really landed on a non-91/93 NB code the OLD census
    # (91/93 only) would have missed entirely
    assert non_9193, "no client on a 92/98/99 cell — test would be vacuous"

    note = [n for n in s.state()["notes"]
            if "non-burnable 91/92/93/98/99" in n]
    assert len(note) == 1, s.state()["notes"]
    m = re.match(r"(\d+) of (\d+) homes", note[0])
    assert int(m.group(1)) == len(nb_all)          # counts ALL NB codes
    assert int(m.group(2)) == len(s._clients)
    assert "ember-threat radius" in note[0]        # law-6 reword


# ---------------------------------------------------------------------------
# FOUNDER ROUND 4 law 8 (PER-CLIENT OUTCOME TAG): state.clients[i].outcome +
# scoreboard.outcomes explain why each home is/isn't protected. Pure projection
# of tick state; carried in the scrub history so replays tag too.
# ---------------------------------------------------------------------------

def test_client_outcome_precedence_pure():
    """The pure law-8 derivation, every tag + the precedence rules (contracts
    law 8): gelled states win over unserved reasons; impacted+gel is
    protected_then_impacted; an ungelled impacted home is abandoned_unsavable
    even under a stale 'triaged' reason; not-jobbed is not_threatened."""
    O = _client_outcome
    # gelled wins over EVERYTHING (even a stale unsavable/triaged reason)
    assert O(gelled=True, impacted=False, threatened=True, in_progress=False,
             assigned=False, reason="no_safe_corridor") == "saved"
    assert O(gelled=True, impacted=True, threatened=True, in_progress=True,
             assigned=True, reason="triaged") == "protected_then_impacted"
    # not jobbed + NO projected ETA -> not_threatened (never mind a lingering
    # reason)
    assert O(gelled=False, impacted=False, threatened=False, in_progress=False,
             assigned=False, reason=None) == "not_threatened"
    assert O(gelled=False, impacted=False, threatened=False, in_progress=False,
             assigned=False, reason="capacity", has_eta=False) == "not_threatened"
    # not jobbed + a FINITE projected ETA (beyond the notice horizon) ->
    # monitoring, NOT not_threatened (round-4 verifier fix: an ETA on the map
    # must never read "not threatened"); monitoring sits between not_threatened
    # and pending and applies ONLY to the not-jobbed branch
    assert O(gelled=False, impacted=False, threatened=False, in_progress=False,
             assigned=False, reason=None, has_eta=True) == "monitoring"
    assert O(gelled=False, impacted=False, threatened=False, in_progress=False,
             assigned=False, reason="triaged", has_eta=True) == "monitoring"
    # has_eta NEVER promotes a JOBBED (threatened) home to monitoring — a
    # <=notice home keeps its threatened-branch tag ...
    assert O(gelled=False, impacted=False, threatened=True, in_progress=False,
             assigned=True, reason=None, has_eta=True) == "pending"
    assert O(gelled=False, impacted=False, threatened=True, in_progress=True,
             assigned=True, reason=None, has_eta=True) == "in_progress"
    # ... nor a GELLED home (gel still wins over everything)
    assert O(gelled=True, impacted=False, threatened=False, in_progress=False,
             assigned=False, reason=None, has_eta=True) == "saved"
    # a crew actively on it
    assert O(gelled=False, impacted=False, threatened=True, in_progress=True,
             assigned=True, reason=None) == "in_progress"
    # impacted ungelled home = fire beat every feasible crew (beats stale reason)
    assert O(gelled=False, impacted=True, threatened=True, in_progress=False,
             assigned=True, reason="triaged") == "abandoned_unsavable"
    # a live future assignment (assigned but not the active stop) -> pending
    assert O(gelled=False, impacted=False, threatened=True, in_progress=False,
             assigned=True, reason="no_safe_corridor") == "pending"
    # every terminal unsavable reason -> abandoned_unsavable
    for reason in ("infeasible_deadline", "infeasible_slack",
                   "gel_window_closed", "no_safe_corridor", "no_safe_egress",
                   "fire_arrived", "withdrawn_unsafe"):
        assert O(gelled=False, impacted=False, threatened=True,
                 in_progress=False, assigned=False, reason=reason) \
            == "abandoned_unsavable", reason
    # the objective's deliberate skip
    assert O(gelled=False, impacted=False, threatened=True, in_progress=False,
             assigned=False, reason="triaged") == "triaged"
    # transient capacity / no reason -> pending (feasible, awaiting a crew)
    for reason in (None, "capacity", "dropped_by_optimizer"):
        assert O(gelled=False, impacted=False, threatened=True,
                 in_progress=False, assigned=False, reason=reason) == "pending"


#: Monitoring band scenario: a wide (notice_h, horizon_h] window (small
#: notice_h, long horizon) so a far moderate fire projects a FINITE eta_p50 for
#: homes that is still BEYOND the notice horizon — the 'monitoring' band. extreme_h
#: stays < notice_h (validate_config). config-only, never forked physics.
MON_CFG = {"ensemble_n": 3, "grid_cell_m": 500.0, "horizon_h": 48.0,
           "notice_h": 3.0, "extreme_h": 1.0}


def test_outcome_monitoring_not_mislabeled_not_threatened(tmp_path):
    """round-4 verifier fix: a home with a FINITE eta_p50 beyond the notice
    horizon (inside horizon_h, past notice_h — not yet an alert/dispatch job)
    is tagged 'monitoring', NOT 'not_threatened'. Its finite ETA shows in the
    same payload, so 'not_threatened' would self-contradict. Invariants every
    tick: not_threatened <=> NO projected ETA; monitoring <=> a finite eta_p50,
    ungelled, un-jobbed; the breakdown sums to len(clients) over the full
    vocabulary. As the fire nears, homes cross into the notice window and LEAVE
    monitoring (become jobbed) — a <=notice home is never tagged monitoring."""
    d = _controlled_tile_dir(tmp_path, "boulder_co")
    s = SimSession("boulder_co", 12, 2, seed="mon", cfg_overrides=dict(MON_CFG),
                   tile_dir=d, roads_module=None)
    ign = _nudge_to_burnable(s, _upwind_ignition("boulder_co", 7_000.0))
    s.ignite(ign.lat, ign.lon, intensity="moderate")
    s.set_speed(2.0)
    saw_monitoring = False
    saw_jobbed_after_monitoring = False
    st = None
    for _ in range(16):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        outs = st["scoreboard"]["outcomes"]
        # stable vocabulary incl. the new tag; sums to exactly one per home
        assert set(outs) == set(OUTCOME_TAGS)
        assert sum(outs.values()) == len(st["clients"])
        for c in st["clients"]:
            # the contradiction is gone: a finite ETA is NEVER 'not_threatened'
            if c["outcome"] == "not_threatened":
                assert c["eta_p50_s"] is None
            if c["outcome"] == "monitoring":
                saw_monitoring = True
                assert c["eta_p50_s"] is not None          # a projected arrival
                assert not c["gel_applied"]
                assert c["client_id"] not in s._jobbed     # not yet a dispatch job
        if saw_monitoring and s._jobbed:
            saw_jobbed_after_monitoring = True
    assert saw_monitoring, "no monitoring home surfaced — re-pin band scenario"
    # homes crossed into the notice window and left monitoring (jobbed) — the
    # <=notice_h path is unaffected by the monitoring branch
    assert saw_jobbed_after_monitoring, "no home crossed notice into a job"


def test_outcome_saved_then_protected_then_impacted(tmp_path):
    """A gelled home is 'saved' the moment gel is applied (fire not yet
    arrived), then flips to 'protected_then_impacted' when the fire overruns it
    — the intended GraphGel outcome. A gelled home's outcome is NEVER anything
    but those two."""
    s = _high_session(tmp_path, seed="gel-out", n_clients=16, n_crews=2)
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    s.set_speed(2.0)
    first_gel_outcome = {}
    saved_seen = protected_seen = False
    st = None
    for _ in range(45):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        for c in st["clients"]:
            # gelled homes only ever carry the two gel outcomes (gel wins)
            if c["gel_applied"]:
                assert c["outcome"] in ("saved", "protected_then_impacted")
                first_gel_outcome.setdefault(c["client_id"], c["outcome"])
            else:
                assert c["outcome"] not in ("saved", "protected_then_impacted")
            saved_seen |= c["outcome"] == "saved"
            protected_seen |= c["outcome"] == "protected_then_impacted"
    assert first_gel_outcome, "nothing gelled — re-pin scenario"
    # gel is applied AHEAD of the fire, so the first outcome at service is 'saved'
    assert all(v == "saved" for v in first_gel_outcome.values()), first_gel_outcome
    assert saved_seen and protected_seen


def test_outcome_abandoned_unsavable_too_close_home(tmp_path):
    """A home too close to an extreme ignition for the lone far crew to reach,
    service and egress in time is 'abandoned_unsavable' (fire beat every
    feasible crew — correct triage of an unsavable home). Robust across seeds:
    ignite extreme right next to a placed client."""
    for seed in ("ab1", "ab3", "abX"):
        d = tmp_path / f"ab_{seed}"
        s = SimSession("boulder_co", 16, 1, seed=seed, cfg_overrides=dict(FAST),
                       tile_dir=d, synth_size_km=12.0, roads_module=None)
        ignp = _nudge_to_burnable(s, s._clients[0].loc)
        s.ignite(ignp.lat, ignp.lon, intensity="extreme")
        s.set_speed(4.0)
        saw = False
        st = None
        for _ in range(24):
            st = s.tick()
            assert st["scoreboard"]["violations"] == 0
            reason_by = {u["client_id"]: u["reason"] for u in st["unserved"]}
            for c in st["clients"]:
                if c["outcome"] != "abandoned_unsavable":
                    continue
                saw = True
                # invariant: an abandoned home is NOT gelled AND is either
                # impacted (fire reached it) or carries an unsavable reason
                assert not c["gel_applied"]
                impacted = (c["state"] == "impacted"
                            or (c["eta_p50_s"] is not None
                                and c["eta_p50_s"] <= st["sim_now_s"]))
                assert impacted or reason_by.get(c["client_id"]) in {
                    "infeasible_deadline", "infeasible_slack",
                    "gel_window_closed", "no_safe_corridor", "no_safe_egress",
                    "fire_arrived", "withdrawn_unsafe"}, (
                    c["client_id"], c["state"], reason_by.get(c["client_id"]))
        assert saw, f"seed {seed}: no abandoned_unsavable home surfaced"


def test_outcome_triaged_matches_reason(tmp_path):
    """A home the count-max objective deliberately deprioritizes ('triaged'
    unserved reason) reads as the 'triaged' OUTCOME — distinct from an
    abandoned (unsavable) or pending home. Scatter geometry (LAW 4)."""
    d = _triage_scatter_dir(tmp_path, seed="C")
    s = SimSession("boulder_co", 50, 1, seed="C",
                   cfg_overrides={"ensemble_n": 3, "grid_cell_m": 500.0,
                                  "horizon_h": 120.0},
                   tile_dir=d, roads_module=None)
    ign = _nudge_to_burnable(s, _upwind_ignition("boulder_co", 5_000.0))
    s.ignite(ign.lat, ign.lon, intensity="moderate")
    saw_triaged = False
    st = None
    for _ in range(12):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        tri = [c for c in st["clients"] if c["outcome"] == "triaged"]
        if tri:
            saw_triaged = True
            for c in tri:
                assert not c["gel_applied"]
                # backed by the real unserved 'triaged' reason (not serviced)
                assert s._unserved_reason.get(c["client_id"]) == "triaged"
                assert not c["serviced"]
    assert saw_triaged, (
        "'triaged' outcome never surfaced — re-pin (LAW 4 objective skip)")


def test_outcomes_scoreboard_shape_sums_and_consistency(tmp_path):
    """scoreboard.outcomes is a stable {tag: count} over the full vocabulary,
    non-negative, summing to len(clients) (every home gets exactly one
    outcome); every client's outcome is a known tag; and the cross-field
    invariants hold every tick (gel<->saved/protected; abandoned=>ungelled;
    not_threatened=>no ETA/assignment)."""
    s = _high_session(tmp_path, seed="out-sb", n_clients=16, n_crews=2)
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    s.set_speed(2.0)
    seen = set()
    st = None
    for _ in range(30):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        outs = st["scoreboard"]["outcomes"]
        assert set(outs) == set(OUTCOME_TAGS)               # every tag present
        assert all(v >= 0 for v in outs.values())
        assert sum(outs.values()) == len(st["clients"])     # exactly one each
        # the breakdown matches the per-client tags exactly
        tally = {}
        for c in st["clients"]:
            assert c["outcome"] in OUTCOME_TAGS
            tally[c["outcome"]] = tally.get(c["outcome"], 0) + 1
            seen.add(c["outcome"])
            gel_out = c["outcome"] in ("saved", "protected_then_impacted")
            assert gel_out == c["gel_applied"]
            if c["outcome"] == "abandoned_unsavable":
                assert not c["gel_applied"]
            if c["outcome"] == "not_threatened":
                assert not c["gel_applied"] and not c["assigned"]
                assert c["eta_p50_s"] is None       # FAST horizon <= notice
        assert {t: tally.get(t, 0) for t in OUTCOME_TAGS} == outs
    # a real, varied run exercised several tags (not a vacuous all-one-bucket)
    assert len(seen) >= 3, seen


def test_outcome_tags_carried_in_scrub_history(tmp_path):
    """LAW 1 TIME-SCRUB carries the law-8 tags: every recorded snapshot has
    per-client outcome + scoreboard.outcomes, byte-identical to the live frame
    (so scrubbing back replays the outcome breakdown too)."""
    s = _high_session(tmp_path, seed="out-scrub", n_clients=12, n_crews=2)
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    s.set_speed(2.0)
    live = {}
    for _ in range(8):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        live[st["tick"]] = json.dumps(st, sort_keys=True, allow_nan=False)
    for k in range(1, 9):
        snap = s.state_at(k)
        assert snap is not None
        # tags present in the replay
        assert all("outcome" in c for c in snap["clients"])
        assert set(snap["scoreboard"]["outcomes"]) == set(OUTCOME_TAGS)
        # byte-identical to what tick k served live
        assert json.dumps(snap, sort_keys=True, allow_nan=False) == live[k]


def test_ember_wiring_replays_byte_identically(tmp_path):
    """Determinism holds through the ember fallback + the outcome tags: same
    (seed, script) with the 200 m radius replays byte-identically."""
    def run():
        d = _controlled_tile_dir(tmp_path, "boulder_co", hamlet_half=3)
        s = SimSession("boulder_co", 16, 2, seed="ember-repro",
                       cfg_overrides=dict(FAST), tile_dir=d, roads_module=None)
        ign = _upwind_ignition("boulder_co", 8_000.0)
        s.ignite(ign.lat, ign.lon, intensity="high")
        s.set_speed(2.0)
        return [_state_hash(s.tick()) for _ in range(6)]
    assert run() == run()


# ---------------------------------------------------------------------------
# REALISTIC-FIRE PHYSICS TOGGLES (contracts SAFETY SEMANTICS 11 ember spotting +
# cfg.grass_ros_floor_frac grass head-ROS floor). Both live in ResponseConfig
# default OFF (live byte-identical); the SIMULATOR turns them ON via the server
# path. A DIRECT SimSession leaves configure's base cfg untouched, so these two
# stay at the product defaults and every scripted engine test is byte-identical.
# ---------------------------------------------------------------------------

#: Short horizon (< notice_h) so a home's finite eta_p50 means it is jobbed:
#: physics-ON reaches homes within the horizon that OFF's slower surface fire
#: does not, and pulls the earliest arrival (embers) forward — a real on/off
#: delta over a short run. config-only, never forked physics.
PHYS_CFG = {"ensemble_n": 3, "grid_cell_m": 500.0, "horizon_h": 12.0}


def _first_fire_burned_cells(session):
    """Count of BURNED cells (nominal-member arrival <= sim-now) of the session's
    first fire — the actual fire footprint, independent of the forecast horizon.
    A larger count at the same sim time == a materially faster fire."""
    fire = session._fires[session._fire_order[0]]
    nominal = fire.ens.members[0]
    now = session._now_s
    return sum(1 for v in nominal.data if v <= now)


def _threatened_homes(state):
    """Homes the forecast projects a finite arrival for (eta_p50 finite)."""
    return sum(1 for c in state["clients"] if c["eta_p50_s"] is not None)


def _earliest_home_p10(state):
    """Earliest (soonest) client eta_p10 in the state, or None — the embers-
    ahead proxy: spotting pulls the pessimistic front forward."""
    p10 = [c["eta_p10_s"] for c in state["clients"] if c["eta_p10_s"] is not None]
    return min(p10) if p10 else None


def _phys_session(tile_dir, physics_on, seed="phys"):
    cfg = dict(PHYS_CFG)
    if physics_on:
        cfg["spotting_enabled"] = True       # SEMANTICS 11 ember spotting
        cfg["grass_ros_floor_frac"] = 0.10   # grass head-ROS floor
    s = SimSession("boulder_co", 12, 2, seed=seed, cfg_overrides=cfg,
                   tile_dir=tile_dir, roads_module=None)
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    s.set_speed(2.0)
    return s


def test_physics_toggles_make_fire_faster_and_threaten_more(tmp_path):
    """The two realistic-fire toggles (spotting_enabled + grass_ros_floor_frac),
    the server maps `spotting`/`grass_realism` onto, MATERIALLY change the fire
    over a short run vs OFF, identical seed + geometry: the fire burns faster
    (>= as many burned cells every tick, strictly more early), embers land ahead
    (earliest home P10 arrives sooner) and more homes are threatened — and the
    continuous crew-safety machinery still holds 0 violations under the faster
    fire."""
    d = _controlled_tile_dir(tmp_path, "boulder_co")   # ONE identical tile
    s_off = _phys_session(d, physics_on=False)
    s_on = _phys_session(d, physics_on=True)
    # the toggle really is off/on on the engine cfg
    assert (s_off._cfg.spotting_enabled, s_off._cfg.grass_ros_floor_frac) == \
        (False, 0.0)
    assert (s_on._cfg.spotting_enabled, s_on._cfg.grass_ros_floor_frac) == \
        (True, 0.10)

    faster_tick = False
    st_off = st_on = None
    for _ in range(5):
        st_off, st_on = s_off.tick(), s_on.tick()
        assert st_off["scoreboard"]["violations"] == 0
        assert st_on["scoreboard"]["violations"] == 0
        b_off, b_on = _first_fire_burned_cells(s_off), _first_fire_burned_cells(s_on)
        assert b_on >= b_off                    # never slower
        faster_tick |= b_on > b_off
    assert faster_tick, "physics-ON never burned more area — re-pin scenario"

    # more homes threatened, and the earliest arrival (embers) lands sooner
    assert _threatened_homes(st_on) > _threatened_homes(st_off)
    p10_on, p10_off = _earliest_home_p10(st_on), _earliest_home_p10(st_off)
    assert p10_on is not None and p10_off is not None
    assert p10_on < p10_off, (p10_on, p10_off)


def test_physics_defaults_off_byte_identical(tmp_path):
    """SimSession-DIRECT construction leaves the two physics toggles at the
    ResponseConfig product defaults (spotting OFF, grass floor 0.0): a session
    built with no toggle in cfg_overrides is BYTE-IDENTICAL, tick for tick, to
    one that pins them explicitly OFF — proof the simulator did NOT bake physics
    into configure's base cfg (so the whole scripted engine suite needs no
    re-pin). state echoes both flags as False."""
    d = _controlled_tile_dir(tmp_path, "boulder_co")   # ONE identical tile

    def _phys_hash(st):
        # everything the physics drives; cmd_log legitimately differs (the
        # explicit session echoes the extra cfg_overrides keys) so drop it
        return _state_hash({k: v for k, v in st.items() if k != "cmd_log"})

    def run(cfg):
        s = SimSession("boulder_co", 8, 2, seed="off-id", cfg_overrides=cfg,
                       tile_dir=d, roads_module=None)
        ign = _upwind_ignition("boulder_co", 8_000.0)
        s.ignite(ign.lat, ign.lon, intensity="high")
        s.set_speed(2.0)
        return s, [_phys_hash(s.tick()) for _ in range(6)]

    s_default, h_default = run(dict(FAST))
    _s_explicit, h_explicit = run(
        {**FAST, "spotting_enabled": False, "grass_ros_floor_frac": 0.0})
    assert h_default == h_explicit            # byte-identical, tick for tick

    st = s_default.state()
    assert st["spotting"] is False and st["grass_realism"] is False
    assert s_default._cfg.spotting_enabled is False
    assert s_default._cfg.grass_ros_floor_frac == 0.0


# ---------------------------------------------------------------------------
# REGIONAL / MUTUAL-AID CREWS (contracts FOUNDER ROUND 5 SEMANTICS 9): three
# crew tiers (in-zone + reg_1h + reg_3h). Reinforcements stand by INBOUND until
# the FIRST ignition mobilizes them (available_at_s = ignition + delay), arrive
# at a staging safe zone, and only THEN become dispatch-eligible. An INBOUND
# crew is never in a plan and never an idle full-utilization failure. Arrival is
# a pure function of (ignition time + delay) -> deterministic + scrub-replayable.
# ---------------------------------------------------------------------------

def _reinf_session(tmp_path, n_crews=1, n_crews_1h=1, n_crews_3h=1,
                   n_clients=24, seed="reinf", d1=15.0, d3=30.0,
                   key="boulder_co", hamlet_half=3):
    """A scripted session WITH out-of-region reinforcements. d1/d3 override the
    1h/3h mobilization delays (minutes) so arrivals land inside a short run."""
    d = _controlled_tile_dir(tmp_path, key, hamlet_half=hamlet_half)
    cfg = dict(FAST)
    cfg["crew_reinforce_1h_min"] = float(d1)
    cfg["crew_reinforce_3h_min"] = float(d3)
    return SimSession(key, n_clients, n_crews, seed=seed, cfg_overrides=cfg,
                      tile_dir=d, roads_module=None,
                      n_crews_1h=n_crews_1h, n_crews_3h=n_crews_3h)


def test_reinforcement_tier_construction_and_counts(tmp_path):
    """Three tiers build with stable/deterministic ids: total crews =
    n_crews + n_crews_1h + n_crews_3h; in-zone crews are AVAILABLE now, region
    'in_zone'; reinforcements are INBOUND, region 'reg_1h'/'reg_3h', with
    available_at_s None (standby, no mobilization clock yet) and parked on the
    tile EDGE toward their region (reg_1h east, reg_3h west)."""
    s = _reinf_session(tmp_path, n_crews=2, n_crews_1h=2, n_crews_3h=1,
                       seed="tiers", d1=60.0, d3=180.0)
    st = s.state()
    crews = st["crews"]
    assert len(crews) == 5                                    # 2 + 2 + 1
    assert [c["crew_id"] for c in crews] == \
        ["sim-g1", "sim-g2", "sim-g3", "sim-g4", "sim-g5"]
    by = {c["crew_id"]: c for c in crews}
    for cid in ("sim-g1", "sim-g2"):
        assert by[cid]["region"] == "in_zone"
        assert by[cid]["inbound"] is False
        assert by[cid]["available_at_s"] is None
        assert by[cid]["arrival_eta_s"] is None
        assert by[cid]["status"] == "available"
    assert by["sim-g3"]["region"] == "reg_1h"
    assert by["sim-g4"]["region"] == "reg_1h"
    assert by["sim-g5"]["region"] == "reg_3h"
    for cid in ("sim-g3", "sim-g4", "sim-g5"):
        assert by[cid]["inbound"] is True
        assert by[cid]["status"] == "inbound"
        assert by[cid]["available_at_s"] is None
        assert by[cid]["arrival_eta_s"] is None
        assert by[cid]["itinerary"] == []
    # staging-entry markers: reg_1h on the EAST edge, reg_3h on the WEST edge,
    # the two reg_1h crews fanned to DISTINCT interior latitudes.
    bbox = st["terrain"]["bbox"]
    assert by["sim-g3"]["lon"] == pytest.approx(bbox["east"], abs=1e-4)
    assert by["sim-g4"]["lon"] == pytest.approx(bbox["east"], abs=1e-4)
    assert by["sim-g5"]["lon"] == pytest.approx(bbox["west"], abs=1e-4)
    assert by["sim-g3"]["lat"] != by["sim-g4"]["lat"]
    for cid in ("sim-g3", "sim-g4", "sim-g5"):
        assert bbox["south"] < by[cid]["lat"] < bbox["north"]
    # JSON-safe from the very first frame (inf/nan never leak)
    json.dumps(st, sort_keys=True, allow_nan=False)


def test_reinforcements_standby_until_first_ignition(tmp_path):
    """With NO fire ignited, reinforcements stay INBOUND on standby forever:
    available_at_s stays None across ticks and they never enter a plan."""
    s = _reinf_session(tmp_path, seed="standby", d1=15.0, d3=30.0)
    reinf = {"sim-g2", "sim-g3"}
    for _ in range(4):
        st = s.tick()                       # ticking with no ignition
        assert st["scoreboard"]["violations"] == 0
        for c in st["crews"]:
            if c["crew_id"] in reinf:
                assert c["inbound"] is True
                assert c["status"] == "inbound"
                assert c["available_at_s"] is None
                assert c["arrival_eta_s"] is None
        stops = st["plan"]["stops"] if st["plan"] else []
        assert not (reinf & {stp["crew_id"] for stp in stops})


def test_first_ignition_stamps_arrival_later_does_not_reset(tmp_path):
    """The FIRST ignition stamps available_at_s = ignition_time + delay for each
    reinforcement (1h and 3h correct); a LATER ignition never re-arms it."""
    s = _reinf_session(tmp_path, n_crews=1, n_crews_1h=1, n_crews_3h=1,
                       seed="stamp", d1=60.0, d3=180.0)
    t_ign = s.state()["sim_now_s"]                       # ignition happens now
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="moderate")
    by = {c["crew_id"]: c for c in s.state()["crews"]}
    assert by["sim-g2"]["region"] == "reg_1h"
    assert by["sim-g3"]["region"] == "reg_3h"
    assert by["sim-g2"]["available_at_s"] == pytest.approx(t_ign + 60.0 * 60.0)
    assert by["sim-g3"]["available_at_s"] == pytest.approx(t_ign + 180.0 * 60.0)
    assert by["sim-g2"]["inbound"] is True and by["sim-g3"]["inbound"] is True
    # advance the clock, then a SECOND ignition: arrival clocks are untouched
    s.set_speed(2.0)
    s.tick(); s.tick()
    second = geo.destination(LOCATIONS["boulder_co"].center, 0.0, 3_000.0)
    s.ignite(second.lat, second.lon, intensity="high")
    by2 = {c["crew_id"]: c for c in s.state()["crews"]}
    assert by2["sim-g2"]["available_at_s"] == pytest.approx(t_ign + 60.0 * 60.0)
    assert by2["sim-g3"]["available_at_s"] == pytest.approx(t_ign + 180.0 * 60.0)


def test_reinforcement_arrival_flip_countdown_and_dispatch(tmp_path):
    """A reinforcement's arrival_eta_s counts down while INBOUND; at the tick
    the mobilization clock elapses it FLIPS to AVAILABLE placed AT its staging
    safe zone (the pre-movement beacon records it there), and from there it is
    ordinary dispatch-eligible work (it picks up a stop / gels a home). The
    per-tick SAFETY AUDIT stays at 0 the whole time."""
    s = _reinf_session(tmp_path, n_crews=1, n_crews_1h=1, n_crews_3h=1,
                       n_clients=24, seed="arrive", d1=15.0, d3=30.0)
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="moderate")
    zone_pts = {(round(z["lat"], 6), round(z["lon"], 6))
                for z in s.state()["safe_zones"]}
    arrived_tick = None
    dispatched = False
    prev_eta = None
    for t in range(1, 16):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        g2 = next(c for c in st["crews"] if c["crew_id"] == "sim-g2")
        assert g2["region"] == "reg_1h"                      # region is fixed
        if g2["inbound"]:
            assert g2["arrival_eta_s"] is not None
            if prev_eta is not None:
                assert g2["arrival_eta_s"] <= prev_eta       # counts DOWN
            prev_eta = g2["arrival_eta_s"]
        elif arrived_tick is None:
            arrived_tick = t
            # PLACED at a staging safe zone: the arrival-tick beacon (recorded
            # before any dispatch could move the crew) sits on a zone.
            last = g2["track"][-1]
            assert last["status"] == "available"
            assert (round(last["lat"], 6), round(last["lon"], 6)) in zone_pts
        if (g2["itinerary"] or g2["status"] in ("en_route", "deploying",
                                                "egressing")
                or any(sv["crew_id"] == "sim-g2" for sv in st["serviced"])):
            dispatched = True
    assert arrived_tick is not None, "reinforcement never arrived — re-pin"
    assert dispatched, "arrived reinforcement never picked up work — re-pin"


def test_inbound_reinforcement_excluded_from_dispatch_and_utilization(tmp_path):
    """An INBOUND reinforcement is NEVER in a dispatch plan and NEVER serviced,
    and it is NOT an idle full-utilization failure (its status is 'inbound', not
    'available'), even under heavy queue pressure that keeps in-zone crews busy.
    Long delays keep both reinforcements inbound for the whole run."""
    s = _reinf_session(tmp_path, n_crews=1, n_crews_1h=1, n_crews_3h=1,
                       n_clients=16, seed="excl", d1=600.0, d3=600.0)
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    s.set_speed(2.0)
    reinf = {"sim-g2", "sim-g3"}
    st = None
    for _ in range(6):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        for c in st["crews"]:
            if c["crew_id"] in reinf:
                assert c["inbound"] is True and c["status"] == "inbound"
        stops = st["plan"]["stops"] if st["plan"] else []
        assert not (reinf & {stp["crew_id"] for stp in stops})       # never planned
        assert not (reinf & {sv["crew_id"] for sv in st["serviced"]})  # never serviced
        # the util idle-signal filters status=='available'; inbound is excluded
        avail_idle = {c["crew_id"] for c in st["crews"]
                      if c["status"] == "available" and not c["itinerary"]}
        assert not (reinf & avail_idle)


def test_reinforcement_scrub_replays_inbound_to_arrived_byte_identically(tmp_path):
    """LAW 1 TIME-SCRUB carries the tier transition: a run spanning the
    inbound -> arrived flip replays every recorded tick byte-identically (the
    inbound marker, the countdown, and the arrival)."""
    s = _reinf_session(tmp_path, n_crews=1, n_crews_1h=1, n_crews_3h=1,
                       n_clients=16, seed="scrub", d1=15.0, d3=45.0)
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="moderate")
    live = {}
    saw_inbound = saw_arrived = False
    for _ in range(10):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        live[st["tick"]] = json.dumps(st, sort_keys=True, allow_nan=False)
        g2 = next(c for c in st["crews"] if c["crew_id"] == "sim-g2")
        saw_inbound = saw_inbound or g2["inbound"]
        saw_arrived = saw_arrived or (not g2["inbound"])
    assert saw_inbound and saw_arrived, "run did not span the transition"
    for k in range(1, 11):
        snap = s.state_at(k)
        assert snap is not None
        assert json.dumps(snap, sort_keys=True, allow_nan=False) == live[k]


def test_reinforcement_determinism_same_script(tmp_path):
    """Same (seed, command script) with reinforcements -> identical state
    sequence (arrivals are a pure function of ignition time + delay)."""
    def run():
        s = _reinf_session(tmp_path, n_crews=1, n_crews_1h=2, n_crews_3h=1,
                           n_clients=16, seed="det", d1=15.0, d3=30.0)
        ign = _upwind_ignition("boulder_co", 8_000.0)
        hs = []
        s.ignite(ign.lat, ign.lon, intensity="moderate")
        for _ in range(8):
            hs.append(_state_hash(s.tick()))
        return hs
    assert run() == run()


def test_zero_reinforcements_behaviorally_identical(tmp_path):
    """The 0-reinforcement case is inert: passing n_crews_1h=n_crews_3h=0
    explicitly yields the SAME state sequence as omitting them entirely, and
    every crew is a plain in-zone crew (no tier machinery perturbs the legacy
    dispatch/audit/served behaviour)."""
    def run(explicit):
        d = _controlled_tile_dir(tmp_path, "boulder_co")
        kw = dict(n_crews_1h=0, n_crews_3h=0) if explicit else {}
        s = SimSession("boulder_co", 8, 2, seed="pin", cfg_overrides=dict(FAST),
                       tile_dir=d, roads_module=None, **kw)
        ign = _upwind_ignition("boulder_co", 8_000.0)
        s.ignite(ign.lat, ign.lon, intensity="high")
        s.set_speed(2.0)
        return [_state_hash(s.tick()) for _ in range(6)]
    assert run(True) == run(False)
    d = _controlled_tile_dir(tmp_path, "boulder_co")
    s = SimSession("boulder_co", 8, 2, seed="pin", cfg_overrides=dict(FAST),
                   tile_dir=d, roads_module=None)
    for c in s.state()["crews"]:
        assert c["region"] == "in_zone" and c["inbound"] is False
        assert c["available_at_s"] is None and c["arrival_eta_s"] is None


def test_reset_and_reconfigure_preserve_tier_counts(tmp_path):
    """reset() re-arms the SAME tier layout (reinforcements back to INBOUND
    standby) and configure() with the tier args (the reroll_clients path)
    preserves them."""
    s = _reinf_session(tmp_path, n_crews=2, n_crews_1h=1, n_crews_3h=1,
                       seed="reset", d1=15.0, d3=30.0)
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="moderate")
    s.set_speed(2.0)
    for _ in range(4):
        s.tick()
    s.reset()
    st = s.state()
    assert sorted(c["region"] for c in st["crews"]) == \
        ["in_zone", "in_zone", "reg_1h", "reg_3h"]
    for c in st["crews"]:
        if c["region"] != "in_zone":
            assert c["inbound"] is True and c["available_at_s"] is None
    # the reroll path: configure() carrying the tier counts keeps them
    s.configure("boulder_co", 5, 2, "reroll#1", dict(FAST),
                n_crews_1h=1, n_crews_3h=1)
    st2 = s.state()
    assert len(st2["crews"]) == 4
    assert sorted(c["region"] for c in st2["crews"]) == \
        ["in_zone", "in_zone", "reg_1h", "reg_3h"]


def test_reinforcement_count_validation(tmp_path):
    """n_crews_1h / n_crews_3h validate to 0..12 (negative/oversized fail loud);
    the in-zone n_crews still requires >= 1."""
    d = _controlled_tile_dir(tmp_path, "boulder_co")
    for kw, frag in [
        (dict(n_crews_1h=-1), "n_crews_1h"),
        (dict(n_crews_3h=13), "n_crews_3h"),
    ]:
        with pytest.raises(ValueError) as ei:
            SimSession("boulder_co", 4, 1, seed="v", cfg_overrides=dict(FAST),
                       tile_dir=d, roads_module=None, **kw)
        assert frag in str(ei.value)
    # 0 reinforcements is legal (the default)
    SimSession("boulder_co", 4, 1, seed="v", cfg_overrides=dict(FAST),
               tile_dir=d, roads_module=None, n_crews_1h=0, n_crews_3h=0)


# ---------------------------------------------------------------------------
# CONTINUOUS CREW SAFETY (contracts SAFETY SEMANTICS 10; docs/review/
# crews_caught_report.md) — the founder "crews G1/G2 caught in fire" fix. The
# per-tick SAFETY AUDIT governed only gel-job DEPARTURES, so an idle crew (or
# one resting at a safe zone the fire overran) burned while scoreboard.
# violations stayed 0 — the crew's LIVE position was never checked. The fix
# EXTENDS (never weakens) the P10/corridor/egress-buffer law to every crew's
# live cell: a continuous per-tick sweep relocates threatened crews to a
# currently-safe zone (10a), safe zones are re-validated against the advancing
# front (10b), and the audit counts any crew standing in the burn so the
# scoreboard can never silently read 0 (10c). Same eroded-P10 transform, same
# egress_buffer_min threshold — extension, not weakening.
# ---------------------------------------------------------------------------

def _crew_fire_exposure(session, cs):
    """(min nominal arrival, min eroded-P10 arrival) at a crew's LIVE cell
    across all fires — computed with the RAW engine samplers INDEPENDENTLY of
    the audit under test (the exact BURNED/IMMINENT definitions the founder
    report uses: nominal member = sim ground truth; eroded P10 = the pessimistic
    corridor front)."""
    nom = p10 = math.inf
    for f in session._fires.values():
        if f.ens is None:
            continue
        v = geo.raster_sample(f.ens.members[0], cs.pos)
        if v is not None and v < nom:
            nom = v
        er = session._eroded_p10(f)
        if er is not None:
            ev = geo.raster_sample(er, cs.pos)
            if ev is not None and ev < p10:
                p10 = ev
    return nom, p10


def _idle_crews_in_burn(session, now):
    """crew_ids of AVAILABLE, itinerary-empty (IDLE) crews whose live cell is
    BURNED (nominal <= now) or IMMINENT (eroded-P10 < now + egress buffer) — the
    exact founder-bug signature (idle crews standing in the fire). INBOUND
    reinforcements are off-tile and excluded."""
    buf_s = session._cfg.egress_buffer_min * 60.0
    out = []
    for cid in session._crew_sims:
        cs = session._crew_sims[cid]
        if cs.status.value != "available" or cs.itinerary:
            continue
        nom, p10 = _crew_fire_exposure(session, cs)
        if (not math.isinf(nom) and nom <= now) or \
           (not math.isinf(p10) and p10 < now + buf_s):
            out.append(cid)
    return out


def _crewsafe_session(tmp_path, key="boulder_co", seed="cs", n_clients=10,
                      n_crews=2, hamlet_half=3):
    """A controlled-tile session sized so crews finish work in the community
    and an extreme fire then threatens them — the fast, deterministic proxy for
    the founder Paradise scenario (roads pinned off; synthetic tile)."""
    d = _controlled_tile_dir(tmp_path, key, hamlet_half=hamlet_half)
    return SimSession(key, n_clients, n_crews, seed=seed,
                      cfg_overrides=dict(FAST), tile_dir=d, roads_module=None)


def test_crew_safety_normal_case_relocates_and_escapes(tmp_path):
    """SAFETY SEMANTICS 10a NORMAL CASE (the fix, MEASURED end-to-end): an idle
    crew whose resting cell / anchor zone becomes threatened RELOCATES to a
    currently-safe zone and escapes BEFORE overrun. Controlled boulder extreme
    fire 5 km upwind, 2 crews, 40 ticks (2x): crews reposition (scoreboard
    relocations > 0; crews[i].relocating flips with a reason, status egressing)
    and — because the sweep leaves one tick ahead of the audit line — NO crew is
    ever left idle in a burned/imminent cell AND violations stays 0
    LEGITIMATELY (crews escaped, not hidden — the founder's 'crews caught,
    violations 0' bug is gone). Derivation run 2026-07-10 (seed cs1): 3
    relocation events, violations 0 every tick, 10 homes gelled."""
    s = _crewsafe_session(tmp_path, seed="cs1", n_clients=10, n_crews=2)
    ign = _upwind_ignition("boulder_co", 5_000.0)
    s.ignite(ign.lat, ign.lon, intensity="extreme")
    s.set_speed(2.0)
    saw_relocating = False
    reasons = set()
    st = None
    for _ in range(40):
        st = s.tick()
        now = s._now_s
        # (1) THE FOUNDER BUG IS GONE: no idle crew ever stands in the burn
        assert not _idle_crews_in_burn(s, now), (
            f"tick {st['tick']}: an idle crew is standing in the fire — the "
            f"continuous crew-safety monitor failed to relocate it")
        # (2) violations stay 0 LEGITIMATELY — crews escaped, not hidden
        assert st["scoreboard"]["violations"] == 0, st["crew_overruns"]
        for c in st["crews"]:
            if c["relocating"]:
                saw_relocating = True
                assert c["relocate_reason"] in ("zone_threatened",
                                                "zone_overrun")
                assert c["status"] == "egressing"        # actively fleeing
                reasons.add(c["relocate_reason"])
    # the sweep ACTUALLY engaged (non-vacuous): crews repositioned to safety
    assert st["scoreboard"]["relocations"] >= 1, "no crew ever relocated — re-pin"
    assert saw_relocating, "relocating telemetry never surfaced"
    assert st["scoreboard"]["served"] >= 1, "scenario serviced no one — vacuous"
    # end-state: no crew is left idle in the burn (they reached safe zones)
    assert not _idle_crews_in_burn(s, s._now_s)


def test_crew_safety_paradise_founder_repro_when_tile_present():
    """SAFETY SEMANTICS 10 FOUNDER HEADLINE — the exact reproduction from
    docs/review/crews_caught_report.md (Paradise EXTENDED, 64 clients, 5 crews,
    extreme fire ignited NE at 39.83,-121.52), 180 ticks on the REAL tile (loud
    skip when uncached).

    BEFORE the fix: all 5 crews idled 12-15 km deep in the community and the
    front overran them while scoreboard.violations stayed 0 (284 exposed ticks,
    max violations 0 — the audit only checked gel-job departures). AFTER: every
    threatened crew RELOCATES toward the durable SE zone (relocations > 0), NO
    crew is EVER left idle in a burned/imminent cell, the scoreboard can never
    read 0 while a crew is idle in the burn (anti-hidden invariant), and
    violations END at 0 LEGITIMATELY — the crews escaped, it is not the old
    blind 0. Derivation run 2026-07-10 (seed caught-1): 73 relocation events, 0
    idle-in-burn ticks, final violations 0, 40 homes gelled."""
    ext = DEFAULT_TILE_DIR / "paradise_ca_ext.wgt"
    if not ext.exists():
        pytest.skip("LOUD SKIP: no data/tiles/paradise_ca_ext.wgt — the founder "
                    "Paradise repro is not exercised this run "
                    "(py -3 tools/fetch_sim_tiles.py --extended)")
    s = SimSession("paradise_ca", 64, 5, seed="caught-1", fire_zone="extended")
    s.ignite(39.83, -121.52, intensity="extreme")
    idle_in_burn_ticks = 0
    hidden_ticks = 0
    st = None
    for _ in range(180):
        st = s.tick()
        idle_burn = _idle_crews_in_burn(s, s._now_s)
        if idle_burn:
            idle_in_burn_ticks += 1
            # ANTI-HIDDEN invariant: an idle crew in the burn can NEVER coexist
            # with a 0 scoreboard (the exact founder bug)
            if st["scoreboard"]["violations"] == 0:
                hidden_ticks += 1
    # (1) the founder bug is GONE — no crew is EVER left idle in the fire
    assert idle_in_burn_ticks == 0, (
        f"crews were left idle in the burn on {idle_in_burn_ticks} ticks — the "
        f"continuous monitor did not relocate them (founder bug reshipped)")
    # (2) even if a crew were caught, it could never be hidden from the count
    assert hidden_ticks == 0, "a crew stood idle in the fire while violations=0"
    # (3) the fix ENGAGED — crews actively repositioned (the old run never moved)
    assert st["scoreboard"]["relocations"] > 0, "no crew ever relocated"
    # (4) violations END at 0 LEGITIMATELY — the crews escaped to the safe zone
    assert st["scoreboard"]["violations"] == 0
    assert not _idle_crews_in_burn(s, s._now_s)
    assert st["scoreboard"]["served"] >= 1


def test_crew_safety_audit_backstop_counts_overrun(tmp_path):
    """SAFETY SEMANTICS 10c NO-RESHIP INVARIANT (the backstop, proven
    INDEPENDENT of the 10a sweep): with continuous relocation DISABLED
    (monkeypatched off) crews are overrun — and _run_audit STILL reports it
    (scoreboard.violations > 0, crew_overrun rows), so the scoreboard can NEVER
    silently read 0 while a crew is in the burn. This is what makes the founder
    bug un-reshippable: even if relocation regressed, the audit tells the truth.
    While the sweep is off, EVERY tick an idle crew stands in the burn the
    scoreboard is > 0."""
    s = _crewsafe_session(tmp_path, seed="cs-backstop", n_clients=10, n_crews=2)
    s._crew_safety_sweep = lambda *a, **k: None      # disable relocation (10a)
    ign = _upwind_ignition("boulder_co", 5_000.0)
    s.ignite(ign.lat, ign.lon, intensity="extreme")
    s.set_speed(2.0)
    saw_violation = False
    saw_overrun_row = False
    st = None
    for _ in range(40):
        st = s.tick()
        v = st["scoreboard"]["violations"]
        idle_burn = _idle_crews_in_burn(s, s._now_s)
        if idle_burn:
            # THE INVARIANT: an idle crew in the burn FORCES violations > 0
            assert v > 0, (
                f"tick {st['tick']}: idle crews {idle_burn} stand in the burn "
                f"but violations == 0 — the audit went blind (founder bug)")
        if v > 0:
            saw_violation = True
        if st["crew_overruns"]:
            saw_overrun_row = True
            for o in st["crew_overruns"]:
                assert o["kind"] == "crew_overrun"
                assert o["reason"] in ("burned", "imminent")
                assert v >= len(st["crew_overruns"])   # each folded into count
    assert st["scoreboard"]["relocations"] == 0, "sweep was not disabled"
    assert saw_violation, "no overrun ever occurred — scenario vacuous (re-pin)"
    assert saw_overrun_row, "crew_overrun rows never surfaced"


def test_dynamic_safe_zone_validity_excludes_overrun_zone(tmp_path):
    """SAFETY SEMANTICS 10b DYNAMIC SAFE ZONES: a zone the fire has overrun is
    no longer 'currently safe' and is FILTERED OUT of the candidate set handed
    to dispatch / adopt / egress selection, so it can never be picked as a
    staging / egress / anchor target. Igniting a fire ON a safe zone flips
    _zone_currently_safe() to False for it while far zones stay True."""
    s = _crewsafe_session(tmp_path, seed="dz", n_clients=8, n_crews=2)
    zones = s._loc.safe_zones
    z_hit = zones[0]
    # all zones start safe (no fire ignited)
    for z in zones:
        assert s._zone_currently_safe(z, s._now_s)
    # ignite ON the first zone; run until the front overruns it
    s.ignite(z_hit.loc.lat, z_hit.loc.lon, intensity="extreme")
    s.set_speed(2.0)
    for _ in range(12):
        s.tick()
    now = s._now_s
    assert not s._zone_currently_safe(z_hit, now), (
        "a zone the fire ignited on is still reported currently-safe")
    safe, fail_closed = s._currently_safe_zones(now)
    assert z_hit not in safe, "overrun zone still in the currently-safe set"
    assert not fail_closed and safe, "the far zones must remain valid"
    # every crew's live egress target is a currently-safe zone (never the
    # overrun one) — a plan never routes a crew into the fire
    for cid in s._crew_sims:
        cs = s._crew_sims[cid]
        if cs.egress_zone_id:
            assert cs.egress_zone_id != z_hit.zone_id, (
                "a crew is egressing to the overrun zone")


def test_no_safe_zone_fail_closed_alarm_and_safest_pick(tmp_path):
    """SAFETY SEMANTICS 10b FAIL-CLOSED: when the front is within the egress
    buffer of EVERY zone, _currently_safe_zones fails closed to the SINGLE
    latest-arrival (safest available) zone and the sweep raises a LOUD operator
    alarm ONCE — a crew is never routed to a nearer burned zone (mirrors
    safety.egress_choice's no_safe_egress)."""
    s = _crewsafe_session(tmp_path, seed="nsz", n_clients=8, n_crews=2)
    for z in s._loc.safe_zones:                   # a fire on every zone
        s.ignite(z.loc.lat, z.loc.lon, intensity="extreme")
    s.set_speed(2.0)
    st = None
    for _ in range(10):
        st = s.tick()
        if s._no_safe_zone:
            break
    assert s._no_safe_zone, "all-zones-on-fire never reached the fail-closed state"
    safe, fail_closed = s._currently_safe_zones(s._now_s)
    assert fail_closed and len(safe) == 1, "fail-closed must pick exactly ONE zone"
    # it is the LATEST-arrival (safest available) zone
    latest = max(s._loc.safe_zones,
                 key=lambda z: (s._crew_exposure(z.loc)[1], z.zone_id))
    assert safe[0].zone_id == latest.zone_id
    # a LOUD operator alarm fired, exactly once (latched on transition)
    alarms = [n for n in st["notes"] if "no_safe_zone" in n]
    assert len(alarms) == 1, f"expected one no_safe_zone alarm, got {alarms}"


def test_inbound_reinforcement_not_relocated_by_sweep(tmp_path):
    """SAFETY SEMANTICS 10a: the crew-safety sweep SKIPS INBOUND reinforcements
    (off-tile, still mobilizing) — an inbound crew parked on the tile edge is
    never falsely relocated even with an extreme fire on the tile, and stays
    INBOUND with relocating False until it mobilizes."""
    d = _controlled_tile_dir(tmp_path, "boulder_co", hamlet_half=3)
    s = SimSession("boulder_co", 12, 1, seed="ib",
                   cfg_overrides={**FAST, "crew_reinforce_1h_min": 100_000.0,
                                  "crew_reinforce_3h_min": 100_000.0},
                   tile_dir=d, roads_module=None, n_crews_1h=1, n_crews_3h=1)
    reinf = {"sim-g2", "sim-g3"}
    ign = _upwind_ignition("boulder_co", 4_000.0)
    s.ignite(ign.lat, ign.lon, intensity="extreme")
    s.set_speed(2.0)
    for _ in range(20):
        st = s.tick()
        for c in st["crews"]:
            if c["crew_id"] in reinf:
                assert c["inbound"] is True and c["status"] == "inbound"
                assert c["relocating"] is False, (
                    "an INBOUND reinforcement was relocated by the sweep")
                assert c["relocate_reason"] is None


def test_crew_safety_telemetry_and_scrub_byte_identical(tmp_path):
    """SAFETY SEMANTICS 10 TELEMETRY: crews[i] gains relocating/relocate_reason,
    scoreboard gains relocations + crew_overruns, state gains a crew_overruns
    list — all JSON-safe and all SNAPSHOTTED, so LAW 1 TIME-SCRUB replays a
    relocation byte-identically (state_at(k) == the live tick-k frame)."""
    s = _crewsafe_session(tmp_path, seed="cs-scrub", n_clients=10, n_crews=2)
    ign = _upwind_ignition("boulder_co", 5_000.0)
    s.ignite(ign.lat, ign.lon, intensity="extreme")
    s.set_speed(2.0)
    live = {}
    saw_relocating = False
    for _ in range(30):
        st = s.tick()
        live[st["tick"]] = json.dumps(st, sort_keys=True, allow_nan=False)
        assert "relocations" in st["scoreboard"]
        assert "crew_overruns" in st["scoreboard"]
        assert isinstance(st["crew_overruns"], list)
        for c in st["crews"]:
            assert "relocating" in c and "relocate_reason" in c
            if c["relocating"]:
                saw_relocating = True
    assert saw_relocating, "no relocation happened — telemetry untested (re-pin)"
    # scrub: every recorded tick replays byte-identically, relocations included
    for k, frame in live.items():
        assert json.dumps(s.state_at(k), sort_keys=True,
                          allow_nan=False) == frame, (
            f"state_at({k}) diverged from the live frame (relocation not "
            f"faithfully snapshotted)")


def test_crew_overrun_audit_zero_exposure_byte_identical_superset(tmp_path):
    """SAFETY SEMANTICS 10c is a STRICT SUPERSET of the old audit: with no crew
    ever exposed the crew-overrun backstop adds NOTHING — crew_overruns stays
    empty, relocations stays 0, and scoreboard.violations equals the
    serviced-stop audit alone (byte-identical to the pre-10c audit). A high fire
    8 km upwind never threatens a crew, so the extension is provably inert."""
    s = _high_session(tmp_path, seed="cs-zero", n_clients=8, n_crews=2)
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    s.set_speed(2.0)
    st = None
    for _ in range(12):
        st = s.tick()
        assert st["crew_overruns"] == []
        assert st["scoreboard"]["crew_overruns"] == 0
        assert st["scoreboard"]["relocations"] == 0
        # violations == the serviced-stop audit alone (nothing added by 10c)
        serviced_viol = sum(1 for a in st["audit"] if a["violation"])
        assert st["scoreboard"]["violations"] == serviced_viol == 0
    assert st["scoreboard"]["served"] >= 1, "vacuous — nothing serviced"


def test_crew_safety_determinism_same_script(tmp_path):
    """Deterministic: the SAME (seed, command script) that triggers relocations
    replays to a byte-identical state() sequence, relocation telemetry and all
    (contracts DETERMINISM law extended to SEMANTICS 10)."""
    def run():
        s = _crewsafe_session(tmp_path, seed="cs-det", n_clients=10, n_crews=2)
        ign = _upwind_ignition("boulder_co", 5_000.0)
        s.ignite(ign.lat, ign.lon, intensity="extreme")
        s.set_speed(2.0)
        frames = []
        for _ in range(30):
            st = s.tick()
            frames.append(json.dumps(st, sort_keys=True, allow_nan=False))
        return frames, st["scoreboard"]["relocations"]
    a, ra = run()
    b, rb = run()
    assert a == b, "same (seed, command script) must replay byte-identically"
    assert ra >= 1 and ra == rb, "relocations must be deterministic and non-zero"


# ---------------------------------------------------------------------------
# SEM-10d ROUTED RELOCATION + AREA ESCAPE (founder finding 2026-07-11): on the
# extended-zone Camp-Fire-scale Paradise run, crews were burned WHILE EGRESSING
# (relocation picked the nearest catalog zone without checking the crew could
# REACH it ahead of the front — the road route hooked through the burn) and,
# once the fire consumed every catalog zone, crews sat at the fail-closed zone
# until overrun. The fix: least-bad ROUTED target choice (lawful-route zone,
# else max-worst-margin zone/escape-ray) + outward AREA ESCAPE. This pins the
# real failing scenario end-to-end: per-tick violations must stay 0 for the
# WHOLE run (scoreboard.violations is instantaneous — final-tick reads hide
# transient overruns, which is exactly how the original bug slipped past).
# ---------------------------------------------------------------------------

def test_sem10d_extended_campfire_scale_no_overrun_ever():
    from wgresponse import geo as _geo
    from wgresponse import simui
    key = "paradise_ca"
    loc = simui.LOCATIONS[key]
    s = simui.SimSession(
        key, 20, 2, seed="alerted|paradise_ca",
        cfg_overrides={"spotting_enabled": True, "grass_ros_floor_frac": 0.10,
                       "deploy_min": 15.0},
        fire_zone="extended")
    ign = _geo.destination(loc.center, loc.hazard_wind_dir_deg, 13_500.0)
    s.ignite(ign.lat, ign.lon, intensity="high",
             wind_dir_deg=loc.hazard_wind_dir_deg)
    s.set_speed(2.0)                    # 30 min per tick -> 60 h in 120 ticks
    escape_seen = False
    for t in range(120):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, (
            f"tick {t}: crew overrun/violation — SEM-10d regression")
        assert not st.get("crew_overruns"), (
            f"tick {t}: live crew-overrun rows — SEM-10d regression")
        if any(n for n in st.get("notes", []) if "area escape" in n):
            escape_seen = True
    # the scenario must actually exercise the escape law (fire consumes the
    # community; crews leave the area) — otherwise this test is vacuous
    assert escape_seen or st["scoreboard"]["relocations"] >= 1, (
        "scenario no longer exercises relocation/escape — rebuild the pin")


# ---------------------------------------------------------------------------
# FOUNDER ROUND 5 — COATING HOLD (2026-07-12): fire > hold_h away -> defer
# coating (opaque product, owners accept it only under live threat), crews on
# alert + prepositioned — gated on the every-cycle feasibility probe (deferred
# start + weather buffer must still cover every coat-now home); gate failure
# releases the hold, latched.
# ---------------------------------------------------------------------------

def _hold_session(tmp_path, n_clients, n_crews, hold_h=24.0, buffer_h=6.0,
                  seed="hold"):
    d = _controlled_tile_dir(tmp_path, "boulder_co")
    cfg = dict(FAST)
    cfg["horizon_h"] = 60.0
    cfg["dispatch_hold_h"] = hold_h
    cfg["dispatch_hold_weather_buffer_h"] = buffer_h
    return SimSession("boulder_co", n_clients, n_crews, seed=seed,
                      cfg_overrides=cfg, tile_dir=d, roads_module=None)


def test_hold_defers_far_fire_then_coats(tmp_path):
    """Fire ~far upwind: p50 at the community > 24h -> first ticks coat
    NOTHING (reason 'held', hold active in state), crews may preposition; as
    the fire closes inside 24h the hold empties naturally and homes get
    coated. Per-tick violations 0 throughout."""
    s = _hold_session(tmp_path, 6, 2)
    ign = _upwind_ignition("boulder_co", 10_000.0)
    s.ignite(ign.lat, ign.lon, intensity="low")
    st = s.tick()
    if st["hold"]["held"] == 0:
        # slow-fuel tile can push p50 beyond the notice horizon (no jobs yet)
        # or inside 24h (no hold) — either way the law is vacuous here; the
        # scenario must actually exercise a hold to pin it.
        import pytest
        pytest.skip("scenario produced no >24h jobs on this tile")
    assert st["scoreboard"]["served"] == 0
    assert st["hold"]["enabled"] and not st["hold"]["released"]
    assert any(u["reason"] == "held" for u in st["unserved"])
    s.set_speed(8.0)                        # 2h per tick — close the distance
    served_after = 0
    for _ in range(20):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        served_after = st["scoreboard"]["served"]
        if served_after:
            break
    assert served_after >= 1, "hold must hand off to coating as fire closes"


def test_hold_releases_when_coverage_would_be_lost(tmp_path):
    """Scarce capacity: 1 crew, many homes, fire 26-30h out. Deferring to the
    last 24h (plus a HUGE weather buffer) cannot cover everyone -> the gate
    must RELEASE the hold immediately (latched) and coat now."""
    s = _hold_session(tmp_path, 12, 1, buffer_h=40.0, seed="scarce")
    ign = _upwind_ignition("boulder_co", 9_000.0)
    s.ignite(ign.lat, ign.lon, intensity="moderate")
    released = False
    for _ in range(6):
        st = s.tick()
        if st["hold"]["released"]:
            released = True
            break
        if st["hold"]["enabled"] and st["hold"]["held"] == 0 \
                and st["scoreboard"]["served"] == 0 and not st["unserved"]:
            continue
    if not released:
        import pytest
        if st["hold"]["held"] == 0 and not st["hold"]["released"]:
            pytest.skip("scenario produced no >24h jobs on this tile")
    assert released, "impossible deferred coverage must release the hold"
    assert any("COATING HOLD RELEASED" in n for n in st["notes"])


def test_hold_off_is_byte_identical(tmp_path):
    """dispatch_hold_h = 0 (the engine default) must leave the tick stream
    byte-identical to a session that never knew the knob."""
    def run(extra):
        d = _controlled_tile_dir(tmp_path, "boulder_co")
        cfg = dict(FAST)
        cfg.update(extra)
        s = SimSession("boulder_co", 4, 1, seed="hi", cfg_overrides=cfg,
                       tile_dir=d, roads_module=None)
        ign = _upwind_ignition("boulder_co", 8_000.0)
        s.ignite(ign.lat, ign.lon, intensity="high")
        out = []
        for _ in range(3):
            st = s.tick()
            st = dict(st)
            st.pop("hold", None)          # the only intended schema delta
            st.pop("cmd_log", None)       # records cfg_overrides verbatim (repro line)
            out.append(_state_hash(st))
        return out
    assert run({}) == run({"dispatch_hold_h": 0.0,
                           "dispatch_hold_weather_buffer_h": 6.0})


# ---------------------------------------------------------------------------
# CREW SHIFTS (FOUNDER ROUND 6b, 2026-07-12): fatigue law for multi-day
# events — max continuous on-duty hours, then rest (OFF_DUTY, never
# dispatchable, still SEM-10 protected), A/B first-shift stagger.
# ---------------------------------------------------------------------------

def test_crew_shifts_rest_stagger_and_return(tmp_path):
    d = _controlled_tile_dir(tmp_path, "boulder_co")
    cfg = dict(FAST)
    cfg["crew_shift_max_on_h"] = 1.0      # tiny shift so the test is fast
    cfg["crew_shift_rest_h"] = 1.0
    s = SimSession("boulder_co", 6, 2, seed="shift", cfg_overrides=cfg,
                   tile_dir=d, roads_module=None)
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    seen_rest = set()
    both_resting_at_once = False
    for _ in range(24):                    # 6 h at 15-min ticks
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        resting = {c["crew_id"] for c in st["crews"]
                   if c.get("status") == "off_duty"}
        seen_rest |= resting
        if len(resting) == 2:
            both_resting_at_once = True
        # a resting crew must never appear in a live plan
        for cp in (st.get("plan") or {}).get("plans", {}).values():
            for stop in cp.get("stops", []):
                assert cp.get("crew_id") not in resting
    assert seen_rest == {"sim-g1", "sim-g2"}, (
        f"both crews must cycle through rest, saw {seen_rest}")
    # A/B stagger (odd crew gets a half first shift): with equal budgets both
    # would bed down the same tick — the stagger must prevent a fleet-wide nap
    assert not both_resting_at_once, (
        "A/B stagger failed — entire fleet resting simultaneously")
    # a rested crew returns and the fleet keeps serving under the fatigue law
    assert st["scoreboard"]["served"] >= 1
    assert st["scoreboard"]["crews_resting"] in (0, 1)


def test_crew_shifts_off_is_inert(tmp_path):
    """Default crew_shift_max_on_h=0: nobody ever rests."""
    s = _high_session(tmp_path, seed="noshift")
    ign = _upwind_ignition("boulder_co", 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    for _ in range(6):
        st = s.tick()
        assert st["scoreboard"]["crews_resting"] == 0
        assert all(c.get("status") != "off_duty" for c in st["crews"])
