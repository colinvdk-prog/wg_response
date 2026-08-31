import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch_review import rvclaude_audit_carveout as M
from scratch_review.census_modviol_ab import PRESETS
# Most-exposed configurations: few crews, extreme intensity, legacy sweep (pre-fix)
for m,dist,crews,inten,preset,extra in [
    ('paradise_ca',8000.0,4,'extreme','legacy',PRESETS['legacy']),
    ('paradise_ca',6000.0,4,'extreme','legacy',PRESETS['legacy']),
    ('malibu_ca',6000.0,4,'extreme','legacy',PRESETS['legacy']),
    ('paradise_ca',8000.0,4,'extreme','current',{}),
]:
    try:
        r = M.run(m,dist,crews,40,inten, extra.get('escape_hysteresis_margin_min',20.0), extra=extra)
    except Exception as e:
        print(f"  ERR {m} {dist} c{crews} {inten} {preset}: {type(e).__name__} {e}", flush=True); continue
    flag = "   <<<< AUDIT UNDER-REPORTS" if r['off_ticks']>0 else ""
    print(f"  {m:<13} d={dist:.0f} c{crews} {inten:<8} {preset:<8} viol={r['violations']} burned_ct={r['burned_ticks']} EXEMPTED={r['off_ticks']} {r['modes']}{flag}", flush=True)
    for w in r['worst'][:8]:
        print(f"      tick {w[0]:3d} crew={w[1]:<10} mode={w[2]:<9} status={w[3]:<10} eroded-P10 lead={w[4]:+6.1f} min", flush=True)
