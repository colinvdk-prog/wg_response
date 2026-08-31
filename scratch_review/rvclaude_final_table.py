import json,statistics,os,collections
D=r'C:\Users\colin\wg_response\scratch_review'
def rows(p): return [r for r in json.load(open(p)).get('rows', json.load(open(p))) if isinstance(r,dict) and r.get('n_new_burnable',0)>0]
def pool(rs):
    n=sum(r['n_new_burnable'] for r in rs)
    w=lambda k:100.0*sum(r[k] for r in rs)/n
    hr=sorted(r['head_m_p50']/r['obs_head_m'] for r in rs if r.get('obs_head_m'))
    o=sum(r['obs_new_area_ac'] for r in rs); m=sum(r['modeled_new_area_ac_p50'] for r in rs)
    per=sorted(r['modeled_new_area_ac_p50']/r['obs_new_area_ac'] for r in rs if r['obs_new_area_ac']>0)
    rate=sorted(r['obs_head_m']/r['dt_h']/1000 for r in rs if r.get('obs_head_m') and r['dt_h'])
    return dict(pairs=len(rs), fires=len(set(r['name'] for r in rs)), cells=n,
        cov=w('covered_possible'), late=w('late_miss'), early=w('early_miss'),
        head_code=hr[len(hr)//2], head_true=statistics.median(hr),
        area_pooled=m/o, area_med=statistics.median(per), rate_max=rate[-1], rate_med=statistics.median(rate))
S=[]
aug=json.load(open(os.path.join(D,'hindcast_aug_data','aug_replay.json')))['rows']
for v in ('calibration','as-deployed'):
    S.append((f"Aug-2026 OOS [{v}]", pool([r for r in aug if r['variant']==v and r['n_new_burnable']>0])))
cal=json.load(open(os.path.join(D,'hindcast_oos_data','cal_realism.json')))
for v in ('blend0.5','grass_floor','realism_pack'):
    S.append((f"Calib 2025-CA [{v}]", pool([r for r in cal if r.get('variant')==v and r['n_new_burnable']>0])))
oos=json.load(open(os.path.join(D,'hindcast_oos_data','oos_realism.json')))
for v in ('grass_floor','realism_pack'):
    S.append((f"Jul-2026 OOS [{v}]", pool([r for r in oos if r.get('variant')==v and r['n_new_burnable']>0])))
print(f"{'era / config':<30} {'prs':>4} {'fires':>5} {'cells':>7} {'cover%':>7} {'late%':>6} {'early%':>7} {'head(pub)':>10} {'head(TRUE)':>11} {'AREA pool':>10} {'AREA med':>9} {'obs km/h max':>13}")
for lbl,p in S:
    print(f"{lbl:<30} {p['pairs']:>4} {p['fires']:>5} {p['cells']:>7} {p['cov']:>7.2f} {p['late']:>6.2f} {p['early']:>7.2f} {p['head_code']:>10.3f} {p['head_true']:>11.3f} {p['area_pooled']:>9.2f}x {p['area_med']:>8.2f}x {p['rate_max']:>13.2f}")
