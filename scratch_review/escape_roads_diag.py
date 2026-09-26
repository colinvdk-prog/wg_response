"""scratch_review/escape_roads_diag.py — WHY does the road-snapped SEM-10d area
escape (contracts escape_road_snap_m) break the pinned Camp-Fire-scale
extended run (tests/test_simui.py::test_sem10d_extended_campfire_scale_no_
overrun_ever) when the raw straight-ray escape does not?

Re-runs that exact scenario (paradise_ca EXTENDED, 20 homes, 2 crews, HIGH
ignition 13.5 km up the hazard bearing, 30 min ticks, 120 ticks) for each
arm and records, READ-ONLY:
  * every _best_escape decision: the crew, its position, the chosen target,
    and every candidate the rule scored (catalog zones + the escape
    proposals — for the diagnosis BOTH the raw ray ends and the road-snapped
    nodes are scored, whichever the arm uses) with its route metrics over the
    crew's actual route seam: burned, transit exposure (s), worst margin,
    drive time, destination margin at arrival, and the destination's
    SAFETY HORIZON (eroded-P10 arrival at the target minus now);
  * every relocation the sweep issues (crew, tick, target);
  * every 10c crew-overrun row.
Instrumentation wraps methods and never changes arguments or results (the
extra scoring calls are pure reads of tick-frozen state).

Usage: py -3 scratch_review/escape_roads_diag.py [--arm off|snap|network ...]
Writes scratch_review/escape_roads/diag_<arm>.json and prints a digest.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wgresponse import geo, simui  # noqa: E402

OUT = ROOT / "scratch_review" / "escape_roads"

ARMS = {
    # the pre-2026-09-26 simulator default: straight rays (the pinned run:
    # 0 rows, 3 relocations). The network is pinned OFF explicitly — the
    # simulator arms it by default now.
    "off": {"escape_road_network": False, "escape_road_snap_m": 0.0},
    # the 2026-09-25 snap, armed (2 imminent-while-egressing rows)
    "snap": {"escape_road_network": False, "escape_road_snap_m": 1500.0},
    # the road-network escape (contracts escape_road_network), explicit
    "network": {"escape_road_network": True},
}


def _r(x, nd=1):
    if x is None:
        return None
    if isinstance(x, float) and math.isinf(x):
        return "inf"
    return round(x, nd)


def run(arm: str, ticks: int = 120, more: dict = None) -> dict:
    extra = dict(ARMS[arm], **(more or {}))
    key = "paradise_ca"
    loc = simui.LOCATIONS[key]
    cfg = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10,
           "deploy_min": 15.0}
    cfg.update(extra)
    try:
        s = simui.SimSession(key, 20, 2, seed="alerted|paradise_ca",
                             cfg_overrides=cfg, fire_zone="extended")
    except ValueError as e:              # knob not in this tree
        return {"arm": arm, "error": str(e)}
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 13_500.0)
    s.ignite(ign.lat, ign.lon, intensity="high",
             wind_dir_deg=loc.hazard_wind_dir_deg)
    s.set_speed(2.0)
    decisions = []
    relocs = []
    rows = []
    tick = {"n": 0}
    orig_best = simui.SimSession._best_escape
    orig_reloc = simui._SimCrew.relocate_to

    def score(sess, pos, now, speed, lead, tgt):
        b, e, w, t, dm = sess._route_metrics(pos, tgt, now, speed, lead)
        _nom, p10 = sess._crew_exposure(tgt)
        hz = (p10 - now) if not math.isinf(p10) else math.inf
        return {"burned": b, "expo_s": _r(e), "worst_s": _r(w), "drive_s": _r(t),
                "dm_s": _r(dm), "horizon_h": _r(hz / 3600.0 if not math.isinf(hz) else hz, 2),
                "dist_km": _r(geo.haversine_m(pos, tgt) / 1000.0, 2),
                "lat": round(tgt.lat, 5), "lon": round(tgt.lon, 5)}

    def best(self, pos, now, speed_mps, lead_s):
        out = orig_best(self, pos, now, speed_mps, lead_s)
        rec = {"tick": tick["n"], "pos": [round(pos.lat, 5), round(pos.lon, 5)],
               "chosen": out[1], "chosen_loc": [round(out[0].lat, 5), round(out[0].lon, 5)],
               "zones": [], "raw": [], "snapped": []}
        safe_zones, fc = self._currently_safe_zones(now, lead_s=lead_s)
        rec["fail_closed"] = fc
        for z in sorted(self._loc.safe_zones, key=lambda z: z.zone_id):
            d = score(self, pos, now, speed_mps, lead_s, z.loc)
            d["id"] = z.zone_id
            rec["zones"].append(d)
        keep = self._cfg.escape_road_snap_m
        try:
            for snap_m, lab in ((0.0, "raw"), (1500.0, "snapped")):
                self._cfg.escape_road_snap_m = snap_m
                for bearing, tgt in self._escape_ray_targets(pos, now, speed_mps):
                    d = score(self, pos, now, speed_mps, lead_s, tgt)
                    d["bearing"] = bearing
                    rec[lab].append(d)
        finally:
            self._cfg.escape_road_snap_m = keep
        rec["chosen_metrics"] = score(self, pos, now, speed_mps, lead_s, out[0])
        decisions.append(rec)
        return out

    def reloc(self, zone_loc, zone_zone_id, *a, **k):
        relocs.append({"tick": tick["n"], "crew": self.crew.crew_id,
                       "from": [round(self.pos.lat, 5), round(self.pos.lon, 5)],
                       "target": zone_zone_id,
                       "target_loc": [round(zone_loc.lat, 5), round(zone_loc.lon, 5)]})
        return orig_reloc(self, zone_loc, zone_zone_id, *a, **k)

    simui.SimSession._best_escape = best
    simui._SimCrew.relocate_to = reloc
    per_tick = []
    try:
        for t in range(ticks):
            tick["n"] = t
            st = s.tick()
            now = st["sim_now_s"]
            crews = []
            for c in st["crews"]:
                cs = s._crew_sims[c["crew_id"]]
                nom, p10 = s._crew_exposure(cs.pos)
                crews.append({"crew": c["crew_id"], "mode": cs._mode,
                              "status": c["status"],
                              "target": cs.egress_zone_id,
                              "reloc": s._relocating.get(c["crew_id"]),
                              "pos": [round(cs.pos.lat, 5), round(cs.pos.lon, 5)],
                              "p10_min_ahead": _r((p10 - now) / 60.0) if not math.isinf(p10) else "inf",
                              "nom_min_ahead": _r((nom - now) / 60.0) if not math.isinf(nom) else "inf"})
            per_tick.append({"tick": t, "crews": crews})
            for o in st.get("crew_overruns", []):
                p10 = o.get("eroded_p10_s")
                nom = o.get("nominal_arrival_s")
                rows.append({"tick": t, "crew": o["crew_id"], "reason": o["reason"],
                             "mode": o["mode"],
                             "min_past_p10": None if p10 is None else _r((now - p10) / 60.0),
                             "min_since_front": None if nom is None else _r((now - nom) / 60.0),
                             "lat": o["lat"], "lon": o["lon"]})
    finally:
        simui.SimSession._best_escape = orig_best
        simui._SimCrew.relocate_to = orig_reloc
    sb = st["scoreboard"]
    return {"arm": arm, "cfg": extra, "served": sb.get("served"),
            "relocations": sb.get("relocations"), "rows": rows,
            "relocs": relocs, "decisions": decisions, "per_tick": per_tick}


def digest(res: dict) -> None:
    if res.get("error"):
        print(f"=== {res['arm']}: {res['error']}")
        return
    print(f"\n=== arm {res['arm']} {res['cfg']}: rows {len(res['rows'])}, "
          f"served {res['served']}, relocations {res['relocations']}")
    for r in res["rows"]:
        print(f"  ROW t{r['tick']} {r['crew']} {r['reason']}/{r['mode']} "
              f"past-P10 {r['min_past_p10']} min, since-front {r['min_since_front']}")
    for r in res["relocs"]:
        print(f"  reloc t{r['tick']} {r['crew']} -> {r['target']} {r['target_loc']} from {r['from']}")
    for d in res["decisions"]:
        cm = d["chosen_metrics"]
        print(f"  decide t{d['tick']} at {d['pos']} -> {d['chosen']} "
              f"(expo {cm['expo_s']} s, drive {cm['drive_s']} s, dm {cm['dm_s']} s, "
              f"horizon {cm['horizon_h']} h, {cm['dist_km']} km) fail_closed={d['fail_closed']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", action="append")
    ap.add_argument("--ticks", type=int, default=120)
    ap.add_argument("--cap-h", type=float, default=None,
                    help="SENSITIVITY: cfg escape_road_horizon_cap_h (hours; "
                         "'inf' = no cap)")
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    more = {}
    if a.cap_h is not None:
        more["escape_road_horizon_cap_h"] = a.cap_h
    for arm in (a.arm or ["off", "snap"]):
        res = run(arm, a.ticks, more)
        cap = res.get("cfg", {}).get("escape_road_horizon_cap_h")
        res["cap_h"] = ("default" if cap is None else
                        "inf" if math.isinf(cap) else cap)
        tag = arm + (f"_{a.label}" if a.label else "")
        (OUT / f"diag_{tag}.json").write_text(json.dumps(res, indent=1),
                                              encoding="utf-8")
        digest(res)
        expo = sum(d["chosen_metrics"]["expo_s"] for d in res.get("decisions", [])
                   if isinstance(d["chosen_metrics"]["expo_s"], (int, float)))
        print(f"  cap {res['cap_h']} h: decisions {len(res.get('decisions', []))}, "
              f"sum of chosen route exposure {expo:.0f} s")


if __name__ == "__main__":
    main()
