"""Print one fire's step table from results_<key>.json (inspection aid)."""
import json
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))


def _f(v, fmt="{:.2f}"):
    return "-" if v is None else fmt.format(v)


def main(key: str) -> None:
    d = json.load(open(os.path.join(_HERE, f"results_{key}.json")))
    print(d["name"], "cell", round(d["cell_m"]), "m, tile", d["tile_shape"])
    print(" k  date   ph   dt  gis0->gis1        obsgrow  | phys: grow  logA  h10  h50 edge | bel: grow  logA  h10g h10u h50 | state  m10  m50  m90 rap hnew app | win: obs  phys  belief50 belief90 surpr")
    for s in d["steps"]:
        t = datetime.fromtimestamp(s["t0_s"], tz=timezone.utc)
        b = s["belief_at_k"]
        w = s.get("window_k_to_k1") or {}
        line = (f"{s['k']:2d} {t:%m-%d} {s['phase'][:4]} {s['dt_h']:4.0f} "
                f"{s['gis0_ac']:>8,.0f}->{s['gis1_ac']:>8,.0f} "
                f"{s['gis1_ac'] - s['gis0_ac']:>+8,.0f} |")
        p, g, u = s.get("phys"), s.get("bel_g"), s.get("bel_u")
        if p:
            line += (f" {p['grow_p50_ac']:>8,.0f} {_f(p['log_area_p50'])} "
                     f"{_f(p['hit_p10'])} {_f(p['hit_p50'])} "
                     f"{'E' if p['edge_p50'] else '.'} |"
                     f" {g['grow_p50_ac']:>8,.0f} {_f(g['log_area_p50'])} "
                     f"{_f(g['hit_p10'])} {_f(u['hit_p10'])} {_f(g['hit_p50'])} |")
        else:
            line += f" unscored {s['unscored_why']} |"
        line += (f" {b['state'][:5]:5s} {_f(b['mult_p10'])} {_f(b['mult_p50'])} "
                 f"{_f(b['mult_p90'])} {'R' if b['rapid'] else '.'} "
                 f"{_f(b['h_new'], '{:d}')} {'A' if b['applied'] else '.'} |")
        if w:
            line += (f" {w.get('obs_growth_ac', 0):>+8,.0f} "
                     f"{_f(w.get('phys_nominal_growth_ac'), '{:>8,.0f}')} "
                     f"{_f(w.get('pred50'), '{:>8,.0f}')} "
                     f"{_f(w.get('pred90'), '{:>8,.0f}')} "
                     f"{'S' if w.get('surprise') else '.'}"
                     f"{' cens' if w.get('cens') else ''}")
        print(line)


if __name__ == "__main__":
    main(sys.argv[1])
