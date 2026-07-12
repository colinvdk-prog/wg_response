"""DIAGNOSIS ONLY — does homes-mode dispatch order SAVABLE homes by URGENCY?

Three deterministic 1-crew / 2-home constructions on a trivially-safe corridor
(uniform UNREACHED raster) so ONLY deadlines + the objective bind. homes-mode is
the LIVE default (contracts.dispatch_objective='homes').

  U = urgent  (fire arrives SOON, savable now, tight window)
  C = comfortable (fire arrives LATE, savable now AND for many more hours)

TEST 1  both serveable by one crew, both visit-orders feasible: does the crew GEL
        THE URGENT ONE FIRST?
TEST 2  mutual exclusion (crew can reach only one in time), U cheaper, C pricier:
        which home does homes-mode protect?  (the founder's c47/c44 inversion)
TEST 3  same exclusion but EQUAL value: with the $-tiebreak neutralised, does the
        engine fall back to urgency, or to something urgency-blind?
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diag_prio_common import (  # noqa: E402
    NOW, cc, describe, make_ens, mk_crew, mk_job, served_order, served_set,
    spec_big, tt_min, uniform,
)
from wgresponse import dispatch  # noqa: E402
from wgresponse.contracts import UNREACHED, ResponseConfig, SafeZone  # noqa: E402

H = 3600.0


def run(label, cfg, crew_loc, jobs, zones, spec, incumbent=None):
    ens = {"inc1": make_ens(uniform(spec, UNREACHED))}
    crew = mk_crew("G1", crew_loc)
    plan = dispatch.plan([crew], list(jobs), ens, zones, cfg, NOW,
                         incumbent=incumbent)
    print(f"\n--- {label} (objective={cfg.dispatch_objective!r}) ---")
    print(describe(plan, jobs))
    return plan


def main():
    cfg = ResponseConfig()                 # dispatch_objective='homes' (default)
    spec = spec_big()
    deploy_s = cfg.deploy_min * 60.0
    egr = (cfg.egress_buffer_min + cfg.ember_buffer_min) * 60.0
    slack = cfg.slack_min * 60.0

    # ---- TEST 1 : within-route ordering (both served) --------------------
    # U and C on the SAME (east) side, adjacent, both easily reachable by one
    # crew in EITHER order; U burns soon, C burns late. Which is gelled first?
    crew_loc = cc(spec, 20, 30)
    U_loc = cc(spec, 20, 40)               # ~10 km E
    C_loc = cc(spec, 21, 40)               # ~10 km E, one row south (adjacent)
    tcu = tt_min(crew_loc, U_loc, cfg)
    tuc = tt_min(U_loc, C_loc, cfg)
    print(f"TEST1 travel min: crew->U {tcu:.1f}  U<->C {tuc:.1f}  "
          f"(both orders feasible by construction)")
    # U urgent: p50 = now+6h, p10 = now+5h.  C comfortable: p50 = now+30h.
    U = mk_job("U_urgent", U_loc, NOW + 5 * H, NOW + 6 * H, value=1_000_000.0)
    C = mk_job("C_comfort", C_loc, NOW + 27 * H, NOW + 30 * H, value=1_400_000.0)
    p1 = run("TEST1 within-route order (U soon / C late, both served)",
             cfg, crew_loc, [U, C], [SafeZone("Z1", cc(spec, 20, 35))], spec)
    order = served_order(p1).get("G1", [])
    if set(order) == {"U_urgent", "C_comfort"}:
        first = order[0]
        print(f"  >> both served; GELLED FIRST = {first}  "
              f"({'URGENT-FIRST (correct)' if first == 'U_urgent' else 'COMFORT-FIRST (urgency-blind)'})")
    else:
        print(f"  >> served order: {order}")

    # ---- TEST 2 : GENUINE mutual exclusion, U more-urgent / C less-urgent
    # crew far SOUTH, U far WEST, C far EAST. Big cross(U,C) so that after
    # serving either DIRECTLY, the second's deadline is missed BOTH ways ->
    # crew protects exactly ONE. U is strictly more urgent than C (sooner
    # deadline) yet cheaper. Mirrors the founder's inversion: c47 (p10 36h,
    # $2.35M) gelled while c44 (p10 23h, $1.4M) NOT.
    crew_loc = cc(spec, 34, 30)            # south-center
    U_loc = cc(spec, 20, 8)                # far NW
    C_loc = cc(spec, 20, 52)               # far NE
    tU = tt_min(crew_loc, U_loc, cfg) * 60.0
    tC = tt_min(crew_loc, C_loc, cfg) * 60.0
    tcross = tt_min(U_loc, C_loc, cfg) * 60.0
    # U urgent: latest_depart just feasible direct with the slack floor + a hair.
    U_latest_depart = NOW + tU + deploy_s + slack + 120.0
    U_p10 = U_latest_depart + egr
    U_p50 = U_p10 + 1 * H                   # SOON
    # C less urgent: latest_depart later than U's, but still tight enough that a
    # SECOND crossing after serving U overruns it (exclusion), while a direct
    # crew->C fits comfortably.
    C_latest_depart = NOW + tU + deploy_s + tcross + deploy_s - 1200.0  # 20m short
    C_p10 = C_latest_depart + egr
    C_p50 = C_p10 + 3 * H                   # noticeably later than U
    # exclusivity proof: each alone fits; the cross strands the 2nd EITHER way.
    u_alone = NOW + tU + deploy_s <= U_latest_depart - slack + 1e-6
    c_alone = NOW + tC + deploy_s <= C_latest_depart - slack + 1e-6
    uc = (NOW + tU + deploy_s + tcross + deploy_s) > C_latest_depart + 1e-6
    cu = (NOW + tC + deploy_s + tcross + deploy_s) > U_latest_depart + 1e-6
    print(f"\nTEST2 travel s: crew->U {tU:.0f}  crew->C {tC:.0f}  cross {tcross:.0f}")
    print(f"  U_alone_fits={u_alone} C_alone_fits={c_alone} "
          f"U-then-C_misses_C={uc} C-then-U_misses_U={cu} "
          f"EXCLUSIVE={u_alone and c_alone and uc and cu}")
    print(f"  urgency: U latest_depart +{(U_latest_depart-NOW)/60:.0f}m (p50 +{(U_p50-NOW)/3600:.1f}h) "
          f"vs C latest_depart +{(C_latest_depart-NOW)/60:.0f}m (p50 +{(C_p50-NOW)/3600:.1f}h)")
    U = mk_job("U_urgent_cheap", U_loc, U_p10, U_p50, value=1_000_000.0)
    C = mk_job("C_comfort_rich", C_loc, C_p10, C_p50, value=1_400_000.0)
    p2 = run("TEST2 exclusion: U(soon,$1.0M) XOR C(late,$1.4M)",
             cfg, crew_loc, [U, C], [SafeZone("Z1", cc(spec, 20, 30))], spec)
    s2 = served_set(p2)
    pick = "U_urgent_cheap" if "U_urgent_cheap" in s2 else (
        "C_comfort_rich" if "C_comfort_rich" in s2 else "NONE")
    print(f"  >> protected = {pick}  "
          f"({'URGENT (correct)' if pick.startswith('U') else 'COMFORT — urgency INVERSION' if pick.startswith('C') else '??'})")

    # ---- TEST 3 : same exclusion, EQUAL value ----------------------------
    U = mk_job("U_urgent_eq", U_loc, U_p10, U_p50, value=1_200_000.0)
    C = mk_job("C_comfort_eq", C_loc, C_p10, C_p50, value=1_200_000.0)
    p3 = run("TEST3 exclusion, EQUAL $1.2M: U(soon) XOR C(late)",
             cfg, crew_loc, [U, C], [SafeZone("Z1", cc(spec, 20, 30))], spec)
    s3 = served_set(p3)
    pick3 = "U_urgent_eq" if "U_urgent_eq" in s3 else (
        "C_comfort_eq" if "C_comfort_eq" in s3 else "NONE")
    print(f"  >> protected = {pick3}  "
          f"({'URGENT (correct)' if pick3.startswith('U') else 'COMFORT — urgency-blind' if pick3.startswith('C') else '??'})")

    # ---- TEST 2v : value mode, for contrast ------------------------------
    cfg_v = ResponseConfig(dispatch_objective="value")
    U = mk_job("U_urgent_cheap", U_loc, U_p10, U_p50, value=1_000_000.0)
    C = mk_job("C_comfort_rich", C_loc, C_p10, C_p50, value=1_400_000.0)
    p2v = run("TEST2v (value mode) U($1.0M) XOR C($1.4M)",
              cfg_v, crew_loc, [U, C], [SafeZone("Z1", cc(spec, 20, 30))], spec)
    s2v = served_set(p2v)
    print(f"  >> protected = {'U' if 'U_urgent_cheap' in s2v else 'C' if 'C_comfort_rich' in s2v else 'NONE'}")


if __name__ == "__main__":
    main()
