"""ADVERSARIAL VERIFIER — simulator attack #1: SAFETY on all five real tiles.

Brief item 1: script sessions on ALL FIVE real tiles (skip LOUDLY if a tile
is missing) x intensities {moderate, extreme}; run 20+ ticks each (we run 30);
assert:

  S1. the engine's own AUDIT reports 0 violations on EVERY tick
      (scoreboard.violations == 0, every audit entry violation == False,
      min_margin_h >= 0 when present);
  S2. INDEPENDENT recomputation from the state payloads (never the engine's
      audit): every serviced stop departed >= (egress_buffer + ember_buffer)
      minutes before the LOCAL p10 arrival at that client's cell, re-checked
      against the CURRENT p10 on every subsequent tick (single fire per
      session, so the client rollup p10 IS the stop's incident p10);
  S3. crews are never positioned inside the burned mask (own RLE decode +
      own lat/lon->cell math);
  S4. non-vacuity: the fire actually grew and the alert ladder actually fired
      (else the whole run proves nothing); globally, every tile serviced at
      least one stop in at least one intensity and at least one audited stop
      had a finite deadline (the deadline check really bit).

Scenario law: default ResponseConfig (buffer 30 / ember 0 / slack 15), 30
clients, 3 crews, one fire ignited at a deep FAST-fuel (grass/shrub) cell
450-2500 m upwind of a burnable-cell client (canonical hazard wind),
set_speed(4.0) -> 30 ticks x 3600 s = 30 h sim, so the nominal fire actually
overruns serviced cells and the reseeded-interior audit regime is exercised
(a 7.5 h run left slow-fuel tiles with a static mask — vacuous).

Run from repo root:
    py -3 -m pytest scratch_review/attack_sim_safety.py -v
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scratch_review.attack_sim_common import (  # noqa: E402
    burned_count, client_p10_map, crews_inside_burned,
    independent_stop_violations, pick_ignition, skip_reason, tile_present)
from wgresponse.simui import LOCATIONS, SimSession  # noqa: E402

KEYS = sorted(LOCATIONS)
INTENSITIES = ("moderate", "extreme")
N_TICKS = 30
N_CLIENTS = 30
N_CREWS = 3

_RESULTS = {}          # (key, intensity) -> summary dict (filled lazily)


def _run(key, intensity):
    got = _RESULTS.get((key, intensity))
    if got is not None:
        return got
    if not tile_present(key):
        pytest.skip(skip_reason(key))

    s = SimSession(key, N_CLIENTS, N_CREWS, seed=f"atk-safety|{key}|{intensity}")
    assert s.state()["terrain"]["synthetic"] is False, \
        f"{key}: expected the REAL cached tile, got synthetic fallback"
    ign, target = pick_ignition(s, key)
    s.ignite(ign.lat, ign.lon, intensity=intensity)
    s.set_speed(4.0)          # 1 h sim per tick; audit still runs EVERY tick

    buffer_min = s._cfg.egress_buffer_min
    ember_min = s._cfg.ember_buffer_min
    assert buffer_min == 30.0 and ember_min == 0.0, "defaults drifted"

    burned0 = None
    burnedN = 0
    alerted_ever = False
    finite_audits = 0
    per_tick_failures = []
    for k in range(N_TICKS):
        st = s.tick()

        # S1 — the engine's own audit, every tick
        sb = st["scoreboard"]
        assert sb["violations"] == 0, (
            f"{key}/{intensity} tick {k + 1}: ENGINE AUDIT VIOLATIONS "
            f"{sb['violations']} != 0 — {st['audit']}")
        assert sb["min_margin_h"] is None or sb["min_margin_h"] >= 0.0, (
            f"{key}/{intensity} tick {k + 1}: min_margin_h "
            f"{sb['min_margin_h']} < 0")
        for a in st["audit"]:
            assert a["violation"] is False, (key, intensity, k + 1, a)
            if a["margin_h"] is not None:
                finite_audits += 1

        # S2 — independent payload-only recompute of every stop's deadline
        bad = independent_stop_violations(st, buffer_min, ember_min)
        if bad:
            per_tick_failures.append((k + 1, "stop_deadline", bad))

        # S3 — crews never inside the burned mask (own decode + own math)
        inside = crews_inside_burned(st)
        if inside:
            per_tick_failures.append((k + 1, "crew_in_burned", inside))

        if burned0 is None:
            burned0 = burned_count(st)
        burnedN = burned_count(st)
        if any(c["state"] != "none" for c in st["clients"]):
            alerted_ever = True

    assert not per_tick_failures, (
        f"{key}/{intensity}: INDEPENDENT SAFETY RECOMPUTE FAILED — "
        f"{per_tick_failures[:4]} (seed=atk-safety|{key}|{intensity}, "
        f"ignition={ign.lat:.6f},{ign.lon:.6f}, target={target.client_id})")

    st = s.state()
    summary = {
        "served": st["scoreboard"]["served"],
        "min_margin_h": st["scoreboard"]["min_margin_h"],
        "burned_growth": burnedN - (burned0 or 0),
        "burned_final": burnedN,
        "alerted": alerted_ever,
        "finite_audits": finite_audits,
        "unserved": st["scoreboard"]["unserved_by_reason"],
        "ignition": (round(ign.lat, 6), round(ign.lon, 6)),
        "target": target.client_id,
    }
    _RESULTS[(key, intensity)] = summary
    return summary


@pytest.mark.parametrize("intensity", INTENSITIES)
@pytest.mark.parametrize("key", KEYS)
def test_safety_real_tile(key, intensity):
    summary = _run(key, intensity)
    # S4 per-run non-vacuity: the fire grew and the ladder fired.
    assert summary["burned_growth"] > 0, (
        f"{key}/{intensity}: fire never grew ({summary}) — scenario vacuous")
    assert summary["alerted"], (
        f"{key}/{intensity}: no client ever alerted ({summary}) — vacuous")


def test_zz_global_service_coverage():
    """Every present tile must have serviced >= 1 stop in >= 1 intensity, and
    globally >= 1 audited stop carried a finite deadline (the p10-buffer rule
    actually bit somewhere) — otherwise the safety pass proved nothing."""
    present = [k for k in KEYS if tile_present(k)]
    if not present:
        pytest.skip("no real tiles on disk at all — nothing to cover")
    for key in present:
        served = [_run(key, i)["served"] for i in INTENSITIES]
        assert max(served) >= 1, (
            f"{key}: no stop serviced under ANY intensity "
            f"({dict(zip(INTENSITIES, served))}) — the simulator demonstrates "
            f"nothing on this tile: "
            f"{ {i: _run(key, i) for i in INTENSITIES} }")
    total_finite = sum(_run(k, i)["finite_audits"]
                       for k in present for i in INTENSITIES)
    assert total_finite >= 1, "no audited stop ever had a finite deadline"
    sys.stderr.write("\n[safety summary]\n")
    for k in present:
        for i in INTENSITIES:
            sys.stderr.write(f"  {k:14s} {i:8s} {_RESULTS[(k, i)]}\n")
    import json as _json
    out = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "out", "verifier_sim_safety_summary.json")
    with open(out, "w", encoding="utf-8") as f:
        _json.dump({f"{k}|{i}": _RESULTS[(k, i)]
                    for k in present for i in INTENSITIES}, f, indent=1)
