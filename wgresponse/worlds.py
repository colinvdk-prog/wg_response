"""wgresponse.worlds — TWO WORLDS: the LIVE map and the TEST map.

Founder ruling 2026-09-27 (verbatim): "we need a test map and a live map so
all simulations or drills do not impact real conditions." contracts SAFETY
SEMANTICS 16 is the law this module implements; docs/LIVE_WORLD_PLAN.md
"Test map and live map (2026-09-27)" is the design record.

The LIVE world is the watch daemon's own stack (cli._WatchDaemon: its locked
ThreatMonitor, the real AlertEngine / CrewTracker / dispatch / notifier /
StateStore). This module owns the TEST world:

    TestWorld            the drill world: its OWN alert engine (a
                         seeded.DrillOnlyNotifier — no client channel at
                         all), its OWN virtual crews, its OWN incumbent plan
                         and seeded store, its OWN StateStore namespace
                         (<state-dir>/test_world/, s3://<prefix>/test_world/).
                         Each test cycle runs over the live cycle's
                         WorldSnapshot (read-only, shared) ∪ its seeds.
    _TestWorldMonitor    a ThreatMonitor whose cycle() — the SAME code the
                         live world runs — takes the live snapshot as its
                         incident set and reuses every live domain verbatim
                         (the ensembles are shared, never recomputed); only
                         seeded domains are computed, through the unchanged
                         ThreatMonitor._assess_incident.
    VirtualCrewTracker   the test crews behind the CrewTracker seam: a clone
                         of the live roster at the drill start, re-cloned by
                         POST /api/test/crews/reset, staged by POST
                         /api/test/crews/place. Virtual: they never beacon,
                         never move, never leave the process.
    TestTerrainOverlay   the test world's terrain: the live LANDFIRE tile
                         cache READ-ONLY, everything else fetched and cached
                         in the test namespace (a drill never writes a live
                         cache file or S3 key).
    split_legacy_seeds   the migration partition for a LIVE state file the
                         pre-two-worlds build wrote with seeds in it.

THE LIVE VIEW. The TestWorld reads the live world only through the view the
daemon attaches (attach_live): latest_snapshot(), live_result(),
live_engine_states(), live_crew_rows(), live_meta(), clone_crews(now_s),
clone_ladder(now_s) and is_busy(). Every one of them READS (dict / deep
copies); none writes, wakes, blocks or reads the clock of the live loop, and
none touches the live registry (the test world asks its own).

DRILL LIFECYCLE (SAFETY SEMANTICS 16c). IDLE: no test cycle runs (zero
cost); the TEST map mirrors the live picture read-only. A drill STARTS on
the first seed or a test-crew reset / placement: the crew roster is cloned
at once, the ladder (records + ledger + growth clocks), the incumbent plan
and the regime postures are cloned while the live loop is idle (the worker
waits for it); from then on each world evolves alone. POST /api/test/reset
wipes the test world and its namespace back to IDLE.

CYCLES. The daemon hands every completed live cycle's snapshot to
on_live_cycle(); the test world then runs ONE test cycle at the live cycle's
own time (lockstep: a live pair on the TEST map moves exactly as on the LIVE
map). A test action (seed, retire, crew staging) re-runs the drill at once
at the test clock. Production runs one worker thread that yields to a
running live cycle; sync=True (tests) runs everything inline. A test-world
failure is logged and never raised into the live loop.

Units per contracts: unix seconds, degrees, meters.
"""
from __future__ import annotations

import copy
import dataclasses
import inspect
import logging
import math
import os
import threading
import time
from pathlib import Path
from typing import Optional

from wgresponse import seeded as seeded_mod
from wgresponse import worldview
from wgresponse.alerts import AlertEngine
from wgresponse.contracts import (
    Crew,
    CrewBeacon,
    CrewStatus,
    DispatchPlan,
    IncidentSource,
    LatLon,
    is_seeded_id,
)
from wgresponse.threat import ThreatMonitor, _ProviderFailure

log = logging.getLogger(__name__)

WORLD_LIVE = "live"
WORLD_TEST = "test"
#: The test world's namespace: <state-dir>/<ns>/ and s3://<prefix>/<ns>/.
TEST_NAMESPACE = "test_world"
#: The page's banner, verbatim (served in /api/test/state too).
TEST_BANNER = ("TEST MAP — drills and simulations. Nothing here affects real "
               "conditions, crews or clients.")

DRILL_IDLE = "idle"
DRILL_STARTING = "starting"         # crews cloned; the ladder clone is pending
DRILL_ACTIVE = "active"

_ELIGIBLE = frozenset({CrewStatus.AVAILABLE, CrewStatus.EN_ROUTE})
#: Statuses a placed test crew may take (POST /api/test/crews/place).
PLACE_STATUSES = ("available", "en_route", "off_duty", "unknown")
_CREW_KEYS = ("crew_id", "lat", "lon", "status", "name")
_TRIGGER_LIVE = "live"
_TRIGGER_ACTION = "action"


def child_s3_uri(uri: Optional[str], child: str) -> Optional[str]:
    """``s3://bucket/prefix`` -> ``s3://bucket/prefix/<child>`` (None stays
    None): the test world's S3 namespace under the live mirror's prefix, so
    its keys can never equal a live key (SAFETY SEMANTICS 16f)."""
    if not uri:
        return None
    return str(uri).rstrip("/") + "/" + str(child).strip("/")


# ---------------------------------------------------------------------------
# the migration partition (SAFETY SEMANTICS 16a)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class LegacySeeds:
    """Drill artifacts a pre-two-worlds build persisted into the LIVE file."""
    rows: list                         # seeded_incidents rows
    records: dict                      # {(cid, iid): AlertRecord}, seeded iid
    ledger: dict                       # {(cid, iid, state): t_s}, seeded iid
    growth: dict                       # {iid: {...}}, seeded iid
    plan: Optional[DispatchPlan]       # the FULL legacy incumbent
    stripped_stops: int = 0
    stripped_unserved: int = 0

    def summary(self) -> dict:
        active = sum(1 for r in self.rows if r.get("retired_at_s") is None)
        return {"seeds": len(self.rows), "active": active,
                "alert_pairs": len(self.records),
                "ledgered_sends": len(self.ledger),
                "growth_clocks": len(self.growth),
                "plan_stops_removed_from_live": self.stripped_stops,
                "plan_unserved_removed_from_live": self.stripped_unserved}

    def note(self) -> str:
        s = self.summary()
        return (f"MIGRATED {s['seeds']} seeded incident(s) ({s['active']} "
                f"active) persisted by the pre-two-worlds build from the LIVE "
                f"state into the TEST world ({TEST_NAMESPACE}/): "
                f"{s['alert_pairs']} alert pair(s), {s['ledgered_sends']} "
                f"ledgered drill send(s) and {s['growth_clocks']} growth "
                f"clock(s) moved with them; the live incumbent plan lost "
                f"{s['plan_stops_removed_from_live']} seeded stop(s) and "
                f"{s['plan_unserved_removed_from_live']} unserved row(s). A "
                f"seeded fire NEVER runs in the live world (SAFETY SEMANTICS "
                f"16a); the drill continues on the TEST map")


def strip_seeded_from_plan(plan):
    """(plan without seeded stops / unserved rows, n_stops, n_unserved) — a
    NEW DispatchPlan (the input is untouched); None -> (None, 0, 0)."""
    if plan is None:
        return None, 0, 0
    n_stops = n_uns = 0
    plans = {}
    for cid, cp in plan.plans.items():
        keep = [st for st in cp.stops if not is_seeded_id(st.incident_id)]
        n_stops += len(cp.stops) - len(keep)
        plans[cid] = dataclasses.replace(cp, stops=keep)
    uns = [u for u in plan.unserved if not is_seeded_id(u.incident_id)]
    n_uns = len(plan.unserved) - len(uns)
    if not n_stops and not n_uns:
        return plan, 0, 0
    return (dataclasses.replace(plan, plans=plans, unserved=uns,
                                objective_breakdown=dict(
                                    plan.objective_breakdown)),
            n_stops, n_uns)


