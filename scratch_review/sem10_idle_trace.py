"""SEM-10 idle-in-path tick tracer (moderate-wind attribution follow-up).

The census_modviol trace (scratch_review/census_modviol_trace_current_
paradise_ca_8000_c8.json) shows crews sim-g1/g4/g5/g7 sitting status=
available/mode=idle on ONE shared cell while the 10c audit flagged them
imminent (ticks 45-46) then BURNED (47-55), only egressing at tick 56.
This script reruns that exact scenario in-process (same recipe as
census_modviol_ab.trace_scenario: LOCATIONS lookup, shrink ladder,
HORIZON_TICKS loop, cfg = census_windregime.BASE + deploy/egress/ember/hold
pins, preset overridable) and instruments the SEM-10 machinery so every
sweep decision is visible:

  * _SimCrew.relocate_to / retarget_egress -> call log (who, when, target)
  * SimSession._best_escape -> call log incl. a RECOMPUTED internals dump:
      - _currently_safe_zones(now, lead) ids + fail_closed
      - _reachable_zone pick over those zones
      - per-catalog-zone _route_metrics(pos, z.loc, ...) rows
      - _escape_ray_targets proposals (bearing, dist)
      - the returned (zone_id, same_pos?) verdict
  * per-tick, per-crew state rows (pos, mode, status, egress target,
    _relocating flag, nom/p10 at pos, burned/imminent-with-lead)

Output: scratch_review/sem10_idle_trace_<map>_<dist>_c<crews>.json plus a
condensed stdout table for the tick window of interest.

Run (PYTHONHASHSEED=0, deploy 10 -- the recorded-attribution convention):
  PYTHONHASHSEED=0 py -3 scratch_review/sem10_idle_trace.py --dist 8000
  PYTHONHASHSEED=0 py -3 scratch_review/sem10_idle_trace.py --dist 6000
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch_review.census_modviol_ab import PRESETS, _base_cfg  # noqa: E402
from scratch_review.bench_dispatch import HORIZON_TICKS  # noqa: E402
from wgresponse import geo, simui  # noqa: E402
from wgresponse.simui import LOCATIONS, T0_S  # noqa: E402


def tick_of(t_s):
    return None if t_s is None or math.isinf(t_s) else (t_s - T0_S) / 900.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="paradise_ca")
    ap.add_argument("--dist", type=float, default=8000.0)
    ap.add_argument("--crews", type=int, default=8)
    ap.add_argument("--intensity", default="moderate")
    ap.add_argument("--clients", type=int, default=40)
    ap.add_argument("--deploy", type=float, default=10.0)
    ap.add_argument("--egress", type=float, default=30.0)
    ap.add_argument("--ember-radius", type=float, default=200.0)
    ap.add_argument("--hold", type=float, default=24.0)
    ap.add_argument("--preset", default="none", choices=tuple(PRESETS))
    ap.add_argument("--from-tick", type=int, default=25)
    ap.add_argument("--to-tick", type=int, default=59)
    ap.add_argument("--watch", nargs="*", default=None,
                    help="crew ids to print (default: all)")
    args = ap.parse_args()

    cfg = _base_cfg(args.deploy, args.egress, args.ember_radius, args.hold)
    cfg.update(PRESETS[args.preset])

    loc = LOCATIONS[args.map]
    wd = (loc.hazard_wind_dir_deg + 0.0) % 360.0
    s = simui.SimSession(args.map, args.clients, args.crews,
                         seed=f"{args.map}|+0|{args.dist:.0f}|{args.intensity}",
                         cfg_overrides=dict(cfg), fire_zone="standard")
    for shrink in (1.0, 0.85, 0.7, 0.55):
        p = geo.destination(loc.center, wd, args.dist * shrink)
        try:
            s.ignite(p.lat, p.lon, intensity=args.intensity, wind_dir_deg=wd)
            break
        except ValueError:
            continue
    else:
        raise SystemExit("off_tile on every shrink factor")

    calls = []          # _best_escape / relocate_to / retarget_egress log
    tickbox = {"n": -1}  # current tick number, visible to the wrappers

    orig_best = simui.SimSession._best_escape
    orig_reloc = simui._SimCrew.relocate_to
    orig_retgt = simui._SimCrew.retarget_egress

    def crew_at(sess, pos):
        for cid, cs in sess._crew_sims.items():
            if cs.pos == pos:
                return cid
        return None

    def best_wrap(self, pos, now, speed_mps, lead_s):
        res_loc, res_id = orig_best(self, pos, now, speed_mps, lead_s)
        cid = crew_at(self, pos)
        entry = dict(kind="best_escape", tick=tickbox["n"], crew=cid,
                     now_tick=round(tick_of(now), 3),
                     pos=[round(pos.lat, 6), round(pos.lon, 6)],
                     result_id=res_id,
                     result_same_pos=bool(pos == res_loc))
        # ---- recomputed internals (queries only; no state mutation) ----
        safe, fc = self._currently_safe_zones(now, lead_s=lead_s)
        entry["safe_zone_ids"] = [z.zone_id for z in safe]
        entry["fail_closed"] = fc
        if not fc:
            rz = self._reachable_zone(pos, safe, now, speed_mps, lead_s)
            entry["reachable_zone"] = None if rz is None else rz.zone_id
        else:
            entry["reachable_zone"] = "(fail_closed)"
        zrows = []
        for z in sorted(self._loc.safe_zones, key=lambda z: z.zone_id):
            b, e, w, t, dm = self._route_metrics(pos, z.loc, now, speed_mps,
                                                 lead_s)
            nomz, p10z = self._crew_exposure(z.loc)
            zrows.append(dict(zone=z.zone_id, burned=b,
                              expo_s=round(e, 1),
                              worst_min=None if math.isinf(w)
                              else round(w / 60.0, 2),
                              total_min=round(t / 60.0, 2),
                              dm_min=None if math.isinf(dm)
                              else round(dm / 60.0, 2),
                              zone_nom_tick=None if math.isinf(nomz)
                              else round(tick_of(nomz), 2),
                              zone_p10_tick=None if math.isinf(p10z)
                              else round(tick_of(p10z), 2),
                              dist_m=round(geo.haversine_m(pos, z.loc))))
        entry["zone_routes"] = zrows
        rays = []
        for bearing, tgt in self._escape_ray_targets(pos, now, speed_mps):
            b, e, w, t, dm = self._route_metrics(pos, tgt, now, speed_mps,
                                                 lead_s)
            rays.append(dict(bearing=bearing,
                             ray_m=round(geo.haversine_m(pos, tgt)),
                             burned=b, expo_s=round(e, 1),
                             dm_min=None if math.isinf(dm)
                             else round(dm / 60.0, 2)))
        entry["rays"] = rays
        calls.append(entry)
        return res_loc, res_id

    def reloc_wrap(self, zone_loc, zone_zone_id):
        calls.append(dict(kind="relocate_to", tick=tickbox["n"],
                          crew=self.crew.crew_id, target=zone_zone_id,
                          target_pos=[round(zone_loc.lat, 6),
                                      round(zone_loc.lon, 6)]))
        return orig_reloc(self, zone_loc, zone_zone_id)

    def retgt_wrap(self, zone_loc, zone_zone_id):
        calls.append(dict(kind="retarget_egress", tick=tickbox["n"],
                          crew=self.crew.crew_id, target=zone_zone_id))
        return orig_retgt(self, zone_loc, zone_zone_id)

    simui.SimSession._best_escape = best_wrap
    simui._SimCrew.relocate_to = reloc_wrap
    simui._SimCrew.retarget_egress = retgt_wrap

    crew_rows = []
    note_seen = 0
    try:
        for tick in range(HORIZON_TICKS):
            tickbox["n"] = tick
            st = s.tick()
            now = s._now_s
            notes = st.get("notes") or []
            new_notes = notes[note_seen:] if len(notes) > note_seen else []
            note_seen = max(note_seen, len(notes))
            for cid in sorted(s._crew_sims):
                cs = s._crew_sims[cid]
                nom, p10 = s._crew_exposure(cs.pos)
                buffer_s = s._cfg.egress_buffer_min * 60.0
                lead_s = 900.0
                crew_rows.append(dict(
                    tick=tick, crew=cid, mode=cs._mode,
                    status=cs.status.value,
                    pos=[round(cs.pos.lat, 6), round(cs.pos.lon, 6)],
                    egress_zone=cs.egress_zone_id,
                    relocating=cid in s._relocating,
                    nom_tick=None if math.isinf(nom)
                    else round(tick_of(nom), 2),
                    p10_tick=None if math.isinf(p10)
                    else round(tick_of(p10), 2),
                    burned=(not math.isinf(nom)) and nom <= now,
                    imminent=(not math.isinf(p10))
                    and p10 < now + buffer_s + lead_s))
            if new_notes:
                calls.append(dict(kind="notes", tick=tick, notes=new_notes))
    finally:
        simui.SimSession._best_escape = orig_best
        simui._SimCrew.relocate_to = orig_reloc
        simui._SimCrew.retarget_egress = orig_retgt

    out = dict(map=args.map, dist=args.dist, crews=args.crews,
               preset=args.preset, cfg_overrides=PRESETS[args.preset],
               calls=calls, crew_rows=crew_rows)
    tag = f"sem10_idle_trace_{args.map}_{args.dist:.0f}_c{args.crews}"
    path = f"scratch_review/{tag}.json"
    json.dump(out, open(path, "w"), indent=1)
    print("wrote", path)

    watch = set(args.watch) if args.watch else None
    print("\n== per-crew state (window {}..{}) ==".format(
        args.from_tick, args.to_tick))
    for r in crew_rows:
        if not (args.from_tick <= r["tick"] <= args.to_tick):
            continue
        if watch and r["crew"] not in watch:
            continue
        flag = ("BURNED" if r["burned"]
                else ("IMM" if r["imminent"] else "     "))
        print(f" t{r['tick']:>2} {r['crew']:<8} {r['mode']:<7} "
              f"{r['status']:<10} {flag:<6} egress={r['egress_zone'] or '-':<18}"
              f" reloc={'Y' if r['relocating'] else 'n'} "
              f"nom_t={r['nom_tick']} p10_t={r['p10_tick']} "
              f"pos={r['pos'][0]:.4f},{r['pos'][1]:.4f}")
    print("\n== sweep calls (window) ==")
    for c in calls:
        if c["kind"] == "notes":
            if args.from_tick <= c["tick"] <= args.to_tick:
                for n in c["notes"]:
                    print(f" t{c['tick']:>2} NOTE {n}")
            continue
        if not (args.from_tick <= c["tick"] <= args.to_tick):
            continue
        if c["kind"] == "best_escape":
            if watch and c.get("crew") and c["crew"] not in watch:
                continue
            print(f" t{c['tick']:>2} best_escape crew={c['crew']} "
                  f"-> {c['result_id']} same_pos={c['result_same_pos']} "
                  f"safe={c['safe_zone_ids']} fc={c['fail_closed']} "
                  f"reach={c['reachable_zone']} rays={len(c['rays'])}")
            for zr in c["zone_routes"]:
                print(f"      {zr['zone']}: burned={zr['burned']} "
                      f"expo={zr['expo_s']}s worst={zr['worst_min']}m "
                      f"tot={zr['total_min']}m dm={zr['dm_min']}m "
                      f"d={zr['dist_m']}m zone_nom_t={zr['zone_nom_tick']} "
                      f"zone_p10_t={zr['zone_p10_tick']}")
            for ray in c["rays"]:
                print(f"      ray b={ray['bearing']:>3} {ray['ray_m']}m "
                      f"burned={ray['burned']} expo={ray['expo_s']}s "
                      f"dm={ray['dm_min']}m")
        else:
            print(f" t{c['tick']:>2} {c['kind']} crew={c['crew']} "
                  f"-> {c.get('target')}")


if __name__ == "__main__":
    main()
