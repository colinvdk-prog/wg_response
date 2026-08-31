import json, statistics, collections, datetime
P = r'C:\Users\colin\wg_response\scratch_review\hindcast_aug_data\aug_replay.json'
d = json.load(open(P))
rows = d['rows']
dep = [r for r in rows if r['variant']=='as-deployed']
cal = [r for r in rows if r['variant']=='calibration']
print("rows per variant:", len(dep), len(cal))
z = [r for r in dep if r['n_new_burnable']==0]
print("zero-burnable rows:", [(r['name'], r['dt_h'], r['n_new'], r['notes']) for r in z])
dep = [r for r in dep if r['n_new_burnable']>0]
cal = [r for r in cal if r['n_new_burnable']>0]
print("scored pairs:", len(dep))

# --- distinct incidents / dependence
inc = collections.Counter(r['name'] for r in dep)
print("\nDISTINCT INCIDENTS:", len(inc))
for k,v in inc.most_common(): print(f"   {k:<20} {v} pair(s)")
cellsby = collections.defaultdict(int)
lateby  = collections.defaultdict(int)
for r in dep:
    cellsby[r['name']] += r['n_new_burnable']; lateby[r['name']] += r['late_miss']
N = sum(cellsby.values()); L = sum(lateby.values())
print(f"\ntotal cells {N}  total late {L}  = {100*L/N:.4f}%")
print("cell share + late share by incident:")
for k in sorted(cellsby, key=lambda k:-cellsby[k]):
    print(f"   {k:<20} cells {cellsby[k]:6d} ({100*cellsby[k]/N:5.2f}%)   late {lateby[k]:6d} ({100*lateby[k]/L if L else 0:6.2f}% of all late)  own-late {100*lateby[k]/cellsby[k]:6.2f}%")

# --- leave-one-out on the pooled late number
print("\nLEAVE-ONE-PAIR-OUT on pooled late-miss (as-deployed):")
base = 100*L/N
for r in sorted(dep, key=lambda r:-r['late_miss'])[:6]:
    n2 = N - r['n_new_burnable']; l2 = L - r['late_miss']
    print(f"   drop {r['name']:<16} dt={r['dt_h']:5.1f}h cells={r['n_new_burnable']:6d} -> pooled late {100*l2/n2:6.3f}%  (base {base:.3f}%)  coverage {100-100*l2/n2:6.3f}%")
print("\nLEAVE-ONE-INCIDENT-OUT:")
for k in sorted(cellsby, key=lambda k:-lateby[k])[:6]:
    n2 = N-cellsby[k]; l2 = L-lateby[k]
    print(f"   drop {k:<20} -> pooled late {100*l2/n2:6.3f}%  coverage {100-100*l2/n2:6.3f}%")

# --- median bug
for label, rs in (('calibration',cal),('as-deployed',dep)):
    heads = sorted(r['head_m_p50']/r['obs_head_m'] for r in rs if r.get('obs_head_m'))
    print(f"\n{label}: n_heads={len(heads)}  code median (heads[n//2]) = {heads[len(heads)//2]:.6f}   TRUE median = {statistics.median(heads):.6f}   lower-mid={heads[len(heads)//2-1]:.6f}")
    print("   sorted head ratios:", " ".join(f"{h:.2f}" for h in heads))

# --- exclusions
print("\nEXCLUSION REASONS (n=%d):" % len(d['excluded']))
c = collections.Counter()
for e in d['excluded']:
    w = e['why']
    key = w.split('(')[0][:60]
    c[key]+=1
for k,v in c.most_common(): print(f"   {v:3d}  {k}")
print("\nfull excluded list:")
for e in d['excluded']:
    t0 = datetime.datetime.fromtimestamp(e['t0'], datetime.timezone.utc).strftime('%m-%d %H:%M') if e['t0'] else '   -   '
    t1 = datetime.datetime.fromtimestamp(e['t1'], datetime.timezone.utc).strftime('%m-%d %H:%M') if e['t1'] else '   -   '
    dt = (e['t1']-e['t0'])/3600 if (e['t0'] and e['t1']) else float('nan')
    print(f"   {e['incident']:<22} {t0} .. {t1}  dt={dt:7.1f}h  {e['why']}")

# --- dt / size distribution of INCLUDED
print("\nINCLUDED pair dt_h:", " ".join(f"{r['dt_h']:.1f}" for r in sorted(dep,key=lambda r:r['dt_h'])))
print("obs head rate km/h:", " ".join(f"{r['obs_head_m']/r['dt_h']/1000:.2f}" for r in sorted(dep,key=lambda r:r['obs_head_m']/r['dt_h'])))