def split_legacy_seeds(loaded: dict):
    """Partition a loaded LIVE state (persist.StateStore.load()) into
    (the live part, LegacySeeds | None). The live part never carries a
    seeded id: no seed rows, no seeded alert pair / ledger entry / growth
    clock, no seeded stop in the incumbent. None when there is nothing to
    migrate (the input is returned unchanged)."""
    rows = [r for r in (loaded.get("seeded_incidents") or [])
            if isinstance(r, dict)]
    recs = dict(loaded.get("alert_records") or {})
    ledger = dict(loaded.get("notified_ledger") or {})
    growth = dict(loaded.get("incident_growth") or {})
    s_recs = {k: r for k, r in recs.items() if is_seeded_id(k[1])}
    s_ledger = {k: t for k, t in ledger.items() if is_seeded_id(k[1])}
    s_growth = {k: g for k, g in growth.items() if is_seeded_id(k)}
    plan = loaded.get("incumbent_plan")
    live_plan, n_stops, n_uns = strip_seeded_from_plan(plan)
    if not (rows or s_recs or s_ledger or s_growth or n_stops or n_uns):
        return loaded, None
    live = dict(loaded)
    live["alert_records"] = {k: r for k, r in recs.items() if k not in s_recs}
    live["notified_ledger"] = {k: t for k, t in ledger.items()
                               if k not in s_ledger}
    live["incident_growth"] = {k: g for k, g in growth.items()
                               if k not in s_growth}
    live["incumbent_plan"] = live_plan
    live["seeded_incidents"] = []
    return live, LegacySeeds(rows=rows, records=s_recs, ledger=s_ledger,
                             growth=s_growth, plan=plan,
                             stripped_stops=n_stops,
                             stripped_unserved=n_uns)


# ---------------------------------------------------------------------------
# virtual crews (SAFETY SEMANTICS 16d)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class VirtualCrew:
    """One TEST crew: where it stands, its status, where it came from."""
    crew_id: str
    name: str
    status: CrewStatus
    loc: Optional[LatLon]
    home_base: Optional[LatLon] = None
    members: int = 2
    origin: str = "live-snapshot"          # | "placed"
    at_s: float = 0.0                      # cloned / placed at (unix s)
    live_status: Optional[str] = None      # the live status at the clone
    live_beacon_t_s: Optional[float] = None
    cloned_stale: bool = False             # the live dispatcher would not use it


def clone_live_crews(tracker, now_s: float) -> "list[VirtualCrew]":
    """The live roster as virtual crews at now_s (READ-ONLY on the tracker:
    crews() and the non-mutating eligible(now_s); never stale(), which flips
    live statuses). A crew the live dispatcher would not use at now_s —
    AVAILABLE / EN_ROUTE with a stale beacon, or already UNKNOWN — is cloned
    UNKNOWN (ineligible, flagged cloned_stale); OFF_DUTY stays OFF_DUTY."""
    if tracker is None:
        return []
    try:
        crews = list(tracker.crews())
    except Exception:
        log.exception("test world: live crew roster unavailable for the "
                      "clone; the drill starts with no crews")
        return []
    eligible_ids = None
    elig = getattr(tracker, "eligible", None)
    if callable(elig):
        try:
            eligible_ids = {c.crew_id for c in elig(now_s)}
        except Exception:
            eligible_ids = None
    out = []
    for c in crews:
        status = c.status
        stale = status is CrewStatus.UNKNOWN
        if (eligible_ids is not None and status in _ELIGIBLE
                and c.crew_id not in eligible_ids):
            status, stale = CrewStatus.UNKNOWN, True
        beacon = c.last_beacon
        out.append(VirtualCrew(
            crew_id=c.crew_id, name=c.name or c.crew_id, status=status,
            loc=c.loc(), home_base=c.home_base,
            members=int(getattr(c, "members", 2) or 2),
            origin="live-snapshot", at_s=float(now_s),
            live_status=getattr(c.status, "value", str(c.status)),
            live_beacon_t_s=(float(beacon.t_s) if beacon else None),
            cloned_stale=stale))
    return out


