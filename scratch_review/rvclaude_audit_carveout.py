"""ADVERSARIAL: does the 10c audit under-report vs the contract text?

contracts SAFETY SEMANTICS 10(c): "the audit ALSO flags a violation for any
crew whose current cell is burned (nominal arrival <= now) OR imminent
(eroded-P10 < now + egress_buffer_min) ... Strict superset of the old audit."

simui.py:3629 implements: `if not (burned or (imminent and cs._mode=="idle"))`
-- i.e. the imminent branch is EXEMPTED for every non-idle crew. This script
counts, per tick, crew-exposures under BOTH readings and reports the gap.
Read-only: it never mutates engine state, only reads cs.pos/_mode/status.
"""
import argparse, json, math, os, sys, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch_review.census_modviol_ab import PRESETS, _base_cfg
from scratch_review.bench_dispatch import HORIZON_TICKS
from wgresponse import geo, simui
from wgresponse.simui import LOCATIONS, T0_S, CrewStatus

def run(map_name, dist, crews, clients, intensity, hyst, extra=None, deploy=10.0, egress=30.0):
    cfg = _base_cfg(deploy, egress, 200.0, 24.0)
    cfg["escape_hysteresis_margin_min"] = hyst
    if extra: cfg.update(extra)
    loc = LOCATIONS[map_name]
    wd = loc.hazard_wind_dir_deg % 360.0
    s = simui.SimSession(map_name, clients, crews,
                         seed=f"{map_name}|+0|{dist:.0f}|{intensity}",
                         cfg_overrides=dict(cfg), fire_zone="standard")
    for shrink in (1.0, 0.85, 0.7, 0.55):
        p = geo.destination(loc.center, wd, dist*shrink)
        try:
            s.ignite(p.lat, p.lon, intensity=intensity, wind_dir_deg=wd); break
        except ValueError: continue
    else: raise SystemExit("off_tile")
    buf_s = s._cfg.egress_buffer_min*60.0
    off_ticks = 0; lit_ticks = 0; burned_ticks = 0
    modes = collections.Counter(); worst = []
    for t in range(HORIZON_TICKS):
        s.tick()
        now = s._now_s
        official = len(s._crew_overruns)
        literal = 0
        for cid in sorted(s._crew_sims):
            cs = s._crew_sims[cid]
            if cs.status == CrewStatus.INBOUND: continue
            nom, p10 = s._crew_exposure(cs.pos)
            burned = (not math.isinf(nom)) and nom <= now
            imminent = (not math.isinf(p10)) and p10 < now + buf_s
            if burned or imminent:
                literal += 1
                if burned: burned_ticks += 1
                if imminent and not burned and cs._mode != "idle":
                    off_ticks += 1; modes[cs._mode] += 1
                    lead = (p10-now)/60.0
                    worst.append((t, cid, cs._mode, cs.status.value, round(lead,1)))
    sb = s.state()["scoreboard"]
    return dict(off_ticks=off_ticks, burned_ticks=burned_ticks, modes=dict(modes),
                violations=sb.get("violations"), coated=sb.get("coated"),
                worst=worst[:14])

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--map", default="paradise_ca")
    ap.add_argument("--dist", type=float, default=8000.0)
    ap.add_argument("--crews", type=int, default=8); ap.add_argument("--clients", type=int, default=40)
    ap.add_argument("--intensity", default="moderate")
    a = ap.parse_args()
    print(f"scenario: {a.map} dist={a.dist} crews={a.crews} clients={a.clients} intensity={a.intensity}")
    print(f"  HORIZON_TICKS={HORIZON_TICKS}")
    for hyst in (0.0, 20.0):
        r = run(a.map, a.dist, a.crews, a.clients, a.intensity, hyst)
        print(f"\n  --- escape_hysteresis_margin_min = {hyst} ---")
        print(f"    scoreboard violations (as shipped) : {r['violations']}   coated={r['coated']}")
        print(f"    crew-ticks BURNED (counted in any mode): {r['burned_ticks']}")
        print(f"    crew-ticks IMMINENT-but-NOT-idle -> EXEMPTED by simui.py:3629 : {r['off_ticks']}")
        print(f"       exempted by mode: {r['modes']}")
        for w in r['worst']:
            print(f"       tick {w[0]:3d} crew={w[1]:<8} mode={w[2]:<8} status={w[3]:<10} eroded-P10 lead = {w[4]:+6.1f} min (< {30.0} min buffer)")

def sweep():
    MAPS=['paradise_ca','malibu_ca','santa_rosa_ca','boulder_co']
    out=[]
    for m in MAPS:
        for dist in (6000.0, 8000.0):
            for crews in (4, 8):
                for inten in ('moderate','extreme'):
                    for preset,extra in (('current',{}),('legacy',PRESETS['legacy'])):
                        try:
                            r = run(m, dist, crews, 40, inten, extra.get('escape_hysteresis_margin_min', 20.0), extra=extra)
                        except SystemExit as e:
                            print(f"  SKIP {m} {dist} c{crews} {inten} {preset}: {e}"); continue
                        except Exception as e:
                            print(f"  ERR  {m} {dist} c{crews} {inten} {preset}: {type(e).__name__} {e}"); continue
                        flag = "   <<<< AUDIT UNDER-REPORTS" if r['off_ticks']>0 else ""
                        print(f"  {m:<15} d={dist:.0f} c{crews} {inten:<8} {preset:<8} viol={str(r['violations']):<4} burned_ct={r['burned_ticks']:<4} EXEMPTED={r['off_ticks']:<4} {r['modes']}{flag}", flush=True)
                        out.append((m,dist,crews,inten,preset,r))
    tot=sum(r['off_ticks'] for *_ ,r in out); tb=sum(r['burned_ticks'] for *_,r in out)
    print(f"\nTOTAL exempted imminent-non-idle crew-ticks across {len(out)} scenarios: {tot}")
    print(f"TOTAL burned crew-ticks (audit does count these): {tb}")
    import json as J
    J.dump([{'map':m,'dist':d,'crews':c,'inten':i,'preset':p,**{k:v for k,v in r.items() if k!='worst'},'worst':r['worst']} for m,d,c,i,p,r in out],
           open(r'C:\Users\colin\wg_response\scratch_review\rvclaude_audit_carveout.json','w'), indent=1)
