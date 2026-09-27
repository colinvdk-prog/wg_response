"""Shared deterministic fixtures for the 2026-09-27 contained-fire / confirmed-
perimeter tests (tests/test_contained_removal.py, tests/test_perimeter_anchor.py).

Not a test module (no test_ prefix). Builds on tests/seeded_stack.py — the
same REAL monitor / alert engine / dispatch / StateStore / _WatchDaemon stack
over stub feeds, weather, terrain and registry, injected clocks, no network.

THE KNOB-RELEVANT WORLD (world_incidents): two feed fires in the seeded_stack
geometry (a west wind; client c1 ~4.3 km east of ORIGIN, c2 ~13 km north-east):

  inc-contained  the Chileno shape: 100 % contained, a small polygon at ORIGIN
                 whose own time is 72 h old on a 10-minute-fresh record that
                 reports the polygon's size. The legacy engine spreads that
                 polygon forward 72 h and walks c1 into the fire.
  inc-anchor     the Willow shape: 80 % contained, a polygon 6 km west of c2
                 whose own time is 100 h old on a 30-minute-fresh record that
                 reports the same size (1 % apart).

baseline_world_run() fingerprints three cycles of that world with every new
knob OFF; the digests were computed on commit a8bebec (BEFORE the removal
rule and the anchor existed) and are pinned in the tests: the knobs OFF must
reproduce every byte of the pre-change engine on the very world they change.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
from wgresponse import geo  # noqa: E402
from wgresponse.contracts import FireIncident, IncidentSource, LatLon  # noqa: E402

T0 = ss.T0
H = ss.H
ACRE_M2 = 4046.8564224

#: the Willow-shaped fire's polygon centre: 6 km due west of c2 (upwind)
ANCHOR_CENTER = geo.destination(ss.FAR_LOC, 270.0, 6000.0)


def square_ring(center: LatLon, half_m: float) -> list:
    """A closed-by-convention square ring (4 corners, clockwise from NW) of
    half-side half_m metres around center."""
    n = geo.destination(center, 0.0, half_m)
    s = geo.destination(center, 180.0, half_m)
    e = geo.destination(center, 90.0, half_m)
    w = geo.destination(center, 270.0, half_m)
    return [LatLon(n.lat, w.lon), LatLon(n.lat, e.lon),
            LatLon(s.lat, e.lon), LatLon(s.lat, w.lon)]


def ring_acres(ring: list) -> float:
    """Planar (cos-lat scaled) shoelace area of a LatLon ring, acres."""
    lat0 = ring[0].lat
    kx = math.cos(math.radians(lat0)) * geo.M_PER_DEG_LAT
    ky = geo.M_PER_DEG_LAT
    xy = [((p.lon - ring[0].lon) * kx, (p.lat - lat0) * ky) for p in ring]
    s = 0.0
    for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]):
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0 / ACRE_M2


CONTAINED_RING = square_ring(ss.ORIGIN, 400.0)
ANCHOR_RING = square_ring(ANCHOR_CENTER, 500.0)


def contained_incident(iid="inc-contained", name="Chileno-shape Fire",
                       containment_pct=100.0, updated_at_s=T0 - 600.0,
                       perimeter_at_s=T0 - 72 * H, acreage=None, meta=None,
                       source=IncidentSource.MOCK, ring=None,
                       origin=None) -> FireIncident:
    """The Chileno shape (module docstring). meta extra keys merge over the
    defaults: the polygon's own time and area, the record's reported size."""
    ring = ring or CONTAINED_RING
    acres = ring_acres(ring)
    m = {"perimeter_at_s": perimeter_at_s, "perimeter_acres": acres,
         "reported_acres": acres if acreage is None else acreage,
         "reported_at_s": updated_at_s}
    m.update(meta or {})
    return FireIncident(incident_id=iid, name=name, source=source,
                        origin=origin or ss.ORIGIN, updated_at_s=updated_at_s,
                        acreage=acres if acreage is None else acreage,
                        containment_pct=containment_pct, active=True,
                        perimeter=[list(ring)], source_ids={source.value: iid},
                        meta=m)


def anchor_incident(iid="inc-anchor", name="Willow-shape Fire",
                    containment_pct=80.0, updated_at_s=T0 - 1800.0,
                    perimeter_at_s=T0 - 100 * H, reported_acres=None,
                    meta=None, ring=None) -> FireIncident:
    """The Willow shape (module docstring): the record reports the polygon's
    size + 1 % unless reported_acres says otherwise."""
    ring = ring or ANCHOR_RING
    poly = ring_acres(ring)
    rep = poly * 1.01 if reported_acres is None else reported_acres
    m = {"perimeter_at_s": perimeter_at_s, "perimeter_acres": poly,
         "reported_acres": rep, "reported_at_s": updated_at_s}
    m.update(meta or {})
    return FireIncident(incident_id=iid, name=name, source=IncidentSource.MOCK,
                        origin=ring[0], updated_at_s=updated_at_s,
                        acreage=rep, containment_pct=containment_pct,
                        active=True, perimeter=[list(ring)],
                        source_ids={"mock": iid}, meta=m)


def world_incidents() -> list:
    return [contained_incident(), anchor_incident()]


def baseline_world_run(state_dir, cfg=None, cycles=3):
    """Three cycles of the knob-relevant world one period apart through the
    REAL stack; returns ([cycle digests], api digest, state digest) with the
    seeded_stack fingerprint functions (wall-clock telemetry masked)."""
    clock = ss.SettableClock(T0)
    daemon, _monitor, _feed, _engine, _notifier, store, _w, _t = ss.make_stack(
        state_dir, cfg=cfg, incidents=world_incidents(), clock=clock)
    digests = []
    for k in range(cycles):
        clock.t = T0 + 60.0 + k * ss.PERIOD_S
        res = daemon.run_cycle()
        digests.append(ss.cycle_digest(res))
    clock.t += 30.0
    return digests, ss.api_digest(daemon.state()), ss.state_digest(store)
