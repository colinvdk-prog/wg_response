"""CORRIDOR FAIL-CLOSED (contracts corridor_fail_closed /
corridor_unreached_bound / corridor_offraster_ros_mps) — fix-program item 1
of docs/TRI_MODEL_REVIEW_2026-08-31.md: the leg oracle used to SKIP samples
off the spread raster and in-domain UNREACHED samples ("no bound" = safe).
Under the knobs such samples are BOUNDED by the earliest arrival the fire
could carry to them through a boundary cell it actually reaches, and a leg
with no raster at all is unverifiable = unsafe. Defaults OFF are pinned
byte-identical to the legacy skip."""
import dataclasses
import math
import random

import pytest

from wgresponse import geo, safety
from wgresponse.contracts import (
    GridSpec,
    LatLon,
    Raster,
    ResponseConfig,
    UNREACHED,
)

NORTH, WEST = 39.5, -121.5
T0 = 1_750_000_000.0
CELL = 100.0
SPEED = 15.0


def _spec(n=21):
    return GridSpec(north_lat=NORTH, west_lon=WEST, cell_m=CELL, nrows=n,
                    ncols=n)


def _fire(spec, ros=1.0, radius_cells=None):
    """arrival = T0 + distance-from-centre / ros, everywhere (radius None) or
    only within radius_cells of the centre (the rest UNREACHED)."""
    r = Raster(spec, fill=UNREACHED)
    n = spec.nrows
    cr = cc = n // 2
    for i in range(n):
        for j in range(n):
            dc = math.hypot(i - cr, j - cc)
            if radius_cells is None or dc <= radius_cells:
                r.set(i, j, T0 + dc * CELL / ros)
    return r


def _pt(spec, r, c):
    return geo.rc_to_latlon(spec, r, c)


CFG_OFF = ResponseConfig()
CFG_FC = dataclasses.replace(CFG_OFF, corridor_fail_closed=True)
CFG_UB = dataclasses.replace(CFG_OFF, corridor_unreached_bound=True)
CFG_BOTH = dataclasses.replace(CFG_OFF, corridor_fail_closed=True,
                               corridor_unreached_bound=True)


def test_defaults_off_are_the_legacy_skip():
    spec = _spec()
    fire = _fire(spec)                       # reaches every boundary cell
    a = _pt(spec, 10, 10)
    b = _pt(spec, 10, 40)                    # 20 cells past the east edge
    legacy = safety.LegOracle(a, b, SPEED, fire, CFG_OFF)
    explicit = safety.LegOracle(a, b, SPEED, fire, dataclasses.replace(
        CFG_OFF, corridor_fail_closed=False, corridor_unreached_bound=False))
    assert legacy.latest_safe_depart_s == explicit.latest_safe_depart_s
    assert len(legacy.samples) == len(explicit.samples)
    assert legacy.samples[-1][0] < legacy.n_pts - 1   # off-raster tail skipped
    assert not legacy.unverifiable


def test_off_raster_samples_are_bounded_under_fail_closed():
    spec = _spec()
    fire = _fire(spec)
    a = _pt(spec, 10, 10)
    b = _pt(spec, 10, 40)
    legacy = safety.LegOracle(a, b, SPEED, fire, CFG_OFF)
    closed = safety.LegOracle(a, b, SPEED, fire, CFG_FC)
    assert len(closed.samples) == closed.n_pts           # every sample bound
    assert len(closed.samples) > len(legacy.samples)
    assert closed.latest_safe_depart_s <= legacy.latest_safe_depart_s
    # the bounded tail carries a FINITE, later-than-edge arrival: the edge is
    # reached at ~10 cells / 1 m/s, the far end ~20 more cells at 1.7 m/s
    edge_t = fire.get(10, 20)
    far = closed.samples[-1][2]
    assert edge_t < far < edge_t + 20 * CELL / 1.0
    # T* and verdict agree either side of the bound (monotone law)
    ts = closed.latest_safe_depart_s
    assert closed.verdict(ts - 1.0).safe
    assert not closed.verdict(ts + 1.0).safe


def test_contained_fire_keeps_the_legacy_skip():
    spec = _spec()
    fire = _fire(spec, radius_cells=6)      # never reaches the boundary
    assert safety._boundary_runs(fire) == []
    a = _pt(spec, 10, 10)
    b = _pt(spec, 10, 40)
    legacy = safety.LegOracle(a, b, SPEED, fire, CFG_OFF)
    closed = safety.LegOracle(a, b, SPEED, fire, CFG_BOTH)
    assert closed.latest_safe_depart_s == legacy.latest_safe_depart_s
    assert len(closed.samples) == len(legacy.samples)


