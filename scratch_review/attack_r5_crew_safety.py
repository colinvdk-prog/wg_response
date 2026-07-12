"""attack_r5_crew_safety — THE HEADLINE GATE (task 1).

Reproduce the founder's g1/g2 bug scenario (Paradise extended, 5 crews, extreme
fire) but with PHYSICS ON (grass_realism + spotting, the sim default) across many
seeds + Malibu/Boulder, and assert the hard invariant EVERY tick:

  no crew ever ends idle/parked in a burned-or-imminent cell without either
  (a) relocating to safety first, or
  (b) the audit reporting violations>0 AND a crew_overruns row (never hidden 0).

Then construct an UNAVOIDABLE fire encirclement and confirm the fail-closed
alarm + honest violation (never a hidden 0). READ-ONLY; in-process only.
Parallelised across processes (24 cores); each worker runs one (tile,seed).
"""
from __future__ import annotations

import sys
from pathlib import Path
from multiprocessing import Pool

sys.path.insert(0, str(Path(__file__).resolve().parent))
from attack_r5_common import (make_session, drive, check_invariant,      # noqa
                              engine_exposure, ignite_point, geo, INF)
from wgresponse.simui import LOCATIONS                                    # noqa
from wgresponse.contracts import CrewStatus                              # noqa


def upwind_point(loc_key, km=10.0):
    loc = LOCATIONS[loc_key]
    return geo.destination(loc.center, loc.hazard_wind_dir_deg, km * 1000.0)


