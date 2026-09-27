"""wgresponse.threat — ThreatMonitor: the full per-cycle orchestration loop.

cycle(now_s): fetch+fuse live fire incidents -> per active incident build a
spread domain around ignition geometry + nearby protected clients -> terrain
tile + weather forecast (fetched at the FIRE CENTROID — weather at the fire
drives spread; Council-1 model F7) -> spread.ensemble_arrival ->
ThreatAssessment per in-domain client -> AlertEngine.evaluate -> optionally
dispatcher.plan for client/incident pairs whose alert state is NOTICE_72H /
WATCH / EXTREME_24H. The dispatcher receives EVERY assessed ensemble, not
just those that produced jobs, so corridor checks see jobless fires too
(Council-1 dispatch F1).

Degradation policy (SAFETY SEMANTICS 6 — Council-1 OPS-1): every external
step (feed fetch, fusion, registry, terrain, weather, alert evaluation, crew
tracking, dispatch) is guarded; a failed step appends a human-readable string
to CycleResult.notes and the cycle continues — the monitor never raises out
of cycle(). Provider failures additionally: (a) mark the cycle
CycleResult.degraded=True, (b) emit operator-facing OperatorEvents
('feed_blind' for an unhealthy fire feed, 'provider_error' for a
weather/terrain/registry failure), and (c) feed the assessed/blind
distinction into AlertEngine.evaluate(assessments, now_s, blind_pairs=...,
feeds_healthy=...) so an outage FREEZES alert states instead of walking them
to a false CLEARED. Feed health follows the contracts `last_fetch_ok`
convention (feeds returning [] with last_fetch_ok=False are BLIND, not
clear). Legacy 2-argument alert engines (hand stubs) are still supported —
the rich call degrades to evaluate(assessments, now_s).

SILENT PARTIAL REGISTRY (Sprint 2, Council-1 verifier finding): a registry
that returns a syntactically-healthy but INCOMPLETE roster (partial scan,
pagination bug, upstream geocode regression dropping lat/lon rows) raises no
exception, yet it must never walk a threatened pair to a false all-clear.
Contracts SAFETY SEMANTICS 6 defaults every unassessed pair to FREEZE and
enumerates exactly two trustworthy-clear absences: (a) incident ASSESSED and
no longer threatening, (b) incident absent while every fire feed is healthy.
A pair whose incident is still ACTIVE this cycle but whose CLIENT was not in
the registry's candidate roster for that incident matches NEITHER clause —
nobody assessed that the fire no longer threatens that client — so cycle()
marks it BLIND (frozen + operator-alarmed + cycle degraded, kind
'provider_error', step 'registry_roster'). A client the registry DID return
who merely sits beyond the plausible threat radius is a genuine assessed-
clear absence and keeps progressing its clear streak. Consequence: a
legitimate unenrollment during an active incident freezes (alarms) until an
explicit unenroll convention exists — fail-safe over fail-open, per Council.

PERIMETER INCIDENTS (Sprint 2 ticket A5, Council-1 model F4): _assess_incident
passes incident.perimeter (rings subsampled <= 200 vertices) plus the
PERIMETER'S OWN provenance time into spread — meta["perimeter_at_s"] when the
feeds layer carried it (Session-3 verifier F3: on a fused record updated_at_s
is the freshest MEMBER's time, and stamping an older member's polygon at the
fused-fresh time ran the modeled front up to 47.8 h late), else the record's
updated_at_s — so the polygon interior is already burned at the polygon's
timestamp (inside clients read hours_out 0 -> IMPACTED ladder path) while
FIRMS hotspots still seed at now even when a perimeter exists. Spread
seeds are built explicitly here; contracts.FireIncident.ignition_points() is
unchanged and remains the no-perimeter seed law.

DATA HYGIENE (Sprint 2 ticket C3a/e, contracts SAFETY SEMANTICS 9, Council-1
OPS-6): the monitor — not the feeds — gates zombie incidents. Active
incidents whose updated_at_s is older than cfg.incident_stale_h (default
168 h = 7 days; live FIRIS "current" perimeters measured at MEDIAN 49 days
old, all active=True) are EXCLUDED from this cycle before assessment: no
spread compute, no alert evaluation, no dispatch jobs, logged + a CycleResult
note ('excluded N stale incident(s) ...'). Feeds still return the records
(data preserved; the gate is monitor-side so an operator can lower/raise
incident_stale_h without re-fetching). A tracked pair whose incident is
gated follows the ordinary ABSENCE rules of SAFETY SEMANTICS 6: with every
feed healthy the multi-source 7-day silence is a trustworthy absence and the
pair walks to CLEARED through normal hysteresis (freezing it forever would
alarm operators for every long-dead zombie); during any outage it freezes
like every other unassessed pair. Clock skew is clamped the opposite way:
perimeter_at_s = min(updated_at_s, now_s) before seeding, so a feed clock
running ahead of ours can never stamp a perimeter interior as burning in the
future (which would freeze interior clients' hours_out above zero).

Collaborator seams (contracts law): feeds FireFeed / WeatherProvider /
TerrainProvider / ClientRegistry protocols, AlertEngine.evaluate/.states
(+ optional pop_operator_events), CrewTracker.crews, dispatcher.plan(crews,
jobs, ens, safe_zones, cfg, now_s, incumbent). AlertEngine, CrewTracker and
the dispatcher are INJECTED and wgresponse.feeds is imported lazily inside
the method (with an id-dedupe fallback), so this module stays testable with
hand stubs while sibling modules are mid-build.

SPRINT-2 SESSION-3 WIRING (C1/C5/D2/D3, all additive with stub fallbacks):
(a) an optional ``aoi`` BBox bounds every feed fetch (the watch daemon's
area of interest; None = legacy unbounded call); (b) the per-incident
fire-centroid WeatherForecast collected during assessment is forwarded to
dispatch.plan(forecasts=...) so gel windows under cfg.gel_env_model_enabled
see real fire weather (Council-1 dispatch F5 — stubs without the kwarg
degrade); (c) an ``incident_meta`` snapshot {incident_id: {name,
containment_pct, acreage, updated_at_s}} of this cycle's active incidents
rides evaluate(..., incident_meta=...) into the alert engine for message
naming (D3) and the SAFETY SEMANTICS 8 containment WATCH-ceiling (D2);
(d) ``incumbent``/``restore_incumbent(plan)`` expose the warm-start plan for
the persist seam so dispatch commitments survive a daemon restart.

W1 SEEDED INCIDENTS (docs/LIVE_WORLD_PLAN.md ticket 1.2; contracts SAFETY
SEMANTICS 12): the monitor owns a wgresponse.seeded.SeededIncidentStore
(``seeded`` / create_seed / add_seed / retire_seed / list_seeds /
restore_seeds — the watch daemon's /api/seed writes it from HTTP threads,
the store is locked). Every cycle the ACTIVE seeds are materialized
(SeededIgnition.to_incident: a point incident stamped fresh at now_s) and
unioned into the incident set AFTER feed fusion (they never fuse) and BEFORE
the staleness gate and the client-range filter — a seeded fire with no
protected client in range is listed but builds no domain, exactly the live
law. In range it runs the unchanged _assess_incident: seeds = the point at
now_s (a feed point-incident's law), weather through _incident_forecast —
the live provider, or the seed's per-incident override
(seeded.seeded_forecast; the mode lands in the cycle notes). A retired seed
simply stops appearing: its pairs take SAFETY SEMANTICS 6's absence path
like a vanished feed incident. After the alerts a seed whose forecast
horizon (t_ignite_s + cfg.horizon_h) has passed with no pair in
NOTICE/WATCH/EXTREME (and a healthy assessment) is auto-retired
(OperatorEvent kind 'seed_retired'). No seeds = byte-identical cycles.

Units: times unix seconds, distances meters, config durations hours/minutes
per contracts.
"""
from __future__ import annotations

