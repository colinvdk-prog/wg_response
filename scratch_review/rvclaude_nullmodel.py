"""ADVERSARIAL: does the Rothermel/Albini physics beat an isotropic dilation
of the T0 perimeter, at EQUAL area over-prediction? Scores a physics-free null
on the exact August corpus, same masks, same burnable filter, same pooling."""
import sys, os, json, math, statistics, collections
sys.path.insert(0, r'C:\Users\colin\wg_response')
from tools import hindcast_lib as H
from tools.hindcast_harness import _incident_domain, _load_all_incidents, _read_json
from wgresponse.contracts import LatLon, ResponseConfig
from wgresponse.terrain import LfpsTerrain, load_ascii_tile
from wgresponse.spread import _perimeter_interior_cells
from wgresponse.rothermel import is_burnable

DATA = r'C:\Users\colin\wg_response\scratch_review\hindcast_aug_data'
INF = float('inf')

def edt2(binary, nrows, ncols):
    """Exact squared Euclidean distance transform (Felzenszwalb-Huttenlocher).
    binary[i] True = source (distance 0)."""
    f = [0.0 if binary[i] else INF for i in range(nrows*ncols)]
    # pass 1: columns
    for c in range(ncols):
        d = _dt1([f[r*ncols+c] for r in range(nrows)])
        for r in range(nrows): f[r*ncols+c] = d[r]
    # pass 2: rows
    for r in range(nrows):
        d = _dt1(f[r*ncols:(r+1)*ncols])
        f[r*ncols:(r+1)*ncols] = d
    return f

def _dt1(fv):
    n = len(fv); d=[0.0]*n; v=[0]*n; z=[0.0]*(n+1)
    k=0; v[0]=0; z[0]=-INF; z[1]=INF
    for q in range(1,n):
        if fv[q]==INF: continue
        while True:
            p=v[k]
            if fv[p]==INF: 
                if k>0: k-=1; continue
                else: break
            s=((fv[q]+q*q)-(fv[p]+p*p))/(2.0*q-2.0*p)
            if s<=z[k] and k>0: k-=1
            else: break
        p=v[k]
        s = -INF if fv[p]==INF else ((fv[q]+q*q)-(fv[p]+p*p))/(2.0*q-2.0*p)
        k+=1; v[k]=q; z[k]=s; z[k+1]=INF
    k=0
    for q in range(n):
        while z[k+1]<q: k+=1
        p=v[k]
        d[q]= INF if fv[p]==INF else (q-p)*(q-p)+fv[p]
    return d

# which pairs were scored?
rep = json.load(open(os.path.join(DATA,'aug_replay.json')))
scored = {(r['name'], r['t0_s'], r['t1_s']): r for r in rep['rows']
          if r['variant']=='as-deployed' and r['n_new_burnable']>0}
