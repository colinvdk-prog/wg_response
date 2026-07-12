"""attack_r5_monitoring — monitoring vs not_threatened label (task 5).

Claims under attack, checked for EVERY client EVERY tick:
  * a home the forecast projects a finite arrival for (finite eta_p50), still
    un-jobbed, is 'monitoring' (NOT 'not_threatened')
  * a home with NO projected arrival (eta_p50 None) stays 'not_threatened'
  * the tag never contradicts the displayed eta_p50_s
  * scoreboard.outcomes sums to len(clients)
Plus a positive existence proof: a 72-96h-ETA home is tagged 'monitoring'.
READ-ONLY; in-process only.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from attack_r5_common import make_session, geo                            # noqa
from wgresponse.simui import LOCATIONS                                    # noqa


def run(loc, nc, ncrew, km, seed, physics, n_ticks=140):
    pt = geo.destination(LOCATIONS[loc].center, LOCATIONS[loc].hazard_wind_dir_deg,
                         km * 1000.0)
    s = make_session(loc, nc, ncrew, seed=seed, fire_zone="extended",
                     physics=physics)
    s.ignite(pt.lat, pt.lon, intensity="high")
    contradictions = []
    sum_fails = 0
    monitoring_seen = 0
    monitoring_72_96 = 0
    for tk in range(1, n_ticks + 1):
        st = s.tick()
        now = s._now_s
        clients = st["clients"]
        # 1/2/3: per-client tag vs displayed eta_p50
        for c in clients:
            tag = c["outcome"]
            eta50 = c["eta_p50_s"]
            has_eta = eta50 is not None
            if tag == "not_threatened" and has_eta:
                contradictions.append(
                    f"t{tk} {c['client_id']} not_threatened but eta_p50={eta50}")
            if tag == "monitoring" and not has_eta:
                contradictions.append(
                    f"t{tk} {c['client_id']} monitoring but eta_p50=None")
            if tag == "monitoring":
                monitoring_seen += 1
                if has_eta:
                    eta_h = (eta50 - now) / 3600.0
                    if 72.0 <= eta_h <= 96.0:
                        monitoring_72_96 += 1
        # 4: outcomes sum to len(clients)
        outs = st["scoreboard"]["outcomes"]
        if sum(outs.values()) != len(clients):
            sum_fails += 1
    return {
        "loc": loc, "seed": seed, "physics": physics,
        "contradictions": contradictions, "sum_fails": sum_fails,
        "monitoring_seen": monitoring_seen, "monitoring_72_96": monitoring_72_96,
    }


def main():
    print("=" * 78)
    print("TASK 5 — monitoring vs not_threatened label")
    print("=" * 78)
    configs = [
        ("paradise_ca", 64, 4, 12.0),   # far fire on extended -> many 72-96h ETAs
        ("malibu_ca",   60, 4, 12.0),
        ("boulder_co",  60, 4, 12.0),
    ]
    total_contra = 0
    total_sumfail = 0
    total_72_96 = 0
    for (loc, nc, ncrew, km) in configs:
        for physics in (False, True):
            r = run(loc, nc, ncrew, km, seed="mon-1", physics=physics)
            nc_ = len(r["contradictions"])
            total_contra += nc_
            total_sumfail += r["sum_fails"]
            total_72_96 += r["monitoring_72_96"]
            print(f"  {loc:>12} phys={str(physics):>5}: "
                  f"tag/eta contradictions={nc_:>3}  outcome-sum fails="
                  f"{r['sum_fails']:>3}  monitoring-tag ticks={r['monitoring_seen']:>4}"
                  f"  of which 72-96h ETA={r['monitoring_72_96']:>4}")
            for x in r["contradictions"][:4]:
                print(f"      - {x}")
    print("\n" + "-" * 78)
    print(f"  total tag/eta contradictions: {total_contra}   (MUST be 0)")
    print(f"  total outcome-sum failures:   {total_sumfail}   (MUST be 0)")
    print(f"  monitoring homes at 72-96h ETA observed: {total_72_96}   (want >0)")
    ok = (total_contra == 0 and total_sumfail == 0 and total_72_96 > 0)
    print("\n" + "=" * 78)
    print(f"TASK 5 (monitoring label) OVERALL: {'PASS' if ok else 'FAIL'}")
    print("=" * 78)


if __name__ == "__main__":
    main()
