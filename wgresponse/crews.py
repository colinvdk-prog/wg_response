"""wgresponse.crews — GPS beacon ingest + crew status tracking.

CrewTracker consumes CrewBeacon pings (phone GPS), keeps one Crew record per
crew_id, ignores out-of-order beacons, marks crews with stale beacons UNKNOWN
(position retained for console display, excluded from dispatch eligibility),
and keeps a short per-crew beacon history ring for the console trail.

Units: all timestamps unix seconds (`_s`); staleness threshold is
cfg.beacon_stale_s (seconds).
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List, Optional

from .contracts import Crew, CrewBeacon, CrewStatus, ResponseConfig

# Beacon history ring size per crew (console trail).
HISTORY_LEN = 50

# Statuses that make a fresh-beacon crew dispatch-eligible.
_ELIGIBLE_STATUSES = frozenset({CrewStatus.AVAILABLE, CrewStatus.EN_ROUTE})


class CrewTracker:
    """Tracks crews from beacons. cfg.beacon_stale_s (seconds) drives staleness."""

    def __init__(self, cfg: ResponseConfig) -> None:
        self._cfg = cfg
        self._crews: Dict[str, Crew] = {}
        self._history: Dict[str, Deque[CrewBeacon]] = {}

    def ingest(self, beacon: CrewBeacon) -> bool:
        """Ingest one beacon (beacon.t_s unix seconds).

        Unknown crew ids are auto-registered. Beacons not strictly newer than
        the crew's last accepted beacon are ignored (out-of-order delivery).
        Returns True if the beacon was accepted.
        """
        crew = self._crews.get(beacon.crew_id)
        if crew is None:
            crew = Crew(crew_id=beacon.crew_id, name=beacon.crew_id)
            self._crews[beacon.crew_id] = crew
            self._history[beacon.crew_id] = deque(maxlen=HISTORY_LEN)
        if crew.last_beacon is not None and beacon.t_s <= crew.last_beacon.t_s:
            return False
        crew.last_beacon = beacon
        crew.status = beacon.status
        self._history[beacon.crew_id].append(beacon)
        return True

    def crews(self) -> List[Crew]:
        """All tracked crews, sorted by crew_id (deterministic)."""
        return [self._crews[cid] for cid in sorted(self._crews)]

    def crew(self, crew_id: str) -> Optional[Crew]:
        """Lookup one crew by id; None if never seen."""
        return self._crews.get(crew_id)

    def history(self, crew_id: str) -> List[CrewBeacon]:
        """Beacon trail for the console, oldest first (last HISTORY_LEN accepted)."""
        return list(self._history.get(crew_id, ()))

    def stale(self, now_s: float) -> List[str]:
        """Crew ids whose last beacon is older than cfg.beacon_stale_s at now_s (unix s).

        Side effect: each stale crew's status is set to UNKNOWN. Last position
        is retained for display; stale crews are never dispatch-eligible.
        """
        out: List[str] = []
        for cid in sorted(self._crews):
            crew = self._crews[cid]
            if self._is_stale(crew, now_s):
                crew.status = CrewStatus.UNKNOWN
                out.append(cid)
        return out

    def eligible(self, now_s: float) -> List[Crew]:
        """Dispatch-eligible crews at now_s (unix s): AVAILABLE or EN_ROUTE
        with a fresh (non-stale) beacon. Sorted by crew_id."""
        return [
            self._crews[cid]
            for cid in sorted(self._crews)
            if not self._is_stale(self._crews[cid], now_s)
            and self._crews[cid].status in _ELIGIBLE_STATUSES
        ]

    def _is_stale(self, crew: Crew, now_s: float) -> bool:
        if crew.last_beacon is None:
            return True
        return (now_s - crew.last_beacon.t_s) > self._cfg.beacon_stale_s
