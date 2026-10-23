"""Shared fixtures for the co-located fusion tests (contracts SAFETY SEMANTICS
24; tests/test_fuse_colocated.py). Not a test module (no test_ prefix).

THE FIXTURE (tests/fixtures/colocated_2026_10_15.json, built by
scratch_review/colocated/make_fixture.py from a recorded CONUS pull): the
real WFIGS / FIRIS / CAL FIRE payloads of the duplicates that pull holds —
the Bike Fire (WFIGS point "BIKE" + FIRIS "CA-LAC-BIKE-N50X"), Marshall
(CAL FIRE + FIRIS "CA-HIA-MARSHALL-N57B"), Surgone (CAL FIRE + two FIRIS
"SURGONE" flights + FIRIS "CA-HUU-SURGONE-N57B"), Cyrus (WFIGS point +
perimeter, CAL FIRE, FIRIS "CYRUS", FIRIS "CA-SQF-CYRUS-N5852K") — and
nearby distractors that are NOT one fire (two WFIGS twins 6 m apart, two
numbered WFIGS fires 174 m apart, a WFIGS start beside a 100-day-old FIRIS
zombie, an unsized "Marshall 1"). fixture_batches() parses every payload
through the REAL adapters (WfigsFeed / FirisFeed / CalFireFeed over a stub
http_get), so the fused records carry exactly the live field shapes.

bike_2026_10_14_batches(): the reported pair as the live watcher saw it on
2026-10-14 — WFIGS then served the Bike Fire as a PERIMETER record ("Bike
Fire", 171.5 acres; RECONSTRUCTED in the fixture, see its builder) beside
FIRIS's own row of the same polygon.

THE KNOB-RELEVANT WORLD (world_incidents): the Bike pair, parsed from the
fixture through the adapters, then RE-HOMED into the seeded_stack geometry
(every coordinate translated so the FIRIS polygon's main ring sits at
ORIGIN, every time shifted so the pull is T0 — relative geometry and times
exactly the live ones). Legacy fusion solves it as two domains; the knob
merges it. baseline_world_run() fingerprints three cycles of it; the
digests pinned in the tests were computed on commit bf2a868, BEFORE the
rule existed.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
from wgresponse import feeds  # noqa: E402
from wgresponse.contracts import LatLon  # noqa: E402
from wgresponse.sim import jsonable  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                       "colocated_2026_10_15.json")
T0 = ss.T0
H = ss.H

BIKE_IRWIN = "aa44e04e-b724-4adf-8ebf-a945159eb058"
CYRUS_IRWIN = "a5b711d9-8aca-457b-9212-88fa8ca7c9d8"
MARSHALL_CF = "calfire:7f5d9531-c8cc-46cf-b4a2-0287e5f82934"
SURGONE_CF = "calfire:7d84da78-6d70-4661-b7bf-8cfc1c657482"


def load_fixture() -> dict:
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def _http(fx: dict, points=None, perims=None, firis=None, calfire=None):
    """A stub http_get serving the fixture's payloads per layer URL."""
    bodies = {
        feeds.WfigsFeed.POINTS_URL: {"features": fx["wfigs_points"]
                                     if points is None else points},
        feeds.WfigsFeed.PERIMETERS_URL: {"features": fx["wfigs_perimeters"]
                                         if perims is None else perims},
        feeds.FirisFeed.LAYER_URL: {"features": fx["firis"]
                                    if firis is None else firis},
    }

    def get(url, headers=None):
        if url.startswith(feeds.CalFireFeed.LIST_URL):
            body = fx["calfire"] if calfire is None else calfire
            return 200, json.dumps(body).encode("utf-8")
        base = url.split("?", 1)[0]
        return 200, json.dumps(bodies[base]).encode("utf-8")
    return get


def fixture_batches(fx=None, points=None, perims=None, firis=None,
                    calfire=None) -> list:
    """[wfigs, firis, calfire] batches parsed by the REAL adapters (the live
    watcher's feed order), from the fixture or the given payload lists."""
    fx = fx or load_fixture()
    get = _http(fx, points, perims, firis, calfire)
    out = []
    for feed in (feeds.WfigsFeed(http_get=get), feeds.FirisFeed(http_get=get),
                 feeds.CalFireFeed(http_get=get)):
        batch = feed.fetch_active()
        assert feed.last_fetch_ok, type(feed).__name__
        out.append(batch)
    return out


def now_s(fx=None) -> float:
    """The recorded pull's clock (the cycle time the payloads were live at)."""
    return float((fx or load_fixture())["recorded_at_s"])


