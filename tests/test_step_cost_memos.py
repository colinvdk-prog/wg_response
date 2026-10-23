"""THE STEP COST OF THE EAGLE RIVER VALLEY LAWS (2026-10-23; integrator brief
"make them faster WITHOUT changing any decision"; docs/SIMULATOR_DESIGN.md
"The safety laws' step cost").

The crew-safety laws of SAFETY SEMANTICS 25-38 made the simulator's steps
slower: the SEMANTICS 29 way-out predicate re-walked and re-sampled the same
egress legs (a stop to every catalog zone) at every judgement, step after
step, and SEMANTICS 38's road-refuge window bisects the departure time with
a full road search per probe. The fixes are pure performance:

  * an egress leg's samples (pass offsets and tile cells) are memoised per
    session (_way_leg) and the fields are read LIVE at those cells
    (_esc_fields) — never a cached value;
  * _crew_exposure reads each grid at the point's cell memoised per grid
    (_cell_of), the value live;
  * the bisection's probes ask only WHETHER a refuge exists
    (_road_escape_search(exists_only=True)), stopping at the first refuge
    the full search would take;
  * escape-edge samples take their cold-black window from the tile cell in
    hand, and a search looks its edge table up once.

These tests pin each against the verbatim pre-change bodies (reproduced
here) on a real tile with a burning, partly burned-out fire, and that the
fields are read live (a field repainted in place is seen at once). The
whole-simulator proof — every tick's state() byte-identical on the 13
one-click scenarios and the valley harness — is in
scratch_review/safety_law_perf_probe.py and
scratch_review/eagle_valley/run_matrix_hashed.py."""
from __future__ import annotations

import math

import pytest

from wgresponse import geo, server, simui
from wgresponse.contracts import UNREACHED, LatLon
from wgresponse.fuels import cold_black_minutes

SCENARIO = "boulder_chinook"
TICKS = 10


@pytest.fixture(scope="module")
def burning():
    """The Boulder one-click scenario 2.5 h in: a wind-driven grass fire,
    part burning, part old black."""
    if not simui.tile_file("boulder_co").exists():
        pytest.skip("LOUD SKIP: boulder_co tile not cached")
    mgr = server.SimSessionManager()
    code, out = mgr.new_session({"scenario": SCENARIO})
    assert code == 200, out
    s = mgr._entry(out["session_id"]).session
    for _ in range(TICKS):
        s.tick()
    return s


# ---------------------------------------------------------------------------
# the pre-change bodies, verbatim (wgresponse/simui.py at 4bcb7a4)
# ---------------------------------------------------------------------------

def _ref_crew_exposure(s, pos):
    nom = UNREACHED
    p10 = UNREACHED
    for fid in s._fire_order:
        f = s._fires[fid]
        if f.ens is None:
            continue
        nv = geo.raster_sample(f.ens.members[0], pos)
        if nv is not None and nv < nom:
            nom = nv
        er = s._eroded_p10(f)
        if er is not None:
            ev = geo.raster_sample(er, pos)
            if ev is not None and ev < p10:
                p10 = ev
    return nom, p10


def _ref_cold_black(s, p):
    if not getattr(s._cfg, "transit_cold_black_by_class", False):
        return simui._COLD_BLACK_PASS_S
    rc = geo.latlon_to_rc(s._tile.spec, p)
    if rc is None:
        return simui._COLD_BLACK_PASS_S
    code = int(s._tile.fuel.data[rc[0] * s._tile.spec.ncols + rc[1]])
    return cold_black_minutes(code) * 60.0


def _ref_exposure_at(s, pos, t, seen):
    nom, p10 = _ref_crew_exposure(s, pos)
    if (getattr(s._cfg, "old_black_refuge", False)
            and not math.isinf(nom)
            and (t - nom) >= _ref_cold_black(s, pos)):
        seen["old_black"] += 1
        return UNREACHED, UNREACHED
    return nom, p10


