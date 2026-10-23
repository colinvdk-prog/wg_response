"""AFTERNOON STARTS (2026-10-21; the Eagle River valley review's iteration 3 —
the propagation review's R2; docs/SIMULATOR_DESIGN.md "Far-side refuges, ember
ETAs and afternoon starts").

Every session began at the fixed epoch T0_S — 07:13 local standard time in
Colorado — so every valley fire started in the morning and burned into the
afternoon. SimSession(start_local_hour=H) and POST /api/sim/new
{"start_local_hour": H} move the epoch to H (decimal local standard hours) on
the epoch's own local day; weather is a pure function of absolute time, so a
fire lit at the start burns into that hour's weather. None (the default) is
the fixed epoch, byte-identical. The valley harness takes --start-hour H.

These tests pin the default (no change), the epoch arithmetic per location
(the city profile's offset, else the solar zone), the validation, the command
log, reset, the server payload and the harness option."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys

import pytest

from wgresponse import server, simui


def _hash(st):
    st = dict(st)
    st.pop("cmd_log", None)
    return hashlib.sha256(json.dumps(st, sort_keys=True, allow_nan=False)
                          .encode()).hexdigest()[:16]


def _local(s):
    return ((s._t0_s / 3600.0 + s._utc_offset_h()) % 24.0 + 24.0) % 24.0


def test_default_is_the_fixed_epoch_byte_identical():
    a = simui.SimSession("vail_co", 6, 1, seed="as", roads_module=None)
    b = simui.SimSession("vail_co", 6, 1, seed="as", roads_module=None,
                         start_local_hour=None)
    assert a._t0_s == b._t0_s == simui.T0_S
    assert round(_local(a), 4) == 7.2222                  # 07:13:20 MST
    assert "start_local_hour" not in a.state()["cmd_log"][0]
    for s in (a, b):
        s.ignite(39.62, -106.40, intensity="high")
    for _ in range(3):
        assert _hash(a.tick()) == _hash(b.tick())


@pytest.mark.parametrize("key,offset", [("vail_co", -7.0),
                                        ("malibu_ca", -8.0)])
def test_the_epoch_moves_to_the_local_hour_of_the_same_day(key, offset):
    s = simui.SimSession(key, 4, 1, seed="as", roads_module=None,
                         start_local_hour=13.22)
    assert s._utc_offset_h() == offset
    assert _local(s) == pytest.approx(13.22, abs=1e-9)
    assert abs(s._t0_s - simui.T0_S) < 24 * 3600.0
    st = s.state()
    assert st["t0_s"] == s._t0_s == st["sim_now_s"]
    assert st["cmd_log"][0]["start_local_hour"] == 13.22
    s2 = simui.SimSession(key, 4, 1, seed="as", roads_module=None,
                          start_local_hour=5.0)
    assert s2._t0_s < simui.T0_S                       # earlier, same day
    assert _local(s2) == pytest.approx(5.0, abs=1e-9)


def test_reset_and_reroll_keep_the_start_hour():
    s = simui.SimSession("vail_co", 4, 1, seed="as", roads_module=None,
                         start_local_hour=13.22)
    t0 = s._t0_s
    s.ignite(39.62, -106.40, intensity="high")
    s.tick()
    s.reset()
    assert s._t0_s == s._now_s == t0
    s.configure("vail_co", 4, 1, "as#r1")
    assert s._t0_s == t0


@pytest.mark.parametrize("bad", [24.0, -0.5, float("nan"), "1pm"])
def test_validation(bad):
    with pytest.raises(ValueError):
        simui.SimSession("vail_co", 4, 1, seed="as", roads_module=None,
                         start_local_hour=bad)


def test_city_weather_burns_into_the_afternoon():
    """vail_westerly at moderate (no wind event: the daily cycle alone): lit
    at 13:13 the fire starts near the afternoon peak, lit at 07:13 in the
    morning calm — same intensity, different weather at ignition."""
    mgr = server.SimSessionManager()
    out = {}
    for h in (None, 13.22):
        payload = {"scenario": "vail_westerly"}
        if h is not None:
            payload["start_local_hour"] = h
        code, res = mgr.new_session(payload)
        assert code == 200, res
        s = mgr._entry(res["session_id"]).session
        f = s._fires["sim-f1"]
        s.reset()
        s.ignite(f.origin.lat, f.origin.lon, intensity="moderate",
                 wind_dir_deg=f.wind_dir_deg)
        out[h] = s.state()["weather"]["now"]
    assert out[None]["local_hour"] == pytest.approx(7.22, abs=0.01)
    assert out[13.22]["local_hour"] == pytest.approx(13.22, abs=0.01)
    assert out[13.22]["wind_mph"] > out[None]["wind_mph"]
    assert out[13.22]["rh_pct"] < out[None]["rh_pct"]


def test_server_payload():
    mgr = server.SimSessionManager()
    for bad in (24, -1, "x", True, float("inf")):
        code, out = mgr.new_session({"scenario": "vail_westerly",
                                     "start_local_hour": bad})
        assert code == 400 and "start_local_hour" in out["error"]
    code, out = mgr.new_session({"scenario": "vail_westerly",
                                 "start_local_hour": 13.22})
    assert code == 200
    entry = mgr._entry(out["session_id"])
    assert entry.args["start_local_hour"] == 13.22
    assert out["state"]["weather"]["now"]["local_hour"] == pytest.approx(
        13.22, abs=0.01)
    code, out = mgr.new_session({"scenario": "vail_westerly"})
    assert "start_local_hour" not in mgr._entry(out["session_id"]).args
    assert out["state"]["t0_s"] == simui.T0_S


def test_the_valley_harness_takes_start_hour():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, "..", "scratch_review",
                                    "eagle_valley"))
    try:
        import run_matrix as rm
    finally:
        sys.path.pop(0)
    _mgr, s = rm.build("vail_co", "canonical", "high", None, 13.22)
    assert _local(s) == pytest.approx(13.22, abs=1e-9)
    _mgr, s = rm.build("vail_co", "canonical", "high", None)
    assert s._t0_s == simui.T0_S
    assert not math.isnan(s._t0_s)
