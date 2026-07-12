"""attack_r4_urgency_dynamic.py — FOUNDER ROUND 4 law 7, the DYNAMIC served-count
claim, RE-VERIFIED after the round-4 mis-tuning fix.

HISTORY: the round-4 build put w_urg x urgency into the homes OBJECTIVE. Under the
now-default physics-on fire (fast grass + spotting) that term steered the
budget-limited ILS to protect FEWER total homes than pure count-max — this file
originally PINNED that regression (adversarial: 'served >= OFF on every seed' was
FALSE, net-negative on the founder's headline classes, up to -113 on Paradise
64/5).

FIX (option A / STRICT can't-cost-a-home): urgency was removed from the objective,
so the count-maximal SET is chosen URGENCY-BLIND and the emitted homes plan is
BYTE-IDENTICAL for every dispatch_urgency_weight. Therefore served(ON) == served
(OFF) on EVERY seed and NET == 0 — urgency can never cost a home. This file now
PINS the fix (the acceptance gate): the paired OFF-vs-ON sweep over the founder's
classes, PHYSICS-ON, extended tiles, 140 ticks, >=8 seeds, OFF and ON paired in
one process, must show served(ON) >= served(OFF) on every seed and NET >= 0.

ON  = default homes mode (dispatch_urgency_weight=0.30).
OFF = urgency fully off (dispatch_urgency_weight=0.0) = pure count-max.

Run:  py -3 -m pytest scratch_review/attack_r4_urgency_dynamic.py -q -s
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from attack_r4_common import TILE_DIR, tile_present, skip_loud         # noqa: E402

SEEDS = [f"seed{i}" for i in range(8)]
TICKS = 140
# the founder's headline classes (physics-on, extended tiles)
SCENARIOS = [("malibu_ca", 48, 3), ("malibu_ca", 80, 3),
             ("paradise_ca", 64, 5), ("boulder_co", 48, 4)]
# PHYSICS-ON: the now-default fast fire that exposed the round-4 mis-tuning.
PHYSICS_ON = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10}


def _served(loc, seed, nc, cr, w_urg, ticks=TICKS):
    from wgresponse.simui import SimSession
    from attack_sim_common import pick_ignition
    sess = SimSession(loc, nc, cr, seed=seed,
                      cfg_overrides=dict(PHYSICS_ON, dispatch_urgency_weight=w_urg),
                      fire_zone="extended", tile_dir=TILE_DIR)
    p, _ = pick_ignition(sess, loc, cluster=True)
    sess.ignite(p.lat, p.lon, intensity="high")
    st = None
    for _ in range(ticks):
        st = sess.tick()
    return st["scoreboard"]["served"], st["scoreboard"]["violations"]


@pytest.mark.parametrize("loc,nc,cr", SCENARIOS)
def test_dynamic_urgency_never_serves_fewer_than_pure_count(loc, nc, cr):
    """ACCEPTANCE GATE: served(ON) >= served(OFF) on EVERY seed and NET >= 0. With
    the fix the emitted homes plan is urgency-blind, so ON and OFF are byte-
    identical -> every delta is exactly 0 and violations pair-match (safety is
    objective-independent)."""
    if not tile_present(loc):
        pytest.skip(skip_loud(loc, "tile"))
    rows = []
    for s in SEEDS:
        # pair OFF then ON in ONE process (shared CPU/budget conditions)
        off, voff = _served(loc, s, nc, cr, 0.0)
        on, von = _served(loc, s, nc, cr, 0.30)
        rows.append((s, off, on, on - off, voff, von))
    deltas = [d for *_, d, _, _ in rows]
    net = sum(deltas)
    print(f"\n=== {loc} {nc}/{cr}, {TICKS} ticks, physics-on, "
          f"OFF(0.0) vs ON(0.30) ===")
    print(f"{'seed':8}{'OFF':>5}{'ON':>5}{'delta':>7}{'vOFF':>6}{'vON':>5}")
    for s, off, on, d, voff, von in rows:
        print(f"{s:8}{off:>5}{on:>5}{d:>+7}{voff:>6}{von:>5}")
    print(f"  NET(ON-OFF)={net:+d} min={min(deltas):+d} max={max(deltas):+d} "
          f"losses={sum(1 for d in deltas if d < 0)}")

    # ACCEPTANCE: urgency never serves FEWER than pure count-max on any seed.
    for s, off, on, d, voff, von in rows:
        assert on >= off, (loc, s, f"ON {on} < OFF {off}")
        # safety is objective-independent: the byte-identical plan gives identical
        # violations (SEMANTICS 10 overruns, when present, are pair-matched).
        assert von == voff, (loc, s, von, voff)
    assert net >= 0, (loc, net)


def test_dynamic_on_equals_off_byte_identical_served():
    """The stronger statement the fix guarantees: ON and OFF produce the SAME
    served count (not merely ON>=OFF) because the emitted homes plan is byte-
    identical across dispatch_urgency_weight. Deterministic run-to-run."""
    if not tile_present("malibu_ca"):
        pytest.skip(skip_loud("malibu_ca", "tile"))
    for w in (0.0, 0.30):
        a, _ = _served("malibu_ca", "seed7", 80, 3, w, ticks=60)
        b, _ = _served("malibu_ca", "seed7", 80, 3, w, ticks=60)
        assert a == b, (w, a, b)                       # deterministic
    on, _ = _served("malibu_ca", "seed7", 80, 3, 0.30, ticks=60)
    off, _ = _served("malibu_ca", "seed7", 80, 3, 0.0, ticks=60)
    assert on == off, (on, off)                        # urgency-blind => identical