def _ref_way_metrics(s, loc, dest, depart_s, speed_mps, seen):
    cfg = s._cfg
    buf_s = cfg.egress_buffer_min * 60.0
    em_s = getattr(cfg, "route_margin_egress_roads_min", 0.0) * 60.0
    ramp = getattr(cfg, "route_margin_egress_ramp", 0.0)
    rr = s._leg_route(loc, dest)
    line_s = buf_s
    slack_s = 0.0
    spd = speed_mps
    raw = [dest]
    if rr is not None:
        raw = list(rr.polyline) + [dest]
        if (rr.time_s and rr.time_s > 0.0 and rr.dist_m
                and rr.dist_m > 0.0):
            spd = rr.dist_m / rr.time_s
        if em_s > 0.0 and getattr(rr, "on_roads", False):
            if ramp > 0.0:
                slack_s = max(buf_s, em_s) - buf_s
            else:
                line_s = max(buf_s, em_s)
    pts = [loc]
    for p in raw:
        if (pts[-1].lat, pts[-1].lon) != (p.lat, p.lon):
            pts.append(p)
    worst = math.inf
    tot = 0.0
    samples = s._strict_points(pts, spd)
    t_end = samples[-1][1] if samples else 0.0
    for q, dt in samples:
        tot = dt
        pass_t = depart_s + dt
        nom, p10 = _ref_exposure_at(s, q, pass_t, seen)
        if ((not math.isinf(nom)) and nom <= pass_t
                and (pass_t - nom) < _ref_cold_black(s, q)):
            worst = -math.inf
            break
        if not math.isinf(p10):
            if slack_s > 0.0:
                m = p10 - pass_t - (line_s + (
                    slack_s if dt >= t_end
                    else min(slack_s, ramp * dt)))
            else:
                m = p10 - pass_t - line_s
            if m < worst:
                worst = m
    return worst, tot


def _legs(s):
    """Every catalog zone and every home, to every catalog zone."""
    zones = sorted(s._loc.safe_zones, key=lambda z: z.zone_id)
    starts = [z.loc for z in zones] + [cl.loc for cl in s._clients]
    return [(a, z.loc) for a in starts for z in zones if a != z.loc]


def _departures(s):
    now = s._now_s
    return [now - 2 * 3600.0, now - 900.0, now, now + 900.0,
            now + 2 * 3600.0, now + 6 * 3600.0]


# ---------------------------------------------------------------------------
# SEMANTICS 29: the way-out leg
# ---------------------------------------------------------------------------

def test_way_metrics_is_the_pre_change_law(burning):
    s = burning
    speeds = sorted({cs.speed for cs in s._crew_sims.values()})
    seen = {"old_black": 0}
    kinds = {"crossed": 0, "bounded": 0, "clear": 0}
    for loc, dest in _legs(s):
        for spd in speeds:
            for d in _departures(s):
                got = s._way_metrics(loc, dest, d, spd)
                assert got == _ref_way_metrics(s, loc, dest, d, spd, seen)
                kinds["crossed" if got[0] == -math.inf
                      else "clear" if got[0] == math.inf else "bounded"] += 1
    # the world exercises every branch: legs the front crosses, legs bounded
    # by the band, clear legs, and old black passed on the way
    assert all(kinds.values()), kinds
    assert seen["old_black"] > 0