def firis_rows(fx: dict, *objectids) -> list:
    return [f for f in fx["firis"] if f["attributes"]["OBJECTID"] in objectids]


def wfigs_points(fx: dict, *irwins) -> list:
    want = {i.upper() for i in irwins}
    return [f for f in fx["wfigs_points"]
            if str(f["attributes"]["IrwinID"]).strip("{}").upper() in want]


def calfire_records(fx: dict, *uids) -> list:
    return [r for r in fx["calfire"] if str(r["UniqueId"]).lower() in uids]


def bike_2026_10_14_batches(fx=None) -> list:
    """The reported pair as served on 2026-10-14: WFIGS's PERIMETER record
    'Bike Fire' (171.5 acres; reconstructed, no point) + FIRIS row 1504."""
    fx = fx or load_fixture()
    perim = fx["reconstructed"]["wfigs_bike_perimeter_2026_10_14"]
    return fixture_batches(fx, points=[], perims=[perim],
                           firis=firis_rows(fx, 1504), calfire=[])


def bike_2026_10_15_batches(fx=None) -> list:
    """The reported pair as served on 2026-10-15: WFIGS point 'BIKE' (no
    size, no polygon) + FIRIS row 1504 'CA-LAC-BIKE-N50X'."""
    fx = fx or load_fixture()
    return fixture_batches(fx, points=wfigs_points(fx, BIKE_IRWIN),
                           perims=[], firis=firis_rows(fx, 1504), calfire=[])


# ---------------------------------------------------------------------------
# canonical fingerprint of a fused incident list (byte-identity pins)
# ---------------------------------------------------------------------------

def fused_digest(incidents: list) -> str:
    """sha256 over EVERY field of every fused incident, in output order
    (exact float text via json's repr; source enums by value)."""
    return hashlib.sha256(json.dumps(jsonable(list(incidents)), sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# the knob-relevant world (seeded_stack geometry, the live Bike pair)
# ---------------------------------------------------------------------------

def _shift_meta(meta: dict, dt: float) -> dict:
    return {k: (v + dt if k.endswith("_at_s") and isinstance(v, float) else v)
            for k, v in (meta or {}).items()}


def rehome(inc, dlat: float, dlon: float, dt: float):
    """A copy of `inc` translated by (dlat, dlon) degrees and dt seconds:
    origin, every ring vertex and hotspot; updated / discovered times and
    every meta '*_at_s' stamp. Every other field is the adapter's own."""
    mv = lambda p: LatLon(p.lat + dlat, p.lon + dlon)  # noqa: E731
    return dataclasses.replace(
        inc, origin=mv(inc.origin),
        updated_at_s=inc.updated_at_s + dt,
        discovered_at_s=(None if inc.discovered_at_s is None
                         else inc.discovered_at_s + dt),
        perimeter=(None if inc.perimeter is None
                   else [[mv(p) for p in ring] for ring in inc.perimeter]),
        hotspots=(None if inc.hotspots is None
                  else [mv(p) for p in inc.hotspots]),
        source_ids=dict(inc.source_ids), meta=_shift_meta(inc.meta, dt))


def world_incidents() -> list:
    """[WFIGS 'BIKE' point, FIRIS 'CA-LAC-BIKE-N50X' polygon] re-homed: the
    FIRIS main ring's vertex mean at ss.ORIGIN, the pull at T0."""
    fx = load_fixture()
    wfigs, firis, _cf = bike_2026_10_15_batches(fx)
    (w,), (f,) = wfigs, firis
    main = max(f.perimeter, key=len)
    c = feeds._ring_centroid(main)
    dlat, dlon = ss.ORIGIN.lat - c.lat, ss.ORIGIN.lon - c.lon
    dt = T0 - now_s(fx)
    return [rehome(w, dlat, dlon, dt), rehome(f, dlat, dlon, dt)]


def baseline_world_run(state_dir, cfg=None, cycles=3):
    """Three cycles of the knob-relevant world one period apart through the
    REAL stack; returns ([cycle digests], api digest, state digest)."""
    clock = ss.SettableClock(T0)
    daemon, _m, _feed, _e, _n, store, _w, _t = ss.make_stack(
        state_dir, cfg=cfg, incidents=world_incidents(), clock=clock)
    digests = []
    for k in range(cycles):
        clock.t = T0 + 60.0 + k * ss.PERIOD_S
        res = daemon.run_cycle()
        digests.append(ss.cycle_digest(res))
    clock.t += 30.0
    return digests, ss.api_digest(daemon.state()), ss.state_digest(store)
