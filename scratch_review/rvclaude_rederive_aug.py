import json, statistics, collections, os, sys
P = r'C:\Users\colin\wg_response\scratch_review\hindcast_aug_data\aug_replay.json'
d = json.load(open(P))
rows = d['rows']
print("total rows:", len(rows))
byvar = collections.defaultdict(list)
for r in rows:
    byvar[r['variant']].append(r)
for v, rs in byvar.items():
    print(f"  variant={v!r} n={len(rs)} labels={sorted(set(r['label'] for r in rs))}")
print()

def pool(rs, label):
    n_cells = sum(r['n_new_burnable'] for r in rs)
    cov = sum(r['covered_possible'] for r in rs)
    late = sum(r['late_miss'] for r in rs)
    early = sum(r['early_miss'] for r in rs)
    nev = sum(r.get('never_reached',0) for r in rs)
    p50w = sum(r['p50_in_window'] for r in rs)
    hr = [r['head_m_p50']/r['obs_head_m'] for r in rs if r.get('obs_head_m')]
    print(f"== {label} ==")
    print(f"  n_pairs={len(rs)}  n_cells={n_cells}")
    print(f"  CELL-WEIGHTED  coverage={100*cov/n_cells:.4f}%  late={100*late/n_cells:.4f}%  early={100*early/n_cells:.4f}%  never={100*nev/n_cells:.4f}%  p50_in_win={100*p50w/n_cells:.4f}%")
    # pair-weighted (each incident-pair equal)
    pc = [100*r['covered_possible']/r['n_new_burnable'] for r in rs if r['n_new_burnable']]
    pl = [100*r['late_miss']/r['n_new_burnable'] for r in rs if r['n_new_burnable']]
    print(f"  PAIR-WEIGHTED  coverage mean={statistics.mean(pc):.4f}%  median={statistics.median(pc):.4f}%  min={min(pc):.2f}  max={max(pc):.2f}")
    print(f"  PAIR-WEIGHTED  late mean={statistics.mean(pl):.4f}%  median={statistics.median(pl):.4f}%")
    print(f"  head ratio (p50/obs): median={statistics.median(hr):.4f}  mean={statistics.mean(hr):.4f}  min={min(hr):.3f} max={max(hr):.3f}  n={len(hr)}")
    # per fuel class
    fc = collections.defaultdict(lambda: [0,0])
    for r in rs:
        for k,v in r.get('by_fuel_class',{}).items():
            fc[k][0]+=v['n']; fc[k][1]+=v['late']
    print("  per-fuel late%: " + "  ".join(f"{k} {100*v[1]/v[0]:.2f}({v[0]})" for k,v in sorted(fc.items())))
    # worst pairs by late
    worst = sorted(rs, key=lambda r: -(r['late_miss']/max(1,r['n_new_burnable'])))[:5]
    print("  worst 5 pairs by late%:")
    for r in worst:
        print(f"    {r['name']:<22} late={100*r['late_miss']/r['n_new_burnable']:6.2f}%  cells={r['n_new_burnable']:6d}  dt={r['dt_h']:.1f}h  headratio={r['head_m_p50']/r['obs_head_m'] if r.get('obs_head_m') else float('nan'):.2f}  cell_m={r['cell_m']}")
    print()

for v in ('calibration','as-deployed'):
    if v in byvar: pool(byvar[v], v)

print("STORED POOLED:", json.dumps(d['pooled'], indent=1))
