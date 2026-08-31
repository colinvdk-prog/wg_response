import json, sys, os
SP=r'C:\Users\colin\AppData\Local\Temp\claude\C--Users-colin\301cc633-83bf-4a52-9972-a841bce65068\scratchpad\old_replay.json'
old = json.load(open(SP))
new = json.load(open(r'C:\Users\colin\wg_response\tools\hindcast_data\replay_results.json'))
sys.path.insert(0,r'C:\Users\colin\wg_response')
print("schema old/new:", old['schema'], new['schema'])
print("n pairs old/new:", len(old['pairs']), len(new['pairs']))
print("skips old/new:", old['skips'], new['skips'])
KEYS=['n_new','n_new_burnable','covered_possible','late_miss','early_miss','never_reached','p50_in_window','obs_head_m','head_m_p50','modeled_new_area_ac_p50']
ndiff=0
for o,n in zip(old['pairs'], new['pairs']):
    assert o['name']==n['name'] and o['t0_s']==n['t0_s'], (o['name'],n['name'])
    ds=[]
    for k in KEYS:
        if o.get(k)!=n.get(k):
            ds.append(f"{k}: {o.get(k)!r} -> {n.get(k)!r}")
    if ds:
        ndiff+=1
        print(f"\n  {o['name']} +{o['dt_h']:.1f}h  DIFFERS:")
        for d in ds: print("     ", d)
print(f"\npairs differing: {ndiff}/{len(old['pairs'])}")

def pool(ps):
    ps=[p for p in ps if p['n_new_burnable']>0]
    n=sum(p['n_new_burnable'] for p in ps)
    w=lambda k: 100.0*sum(p[k] for p in ps)/n
    hr=sorted(p['head_m_p50']/p['obs_head_m'] for p in ps if p['obs_head_m']>0)
    return dict(n_pairs=len(ps), n_cells=n, coverage=w('covered_possible'), late=w('late_miss'),
                early=w('early_miss'), p50in=w('p50_in_window'), med_head_code=hr[len(hr)//2],
                med_head_true=(hr[len(hr)//2]+hr[len(hr)//2-1])/2 if len(hr)%2==0 else hr[len(hr)//2])
for lbl,d in (('COMMITTED',old),('REPRODUCED',new)):
    for pk in ('pairs','pairs_wide'):
        r=pool(d[pk])
        print(f"{lbl:<11} {pk:<11} pairs={r['n_pairs']} cells={r['n_cells']} coverage={r['coverage']:.4f}% late={r['late']:.4f}% early={r['early']:.4f}% p50in={r['p50in']:.4f}% medhead(code)={r['med_head_code']:.4f} medhead(true)={r['med_head_true']:.4f}")
