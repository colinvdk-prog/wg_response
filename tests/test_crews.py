"""Tests for wgresponse.crews — beacon ordering, staleness, eligibility, ring."""
from __future__ import annotations

import pytest

from wgresponse.contracts import CrewBeacon, CrewStatus, LatLon, ResponseConfig
from wgresponse.crews import HISTORY_LEN, CrewTracker


def beacon(cid, t_s, lat=34.0, lon=-118.0, status=CrewStatus.AVAILABLE):
    return CrewBeacon(crew_id=cid, loc=LatLon(lat, lon), t_s=t_s, status=status)


@pytest.fixture
def cfg():
    return ResponseConfig()  # beacon_stale_s = 600


@pytest.fixture
def tracker(cfg):
    return CrewTracker(cfg)


def test_unknown_crew_auto_registered(tracker):
    assert tracker.crew("alpha") is None
    assert tracker.ingest(beacon("alpha", 100.0)) is True
    crew = tracker.crew("alpha")
    assert crew is not None
    assert crew.crew_id == "alpha"
    assert crew.status is CrewStatus.AVAILABLE
    assert crew.last_beacon.t_s == 100.0
    assert crew.loc() == LatLon(34.0, -118.0)


def test_out_of_order_beacons_ignored(tracker):
    tracker.ingest(beacon("alpha", 200.0, lat=35.0, status=CrewStatus.EN_ROUTE))
    # Older beacon: rejected; position and status unchanged.
    assert tracker.ingest(beacon("alpha", 150.0, lat=36.0, status=CrewStatus.OFF_DUTY)) is False
    crew = tracker.crew("alpha")
    assert crew.last_beacon.t_s == 200.0
    assert crew.loc().lat == 35.0
    assert crew.status is CrewStatus.EN_ROUTE
    # Equal timestamp (duplicate delivery): also rejected.
    assert tracker.ingest(beacon("alpha", 200.0, lat=37.0)) is False
    assert crew.loc().lat == 35.0
    # Newer beacon accepted and status carried.
    assert tracker.ingest(beacon("alpha", 250.0, lat=38.0, status=CrewStatus.DEPLOYING)) is True
    assert crew.loc().lat == 38.0
    assert crew.status is CrewStatus.DEPLOYING
    # Rejected beacons never enter the history trail.
    assert [b.t_s for b in tracker.history("alpha")] == [200.0, 250.0]


def test_crews_listing_sorted(tracker):
    tracker.ingest(beacon("bravo", 10.0))
    tracker.ingest(beacon("alpha", 10.0))
    tracker.ingest(beacon("charlie", 10.0))
    assert [c.crew_id for c in tracker.crews()] == ["alpha", "bravo", "charlie"]


def test_staleness_marks_unknown_but_keeps_position(tracker, cfg):
    tracker.ingest(beacon("alpha", 0.0, lat=35.5))
    tracker.ingest(beacon("bravo", 500.0))
    now = cfg.beacon_stale_s + 1.0  # alpha stale, bravo fresh
    assert tracker.stale(now) == ["alpha"]
    alpha = tracker.crew("alpha")
    assert alpha.status is CrewStatus.UNKNOWN
    assert alpha.last_beacon.loc.lat == 35.5  # position kept for display
    assert tracker.crew("bravo").status is CrewStatus.AVAILABLE
    # Boundary: exactly beacon_stale_s old is still fresh (strictly older).
    assert tracker.stale(500.0 + cfg.beacon_stale_s) == ["alpha"]


def test_fresh_beacon_recovers_stale_crew(tracker, cfg):
    tracker.ingest(beacon("alpha", 0.0))
    now = cfg.beacon_stale_s + 1.0
    tracker.stale(now)
    assert tracker.crew("alpha").status is CrewStatus.UNKNOWN
    tracker.ingest(beacon("alpha", now, status=CrewStatus.AVAILABLE))
    assert tracker.crew("alpha").status is CrewStatus.AVAILABLE
    assert tracker.stale(now + 1.0) == []
    assert [c.crew_id for c in tracker.eligible(now + 1.0)] == ["alpha"]


def test_eligibility_by_status_and_freshness(tracker, cfg):
    now = 1000.0
    tracker.ingest(beacon("avail", now - 10, status=CrewStatus.AVAILABLE))
    tracker.ingest(beacon("enroute", now - 10, status=CrewStatus.EN_ROUTE))
    tracker.ingest(beacon("deploying", now - 10, status=CrewStatus.DEPLOYING))
    tracker.ingest(beacon("egressing", now - 10, status=CrewStatus.EGRESSING))
    tracker.ingest(beacon("offduty", now - 10, status=CrewStatus.OFF_DUTY))
    tracker.ingest(beacon("stale", now - cfg.beacon_stale_s - 1, status=CrewStatus.AVAILABLE))
    assert [c.crew_id for c in tracker.eligible(now)] == ["avail", "enroute"]
    # eligible() does not require stale() to have been called first, and the
    # stale-but-AVAILABLE crew is excluded either way.
    assert "stale" in tracker.stale(now)


def test_history_ring_capped_at_50(tracker):
    for i in range(60):
        tracker.ingest(beacon("alpha", float(i)))
    trail = tracker.history("alpha")
    assert len(trail) == HISTORY_LEN == 50
    assert trail[0].t_s == 10.0    # oldest 10 evicted
    assert trail[-1].t_s == 59.0   # newest kept, oldest-first ordering


def test_history_unknown_crew_empty(tracker):
    assert tracker.history("nope") == []