import dataclasses
import logging
import math
import time
from typing import Optional

from wgresponse import fuels, geo, spread
from wgresponse import seeded as seeded_mod
from wgresponse.contracts import (
    AlertState,
    BBox,
    CycleResult,
    DispatchJob,
    FireIncident,
    IncidentSource,
    LatLon,
    OperatorEvent,
    ResponseConfig,
    ThreatAssessment,
)

log = logging.getLogger(__name__)

# Plausible sustained max head ROS for domain sizing, m/s (~6 km/h — a
# Tubbs-class sustained run, digest §5.2). Deliberately generous: it only
# widens the client-search radius, never the physics.
_PLAUSIBLE_MAX_ROS_MPS = 1.7
_MAX_THREAT_RADIUS_M = 60_000.0     # hard cap on the client-search radius
_DOMAIN_PAD_M = 2_000.0             # spread-domain padding beyond ignitions+clients

_DISPATCH_STATES = frozenset({
    AlertState.NOTICE_72H, AlertState.WATCH, AlertState.EXTREME_24H,
})

# Perimeter rings are subsampled to this many vertices before being handed to
# spread (contracts: "rings subsampled <= 200 verts upstream") — bounds the
# per-row scanline cost of interior rasterization on pathological rings.
_MAX_RING_VERTS = 200


