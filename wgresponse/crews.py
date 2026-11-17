"""wgresponse.crews — GPS beacon ingest + crew status tracking.

CrewTracker consumes CrewBeacon pings (phone GPS), keeps one Crew record per
crew_id, ignores out-of-order beacons, marks crews with stale beacons UNKNOWN
(position retained for console display, excluded from dispatch eligibility),
and keeps a short per-crew beacon history ring for the console trail.

Units: all timestamps unix seconds (`_s`); staleness threshold is
cfg.beacon_stale_s (seconds).

PER-CREW IDENTITY — THE ROSTER (2026-11-17, the crew-program review step 3;
contracts SAFETY SEMANTICS 45f). Optional env WGRESP_CREW_ROSTER names a JSON
file of crews, a list (or {"crews": [...]}) of

    {"crew_id": "G1", "name": "Engine 1", "members": 3,
     "key_sha256": "<64 hex: sha256 of that crew's X-Beacon-Key>"}

CrewRoster reads it (and again whenever the file changes); a file it cannot
read or parse makes every lookup raise RosterError — every report is refused
until it is fixed (fail closed; the previous roster is NOT kept). A tracker
built with a roster lists every roster crew from the start (status UNKNOWN,
no position, never dispatch-eligible until it reports), takes its name and
members from the roster, and refuses a report for a crew not on it. The
server checks the crew's own key (sha256 compared in constant time) before a
report reaches the tracker. Only key HASHES live in the roster; no key is
ever logged or echoed. No roster (the default) = the shared-key behaviour of
SEMANTICS 44 and an auto-registering tracker, byte-identical.

CREW STATE SURVIVES A RESTART (SEMANTICS 45g): rows() -> the last report per
crew (the persisted crew_reports rows) and restore_rows(rows) -> how many
crews came back; a restored report keeps its own t_s, so staleness applies.
restore_rows never raises (a malformed row is skipped, loudly).

A lock guards every read and write of the crew table (beacon ingest runs on
the HTTP threads while the cycle reads it — the review's F11 race).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Dict, List, Optional

from .contracts import Crew, CrewBeacon, CrewStatus, LatLon, ResponseConfig

log = logging.getLogger(__name__)

# Beacon history ring size per crew (console trail).
HISTORY_LEN = 50

# Statuses that make a fresh-beacon crew dispatch-eligible.
_ELIGIBLE_STATUSES = frozenset({CrewStatus.AVAILABLE, CrewStatus.EN_ROUTE})

#: SAFETY SEMANTICS 45f: the env naming the roster file.
ROSTER_ENV = "WGRESP_CREW_ROSTER"
#: A roster's crew id / name is at most this long.
_ROSTER_TEXT_MAX = 128
_HEX = frozenset("0123456789abcdef")


class RosterError(Exception):
    """The crew roster cannot be used (unreadable, malformed): every crew
    report is refused until it is fixed (SAFETY SEMANTICS 45f)."""


@dataclass(frozen=True)
class RosterEntry:
    """One rostered crew. key_sha256 = lowercase hex sha256 of its key."""
    crew_id: str
    name: str
    members: int
    key_sha256: str


def parse_roster(text: str) -> "Dict[str, RosterEntry]":
    """Decode a roster file's text -> {crew_id: RosterEntry}. Raises
    RosterError naming the first problem (never a key hash)."""
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise RosterError(f"crew roster is not JSON ({exc})")
    if isinstance(raw, dict):
        raw = raw.get("crews")
    if not isinstance(raw, list):
        raise RosterError('crew roster must be a list of crews (or '
                          '{"crews": [...]})')
    out: Dict[str, RosterEntry] = {}
    for i, row in enumerate(raw):
        where = f"crew roster entry #{i + 1}"
        if not isinstance(row, dict):
            raise RosterError(f"{where} is not an object")
        cid = row.get("crew_id")
        if (not isinstance(cid, str) or not cid.strip()
                or len(cid.strip()) > _ROSTER_TEXT_MAX):
            raise RosterError(f"{where}: crew_id must be a non-empty string "
                              f"(at most {_ROSTER_TEXT_MAX} characters)")
        cid = cid.strip()
        if cid in out:
            raise RosterError(f"{where}: crew {cid!r} is listed twice")
        name = row.get("name", cid)
        if not isinstance(name, str) or len(name) > _ROSTER_TEXT_MAX:
            raise RosterError(f"{where} ({cid}): name must be a string")
        members = row.get("members", 2)
        if (isinstance(members, bool) or not isinstance(members, int)
                or not 1 <= members <= 50):
            raise RosterError(f"{where} ({cid}): members must be an integer "
                              f"1-50")
        key = row.get("key_sha256")
        if (not isinstance(key, str) or len(key.strip()) != 64
                or not set(key.strip().lower()) <= _HEX):
            raise RosterError(f"{where} ({cid}): key_sha256 must be the 64 "
                              f"hex digits of sha256(the crew's key)")
        out[cid] = RosterEntry(crew_id=cid, name=name.strip() or cid,
                               members=members,
                               key_sha256=key.strip().lower())
    return out


def key_sha256(key: str) -> str:
    """The roster's form of a crew key: lowercase hex sha256 of its UTF-8."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class CrewRoster:
    """The crew roster file (SAFETY SEMANTICS 45f), re-read whenever its
    modification time or size changes. Thread-safe."""

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._sig = None
        self._entries: "Optional[Dict[str, RosterEntry]]" = None
        self._error = "crew roster not read yet"

    @classmethod
    def from_env(cls) -> "Optional[CrewRoster]":
        """The roster WGRESP_CREW_ROSTER names, or None when it is unset."""
        path = (os.environ.get(ROSTER_ENV) or "").strip()
        return cls(path) if path else None

    @property
    def path(self) -> Path:
        return self._path

    def _current(self) -> "Dict[str, RosterEntry]":
        try:
            st = os.stat(self._path)
        except OSError as exc:
            with self._lock:
                self._sig, self._entries = None, None
                self._error = (f"crew roster {self._path} unreadable "
                               f"({exc.strerror or exc})")
                raise RosterError(self._error)
        sig = (st.st_mtime_ns, st.st_size)
        with self._lock:
            if sig != self._sig:
                try:
                    entries = parse_roster(
                        self._path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError) as exc:
                    self._entries = None
                    self._error = (f"crew roster {self._path} unreadable "
                                   f"({exc})")
                except RosterError as exc:
                    self._entries = None
                    self._error = f"{exc} ({self._path})"
                else:
                    self._entries = entries
                    self._error = ""
                self._sig = sig
                if self._entries is None:
                    log.error("crews: %s — every crew report is refused "
                              "until it is fixed", self._error)
                else:
                    log.info("crews: roster %s: %d crew(s)", self._path,
                             len(self._entries))
            if self._entries is None:
                raise RosterError(self._error)
            return self._entries

    def entries(self) -> "List[RosterEntry]":
        """Every rostered crew, sorted by crew_id. Raises RosterError."""
        cur = self._current()
        return [cur[k] for k in sorted(cur)]

    def get(self, crew_id: str) -> "Optional[RosterEntry]":
        """The crew's entry, or None when it is not rostered. Raises
        RosterError when the roster cannot be used."""
        return self._current().get(crew_id)

    def verify(self, crew_id: str, key: str) -> bool:
        """True iff crew_id is rostered and sha256(key) equals its
        key_sha256 (a constant-time compare; an unknown crew is compared
        against a dummy so the time does not say which). Raises RosterError
        when the roster cannot be used."""
        entry = self.get(crew_id)
        digest = key_sha256(key or "")
        want = entry.key_sha256 if entry is not None else "0" * 64
        same = hmac.compare_digest(digest.encode("ascii"),
                                   want.encode("ascii"))
        return bool(same and entry is not None and key)

    def status(self) -> dict:
        """The ops surface (no hashes): crews + usable + the error."""
        try:
            return {"crews": len(self._current()), "usable": True,
                    "error": None}
        except RosterError as exc:
            return {"crews": 0, "usable": False, "error": str(exc)}


