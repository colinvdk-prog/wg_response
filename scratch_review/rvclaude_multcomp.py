import json,glob,os,collections,statistics
D=r'C:\Users\colin\wg_response\scratch_review'
fs=sorted(glob.glob(os.path.join(D,'opt_ember_*.json')))+sorted(glob.glob(os.path.join(D,'bench_*.json')))
d=json.load(open(os.path.join(D,'opt_ember_cb_c4.json')))
print("stats keys:", json.dumps(d['stats'], indent=1))
print("params:", json.dumps(d['params'], indent=1))
print("row0:", json.dumps(d['rows'][0], indent=1)[:900])
print()
recs=[]
for f in fs:
    try: j=json.load(open(f))
    except Exception: continue
    if not isinstance(j,dict) or 'stats' not in j: continue
    s=j['stats']
    recs.append((os.path.basename(f), j.get('suite'), len(j.get('rows',[])), s))
print(f"runs with stats blocks: {len(recs)}")
suites=collections.Counter(r[1] for r in recs); print("suites:", dict(suites))
sizes=collections.Counter(r[2] for r in recs); print("scenario counts per run:", dict(sizes))
keys=collections.Counter()
for _,_,_,s in recs: keys.update(s.keys())
print("stat keys seen:", dict(keys))

print("\n================ MULTIPLE-COMPARISONS SURFACE ================")
# headline metric used across the repo: e500 belt survivability %
by_suite=collections.defaultdict(list)
for name,suite,n,s in recs:
    if 'e500' in s and isinstance(s['e500'],list) and len(s['e500'])==3:
        by_suite[(suite,n)].append((name, s['e500'][2], s['e500'][0], s['e500'][1], s.get('viol'), s.get('coated_total')))
for k in sorted(by_suite, key=lambda k: -len(by_suite[k])):
    v=by_suite[k]
    if len(v)<4: continue
    vals=[x[1] for x in v]
    print(f"\n suite={k[0]} scenarios={k[1]}  n_runs={len(v)}")
    print(f"   e500 belt %: min={min(vals):.2f} max={max(vals):.2f} spread={max(vals)-min(vals):.2f}pp  median={statistics.median(vals):.2f} sd={statistics.pstdev(vals):.2f}")
    top=sorted(v,key=lambda x:-x[1])[:5]; bot=sorted(v,key=lambda x:x[1])[:3]
    print("   best:  " + " | ".join(f"{t[0][:34]}={t[1]:.2f}" for t in top))
    print("   worst: " + " | ".join(f"{t[0][:34]}={t[1]:.2f}" for t in bot))
    # denominators differ -> the belt denominator itself moves between runs
    dens=[x[3] for x in v]
    print(f"   e500 DENOMINATOR (homes within 500 m of burn) varies: min={min(dens)} max={max(dens)} spread={max(dens)-min(dens)} homes")
    viols=[x[4] for x in v if x[4] is not None]
    print(f"   runs with viol>0: {sum(1 for x in viols if x)}/{len(viols)}")
