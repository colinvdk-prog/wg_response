"""scratch_review/escape_roads_refuge_check.py — why does bench scenario
paradise_ca|+0|8000|high serve 5 fewer homes with the road-network escape
under CHORD dispatch (docs/review/escape_roads_2026-09-26.md §4.3)?

Runs the scenario in both arms to tick T (default 14), takes sim-g3's
position in each (on: its road refuge up the ridge; off: where the old arm's
crew is working), and asks the corridor law (safety.LegOracle on the straight
chord, as chord dispatch does) whether the homes the old arm's crew went on to
serve (c30, c1, c16) can still be reached safely from each.

Usage: py -3 scratch_review/escape_roads_refuge_check.py [T]
"""
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scratch_review.escape_roads_scenario import run  # noqa: E402
from wgresponse import geo, safety  # noqa: E402

seed = "paradise_ca|+0|8000|high"
T = int(sys.argv[1]) if len(sys.argv) > 1 else 14
s_off, *_ = run(seed, {"escape_road_network": False}, T)
s_on, *_ = run(seed, {}, T)
p_off = s_off._crew_sims["sim-g3"].pos
p_on = s_on._crew_sims["sim-g3"].pos
print("t", T, "sim-g3 off at", p_off, "mode", s_off._crew_sims["sim-g3"]._mode,
      "| on at", p_on, "mode", s_on._crew_sims["sim-g3"]._mode)
now = s_off._now_s
cfg = s_off._cfg
fire = next(iter(s_off._fires.values()))
er = s_off._eroded_p10(fire)
spd = s_off._crew_sims["sim-g3"].speed
by_id = {c.client_id: c for c in s_off._clients}
for cid in ("sim-c30", "sim-c1", "sim-c16"):
    cl = by_id[cid]
    for lab, p in (("off-pos", p_off), ("on-pos", p_on)):
        t_star = safety.LegOracle(p, cl.loc, spd, er, cfg).latest_safe_depart_s
        print(f"  {cid} from {lab}: chord {geo.haversine_m(p, cl.loc) / 1000.0:.1f} km, "
              f"latest safe depart "
              f"{'inf' if math.isinf(t_star) else round((t_star - now) / 60.0, 1)} min "
              f"from now -> {'OK' if t_star >= now else 'UNSAFE'}")
