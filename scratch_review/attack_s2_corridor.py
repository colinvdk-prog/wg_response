"""ADVERSARIAL VERIFIER (Session 2) — attack #3: B1 corridor byte-identity.

Claims under attack (Sprint 2 ticket B1; safety.LegOracle docstring):
  3a. corridor_safe (now LegOracle.verdict) is BYTE-IDENTICAL to the Sprint-1
      inline sampling loop: same safe flag, bitwise-equal worst_margin_h,
      identical first-failure reason string. Property-tested here on 300
      independently-seeded random legs (different RNG stream than the shipped
      tests) plus targeted edge cases: leg fully outside the raster, leg
      entirely UNREACHED, depart exactly at the T* boundary, zero-length legs,
      speed extremes (1e-6 .. 1e9 m/s), arrivals already in the past.
  3b. T* (latest_safe_depart_s) is the true verdict flip point: brute-force
      the flip by probing corridor_safe around T*; |flip - T*| <= 1 ms, and
      depart <= T* <=> verdict.safe outside a 1 ms guard band.
  3c. Egress zone selection ordering unchanged: nearest-first by
      travel_time_s, ties by zone_id, first SAFE zone wins (not best-margin).

The reference below is written INDEPENDENTLY from the documented Sprint-1
semantics (safety.corridor_safe docstring + contracts SAFETY SEMANTICS 3),
deliberately NOT copied from tests/test_safety.py, so a shared transcription
error cannot hide a divergence. Float operations mirror the documented
sampling loop: cum += haversine(prev, p); t_pass = depart + cum / speed.

Run from repo root:
    py -3 -m pytest scratch_review/attack_s2_corridor.py -v
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import safety, travel
from wgresponse.contracts import (CorridorVerdict, GridSpec, LatLon, Raster,
                                  ResponseConfig, SafeZone, UNREACHED,
                                  seeded_rng)

NOW = 1_780_000_000.0


# ---------------------------------------------------------------------------
# Independent Sprint-1 reference (from documented semantics)
# ---------------------------------------------------------------------------

def corridor_safe_reference(a, b, depart_s, speed_mps, eroded_p10, cfg):
    """Sprint-1 corridor_safe semantics, reconstructed from the docstring:
    sample every corridor_sample_m (geo.sample_polyline), pass time
    depart + cum/speed, sample unsafe when (arrival - pass) < margin, first
    failure fixes the reason, worst_margin_h = min finite (arrival-pass)/3600,
    outside-raster / UNREACHED samples are safe and unconstraining."""
    if speed_mps <= 0.0:
        raise ValueError("speed_mps must be > 0")
    if eroded_p10 is None:
        return CorridorVerdict(True, "", UNREACHED)
    pts = safety._sample_polyline(a, b, cfg.corridor_sample_m)
    margin_s = cfg.route_margin_min * 60.0
    worst_h = UNREACHED
    safe = True
    reason = ""
    cum_m = 0.0
    prev = None
    for i, p in enumerate(pts):
        if prev is not None:
            cum_m += safety._haversine_m(prev, p)
        prev = p
        v = safety._raster_value_at(eroded_p10, p)
        if v is None or math.isinf(v):
            continue
        t_pass = depart_s + cum_m / speed_mps
        m_h = (v - t_pass) / 3600.0
        if m_h < worst_h:
            worst_h = m_h
        if safe and (v - t_pass) < margin_s:
            safe = False
            reason = (f"corridor unsafe at sample {i + 1}/{len(pts)} "
                      f"({p.lat:.5f},{p.lon:.5f}): eroded p10 arrival {v:.0f}s < "
                      f"pass {t_pass:.0f}s + margin {margin_s:.0f}s")
    return CorridorVerdict(safe, reason, worst_h)


def _rc_center(spec, rr, cc):
    return safety._rc_center(spec, rr, cc)


def _case(k, tag="vf-s2-b1"):
    """Independent random case generator (own RNG stream)."""
    rng = seeded_rng(tag, k)
    spec = GridSpec(north_lat=33.5 + rng.random() * 7.0,
                    west_lon=-123.5 + rng.random() * 6.0,
                    cell_m=rng.uniform(80.0, 1500.0),
                    nrows=rng.randrange(5, 40), ncols=rng.randrange(5, 40))
    kind = k % 6
    n = spec.nrows * spec.ncols
    if kind == 0:                       # all UNREACHED
        data = [UNREACHED] * n
    elif kind == 1:                     # everything already burned (past)
        data = [NOW - rng.uniform(0.0, 20.0) * 3600.0 for _ in range(n)]
    else:                               # mixed field
        p_inf = rng.uniform(0.0, 0.8)
        data = [UNREACHED if rng.random() < p_inf
                else NOW + rng.uniform(-6.0, 40.0) * 3600.0
                for _ in range(n)]
    ras = Raster(spec, data)

    def pt(outside=False):
        if outside:
            rr = rng.choice([-90.0, spec.nrows + 90.0]) + rng.random()
            cc = rng.uniform(-60.0, spec.ncols + 60.0)
        else:
            rr = rng.uniform(-5.0, spec.nrows + 5.0)
            cc = rng.uniform(-5.0, spec.ncols + 5.0)
        return _rc_center(spec, rr, cc)

    if kind == 3:                       # leg fully outside the raster
        a, b = pt(outside=True), pt(outside=True)
    elif kind == 4:                     # zero-length leg
        a = pt()
        b = a
    else:
        a, b = pt(), pt()
    if kind == 5:                       # speed extremes
        speed = rng.choice([1e-6, 1e-3, 1e6, 1e9])
    else:
        speed = rng.uniform(2.0, 30.0)
    depart = NOW + rng.uniform(-8.0, 16.0) * 3600.0
    cfg = ResponseConfig()
    if k % 7 == 0:
        cfg.corridor_sample_m = rng.uniform(50.0, 900.0)
        cfg.route_margin_min = rng.uniform(5.0, 120.0)
    return a, b, depart, speed, ras, cfg


def _eq_verdict(v1, v2):
    same_margin = (v1.worst_margin_h == v2.worst_margin_h) or (
        math.isinf(v1.worst_margin_h) and math.isinf(v2.worst_margin_h))
    return v1.safe == v2.safe and v1.reason == v2.reason and same_margin


# ---------------------------------------------------------------------------
# 3a — 300-leg property test, bitwise
# ---------------------------------------------------------------------------

def test_b1_byte_identity_300_random_legs_and_edges():
    mismatches = []
    n_unsafe = n_safe = n_unconstrained = 0
    for k in range(300):
        a, b, depart, speed, ras, cfg = _case(k)
        got = safety.corridor_safe(a, b, depart, speed, ras, cfg)
        ref = corridor_safe_reference(a, b, depart, speed, ras, cfg)
        if not _eq_verdict(got, ref):
            mismatches.append((k, got, ref))
        if got.safe:
            n_safe += 1
            if math.isinf(got.worst_margin_h):
                n_unconstrained += 1
        else:
            n_unsafe += 1
        # depart exactly at the oracle's T* boundary (the brief's edge case)
        o = safety.LegOracle(a, b, speed, ras, cfg)
        tstar = o.latest_safe_depart_s
        if math.isfinite(tstar):
            got_t = safety.corridor_safe(a, b, tstar, speed, ras, cfg)
            ref_t = corridor_safe_reference(a, b, tstar, speed, ras, cfg)
            if not _eq_verdict(got_t, ref_t):
                mismatches.append((f"{k}@T*", got_t, ref_t))
    assert n_unsafe >= 60 and n_safe >= 60 and n_unconstrained >= 10, (
        "case generator degenerate", n_safe, n_unsafe, n_unconstrained)
    assert not mismatches, (
        f"{len(mismatches)} corridor verdicts diverge from the Sprint-1 "
        f"reference; first: {mismatches[0]}")


def test_b1_none_raster_and_nonpositive_speed():
    cfg = ResponseConfig()
    a, b = LatLon(34.2, -118.4), LatLon(34.25, -118.3)
    got = safety.corridor_safe(a, b, NOW, 12.0, None, cfg)
    assert got.safe and got.reason == "" and math.isinf(got.worst_margin_h)
    try:
        safety.corridor_safe(a, b, NOW, 0.0, None, cfg)
        raised = False
    except ValueError:
        raised = True
    assert raised, "speed_mps=0 must be rejected (was a ZeroDivision before)"


# ---------------------------------------------------------------------------
# 3b — T* is the brute-forced verdict flip point
# ---------------------------------------------------------------------------

def test_b1_tstar_matches_bruteforced_flip_point():
    checked = 0
    for k in range(150):
        a, b, _depart, speed, ras, cfg = _case(k, tag="vf-s2-tstar")
        o = safety.LegOracle(a, b, speed, ras, cfg)
        tstar = o.latest_safe_depart_s
        if not math.isfinite(tstar):
            # unconstrained: any departure must be safe, probe far out
            for d in (NOW - 1e7, NOW, NOW + 1e7):
                assert safety.corridor_safe(a, b, d, speed, ras, cfg).safe
            continue
        checked += 1
        # verdicts must agree with the T* threshold outside a 1 ms guard band
        for off in (-3600.0, -60.0, -1.0, -0.01):
            v = safety.corridor_safe(a, b, tstar + off, speed, ras, cfg)
            assert v.safe, (k, off, "safe side misclassified")
        for off in (0.01, 1.0, 60.0, 3600.0):
            v = safety.corridor_safe(a, b, tstar + off, speed, ras, cfg)
            assert not v.safe, (k, off, "unsafe side misclassified")
        # bisect the true flip point of the public verdict
        lo, hi = tstar - 0.01, tstar + 0.01
        for _ in range(60):
            mid = (lo + hi) / 2.0
            if safety.corridor_safe(a, b, mid, speed, ras, cfg).safe:
                lo = mid
            else:
                hi = mid
        assert abs(lo - tstar) <= 1e-3, (
            f"leg {k}: brute-forced flip {lo!r} vs T* {tstar!r} "
            f"(> 1 ms apart)")
    assert checked >= 60, f"only {checked} constrained legs probed"


# ---------------------------------------------------------------------------
# 3c — egress ordering: nearest-first, tie by zone_id, first safe wins
# ---------------------------------------------------------------------------

def _egress_reference(stop, depart, zones, ras, cfg):
    """Independent reimplementation of the documented egress_choice."""
    if not zones:
        return "", False
    speed = cfg.road_speed_mps / (cfg.circuity * cfg.fire_traffic_factor)
    ordered = sorted(zones, key=lambda z: (travel.travel_time_s(stop, z.loc, cfg),
                                           z.zone_id))
    for z in ordered:
        if corridor_safe_reference(stop, z.loc, depart, speed, ras, cfg).safe:
            return z.zone_id, True
    return "", False


def test_b1_egress_ordering_nearest_first_tie_by_zone_id():
    rng = seeded_rng("vf-s2-egress", 0)
    agree = 0
    for k in range(120):
        a, _b, depart, _speed, ras, cfg = _case(k, tag="vf-s2-egress-case")
        spec = ras.spec
        zones = [SafeZone(f"z{z}", _rc_center(
            spec, rng.uniform(-2.0, spec.nrows + 2.0),
            rng.uniform(-2.0, spec.ncols + 2.0))) for z in range(4)]
        # exact-tie construction: two zones mirrored east/west of the stop --
        # identical haversine distance to double precision is not guaranteed,
        # so FORCE the tie by placing both at the same point with different ids
        tie_pt = _rc_center(spec, spec.nrows * 0.5, spec.ncols * 0.5)
        zones += [SafeZone("tie-b", tie_pt), SafeZone("tie-a", tie_pt)]
        want_zid, want_safe = _egress_reference(a, depart, zones, ras, cfg)
        verdict, zid = safety.egress_choice(a, depart, zones, ras, cfg)
        assert zid == want_zid and verdict.safe == want_safe, (
            f"case {k}: egress picked {zid!r}/{verdict.safe} "
            f"expected {want_zid!r}/{want_safe}")
        agree += 1
        if want_safe and want_zid.startswith("tie-"):
            assert want_zid == "tie-a", "tie not broken by zone_id"
    assert agree == 120
    # no zones configured: fail closed
    v, zid = safety.egress_choice(LatLon(34.2, -118.4), NOW, [], None,
                                  ResponseConfig())
    assert not v.safe and zid == "" and v.worst_margin_h == -UNREACHED


def test_b1_egress_first_safe_wins_not_best_margin():
    """A nearer zone that is merely *barely* safe must win over a farther zone
    with a huge margin (documented nearest-first-safe semantics)."""
    cfg = ResponseConfig()
    spec = GridSpec(north_lat=34.60, west_lon=-119.0, cell_m=200.0,
                    nrows=30, ncols=60)
    speed = cfg.road_speed_mps / (cfg.circuity * cfg.fire_traffic_factor)
    stop = _rc_center(spec, 15.0, 5.0)
    near = _rc_center(spec, 15.0, 12.0)      # ~1.4 km east
    far = _rc_center(spec, 15.0, 40.0)       # ~7 km east
    leg_near_s = safety._haversine_m(stop, near) / speed
    # arrivals: everywhere just barely safe for the near leg, enormous east
    data = []
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            if c <= 13:
                data.append(NOW + leg_near_s + cfg.route_margin_min * 60.0
                            + 120.0)          # 2 min of true slack
            else:
                data.append(NOW + 1000.0 * 3600.0)
    ras = Raster(spec, data)
    verdict, zid = safety.egress_choice(stop, NOW, [SafeZone("far", far),
                                                    SafeZone("near", near)],
                                        ras, cfg)
    assert verdict.safe and zid == "near", (zid, verdict)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