class CrewTracker:
    """Tracks crews from beacons. cfg.beacon_stale_s (seconds) drives staleness.

    roster (SAFETY SEMANTICS 45f): an optional CrewRoster — its crews are
    listed from the start and only they may report. None = every crew id
    auto-registers on its first report (the legacy tracker)."""

    def __init__(self, cfg: ResponseConfig,
                 roster: "Optional[CrewRoster]" = None) -> None:
        self._cfg = cfg
        self._crews: Dict[str, Crew] = {}
        self._history: Dict[str, Deque[CrewBeacon]] = {}
        self._roster = roster
        self._lock = threading.RLock()

    @property
    def roster(self) -> "Optional[CrewRoster]":
        return self._roster

    def _sync_roster(self) -> None:
        """List every roster crew (status UNKNOWN until it reports); the
        roster names it. An unusable roster changes nothing here (the
        server and ingest refuse every report meanwhile)."""
        if self._roster is None:
            return
        try:
            entries = self._roster.entries()
        except RosterError:
            return
        for e in entries:
            crew = self._crews.get(e.crew_id)
            if crew is None:
                self._crews[e.crew_id] = Crew(crew_id=e.crew_id, name=e.name,
                                              status=CrewStatus.UNKNOWN,
                                              members=e.members)
                self._history[e.crew_id] = deque(maxlen=HISTORY_LEN)
            else:
                crew.name, crew.members = e.name, e.members

    def _rostered(self, crew_id: str) -> bool:
        if self._roster is None:
            return True
        try:
            return self._roster.get(crew_id) is not None
        except RosterError:
            return False

    def ingest(self, beacon: CrewBeacon) -> bool:
        """Ingest one beacon (beacon.t_s unix seconds).

        Unknown crew ids are auto-registered (with a roster: refused).
        Beacons not strictly newer than the crew's last accepted beacon are
        ignored (out-of-order delivery). Returns True if the beacon was
        accepted.
        """
        if not math.isfinite(beacon.t_s):
            # SAFETY SEMANTICS 44: a NaN time never compares newer (every
            # later report would be dropped) and an infinite one never goes
            # stale — refused before it can freeze a crew
            return False
        if not self._rostered(beacon.crew_id):
            # SAFETY SEMANTICS 45f: only a rostered crew reports
            return False
        with self._lock:
            crew = self._crews.get(beacon.crew_id)
            if crew is None:
                crew = Crew(crew_id=beacon.crew_id, name=beacon.crew_id)
                self._crews[beacon.crew_id] = crew
                self._history[beacon.crew_id] = deque(maxlen=HISTORY_LEN)
            if (crew.last_beacon is not None
                    and beacon.t_s <= crew.last_beacon.t_s):
                return False
            crew.last_beacon = beacon
            crew.status = beacon.status
            self._history[beacon.crew_id].append(beacon)
            return True

    def restage(self, beacon: CrewBeacon) -> None:
        """Re-stage a crew at `beacon` REGARDLESS of its timestamp and restart
        its history there. This is a SETUP action, not telemetry: the
        interactive simulator's crew placement (wgresponse.simui
        SimSession.place_crew / reset_crews, before any fire is lit) parks a
        crew somewhere it never drove to, so the old trail is dropped rather
        than joined to the new spot. ingest() keeps its strictly-newer rule
        for real beacons; this is the only path that bypasses it. Unknown
        crew ids are auto-registered, like ingest()."""
        with self._lock:
            crew = self._crews.get(beacon.crew_id)
            if crew is None:
                crew = Crew(crew_id=beacon.crew_id, name=beacon.crew_id)
                self._crews[beacon.crew_id] = crew
            crew.last_beacon = beacon
            crew.status = beacon.status
            self._history[beacon.crew_id] = deque([beacon],
                                                  maxlen=HISTORY_LEN)

    def crews(self) -> List[Crew]:
        """All tracked crews, sorted by crew_id (deterministic)."""
        with self._lock:
            self._sync_roster()
            return [self._crews[cid] for cid in sorted(self._crews)]

    def crew(self, crew_id: str) -> Optional[Crew]:
        """Lookup one crew by id; None if never seen."""
        with self._lock:
            self._sync_roster()
            return self._crews.get(crew_id)

    def history(self, crew_id: str) -> List[CrewBeacon]:
        """Beacon trail for the console, oldest first (last HISTORY_LEN accepted)."""
        with self._lock:
            return list(self._history.get(crew_id, ()))

    def stale(self, now_s: float) -> List[str]:
        """Crew ids whose last beacon is older than cfg.beacon_stale_s at now_s (unix s).

        Side effect: each stale crew's status is set to UNKNOWN. Last position
        is retained for display; stale crews are never dispatch-eligible. A
        rostered crew that has never reported is not listed (it has no GPS
        to go stale; it is never eligible either)."""
        out: List[str] = []
        with self._lock:
            self._sync_roster()
            for cid in sorted(self._crews):
                crew = self._crews[cid]
                if crew.last_beacon is None:
                    continue
                if self._is_stale(crew, now_s):
                    crew.status = CrewStatus.UNKNOWN
                    out.append(cid)
        return out

    def eligible(self, now_s: float) -> List[Crew]:
        """Dispatch-eligible crews at now_s (unix s): AVAILABLE or EN_ROUTE
        with a fresh (non-stale) beacon. Sorted by crew_id."""
        with self._lock:
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

    # ------------------------------------------ SAFETY SEMANTICS 45g: persist

    def rows(self) -> "List[dict]":
        """The last report of every crew that has reported, as JSON-able
        rows (the persisted crew_reports), sorted by crew_id. The status is
        the REPORTED one (stale() may since have marked the crew UNKNOWN)."""
        out = []
        with self._lock:
            for cid in sorted(self._crews):
                crew = self._crews[cid]
                b = crew.last_beacon
                if b is None:
                    continue
                out.append({
                    "crew_id": cid, "name": crew.name or cid,
                    "members": int(crew.members),
                    "status": getattr(b.status, "value", str(b.status)),
                    "lat": float(b.loc.lat), "lon": float(b.loc.lon),
                    "t_s": float(b.t_s), "accuracy_m": float(b.accuracy_m),
                    "speed_mps": float(b.speed_mps),
                    "client_id": b.client_id or ""})
        return out

    def restore_rows(self, rows) -> int:
        """Bring persisted last reports back (a restart). Each row restores
        its crew's last report with its OWN t_s (so a report older than
        beacon_stale_s is stale at once) unless the crew already holds a
        newer one; a crew not on the roster (when one is set) and a
        malformed row are skipped with a log. Never raises. Returns how
        many crews came back."""
        n = 0
        try:
            items = list(rows or [])
        except Exception:
            log.error("crews: persisted crew reports are not a list — none "
                      "restored")
            return 0
        for i, row in enumerate(items):
            try:
                cid = str(row["crew_id"]).strip()
                t_s = float(row["t_s"])
                lat, lon = float(row["lat"]), float(row["lon"])
                if not (cid and math.isfinite(t_s) and -90.0 <= lat <= 90.0
                        and -180.0 <= lon <= 180.0):
                    raise ValueError("crew_id / t_s / lat / lon out of range")
                beacon = CrewBeacon(
                    crew_id=cid, loc=LatLon(lat, lon), t_s=t_s,
                    status=CrewStatus(row.get("status", "unknown")),
                    accuracy_m=float(row.get("accuracy_m", 30.0)),
                    speed_mps=float(row.get("speed_mps", 0.0)),
                    client_id=str(row.get("client_id") or ""))
                if not (math.isfinite(beacon.accuracy_m)
                        and math.isfinite(beacon.speed_mps)):
                    raise ValueError("non-finite accuracy / speed")
                name = str(row.get("name") or cid)
                members = int(row.get("members", 2))
            except Exception as exc:
                log.error("crews: skipping malformed persisted crew report "
                          "#%d (%s)", i, exc)
                continue
            if not self._rostered(cid):
                log.warning("crews: persisted report of crew %s not restored "
                            "— it is not on the crew roster", cid)
                continue
            with self._lock:
                self._sync_roster()
                crew = self._crews.get(cid)
                if crew is None:
                    crew = Crew(crew_id=cid, name=name,
                                members=max(1, members))
                    self._crews[cid] = crew
                if (crew.last_beacon is not None
                        and crew.last_beacon.t_s >= beacon.t_s):
                    continue
                crew.last_beacon = beacon
                crew.status = beacon.status
                self._history[cid] = deque([beacon], maxlen=HISTORY_LEN)
                n += 1
        return n