def test_way_metrics_reads_the_fields_live(burning):
    """A field repainted IN PLACE (the scripted worlds of the suite paint
    rasters that way) is seen by the very next judgement: the memo holds
    geometry, never a value."""
    s = burning
    spd = next(iter(s._crew_sims.values())).speed
    now = s._now_s
    f = s._fires[s._fire_order[0]]
    nom = f.ens.members[0].data
    for loc, dest in _legs(s):
        worst, _tot = s._way_metrics(loc, dest, now, spd)
        if math.isfinite(worst) and worst > 3600.0:
            break
    else:
        pytest.fail("no leg clear by an hour in this world")
    # a cell of the leg the nominal front never reaches (never old black)
    _t_end, _dts, idxs = next(ent[2] for k, ent in s._way_legs.items()
                              if k[:4] == (loc.lat, loc.lon,
                                           dest.lat, dest.lon))
    cell = next(i for i in idxs[len(idxs) // 2:]
                if i >= 0 and math.isinf(nom[i]))
    er = s._eroded_p10(f)
    old = er.data[cell]
    try:
        er.data[cell] = now + 600.0                   # the band, in place
        seen = {"old_black": 0}
        got = s._way_metrics(loc, dest, now, spd)
        assert got[0] < worst
        assert got == _ref_way_metrics(s, loc, dest, now, spd, seen)
    finally:
        er.data[cell] = old
    assert s._way_metrics(loc, dest, now, spd)[0] == worst


def test_a_scripted_world_is_judged_through_exposure_at(burning):
    """_crew_exposure replaced on the instance (a scripted world): the leg is
    judged sample by sample through _exposure_at, exactly the pre-change
    loop over the scripted answers."""
    s = burning
    spd = next(iter(s._crew_sims.values())).speed
    live = s._crew_exposure
    s._crew_exposure = lambda pos: live(pos)
    try:
        assert s._live_fields() is None
        for loc, dest in _legs(s)[:12]:
            for d in _departures(s):
                seen = {"old_black": 0}
                assert (s._way_metrics(loc, dest, d, spd)
                        == _ref_way_metrics(s, loc, dest, d, spd, seen))
    finally:
        del s._crew_exposure
    assert s._live_fields() is not None


def test_a_pinned_route_is_a_new_leg(burning):
    """The leg memo holds the route object it walked: a different route for
    the same endpoints (a pinned SEM-10d escape path) is walked afresh."""
    s = burning
    spd = next(iter(s._crew_sims.values())).speed
    loc, dest = _legs(s)[0]
    s._way_metrics(loc, dest, s._now_s, spd)
    straight = s._roads.route(None, loc, dest, s._cfg)   # the chord fallback
    key = (loc.lat, loc.lon, dest.lat, dest.lon)
    s._escape_pins[key] = straight
    try:
        assert s._leg_route(loc, dest) is straight
        for d in _departures(s):
            seen = {"old_black": 0}
            assert (s._way_metrics(loc, dest, d, spd)
                    == _ref_way_metrics(s, loc, dest, d, spd, seen))
    finally:
        del s._escape_pins[key]


# ---------------------------------------------------------------------------
# the fields at a point; escape-edge samples
# ---------------------------------------------------------------------------

def test_crew_exposure_is_raster_sample(burning):
    s = burning
    spec = s._tile.spec
    pts = [geo.rc_to_latlon(spec, r, c)
           for r in range(0, spec.nrows, 7) for c in range(0, spec.ncols, 5)]
    # cell corners and points off the tile too
    pts += [LatLon(spec.north_lat - 1e-9, spec.west_lon + 1e-9),
            LatLon(spec.north_lat + 0.01, spec.west_lon),
            LatLon(spec.north_lat - 0.5, spec.west_lon - 0.5)]
    finite = 0
    for _round in range(2):                       # cold, then from the memo
        for p in pts:
            got = s._crew_exposure(p)
            assert got == _ref_crew_exposure(s, p)
            finite += not math.isinf(got[0])
    assert finite > 0


def test_escape_edge_samples_take_the_cold_black_of_their_cell(burning):
    s = burning
    g = s._road_graph
    spec = s._tile.spec
    n = 0
    for ei in range(0, len(g.edges), max(1, len(g.edges) // 400)):
        for fwd in (True, False):
            for _d, q, idx, cb, _cix in s._esc_edge_samples(ei, fwd):
                rc = geo.latlon_to_rc(spec, q)
                assert idx == (-1 if rc is None
                               else rc[0] * spec.ncols + rc[1])
                assert cb == _ref_cold_black(s, q)
                n += 1
    assert n > 1000


# ---------------------------------------------------------------------------
# SEMANTICS 38: the bisection's existence probes
# ---------------------------------------------------------------------------

def test_the_existence_probe_answers_as_the_full_search(burning):
    """_road_refuge_until's probe — a lawful durable road refuge departing
    at d — answered by the existence-only search exactly as by the full
    search, over the bisection's whole span, from every zone and a spread
    of homes."""
    s = burning
    cfg = s._cfg
    spd = next(iter(s._crew_sims.values())).speed
    lead = s._judge_lead_s()
    h_s = cfg.egress_dest_durable_h * 3600.0
    buf_s = cfg.egress_buffer_min * 60.0
    starts = ([z.loc for z in s._loc.safe_zones]
              + [cl.loc for cl in s._clients[::5]])
    answers = set()
    for loc in starts:
        for k in range(0, 49, 4):
            d = s._now_s + k * 1800.0
            for on_map in (True, False):
                full = s._road_escape_search(
                    loc, d, spd, lead, lawful_only=True,
                    min_p10_s=d + h_s + buf_s, on_map_only=on_map)
                probe = s._road_escape_search(
                    loc, d, spd, lead, lawful_only=True,
                    min_p10_s=d + h_s + buf_s, on_map_only=on_map,
                    exists_only=True)
                assert (probe is not None) == (full is not None)
                assert probe in (True, None)
                answers.add(full is not None)
    assert answers == {True, False}


def test_a_spied_search_is_asked_as_it_always_was(burning):
    """A search replaced on the instance (a test's spy, whose signature
    predates exists_only) still serves the bisection — with the old call."""
    s = burning
    spd = next(iter(s._crew_sims.values())).speed

    def window(loc):
        s._dest_keep = {}                         # ask afresh
        return s._road_refuge_until(loc, spd)
    for cl in s._clients:
        v = window(cl.loc)
        if s._now_s < v < s._now_s + simui.DEST_ROAD_SPAN_S:
            break                                 # a window the probes bisect
    else:
        pytest.fail("no bisected road-refuge window in this world")
    calls = []
    orig = s._road_escape_search

    def spy(pos, now, speed_mps, lead_s, lawful_only=False,
            min_p10_s=-math.inf, on_map_only=False):
        calls.append(now)
        return orig(pos, now, speed_mps, lead_s, lawful_only, min_p10_s,
                    on_map_only)
    s._road_escape_search = spy
    try:
        assert window(cl.loc) == v
    finally:
        del s._road_escape_search
    assert len(calls) > 3                         # every probe, through the spy
