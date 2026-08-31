import json,glob,os,collections,statistics
D=r'C:\Users\colin\wg_response\scratch_review'
def load(n):
    p=os.path.join(D,n)
    return json.load(open(p)) if os.path.exists(p) else None
names=['opt_ember_te_a_c4','opt_ember_te_a_c8','opt_ember_te_b_c4','opt_ember_te_b_c8','opt_ember_te_c_c8',
       'opt_ember_tehy_a_c4','opt_ember_tehy_a_c8','opt_ember_tehy_b_c8',
       'opt_ember_rad550_c4','opt_ember_rad550_c8','opt_ember_rad600_c4','opt_ember_rad600_c8',
       'opt_ember_hy20_c8','opt_ember_s_c4','opt_ember_s_c8','opt_ember_cb_c4','opt_ember_cb_c8',
       'opt_ember_flip_c4','opt_ember_flip_c4b','opt_ember_flip_c8','opt_ember_sem10e_screen_c8']
print(f"{'run':<28} {'suite':<7} {'n':>3} {'crews':>5} {'tiered':>18} {'hyst':>5} {'cb':>3} {'e500%':>7} {'e500 n/d':>12} {'viol':>5} {'ovtk':>5} {'coated':>7}")
rows=[]
for n in names:
    j=load(n+'.json')
    if not j: print(f"{n:<28} (missing)"); continue
    s=j['stats']; p=j['params']
    e=s.get('e500',[0,0,0])
    print(f"{n:<28} {j.get('suite'):<7} {len(j.get('rows',[])):>3} {str(p.get("crews")):>5} {str(p.get('tiered'))[:18]:>18} {str(p.get("escape_hyst")):>5} {str(p.get("cold_black")):>3} {e[2]:>7.2f} {str(e[0])+'/'+str(e[1]):>12} {str(s.get('viol')):>5} {str(s.get('ov_ticks')):>5} {str(s.get('coated_total')):>7}")
    rows.append((n,j,p,s,e))
print("\n--- MATCHED PAIRS (identical params except the named knob) ---")
for i in range(len(rows)):
    for k in range(i+1,len(rows)):
        n1,j1,p1,s1,e1=rows[i]; n2,j2,p2,s2,e2=rows[k]
        if j1.get('suite')!=j2.get('suite') or len(j1['rows'])!=len(j2['rows']): continue
        diff={kk for kk in set(p1)|set(p2) if p1.get(kk)!=p2.get(kk)}
        if len(diff)==1 and e1[1]==e2[1]:
            kk=diff.pop()
            print(f"  {kk:<16} {p1.get(kk)!s:>10} -> {p2.get(kk)!s:<10}  e500 {e1[2]:6.2f} -> {e2[2]:6.2f}  delta={e2[2]-e1[2]:+6.2f}pp  (denominator {e1[1]} homes; 1 home = {100/e1[1]:.2f}pp)   viol {s1.get('viol')}->{s2.get('viol')}   [{n1} vs {n2}]")
