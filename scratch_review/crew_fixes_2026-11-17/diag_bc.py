"""Beaver Creek 24-h canonical extreme (page settings): at sim-g1's 28a
decision (T+5.75 h) list every candidate refuge and whether it keeps a way
out (SEMANTICS 46b terms). NOT product code.

Usage: python diag_bc.py [MAX_NODES]
"""
from __future__ import annotations

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)


def main(argv):
    import matrix
    from wgresponse import simui, geo
    simui.REFUGE_EXIT_MAX_PROBES = 10 ** 6
    mgr, s = matrix.build("beaver_creek_co", "standard", "canon", "extreme")
    t0 = s._now_s
    S = type(s)
    orig = S._durable_egress
    hits = []

    def wrapped(self, cs, t_s, lead_s, dur_s):
        if cs.crew.crew_id == "sim-g1" and 5.4 <= (t_s - t0) / 3600.0 <= 6.1:
            hits.append(1)
            print(f"--- 28a at {(t_s - t0) / 3600.0:.2f} h, pos {cs.pos}, target "
                  f"{cs.egress_zone_id}", flush=True)
            le = self._cfg.escape_last_exit_lead_min * 60.0
            for z in sorted(self._loc.safe_zones, key=lambda q: q.zone_id):
                b, w, d, dm, tr = self._leg_verdict(cs, z.loc, t_s)
                ku = self._dest_keep_until(z, cs.speed)
                own = self._dest_durable_until(z.loc)
                ex = self._dest_exit_until(z, cs.speed)
                keeps = self._refuge_keeps_exit(z.loc, t_s + d, cs.speed,
                                                zone_id=z.zone_id)

                def h(v):
                    return None if math.isinf(v) else round((v - t_s) / 3600.0, 2)
                print(f"  zone {z.zone_id}: burned {b} worst {w / 60:.1f} min drive "
                      f"{d / 60:.1f} dest {dm / 3600:.2f} h | KU {h(ku)} I {h(own)} "
                      f"E {h(ex)} keeps {keeps}", flush=True)
            rec = []

            def accept(loc, tot):
                arr = t_s + tot
                z = self._spot_zone(loc)
                ku = self._dest_keep_until(z, cs.speed)
                own = self._dest_durable_until(loc)
                ex = self._dest_exit_until(z, cs.speed)
                _n, p10 = self._exposure_at(loc, t_s)
                keeps = (math.isinf(ku) or (ku - arr > le + lead_s
                                            and ex >= own - le))
                rec.append((round(geo.haversine_m(cs.pos, loc)), round(tot / 60, 1),
                            None if math.isinf(p10) else round((p10 - t_s) / 3600, 2),
                            None if math.isinf(ku) else round((ku - t_s) / 3600, 2),
                            None if math.isinf(own) else round((own - t_s) / 3600, 2),
                            None if math.isinf(ex) else round((ex - t_s) / 3600, 2),
                            self._on_map(loc), keeps, (round(loc.lat, 5), round(loc.lon, 5))))
                return False
            buf = self._cfg.egress_buffer_min * 60.0
            self._road_escape_search(cs.pos, t_s, cs.speed, lead_s, lawful_only=True,
                                     min_p10_s=t_s + dur_s + buf,
                                     on_map_only=self._refuge_on_map(),
                                     accept=accept)
            print("  road candidates (dist m, drive min, p10 h, KU h, I h, E h, on_map, keeps, loc):")
            for r in rec:
                print("   ", r, flush=True)
            print(f"  {len(rec)} candidates, {sum(1 for r in rec if r[7])} keep a way out")
        return orig(self, cs, t_s, lead_s, dur_s)
    S._durable_egress = wrapped
    for k in range(26):
        s.tick()
    print("hits", len(hits))


if __name__ == "__main__":
    main(sys.argv[1:])
