"""tests/test_wind_direction.py — the wind DIAL must steer the fire.

Founder observation (2026-09-25): "in the public simulator, changing the wind
direction does not seem to change how the fire propagates".

What scratch_review/wind_direction_probe.py measured on the real tiles
(park_city_ut / vail_co / basalt_carbondale_co, high + extreme):
  * the ENGINE was direction-correct — an ignition under wind FROM w puts the
    burn centroid within ~6 deg of the downwind bearing w + 180, L/B 3-8;
  * the PAGE's dial reached the engine ONLY inside /ignite: no mid-run command
    existed (no dial change handler, no /config key), and the page's 1 s
    render loop snapped the dial back to the session wind — so a dial turn
    made while the sim ran changed nothing, not even the next ignition;
  * the engine's own mid-run seam (SimSession.set_wind) re-anchored each fire
    by filling its angle-sorted boundary ring as a perimeter polygon, which
    un-burned 13-54% of a 1-3 h burn and stamped unburned wedges burned, and
    re-ran a fire too young for a ring from its ignition time (rewriting its
    past under the new wind); with spotting on (the public default) every
    kept cell also re-threw its ember toward the new downwind side at once.
Fixed by POST /api/sim/<id>/config {wind_dir_deg} -> SimSession.set_wind, the
page wiring, and an EXACT, QUIET re-anchor (every burned cell seeds at `now`
without re-throwing its ember) — a mid-run turn now matches one continuous
run with the same wind shift.

Offline + fast: tests/test_simui.py's controlled uniform-grass tiles (flat
GR2) and FAST config, roads pinned off; the HTTP test reuses
tests/test_server.py's loopback fixture.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

from wgresponse import geo, spread
from wgresponse.server import SimSessionManager
from wgresponse.simui import LOCATIONS, SimSession

from tests.test_simui import FAST, _controlled_tile_dir
from tests.test_server import (  # noqa: F401  (sim_ctx is a pytest fixture)
    KEY as API_KEY,
    SIM_KEY,
    _sim_new,
    _sim_post,
    sim_ctx,
)

LOC = "boulder_co"
HOUR = 3600.0
SIMULATOR_HTML = (Path(__file__).resolve().parent.parent / "console"
                  / "simulator.html")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _grass_session(tmp_path, *, cell_m=500.0, hamlet_half=0, **cfg):
    """1 client / 1 crew session on a controlled flat uniform-grass tile
    (hamlet_half=0: no developed block — every direction has equal room)."""
    d = tmp_path / f"tiles_{int(cell_m)}_{hamlet_half}"
    d.mkdir(exist_ok=True)
    d = _controlled_tile_dir(d, LOC, size_km=20.0, cell_m=cell_m,
                             hamlet_half=hamlet_half)
    c = dict(FAST)
    c.update(cfg)
    return SimSession(LOC, 1, 1, seed="wind", cfg_overrides=c, tile_dir=d,
                      roads_module=None)


def _snap(session, p):
    """Center of the tile cell containing p (exact geometry)."""
    spec = session._tile.spec
    return geo.rc_to_latlon(spec, *geo.latlon_to_rc(spec, p))


def _xy(origin, p):
    return ((p.lon - origin.lon) * geo.m_per_deg_lon(origin.lat),
            (p.lat - origin.lat) * geo.M_PER_DEG_LAT)


def _centroid(values, spec, origin, lo, hi):
    """(east_m, north_m, n): centroid of the cells with lo < value <= hi."""
    n = 0
    sx = sy = 0.0
    nc = spec.ncols
    for i, v in enumerate(values):
        if lo < v <= hi:
            x, y = _xy(origin, geo.rc_to_latlon(spec, i // nc, i % nc))
            sx += x
            sy += y
            n += 1
    return (sx / n, sy / n, n) if n else (0.0, 0.0, 0)


def _bearing(dx, dy):
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _angdiff(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _burned(values, t):
    return {i for i, v in enumerate(values) if v <= t}


def _rle_cells(rle):
    """Decode state.terrain.burned (the NOMINAL burned mask, rle01)."""
    out = set()
    i = 0
    val = rle["first_value"]
    for run in rle["runs"]:
        if val:
            out.update(range(i, i + run))
        i += run
        val = 1 - val
    return out


# ---------------------------------------------------------------------------
# 1. ignition-time wind: a 90 deg turn of the wind turns the burn 90 deg
# ---------------------------------------------------------------------------

def test_ignition_wind_rotation_rotates_the_burn(tmp_path):
    """Four sessions, identical but for the dial at ignition (FROM 0/90/180/
    270): the nominal burn centroid (sim ground truth) sits on the downwind
    bearing w + 180, so every 90 deg turn of the wind turns the burn 90 deg;
    the ensemble P50 (the ladder median — the page's burned fill until the
    2026-09-26 "shade the fire" change) and the h5 band follow."""
    bearings = []
    bands = []
    for w in (0.0, 90.0, 180.0, 270.0):
        s = _grass_session(tmp_path)
        ign = _snap(s, LOCATIONS[LOC].center)
        fid = s.ignite(ign.lat, ign.lon, intensity="high", wind_dir_deg=w)
        st = s.tick()
        f = s._fires[fid]
        spec = s._tile.spec
        t = f.t_ignite_s + 1.5 * HOUR
        cx, cy, n = _centroid(f.ens.members[0].data, spec, ign, -math.inf, t)
        assert n >= 6, "the fire must have run for the geometry to mean much"
        assert math.hypot(cx, cy) > 1500.0, (w, cx, cy)
        brg = _bearing(cx, cy)
        assert _angdiff(brg, w + 180.0) <= 5.0, (w, brg)
        # the burn is a wind-aligned ellipse, not a disc
        along = cross = 0.0
        to = math.radians(w + 180.0)
        for i in _burned(f.ens.members[0].data, t):
            x, y = _xy(ign, geo.rc_to_latlon(spec, i // spec.ncols,
                                             i % spec.ncols))
            along = max(along, abs(x * math.sin(to) + y * math.cos(to)))
            cross = max(cross, abs(x * math.cos(to) - y * math.sin(to)))
        assert along >= 3.0 * max(cross, spec.cell_m), (w, along, cross)
        # the ladder median (the page's fill until 2026-09-26) points the same way
        ux, uy, un = _centroid(f.ens.p50.data, spec, ign, -math.inf, t)
        assert un >= 3 and _angdiff(_bearing(ux, uy), w + 180.0) <= 10.0
        assert st["wind_dir_deg"] == w
        bands.append(json.dumps(st["fire_bands"]["h5"]))
        bearings.append(brg)
    for a, b in zip(bearings, bearings[1:]):
        turn = (b - a) % 360.0
        assert abs(turn - 90.0) <= 5.0, (bearings, turn)
    assert len(set(bands)) == 4, "the h5 band must differ for every wind"


# ---------------------------------------------------------------------------
# 2. mid-run wind: set_wind moves the NEXT tick's forecast
# ---------------------------------------------------------------------------

def test_midrun_set_wind_resteers_the_next_tick(tmp_path):
    """Ignite under wind FROM 270 (head runs east), run 1 h, turn the wind to
    FROM 0: nothing is recomputed until the next tick (lazy, like every
    world change); that tick re-anchors the fire AT the switch time, the
    P50 arrival 3 km south of the burn drops from 'never inside the
    horizon' to under 2 h, the old head direction slows to a flank, and all
    growth after the switch runs south."""
    s = _grass_session(tmp_path)
    ign = _snap(s, geo.destination(LOCATIONS[LOC].center, 270.0, 4000.0))
    fid = s.ignite(ign.lat, ign.lon, intensity="high", wind_dir_deg=270.0)
    for _ in range(4):
        s.tick()
    f = s._fires[fid]
    spec = s._tile.spec
    t_sw = s._now_s
    assert t_sw == f.t_ignite_s + HOUR
    old = f.ens
    bx, by, bn = _centroid(old.members[0].data, spec, ign, -math.inf, t_sw)
    assert bn >= 5 and _angdiff(_bearing(bx, by), 90.0) <= 5.0
    mid = geo.destination(geo.destination(ign, 90.0, bx), 0.0, by)
    south = geo.destination(mid, 180.0, 3000.0)
    ahead_east = geo.destination(ign, 90.0, 6000.0)
    s_before = spread.eta_at(old, south).eta_p50_s
    e_before = spread.eta_at(old, ahead_east).eta_p50_s
    assert math.isinf(s_before) or s_before - t_sw > 12 * HOUR
    assert e_before - t_sw < 1.5 * HOUR          # the east head is coming

    s.set_wind(0.0)
    assert f.ens is old, "set_wind is lazy: the field re-anchors next tick"
    st = s.tick()
    assert f.ens is not old, "the wind change must re-anchor the fire"
    assert f.anchor_t0_s == t_sw
    assert st["wind_dir_deg"] == 0.0
    assert st["fires"][0]["wind_dir_deg"] == 0.0
    s_after = spread.eta_at(f.ens, south).eta_p50_s
    e_after = spread.eta_at(f.ens, ahead_east).eta_p50_s
    assert s_after - t_sw < 2.0 * HOUR, (s_after - t_sw) / HOUR
    assert e_after > e_before + 2.0 * HOUR       # the old head is now a flank
    # all growth after the switch runs south of the burned-at-switch centroid
    gx, gy, gn = _centroid(f.ens.members[0].data, spec, ign, t_sw,
                           t_sw + 1.5 * HOUR)
    assert gn >= 10
    assert _angdiff(_bearing(gx - bx, gy - by), 180.0) <= 15.0


# ---------------------------------------------------------------------------
# 3. the re-anchor keeps the burn EXACTLY (nothing un-burns, nothing invented)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cell_m,hamlet_half,spotting,ticks,why", [
    # a fire stalled against the (non-burnable) checkerboard block has < 3
    # boundary cells: the old path re-ran it from its IGNITION TIME under the
    # new wind, back-dating 19 burned cells into the past
    (500.0, 4, False, 16, "young/stalled fire"),
    # spot-fire islands + a concave front: the angle-sorted ring filled as a
    # polygon dropped a burned cell and stamped two unburned ones
    (250.0, 4, True, 16, "spotting islands"),
])
def test_reanchor_keeps_the_burned_mask_exactly(tmp_path, cell_m, hamlet_half,
                                                spotting, ticks, why):
    s = _grass_session(tmp_path, cell_m=cell_m, hamlet_half=hamlet_half,
                       spotting_enabled=spotting)
    ign = _snap(s, geo.destination(LOCATIONS[LOC].center, 270.0, 3000.0))
    fid = s.ignite(ign.lat, ign.lon, intensity="high", wind_dir_deg=270.0)
    for _ in range(ticks):
        s.tick()
    f = s._fires[fid]
    t_sw = s._now_s
    before = _burned(f.ens.members[0].data, t_sw)
    s.set_wind(0.0)
    s.tick()
    after = _burned(f.ens.members[0].data, t_sw)
    assert after == before, (why, len(before), sorted(before - after),
                             sorted(after - before))
    # the kept burn is burned in EVERY member (it is history, not forecast)
    for q in (f.ens.p10, f.ens.p50, f.ens.p90):
        assert all(q.data[i] <= t_sw for i in before), why


def test_midrun_turn_matches_one_continuous_run_with_the_shift(tmp_path):
    """The yardstick for a mid-run wind change: ONE continuous nominal run
    from the ignition under the same scripted weather with the wind shifting
    at the switch hour (piecewise edge weather, so edges already crossing the
    shift continue at the new rate). With spotting ON the re-anchored fire
    must match it — which needs the kept cells to stay QUIET
    (seed_spotting=False): re-throwing their embers showers the whole new
    downwind side of the burn at once (the 'loud' control below)."""
    import dataclasses

    from wgresponse.simui import (GUST_FACTOR, INTENSITY_WEATHER, MPH_TO_MPS,
                                  T0_S)
    from wgresponse.weather import MockWeather

    s = _grass_session(tmp_path, cell_m=250.0, spotting_enabled=True)
    ign = _snap(s, geo.destination(LOCATIONS[LOC].center, 270.0, 5000.0))
    fid = s.ignite(ign.lat, ign.lon, intensity="extreme", wind_dir_deg=270.0)
    f = s._fires[fid]
    for _ in range(8):
        s.tick()
    t_sw = s._now_s
    s.set_wind(0.0)
    p = INTENSITY_WEATHER["extreme"]
    u = p["wind_mph"] * MPH_TO_MPS
    shift_h = int(round((t_sw - T0_S) / HOUR))
    wx = MockWeather(seed=f"simui|{s._seed}|wx", base_wind_mps=u,
                     dir_deg=270.0, rh_pct=p["rh_pct"], temp_c=p["temp_c"],
                     gust_factor=GUST_FACTOR, t0_s=T0_S,
                     shifts=[(shift_h, u, 0.0, p["rh_pct"])]).forecast(
        LOCATIONS[LOC].center, s._cfg.horizon_h)
    truth = spread.compute_arrival(
        s._tile, [f.origin], wx, f.t_ignite_s,
        dataclasses.replace(s._cfg, edge_weather_piecewise=True),
        incident_id=fid, member_k=0).data
    loud = spread.compute_arrival(
        s._tile, s._burned_seed_points(f, t_sw), s._wx, t_sw, s._cfg,
        incident_id=fid, member_k=0).data        # seeds re-throw embers
    s.tick()
    got = f.ens.members[0].data

    def iou(a, b):
        return len(a & b) / max(1, len(a | b))

    for lead_h in (0.25, 0.5, 1.0):
        t = t_sw + lead_h * HOUR
        ref = _burned(truth, t)
        assert iou(_burned(got, t), ref) >= 0.95, lead_h
    t = t_sw + 0.25 * HOUR
    ref = _burned(truth, t)
    assert iou(_burned(loud, t), ref) < iou(_burned(got, t), ref) - 0.05, \
        "control: the scenario must exercise the re-anchor ember shower"


def test_seed_spotting_default_is_byte_identical(tmp_path):
    """spread's new seed_spotting seam defaults to True — the pre-seam
    engine, bit for bit (and it is inert with spotting off)."""
    s = _grass_session(tmp_path, cell_m=500.0, spotting_enabled=True)
    ign = _snap(s, LOCATIONS[LOC].center)
    s.ignite(ign.lat, ign.lon, intensity="extreme", wind_dir_deg=270.0)
    s.tick()
    args = (s._tile, [ign], s._wx, s._now_s, s._cfg)
    a = spread.ensemble_arrival(*args, incident_id="x")
    b = spread.ensemble_arrival(*args, incident_id="x", seed_spotting=True)
    for ra, rb in zip(a.members + [a.p10, a.p50, a.p90],
                      b.members + [b.p10, b.p50, b.p90]):
        assert ra.data == rb.data
    import dataclasses
    off = dataclasses.replace(s._cfg, spotting_enabled=False)
    c = spread.compute_arrival(s._tile, [ign], s._wx, s._now_s, off)
    d = spread.compute_arrival(s._tile, [ign], s._wx, s._now_s, off,
                               seed_spotting=False)
    assert c.data == d.data


def test_same_wind_reanchor_is_transparent(tmp_path):
    """A world change that does not touch this fire's weather (the wind
    re-set to the same bearing, as a second ignition's shared-weather rebuild
    does) re-anchors without moving the burn: identical burned mask at the
    switch and the same run direction afterwards."""
    s = _grass_session(tmp_path, spotting_enabled=True)
    ign = _snap(s, geo.destination(LOCATIONS[LOC].center, 270.0, 4000.0))
    fid = s.ignite(ign.lat, ign.lon, intensity="high", wind_dir_deg=270.0)
    for _ in range(4):
        s.tick()
    f = s._fires[fid]
    spec = s._tile.spec
    t_sw = s._now_s
    before = _burned(f.ens.members[0].data, t_sw)
    bx, by, _ = _centroid(f.ens.members[0].data, spec, ign, -math.inf, t_sw)
    s.set_wind(270.0)
    s.tick()
    assert f.anchor_t0_s == t_sw
    assert _burned(f.ens.members[0].data, t_sw) == before
    gx, gy, gn = _centroid(f.ens.members[0].data, spec, ign, t_sw,
                           t_sw + HOUR)
    assert gn >= 3 and _angdiff(_bearing(gx - bx, gy - by), 90.0) <= 10.0


# ---------------------------------------------------------------------------
# 4. the PUBLIC path: /api/sim/<id>/config {wind_dir_deg} turns a running fire
# ---------------------------------------------------------------------------

def _run_api_fire(ctx, seed, turn_to):
    """New session -> ignite under FROM 270 -> 4 steps -> optional dial turn
    via /config -> 4 more steps. Returns (growth bearing from the burned-at-
    switch centroid, the config response or None)."""
    _, data = _sim_new(ctx, clients=1, crews=1, seed=seed, spotting=False,
                       grass_realism=False)
    sid = data["session_id"]
    center = LOCATIONS[SIM_KEY].center
    ign = geo.destination(center, 315.0, 8000.0)     # NW: room east + south
    status, out = _sim_post(ctx, sid, "ignite", {
        "lat": ign.lat, "lon": ign.lon, "intensity": "high",
        "wind_dir_deg": 270.0})
    assert status == 200, out
    st = out["state"]
    for _ in range(4):
        _, out = _sim_post(ctx, sid, "ctl", {"action": "step"})
        st = out["state"]
    spec_n, spec_c = st["terrain"]["burned"]["nrows"], st["terrain"]["burned"]["ncols"]
    before = _rle_cells(st["terrain"]["burned"])
    cfg_out = None
    if turn_to is not None:
        status, cfg_out = _sim_post(ctx, sid, "config",
                                    {"wind_dir_deg": turn_to})
        assert status == 200, cfg_out
    for _ in range(4):
        _, out = _sim_post(ctx, sid, "ctl", {"action": "step"})
    after = _rle_cells(out["state"]["terrain"]["burned"])
    assert before <= after, "burned cells must never un-burn"
    grown = after - before
    assert len(grown) >= 5

    def _mean_rc(cells):
        return (sum(i // spec_c for i in cells) / len(cells),
                sum(i % spec_c for i in cells) / len(cells))

    r0, c0 = _mean_rc(before)
    r1, c1 = _mean_rc(grown)
    assert spec_n > 0
    # grid bearing: row 0 = north, +col = east
    return _bearing(c1 - c0, -(r1 - r0)), cfg_out, out["state"]


def test_api_config_wind_turns_a_running_fire(sim_ctx):
    # control: the dial untouched -> the fire keeps running east
    kept, _, _ = _run_api_fire(sim_ctx, "wind-a", None)
    assert _angdiff(kept, 90.0) <= 15.0, kept
    # the dial turned to FROM 0 mid-run -> growth runs south
    turned, cfg_out, st = _run_api_fire(sim_ctx, "wind-b", 0.0)
    assert cfg_out["applied"] == {"wind_dir_deg": 0.0}
    assert cfg_out["state"]["wind_dir_deg"] == 0.0
    assert st["wind_dir_deg"] == 0.0
    assert st["fires"][0]["wind_dir_deg"] == 0.0
    assert _angdiff(turned, 180.0) <= 15.0, turned
    assert any(c.get("cmd") == "set_wind" for c in st["cmd_log"])


@pytest.mark.parametrize("body,status,frag", [
    ({"wind_dir_deg": "north"}, 400, "number"),
    ({"wind_dir_deg": None}, 400, "number"),
    ({"wind_dir_deg": 450}, 200, None),      # any bearing, taken mod 360
    ({"wind_dir_deg": -90}, 200, None),
])
def test_api_config_wind_validation(sim_ctx, body, status, frag):
    _, data = _sim_new(sim_ctx, clients=1, crews=1)
    sid = data["session_id"]
    got, out = _sim_post(sim_ctx, sid, "config", body)
    assert got == status, out
    if frag:
        assert frag in out["error"]
    else:
        want = float(body["wind_dir_deg"]) % 360.0
        assert out["applied"] == {"wind_dir_deg": want}
        # no fire yet: the dial sets the wind the next ignition inherits
        assert out["state"]["wind_dir_deg"] == want


def test_api_config_wind_rejects_nan(sim_ctx):
    _, data = _sim_new(sim_ctx, clients=1, crews=1)
    sid = data["session_id"]
    status, raw = sim_ctx.request("POST", f"/api/sim/{sid}/config",
                                  raw_body=b'{"wind_dir_deg": NaN}',
                                  key=API_KEY)
    assert status == 400
    assert "finite" in json.loads(raw)["error"]


def test_manager_config_keys_include_the_wind_dial():
    assert "wind_dir_deg" in SimSessionManager._CONFIG_KEYS


# ---------------------------------------------------------------------------
# 5. the page: the dial is wired to the engine and never snapped back mid-drag
# ---------------------------------------------------------------------------

def test_simulator_page_wires_the_wind_dial():
    html = SIMULATOR_HTML.read_text(encoding="utf-8")
    # releasing the dial sends the wind to the running session
    assert re.search(r'\$\("wind"\)\.addEventListener\("change"', html)
    assert "sendConfig({ wind_dir_deg: w })" in html
    # the ignite call still carries the dial (first fire of a session)
    assert 'wind_dir_deg: +$("wind").value' in html
    # render() syncs dial + label + arrow from the ENGINE only when the user
    # is not holding the dial (the old code relabelled it every poll)
    m = re.search(r'if \(document\.activeElement !== \$\("wind"\) && '
                  r'windWant == null\) \{(.*?)\n  \}', html, re.S)
    assert m, "render() must guard the wind sync on the dial's focus"
    block = m.group(1)
    for frag in ('$("wind").value = Math.round(st.wind_dir_deg)',
                 '$("windval").textContent = Math.round(st.wind_dir_deg)',
                 '$("windarrow").style.transform'):
        assert frag in block, frag
    # and no unguarded relabel remains anywhere else
    assert html.count('$("windval").textContent = Math.round(st.wind_dir_deg)') == 1
