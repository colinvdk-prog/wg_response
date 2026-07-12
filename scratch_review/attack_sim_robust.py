"""ADVERSARIAL VERIFIER — simulator attack #4: ROBUSTNESS.

Brief item 4:

  R1. Ignite outside the tile / on non-burnable fuel / garbage payloads ->
      clean 4xx with the engine's message, session keeps working, server
      never crashes (real HTTP server, real tiles; auth fail-closed checked
      for the /api/sim endpoints too).
  R2. LOAD: 200 clients + 12 crews + 3 simultaneous extreme fires on a real
      tile -> every tick completes < 5 s (default shipped config; 20 ticks;
      numbers written to out/verifier_sim_perf.json).
  R3. SPEED 600x must not skip safety audits: the server clock path keeps
      one ENGINE tick per cycle (sim_now advances exactly replan_period_s per
      tick at speed_x=600, violations checked every tick); the session-level
      coarse-jump path (set_speed(600) -> 6.25-day steps) also keeps the
      per-tick audit at 0 violations.
  R4. RESET actually resets: engine state equals a fresh session with the
      same seed (everything except cmd_log, which BY DESIGN keeps the full
      replay journal), and replaying the same script after reset is
      byte-equal to the fresh session's replay.
  R5. Two concurrent sessions don't share state: interleaved A/B command
      streams leave A byte-identical to a solo run of A's stream; a 4-thread
      step/state hammer loses no ticks and raises nothing.
  R6. Ticker-thread hygiene: 10 run sessions through a 4-slot LRU manager
      leave <= 4 live tickers; evicted sessions 404 and their threads die;
      stop_all() ends every ticker (no leak).

Run from repo root:
    py -3 -m pytest scratch_review/attack_sim_robust.py -v
"""
from __future__ import annotations

import http.client
import json
import math
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scratch_review.attack_sim_common import (  # noqa: E402
    engine_state_hash, pick_ignition, skip_reason, tile_present)
from wgresponse import geo, server  # noqa: E402
from wgresponse.fuels import is_burnable  # noqa: E402
from wgresponse.simui import LOCATIONS, SimSession  # noqa: E402
from wgresponse.server import SimSessionManager  # noqa: E402

KEY = "boulder_co"
API_KEY = "atk-key-1"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _need_tile(key=KEY):
    if not tile_present(key):
        pytest.skip(skip_reason(key))


# ---------------------------------------------------------------------------
# R1 — HTTP server: garbage in, clean 4xx out, nothing crashes
# ---------------------------------------------------------------------------

