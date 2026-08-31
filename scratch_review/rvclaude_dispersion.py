"""Is the ensemble wide enough to contain its own error? Compare the ENSEMBLE's
internal spread (head_p10 vs head_p50) against the MODEL's actual error spread
(head_p50 vs observed) on the August corpus."""
import json, statistics, math
P=r'C:\Users\colin\wg_response\scratch_review\hindcast_aug_data\aug_replay.json'
rows=[r for r in json.load(open(P))['rows'] if r['n_new_burnable']>0]
for var in ('calibration','as-deployed'):
    rs=[r for r in rows if r['variant']==var and r.get('obs_head_m')]
    ens=[r['head_m_p10']/r['head_m_p50'] for r in rs if r['head_m_p50']>0]
    err=[r['head_m_p50']/r['obs_head_m'] for r in rs]
    lerr=[math.log(x) for x in err]; lens=[math.log(x) for x in ens]
    print(f"\n=== {var} (n={len(rs)}) ===")
    print(f"  ENSEMBLE internal spread  head_P10/head_P50 : median={statistics.median(ens):.3f}  range {min(ens):.3f}-{max(ens):.3f}")
    print(f"     => the band's own optimistic edge sits only {100*(statistics.median(ens)-1):+.1f}% from its median")
    print(f"  MODEL error   head_P50/observed              : median={statistics.median(err):.3f}  range {min(err):.3f}-{max(err):.3f}")
    print(f"     sd of log(error) = {statistics.pstdev(lerr):.3f}   sd of log(ens spread) = {statistics.pstdev(lens):.3f}")
    print(f"     ratio of error spread to ensemble spread (in log units, vs the P10 arm):")
    half = abs(statistics.median(lens))   # log-distance from P50 to P10 arm
    need = statistics.pstdev(lerr)
    print(f"       band half-width (P50->P10) = {half:.3f} log-units;  1 sd of actual error = {need:.3f} log-units  ->  UNDER-DISPERSED by {need/half:.1f}x" if half>0 else "       band half-width is ZERO")
    # how many pairs fall OUTSIDE the ensemble's own head band?
    out=sum(1 for r in rs if not (r['head_m_p10']<=r['obs_head_m']<=r['head_m_p50']*1.0 or r['head_m_p50']>=r['obs_head_m']>=r['head_m_p10']))
    inside=sum(1 for r in rs if min(r['head_m_p10'],r['head_m_p50'])<=r['obs_head_m']<=max(r['head_m_p10'],r['head_m_p50']))
    print(f"  observed head inside the model's own [head_P10, head_P50] interval: {inside}/{len(rs)} pairs = {100*inside/len(rs):.0f}%")
