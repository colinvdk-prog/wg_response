"""attack_r5_urgency — HONEST urgency re-measurement under physics-on (task 4).

FAIR DESIGN NOTE: the dispatch solver is ITERATED-LOCAL-SEARCH under a WALL-CLOCK
budget (cfg.solver_budget_s=5.0, time.monotonic in dispatch.py) — for large /
capacity-limited scenarios MORE cpu-time => MORE iterations => a better plan =>
MORE homes served. So an ON-vs-OFF served comparison is only fair if both arms
run under IDENTICAL cpu conditions. Here ONE worker runs OFF then ON BACK-TO-BACK
for a single (config, seed) in the same process, and concurrency is kept <= cores
with nothing else running, so the per-seed DELTA is controlled for cpu load.
Physics-ON (grass+spotting). Malibu 48/3 & 80/3 AND Paradise & Boulder.
READ-ONLY; in-process only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from multiprocessing import Pool

sys.path.insert(0, str(Path(__file__).resolve().parent))
from attack_r5_common import make_session, ignite_point                   # noqa
from wgresponse.simui import LOCATIONS                                    # noqa

CONFIGS = [
    ("malibu_ca",   48, 3, 9.0, "extreme"),
    ("malibu_ca",   80, 3, 9.0, "extreme"),
    ("paradise_ca", 64, 5, 8.0, "extreme"),
    ("boulder_co",  48, 4, 9.0, "extreme"),
]
SEEDS = ["u1", "u2", "u3", "u4", "u5", "u6", "u7", "u8", "u9", "u10"]
MAX_TICKS = 140


def _served_once(loc, nc, ncrew, km, inten, seed, w_urg):
    s = make_session(loc, n_clients=nc, n_crews=ncrew, seed=seed,
                     fire_zone="extended", physics=True,
                     extra={"dispatch_urgency_weight": w_urg})
    pt = ignite_point(s, km=km)
    s.ignite(pt.lat, pt.lon, intensity=inten)
    for _ in range(MAX_TICKS):
        st = s.tick()
    return st["scoreboard"]["served"]


def _pair(args):
    """OFF then ON back-to-back in ONE process -> identical cpu conditions."""
    loc, nc, ncrew, km, inten, seed = args
    off = _served_once(loc, nc, ncrew, km, inten, seed, 0.0)
    on = _served_once(loc, nc, ncrew, km, inten, seed, 0.30)
    return (loc, nc, ncrew, seed, off, on)


def main():
    print("=" * 78)
    print("TASK 4 — HONEST urgency ON(0.30) vs OFF(0.0) served-count, physics-ON")
    print(f"  {len(SEEDS)} seeds/config, {MAX_TICKS} ticks, extended tiles, "
          f"OFF/ON PAIRED per process (fair delta under a wall-clock solver)")
    print("=" * 78)
    jobs = [(loc, nc, ncrew, km, inten, seed)
            for (loc, nc, ncrew, km, inten) in CONFIGS for seed in SEEDS]
    # Pool(6) on a 24-core box => 4 cores/worker, no cpu starvation of the
    # solver; run with nothing else heavy going.
    with Pool(processes=6) as pool:
        res = pool.map(_pair, jobs)

    by = {}
    for (loc, nc, ncrew, seed, off, on) in res:
        by[(loc, nc, ncrew, seed)] = (off, on)

    net_all = 0
    grand = []
    for (loc, nc, ncrew, km, inten) in CONFIGS:
        print(f"\n### {loc} {nc}/{ncrew}  (physics-ON, extreme)")
        print(f"{'seed':>6} | {'OFF':>4} {'ON':>4} | {'delta(ON-OFF)':>13}")
        cfg_net = 0
        deltas = []
        for seed in SEEDS:
            off, on = by[(loc, nc, ncrew, seed)]
            d = on - off
            deltas.append(d)
            cfg_net += d
            print(f"{seed:>6} | {off:>4} {on:>4} | {d:>+13}")
        wins = sum(1 for d in deltas if d > 0)
        losses = sum(1 for d in deltas if d < 0)
        ties = sum(1 for d in deltas if d == 0)
        print(f"  config net delta = {cfg_net:+d}   "
              f"(ON wins {wins}, ties {ties}, ON loses {losses})")
        net_all += cfg_net
        grand.append((f"{loc} {nc}/{ncrew}", cfg_net, wins, ties, losses))

    print("\n" + "=" * 78)
    print("SUMMARY — served-count delta (ON minus OFF), physics-ON, paired")
    print("=" * 78)
    for name, net, w, t, l in grand:
        print(f"  {name:>18}: net {net:+d}  (win {w}/tie {t}/lose {l})")
    print(f"\n  NET ACROSS ALL CONFIGS: {net_all:+d}")
    if net_all > 0:
        print('  VERDICT: "urgency protects more homes" is SUPPORTED under physics-on.')
    elif net_all == 0:
        print('  VERDICT: "urgency protects more homes" is NOT supported — net NEUTRAL.')
    else:
        print('  VERDICT: "urgency protects more homes" is FALSE under physics-on '
              '(net NEGATIVE).')
    return net_all


if __name__ == "__main__":
    main()
