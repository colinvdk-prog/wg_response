"""Deterministic hazard world for the feed-sanity tests (contracts SAFETY
SEMANTICS 18; docs/review/feed_sanity_2026-09-27.md).

Not a test module (no test_ prefix; imported the way test_fire_belief
imports fire_belief_stack). Five feed fires, each carrying ONE of the live-
data hazards the 2026-09-27 audit measured, run through the REAL monitor /
alert engine / dispatch / StateStore / _WatchDaemon / worldview in the
deployed watcher's posture (belief tracking, fail-closed corridors, the per-
fire off-domain horizon, the confirmed-perimeter anchor, model error 0.71,
the p50 bound), five cycles four hours apart:

  collapse-1  a ~6,200 ac polygon that collapses to a 3.6 ac one for one
              cycle under a NEWER own time while its record still reports
              ~6,250 ac, comes back, then is re-stamped twice unchanged
              (the Sinlahekin / Grasshopper / Sand Creek pattern + the WFIGS
              re-stamp)
  future-1    a polygon stamped 400 days ahead (a future own time: the
              legacy clamp re-observes it every cycle)
  ancient-1   a polygon stamped three years before its fire was discovered,
              under a record that reports more acres than it (the anchor
              keeps the legacy seeding: the domain burns before now)
  reset-1     a point fire whose reported size resets 5,000 -> 500 -> 5,100
              (the Hagen IncidentSize reset)
  figure-1    a ~990 ac polygon whose area figure says 1 ac (a WFIGS
              'Auto-generated for InFORM' polygon), re-mapped later with a
              correct figure

hazard_run() returns ([cycle digests], api digest, state digest, world
digest, belief digest). Its goldens (tests/test_feed_sanity.py) were
computed on commit 0bbd430, BEFORE any feed-sanity code existed
(scratch_review/feed_sanity/fs_golden.py); with cfg.feed_sanity OFF the
build must reproduce them byte for byte.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fire_belief_stack as fbs  # noqa: E402
import seeded_stack as ss  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    ClientProperty,
    FireIncident,
    IncidentSource,
    LatLon,
)

T0 = ss.T0
H = ss.H
DAY = 86400.0
STEP_S = 4.0 * H                 # the cycles run four hours apart
N_CYCLES = 5

COLLAPSE_CENTER = LatLon(39.0, -121.02)
FUTURE_CENTER = LatLon(39.12, -121.10)
ANCIENT_CENTER = LatLon(38.88, -121.10)
RESET_ORIGIN = LatLon(38.93, -120.93)
FIGURE_CENTER = LatLon(39.15, -120.90)


def t_cycle(k: int) -> float:
    return T0 + 60.0 + k * STEP_S


def _perim_fire(iid, name, center, half_m, k, pat, perimeter_acres,
                reported, disc, rep_at=None) -> FireIncident:
    t = t_cycle(k)
    meta = {"perimeter_at_s": pat, "perimeter_acres": perimeter_acres,
            "reported_acres": reported,
            "reported_at_s": t - 0.5 * H if rep_at is None else rep_at}
    return FireIncident(
        incident_id=iid, name=name, source=IncidentSource.MOCK,
        origin=center, updated_at_s=t - 600.0, discovered_at_s=disc,
        acreage=reported, containment_pct=20.0, active=True,
        perimeter=[fbs.square_ring(center, half_m)],
        source_ids={"mock": iid}, meta=meta)


def collapse_fire(k: int) -> FireIncident:
    """big -> 3.6 ac under a newer time -> big again -> re-stamped x2."""
    half, figure = [(2500.0, 6178.0), (60.0, 3.56), (2550.0, 6427.0),
                    (2550.0, 6427.0), (2550.0, 6427.0)][k]
    reported = [6200.0, 6250.0, 6400.0, 6400.0, 6400.0][k]
    return _perim_fire("collapse-1", "Collapse Fire", COLLAPSE_CENTER, half,
                       k, t_cycle(k) - 1.0 * H, figure, reported,
                       T0 - 5.0 * DAY)


def future_fire(k: int) -> FireIncident:
    """A fixed polygon whose own time sits 400 days ahead."""
    return _perim_fire("future-1", "Future Fire", FUTURE_CENTER, 800.0, k,
                       T0 + 400.0 * DAY, 633.0, 640.0, T0 - 2.0 * DAY,
                       rep_at=T0 - 0.5 * H)


def ancient_fire(k: int) -> FireIncident:
    """A fixed polygon stamped three years before its fire was discovered;
    the record reports more acres than it (the anchor keeps the legacy)."""
    return _perim_fire("ancient-1", "Ancient Fire", ANCIENT_CENTER, 800.0, k,
                       T0 - 3.0 * 365.0 * DAY, 633.0, 900.0, T0 - 2.0 * DAY)


def reset_fire(k: int) -> FireIncident:
    """A point fire whose reported size resets for one cycle."""
    acres = [5000.0, 5000.0, 500.0, 5100.0, 5200.0][k]
    t = t_cycle(k)
    return FireIncident(
        incident_id="reset-1", name="Reset Fire", source=IncidentSource.MOCK,
        origin=RESET_ORIGIN, updated_at_s=t - 600.0,
        discovered_at_s=T0 - 3.0 * DAY, acreage=acres, containment_pct=10.0,
        active=True, source_ids={"mock": "reset-1"},
        meta={"reported_acres": acres, "reported_at_s": t - 0.5 * H})


def figure_fire(k: int) -> FireIncident:
    """~988 ac of rings whose area figure says 1 ac, re-mapped at k=2 with a
    correct figure (1,028 ac); own times advance only at k=0 and k=2."""
    if k < 2:
        return _perim_fire("figure-1", "Figure Fire", FIGURE_CENTER, 1000.0,
                           k, t_cycle(0) - 1.0 * H, 1.0, 990.0,
                           T0 - 4.0 * DAY)
    return _perim_fire("figure-1", "Figure Fire", FIGURE_CENTER, 1020.0, k,
                       t_cycle(2) - 1.0 * H, 1028.0, 1030.0, T0 - 4.0 * DAY)


def incidents(k: int) -> list:
    return [collapse_fire(k), future_fire(k), ancient_fire(k), reset_fire(k),
            figure_fire(k)]


def clients() -> list:
    return [
        ClientProperty(client_id="c1", loc=ss.NEAR_LOC, name="Casa Uno"),
        ClientProperty(client_id="c2", loc=ss.FAR_LOC, name="Casa Dos"),
        ClientProperty(client_id="c3", loc=LatLon(38.93, -120.88),
                       name="Casa Tres"),
    ]


def hazard_cfg(**kw):
    """The deployed watcher's posture on the small deterministic config:
    belief tracking, fail-closed corridors + the per-fire off-domain horizon
    (60-min margin), the confirmed-perimeter anchor, model error 0.71 and the
    p50 bound. Keyword overrides on top (e.g. feed_sanity=True)."""
    base = dict(ensemble_n=2, model_error_sd_log=0.71,
                ensemble_p50_nominal_bound=True, fire_belief_track=True,
                corridor_fail_closed=True, corridor_unreached_bound=True,
                corridor_offraster_per_fire=True,
                corridor_offraster_margin_min=60.0,
                perimeter_confirmed_anchor=True)
    base.update(kw)
    return ss.small_cfg(**base)


def belief_digest(store) -> str:
    """sha256 of the belief store's canonical JSON (the fire_beliefs.json
    content without its saved_at_s stamp)."""
    return hashlib.sha256(json.dumps(
        store.to_json(), sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def make_stack(state_dir, cfg=None, clock=None):
    feed = ss.ScriptedFeed(incidents(0))
    daemon, monitor, feed, engine, _n, store, _w, _t = ss.make_stack(
        state_dir, cfg=cfg or hazard_cfg(), feed=feed,
        registry_clients=clients(), clock=clock or ss.SettableClock())
    return daemon, monitor, feed, engine, store


def hazard_run(state_dir, cfg=None, cycles=N_CYCLES, keep=None):
    """The pinned scenario. Returns ([cycle digests], api digest, state
    digest, world digest, belief digest); `keep` (a list) receives each
    CycleResult when given."""
    clock = ss.SettableClock(T0)
    daemon, monitor, feed, engine, store = make_stack(state_dir, cfg=cfg,
                                                      clock=clock)
    digests = []
    for k in range(cycles):
        feed.incidents = incidents(k)
        clock.t = t_cycle(k)
        res = daemon.run_cycle()
        if keep is not None:
            keep.append(res)
        digests.append(ss.cycle_digest(res))
    clock.t += 30.0
    beliefs = monitor.fire_beliefs
    return (digests, ss.api_digest(daemon.state()), ss.state_digest(store),
            fbs.world_digest(daemon.world_summary()),
            belief_digest(beliefs) if beliefs is not None else None)
