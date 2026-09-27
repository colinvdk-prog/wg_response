"""Deterministic stub stack for the fire-belief tests (SAFETY SEMANTICS 14).

Not a test module (no test_ prefix; imported the way test_seeded_incidents
imports seeded_stack). It reuses tests/seeded_stack.py's stubs and canonical
digest functions and adds the scenario the fire-belief byte-identity pin
runs: a PERIMETER incident whose polygon and reported acreage change between
cycles and a POINT incident whose reported acreage changes — exactly the
inputs the belief tracker observes — through the REAL monitor / alert
engine / dispatch / StateStore / _WatchDaemon / worldview, with the
ensemble model-error term and the p50 median bound armed (the live watcher's
posture), so every seam the belief can touch is exercised.

belief_baseline_run() returns ([cycle digests], api digest, state digest,
world digest). Its goldens (tests/test_fire_belief.py) were computed on the
a8bebec engine, BEFORE any fire-belief code existed; with every fire-belief
knob OFF the build must reproduce them byte for byte.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
from wgresponse import geo  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    ClientProperty,
    FireIncident,
    IncidentSource,
    LatLon,
)

T0 = ss.T0
H = ss.H
PERIOD_S = ss.PERIOD_S

#: the perimeter fire (a ~1.6 km square polygon) and the point fire
PERIM_CENTER = LatLon(39.0, -121.02)
POINT_ORIGIN = LatLon(38.93, -120.93)


def square_ring(center: LatLon, half_m: float) -> list:
    """A closed-implicitly square ring (4 vertices) of half-width half_m."""
    n = geo.destination(center, 0.0, half_m).lat
    s = geo.destination(center, 180.0, half_m).lat
    e = geo.destination(center, 90.0, half_m).lon
    w = geo.destination(center, 270.0, half_m).lon
    return [LatLon(n, w), LatLon(n, e), LatLon(s, e), LatLon(s, w)]


def perim_incident(k: int) -> FireIncident:
    """Cycle k's perimeter incident: the polygon grows and gets a newer own
    time at k == 2; reported acreage changes every cycle."""
    half = 800.0 if k < 2 else 950.0
    pat = T0 - 3.0 * H if k < 2 else T0 + 0.5 * H
    return FireIncident(
        incident_id="perim-1", name="Perimeter Fire",
        source=IncidentSource.MOCK, origin=PERIM_CENTER,
        updated_at_s=T0 - 600.0 + k * PERIOD_S,
        acreage=[640.0, 700.0, 890.0][k], containment_pct=35.0,
        active=True, perimeter=[square_ring(PERIM_CENTER, half)],
        source_ids={"mock": "perim-1"},
        meta={"perimeter_at_s": pat})


def point_incident(k: int) -> FireIncident:
    return FireIncident(
        incident_id="point-1", name="Point Fire",
        source=IncidentSource.MOCK, origin=POINT_ORIGIN,
        updated_at_s=T0 - 900.0 + k * PERIOD_S,
        acreage=[120.0, 120.0, 180.0][k], containment_pct=0.0,
        active=True, source_ids={"mock": "point-1"})


def clients() -> list:
    return [
        ClientProperty(client_id="c1", loc=ss.NEAR_LOC, name="Casa Uno"),
        ClientProperty(client_id="c2", loc=ss.FAR_LOC, name="Casa Dos"),
        ClientProperty(client_id="c3", loc=LatLon(38.93, -120.88),
                       name="Casa Tres"),
    ]


def belief_cfg(**kw):
    """The pinned config: seeded_stack.small_cfg + the live watcher's
    ensemble posture (model error 0.71, p50 median bound), 4 members."""
    base = dict(ensemble_n=4, model_error_sd_log=0.71,
                ensemble_p50_nominal_bound=True)
    base.update(kw)
    return ss.small_cfg(**base)


_WORLD_WALL_KEYS = ("terrain_s", "weather_s", "spread_s")


def _mask_world(obj):
    """Zero the per-domain wall-clock phase times (CycleResult.timings)."""
    if isinstance(obj, dict):
        return {k: (0.0 if k in _WORLD_WALL_KEYS else _mask_world(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_world(v) for v in obj]
    return obj


def world_digest(summary: dict) -> str:
    return hashlib.sha256(json.dumps(_mask_world(summary), sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()


def make_stack(state_dir, cfg=None, clock=None, weather=None):
    """(daemon, monitor, feed, engine, store) for the belief scenario."""
    feed = ss.ScriptedFeed([perim_incident(0), point_incident(0)])
    daemon, monitor, feed, engine, _n, store, _w, _t = ss.make_stack(
        state_dir, cfg=cfg or belief_cfg(), feed=feed,
        registry_clients=clients(), clock=clock or ss.SettableClock(),
        weather=weather)
    return daemon, monitor, feed, engine, store


def belief_baseline_run(state_dir, cfg=None, cycles=3, keep=None):
    """The pinned scenario: `cycles` cycles one period apart; the feed's
    perimeter/acreage change between cycles. Returns
    ([cycle digests], api digest, state digest, world digest). `keep`
    (a list) receives each CycleResult when given."""
    clock = ss.SettableClock(T0)
    daemon, monitor, feed, engine, store = make_stack(state_dir, cfg=cfg,
                                                      clock=clock)
    digests = []
    for k in range(cycles):
        feed.incidents = [perim_incident(k), point_incident(k)]
        clock.t = T0 + 60.0 + k * PERIOD_S
        res = daemon.run_cycle()
        if keep is not None:
            keep.append(res)
        digests.append(ss.cycle_digest(res))
    clock.t += 30.0
    return (digests, ss.api_digest(daemon.state()), ss.state_digest(store),
            world_digest(daemon.world_summary()))