SCENARIOS = [
    # (location, n_clients, n_crews, upwind_km, intensity)
    ("paradise_ca", 64, 5, 8.0, "extreme"),   # founder's exact geometry
    ("malibu_ca",   48, 5, 9.0, "extreme"),
    ("boulder_co",  48, 5, 9.0, "extreme"),
]
# per-seed (bearing_offset_deg, dkm) jitter so EACH seed is a genuinely different
# fire (the ensemble is seeded on incident_id, so distinct fire geometry can only
# come from a distinct ignition point) AND a different crew/client layout.
SEEDS = ["caught-1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "founder-9"]
_JITTER = [(0.0, 0.0), (18.0, 1.0), (-18.0, -1.0), (32.0, 2.0), (-32.0, 2.5),
           (10.0, -2.0), (-10.0, 3.0), (24.0, -1.5), (-24.0, 1.5)]
MAX_TICKS = 170


def _one_run(args):
    loc, nc, ncrew, km, inten, seed, jit = args
    dbrg, dkm = jit
    s = make_session(loc, n_clients=nc, n_crews=ncrew, seed=seed,
                     fire_zone="extended", physics=True)
    brg = (LOCATIONS[loc].hazard_wind_dir_deg + dbrg) % 360.0
    pt = ignite_point(s, bearing_deg=brg, km=km + dkm)
    r = drive(s, [(pt.lat, pt.lon, inten, 0)], MAX_TICKS, invariant=True)
    fail_lines = []
    for tk, findings, stats in r["fails"][:3]:
        fail_lines.append(f"tick {tk}: {stats}")
        fail_lines.extend(f"  - {f}" for f in findings[:4])
    return {
        "loc": loc, "seed": seed, "n_fails": len(r["fails"]),
        "exposed_ticks": r["exposed_ticks"], "max_overruns": r["max_overruns"],
        "final_reloc": r["final_reloc"], "ever_burned": r["ever_burned"],
        "alarms": r["alarm_notes"], "fail_lines": fail_lines,
    }


def run_multi():
    print("=" * 78)
    print("PART 1 — physics-ON founder repro, multi-seed x 3 tiles, every-tick invariant")
    print(f"  ({len(SEEDS)} seeds x {len(SCENARIOS)} tiles, {MAX_TICKS} ticks each, "
          f"grass+spotting ON)")
    print("=" * 78)
    jobs = [(loc, nc, ncrew, km, inten, seed, _JITTER[i])
            for (loc, nc, ncrew, km, inten) in SCENARIOS
            for i, seed in enumerate(SEEDS)]
    with Pool(processes=min(12, len(jobs))) as pool:
        results = pool.map(_one_run, jobs)
    grand_fail = 0
    by_loc = {}
    for res in results:
        by_loc.setdefault(res["loc"], []).append(res)
    for loc in [s[0] for s in SCENARIOS]:
        print(f"\n### {loc}  physics=ON")
        print(f"{'seed':>10} | {'exp.ticks':>9} {'maxOvr':>6} {'reloc':>5} "
              f"{'burned?':>7} {'alarms':>6} | verdict")
        for res in by_loc[loc]:
            v = "OK" if res["n_fails"] == 0 else f"FAIL x{res['n_fails']}"
            grand_fail += res["n_fails"]
            print(f"{res['seed']:>10} | {res['exposed_ticks']:>9} "
                  f"{res['max_overruns']:>6} {res['final_reloc']:>5} "
                  f"{str(res['ever_burned']):>7} {res['alarms']:>6} | {v}")
            for line in res["fail_lines"]:
                print(f"            {line}")
    print(f"\n>>> PART 1 total invariant failures across all seeds/tiles: {grand_fail}")
    return grand_fail


def run_encirclement():
    """Construct genuinely-inescapable encirclement: main fire + fires ignited
    ON all three safe zones + center once crews have staged. Verify (1) fail-
    closed alarm fires, (2) once crews are physically burned, violations>0 with
    crew_overrun rows, (3) the invariant NEVER hides a zero throughout."""
    print("\n" + "=" * 78)
    print("PART 2 — UNAVOIDABLE encirclement: fires ignited ON every safe zone")
    print("=" * 78)
    loc = "paradise_ca"
    zones = LOCATIONS[loc].safe_zones
    pt = upwind_point(loc, 6.0)
    s = make_session(loc, n_clients=40, n_crews=5, seed="encircle-1",
                     fire_zone="extended", physics=True)
    s.ignite(pt.lat, pt.lon, intensity="extreme")

    ZONE_STRIKE_TICK = 18
    struck = False
    worst = {"violations": 0, "overruns": 0}
    hidden_zero_ticks = 0
    first_overrun_tick = None
    burned_no_viol = 0
    old_audit_while_burned = None   # serviced-only violations at a burned tick
    for tk in range(1, 150):
        if tk == ZONE_STRIKE_TICK and not struck:
            for z in zones:
                s.ignite(z.loc.lat, z.loc.lon, intensity="extreme")
            s.ignite(LOCATIONS[loc].center.lat, LOCATIONS[loc].center.lon,
                     intensity="extreme")
            struck = True
        st = s.tick()
        ok, findings, stats = check_invariant(s, st)
        worst["violations"] = max(worst["violations"], stats["violations"])
        worst["overruns"] = max(worst["overruns"], stats["overruns"])
        # DETECTOR SELF-CHECK: what would the PRE-fix audit (serviced-stop
        # departures only) have reported at a tick with burned crews? If it is
        # 0 while crews burn, the new crew_overruns term is load-bearing and my
        # detector is catching a genuine gap (not trivially green).
        if stats["n_burned"] > 0 and old_audit_while_burned is None:
            old_audit_while_burned = len(s._violations)
        if not ok:
            hz = [f for f in findings if f.startswith(("V1", "V2", "V3"))]
            if hz:
                hidden_zero_ticks += 1
                if hidden_zero_ticks <= 3:
                    print(f"  !! tick {tk} HARD FAIL: {hz}")
        if stats["overruns"] > 0 and first_overrun_tick is None:
            first_overrun_tick = tk
        if stats["n_burned"] > 0 and stats["violations"] == 0:
            burned_no_viol += 1
    alarm_seen = any("no_safe_zone" in n or "CREW SAFETY ALARM" in n
                     for n in s._notes)
    print(f"  fail-closed alarm emitted:        {alarm_seen}")
    print(f"  first crew_overrun row at tick:   {first_overrun_tick}")
    print(f"  max scoreboard.violations seen:   {worst['violations']}")
    print(f"  max crew_overruns seen:           {worst['overruns']}")
    print(f"  ticks with burned crew & viol==0: {burned_no_viol}  (MUST be 0)")
    print(f"  hidden-zero/hidden-burn ticks:    {hidden_zero_ticks}  (MUST be 0)")
    print(f"  OLD serviced-only audit at a burned tick: {old_audit_while_burned} "
          f"(=> crew_overruns term is load-bearing; detector not trivially green)")
    now = s._now_s
    print("  final crew disposition:")
    for cid in sorted(s._crew_sims):
        cs = s._crew_sims[cid]
        if cs.status == CrewStatus.INBOUND:
            continue
        nom, p10 = engine_exposure(s, cs.pos)
        tag = ("BURNED" if (nom != INF and nom <= now)
               else "imminent" if (p10 != INF and p10 < now + s._cfg.egress_buffer_min*60)
               else "clear")
        inovr = any(o["crew_id"] == cid for o in s._crew_overruns)
        print(f"    {cid}: mode={cs._mode} reloc={cid in s._relocating} "
              f"{tag} in_overrun={inovr}")
    hard_ok = (hidden_zero_ticks == 0 and burned_no_viol == 0
               and alarm_seen and first_overrun_tick is not None)
    print(f"  >>> ENCIRCLEMENT VERDICT: {'PASS' if hard_ok else 'FAIL'}")
    return 0 if hard_ok else 1


if __name__ == "__main__":
    f1 = run_multi()
    f2 = run_encirclement()
    print("\n" + "=" * 78)
    verdict = "PASS" if (f1 == 0 and f2 == 0) else "FAIL"
    print(f"TASK 1 (crew safety under physics-on) OVERALL: {verdict}  "
          f"(part1 fails={f1}, encirclement={'ok' if f2==0 else 'bad'})")
    print("=" * 78)