class VirtualCrewTracker:
    """The TEST world's crews behind the CrewTracker seam (crews / stale /
    eligible) that ThreatMonitor._eligible_crews and dispatch read.

    Virtual crews never beacon and never move; every read hands out FRESH
    contracts.Crew objects (a virtual beacon at the reading time, so a
    virtual crew never 'goes stale' — a crew the live world could not use
    at the clone was cloned UNKNOWN instead). Thread-safe (the HTTP threads
    stage crews while the test cycle reads them)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._crews: "dict[str, VirtualCrew]" = {}
        self.cloned_at_s: Optional[float] = None

    def __len__(self) -> int:
        with self._lock:
            return len(self._crews)

    def load_clone(self, crews: list, now_s: Optional[float]) -> int:
        """Replace the roster with a clone (drill start / crews reset)."""
        with self._lock:
            self._crews = {c.crew_id: c for c in crews}
            self.cloned_at_s = None if now_s is None else float(now_s)
        return len(crews)

    def clear(self) -> None:
        self.load_clone([], None)

    def place(self, crew_id: str, lat: float, lon: float, now_s: float,
              status: str = "available",
              name: Optional[str] = None) -> VirtualCrew:
        """Stage one crew (an existing id moves; a new id joins)."""
        st = CrewStatus(status)
        with self._lock:
            cur = self._crews.get(crew_id)
            vc = VirtualCrew(
                crew_id=crew_id,
                name=(name or (cur.name if cur else crew_id)),
                status=st, loc=LatLon(float(lat), float(lon)),
                home_base=(cur.home_base if cur else None),
                members=(cur.members if cur else 2), origin="placed",
                at_s=float(now_s),
                live_status=(cur.live_status if cur else None),
                live_beacon_t_s=(cur.live_beacon_t_s if cur else None),
                cloned_stale=False)
            self._crews[crew_id] = vc
            return dataclasses.replace(vc)

    # -- the CrewTracker seam ---------------------------------------------------

    @staticmethod
    def _crew(vc: VirtualCrew, t_s: float) -> Crew:
        beacon = (None if vc.loc is None else
                  CrewBeacon(crew_id=vc.crew_id, loc=vc.loc, t_s=float(t_s),
                             status=vc.status))
        return Crew(crew_id=vc.crew_id, name=vc.name, status=vc.status,
                    last_beacon=beacon, home_base=vc.home_base,
                    members=vc.members)

    def crews(self) -> "list[Crew]":
        with self._lock:
            vcs = [self._crews[k] for k in sorted(self._crews)]
        return [self._crew(v, v.at_s) for v in vcs]

    def stale(self, now_s: float) -> "list[str]":
        """Crews cloned from a live crew the live dispatcher could not use
        (stale beacon / UNKNOWN) — reported for the cycle note; nothing is
        mutated (they already carry UNKNOWN)."""
        with self._lock:
            return sorted(k for k, v in self._crews.items()
                          if v.cloned_stale and v.status is CrewStatus.UNKNOWN)

    def eligible(self, now_s: float) -> "list[Crew]":
        with self._lock:
            vcs = [self._crews[k] for k in sorted(self._crews)
                   if self._crews[k].status in _ELIGIBLE
                   and self._crews[k].loc is not None]
        return [self._crew(v, now_s) for v in vcs]

    # -- rows ---------------------------------------------------------------------

    def state_rows(self) -> "list[dict]":
        """The /api/test/state rows (distinct from the live crews')."""
        with self._lock:
            vcs = [self._crews[k] for k in sorted(self._crews)]
        return [{"crew_id": v.crew_id, "name": v.name,
                 "status": v.status.value,
                 "lat": v.loc.lat if v.loc else None,
                 "lon": v.loc.lon if v.loc else None,
                 "t_s": v.at_s, "virtual": True, "world": WORLD_TEST,
                 "origin": v.origin, "live_status": v.live_status,
                 "cloned_stale": v.cloned_stale} for v in vcs]

    def rows(self) -> "list[dict]":
        """Persistence rows (explicit field map)."""
        with self._lock:
            vcs = [self._crews[k] for k in sorted(self._crews)]
        out = []
        for v in vcs:
            out.append({
                "crew_id": v.crew_id, "name": v.name,
                "status": v.status.value,
                "lat": v.loc.lat if v.loc else None,
                "lon": v.loc.lon if v.loc else None,
                "home_lat": v.home_base.lat if v.home_base else None,
                "home_lon": v.home_base.lon if v.home_base else None,
                "members": int(v.members), "origin": v.origin,
                "at_s": float(v.at_s), "live_status": v.live_status,
                "live_beacon_t_s": v.live_beacon_t_s,
                "cloned_stale": bool(v.cloned_stale),
                "cloned_at_s": self.cloned_at_s})
        return out

    def restore(self, rows) -> int:
        """Replace the roster from persisted rows; malformed rows are
        skipped loudly (never raised)."""
        crews, cloned_at = {}, None
        for i, r in enumerate(rows or []):
            try:
                loc = (None if r.get("lat") is None else
                       LatLon(float(r["lat"]), float(r["lon"])))
                home = (None if r.get("home_lat") is None else
                        LatLon(float(r["home_lat"]), float(r["home_lon"])))
                bt = r.get("live_beacon_t_s")
                vc = VirtualCrew(
                    crew_id=str(r["crew_id"]), name=str(r.get("name") or ""),
                    status=CrewStatus(r["status"]), loc=loc, home_base=home,
                    members=int(r.get("members") or 2),
                    origin=str(r.get("origin") or "live-snapshot"),
                    at_s=float(r.get("at_s") or 0.0),
                    live_status=r.get("live_status"),
                    live_beacon_t_s=None if bt is None else float(bt),
                    cloned_stale=bool(r.get("cloned_stale")))
                if r.get("cloned_at_s") is not None:
                    cloned_at = float(r["cloned_at_s"])
            except Exception as exc:
                log.error("test world: skipping malformed persisted test "
                          "crew #%d (%s)", i, exc)
                continue
            crews[vc.crew_id] = vc
        with self._lock:
            self._crews = crews
            self.cloned_at_s = cloned_at
        return len(crews)


# ---------------------------------------------------------------------------
# terrain overlay (SAFETY SEMANTICS 16b/f)
# ---------------------------------------------------------------------------

class TestTerrainOverlay:
    """The TEST world's terrain: a tile the LIVE provider already cached is
    read from the live cache READ-ONLY (never re-written, never re-mirrored);
    everything else comes from ``own`` — a provider rooted in the test
    namespace (its own cache dir and S3 prefix). A drill therefore never
    writes a live cache file or key, yet a seed near a live fire reuses the
    live world's cached LANDFIRE tile when the domain box matches."""

    def __init__(self, live, own) -> None:
        self._live = live
        self._own = own

    def tile(self, bbox, cell_m: float):
        path_fn = getattr(self._live, "cache_path", None)
        path = path_fn(bbox, cell_m) if callable(path_fn) else None
        if path and os.path.exists(path):
            try:
                from wgresponse.terrain import load_ascii_tile
                return self._with_canopy(load_ascii_tile(path), bbox, cell_m)
            except Exception as exc:
                log.warning("test world: live cached tile %s unreadable (%s)"
                            " — fetching in the test namespace", path, exc)
        return self._own.tile(bbox, cell_m)

    def _with_canopy(self, tile, bbox, cell_m: float):
        """CROWN FIRE (SAFETY SEMANTICS 22): when the live provider fetches
        canopy layers, a tile read from the live cache carries them too —
        the live canopy cache read-only, else the test namespace's own
        fetch. A live provider without canopy layers: the tile as read."""
        if not getattr(self._live, "canopy_layer_list", None):
            return tile
        from wgresponse.terrain import attach_canopy, load_ascii_canopy
        cpath = self._live.canopy_cache_path(bbox, cell_m)
        if cpath and os.path.exists(cpath):
            try:
                return attach_canopy(tile, load_ascii_canopy(cpath)[1])
            except Exception as exc:
                log.warning("test world: live cached canopy %s unreadable "
                            "(%s) — fetching in the test namespace", cpath,
                            exc)
        return attach_canopy(tile, self._own.canopy(bbox, cell_m))


def test_terrain_for(live, test_dir, email=None, s3_uri=None):
    """The test world's terrain provider for the daemon's live provider: a
    live LfpsTerrain gets a TestTerrainOverlay whose own LfpsTerrain caches
    in <test_dir>/terrain_cache (+ the test S3 prefix) and fetches the same
    canopy layers as the live one (SAFETY SEMANTICS 22); a stateless
    provider (SyntheticTerrain, a stub) is shared as is — it writes
    nothing."""
    from wgresponse import terrain as terrain_mod
    if isinstance(live, terrain_mod.LfpsTerrain):
        own = terrain_mod.LfpsTerrain(
            cache_dir=str(Path(test_dir) / "terrain_cache"), email=email,
            s3_uri=s3_uri, canopy_layer_list=live.canopy_layer_list)
        return TestTerrainOverlay(live, own)
    return live


# ---------------------------------------------------------------------------
# the test world's monitor: the SAME cycle() over the live snapshot
# ---------------------------------------------------------------------------

_ASSESS_SIG = inspect.signature(ThreatMonitor._assess_incident)


def _readonly_copy(inc):
    """A shallow copy of a live FireIncident with its own meta dict, so no
    code in the test cycle can write into the live world's objects."""
    return dataclasses.replace(inc, meta=dict(inc.meta or {}))


class _TestWorldMonitor(ThreatMonitor):
    """The TEST world's ThreatMonitor (SAFETY SEMANTICS 16b). cycle_over()
    runs the unchanged ThreatMonitor.cycle() with two seams overridden:

      _fetch_and_fuse   returns the live snapshot's (post-gate) incident
                        set — copies with their own meta — and the live
                        feed health, plus the live feed notes / feed-blind
                        events; no feed is fetched.
      _assess_incident  a LIVE incident reuses the live cycle's results
                        verbatim (ensemble, assessments, clients, ETAs,
                        forecast, regime flag, eroded raster, roster; a
                        live-failed assessment fails here too, so its pairs
                        are BLIND in the test world as well); a SEEDED
                        incident runs the real ThreatMonitor._assess_incident.

    Everything after — the seed union, the staleness gate, blind pairs, the
    regime postures, the ladder, the seed auto-retire and the planner — is
    the live code path, on this world's own engine, crews and incumbent.

    seeded_ladder_only=True (an ACTION-triggered re-run): the ladder
    evaluates the SEEDED fires' pairs only (AlertEngine.evaluate
    only_incidents); every live pair keeps the once-per-live-cycle cadence
    it has on the live map (SAFETY SEMANTICS 16b)."""

    _base = None
    _base_ids = frozenset()
    _base_by_incident: dict = {}
    _seeded_ladder_only = False

    def cycle_over(self, snapshot, now_s: float,
                   seeded_ladder_only: bool = False):
        by_inc: dict = {}
        for a in snapshot.assessments:
            by_inc.setdefault(a.incident_id, []).append(a)
        self._base = snapshot
        self._base_ids = frozenset(i.incident_id for i in snapshot.incidents)
        self._base_by_incident = by_inc
        self._seeded_ladder_only = bool(seeded_ladder_only)
        try:
            return self.cycle(now_s)
        finally:
            self._base = None
            self._base_ids = frozenset()
            self._base_by_incident = {}
            self._seeded_ladder_only = False

    def _evaluate_alerts(self, assessments, now_s, blind_pairs,
                         feeds_healthy, incident_meta=None):
        if not self._seeded_ladder_only:
            return super()._evaluate_alerts(assessments, now_s, blind_pairs,
                                            feeds_healthy, incident_meta)
        only = ({k[1] for k in self._alert_engine.states()
                 if is_seeded_id(k[1])}
                | {iid for iid in (incident_meta or {}) if is_seeded_id(iid)})
        return list(self._alert_engine.evaluate(
            [a for a in assessments if is_seeded_id(a.incident_id)], now_s,
            blind_pairs={k for k in blind_pairs if is_seeded_id(k[1])},
            feeds_healthy=feeds_healthy,
            incident_meta={k: v for k, v in (incident_meta or {}).items()
                           if is_seeded_id(k)},
            only_incidents=only))

    def _fetch_and_fuse(self, now_s, notes, operator_events):
        base = self._base
        if base is None:
            notes.append("test world: no live snapshot to build on")
            return [], True
        notes.append(
            f"live world (read-only): {len(base.incidents)} incident(s) as of "
            f"the live cycle at {base.t_s:.0f} — every live domain is shared, "
            f"never recomputed (SAFETY SEMANTICS 16b)")
        for n in base.notes:
            if isinstance(n, str) and (n.startswith("feed[")
                                       or n.startswith("excluded ")):
                notes.append(n)
        operator_events.extend(base.feed_events)
        return [_readonly_copy(i) for i in base.incidents], \
            bool(base.feeds_healthy)

    def _assess_incident(self, inc, now_s, *args, **kwargs):
        base = self._base
        if (base is None or inc.source is IncidentSource.SEEDED
                or inc.incident_id not in self._base_ids):
            return super()._assess_incident(inc, now_s, *args, **kwargs)
        acc = _ASSESS_SIG.bind(self, inc, now_s, *args, **kwargs).arguments
        iid = inc.incident_id
        if iid in base.failed_incidents:
            raise _ProviderFailure("live", RuntimeError(
                "the live world's assessment of this incident failed this "
                "cycle — its pairs are BLIND on the test map too"))
        roster_seen = acc.get("roster_seen")
        if roster_seen is not None and iid in base.roster_seen:
            roster_seen[iid] = set(base.roster_seen[iid])
        ens = base.ensembles.get(iid)
        notes = acc["notes"]
        if ens is None:
            head = f"incident {iid}: "
            for n in base.notes:
                if (isinstance(n, str) and n.startswith(head)
                        and "spread skipped" in n):
                    notes.append(n)
            return
        for key, src in (("forecasts", base.forecasts),
                         ("extreme_flags", base.extreme_flags),
                         ("eroded_map", base.eroded)):
            dst = acc.get(key)
            if dst is not None and iid in src:
                dst[iid] = src[iid]
        acc["ensembles"][iid] = ens
        assessments, clients_seen, etas = (acc["assessments"],
                                           acc["clients_seen"], acc["etas"])
        for a in self._base_by_incident.get(iid, ()):
            assessments.append(a)
            client = base.clients.get(a.client_id)
            if client is not None:
                clients_seen[a.client_id] = client
            eta = base.etas.get((a.client_id, iid))
            if eta is not None:
                etas[(a.client_id, iid)] = eta
        timings = acc.get("timings")
        if timings is not None:
            timings.setdefault("incidents", {})[iid] = {
                "shared_from_live": True,
                "cells": ens.spec.nrows * ens.spec.ncols,
                "clients": len(self._base_by_incident.get(iid, ()))}


# ---------------------------------------------------------------------------
# rows (the /api/test/state shapes; same keys as the live payload + world)
# ---------------------------------------------------------------------------

_STATE_RANK = {"none": 0, "cleared": 1, "notice_72h": 2, "watch": 3,
               "extreme_24h": 4, "impacted": 5}


def incident_row(inc) -> dict:
    """The live /api/state incident row + the world it belongs to."""
    src = getattr(inc.source, "value", None) or str(inc.source)
    row = {"incident_id": inc.incident_id, "name": inc.name,
           "source": src, "lat": inc.origin.lat, "lon": inc.origin.lon,
           "acreage": inc.acreage,
           "containment_pct": inc.containment_pct,
           "updated_at_s": inc.updated_at_s,
           "world": WORLD_TEST if src == "seeded" else WORLD_LIVE,
           "read_only": src != "seeded"}
    if src == "seeded":
        row["seed"] = dict((inc.meta or {}).get("seed") or {})
    return row


def client_rows(registry, aoi, states: dict, assessments) -> list:
    """The live console's client rows (worst pair state, best ETA) over the
    given registry / alert states / assessments."""
    try:
        roster = {c.client_id: c for c in registry.protected_clients(aoi)}
    except Exception:
        log.exception("test world: registry unavailable for /api/test/state")
        roster = {}
    worst: dict = {}
    for (cid, _iid), rec in (states or {}).items():
        sv = getattr(rec.state, "value", None) or str(rec.state)
        cur = worst.get(cid)
        if cur is None or _STATE_RANK.get(sv, 0) > _STATE_RANK.get(cur, 0):
            worst[cid] = sv
    best: dict = {}
    for a in assessments or ():
        cur = best.get(a.client_id)
        if cur is None or a.eta.eta_p50_s < cur[0]:
            best[a.client_id] = (a.eta.eta_p50_s, a.eta.eta_p10_s)
    rows = []
    for cid in sorted(set(roster) | set(worst)):
        c = roster.get(cid)
        eta = best.get(cid)
        rows.append({"client_id": cid, "name": c.name if c else "",
                     "lat": c.loc.lat if c else None,
                     "lon": c.loc.lon if c else None,
                     "state": worst.get(cid, "none"),
                     "eta_p50_s": eta[0] if eta else None,
                     "eta_p10_s": eta[1] if eta else None})
    return rows


def mark_summary(summary: dict, drill: dict) -> dict:
    """A world summary relabelled for the TEST map (in place): every row
    says which world it belongs to; live rows are read-only."""
    for r in summary.get("incidents") or []:
        seeded = r.get("source") == "seeded"
        r["world"] = WORLD_TEST if seeded else WORLD_LIVE
        r["read_only"] = not seeded
    summary["world"] = WORLD_TEST
    summary["drill"] = dict(drill)
    summary["banner"] = TEST_BANNER
    return summary


# ---------------------------------------------------------------------------
# the TEST world
# ---------------------------------------------------------------------------

class TestWorld:
    """The TEST map (contracts SAFETY SEMANTICS 16) — see the module
    docstring. Every public API method returns (http_status, payload) like
    the daemon's other seams; state() / world_summary() return dicts.

    Args: cfg / aoi / safe_zones (the live world's), weather_provider /
    terrain_provider / registry (the test world's OWN instances — see
    cli._cmd_watch), dispatcher (the dispatch module), store (a StateStore
    in the test namespace), clients_by_id (message names), clock (the TEST
    clock, never the live daemon's), sync (tests: run everything inline),
    aoi_desc / period_s (display)."""

    EVENTS_KEEP = 200
    OP_LOG_KEEP = 100
    DRILL_LOG_SHOWN = 60

    __test__ = False            # not a pytest test class (name starts "Test")

    def __init__(self, cfg, aoi, safe_zones, weather_provider,
                 terrain_provider, registry, dispatcher, store,
                 clients_by_id=None, clock=time.time, sync=False,
                 aoi_desc: str = "", period_s: Optional[float] = None):
        self._cfg = cfg
        self._aoi = aoi
        self._aoi_desc = aoi_desc
        self._safe_zones = list(safe_zones)
        self._weather = weather_provider
        self._terrain = terrain_provider
        self._registry = registry
        self._dispatcher = dispatcher
        self._store = store
        self._clients_by_id = dict(clients_by_id or {})
        self._clock = clock
        self._sync = bool(sync)
        self._period_s = period_s
        self._live = None
        self._lock = threading.RLock()        # a test cycle / a reset
        # drill-state transitions (IDLE -> STARTING -> ACTIVE, reset): held
        # for milliseconds, never across a test cycle
        self._state_lock = threading.RLock()
        self._persist_lock = threading.Lock()
        self._flag_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._want_live = False               # a new live snapshot arrived
        self._want_action = False             # a test action asked for a run
        self._pending_start = False           # ladder clone awaits live idle
        self._pending_reset: Optional[str] = None
        self._summary_lock = threading.Lock()
        self._summary_cache = None
        self.restored = False
        self.migrated: Optional[dict] = None
        self.drill_log = seeded_mod.DrillLog(label="TEST MAP")
        self.notifier = seeded_mod.DrillOnlyNotifier(self.drill_log)
        self.engine = AlertEngine(cfg, notifier=self.notifier,
                                  clients_by_id=dict(self._clients_by_id))
        self.crews = VirtualCrewTracker()
        self.monitor = _TestWorldMonitor(
            feeds=[], weather_provider=weather_provider,
            terrain_provider=terrain_provider, registry=registry,
            alert_engine=self.engine, cfg=cfg, safe_zones=self._safe_zones,
            crew_tracker=self.crews, dispatcher=dispatcher, aoi=aoi)
        self._clear_counters()

    def _clear_counters(self) -> None:
        self._drill = {"state": DRILL_IDLE}
        self.cycles_run = 0
        self.last_result = None
        self.last_live_t_s: Optional[float] = None
        self.last_cycle_duration_s: Optional[float] = None
        self.last_trigger: Optional[str] = None
        self._last_t: Optional[float] = None
        self._events: list = []
        self._op_log: list = []
        with self._summary_lock:
            self._summary_cache = None

    # ------------------------------------------------------------- wiring

    def attach_live(self, view) -> None:
        """The daemon's read-only live view (module docstring)."""
        self._live = view

    @property
    def store(self):
        return self._store

    @property
    def drill_state(self) -> str:
        return self._drill.get("state", DRILL_IDLE)

    def start(self) -> None:
        """Start the worker thread (production). No-op in sync mode."""
        if self._sync or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._worker, daemon=True,
                                        name="wgresponse-test-world")
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the worker (after its running cycle); a reset still queued
        behind that cycle is applied here, so a shutdown never loses it."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        if self._pending_reset is not None and (
                self._thread is None or not self._thread.is_alive()):
            with self._lock:
                if self._pending_reset is not None:
                    self._apply_reset(self._pending_reset)

    def _worker(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(1.0)
            if self._stop.is_set():
                return
            if not self._wake.is_set():
                continue
            self._wake.clear()
            try:
                self._wait_live_idle()
                self._service()
            except Exception:
                log.exception("test world: service failed — the LIVE world "
                              "is untouched (SAFETY SEMANTICS 16g)")

    def _wait_live_idle(self) -> None:
        """SEMANTICS 16g: never compete with a running live cycle."""
        live = self._live
        while (live is not None and not self._stop.is_set()
               and live.is_busy()):
            time.sleep(0.25)

    def _request(self, kind: str) -> None:
        with self._flag_lock:
            if kind == _TRIGGER_LIVE:
                self._want_live = True
            else:
                self._want_action = True
        if self._sync:
            self._service()
        else:
            self._wake.set()

    def on_live_cycle(self, snapshot) -> None:
        """The daemon calls this after every completed live cycle (and its
        persist). Returns at once in production — a flag and an event; the
        worker runs the test cycle — and runs inline in sync mode. Takes no
        lock a test cycle holds: the live loop never waits (16g)."""
        if self.drill_state == DRILL_IDLE and not self._pending_start:
            return                               # IDLE: zero cost
        self._request(_TRIGGER_LIVE)

    def _service(self) -> None:
        """Apply a pending reset / drill start, then run at most one test
        cycle for the requests that are pending."""
        with self._lock:
            if self._pending_reset is not None:
                self._apply_reset(self._pending_reset)
            with self._flag_lock:
                want_live, want_action = self._want_live, self._want_action
                self._want_live = self._want_action = False
            if self._pending_start:
                self._start_ladder_now(float(self._clock()))
            if self.drill_state != DRILL_ACTIVE:
                return
            if want_live or want_action:
                self.run_cycle(_TRIGGER_LIVE if want_live else _TRIGGER_ACTION)

    # ---------------------------------------------------------- the drill

    def _drill_view(self) -> dict:
        d = dict(self._drill)
        d["seeds_active"] = len(self.monitor.seeded.active())
        d["crews"] = len(self.crews)
        return d

    def _ensure_drill(self, now_s: float, by: str, reason: str) -> str:
        """Start a drill if none runs (SEMANTICS 16c): clone the crews at
        once; clone the ladder now (sync) or at the next live-idle moment
        (the worker). Returns the drill state."""
        with self._state_lock:
            if self.drill_state in (DRILL_ACTIVE, DRILL_STARTING):
                return self.drill_state
            n = self.crews.load_clone(self._live.clone_crews(now_s)
                                      if self._live is not None else [],
                                      now_s)
            self._drill = {"state": DRILL_STARTING,
                           "started_at_s": float(now_s),
                           "started_by": str(by or ""), "reason": str(reason),
                           "crews_cloned": n,
                           "crews_cloned_at_s": float(now_s)}
            log.warning("TEST MAP: drill starting (%s, by %s) — %d live "
                        "crew(s) cloned as virtual test crews", reason,
                        by or "?", n)
            if self._sync:
                self._start_ladder_now(now_s)
            else:
                self._pending_start = True
            return self.drill_state

    def _start_ladder_now(self, now_s: float) -> None:
        """Clone the live ladder + ledger + growth clocks + incumbent plan +
        regime postures into this world (the live loop is idle: the worker
        waited for it, or this is sync / startup). The drill is ACTIVE."""
        with self._state_lock:
            self._pending_start = False
            if self.drill_state != DRILL_STARTING:
                return                      # reset (or started) meanwhile
            clone = (self._live.clone_ladder(now_s)
                     if self._live is not None else None)
            if clone is not None:
                self.engine.restore(clone["records"],
                                    notified_ledger=clone["ledger"],
                                    incident_growth=clone["growth"])
                self.monitor.restore_incumbent(clone["incumbent"])
                self.monitor._extreme_prev = dict(clone["extreme_prev"])
            self._drill["state"] = DRILL_ACTIVE
            self._drill["ladder_cloned_at_s"] = float(now_s)
            self._drill["pairs_cloned"] = (len(clone["records"]) if clone
                                           else 0)
            log.warning("TEST MAP: drill ACTIVE — %d live alert pair(s) "
                        "cloned; the test world now evolves on its own "
                        "(SAFETY SEMANTICS 16c)",
                        self._drill["pairs_cloned"])
            self.persist_now()

    def run_cycle(self, trigger: str = _TRIGGER_ACTION):
        """One test cycle over the latest live snapshot ∪ the seeds; None
        when no drill is active or no live cycle has completed yet. Time:
        the live cycle's own for a live trigger (lockstep), the test clock
        for an action — never earlier than the snapshot or the last test
        cycle. An ACTION re-run evaluates only the seeded fires' pairs
        (live pairs keep the live cadence); the planner always re-plans."""
        with self._lock:
            if self.drill_state != DRILL_ACTIVE or self._live is None:
                return None
            snap = self._live.latest_snapshot()
            if snap is None:
                return None
            t = (float(snap.t_s) if trigger == _TRIGGER_LIVE
                 else float(self._clock()))
            t = max(t, float(snap.t_s),
                    self._last_t if self._last_t is not None else -math.inf)
            t_wall = time.monotonic()
            # SAFETY SEMANTICS 14: the drill starts from the live world's
            # learned spread rates, a fresh non-persistent copy every cycle
            # ("simulations are always corrected by the real data").
            clone = getattr(self._live, "clone_fire_beliefs", None)
            if callable(clone):
                try:
                    beliefs = clone()
                except Exception:
                    log.exception("test world: live fire beliefs not copied "
                                  "this cycle (the drill keeps its last copy)")
                    beliefs = None
                if beliefs is not None:
                    self.monitor.use_fire_beliefs(beliefs)
            res = self.monitor.cycle_over(
                snap, t, seeded_ladder_only=(trigger != _TRIGGER_LIVE))
            self.last_cycle_duration_s = time.monotonic() - t_wall
            self.cycles_run += 1
            self.last_result = res
            self.last_live_t_s = float(snap.t_s)
            self.last_trigger = trigger
            self._last_t = t
            self._events = (self._events + list(res.events))[
                -self.EVENTS_KEEP:]
            self._op_log = (self._op_log + list(res.operator_events))[
                -self.OP_LOG_KEEP:]
            self.persist_now()
            return res

    # ------------------------------------------------------ persistence

    def persist_now(self, final: bool = False) -> bool:
        """Persist the test world into ITS namespace (never a live key). A
        failure is a loud log + False, never raised."""
        with self._persist_lock:
            try:
                meta = {"world": WORLD_TEST, "drill": dict(self._drill),
                        "cycles_run": self.cycles_run,
                        "last_cycle_t_s": self._last_t,
                        "last_live_t_s": self.last_live_t_s,
                        "extreme_prev": {k: bool(v) for k, v in
                                         self.monitor._extreme_prev.items()},
                        "aoi_desc": self._aoi_desc,
                        "migrated": self.migrated, "final": bool(final)}
                self._store.save(
                    self.engine.states(), self.engine.notified_ledger(),
                    self.monitor.incumbent, meta,
                    incident_growth=self.engine.incident_growth(),
                    seeded_incidents=self.monitor.seeded.rows(),
                    virtual_crews=self.crews.rows(),
                    drill_log=self.drill_log.rows())
                return True
            except Exception:
                log.exception("test world: persist FAILED (the live world "
                              "is untouched)")
                return False

    def restore(self) -> bool:
        """Restore the drill from the test namespace (restart re-fires
        nothing: the ladder and its drill ledger come back together)."""
        try:
            loaded = self._store.load()
        except Exception:
            log.exception("test world: state load raised; starting IDLE")
            return False
        if not loaded:
            return False
        meta = loaded.get("meta") or {}
        with self._lock:
            try:
                self.monitor.restore_seeds(loaded.get("seeded_incidents")
                                           or [])
            except Exception:
                log.exception("test world: seed restore failed")
            self.crews.restore(loaded.get("virtual_crews") or [])
            self.drill_log.restore(loaded.get("drill_log") or [])
            try:
                self.engine.restore(
                    loaded["alert_records"],
                    notified_ledger=loaded["notified_ledger"],
                    incident_growth=loaded["incident_growth"])
            except Exception:
                log.exception("test world: ladder restore failed; the drill "
                              "ladder restarts fresh")
            self.monitor.restore_incumbent(loaded.get("incumbent_plan"))
            xp = meta.get("extreme_prev")
            if isinstance(xp, dict):
                self.monitor._extreme_prev = {str(k): bool(v)
                                              for k, v in xp.items()}
            drill = meta.get("drill") if isinstance(meta.get("drill"),
                                                    dict) else {}
            state = drill.get("state")
            if state in (DRILL_ACTIVE, DRILL_STARTING):
                self._drill = dict(drill)
                if state == DRILL_STARTING:
                    self._pending_start = True
            else:
                self._drill = {"state": DRILL_IDLE}
            try:
                self.cycles_run = int(meta.get("cycles_run") or 0)
            except (TypeError, ValueError):
                self.cycles_run = 0
            for attr, key in (("_last_t", "last_cycle_t_s"),
                              ("last_live_t_s", "last_live_t_s")):
                v = meta.get(key)
                setattr(self, attr, float(v) if isinstance(v, (int, float))
                        and not isinstance(v, bool) else None)
            if isinstance(meta.get("migrated"), dict):
                self.migrated = dict(meta["migrated"])
            self.restored = True
        log.info("test world: restored the drill (%s; %d seed(s), %d alert "
                 "pair(s), %d test crew(s)) from %s", self.drill_state,
                 len(self.monitor.seeded), len(self.engine.states()),
                 len(self.crews), self._store.path)
        return True

    def adopt_legacy(self, legacy: LegacySeeds,
                     now_s: Optional[float] = None) -> dict:
        """SEMANTICS 16a migration: take the drill artifacts a pre-two-worlds
        build persisted into the LIVE file (split_legacy_seeds). Starts a
        drill when none runs (cloning the just-restored live world — at
        startup the live loop is idle), merges the seeds / pairs / ledger /
        growth clocks by id (never overwriting what the test world already
        holds), keeps the full legacy incumbent as the drill's warm start,
        and persists into the test namespace at once. now_s defaults to the
        TEST clock (the live clock is never read)."""
        if now_s is None:
            now_s = float(self._clock())
        with self._lock:
            if self.drill_state != DRILL_ACTIVE:
                if self.drill_state == DRILL_IDLE:
                    self._ensure_drill(now_s, "migration",
                                       "seeds migrated from the live state")
                if self.drill_state != DRILL_ACTIVE:
                    self._start_ladder_now(now_s)
            have = {e.ignition.incident_id
                    for e in self.monitor.seeded.entries()}
            add = [r for r in legacy.rows if r.get("incident_id") not in have]
            if add:
                self.monitor.restore_seeds(self.monitor.seeded.rows() + add)
            recs = dict(self.engine.states())
            for k, r in legacy.records.items():
                recs.setdefault(k, r)
            ledger = dict(self.engine.notified_ledger())
            for k, t in legacy.ledger.items():
                ledger.setdefault(k, t)
            growth = dict(self.engine.incident_growth())
            for k, g in legacy.growth.items():
                growth.setdefault(k, g)
            self.engine.restore(recs, notified_ledger=ledger,
                                incident_growth=growth)
            if legacy.plan is not None and (legacy.stripped_stops
                                            or legacy.stripped_unserved):
                self.monitor.restore_incumbent(legacy.plan)
            self.migrated = dict(legacy.summary(), at_s=float(now_s),
                                 note=legacy.note())
            self.persist_now()
            return dict(self.migrated)

    # --------------------------------------------------------- the reset

    def _apply_reset(self, by: str) -> None:
        """Wipe the test world back to IDLE (in place; the caller holds the
        cycle lock or runs sync) and persist the empty state into the test
        namespace (restart-safe: a restart comes back IDLE)."""
        with self._state_lock:
            self._pending_reset = None
            self._pending_start = False
            with self._flag_lock:
                self._want_live = self._want_action = False
            self.monitor.restore_seeds([])
            self.engine.restore({}, notified_ledger={}, incident_growth={})
            self.engine.pop_operator_events()
            self.monitor.restore_incumbent(None)
            self.monitor._extreme_prev = {}
            self.crews.clear()
            self.drill_log.restore([])
            self.migrated = None
            self._clear_counters()
            self.persist_now()
        log.warning("TEST MAP: reset by %s — the test world is IDLE again "
                    "(seeds, ladder, plan, crews and drill log wiped)", by)

    # ------------------------------------------------------------- views

    def _seed_row(self, entry, states: Optional[dict] = None) -> dict:
        ign = entry.ignition
        iid = ign.incident_id
        row = ign.summary()
        row.update({
            "world": WORLD_TEST,
            "status": "active" if entry.active else "retired",
            "seq": entry.seq,
            "retired_at_s": entry.retired_at_s,
            "retired_reason": entry.retired_reason or None,
            "retired_by": entry.retired_by or None,
            "weather_label": seeded_mod.weather_label(ign),
            "in_last_cycle": False, "domain": None, "pairs": {},
        })
        res = self.last_result
        if res is not None:
            row["in_last_cycle"] = any(i.incident_id == iid
                                       for i in res.incidents)
            ens = (res.ensembles or {}).get(iid)
            if ens is not None:
                tm = ((res.timings or {}).get("incidents") or {}).get(iid) \
                    or {}
                sp = ens.spec
                row["domain"] = {"north_lat": sp.north_lat,
                                 "west_lon": sp.west_lon, "cell_m": sp.cell_m,
                                 "nrows": sp.nrows, "ncols": sp.ncols,
                                 "cells": sp.nrows * sp.ncols,
                                 "clients": tm.get("clients"),
                                 "phases_s": {k: tm.get(k) for k in (
                                     "terrain_s", "weather_s", "spread_s")}}
        if states is None:
            states = self.engine.states()
        pairs: dict = {}
        for (_cid, pid), rec in states.items():
            if pid == iid:
                sv = getattr(rec.state, "value", None) or str(rec.state)
                pairs[sv] = pairs.get(sv, 0) + 1
        row["pairs"] = dict(sorted(pairs.items()))
        return row

    def seed_rows(self) -> list:
        states = self.engine.states()
        return [self._seed_row(e, states)
                for e in self.monitor.seeded.entries()]

    def state(self) -> dict:
        """The /api/test/state payload: the live payload's keys (so the page
        renders both worlds with one code path) + world / banner / drill /
        seeds / live_crews / drill_log. IDLE (no test cycle yet): the live
        picture, read-only, with the would-be starting ladder."""
        live = self._live
        meta = live.live_meta() if live is not None else {}
        out = {
            "world": WORLD_TEST,
            "live": False,
            "banner": TEST_BANNER,
            "drill": self._drill_view(),
            "aoi": meta.get("aoi"), "bbox": meta.get("aoi"),
            "aoi_desc": meta.get("aoi_desc", self._aoi_desc),
            "poll_period_s": meta.get("poll_period_s", self._period_s),
            "cycles_run": self.cycles_run,
            "restored": self.restored,
            "migrated": self.migrated,
            "state_mirror": (self._store.mirror_status()
                             if hasattr(self._store, "mirror_status")
                             else None),
            "safe_zones": list(meta.get("safe_zones") or []),
            "crews": self.crews.state_rows(),
            "live_crews": (live.live_crew_rows() if live is not None
                           else []),
            "seeds": self.seed_rows(),
            "max_active": self.monitor.seeded.max_active,
            "events": list(self._events),
            "drill_log": self.drill_log.rows()[-self.DRILL_LOG_SHOWN:],
            "operator_log": list(self._op_log),
        }
        res = self.last_result
        if res is None:
            lres = live.live_result() if live is not None else None
            if lres is None:
                out.update({"degraded": False, "operator_events": [],
                            "notes": ["no live cycle has completed yet"],
                            "incidents": [], "clients": [], "plan": None})
                return out
            if self.drill_state == DRILL_IDLE:
                note = ("no drill running — the TEST map mirrors the live "
                        "picture read-only; seed a fire (or reset / place "
                        "test crews) to start a drill")
            else:
                note = ("drill " + self.drill_state + " — the first test "
                        "cycle runs as soon as the live loop is idle; until "
                        "then the TEST map shows the live picture read-only")
            out.update({
                "now_s": lres.t_s, "last_cycle_t_s": lres.t_s,
                "live_cycle_t_s": lres.t_s,
                "last_cycle_age_s": max(0.0, float(self._clock()) - lres.t_s),
                "degraded": bool(lres.degraded),
                "operator_events": [],
                "notes": [note],
                "incidents": [incident_row(i) for i in lres.incidents],
                "clients": client_rows(self._registry, self._aoi,
                                       live.live_engine_states(),
                                       lres.assessments),
                "plan": None,
            })
            return out
        out.update({
            "now_s": res.t_s,
            "last_cycle_t_s": res.t_s,
            "live_cycle_t_s": self.last_live_t_s,
            "last_cycle_age_s": max(0.0, float(self._clock()) - res.t_s),
            "last_cycle_duration_s": self.last_cycle_duration_s,
            "last_cycle_trigger": self.last_trigger,
            "last_cycle_phases": dict(res.timings or {}),
            "degraded": bool(res.degraded),
            "operator_events": list(res.operator_events),
            "notes": list(res.notes),
            "incidents": [incident_row(i) for i in res.incidents],
            "clients": client_rows(self._registry, self._aoi,
                                   self.engine.states(), res.assessments),
            "plan": res.plan,
        })
        return out

    def world_summary(self, since=None) -> dict:
        """GET /api/world/summary?world=test: worldview.build_summary over
        THIS world's state and result (rows marked live / test), built once
        per test cycle; IDLE: the same read model over the live result, the
        test world's own copy (never the live summary cache). Several test
        cycles can share a cycle time (a test action re-runs the drill), so
        the summary carries a `rev` token and ?since=<rev> answers
        {"unchanged": true} only when nothing moved."""
        res = self.last_result
        live = self._live
        if res is None:
            res = live.live_result() if live is not None else None
            key = ("idle", None if res is None else float(res.t_s),
                   self.drill_state, len(self.monitor.seeded))
        else:
            key = ("drill", float(res.t_s), self.cycles_run)
        rev = ":".join(str(k) for k in key)
        with self._summary_lock:
            cached = self._summary_cache
        if cached is None or cached[0] != key:
            state = self.state()
            if res is None:
                summary = worldview.summary_from_state(state)
                summary["note"] = "no live cycle has completed yet"
            else:
                summary = worldview.build_summary(state, res)
            summary = mark_summary(summary, self._drill_view())
            summary["rev"] = rev
            with self._summary_lock:
                self._summary_cache = cached = (key, summary)
        summary = cached[1]
        if since not in (None, "") and str(since) == summary.get("rev"):
            return {"unchanged": True, "t_s": summary.get("t_s"),
                    "rev": summary.get("rev"),
                    "cycles_run": summary.get("cycles_run"),
                    "world": WORLD_TEST}
        return summary

    # ------------------------------------------------------------- the API

    def add_seed(self, ign, by: str = "tooling"):
        """Store a pre-built contracts.SeededIgnition (tests, tooling) and
        start the drill if none runs; it joins the NEXT test cycle (no
        re-run is triggered — the monitor-level add). Raises like
        ThreatMonitor.add_seed."""
        out = self.monitor.add_seed(ign)
        self._ensure_drill(float(self._clock()), by,
                           f"seed {ign.incident_id}")
        self.persist_now()
        return out

    def seed_list(self):
        rows = self.seed_rows()
        return 200, {"world": WORLD_TEST, "seeds": rows,
                     "active": sum(1 for r in rows
                                   if r["status"] == "active"),
                     "max_active": self.monitor.seeded.max_active,
                     "drill": self._drill_view()}

    def seed_get(self, incident_id: str):
        entry = self.monitor.seeded.entry(incident_id)
        if entry is None:
            return 404, {"error": f"unknown seed {incident_id!r}"}
        return 200, {"world": WORLD_TEST, "seed": self._seed_row(entry)}

    def seed_create(self, payload):
        """POST /api/test/seed: validate (400; the watch AOI bounds it),
        light the fire at the TEST clock (409 at the active cap), start the
        drill if none runs, persist the test namespace at once and re-run
        the drill. 201 {incident_id, seed, drill}."""
        try:
            kw = seeded_mod.parse_seed_request(payload, self._aoi)
        except ValueError as e:
            return 400, {"error": str(e)}
        now = float(self._clock())
        try:
            ign = self.monitor.create_seed(
                kw["lat"], kw["lon"], now, intensity=kw["intensity"],
                weather=kw["weather"], wind_dir_deg=kw["wind_dir_deg"],
                created_by=kw["created_by"], note=kw["note"])
        except seeded_mod.SeedConflict as e:
            return 409, {"error": str(e)}
        except ValueError as e:
            return 400, {"error": str(e)}
        self._ensure_drill(now, kw["created_by"],
                           f"seed {ign.incident_id}")
        persisted = self.persist_now()
        log.warning("TEST MAP: SEEDED fire %s lit at (%.5f, %.5f) by %s — %s",
                    ign.incident_id, ign.loc.lat, ign.loc.lon,
                    ign.created_by, seeded_mod.weather_label(ign))
        self._request(_TRIGGER_ACTION)
        return 201, {"ok": True, "world": WORLD_TEST,
                     "incident_id": ign.incident_id, "persisted": persisted,
                     "drill": self._drill_view(),
                     "takes_effect": ("the TEST world re-runs now over the "
                                      "latest live snapshot (drill only — "
                                      "the live world is untouched)"),
                     "seed": self._seed_row(
                         self.monitor.seeded.entry(ign.incident_id))}

    def seed_retire(self, incident_id: str, by: str = "api"):
        now = float(self._clock())
        try:
            entry = self.monitor.retire_seed(incident_id, now,
                                             by=by or "api")
        except KeyError:
            return 404, {"error": f"unknown seed {incident_id!r}"}
        except seeded_mod.SeedAlreadyRetired as e:
            return 409, {"error": str(e)}
        persisted = self.persist_now()
        log.warning("TEST MAP: SEEDED fire %s retired by %s", incident_id,
                    entry.retired_by)
        self._request(_TRIGGER_ACTION)
        return 200, {"ok": True, "world": WORLD_TEST,
                     "incident_id": incident_id,
                     "retired_at_s": entry.retired_at_s,
                     "persisted": persisted,
                     "ladder": ("absent from the next TEST cycle; its pairs "
                                "follow the absence law like a vanished feed "
                                "incident — clear streak toward CLEARED while "
                                "every live feed is healthy, frozen while any "
                                "is blind (SAFETY SEMANTICS 6/12e)"),
                     "seed": self._seed_row(entry)}

    @staticmethod
    def _by(payload) -> "tuple[Optional[str], Optional[str]]":
        """(by, error) from an optional {"by": name} body."""
        if payload is None:
            return "api", None
        if not isinstance(payload, dict):
            return None, "body must be a JSON object"
        extra = sorted(set(payload) - {"by"})
        if extra:
            return None, f"unknown keys {extra}; allowed: ['by']"
        by = payload.get("by", "api")
        if by is None:
            by = "api"
        if not isinstance(by, str) or not by.strip() or len(by) > 64:
            return None, "by must be a non-empty string of at most 64 chars"
        return by.strip(), None

    def crews_reset(self, payload=None):
        """POST /api/test/crews/reset: re-clone the virtual crews from the
        live roster NOW (positions + statuses; placements dropped). Starts a
        drill when none runs."""
        by, err = self._by(payload)
        if err:
            return 400, {"error": err}
        now = float(self._clock())
        with self._state_lock:
            if self.drill_state == DRILL_IDLE:
                self._ensure_drill(now, by, "test crews reset")
            else:
                self.crews.load_clone(self._live.clone_crews(now)
                                      if self._live is not None else [], now)
                self._drill["crews_cloned"] = len(self.crews)
                self._drill["crews_cloned_at_s"] = now
        persisted = self.persist_now()
        self._request(_TRIGGER_ACTION)
        return 200, {"ok": True, "world": WORLD_TEST, "by": by,
                     "crews": self.crews.state_rows(),
                     "persisted": persisted, "drill": self._drill_view()}

    def crews_place(self, payload):
        """POST /api/test/crews/place {crew_id, lat, lon, status?, name?}:
        stage one virtual crew (an existing id moves, a new id joins).
        Starts a drill when none runs (the clone first, then the
        placement)."""
        if not isinstance(payload, dict):
            return 400, {"error": "body must be a JSON object"}
        unknown = sorted(set(payload) - set(_CREW_KEYS))
        if unknown:
            return 400, {"error": f"unknown crew keys {unknown}; allowed: "
                                  f"{list(_CREW_KEYS)}"}
        cid = payload.get("crew_id")
        if not isinstance(cid, str) or not cid.strip() or len(cid) > 64:
            return 400, {"error": "crew_id must be a non-empty string of at "
                                  "most 64 characters"}
        vals = {}
        for k in ("lat", "lon"):
            v = payload.get(k)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return 400, {"error": f"{k} must be a number"}
            if not math.isfinite(v):
                return 400, {"error": f"{k} must be finite"}
            vals[k] = float(v)
        if not (-90.0 <= vals["lat"] <= 90.0
                and -180.0 <= vals["lon"] <= 180.0):
            return 400, {"error": "lat/lon out of range (lat ±90, lon ±180)"}
        status = payload.get("status", "available")
        if status not in PLACE_STATUSES:
            return 400, {"error": f"status must be one of "
                                  f"{list(PLACE_STATUSES)}"}
        name = payload.get("name")
        if name is not None and (not isinstance(name, str) or len(name) > 64):
            return 400, {"error": "name must be a string of at most 64 "
                                  "characters"}
        now = float(self._clock())
        if self.drill_state == DRILL_IDLE:
            self._ensure_drill(now, "api", f"test crew {cid.strip()} placed")
        vc = self.crews.place(cid.strip(), vals["lat"], vals["lon"], now,
                              status=status, name=name)
        persisted = self.persist_now()
        self._request(_TRIGGER_ACTION)
        (row,) = [r for r in self.crews.state_rows()
                  if r["crew_id"] == vc.crew_id]
        return 200, {"ok": True, "world": WORLD_TEST, "crew": row,
                     "persisted": persisted, "drill": self._drill_view(),
                     "note": ("a virtual test crew — it never beacons, never "
                              "moves and no real crew is ever dispatched "
                              "from the TEST map (SAFETY SEMANTICS 16d)")}

    def reset(self, payload=None):
        """POST /api/test/reset: wipe the test world back to IDLE — seeds,
        ladder, plan, crews, drill log and its namespace's state (never a
        live byte). 200 applied; 202 queued behind a running test cycle
        (applied before the next one)."""
        by, err = self._by(payload)
        if err:
            return 400, {"error": err}
        if self._sync or self._lock.acquire(blocking=False):
            try:
                self._apply_reset(by)
            finally:
                if not self._sync:
                    self._lock.release()
            return 200, {"ok": True, "world": WORLD_TEST, "by": by,
                         "drill": self._drill_view(),
                         "note": "the TEST map is IDLE again; the live world "
                                 "was never touched"}
        self._pending_reset = by
        self._wake.set()
        return 202, {"ok": True, "world": WORLD_TEST, "by": by,
                     "queued": True,
                     "note": "a test cycle is running; the reset applies "
                             "before the next one"}