print(f"scored pairs to reproduce: {len(scored)}")
cfg0 = ResponseConfig()
RADII_M = [0,250,500,750,1000,1500,2000,2500,3000,4000,5000,6000,8000,10000,12000,15000,20000,30000]
agg = {R: {'cov':0,'area':0} for R in RADII_M}
tot_new = 0
eng_cov = 0; eng_area_cells = 0
per_pair=[]
for slug, inc in _load_all_incidents(DATA):
    bbox, cell_m = _incident_domain(inc, cfg0)
    lfps = LfpsTerrain(cache_dir=os.path.join(DATA,'lfps'))
    cp = lfps.cache_path(bbox, cell_m)
    if not (cp and os.path.exists(cp)): continue
    tile = load_ascii_tile(cp)
    spec = tile.spec; nr, nc = spec.nrows, spec.ncols
    fdata = tile.fuel.data
    burn = [is_burnable(int(round(fdata[i]))) for i in range(nr*nc)]
    for a,b in zip(inc.snapshots, inc.snapshots[1:]):
        key=(inc.name, a.t_s, b.t_s)
        if key not in scored: continue
        row = scored[key]
        rings=[H.subsample_ring(r, H.MAX_RING_VERTS) for r in a.rings]
        t0mask=set(_perimeter_interior_cells(spec, rings))
        t1mask=H.polygon_interior_mask(spec, b.rings)
        new=[i for i in (t1mask-t0mask) if burn[i]]
        if not new: continue
        src=[False]*(nr*nc)
        for i in t0mask: src[i]=True
        d2=edt2(src,nr,nc)
        tot_new += len(new)
        eng_cov += row['covered_possible']
        # engine's predicted-new-cell count (from its area, back out cells)
        cell_ac = cell_m*cell_m/4046.8564224
        eng_area_cells += row['modeled_new_area_ac_p50']/cell_ac
        pp={}
        for R in RADII_M:
            rc=(R/cell_m)**2
            cov=sum(1 for i in new if d2[i]<=rc)
            area=sum(1 for i in range(nr*nc) if d2[i]<=rc and burn[i] and i not in t0mask)
            agg[R]['cov']+=cov; agg[R]['area']+=area
            pp[R]=(cov,area)
        per_pair.append((inc.name, row['dt_h'], len(new), row['covered_possible'], row['modeled_new_area_ac_p50']/cell_ac, pp, cell_m))
        print(f"  {inc.name:<16} +{row['dt_h']:5.1f}h new={len(new):6d}  engine cov={100*row['covered_possible']/len(new):6.2f}% area_cells={row['modeled_new_area_ac_p50']/cell_ac:9.0f}", flush=True)

print("\n================ POOLED (cell-weighted, same as harness) ================")
print(f"ENGINE (as-deployed): coverage = {100*eng_cov/tot_new:.2f}%   predicted-new cells = {eng_area_cells:.0f}  = {eng_area_cells/tot_new:.2f}x the observed new-burn cells")
print(f"\nNULL MODEL = isotropic dilation of the T0 perimeter by R metres (NO weather, NO fuel, NO terrain, NO Rothermel):")
print(f"  {'R (m)':>7} {'coverage':>10} {'pred cells':>12} {'x observed':>11}")
for R in RADII_M:
    a=agg[R]
    print(f"  {R:7d} {100*a['cov']/tot_new:9.2f}% {a['area']:12d} {a['area']/tot_new:10.2f}x")
# interpolate: null coverage at the engine's area budget
xs=[agg[R]['area']/tot_new for R in RADII_M]; ys=[100*agg[R]['cov']/tot_new for R in RADII_M]
tgt=eng_area_cells/tot_new
for i in range(1,len(xs)):
    if xs[i-1]<=tgt<=xs[i]:
        f=(tgt-xs[i-1])/(xs[i]-xs[i-1]); nullc=ys[i-1]+f*(ys[i]-ys[i-1])
        print(f"\n>>> At the ENGINE's own area budget ({tgt:.2f}x observed), the NULL scores {nullc:.2f}% coverage vs the engine's {100*eng_cov/tot_new:.2f}%.")
        break
# area the null needs to match engine coverage
ec=100*eng_cov/tot_new
for i in range(1,len(ys)):
    if ys[i-1]<=ec<=ys[i]:
        f=(ec-ys[i-1])/(ys[i]-ys[i-1]); na=xs[i-1]+f*(xs[i]-xs[i-1])
        print(f">>> To MATCH the engine's {ec:.2f}% coverage the null needs {na:.2f}x observed area vs the engine's {tgt:.2f}x  -> engine skill ratio = {na/tgt:.2f}x")
        break
json.dump({'tot_new':tot_new,'eng_cov':eng_cov,'eng_area_cells':eng_area_cells,
           'null':{str(R):agg[R] for R in RADII_M}}, open(os.path.join(r'C:\Users\colin\wg_response\scratch_review','rvclaude_nullmodel.json'),'w'), indent=1)
