import json,glob,os,statistics,collections
D=r'C:\Users\colin\wg_response\scratch_review'
for f in sorted(glob.glob(os.path.join(D,'rsf_city_*.json')))+sorted(glob.glob(os.path.join(D,'rsf_grid_*.json'))):
    j=json.load(open(f))
    if not isinstance(j,dict): print(os.path.basename(f), type(j), len(j)); continue
    ks=list(j.keys())
    print(f"\n--- {os.path.basename(f)}  keys={ks[:8]}")
    if 'rows' in j and isinstance(j['rows'],list) and j['rows'] and isinstance(j['rows'][0],dict):
        r0=j['rows'][0]; print("    row keys:", sorted(r0.keys())[:18])
        # find a survivability-ish field
        for key in ('belt_pct','e500','survival_pct','saved_pct','pct','coated_pct'):
            if key in r0: 
                vals=[x[key] if not isinstance(x[key],list) else x[key][-1] for x in j['rows']]
                print(f"    {key}: n={len(vals)} min={min(vals):.2f} max={max(vals):.2f} med={statistics.median(vals):.2f} sd={statistics.pstdev(vals):.2f}")
    if 'stats' in j: print("    stats:", json.dumps(j['stats'])[:400])