def test_unreached_bound_covers_the_truncation_wall():
    """The review's manufactured-UNREACHED case: a non-burnable wall at the
    domain edge leaves the cells behind it UNREACHED although the real fire
    walks around it outside the domain."""
    spec = _spec()
    fire = _fire(spec)
    n = spec.nrows
    for i in range(n):                       # wall + everything east of it
        for j in range(13, n):
            fire.set(i, j, UNREACHED)
    a = _pt(spec, 10, 15)                    # a leg entirely inside the void
    b = _pt(spec, 10, 19)
    legacy = safety.LegOracle(a, b, SPEED, fire, CFG_OFF)
    assert math.isinf(legacy.latest_safe_depart_s)          # "safe forever"
    fc_only = safety.LegOracle(a, b, SPEED, fire, CFG_FC)
    assert math.isinf(fc_only.latest_safe_depart_s)         # sim posture
    bounded = safety.LegOracle(a, b, SPEED, fire, CFG_UB)
    assert not math.isinf(bounded.latest_safe_depart_s)
    assert len(bounded.samples) == bounded.n_pts
    # the bound is carried from reached boundary cells (west of the wall),
    # so it is never earlier than the earliest of them and never "never"
    edge_t = min(t for t, *_ in safety._boundary_runs(fire))
    assert all(edge_t <= v < UNREACHED for _i, _p, v, _dt in bounded.samples)


def test_no_raster_is_unverifiable_under_fail_closed_only():
    a = LatLon(NORTH - 0.01, WEST + 0.01)
    b = LatLon(NORTH - 0.02, WEST + 0.02)
    legacy = safety.LegOracle(a, b, SPEED, None, CFG_OFF)
    assert legacy.verdict(T0).safe and math.isinf(legacy.latest_safe_depart_s)
    closed = safety.LegOracle(a, b, SPEED, None, CFG_FC)
    assert closed.unverifiable
    assert closed.latest_safe_depart_s == -math.inf
    v = closed.verdict(T0)
    assert not v.safe and "unverifiable" in v.reason
    assert safety.corridor_safe(a, b, T0, SPEED, None, CFG_FC).safe is False
    assert safety.corridor_safe(a, b, T0, SPEED, None, CFG_OFF).safe is True


def test_boundary_runs_are_memoised_and_reached_only():
    spec = _spec()
    fire = _fire(spec)
    fire.set(0, 5, UNREACHED)               # a hole splits the north edge
    runs = safety._boundary_runs(fire)
    assert safety._boundary_runs(fire) is runs
    assert runs and all(not math.isinf(t) for t, *_ in runs)
    assert all(len(run) == 5 for run in runs)


@pytest.mark.parametrize("seed", range(6))
def test_run_bound_is_a_rigorous_lower_bound(seed):
    """For random reached boundaries and random probe points, the run bound
    never exceeds the exact per-cell minimum arrival(b) + haversine(p,b)/ros
    — and sits within the documented slack of it."""
    rng = random.Random(seed)
    spec = _spec(n=17)
    fire = Raster(spec, fill=UNREACHED)
    n = spec.nrows
    boundary = ([(0, c) for c in range(n)] + [(n - 1, c) for c in range(n)]
                + [(r, 0) for r in range(1, n - 1)]
                + [(r, n - 1) for r in range(1, n - 1)])
    reached = []
    for rc in boundary:
        if rng.random() < 0.6:
            t = T0 + rng.uniform(0.0, 20_000.0)
            fire.set(rc[0], rc[1], t)
            reached.append((rc, t))
    assert reached
    runs = safety._boundary_runs(fire)
    ros = 1.7
    for _ in range(40):
        p = LatLon(NORTH - rng.uniform(-0.03, 0.05), WEST + rng.uniform(-0.03, 0.05))
        bound = safety._bound_arrival_s(p, runs, ros, CELL)
        exact = UNREACHED
        d_exact = 0.0
        for rc, t in reached:
            d = geo.haversine_m(p, geo.rc_to_latlon(spec, rc[0], rc[1]))
            cand = t + d / ros
            if cand < exact:
                exact, d_exact = cand, d
        assert bound <= exact + 1e-6
        # conservatism is bounded: a run pairs its EARLIEST arrival with the
        # NEAREST point of its segment, so the bound sits at most one run
        # length (run_len - 1 cells) earlier than the exact per-cell minimum,
        # plus the documented 1%-of-distance and 0.71-cell rigor slack
        run_m = (safety._BOUNDARY_RUN_LEN - 1) * CELL
        assert bound >= exact - (run_m + 0.01 * d_exact + 0.71 * CELL) / ros - 1e-6