def _subsample_ring(ring: list, max_verts: int = _MAX_RING_VERTS) -> list:
    """Every ceil(n/max_verts)-th vertex of `ring`, keeping <= max_verts.

    Keeps the first vertex; order preserved (the polygon stays closed
    implicitly — spread's rasterizer closes last->first). Rings at or under
    the cap pass through unchanged."""
    n = len(ring)
    if n <= max_verts:
        return list(ring)
    step = -(-n // max_verts)          # ceil division
    return list(ring[::step])


def _extreme_regime_now(tile, wxf, now_s: float, cfg) -> bool:
    """EXTREME SPREAD REGIME detector (contracts knob block; measured in
    docs/calibration/hindcast_oos_2026-07-28.md + scratch_review/
    extreme_signature.py): a cured-grass-led burnable domain (GR+GS fraction
    >= cfg.extreme_regime_grass_frac) with any meaningful wind (max effective
    wind over the next cfg.extreme_regime_lookahead_h >= cfg.
    extreme_regime_wind_mps). Deliberately NOT a red-flag-warning clone —
    the measured blow-ups occurred at modest reanalysis wind/RH while
    well-forecast windows were windier and drier; fuel is the separator, the
    wind clause only keeps a dead-calm grass sea from arming the posture.

    Detector wind uses the FIXED hindcast blend 0.5 (the unit the 4.0 m/s
    threshold was measured in), independent of cfg.wind_gust_blend: a spread
    model running conservative must not also weaken regime detection.
    Fail-safe: an empty/unburnable tile or empty forecast window returns
    False (no regime claim without evidence)."""
    burnable = 0
    grass = 0
    for v in tile.fuel.data:
        code = int(v)
        if not fuels.is_burnable(code):
            continue
        burnable += 1
        if fuels.is_grass(code) or fuels.is_grass_shrub(code):
            grass += 1
    if not burnable or grass / burnable < cfg.extreme_regime_grass_frac:
        return False
    horizon_s = now_s + cfg.extreme_regime_lookahead_h * 3600.0
    max_eff = -1.0
    for s in wxf.samples:
        if now_s - 3600.0 <= s.t_s <= horizon_s:
            eff = s.wind_speed_mps + 0.5 * max(
                0.0, s.wind_gust_mps - s.wind_speed_mps)
            if eff > max_eff:
                max_eff = eff
    return max_eff >= cfg.extreme_regime_wind_mps


def regime_spread_cfg(tile, wxf, now_s: float, cfg):
    """(config to spread THIS incident with, regime flag) — the EXTREME
    SPREAD REGIME posture applied to the spread physics (contracts
    extreme_regime_floor_frac; calibration matrix 2026-09-03 verdict). When
    the detector trips and the knob is > 0 the incident spreads with the
    blow-up floor on EVERY floored class (ros_floor_grass_only lifted);
    otherwise the caller's config is returned unchanged (byte-identical).
    Shared by ThreatMonitor._assess_incident and the hindcast harness so the
    live path and the calibration replays see one rule."""
    if not cfg.extreme_regime_enabled:
        return cfg, False
    flag = _extreme_regime_now(tile, wxf, now_s, cfg)
    frac = float(getattr(cfg, "extreme_regime_floor_frac", 0.0) or 0.0)
    if flag and frac > 0.0:
        cfg = dataclasses.replace(cfg, grass_ros_floor_frac=frac,
                                  ros_floor_grass_only=False)
    return cfg, flag


class _ProviderFailure(RuntimeError):
    """A tagged external-provider failure inside one incident assessment.

    Carries which step failed (registry / terrain / weather) so cycle() can
    emit a precise OperatorEvent while keeping the original exception text in
    CycleResult.notes."""

    def __init__(self, step: str, cause: Exception) -> None:
        super().__init__(f"{step} provider failed ({cause})")
        self.step = step
        self.cause = cause


class ThreatMonitor:
    """Orchestrates one feeds->spread->alerts->dispatch cycle per call.

    Args:
        feeds: list of contracts.FireFeed providers (fetch_active()).
        weather_provider: contracts.WeatherProvider (forecast(loc, horizon_h)).
        terrain_provider: contracts.TerrainProvider (tile(bbox, cell_m)).
        registry: contracts.ClientRegistry (protected_clients(bbox)).
        alert_engine: wgresponse.alerts.AlertEngine-shaped object
            (.evaluate(assessments, now_s[, blind_pairs=, feeds_healthy=])
            -> events, .states() -> records, optional .pop_operator_events()).
        cfg: contracts.ResponseConfig.
        safe_zones: list[contracts.SafeZone] forwarded to the dispatcher.
        crew_tracker: optional wgresponse.crews.CrewTracker-shaped object
            (.crews() -> list[Crew]).
        dispatcher: optional object exposing the wgresponse.dispatch seam
            plan(crews, jobs, ens, safe_zones, cfg, now_s, incumbent=None
            [, forecasts=None]); pass the wgresponse.dispatch module itself
            or any stub (a stub without the Sprint-2 ``forecasts`` kwarg is
            still supported — the call degrades). None disables planning.
        aoi: optional contracts.BBox — the watch daemon's area of interest
            (Sprint 2 C1, contracts cli seam). When given it is threaded
            into EVERY feed fetch (fetch_active(aoi)) so live adapters query
            bounded windows instead of the whole country; None keeps the
            legacy unbounded fetch byte-identically. The AOI never filters
            monitor-side — a fire just outside the box that a feed still
            returns is assessed normally (dropping it could silence a
            threat to an in-AOI client).
    """

    def __init__(self, feeds: list, weather_provider, terrain_provider,
                 registry, alert_engine, cfg: ResponseConfig, safe_zones: list,
                 crew_tracker=None, dispatcher=None, aoi=None) -> None:
        self._feeds = list(feeds)
        self._weather = weather_provider
        self._terrain = terrain_provider
        self._registry = registry
        self._alert_engine = alert_engine
        self._cfg = cfg
        self._safe_zones = list(safe_zones)
        self._crew_tracker = crew_tracker
        self._dispatcher = dispatcher
        self._aoi = aoi
        self._incumbent = None          # last DispatchPlan, warm-start seed
        self._extreme_prev: dict = {}   # incident_id -> bool (regime toggle events)
        # W1 SEEDED INCIDENTS (contracts SAFETY SEMANTICS 12): user-lit fires,
        # consulted every cycle; empty = byte-identical cycles.
        self._seeded = seeded_mod.SeededIncidentStore()

    # ------------------------------------------------- W1 seeded incidents

    @property
    def seeded(self) -> "seeded_mod.SeededIncidentStore":
        """The seeded-incident store (thread-safe; the daemon's API writes it)."""
        return self._seeded

    def create_seed(self, lat: float, lon: float, t_ignite_s: float,
                    **kw) -> "object":
        """Light a fire at (lat, lon) at t_ignite_s (unix s): mints the
        'seeded:<uuid>' id and stores the seed (kw: intensity / weather /
        wind_dir_deg / created_by / note — SeededIncidentStore.create). It
        joins the incident set from the next cycle. Raises ValueError (bad
        ignition) / seeded.SeedCapacityError (too many active)."""
        return self._seeded.create(lat, lon, t_ignite_s, **kw)

    def add_seed(self, ign) -> "object":
        """Store a pre-built contracts.SeededIgnition (tests, tooling)."""
        return self._seeded.add(ign)

    def retire_seed(self, incident_id: str, now_s: float,
                    reason: str = seeded_mod.RETIRED_BY_OPERATOR,
                    by: str = "") -> "object":
        """Retire a seed at now_s (SAFETY SEMANTICS 12e): it leaves the
        incident set from the next cycle and its pairs follow the absence
        law. KeyError unknown; seeded.SeedAlreadyRetired if already retired."""
        return self._seeded.retire(incident_id, now_s, reason=reason, by=by)

    def list_seeds(self) -> list:
        """Every seed entry (active + retired), creation order."""
        return self._seeded.entries()

    def restore_seeds(self, rows) -> int:
        """Re-seed the store from persisted rows (persist seam); returns the
        number restored (malformed rows are skipped loudly, never raised)."""
        return self._seeded.restore(rows)

    @property
    def incumbent(self):
        """Latest adopted DispatchPlan (warm-start seed) or None.

        Exposed for the persist seam: the watch daemon saves it every cycle
        and re-seeds a restarted process via restore_incumbent() so plan
        commitments survive restart."""
        return self._incumbent

    def restore_incumbent(self, plan) -> None:
        """Seed the warm-start incumbent from persisted state (persist seam).

        Passing None is a no-op-equivalent cold start. The next cycle's
        dispatcher call receives this plan as ``incumbent`` exactly as if it
        had been computed by the previous in-process cycle."""
        self._incumbent = plan

    # ------------------------------------------------------------------ API

    def cycle(self, now_s: float) -> CycleResult:
        """Run one full monitoring cycle at unix time now_s (seconds).

        Returns a CycleResult: fused ACTIVE incidents plus the active SEEDED
        incidents (SAFETY SEMANTICS 12; after the fused ones, creation order)
        (after the SAFETY
        SEMANTICS 9 staleness gate — incidents older than
        cfg.incident_stale_h are excluded from the cycle with a note),
        per-client ThreatAssessments (etas in absolute unix seconds),
        AlertEvents emitted this cycle, the DispatchPlan (None unless a
        dispatcher is configured AND at least one pair is in an active alert
        state), the per-incident EnsembleArrival map, degradation notes,
        operator-facing OperatorEvents, and the degraded flag (True when any
        provider was blind/failed this cycle — SAFETY SEMANTICS 6).
        """
        notes: list = []
        operator_events: list = []
        timings: dict = {"incidents": {}}      # cadence telemetry (review item 6)
        _t = time.monotonic()
        incidents, feeds_healthy = self._fetch_and_fuse(now_s, notes,
                                                        operator_events)
        timings["feeds_s"] = time.monotonic() - _t
        _t = time.monotonic()
        active = [inc for inc in incidents if inc.active]
        # W1 SEEDED INCIDENTS (SAFETY SEMANTICS 12a/c/d): unioned AFTER
        # fusion (never merged with a feed record) and BEFORE the staleness
        # gate + the client-range filter (the live law); [] = unchanged.
        seeded_now = self._seeded_incidents(now_s, active, notes)
        if seeded_now:
            active = active + seeded_now
        active = self._gate_stale_incidents(active, now_s, notes)

        assessments: list = []
        ensembles: dict = {}
        clients_seen: dict = {}
        etas: dict = {}
        forecasts: dict = {}            # incident_id -> WeatherForecast (C5)
        failed_incidents: set = set()
        roster_seen: dict = {}          # incident_id -> {client_id} registry returned
        extreme_flags: dict = {}        # incident_id -> bool (extreme spread regime)
        eroded_map: dict = {}           # incident_id -> tiered eroded raster
        for inc in active:
            try:
                self._assess_incident(inc, now_s, assessments, ensembles,
                                      clients_seen, etas, notes, roster_seen,
                                      forecasts, extreme_flags, eroded_map,
                                      timings=timings)
            except _ProviderFailure as pf:
                failed_incidents.add(inc.incident_id)
                notes.append(
                    f"incident {inc.incident_id}: assessment skipped ({pf})")
                operator_events.append(OperatorEvent(
                    t_s=now_s, kind="provider_error",
                    message=(f"{pf.step} provider failed for incident "
                             f"{inc.incident_id}: {pf.cause}"),
                    incident_id=inc.incident_id, detail={"step": pf.step}))
            except Exception as exc:  # degrade, never raise out of cycle()
                failed_incidents.add(inc.incident_id)
                notes.append(
                    f"incident {inc.incident_id}: assessment skipped ({exc})")
                operator_events.append(OperatorEvent(
                    t_s=now_s, kind="provider_error",
                    message=(f"assessment failed for incident "
                             f"{inc.incident_id}: {exc}"),
                    incident_id=inc.incident_id, detail={"step": "assess"}))

        # BLIND pairs (SAFETY SEMANTICS 6): tracked pairs whose incident's
        # assessment failed this cycle, PLUS tracked pairs whose incident is
        # still ACTIVE but whose client the registry did not return for that
        # incident (silent partial roster — neither trustworthy-clear clause
        # applies; see module docstring). Pairs absent because every feed is
        # healthy and the incident is gone are NOT blind — they may clear; so
        # may pairs whose client the registry DID return but who sit beyond
        # the plausible threat radius (assessed-clear absence).
        assessed_pairs = {(a.client_id, a.incident_id) for a in assessments}
        active_ids = {inc.incident_id for inc in active}
        blind_pairs: set = set()
        roster_missing: dict = {}       # incident_id -> [client_id] silently absent
        try:
            tracked = list(self._alert_engine.states())
        except Exception as exc:
            tracked = []
            notes.append(f"blind-pair derivation failed ({exc})")
        for key in tracked:
            if key in assessed_pairs:
                continue
            cid, iid = key
            if iid in failed_incidents:
                blind_pairs.add(key)
            elif iid in active_ids and cid not in roster_seen.get(iid, ()):
                blind_pairs.add(key)
                roster_missing.setdefault(iid, []).append(cid)
        for iid in sorted(roster_missing):
            cids = sorted(roster_missing[iid])
            notes.append(
                f"incident {iid}: tracked client(s) {', '.join(cids)} missing "
                f"from the registry roster while the incident is active — "
                f"pair(s) treated as BLIND (absence is not clearance)")
            operator_events.append(OperatorEvent(
                t_s=now_s, kind="provider_error",
                message=(f"registry roster no longer contains tracked "
                         f"client(s) {', '.join(cids)} for active incident "
                         f"{iid}; suspected silent partial registry response "
                         f"— alert state(s) FROZEN, never cleared"),
                incident_id=iid,
                detail={"step": "registry_roster", "clients": cids}))

        # Incident snapshot for the alert engine (Sprint 2 D2/D3, additive
        # kwarg): display names for message wording + containment/acreage for
        # the SAFETY SEMANTICS 8 WATCH ceiling. Built from THIS cycle's
        # post-staleness-gate active set only — a gated/absent incident sends
        # no meta, so the engine's no-growth clock only advances on real
        # observations.
        incident_meta = {
            inc.incident_id: {
                "name": inc.name,
                "containment_pct": inc.containment_pct,
                "acreage": inc.acreage,
                "updated_at_s": inc.updated_at_s,
            }
            for inc in active
        }
        if self._cfg.extreme_regime_enabled:
            # EXTREME SPREAD REGIME: carried to the alert engine as additive
            # meta (flagged incidents evaluate ladder stages on p10); one
            # operator event per TOGGLE so shadow ops can count regime
            # frequency and false-positive load. Incidents that failed
            # assessment this cycle keep their previous posture (no evidence
            # either way — a blind cycle must not silently drop the regime).
            for iid, meta in incident_meta.items():
                flag = extreme_flags.get(iid, self._extreme_prev.get(iid, False))
                meta["extreme_regime"] = flag
                prev = self._extreme_prev.get(iid, False)
                if flag != prev:
                    verb = "ENTERED" if flag else "exited"
                    name = incident_meta[iid].get("name") or iid
                    notes.append(f"incident {name}: {verb} extreme spread "
                                 f"regime (cured-grass domain + wind)")
                    operator_events.append(OperatorEvent(
                        t_s=now_s, kind="extreme_regime",
                        message=(f"incident {name} {verb} EXTREME SPREAD "
                                 f"REGIME: cured-grass fuelscape with wind — "
                                 f"forecasts may under-run observed spread; "
                                 f"alert ladder evaluating on the pessimistic "
                                 f"(p10) arrival"),
                        incident_id=iid, detail={"extreme_regime": flag}))
                self._extreme_prev[iid] = flag
            # forget incidents that left the active set (no stale toggles)
            gone = set(self._extreme_prev) - set(incident_meta)
            for iid in gone:
                del self._extreme_prev[iid]
        events: list = []
        timings["assess_s"] = time.monotonic() - _t
        _t = time.monotonic()
        try:
            events = self._evaluate_alerts(assessments, now_s, blind_pairs,
                                           feeds_healthy, incident_meta)
        except Exception as exc:
            notes.append(f"alert evaluation failed ({exc})")
            operator_events.append(OperatorEvent(
                t_s=now_s, kind="provider_error",
                message=f"alert evaluation failed: {exc}",
                detail={"step": "alerts"}))
        operator_events.extend(self._drain_alert_operator_events(notes))
        if self._seeded.has_active():
            # SEMANTICS 12e: a seed past its forecast horizon with nothing
            # threatened retires (after the ladder has seen this cycle).
            self._retire_spent_seeds(now_s, failed_incidents, notes,
                                     operator_events)
        timings["alerts_s"] = time.monotonic() - _t
        _t = time.monotonic()

        plan = self._maybe_dispatch(now_s, ensembles, clients_seen, etas,
                                    notes, forecasts,
                                    eroded_map=(eroded_map or None))
        timings["dispatch_s"] = time.monotonic() - _t

        degraded = (not feeds_healthy) or bool(failed_incidents) or any(
            ev.kind in ("degraded", "feed_blind", "provider_error")
            for ev in operator_events)
        return CycleResult(t_s=now_s, incidents=active, assessments=assessments,
                           events=events, plan=plan, ensembles=ensembles,
                           notes=notes, operator_events=operator_events,
                           degraded=degraded, timings=timings)

    # ------------------------------------------------------------- internals

    def _seeded_incidents(self, now_s: float, feed_active: list,
                          notes: list) -> list:
        """This cycle's seeded FireIncidents (SAFETY SEMANTICS 12), creation
        order; [] with no active seed (and then no note: byte-identical).

        Operator retirements since the last cycle are noted once here — from
        this cycle the retired seed is absent and its pairs take the absence
        law. Each active seed materializes through SeededIgnition.
        to_incident(now_s) (a fresh point incident) with its weather label in
        meta["seed"]; an id already used by a feed incident (impossible
        across namespaces — defense in depth) is skipped with a note, never
        merged."""
        for e in self._seeded.take_announcements():
            notes.append(
                f"seeded incident {e.ignition.incident_id} retired by "
                f"{e.retired_by or 'the operator'} at {e.retired_at_s:.0f}: "
                f"absent from the incident set from this cycle — its pairs "
                f"follow the absence law like a vanished feed incident "
                f"(clear streak toward CLEARED while every feed is healthy, "
                f"frozen while any is blind; SAFETY SEMANTICS 6/12e)")
        igns = self._seeded.active()
        if not igns:
            return []
        taken = {inc.incident_id for inc in feed_active}
        out = []
        for ign in igns:
            if ign.incident_id in taken:
                notes.append(
                    f"seeded incident {ign.incident_id}: id collides with a "
                    f"feed incident — skipped this cycle (a seeded fire never "
                    f"merges with a feed record)")
                continue
            inc = ign.to_incident(now_s)
            inc.meta["seed"]["weather_label"] = seeded_mod.weather_label(ign)
            out.append(inc)
        return out

    def _incident_forecast(self, inc: FireIncident, loc: LatLon,
                           now_s: float, notes: list):
        """The fire-weather forecast for one incident at `loc` (the fire
        centroid). Feed incidents: the live provider, exactly the legacy
        call. A SEEDED incident: its per-incident override through the same
        seam (seeded.seeded_forecast — the live provider untouched when the
        seed overrides nothing), for THAT incident only; the mode that
        applied is noted (SAFETY SEMANTICS 12f)."""
        if inc.source is IncidentSource.SEEDED:
            ign = self._seeded.ignition(inc.incident_id)
            if ign is not None:
                wxf = seeded_mod.seeded_forecast(ign, self._weather, loc,
                                                 self._cfg.horizon_h, now_s)
                notes.append(f"seeded incident {inc.incident_id}: weather "
                             f"{seeded_mod.weather_label(ign)}")
                return wxf
        return self._weather.forecast(loc, self._cfg.horizon_h)

    @staticmethod
    def _note_seed_fuel(inc: FireIncident, tile, notes: list) -> None:
        """A seeded ignition on NON-BURNABLE fuel spreads nothing (the
        simulator rejects such a click; the watcher learns the fuel only with
        the domain tile) — say so loudly instead of a silent dead fire."""
        rc = geo.latlon_to_rc(tile.spec, inc.origin)
        if rc is None:
            return
        code = int(round(tile.fuel.get(*rc)))
        if not fuels.is_burnable(code):
            notes.append(
                f"seeded incident {inc.incident_id}: the ignition cell is "
                f"NON-BURNABLE (FBFM40 {code}) — the surface-spread model "
                f"cannot ignite or spread from urban / agriculture / water / "
                f"barren fuel; retire it and seed on burnable fuel")

    def _retire_spent_seeds(self, now_s: float, failed_incidents: set,
                            notes: list, operator_events: list) -> None:
        """SAFETY SEMANTICS 12e auto-retire: an active seed whose forecast
        horizon (t_ignite_s + cfg.horizon_h) has passed, whose assessment
        did not fail this cycle, and none of whose pairs is in NOTICE_72H /
        WATCH / EXTREME_24H retires (reason 'horizon', by 'watcher') — note +
        OperatorEvent('seed_retired'). A seed that still threatens a client
        keeps burning until the operator retires it (a point incident spreads
        from its point every cycle, so it would threaten indefinitely).
        Alert states unavailable = no evidence = no retirement."""
        horizon_s = self._cfg.horizon_h * 3600.0
        due = [ign for ign in self._seeded.active()
               if now_s - ign.t_ignite_s >= horizon_s
               and ign.incident_id not in failed_incidents]
        if not due:
            return
        try:
            threatened = {key[1] for key, rec in self._alert_engine.states().items()
                          if getattr(rec, "state", None) in _DISPATCH_STATES}
        except Exception as exc:
            notes.append(f"seeded auto-retire skipped: alert states "
                         f"unavailable ({exc})")
            return
        for ign in due:
            if ign.incident_id in threatened:
                continue
            try:
                self._seeded.retire(ign.incident_id, now_s,
                                    reason=seeded_mod.RETIRED_AT_HORIZON,
                                    by="watcher", announce=False)
            except (KeyError, seeded_mod.SeedConflict):
                continue            # retired concurrently by the operator
            msg = (f"seeded incident {ign.incident_id} auto-RETIRED: its "
                   f"{self._cfg.horizon_h:g} h forecast horizon has passed "
                   f"(lit {(now_s - ign.t_ignite_s) / 3600.0:.1f} h ago) with "
                   f"no client pair in NOTICE/WATCH/EXTREME — absent from the "
                   f"next cycle (SAFETY SEMANTICS 12e)")
            notes.append(msg)
            operator_events.append(OperatorEvent(
                t_s=now_s, kind="seed_retired", message=msg,
                incident_id=ign.incident_id,
                detail={"reason": seeded_mod.RETIRED_AT_HORIZON}))

    def _evaluate_alerts(self, assessments: list, now_s: float,
                         blind_pairs: set, feeds_healthy: bool,
                         incident_meta: "Optional[dict]" = None) -> list:
        """Call the alert engine with the assessed/blind distinction + meta.

        Prefers the full Sprint-2 seam evaluate(assessments, now_s,
        blind_pairs=..., feeds_healthy=..., incident_meta=...); an engine
        without the D2/D3 ``incident_meta`` kwarg degrades to the
        blind-pairs call, and one predating Sprint 2 entirely (hand stub
        with the 2-arg signature) degrades to the legacy call."""
        def _sig_error(exc: TypeError) -> bool:
            msg = str(exc)
            return ("incident_meta" in msg or "blind_pairs" in msg
                    or "feeds_healthy" in msg or "positional" in msg
                    or "keyword" in msg)

        try:
            return list(self._alert_engine.evaluate(
                assessments, now_s, blind_pairs=blind_pairs,
                feeds_healthy=feeds_healthy, incident_meta=incident_meta))
        except TypeError as exc:
            if not _sig_error(exc):
                raise
        try:
            return list(self._alert_engine.evaluate(
                assessments, now_s, blind_pairs=blind_pairs,
                feeds_healthy=feeds_healthy))
        except TypeError as exc:
            if not _sig_error(exc):
                raise
            return list(self._alert_engine.evaluate(assessments, now_s))

    def _drain_alert_operator_events(self, notes: list) -> list:
        """Pull queued OperatorEvents off the alert engine (optional seam)."""
        pop = getattr(self._alert_engine, "pop_operator_events", None)
        if not callable(pop):
            return []
        try:
            return list(pop())
        except Exception as exc:
            notes.append(f"operator-event drain failed ({exc})")
            return []

    def _fetch_and_fuse(self, now_s: float, notes: list,
                        operator_events: list) -> "tuple[list, bool]":
        """Fetch every feed (each guarded) and fuse the batches.

        Returns (incidents, feeds_healthy). feeds_healthy is True only when
        EVERY feed fetch succeeded end to end: a fetch that raised, or one
        that returned with last_fetch_ok=False (contracts convention — the
        adapter went blind and returned [] by robustness design), flips it to
        False, appends a note, and emits an OperatorEvent('feed_blind'). An
        unhealthy feed's (possibly partial) batch is still fused — real data
        is never discarded — but its EMPTINESS must not read as clearance
        (SAFETY SEMANTICS 6).

        Fusion uses wgresponse.feeds.fuse_incidents (lazy import — the sibling
        may be mid-build); on any fusion failure it degrades to keeping the
        freshest incident per incident_id and notes the degradation.
        """
        batches = []
        healthy = True
        for i, feed in enumerate(self._feeds):
            fname = type(feed).__name__
            try:
                # AOI (Sprint 2 C1): the watch daemon's bbox bounds every
                # live fetch; None keeps the legacy no-arg call so hand
                # stubs without a bbox parameter stay compatible.
                if self._aoi is not None:
                    batch = list(feed.fetch_active(self._aoi))
                else:
                    batch = list(feed.fetch_active())
            except Exception as exc:
                healthy = False
                notes.append(f"feed[{i}] {fname}: fetch failed ({exc})")
                operator_events.append(OperatorEvent(
                    t_s=now_s, kind="feed_blind",
                    message=f"feed[{i}] {fname} fetch raised: {exc}",
                    detail={"feed": fname, "index": i}))
                continue
            if not getattr(feed, "last_fetch_ok", True):
                healthy = False
                notes.append(f"feed[{i}] {fname}: fetch unhealthy "
                             f"(last_fetch_ok=False); batch treated as blind, "
                             f"not clear")
                operator_events.append(OperatorEvent(
                    t_s=now_s, kind="feed_blind",
                    message=(f"feed[{i}] {fname} reported an unhealthy fetch "
                             f"(last_fetch_ok=False)"),
                    detail={"feed": fname, "index": i}))
            batches.append(batch)
        if not batches:
            return [], healthy
        try:
            from wgresponse.feeds import fuse_incidents
            return list(fuse_incidents(batches)), healthy
        except Exception as exc:
            notes.append(f"incident fusion degraded to id-dedupe ({exc})")
            merged: dict = {}
            for batch in batches:
                for inc in batch:
                    cur = merged.get(inc.incident_id)
                    if cur is None or inc.updated_at_s > cur.updated_at_s:
                        merged[inc.incident_id] = inc
            return list(merged.values()), healthy

    def _gate_stale_incidents(self, active: list, now_s: float,
                              notes: list) -> list:
        """STALENESS GATE (SAFETY SEMANTICS 9, ticket C3a): drop incidents
        whose updated_at_s is MORE than cfg.incident_stale_h old from this
        cycle's active set — they are excluded from alerting AND dispatch
        (never assessed, so they can neither escalate a ladder nor spawn
        jobs, and no terrain/spread compute is spent on them). Logged + one
        CycleResult note listing the excluded ids with their ages.

        This is a monitor-side gate, NOT a feed-level drop: the adapters keep
        returning the records (live FIRIS retains months-old perimeters
        flagged active=True — median 49 days in the ops review). Incidents at
        EXACTLY the threshold age are kept (strictly-older excludes). Tracked
        pairs of a gated incident follow the SAFETY SEMANTICS 6 absence rules
        — see the module docstring. Exclusion is data hygiene, not provider
        failure: it does not mark the cycle degraded."""
        max_age_s = self._cfg.incident_stale_h * 3600.0
        fresh = [inc for inc in active if now_s - inc.updated_at_s <= max_age_s]
        stale = [inc for inc in active if now_s - inc.updated_at_s > max_age_s]
        if stale:
            listed = ", ".join(
                f"{inc.incident_id} (age {(now_s - inc.updated_at_s) / 3600.0:.0f} h)"
                for inc in sorted(stale, key=lambda i: i.incident_id))
            note = (f"excluded {len(stale)} stale incident(s) from "
                    f"alerting/dispatch (updated_at older than "
                    f"{self._cfg.incident_stale_h:.0f} h — SAFETY SEMANTICS 9): "
                    f"{listed}")
            notes.append(note)
            log.warning("%s", note)
        return fresh

    def _threat_radius_m(self) -> float:
        """Client-search radius, meters: how far a fire could plausibly run
        within cfg.horizon_h at ~1.7 m/s sustained, capped at 60 km."""
        return min(self._cfg.horizon_h * 3600.0 * _PLAUSIBLE_MAX_ROS_MPS,
                   _MAX_THREAT_RADIUS_M)

    def _pick_cell_m(self, bbox: BBox) -> float:
        """Grid cell size (meters) for a spread domain: cfg.grid_cell_m,
        coarsened just enough that the domain fits cfg.grid_max_cells."""
        cfg = self._cfg
        ctr = bbox.center()
        w_m = max((bbox.east - bbox.west) * geo.m_per_deg_lon(ctr.lat),
                  cfg.grid_cell_m)
        h_m = max((bbox.north - bbox.south) * geo.M_PER_DEG_LAT,
                  cfg.grid_cell_m)
        needed = math.sqrt((w_m * h_m) / float(max(1, cfg.grid_max_cells)))
        return max(cfg.grid_cell_m, needed)

    def _assess_incident(self, inc: FireIncident, now_s: float,
                         assessments: list, ensembles: dict,
                         clients_seen: dict, etas: dict, notes: list,
                         roster_seen: Optional[dict] = None,
                         forecasts: Optional[dict] = None,
                         extreme_flags: Optional[dict] = None,
                         eroded_map: Optional[dict] = None,
                         timings: Optional[dict] = None) -> None:
        """Spread one incident and append per-client ThreatAssessments.

        Domain = bbox around (ignition geometry + protected clients within the
        threat radius) padded _DOMAIN_PAD_M; incidents with no client in range
        are skipped (noted) — no spread compute is spent on them. Weather is
        fetched at the FIRE CENTROID (mean of ignition points), never the
        domain-bbox center: weather at the fire drives spread physics, and a
        client-shifted bbox center can sit tens of km from the flame front in
        a different weather regime (Council-1 model F7). External-provider
        failures raise _ProviderFailure tagged with the failing step
        (registry/terrain/weather) so cycle() can alarm precisely.

        ``roster_seen`` (when given) records {incident_id: {client_id}} for
        every client the registry returned for this incident's search bbox —
        recorded BEFORE any radius filtering or later provider step, so
        cycle() can distinguish "registry vouched for this client, who is out
        of plausible reach" (trustworthy-clear) from "client silently vanished
        from the roster" (BLIND — see module docstring).

        PERIMETER SEEDING (Sprint 2 ticket A5, Council-1 model F4): incidents
        WITH a perimeter no longer seed spread from ring VERTICES at now —
        the rings (subsampled to <= 200 vertices each, per contracts) are
        passed to spread with perimeter_at_s = the PERIMETER's own provenance
        time (meta["perimeter_at_s"] carried by the feeds layer, Session-3
        verifier F3; falls back to the record's updated_at_s when absent)
        CLAMPED to <= now_s (SAFETY SEMANTICS 9 clock clamp, ticket C3e:
        skewed feed clocks must not stamp interiors in the future), so the
        polygon interior is already burned at the perimeter's own timestamp
        and the front advances from the full burned edge (a stale perimeter
        is correctly ahead of a seeded-at-now front; clients inside get
        arrival = the perimeter time => hours_out 0 => the ladder's IMPACTED
        path). FIRMS hotspots are merged as t0 seeds even when a perimeter
        exists (ignition_points()'s perimeter-first rule shadowed them —
        contracts.FireIncident is unchanged; the seed set is built here).
        Geometry (search bbox, threat radius, domain, weather centroid) uses
        ring vertices + hotspots. Incidents WITHOUT a perimeter build seeds
        exactly as ignition_points() does (hotspots, else origin) and pass no
        perimeter — byte-identical to the legacy path (pinned in
        tests/test_threat.py).
        """
        ign = inc.ignition_points()
        if inc.perimeter:
            perimeter = [r for r in
                         (_subsample_ring(ring) for ring in inc.perimeter if ring)
                         if r]
            # PERIMETER PROVENANCE (Session-3 verifier F3): a fused record
            # carries the FRESHEST member's updated_at_s, but its perimeter
            # may come from an OLDER member — stamping that polygon at the
            # fused-fresh time loses (fresh - perimeter age) hours of front
            # advance (measured 47.8 h LATE on a 48 h-old ring + 15 min
            # point). feeds carries the polygon's own currency time as
            # meta["perimeter_at_s"]; fall back to updated_at_s when absent
            # (single-timestamp records — for them it IS the perimeter time).
            # Non-numeric/bool/non-positive stamps fall back too: a bogus
            # epoch-0 provenance must never burn the whole domain in 1970.
            raw_pat = (inc.meta or {}).get("perimeter_at_s")
            if (isinstance(raw_pat, (int, float))
                    and not isinstance(raw_pat, bool)
                    and math.isfinite(raw_pat) and raw_pat > 0):
                provenance_s = float(raw_pat)
            else:
                provenance_s = float(inc.updated_at_s)
            # CLOCK CLAMP (SAFETY SEMANTICS 9, ticket C3e): a feed clock
            # running ahead of ours must not stamp the burned interior at a
            # FUTURE time — inside clients would read hours_out > 0 while
            # standing in the fire. Clamp to now_s before seeding.
            perimeter_at_s = min(provenance_s, float(now_s))
            hotspots = list(inc.hotspots or [])
            seeds = hotspots                     # exterior seeds at now (union)
            geom_pts = list(ign) + hotspots      # footprint + fresh detections
        else:
            perimeter = None
            perimeter_at_s = None
            seeds = list(ign)
            geom_pts = list(ign)
        radius_m = self._threat_radius_m()
        search_bbox = geo.bbox_around(geom_pts, radius_m)
        try:
            candidates = list(self._registry.protected_clients(search_bbox))
        except Exception as exc:
            raise _ProviderFailure("registry", exc)
        if roster_seen is not None:
            roster_seen[inc.incident_id] = {c.client_id for c in candidates}
        near = [c for c in candidates
                if min(geo.haversine_m(c.loc, p) for p in geom_pts) <= radius_m]
        if not near:
            note = (f"incident {inc.incident_id}: no protected clients "
                    f"within {radius_m / 1000.0:.0f} km; spread skipped")
            if inc.source is IncidentSource.SEEDED:
                # SEMANTICS 12d: the same law as a live fire, said out loud
                note += (" — a seeded fire follows the live law: listed, but "
                         "no domain without a protected client in range")
            notes.append(note)
            return

        domain = geo.bbox_around(geom_pts + [c.loc for c in near],
                                 _DOMAIN_PAD_M)
        cell_m = self._pick_cell_m(domain)
        _tm = {}
        _t = time.monotonic()
        try:
            tile = self._terrain.tile(domain, cell_m)
        except Exception as exc:
            raise _ProviderFailure("terrain", exc)
        _tm["terrain_s"] = time.monotonic() - _t
        if inc.source is IncidentSource.SEEDED:
            self._note_seed_fuel(inc, tile, notes)
        _t = time.monotonic()
        fire_centroid = LatLon(sum(p.lat for p in ign) / len(ign),
                               sum(p.lon for p in ign) / len(ign))
        try:
            # SEMANTICS 12f: a seeded incident's per-incident override; every
            # feed incident gets the unchanged live-provider call.
            wxf = self._incident_forecast(inc, fire_centroid, now_s, notes)
        except Exception as exc:
            raise _ProviderFailure("weather", exc)
        _tm["weather_s"] = time.monotonic() - _t
        _t = time.monotonic()
        if forecasts is not None:
            # Sprint 2 C5 (Council-1 dispatch F5): the SAME fire-centroid
            # forecast that drives spread is threaded to dispatch.plan(
            # forecasts=...) so gel windows under cfg.gel_env_model_enabled
            # reflect the actual fire weather, not the 25 C reference.
            forecasts[inc.incident_id] = wxf

        spread_cfg = self._cfg
        if self._cfg.extreme_regime_enabled:
            # EXTREME SPREAD REGIME (contracts knob block): computed per
            # incident from the SAME tile + fire-centroid forecast that drive
            # spread, so the posture and the forecast see one world. With
            # extreme_regime_floor_frac > 0 the posture also carries the
            # spread physics for THIS incident (regime_spread_cfg).
            spread_cfg, flag = regime_spread_cfg(tile, wxf, now_s, self._cfg)
            if extreme_flags is not None:
                extreme_flags[inc.incident_id] = flag

        ens = spread.ensemble_arrival(tile, seeds, wxf, now_s, spread_cfg,
                                      incident_id=inc.incident_id,
                                      perimeter=perimeter,
                                      perimeter_at_s=perimeter_at_s)
        _tm["spread_s"] = time.monotonic() - _t
        _tm["cells"] = tile.spec.nrows * tile.spec.ncols
        _tm["clients"] = len(near)
        if timings is not None:
            timings.setdefault("incidents", {})[inc.incident_id] = _tm
        if (eroded_map is not None
                and getattr(self._cfg, "corridor_erosion_tiered", False)):
            # FUEL-TIERED EROSION: built here because this is where the fuel
            # tile lives; dispatch consumes it via the eroded_rasters seam.
            eroded_map[inc.incident_id] = spread.tiered_eroded_p10(
                ens.p10, tile.fuel, self._cfg)
        ens.incident_id = inc.incident_id
        ensembles[inc.incident_id] = ens
        for client in near:
            eta = spread.eta_at(ens, client.loc)
            assessments.append(ThreatAssessment(
                client_id=client.client_id, incident_id=inc.incident_id,
                eta=eta, computed_at_s=now_s))
            clients_seen[client.client_id] = client
            etas[(client.client_id, inc.incident_id)] = eta

    def _eligible_crews(self, now_s: float, notes: list) -> list:
        """Crews the dispatcher may route at unix time now_s.

        Applies beacon staleness FIRST (crew_tracker.stale(now_s) flips crews
        whose phone GPS has gone dark to UNKNOWN so their stale last-known
        position is not treated as current), then returns only dispatch-eligible
        crews (crew_tracker.eligible(now_s): AVAILABLE/EN_ROUTE with a fresh
        beacon). A crew_tracker that predates these seams (e.g. a hand stub
        exposing only .crews()) degrades gracefully to the full crew list — the
        dispatcher's own filter still rejects UNKNOWN/OFF_DUTY/locationless
        crews. Returns [] and notes the degradation if the tracker raises.
        """
        if self._crew_tracker is None:
            return []
        tracker = self._crew_tracker
        try:
            stale_fn = getattr(tracker, "stale", None)
            if callable(stale_fn):
                stale_ids = list(stale_fn(now_s))
                if stale_ids:
                    notes.append(f"crews with stale GPS excluded from dispatch: "
                                 f"{', '.join(sorted(stale_ids))}")
            eligible_fn = getattr(tracker, "eligible", None)
            if callable(eligible_fn):
                return list(eligible_fn(now_s))
            return list(tracker.crews())
        except Exception as exc:
            notes.append(f"crew tracker unavailable ({exc}); "
                         f"planning with 0 crews")
            return []

    def _maybe_dispatch(self, now_s: float, ensembles: dict,
                        clients_seen: dict, etas: dict,
                        notes: list,
                        forecasts: Optional[dict] = None,
                        eroded_map: Optional[dict] = None) -> Optional[object]:
        """Build DispatchJobs for pairs in an active alert state and call the
        injected dispatcher. Returns the DispatchPlan or None; the returned
        plan becomes the incumbent (warm start) for the next cycle.

        The dispatcher receives EVERY assessed ensemble this cycle — not just
        the ensembles of incidents that produced jobs — so corridor safety
        checks see fires whose client pairs are all IMPACTED/CLEARED/
        not-yet-alerted (Council-1 dispatch F1: dropping a jobless incident's
        raster routed a crew straight through its fire footprint).

        ``forecasts`` (Sprint 2 C5): per-incident fire-centroid
        WeatherForecasts collected during assessment, forwarded as
        plan(forecasts=...) so gel windows under cfg.gel_env_model_enabled
        see real fire weather. A dispatcher stub without the kwarg degrades
        to the legacy call (flag-OFF plans are byte-identical either way,
        pinned in tests/test_dispatch.py)."""
        if self._dispatcher is None:
            return None
        try:
            states = self._alert_engine.states()
        except Exception as exc:
            notes.append(f"dispatch skipped: alert states unavailable ({exc})")
            return None

        jobs: list = []
        try:
            for key in sorted(states):
                rec = states[key]
                if getattr(rec, "state", None) not in _DISPATCH_STATES:
                    continue
                cid, iid = key
                eta = etas.get((cid, iid))
                client = clients_seen.get(cid)
                if eta is None or client is None or iid not in ensembles:
                    notes.append(f"dispatch: pair ({cid}, {iid}) is "
                                 f"{rec.state.value} but was not assessed this "
                                 f"cycle; job skipped")
                    continue
                jobs.append(DispatchJob(client=client, incident_id=iid, eta=eta))
        except Exception as exc:
            notes.append(f"dispatch skipped: alert states unusable ({exc})")
            return None
        if not jobs:
            return None

        crews: list = self._eligible_crews(now_s, notes)

        def _call_plan():
            # ALL assessed ensembles (dispatch F1) — never filter to job ids.
            # eroded_map (FUEL-TIERED EROSION): pre-eroded corridor rasters
            # built where the fuel tile lives (_assess_incident); None when
            # tiering is off. Stub dispatchers degrade kwarg-by-kwarg.
            try:
                return self._dispatcher.plan(crews, jobs, dict(ensembles),
                                             list(self._safe_zones), self._cfg,
                                             now_s, incumbent=self._incumbent,
                                             forecasts=dict(forecasts or {}),
                                             eroded_rasters=eroded_map)
            except TypeError as exc:
                if "eroded_rasters" not in str(exc):
                    if "forecasts" not in str(exc):
                        raise
                    # Legacy dispatcher seam (pre-C5 stub): weatherless.
                    return self._dispatcher.plan(
                        crews, jobs, dict(ensembles),
                        list(self._safe_zones), self._cfg,
                        now_s, incumbent=self._incumbent)
            try:
                return self._dispatcher.plan(crews, jobs, dict(ensembles),
                                             list(self._safe_zones), self._cfg,
                                             now_s, incumbent=self._incumbent,
                                             forecasts=dict(forecasts or {}))
            except TypeError as exc:
                if "forecasts" not in str(exc):
                    raise
                # Legacy dispatcher seam (pre-C5 stub): weatherless windows.
                return self._dispatcher.plan(crews, jobs, dict(ensembles),
                                             list(self._safe_zones), self._cfg,
                                             now_s, incumbent=self._incumbent)

        try:
            plan = _call_plan()
        except Exception as exc:
            notes.append(f"dispatch planning failed ({exc})")
            return None
        self._incumbent = plan
        return plan
