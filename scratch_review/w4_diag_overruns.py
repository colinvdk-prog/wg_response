"""Diagnose the 3 sim tests that went 0 -> 1 violation after the 10c audit
counts imminent exposure in every mode (fix-program item 4) and the sim
armed corridor_fail_closed (item 1): which change moved them, and what the
exposed crew was doing."""
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_simui_policy import _checkerboard_dir, FAST_TIGHT, KEY  # noqa: E402
from tests.test_simui import FAST, _controlled_tile_dir, _upwind_ignition  # noqa: E402
from wgresponse import geo  # noqa: E402
from wgresponse.simui import LOCATIONS, SimSession  # noqa: E402


def _report(label, i, st):
    now = st["sim_now_s"]
    for o in st["crew_overruns"]:
        p10 = o["eroded_p10_s"]
        nom = o["nominal_arrival_s"]
        print(f"  {label} tick {i}: {o['crew_id']} {o['reason']} mode={o['mode']} "
              f"status={o['status']} p10-now={(p10 - now) / 60 if p10 else None} min "
              f"nom-now={(nom - now) / 60 if nom else None} min")
    if st["scoreboard"]["violations"]:
        print(f"  {label} tick {i}: violations={st['scoreboard']['violations']} "
              f"audit_rows={len(st['audit'])}")


def speed600(fail_closed):
    tmp = pathlib.Path(tempfile.mkdtemp())
    d = _checkerboard_dir(tmp)
    over = {**FAST_TIGHT, "dispatch_objective": "value"}
    if not fail_closed:
        over["corridor_fail_closed"] = False
    s = SimSession(KEY, 8, 2, seed="m1", cfg_overrides=over, tile_dir=d,
                   roads_module=None)
    loc = LOCATIONS[KEY]
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 8000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    label = f"speed600 fc={fail_closed}"
    st = s.tick()
    _report(label, 0, st)
    s.set_speed(600.0)
    for i in range(1, 7):
        st = s.tick()
        _report(label, i, st)
    print(label, "done; final violations", st["scoreboard"]["violations"])


def buffer_binds(fail_closed, buf):
    tmp = pathlib.Path(tempfile.mkdtemp())
    cfg = dict(FAST)
    cfg["egress_buffer_min"] = buf
    cfg["circuity"] = 1.35
    cfg["route_margin_min"] = 60.0
    if not fail_closed:
        cfg["corridor_fail_closed"] = False
    d = _controlled_tile_dir(tmp, "boulder_co", hamlet_half=3)
    s = SimSession("boulder_co", 16, 1, seed="m1", cfg_overrides=cfg,
                   tile_dir=d, roads_module=None)
    ign = _upwind_ignition("boulder_co", 6_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    s.set_speed(2.0)
    label = f"buffer{buf:g} fc={fail_closed}"
    for i in range(10):
        st = s.tick()
        _report(label, i, st)
    print(label, "done; final violations", st["scoreboard"]["violations"],
          "unserved", st["scoreboard"]["unserved_by_reason"])


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "speed"):
        speed600(True)
        speed600(False)
    if which in ("all", "buffer"):
        for buf in (30.0, 90.0):
            buffer_binds(True, buf)
            buffer_binds(False, buf)
