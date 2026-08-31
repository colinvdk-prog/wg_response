import sys, json, os, glob, collections, statistics
sys.path.insert(0, r'C:\Users\colin\wg_response')
from wgresponse.fuels import dead_fuel_moisture_frac
print("=== Simard EMC*1.03 dead fuel moisture vs RH (temp 25C) ===")
print(f"{'RH%':>5} {'m_dead':>8}   binds-extinction-for")
for rh in (10,20,30,40,50,60,70,75,80,85,90,95,100):
    m = dead_fuel_moisture_frac(25.0, rh)
    who=[]
    if m>0.15: who.append('GR/GS(mx.15)')
    if m>0.20: who.append('TL3(mx.20)')
    if m>0.25: who.append('TU5(mx.25)')
    print(f"{rh:5d} {m:8.4f}   {', '.join(who) if who else '-'}")
# find crossing RH
import bisect
for mx,lbl in ((0.15,'GR/GS'),(0.20,'TL3'),(0.25,'TU5')):
    lo,hi=0.0,100.0
    for _ in range(60):
        mid=(lo+hi)/2
        if dead_fuel_moisture_frac(25.0,mid)>mx: hi=mid
        else: lo=mid
    print(f"  -> {lbl} extinction (mx={mx}) crossed at RH = {hi:.1f}% (T=25C)")
for T in (5,10,15,30,40):
    lo,hi=0.0,100.0
    for _ in range(60):
        mid=(lo+hi)/2
        if dead_fuel_moisture_frac(T,mid)>0.15: hi=mid
        else: lo=mid
    print(f"  -> GR/GS extinction crossed at RH = {hi:.1f}%  (T={T}C)")

print("\n=== AUGUST CORPUS: hourly RH from cached ERA5, fraction above extinction ===")
D=r'C:\Users\colin\wg_response\scratch_review\hindcast_aug_data\weather'
allm=[]
for p in sorted(glob.glob(os.path.join(D,'*.json'))):
    wd=json.load(open(p))
    h=wd['payload'].get('hourly',{})
    rhs=h.get('relative_humidity_2m') or h.get('relativehumidity_2m') or []
    ts=h.get('temperature_2m') or []
    ms=[dead_fuel_moisture_frac(t,r) for t,r in zip(ts,rhs) if t is not None and r is not None]
    if not ms: continue
    allm+=ms
    ab15=100*sum(1 for m in ms if m>0.15)/len(ms)
    ab20=100*sum(1 for m in ms if m>0.20)/len(ms)
    print(f"  {os.path.basename(p):<26} n={len(ms):5d}  m_dead med={statistics.median(ms):.3f} max={max(ms):.3f}  >mx0.15: {ab15:5.1f}%  >mx0.20: {ab20:5.1f}%")
print(f"\n  POOLED n={len(allm)}  median={statistics.median(allm):.4f}  mean={statistics.mean(allm):.4f}")
for mx in (0.15,0.20,0.25):
    print(f"  fraction of cached August fire-hours with m_dead > {mx}: {100*sum(1 for m in allm if m>mx)/len(allm):.2f}%")