class _Http:
    def __init__(self, port):
        self.port = port

    def req(self, method, path, body=None, key=API_KEY, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            hdrs = {}
            if key is not None:
                hdrs["X-Internal-API-Key"] = key
            payload = raw
            if body is not None:
                payload = json.dumps(body).encode()
            if payload is not None:
                hdrs["Content-Type"] = "application/json"
            conn.request(method, path, body=payload, headers=hdrs)
            r = conn.getresponse()
            data = r.read()
            return r.status, (json.loads(data) if data else None)
        finally:
            conn.close()


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("WGRESP_API_KEY", API_KEY)
    monkeypatch.delenv("WGRESP_DEV_OPEN", raising=False)
    srv = server.serve(0)                      # REAL tiles (default dir)
    th = threading.Thread(target=lambda: srv.serve_forever(0.05), daemon=True)
    th.start()
    yield _Http(srv.server_address[1])
    srv.app.sim_manager.stop_all()
    srv.shutdown()
    th.join(timeout=5)
    srv.server_close()


def _non_burnable_point(key):
    """Center of the first non-burnable (urban/water) cell — a click the
    engine must reject loudly."""
    from wgresponse.simui import load_tile
    tile, synth = load_tile(key)
    assert not synth
    spec = tile.spec
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            code = int(round(tile.fuel.get(r, c)))
            if code > 0 and not is_burnable(code):
                return geo.rc_to_latlon(spec, r, c)
    raise AssertionError("tile has no non-burnable cell?!")


def test_http_bad_inputs_clean_4xx(live):
    _need_tile()
    # auth fail-closed on the sim API
    st, _ = live.req("POST", "/api/sim/new", body={"location": KEY,
                                                   "clients": 5, "crews": 1},
                     key=None)
    assert st == 401
    st, _ = live.req("GET", "/api/sim/sim-1/state", key="wrong")
    assert st == 401

    # bad session creations
    for body, want in (
            ({"location": "narnia", "clients": 5, "crews": 1}, 400),
            ({"location": KEY, "clients": 0, "crews": 1}, 400),
            ({"location": KEY, "clients": 201, "crews": 1}, 400),
            ({"location": KEY, "clients": 5, "crews": 13}, 400),
            ({"location": KEY, "clients": "x", "crews": 1}, 400),
            ({"location": KEY, "clients": 5, "crews": 1,
              "cfg": {"no_such_field": 1}}, 400),
            ({"location": KEY, "clients": 5, "crews": 1,
              "cfg": {"egress_buffer_min": -5}}, 400),
            (["not", "an", "object"], 400)):
        st, out = live.req("POST", "/api/sim/new", body=body)
        assert st == want, (body, st, out)
        assert "error" in out

    # malformed JSON + empty body
    st, out = live.req("POST", "/api/sim/new", raw=b"{{{nope")
    assert st == 400 and "malformed" in out["error"]
    st, out = live.req("POST", "/api/sim/new", raw=None)
    assert st == 400

    # a good session
    st, out = live.req("POST", "/api/sim/new",
                       body={"location": KEY, "clients": 5, "crews": 1,
                             "seed": "atk-robust"})
    assert st == 200, out
    sid = out["session_id"]

    # ignite OUTSIDE the tile -> clean 400 with the engine's message
    st, out = live.req("POST", f"/api/sim/{sid}/ignite",
                       body={"lat": 0.0, "lon": 0.0})
    assert st == 400 and "outside" in out["error"], out
    # NaN / non-numeric / missing coordinates
    st, out = live.req("POST", f"/api/sim/{sid}/ignite",
                       raw=b'{"lat": NaN, "lon": 1}')
    assert st == 400
    st, out = live.req("POST", f"/api/sim/{sid}/ignite",
                       body={"lat": "x", "lon": 1})
    assert st == 400
    st, out = live.req("POST", f"/api/sim/{sid}/ignite", body={"lat": 39.9})
    assert st == 400
    # non-burnable click -> loud rejection, engine's own hint
    nb = _non_burnable_point(KEY)
    st, out = live.req("POST", f"/api/sim/{sid}/ignite",
                       body={"lat": nb.lat, "lon": nb.lon})
    assert st == 400 and "non-burnable" in out["error"], out
    # bad intensity / bad wind
    st, out = live.req("POST", f"/api/sim/{sid}/ignite",
                       body={"lat": nb.lat, "lon": nb.lon,
                             "intensity": "apocalyptic"})
    assert st == 400
    st, out = live.req("POST", f"/api/sim/{sid}/ignite",
                       body={"lat": nb.lat, "lon": nb.lon,
                             "wind_dir_deg": "north"})
    assert st == 400

    # bad config / ctl
    st, out = live.req("POST", f"/api/sim/{sid}/config", body={"speed": 601})
    assert st == 400
    st, out = live.req("POST", f"/api/sim/{sid}/config", body={"speed": 0})
    assert st == 400
    st, out = live.req("POST", f"/api/sim/{sid}/config",
                       body={"egress_buffer_min": -1})
    assert st == 400
    st, out = live.req("POST", f"/api/sim/{sid}/config", body={"bogus": 1})
    assert st == 400
    st, out = live.req("POST", f"/api/sim/{sid}/ctl", body={"action": "warp"})
    assert st == 400
    st, out = live.req("POST", "/api/sim/no-such/ctl", body={"action": "step"})
    assert st == 404
    st, out = live.req("GET", "/api/sim/no-such/state")
    assert st == 404

    # after all that abuse the session still works: ignite + 2 steps clean
    s_probe = SimSession(KEY, 5, 1, seed="atk-robust")
    ign, _ = pick_ignition(s_probe, KEY)
    st, out = live.req("POST", f"/api/sim/{sid}/ignite",
                       body={"lat": ign.lat, "lon": ign.lon,
                             "intensity": "extreme"})
    assert st == 200 and out["incident_id"] == "sim-f1"
    for _ in range(2):
        st, out = live.req("POST", f"/api/sim/{sid}/ctl", body={"action": "step"})
        assert st == 200
        assert out["state"]["scoreboard"]["violations"] == 0
    assert out["state"]["tick"] == 2


# ---------------------------------------------------------------------------
# R2 — load: 200 clients, 12 crews, 3 extreme fires, tick < 5 s
# ---------------------------------------------------------------------------

def _three_far_ignitions(s, key, min_sep_m=3000.0):
    """Three deep-fast-fuel ignition points, pairwise >= min_sep_m apart:
    the cluster pick plus scans anchored at other clients."""
    pts = []
    for anchor in (0, len(s._clients) // 2, len(s._clients) - 1):
        # rotate client order by re-picking with different min ranges
        for min_m, max_m in ((500, 2500), (2500, 4500), (4500, 7000)):
            try:
                p, _ = pick_ignition(s, key, min_m=min_m, max_m=max_m,
                                     cluster=(anchor == 0))
            except AssertionError:
                continue
            if all(geo.haversine_m(p, q) >= min_sep_m for q in pts):
                pts.append(p)
                break
        if len(pts) == 3:
            break
    # last resort: relax separation
    if len(pts) < 3:
        for min_m in range(500, 8000, 500):
            try:
                p, _ = pick_ignition(s, key, min_m=min_m, max_m=min_m + 400)
            except AssertionError:
                continue
            if all(geo.haversine_m(p, q) >= 1000.0 for q in pts):
                pts.append(p)
            if len(pts) == 3:
                break
    assert len(pts) == 3, f"could only stage {len(pts)} separated ignitions"
    return pts


def test_load_200_clients_12_crews_3_extreme_fires_tick_under_5s():
    _need_tile()
    s = SimSession(KEY, 200, 12, seed="atk-load")      # default shipped cfg
    pts = _three_far_ignitions(s, KEY)
    fids = [s.ignite(p.lat, p.lon, intensity="extreme") for p in pts]
    assert fids == ["sim-f1", "sim-f2", "sim-f3"]
    times = []
    for k in range(20):
        t0 = time.perf_counter()
        st = s.tick()
        dt = time.perf_counter() - t0
        times.append(dt)
        assert st["scoreboard"]["violations"] == 0
        assert dt < 5.0, (
            f"LOAD BAR MISSED: tick {k + 1} took {dt:.2f}s >= 5s "
            f"(200 clients / 12 crews / 3 extreme fires, default config)")
    payload = json.dumps(s.state(), allow_nan=False)
    out = {"ticks": len(times), "max_s": round(max(times), 3),
           "mean_s": round(sum(times) / len(times), 3),
           "state_bytes": len(payload),
           "fires": len(fids), "clients": 200, "crews": 12}
    with open(os.path.join(REPO, "out", "verifier_sim_perf.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    sys.stderr.write(f"\n[load] {out}\n")


# ---------------------------------------------------------------------------
# R3 — 600x never skips audits
# ---------------------------------------------------------------------------

def test_speed_600x_server_clock_keeps_engine_cadence():
    _need_tile()
    mgr = SimSessionManager()
    try:
        code, out = mgr.new_session({"location": KEY, "clients": 12,
                                     "crews": 2, "seed": "atk-600"})
        assert code == 200
        sid = out["session_id"]
        probe = SimSession(KEY, 12, 2, seed="atk-600")
        ign, _ = pick_ignition(probe, KEY, cluster=True)
        code, out = mgr.ignite(sid, {"lat": ign.lat, "lon": ign.lon,
                                     "intensity": "extreme"})
        assert code == 200
        code, out = mgr.config(sid, {"speed": 600.0})
        assert code == 200
        st = out["state"]
        assert st["speed_x"] == 600.0
        assert st["tick_interval_s"] == pytest.approx(1.5)
        # the ENGINE step multiplier must stay 1.0 — no coarse jumps
        assert st["speed"] == 1.0
        period = 900.0
        last_now = st["sim_now_s"]
        last_tick = st["tick"]
        for _ in range(25):
            code, out = mgr.ctl(sid, {"action": "step"})
            assert code == 200
            st = out["state"]
            assert st["tick"] == last_tick + 1, "a tick was skipped"
            assert st["sim_now_s"] == pytest.approx(last_now + period), (
                "engine advanced more than one replan period per tick at "
                "600x — audit cadence broken")
            assert st["scoreboard"]["violations"] == 0
            last_now = st["sim_now_s"]
            last_tick = st["tick"]
    finally:
        mgr.stop_all()


def test_speed_600x_session_coarse_jump_audit_still_clean():
    """Direct SimSession.set_speed(600): 6.25-day steps. The audit must still
    run each tick and stay at 0 violations even as the fire sweeps every
    serviced cell within a single step."""
    _need_tile()
    s = SimSession(KEY, 20, 3, seed="atk-jump")
    ign, _ = pick_ignition(s, KEY, cluster=True)
    s.ignite(ign.lat, ign.lon, intensity="extreme")
    st = s.tick()                       # one fine tick: adopt plans
    assert st["scoreboard"]["violations"] == 0
    s.set_speed(600.0)
    for _ in range(8):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, st["audit"]
        for a in st["audit"]:
            assert a["violation"] is False
    assert st["sim_now_s"] - st["t0_s"] >= 8 * 600.0 * 900.0


# ---------------------------------------------------------------------------
# R4 — reset really resets
# ---------------------------------------------------------------------------

def _script(s, ign):
    hashes = []
    s.ignite(ign.lat, ign.lon, intensity="extreme")
    for _ in range(3):
        hashes.append(engine_state_hash(s.tick()))
    s.set_buffer(60.0)
    hashes.append(engine_state_hash(s.tick()))
    s.set_speed(4.0)
    for _ in range(2):
        hashes.append(engine_state_hash(s.tick()))
    return hashes


def test_reset_equals_fresh_session_and_replays_identically():
    _need_tile()
    probe = SimSession(KEY, 16, 2, seed="atk-reset")
    ign, _ = pick_ignition(probe, KEY, cluster=True)

    a = SimSession(KEY, 16, 2, seed="atk-reset")
    _script(a, ign)
    a.reset()
    fresh = SimSession(KEY, 16, 2, seed="atk-reset")

    st_a = a.state()
    st_f = fresh.state()
    # engine state must match a fresh session exactly (cmd_log excluded: it
    # is BY DESIGN the cumulative replay journal and now ends with 'reset')
    assert engine_state_hash(st_a) == engine_state_hash(st_f), (
        "reset state differs from a fresh session beyond cmd_log")
    assert st_a["fires"] == [] and st_a["tick"] == 0
    assert st_a["sim_now_s"] == st_a["t0_s"]
    assert st_a["speed"] == 1.0 and st_a["paused"] is False
    assert st_a["scoreboard"]["served"] == 0
    assert st_a["scoreboard"]["buffer_min"] == 30.0, (
        "slider change survived reset (must return to post-configure cfg)")
    assert st_a["cmd_log"][-1]["cmd"] == "reset"

    # replay the identical script on both: byte-equal timelines
    ha = _script(a, ign)
    hf = _script(fresh, ign)
    assert ha == hf, "post-reset replay diverges from a fresh session"


def test_server_ctl_reset_pauses_and_zeroes():
    _need_tile()
    mgr = SimSessionManager()
    try:
        code, out = mgr.new_session({"location": KEY, "clients": 8,
                                     "crews": 1, "seed": "atk-ctl-reset"})
        sid = out["session_id"]
        probe = SimSession(KEY, 8, 1, seed="atk-ctl-reset")
        ign, _ = pick_ignition(probe, KEY, cluster=True)
        mgr.ignite(sid, {"lat": ign.lat, "lon": ign.lon})
        mgr.ctl(sid, {"action": "run"})
        code, out = mgr.ctl(sid, {"action": "reset"})
        assert code == 200
        st = out["state"]
        assert st["tick"] == 0 and st["fires"] == []
        assert st["running"] is False, "reset must leave the session paused"
    finally:
        mgr.stop_all()


# ---------------------------------------------------------------------------
# R5 — concurrent sessions are isolated
# ---------------------------------------------------------------------------

def _drive_a(mgr, sid, ign):
    """A's fixed command stream via the manager; returns engine hashes."""
    hashes = []
    code, out = mgr.ignite(sid, {"lat": ign.lat, "lon": ign.lon,
                                 "intensity": "extreme"})
    assert code == 200
    hashes.append(engine_state_hash(out["state"], drop=()))
    for _ in range(4):
        code, out = mgr.ctl(sid, {"action": "step"})
        assert code == 200
        hashes.append(engine_state_hash(out["state"], drop=()))
    code, out = mgr.config(sid, {"egress_buffer_min": 75.0})
    assert code == 200
    code, out = mgr.ctl(sid, {"action": "step"})
    hashes.append(engine_state_hash(out["state"], drop=()))
    return hashes


def test_two_sessions_do_not_share_state():
    _need_tile()
    if not tile_present("santa_rosa_ca"):
        pytest.skip(skip_reason("santa_rosa_ca"))
    probe = SimSession(KEY, 10, 2, seed="atk-iso")
    ign_a, _ = pick_ignition(probe, KEY, cluster=True)
    probe_b = SimSession("santa_rosa_ca", 12, 1, seed="atk-iso-b")
    ign_b, _ = pick_ignition(probe_b, "santa_rosa_ca", cluster=True)

    # solo baseline: A alone
    solo = SimSessionManager()
    try:
        _, out = solo.new_session({"location": KEY, "clients": 10,
                                   "crews": 2, "seed": "atk-iso"})
        base = _drive_a(solo, out["session_id"], ign_a)
    finally:
        solo.stop_all()

    # interleaved: B commands woven between every A command
    mgr = SimSessionManager()
    try:
        _, oa = mgr.new_session({"location": KEY, "clients": 10,
                                 "crews": 2, "seed": "atk-iso"})
        sa = oa["session_id"]
        _, ob = mgr.new_session({"location": "santa_rosa_ca", "clients": 12,
                                 "crews": 1, "seed": "atk-iso-b"})
        sb = ob["session_id"]
        mgr.ignite(sb, {"lat": ign_b.lat, "lon": ign_b.lon,
                        "intensity": "high"})
        hashes = []
        code, out = mgr.ignite(sa, {"lat": ign_a.lat, "lon": ign_a.lon,
                                    "intensity": "extreme"})
        hashes.append(engine_state_hash(out["state"], drop=()))
        for _ in range(4):
            mgr.ctl(sb, {"action": "step"})
            code, out = mgr.ctl(sa, {"action": "step"})
            hashes.append(engine_state_hash(out["state"], drop=()))
            mgr.config(sb, {"slack_min": 5.0})
        mgr.config(sa, {"egress_buffer_min": 75.0})
        mgr.ctl(sb, {"action": "step"})
        code, out = mgr.ctl(sa, {"action": "step"})
        hashes.append(engine_state_hash(out["state"], drop=()))

        assert hashes == base, (
            "SESSION CROSS-CONTAMINATION: A's timeline changed because B "
            "was driven between A's commands")
        # B stayed its own session
        _, stb = mgr.state(sb)
        assert stb["location"] == "santa_rosa_ca"
        assert stb["tick"] == 5
        cmds = [c["cmd"] for c in stb["cmd_log"]]
        assert cmds.count("set_slack") == 4 and "set_buffer" not in cmds
    finally:
        mgr.stop_all()


def test_thread_hammer_no_lost_ticks():
    _need_tile()
    mgr = SimSessionManager()
    try:
        sids = []
        for k in range(2):
            _, out = mgr.new_session({"location": KEY, "clients": 6,
                                      "crews": 1, "seed": f"atk-hammer-{k}"})
            sids.append(out["session_id"])
        errors = []
        STEPS = 30

        def stepper(sid):
            try:
                for _ in range(STEPS):
                    code, out = mgr.ctl(sid, {"action": "step"})
                    assert code == 200
            except Exception as e:      # noqa: BLE001
                errors.append(e)

        def poller(sid):
            try:
                for _ in range(60):
                    code, out = mgr.state(sid)
                    assert code == 200
                    json.dumps(out, allow_nan=False)
            except Exception as e:      # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=stepper, args=(sids[0],)),
                   threading.Thread(target=stepper, args=(sids[1],)),
                   threading.Thread(target=poller, args=(sids[0],)),
                   threading.Thread(target=poller, args=(sids[1],))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        assert not errors, errors
        for sid in sids:
            _, st = mgr.state(sid)
            assert st["tick"] == STEPS, (
                f"{sid}: {st['tick']} ticks != {STEPS} steps issued "
                f"(lost/duplicated under concurrency)")
    finally:
        mgr.stop_all()


# ---------------------------------------------------------------------------
# R6 — ticker-thread hygiene over 10 sessions (LRU 4)
# ---------------------------------------------------------------------------

def _live_tickers():
    return [t for t in threading.enumerate()
            if t.name.startswith("simui-ticker-") and t.is_alive()]


def _wait_until(pred, timeout_s=10.0, step_s=0.1):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(step_s)
    return pred()


def test_no_ticker_thread_leak_over_10_sessions():
    _need_tile()
    assert not _live_tickers(), "pre-existing ticker threads?"
    mgr = SimSessionManager()
    try:
        sids = []
        for k in range(10):
            code, out = mgr.new_session({"location": KEY, "clients": 5,
                                         "crews": 1, "seed": f"atk-leak-{k}"})
            assert code == 200
            sid = out["session_id"]
            sids.append(sid)
            code, out = mgr.ctl(sid, {"action": "run"})
            assert code == 200
            assert out["state"]["running"] is True
        # LRU keeps 4; evicted sessions must 404 and their tickers must die
        assert mgr.max_sessions == 4
        for sid in sids[:6]:
            code, _ = mgr.state(sid)
            assert code == 404, f"evicted session {sid} still answers"
        assert _wait_until(lambda: len(_live_tickers()) <= 4), (
            f"TICKER LEAK: {len(_live_tickers())} live ticker threads for 4 "
            f"resident sessions: {[t.name for t in _live_tickers()]}")
        survivors = {t.name for t in _live_tickers()}
        assert survivors <= {f"simui-ticker-{sid}" for sid in sids[6:]}, (
            f"evicted sessions' tickers survived: {survivors}")
    finally:
        mgr.stop_all()
    assert _wait_until(lambda: not _live_tickers()), (
        f"stop_all left tickers alive: {[t.name for t in _live_tickers()]}")
