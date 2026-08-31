import json, statistics, collections
P = r'C:\Users\colin\wg_response\scratch_review\hindcast_aug_data\aug_replay.json'
d = json.load(open(P))
rows=[r for r in d['rows'] if r['n_new_burnable']>0]
print("=== CHECK 1: is covered_possible identical to p10_not_after_window? ===")
bad=[r for r in rows if r['covered_possible']!=r['p10_not_after_window']]
print(f"  rows where they differ: {len(bad)} / {len(rows)}")
print("=== CHECK 2: does covered+late == burnable exactly (i.e. early/P90 never bind)? ===")
bad2=[r for r in rows if r['covered_possible']+r['late_miss']!=r['n_new_burnable']]
print(f"  rows violating: {len(bad2)} / {len(rows)}")
print("=== CHECK 3: early_miss anywhere nonzero? covered_certain anywhere nonzero? p10_before_window? ===")
print("  sum early_miss     =", sum(r['early_miss'] for r in rows))
print("  sum covered_certain=", sum(r['covered_certain'] for r in rows))
print("  sum p10_before_win =", sum(r['p10_before_window'] for r in rows))
print("  sum p90_unreached  =", sum(r['p90_unreached'] for r in rows), " (P90 never arrives but P10 does)")
print("  sum never_reached  =", sum(r['never_reached'] for r in rows))

print("\n=== AREA OVER-PREDICTION (the unreported cost side) ===")
for var in ('calibration','as-deployed'):
    rs=[r for r in rows if r['variant']==var]
    o=sum(r['obs_new_area_ac'] for r in rs); m=sum(r['modeled_new_area_ac_p50'] for r in rs)
    per=[r['modeled_new_area_ac_p50']/r['obs_new_area_ac'] for r in rs if r['obs_new_area_ac']>0]
    per.sort()
    print(f"  {var:<12} pooled obs={o:11.0f} ac   modeled_P50={m:12.0f} ac   POOLED RATIO={m/o:6.2f}x")
    print(f"               per-pair area ratio: median={statistics.median(per):.2f}x  mean={statistics.mean(per):.2f}x  min={per[0]:.2f}  max={per[-1]:.2f}")
    print(f"               sorted: {' '.join(f'{x:.1f}' for x in per)}")

print("\n=== PIT HISTOGRAM (computed by the harness, never reported) ===")
for var in ('calibration','as-deployed'):
    rs=[r for r in rows if r['variant']==var]
    nb=max(len(r['pit_hist']) for r in rs)
    pit=[0]*nb
    for r in rs:
        for k,v in enumerate(r['pit_hist']): pit[k]+=v
    tot=sum(pit)
    print(f"  {var:<12} n_members={nb-1}  bins(n={tot}):")
    print(f"     counts: {pit}")
    print(f"     pct   : {' '.join(f'{100*x/tot:5.1f}' for x in pit)}   (flat would be {100/nb:.1f} each)")
    ends=(pit[0]+pit[-1])/tot
    print(f"     extreme-bin mass (bin0+binN) = {100*ends:.1f}%  (flat expectation {100*2/nb:.1f}%)  -> ratio {ends/(2/nb):.2f}x")
