"""Diagnose tests/test_simui_policy.py::test_speed_600x_coarse_jumps_audit_
still_clean after the progress-preserving re-anchor + coarse-jump
sub-stepping: at which internal step is a crew seen on BURNED fuel, what is
it doing, how old is the burn under it, and where is it heading?
Usage: py -3 scratch_review/coarse_jump_diag.py
"""
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_simui_policy import _session  # noqa: E402
from wgresponse import geo, simui  # noqa: E402

tmp = pathlib.Path(tempfile.mkdtemp())
s = _session(tmp, n_clients=8, n_crews=2)
st = s.tick()
print("fine tick 1: violations", st["scoreboard"]["violations"])
s.set_speed(600.0)
orig = s._tick_step
state = {"sub": 0, "tick": 0}


def traced(step, *, emit=True, count=True):
    out = orig(step, emit=emit, count=count)
    state["sub"] += 1
    now = s._now_s
    for o in s._crew_overruns:
        cs = s._crew_sims[o["crew_id"]]
        leg = getattr(cs, "_leg", None) or {}
        tgt = leg.get("target")
        nom = o.get("nominal_arrival_s")
        age = None if nom is None else round((now - nom) / 60.0, 1)
        print(f"  tick {state['tick']} sub {state['sub']:3d} t+{(now - s._t0_s if hasattr(s, '_t0_s') else 0) / 3600.0:7.1f}h "
              f"{o['crew_id']} {o['reason']:8s} mode={o['mode']:8s} status={o['status']:10s} "
              f"burn age {age} min  leg={leg.get('kind')} "
              f"target={None if tgt is None else (round(tgt.lat, 4), round(tgt.lon, 4))} "
              f"pos=({o['lat']:.4f},{o['lon']:.4f})")
    return out


s._tick_step = traced
for t in range(6):
    state["tick"] = t + 1
    state["sub"] = 0
    st = s.tick()
    rows = st["crew_overruns"]
    print(f"TICK {t + 1}: violations {st['scoreboard']['violations']}, rows "
          f"{[(o['crew_id'], o['reason'], o['mode']) for o in rows]}, relocations "
          f"{st['scoreboard'].get('relocations')}")
    zones = {z.zone_id: z for z in simui.LOCATIONS['boulder_co'].safe_zones}
