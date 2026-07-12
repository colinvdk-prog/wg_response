"""attack_r3_scrub.py — FOUNDER ROUND 3 law 1 (TIME SCRUB / HISTORY), the
byte-identity replay law, driven END-TO-END through the real HTTP server on an
EPHEMERAL port (never the user's :8878).

Attacks:
  1. Drive N ticks over HTTP (POST /ctl step), capturing each LIVE frame
     (GET /state). Then GET /state?tick=k for EVERY k and assert the recorded
     snapshot is BYTE-IDENTICAL to the frame served live at tick k, modulo the
     documented live-only server keys (session_id/running/speed_x/
     tick_interval_s). A scrubbed frame must carry NONE of those keys and its
     own history.current_tick == k.
  2. 404 surface: tick 0 (never recorded), future tick, fractional/non-numeric/
     boolean tick, and evicted-past-history_ticks all 404.
  3. goto is READ-ONLY: after a scrub the live clock/tick is unchanged (the
     server never advanced time to serve a past frame).
  4. Ring eviction: with a small history_ticks the window slides and old ticks
     404 while first_tick advances exactly as the algebra predicts.
  5. Determinism: two fresh sessions with the same script are byte-identical per
     tick; reset() clears history (tick 1 -> 404) and a re-run replays
     byte-identically (modulo cmd_log, which legitimately records the reset).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from attack_r3_common import (                                  # noqa: E402
    SimServer, TILE_DIR, canon, make_session, tile_present, skip_loud,
    LIVE_ONLY_KEYS,
)

SCRUB_KEY = "malibu_ca"
SCRUB_CFG = {"ensemble_n": 3, "grid_cell_m": 120.0, "horizon_h": 48.0}


def _twin_ignition(key, n_clients, n_crews, seed, cfg, fire_zone="standard"):
    """A burnable upwind ignition point for the SERVER's session, found on a
    byte-identical LOCAL twin (same seed+tile => same placement)."""
    from attack_sim_common import pick_ignition
    twin = make_session(key, n_clients, n_crews, seed=seed, cfg=cfg,
                        fire_zone=fire_zone)
    p, _t = pick_ignition(twin, key, cluster=False)
    return p.lat, p.lon


def _drive(srv, sid, n):
    """Step the session n times, returning the LIVE frame served after each
    step (index k in 1..n)."""
    frames = {}
    for k in range(1, n + 1):
        code, _ = srv.post(f"/api/sim/{sid}/ctl", {"action": "step"})
        assert code == 200, f"step {k} failed: {code}"
        code, live = srv.state(sid)
        assert code == 200
        assert live["tick"] == k and live["history"]["current_tick"] == k
        frames[k] = live
    return frames


# ===========================================================================
# 1. BYTE-IDENTITY end-to-end over HTTP
# ===========================================================================
def test_scrub_byte_identical_end_to_end():
    if not tile_present(SCRUB_KEY):
        pytest.skip(skip_loud(SCRUB_KEY, "data/tiles/*.wgt"))
    n = 24
    with SimServer(tile_dir=TILE_DIR) as srv:
        code, data = srv.new(SCRUB_KEY, 40, 4, seed="scrub1", cfg=SCRUB_CFG)
        assert code == 200, data
        sid = data["session_id"]
        lat, lon = _twin_ignition(SCRUB_KEY, 40, 4, "scrub1", SCRUB_CFG)
        code, _ = srv.post(f"/api/sim/{sid}/ignite",
                           {"lat": lat, "lon": lon, "intensity": "high"})
        assert code == 200

        live = _drive(srv, sid, n)

        # the run must be non-trivial (else byte-identity proves little)
        last = live[n]
        assert last["fires"], "no fire ignited"
        assert last["tick"] == n

        mismatches = []
        for k in range(1, n + 1):
            code, scrub = srv.state(sid, f"?tick={k}")
            assert code == 200, f"?tick={k} -> {code}"
            # a scrubbed frame carries NONE of the live-only server keys
            for lk in LIVE_ONLY_KEYS:
                assert lk not in scrub, \
                    f"tick {k}: scrub leaked live-only key {lk!r}"
            assert scrub["history"]["current_tick"] == k
            # THE LAW: byte-identical to the live frame (minus live-only keys)
            if canon(scrub) != canon(live[k]):
                mismatches.append(k)
        assert not mismatches, \
            f"ticks not byte-identical to their live frames: {mismatches}"


# ===========================================================================
# 2. 404 surface (non-int / future / zero / evicted)
# ===========================================================================
def test_scrub_404_surface():
    if not tile_present(SCRUB_KEY):
        pytest.skip(skip_loud(SCRUB_KEY, "data/tiles/*.wgt"))
    with SimServer(tile_dir=TILE_DIR) as srv:
        code, data = srv.new(SCRUB_KEY, 8, 2, seed="s404", cfg=SCRUB_CFG)
        sid = data["session_id"]
        _drive(srv, sid, 5)                       # ticks 1..5 recorded

        # tick 0 was never recorded (history starts at tick 1)
        assert srv.state(sid, "?tick=0")[0] == 404
        # future
        assert srv.state(sid, "?tick=6")[0] == 404
        assert srv.state(sid, "?tick=999")[0] == 404
        # non-integer / non-numeric / boolean-ish query strings -> 404
        for bad in ("1.5", "abc", "nan", "inf", "true", "2.0000001", "-1"):
            code, _ = srv.state(sid, f"?tick={bad}")
            assert code == 404, f"?tick={bad!r} should 404, got {code}"
        # COSMETIC (documented, not a finding): an EMPTY value `?tick=` is
        # dropped by parse_qs (keep_blank_values default False), so the server
        # sees NO tick param and returns the LIVE state (200) rather than a
        # scrub 404. Harmless — a real UI always sends `?tick=<int>`; empty is
        # 'absent', not a non-integer tick. Pinned here so it can't silently
        # change into something worse.
        assert srv.state(sid, "?tick=")[0] == 200
        # integral float form IS accepted
        assert srv.state(sid, "?tick=3.0")[0] == 200
        # in-window integers OK
        for k in range(1, 6):
            assert srv.state(sid, f"?tick={k}")[0] == 200
        # goto mirror endpoint: missing tick -> 400; bad tick -> 404
        assert srv.post(f"/api/sim/{sid}/ctl", {"action": "goto"})[0] == 400
        assert srv.post(f"/api/sim/{sid}/ctl",
                        {"action": "goto", "tick": "x"})[0] == 404
        assert srv.post(f"/api/sim/{sid}/ctl",
                        {"action": "goto", "tick": 99})[0] == 404
        assert srv.state("sim-nope", "?tick=1")[0] == 404   # unknown session


# ===========================================================================
# 3. goto / scrub is READ-ONLY: the live clock never advances
# ===========================================================================
def test_goto_is_read_only():
    if not tile_present(SCRUB_KEY):
        pytest.skip(skip_loud(SCRUB_KEY, "data/tiles/*.wgt"))
    with SimServer(tile_dir=TILE_DIR) as srv:
        code, data = srv.new(SCRUB_KEY, 8, 2, seed="sgoto", cfg=SCRUB_CFG)
        sid = data["session_id"]
        _drive(srv, sid, 6)
        code, before = srv.state(sid)
        # scrub back through goto AND ?tick several times
        for k in (1, 2, 3, 6):
            code, resp = srv.post(f"/api/sim/{sid}/ctl",
                                  {"action": "goto", "tick": k})
            assert code == 200 and resp["state"]["history"]["current_tick"] == k
            assert srv.state(sid, f"?tick={k}")[0] == 200
        code, after = srv.state(sid)
        # the live clock/tick is UNTOUCHED by all that scrubbing
        assert after["tick"] == before["tick"] == 6
        assert after["sim_now_s"] == before["sim_now_s"]
        assert canon(after) == canon(before), \
            "scrubbing mutated the live state"


# ===========================================================================
# 4. Ring eviction past history_ticks
# ===========================================================================
def test_history_ring_eviction():
    if not tile_present(SCRUB_KEY):
        pytest.skip(skip_loud(SCRUB_KEY, "data/tiles/*.wgt"))
    H = 5
    cfg = dict(SCRUB_CFG, history_ticks=H)
    with SimServer(tile_dir=TILE_DIR) as srv:
        code, data = srv.new(SCRUB_KEY, 8, 2, seed="sring", cfg=cfg)
        sid = data["session_id"]
        n = 12
        live = _drive(srv, sid, n)
        # only the last H ticks survive: [n-H+1 .. n]
        first = n - H + 1
        st = live[n]
        assert st["history"]["first_tick"] == first
        assert st["history"]["last_tick"] == n
        for k in range(1, first):
            assert srv.state(sid, f"?tick={k}")[0] == 404, \
                f"evicted tick {k} should 404"
        for k in range(first, n + 1):
            code, scrub = srv.state(sid, f"?tick={k}")
            assert code == 200, f"in-window tick {k} should 200"
            assert canon(scrub) == canon(live[k]), \
                f"evicted-window tick {k} not byte-identical"


# ===========================================================================
# 5. Determinism + reset replay
# ===========================================================================
def test_determinism_two_sessions_and_reset():
    if not tile_present(SCRUB_KEY):
        pytest.skip(skip_loud(SCRUB_KEY, "data/tiles/*.wgt"))
    n = 10
    lat, lon = _twin_ignition(SCRUB_KEY, 40, 4, "sdet", SCRUB_CFG)
    with SimServer(tile_dir=TILE_DIR) as srv:
        # --- two FRESH sessions, identical script -> byte-identical per tick ---
        code, a = srv.new(SCRUB_KEY, 40, 4, seed="sdet", cfg=SCRUB_CFG)
        sid_a = a["session_id"]
        srv.post(f"/api/sim/{sid_a}/ignite",
                 {"lat": lat, "lon": lon, "intensity": "high"})
        frames_a = _drive(srv, sid_a, n)

        code, b = srv.new(SCRUB_KEY, 40, 4, seed="sdet", cfg=SCRUB_CFG)
        sid_b = b["session_id"]
        srv.post(f"/api/sim/{sid_b}/ignite",
                 {"lat": lat, "lon": lon, "intensity": "high"})
        frames_b = _drive(srv, sid_b, n)

        for k in range(1, n + 1):
            # identical command scripts => identical cmd_log too: FULL identity
            assert canon(frames_a[k]) == canon(frames_b[k]), \
                f"tick {k}: two fresh sessions diverged (nondeterminism)"

        # --- reset clears history, re-run replays identically ---
        code, _ = srv.post(f"/api/sim/{sid_a}/ctl", {"action": "reset"})
        assert code == 200
        assert srv.state(sid_a, "?tick=1")[0] == 404, \
            "reset did not clear history"
        code, post = srv.state(sid_a)
        assert post["tick"] == 0 and post["fires"] == [], \
            "reset did not re-arm the scenario"
        # re-run the SAME script
        srv.post(f"/api/sim/{sid_a}/ignite",
                 {"lat": lat, "lon": lon, "intensity": "high"})
        frames_a2 = _drive(srv, sid_a, n)
        for k in range(1, n + 1):
            # cmd_log legitimately differs (records the reset), so compare the
            # ENGINE state modulo cmd_log
            assert canon(frames_a2[k], drop=("cmd_log",)) == \
                   canon(frames_a[k], drop=("cmd_log",)), \
                f"tick {k}: reset re-run diverged from the original run"
