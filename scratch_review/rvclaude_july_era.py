import json, statistics, collections, os
D=r'C:\Users\colin\wg_response\scratch_review\hindcast_oos_data'
print("########## oos_pooled.json — the GUST-BLEND SWEEP (July OOS) ##########")
for r in json.load(open(os.path.join(D,'oos_pooled.json'))):
    print("  ", json.dumps(r))
def pool(rs, lbl):
    rs=[r for r in rs if r.get('n_new_burnable',0)>0]
    if not rs: return
    n=sum(r['n_new_burnable'] for r in rs)
    w=lambda k:100.0*sum(r[k] for r in rs)/n
    hr=sorted(r['head_m_p50']/r['obs_head_m'] for r in rs if r.get('obs_head_m'))
    o=sum(r['obs_new_area_ac'] for r in rs); m=sum(r['modeled_new_area_ac_p50'] for r in rs)
    per=sorted(r['modeled_new_area_ac_p50']/r['obs_new_area_ac'] for r in rs if r['obs_new_area_ac']>0)
    print(f"  {lbl:<26} pairs={len(rs):<3} cells={n:<7} cov={w('covered_possible'):6.2f}% late={w('late_miss'):6.2f}% early={w('early_miss'):4.2f}% "
          f"medhead code={hr[len(hr)//2]:5.3f}/true={statistics.median(hr):5.3f}  AREA pooled={m/o:6.2f}x med-pair={statistics.median(per):7.2f}x")
    inc=collections.Counter(r['name'] for r in rs)
    lateby=collections.defaultdict(int); cellby=collections.defaultdict(int)
    for r in rs: lateby[r['name']]+=r['late_miss']; cellby[r['name']]+=r['n_new_burnable']
    L=sum(lateby.values())
    print(f"       incidents={len(inc)} {dict(inc)}")
    print(f"       late share: " + " | ".join(f"{k}:{100*lateby[k]/L:.0f}%" for k in sorted(lateby,key=lambda k:-lateby[k])) if L else "       late share: (none)")
    # PIT
    nb=max(len(r['pit_hist']) for r in rs); pit=[0]*nb
    for r in rs:
        for k,v in enumerate(r['pit_hist']): pit[k]+=v
    t=sum(pit); print(f"       PIT %: {' '.join(f'{100*x/t:.1f}' for x in pit)}  (flat={100/nb:.1f})  extremes={100*(pit[0]+pit[-1])/t:.1f}% vs {200/nb:.1f}%")
for f in ('oos_realism.json','cal_realism.json'):
    d=json.load(open(os.path.join(D,f)))
    print(f"\n########## {f} ##########")
    vs=collections.Counter(r.get('variant') for r in d)
    print("  variants:", dict(vs))
    for v in vs: pool([r for r in d if r.get('variant')==v], f"{v}")
