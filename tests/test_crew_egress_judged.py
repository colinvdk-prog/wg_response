"""SAFETY SEMANTICS 20 — NO CREW DRIVES A LEG THE LAW HAS NOT JUDGED
(2026-10-09; contracts corridor_cell_exact / crew_egress_judged;
docs/SIMULATOR_DESIGN.md "No crew drives an unjudged leg";
scratch_review/crew_egress_gap_sweep.py).

The city-weather review (docs/review/city_weather_2026-10-09.md "Safety: one
gap") found SEM-10c crew-overrun rows — crews "imminent" while egressing —
that the crew-safety law did not prevent. Two root causes, both in the crew-
safety code, neither in the weather:

  (a) the corridor law sampled a leg every 300 m (the SEM-10 judgement every
      ~275 m), so a leg that clipped a cell between two samples was never
      judged there — the synthetic triage scatter's chord passed 467 m from
      the ignition cell, inside its 550 m eroded band, and read 9 h clear;
  (b) a crew that left its route mid-way (a safety withdrawal) egressed to
      the route's adopted zone — the LAST stop's, whose road from THIS stop no
      law had judged (all Malibu one-click overruns); an egress in flight
      (or leaving the last stop) was checked only by its zone's own cell at
      zero lead, so a zone the front reached inside the buffer before the
      next audit was kept (Aspen, Glenwood); and a VOLUNTARY decamp rode
      transit the law had judged exposed (Eagle).

These tests pin the two knobs: the cell geometry, the corridor oracle, the
egress judgement at the moment a leg starts and in flight, the voluntary
decamp, byte-identity with both knobs off (goldens computed on commit
8973cd9, before the rule, on worlds where it bites), and every overrun the
sweep reproduced before the rule — now 0."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import random

import pytest

from wgresponse import geo, safety, server, simui
from wgresponse.contracts import (
    GridSpec,
    LatLon,
    Raster,
    ResponseConfig,
    SafeZone,
    UNREACHED,
)
from wgresponse.simui import SimSession, _SimFire

INF = math.inf


# ---------------------------------------------------------------------------
# geo.path_cell_runs — every cell a polyline passes through
# ---------------------------------------------------------------------------

SPEC90 = GridSpec(north_lat=40.1, west_lon=-105.4, cell_m=90.0, nrows=200,
                  ncols=200)


def _runs(spec, path):
    exits, idxs = geo.path_cell_runs(spec, path)
    return list(zip(exits, idxs))


def test_path_cell_runs_covers_every_point_the_path_occupies():
    """Dense walk (every ~2 m) of random polylines on a 90 m grid: every
    point a vehicle occupies lies in the cell of the run that covers its
    along-path distance (to within a millimetre of a run boundary)."""
    rng = random.Random(20261009)
    for _trial in range(120):
        lat = 40.1 - rng.uniform(0.01, 0.15)
        lon = -105.4 + rng.uniform(0.01, 0.2)
        path = []
        for _ in range(rng.randint(2, 6)):
            path.append(LatLon(lat, lon))
            lat += rng.uniform(-0.01, 0.01)
            lon += rng.uniform(-0.01, 0.01)
        runs = _runs(SPEC90, path)
        assert runs and all(b[0] >= a[0] for a, b in zip(runs, runs[1:]))
        cum = 0.0
        for j in range(1, len(path)):
            p0, p1 = path[j - 1], path[j]
            d = geo.haversine_m(p0, p1)
            n = max(1, int(d / 2.0))
            for k in range(n + 1):
                f = k / n
                q = LatLon(p0.lat + (p1.lat - p0.lat) * f,
                           p0.lon + (p1.lon - p0.lon) * f)
                rc = geo.latlon_to_rc(SPEC90, q)
                if rc is None:
                    continue
                idx = rc[0] * SPEC90.ncols + rc[1]
                dist = cum + d * f
                prev = 0.0
                hit = False
                for e, i in runs:
                    if prev - 1e-3 <= dist <= e + 1e-3 and i == idx:
                        hit = True
                        break
                    prev = e
                assert hit, (dist, rc)
            cum += d


def _grid_pt(spec, y, x):
    """The point at fractional grid coordinates (y rows from the north edge,
    x columns from the west edge) — the inverse of geo.latlon_to_rc."""
    lat = spec.north_lat - y * spec.cell_m / geo.M_PER_DEG_LAT
    lon = spec.west_lon + x * spec.cell_m / geo.m_per_deg_lon(lat)
    return LatLon(lat, lon)


def test_path_cell_runs_a_corner_clip_is_a_run_of_its_own():
    """From the centre of (5,5) to near the centre of (6,6), passing just
    beside the shared corner: the 2 % of the leg inside (5,6) is its own run,
    left where the leg crosses into row 6."""
    a = _grid_pt(SPEC90, 5.5, 5.5)
    b = _grid_pt(SPEC90, 6.5, 6.52)
    runs = _runs(SPEC90, [a, b])
    cells = [divmod(i, SPEC90.ncols) for _e, i in runs]
    assert cells == [(5, 5), (5, 6), (6, 6)]
    d = geo.haversine_m(a, b)
    assert runs[1][0] == pytest.approx(0.5 * d, rel=1e-3)     # leaves at y=6
    # memoised: the same geometry is never walked twice
    assert geo.path_cell_runs(SPEC90, [a, b]) is geo.path_cell_runs(
        SPEC90, (a, b))


# ---------------------------------------------------------------------------
# safety.LegOracle — the cell-exact corridor (SEMANTICS 20a)
# ---------------------------------------------------------------------------

T0 = 1_750_000_000.0
CFG = ResponseConfig()
CFG_EXACT = dataclasses.replace(CFG, corridor_cell_exact=True)


def _late_field(spec, hot=(), late_h=20.0):
    """Eroded arrivals: T0 + late_h everywhere except the `hot` cells
    {(r, c): unix s}."""
    r = Raster(spec, fill=T0 + late_h * 3600.0)
    for (rr, cc), v in hot:
        r.set(rr, cc, v)
    return r


def test_leg_oracle_cell_exact_judges_the_cell_its_samples_step_over():
    """The triage geometry in miniature: a chord clips the corner of a cell
    burning at T0 between two 300 m samples. Legacy: 20 h clear. Cell-exact:
    the clipped cell binds at the moment the leg leaves it, and the verdict
    names the cell."""
    spec = GridSpec(north_lat=40.0, west_lon=-105.0, cell_m=500.0, nrows=30,
                    ncols=30)
    a = _grid_pt(spec, 10.5, 10.5)
    b = _grid_pt(spec, 19.5, 15.02)        # passes beside the (15,13) corner
    exits, idxs = geo.path_cell_runs(spec, [a, b])
    legacy0 = safety.LegOracle(a, b, 7.52, _late_field(spec), CFG)
    sampled = {geo.latlon_to_rc(spec, p) for _i, p, _v, _dt in legacy0.samples}
    clipped = [divmod(i, spec.ncols) for i in idxs
               if divmod(i, spec.ncols) not in sampled]
    assert clipped, "geometry must step over a cell the leg passes through"
    hot_rc = clipped[0]
    field = _late_field(spec, hot=[(hot_rc, T0)])
    legacy = safety.LegOracle(a, b, 7.52, field, CFG)
    exact = safety.LegOracle(a, b, 7.52, field, CFG_EXACT)
    assert legacy.latest_safe_depart_s > T0 + 18.0 * 3600.0   # ~19 h clear
    k = [divmod(i, spec.ncols) for i in idxs].index(hot_rc)
    want = T0 - CFG.route_margin_min * 60.0 - exits[k] / 7.52
    assert exact.latest_safe_depart_s == pytest.approx(want, abs=1e-6)
    v = exact.verdict(T0 - 3600.0)
    assert not v.safe and f"cell ({hot_rc[0]},{hot_rc[1]})" in v.reason
    assert legacy.verdict(T0 - 3600.0).safe


def test_leg_oracle_cell_exact_keeps_every_legacy_sample_and_is_never_later():
    """Random fields and legs (chords and road paths, fail-closed and per-
    fire margins included): the cell-exact samples contain the legacy ones
    in order, T* is never later, and the verdict agrees with T*."""
    rng = random.Random(7)
    spec = GridSpec(north_lat=39.5, west_lon=-121.5, cell_m=90.0, nrows=60,
                    ncols=60)
    for trial in range(40):
        field = Raster(spec, [T0 + rng.uniform(0.0, 12.0) * 3600.0
                              if rng.random() > 0.1 else INF
                              for _ in range(spec.nrows * spec.ncols)])
        a = _grid_pt(spec, rng.uniform(1, 59), rng.uniform(1, 59))
        b = _grid_pt(spec, rng.uniform(1, 59), rng.uniform(1, 75))
        path = None
        if trial % 2:
            path = [a, _grid_pt(spec, rng.uniform(1, 59), rng.uniform(1, 59)),
                    b]
        for base in (CFG, dataclasses.replace(CFG, corridor_fail_closed=True)):
            kw = {}
            if trial % 3 == 0:
                kw = {"offraster_ros_mps": 1.0, "offraster_margin_min": 60.0}
            leg = safety.LegOracle(a, b, 12.0, field, base, path=path, **kw)
            ex = safety.LegOracle(a, b, 12.0, field, dataclasses.replace(
                base, corridor_cell_exact=True), path=path, **kw)
            it = iter(ex.samples)
            assert all(s in it for s in leg.samples)          # in order
            assert ex.latest_safe_depart_s <= leg.latest_safe_depart_s
            assert len(ex.samples) >= len(leg.samples)
            for dep in (ex.latest_safe_depart_s - 1.0,
                        ex.latest_safe_depart_s + 1.0):
                if math.isfinite(dep):
                    assert ex.verdict(dep).safe == (
                        dep <= ex.latest_safe_depart_s)


def test_leg_oracle_knob_off_is_the_legacy_oracle():
    spec = GridSpec(north_lat=40.0, west_lon=-105.0, cell_m=500.0, nrows=30,
                    ncols=30)
    a, b = _grid_pt(spec, 10.5, 10.5), _grid_pt(spec, 19.5, 15.02)
    field = _late_field(spec, hot=[((15, 13), T0)])
    off = safety.LegOracle(a, b, 7.52, field, dataclasses.replace(
        CFG, corridor_cell_exact=False))
    legacy = safety.LegOracle(a, b, 7.52, field, CFG)
    assert off.samples == legacy.samples
    assert off.latest_safe_depart_s == legacy.latest_safe_depart_s
    assert all(i >= 0 for i, _p, _v, _dt in off.samples)


# ---------------------------------------------------------------------------
# the simulator's judgement (SEMANTICS 20a in SEM-10, 20b egress)
# ---------------------------------------------------------------------------

def _tile_dir(tmp_path, key="boulder_co", cell_m=250.0, size_km=24.0):
    from wgresponse.terrain import SyntheticTerrain, save_ascii_tile
    loc = simui.LOCATIONS[key]
    bbox = geo.bbox_around([loc.center], pad_m=size_km * 500.0)
    tile = SyntheticTerrain(seed=f"egress-judged|{key}",
                            kind="grass").tile(bbox, cell_m)
    d = tmp_path / "tiles"
    d.mkdir(exist_ok=True)
    save_ascii_tile(tile, str(d / f"{key}.wgt"))
    return d


def _pt(bearing, km, key="boulder_co"):
    return geo.destination(simui.LOCATIONS[key].center, bearing, km * 1000.0)


def _judged_session(tmp_path, **cfg):
    """One crew on a synthetic grass tile, straight legs, two catalog zones
    6 km EAST (zA) and 6 km WEST (zB) of the centre."""
    s = SimSession("boulder_co", 2, 1, seed="egress-judged",
                   cfg_overrides=dict({"ensemble_n": 3, "grid_cell_m": 250.0,
                                       "horizon_h": 24.0}, **cfg),
                   tile_dir=_tile_dir(tmp_path), roads_module=None)
    zones = (SafeZone("zA", _pt(90, 6.0), "east zone"),
             SafeZone("zB", _pt(270, 6.0), "west zone"))
    s._loc = dataclasses.replace(s._loc, safe_zones=zones)
    s._zone_by_id = {z.zone_id: z for z in zones}
    return s


def _paint(s, p10_paint=(), nom_paint=()):
    """Inject one fire whose nominal / eroded-P10 fields are painted: paint =
    [(centre LatLon, radius m, unix s)]; the rest UNREACHED."""
    spec = s._tile.spec
    n = spec.nrows * spec.ncols
    nom = Raster(spec, [INF] * n)
    p10 = Raster(spec, [INF] * n)
    for ras, paints in ((nom, nom_paint), (p10, p10_paint)):
        for centre, radius, val in paints:
            for r in range(spec.nrows):
                for c in range(spec.ncols):
                    if geo.haversine_m(centre, geo.rc_to_latlon(spec, r, c)) \
                            <= radius:
                        i = r * spec.ncols + c
                        ras.data[i] = min(ras.data[i], val)
    ens = type("Ens", (), {})()
    ens.members = [nom]
    ens.p10 = p10
    f = _SimFire(incident_id="F1", origin=_pt(90, 20.0), t_ignite_s=s._now_s,
                 intensity="high", wind_dir_deg=270.0, ens=ens)
    f._eroded_src = ens
    f._eroded_p10 = p10
    s._fires = {"F1": f}
    s._fire_order = ["F1"]
    return f


def _deploying_at_centre(s, zone_id="zA"):
    """The crew mid-deploy at the tile centre with its route's adopted zone
    = zone_id (the LAST stop's zone of a longer plan)."""
    cs = next(iter(s._crew_sims.values()))
    centre = simui.LOCATIONS["boulder_co"].center
    z = s._zone_by_id[zone_id]
    stop = {"client_id": "sim-c1", "incident_id": "F1", "loc": centre,
            "service_start_s": s._now_s, "deploy_s": 900.0,
            "latest_depart_s": s._now_s + 7200.0, "gel_fresh_frac": 1.0,
            "actual_arrive_s": s._now_s, "actual_service_start_s": s._now_s}
    cs.pos = centre
    cs.itinerary = [stop]
    cs.egress_loc, cs.egress_zone_id = z.loc, z.zone_id
    cs._mode = "deploy"
    cs._deploy_end_s = s._now_s + 900.0
    return cs


def _band_toward_za(s, minutes=10.0):
    """The eroded-P10 band reaches the straight road to zA 3 km east of the
    centre `minutes` from now (the zones and the west road stay clear)."""
    return _paint(s, p10_paint=[(_pt(90, 3.0), 600.0,
                                 s._now_s + minutes * 60.0)])


@pytest.mark.parametrize("armed", [True, False])
def test_a_withdrawal_egress_is_judged_from_the_stop(tmp_path, armed):
    """A safety withdrawal mid-route: the adopted zone (east) lies across the
    band. Armed: the leg from the stop, now, is not lawful, so the crew is
    sent to the lawful west zone — said loudly. Off: it drives east, into the
    band (the legacy behaviour the Malibu overruns came from)."""
    s = _judged_session(tmp_path, crew_egress_judged=armed)
    _band_toward_za(s)
    cs = _deploying_at_centre(s)
    lead = s._judge_lead_s()
    assert not s._leg_lawful(cs, s._zone_by_id["zA"].loc, s._now_s, lead)
    assert s._leg_lawful(cs, s._zone_by_id["zB"].loc, s._now_s, lead)
    cs.abort_deploy(t_s=s._now_s)
    assert cs._mode == "egress"
    if armed:
        assert cs.egress_zone_id == "zB"
        assert any("egress re-routed (SEMANTICS 20b)" in n for n in s._notes)
    else:
        assert cs.egress_zone_id == "zA"
        assert not any("SEMANTICS 20b" in n for n in s._notes)


def test_a_lawful_adopted_egress_is_kept(tmp_path):
    """Nothing in the way: the adopted zone stays (no churn, no note)."""
    s = _judged_session(tmp_path)
    _paint(s, p10_paint=[(_pt(0, 5.0), 600.0, s._now_s + 600.0)])  # north
    cs = _deploying_at_centre(s)
    cs.abort_deploy(t_s=s._now_s)
    assert cs.egress_zone_id == "zA"
    assert not any("SEMANTICS 20b" in n for n in s._notes)


def test_leaving_the_last_stop_is_judged_too(tmp_path):
    """The deploy completes inside a step and the itinerary is empty: the
    crew egresses from HERE — judged at the completion time."""
    s = _judged_session(tmp_path)
    _band_toward_za(s)
    cs = _deploying_at_centre(s)
    cs._deploy_end_s = s._now_s + 60.0
    serviced = []
    cs.advance(s._now_s, s._now_s + 120.0, serviced)
    assert len(serviced) == 1 and cs._mode == "egress"
    assert cs.egress_zone_id == "zB"


@pytest.mark.parametrize("armed", [True, False])
def test_an_egress_in_flight_is_rejudged_as_a_leg(tmp_path, armed):
    """The Aspen mechanism: an egressing crew's zone is clear NOW (the legacy
    10b check, zero lead, passes) but the pessimistic front reaches it inside
    the buffer before the next audit after arrival. Armed: retargeted to the
    lawful zone this sweep. Off: kept."""
    s = _judged_session(tmp_path, crew_egress_judged=armed)
    cs = next(iter(s._crew_sims.values()))
    za = s._zone_by_id["zA"]
    cs.pos = _pt(90, 3.0)                           # 3 km from zA
    cs.itinerary = []
    cs.egress_loc, cs.egress_zone_id = za.loc, "zA"
    cs._mode = "egress"
    cs._begin_leg("egress", za.loc)
    arrive_s = geo.haversine_m(cs.pos, za.loc) / cs.speed
    buffer_s = s._cfg.egress_buffer_min * 60.0
    _paint(s, p10_paint=[(za.loc, 300.0,
                          s._now_s + arrive_s + buffer_s + 300.0)])
    assert s._zone_currently_safe(za, s._now_s)      # legacy: zone clear
    s._crew_safety_sweep(lead_s=900.0)
    assert cs.egress_zone_id == ("zB" if armed else "zA")


@pytest.mark.parametrize("armed", [True, False])
def test_a_voluntary_decamp_never_rides_exposed_transit(armed):
    """SEMANTICS 20b (iii): the SEM-10e exposed-transit ZONE fallback — a
    VOLUNTARY decamp over pessimistic-exposed transit to a lawful zone — is
    not offered under the judged-leg law (measured: an Eagle one-click
    decamp, extreme, city weather, all-fuel floor, rode 915 s of exposed
    transit to z2 and was audited inside the band at the next step). The
    scripted world of tests/test_escape_proactive.py::test_picker_falls_
    back_to_clean_at_arrival_route (road search off: the zone rules only):
    armed, no move (the imminent law still owns the emergency); off, z2."""
    from tests.test_escape_proactive import (_crew, _key, _script_exposure,
                                             _script_routes, _session,
                                             _zones)
    s = _session(crew_egress_judged=armed, escape_road_network=False)
    cs = _crew(s)
    now = s._now_s
    z1, z2, z3 = _zones(s)
    _script_exposure(s, {z1.zone_id: now + 600.0, z2.zone_id: UNREACHED,
                         z3.zone_id: now + 600.0})
    _script_routes(s, {_key(z1): (False, 0.0, 0.0, 0.0, -100.0),
                       _key(z2): (False, 500.0, -900.0, 1800.0, UNREACHED),
                       _key(z3): (False, 0.0, 10.0, 60.0, UNREACHED)})
    pick = s._proactive_escape_zone(cs.pos, now, cs.speed, 900.0, 7200.0)
    if armed:
        assert pick is None
    else:
        assert pick is not None and pick.zone_id == z2.zone_id


def test_cell_exact_sem10_samples_include_every_cell_and_keep_the_carve_out(
        tmp_path):
    """_sem10_points under the knob: every field cell the path crosses after
    the crew's own is sampled at its exit, the start point keeps dt = 0 and
    the destination stays the last sample; off = _points_with_times."""
    s = _judged_session(tmp_path)
    _paint(s)
    a, b = _pt(90, 0.0), _pt(60, 4.0)
    pts = s._sem10_points([a, b], 7.52)
    spec = s._tile.spec
    assert pts[0] == (a, 0.0) and pts[-1][0] == b
    times = [t for _q, t in pts]
    assert times == sorted(times)
    _exits, idxs = geo.path_cell_runs(spec, [a, b])
    sampled = {geo.latlon_to_rc(spec, q) for q, _t in pts[1:]}
    start = geo.latlon_to_rc(spec, a)
    for i in idxs:
        rc = divmod(i, spec.ncols)
        assert rc in sampled or rc == start
    s0 = _judged_session(tmp_path, corridor_cell_exact=False)
    _paint(s0)
    assert s0._sem10_points([a, b], 7.52) == SimSession._points_with_times(
        [a, b], 7.52)


def test_road_escape_into_the_black_cell_exact(tmp_path):
    """The cell-exact twin of tests/test_sim_old_black.py::
    test_road_escape_into_the_black_behind_the_front: the crew at node 0 in
    the band escapes west over black the front left 5 h ago; the black costs
    nothing, the only exposure is leaving the band's 500 m CELLS (centres
    within 400 m of node 0: the farthest reaches 400 m + half a cell
    diagonal), and the search's numbers are still exactly the route law's."""
    from tests.test_sim_escape_roads import (LEAD, SPEED, _fire, _node_of,
                                             _pt as _npt)
    from tests.test_sim_escape_roads import _session as _net_session
    s, g = _net_session(tmp_path)
    assert s._cfg.corridor_cell_exact
    now = s._now_s
    west = [(_npt(270, km), 450.0, now - 5 * 3600.0)
            for km in (1.5, 3.0, 4.5, 6.0)]
    _fire(s, paint_nom=list(west),
          paint_p10=list(west) + [(_npt(90, 1.5), 500.0, now + 60.0),
                                  (_npt(90, 3.0), 500.0, now + 60.0),
                                  (g.nodes[0], 400.0, now + 60.0),
                                  (_npt(0, 1.5), 450.0, now + 60.0),
                                  (_npt(0, 3.0), 450.0, now + 60.0)])
    found = s._road_escape_search(g.nodes[0], now, SPEED, LEAD)
    assert found is not None and _node_of(g, found["loc"]) in (5, 6, 7)
    band_cells_s = (400.0 + 500.0 * math.sqrt(2.0) / 2.0) / SPEED
    assert 0.0 < found["exposure_s"] <= band_cells_s
    assert math.isinf(found["dm_s"])
    b, e, _w, t, dm = s._route_metrics(g.nodes[0], found["loc"], now,
                                       SPEED, LEAD)
    assert not b and e == found["exposure_s"] and t == found["total_s"]
    assert math.isinf(dm)


# ---------------------------------------------------------------------------
# both knobs OFF = byte-identical (goldens computed on commit 8973cd9, before
# SEMANTICS 20, on worlds where it bites)
# ---------------------------------------------------------------------------

OFF = {"corridor_cell_exact": False, "crew_egress_judged": False}

#: sha256(state minus cmd_log)[:16] per tick, computed on 8973cd9
GOLDEN_MALIBU = ["9ef10b49b9e37515", "00776b4a801c205d", "ae4d5f0d648277d2",
                 "2c51dd740ec5bb8f"]
GOLDEN_TRIAGE = ["a0ce1f1bdbbaddb6", "b8c9c01c1a45c9f4", "4dbf779ab30e1bea",
                 "280b372442a632b8"]


def _hash(st):
    st = dict(st)
    st.pop("cmd_log", None)            # echoes the override keys themselves
    return hashlib.sha256(json.dumps(st, sort_keys=True, allow_nan=False)
                          .encode()).hexdigest()[:16]


def _one_click(key, intensity, profile, grass_only, **cfg):
    """The page's one-click scenario (server.SCENARIOS: spotting, grass
    floor 0.10, plan on roads, 24 h hold, the simulator defaults), re-lit at
    its own ignition point and wind when `intensity` is not its preset.
    The legacy ember draw, first-burnable ignition, surface-only forest and
    16-direction spread are pinned: the sweep that reproduced these overruns
    and the goldens below predate the simulator's calibrated-ember default,
    the fine-fuel starts, crown fire (founder options 1-3, 2026-10-12) and the
    spread-direction fix (2026-10-14)."""
    mgr = server.SimSessionManager()
    code, out = mgr.new_session({"scenario": key, "ign_fine_fuel": False,
                                 "cfg": dict(
        {"sim_weather_profile": profile, "ros_floor_grass_only": grass_only,
         "spot_fire_size": False, "crown_fire": False,
         "spread_any_angle": False}, **cfg)})
    assert code == 200, out
    s = mgr._entry(out["session_id"]).session
    f = s._fires["sim-f1"]
    if intensity != f.intensity:
        lat, lon, wd = f.origin.lat, f.origin.lon, f.wind_dir_deg
        s.reset()
        s.ignite(lat, lon, intensity=intensity, wind_dir_deg=wd)
    return s


def test_knobs_off_malibu_one_click_is_byte_identical_and_still_overruns():
    """Both knobs off: the Malibu one-click (city extreme, grass-only floor)
    replays the pre-rule session byte for byte, overrun included — at T+0.75 h
    sim-g1, withdrawn by the road-egress guard at its first home, is egressing
    east toward the last stop's zone through the band (the regression arm
    below therefore bites)."""
    s = _one_click("malibu_santa_ana", "extreme", "city", True, **OFF)
    hashes, rows = [], []
    for k in range(len(GOLDEN_MALIBU)):
        st = s.tick()
        hashes.append(_hash(st))
        if k == 2:
            rows = st["crew_overruns"]
    assert hashes == GOLDEN_MALIBU
    assert [(r["crew_id"], r["reason"], r["mode"]) for r in rows] == [
        ("sim-g1", "imminent", "egress")]


def test_knobs_off_triage_scatter_is_byte_identical_and_still_overruns(
        tmp_path):
    """Both knobs off: the synthetic triage scatter (city weather) replays
    the pre-rule session byte for byte, its T+1.0 h overrun included (the
    chord that clips the ignition cell's band between two samples);
    tests/test_city_weather.py::test_young_fire_leg_no_overrun_under_city_
    weather is the armed arm."""
    from tests.test_simui import (_nudge_to_burnable, _triage_scatter_dir,
                                  _upwind_ignition)
    d = _triage_scatter_dir(tmp_path, seed="C")
    s = SimSession("boulder_co", 50, 1, seed="C", cfg_overrides=dict({
        "ensemble_n": 3, "grid_cell_m": 500.0, "horizon_h": 120.0,
        "sim_weather_profile": "city",
        # the 16-direction spread the golden was computed under (the
        # simulator arms the spread-direction fix since 2026-10-14)
        "spread_any_angle": False}, **OFF), tile_dir=d, roads_module=None)
    ign = _nudge_to_burnable(s, _upwind_ignition("boulder_co", 5_000.0))
    s.ignite(ign.lat, ign.lon, intensity="moderate")
    hashes, viol = [], []
    for _ in range(len(GOLDEN_TRIAGE)):
        st = s.tick()
        hashes.append(_hash(st))
        viol.append(st["scoreboard"]["violations"])
    assert hashes == GOLDEN_TRIAGE
    assert viol == [0, 0, 0, 1]


# ---------------------------------------------------------------------------
# every overrun the sweep reproduced before the rule — now 0
# ---------------------------------------------------------------------------

#: (scenario, intensity, weather, grass-only floor, ticks): each produced a
#: crew-overrun row ("imminent", egressing) at its last tick before the rule
#: (scratch_review/crew_egress_gap_sweep.py, 13 one-click scenarios x 4
#: intensities x 2 weathers x 2 floor class sets, 6 h; the first row's time
#: and the mechanism in the comment — W: a withdrawal egress to the last
#: stop's zone through the band; Z: a zone that turns imminent before the
#: first audit after arrival; V: a voluntary decamp over exposed transit).
REPRODUCED = [
    ("malibu_santa_ana", "extreme", "city", True, 3),           # 0.75 h W
    ("malibu_santa_ana", "high", "city", True, 5),              # 1.25 h W
    ("malibu_santa_ana", "high", "city", False, 7),             # 1.75 h W
    ("malibu_santa_ana", "high", "generic", True, 7),           # 1.0, 1.75 W
    ("malibu_santa_ana", "moderate", "generic", True, 9),       # 2.25 h W
    ("aspen_southwesterly", "high", "city", False, 14),         # 3.5 h Z
    ("glenwood_springs_westerly", "extreme", "generic", False, 3),  # 0.75 Z
    ("glenwood_springs_westerly", "extreme", "city", False, 6),     # 1.5 h Z
    ("eagle_gypsum_westerly", "extreme", "city", False, 16),    # 4.0 h V
]


@pytest.mark.parametrize("key,intensity,profile,grass_only,ticks", REPRODUCED)
def test_reproduced_overrun_is_gone(key, intensity, profile, grass_only, ticks):
    s = _one_click(key, intensity, profile, grass_only)
    assert s._cfg.corridor_cell_exact and s._cfg.crew_egress_judged
    for _ in range(ticks):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, st["crew_overruns"]
