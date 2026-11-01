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

CONTAINED FIRES LEAVE THE MAP (contracts SAFETY SEMANTICS 13a-d; founder
ruling 2026-09-27, "a fire that is contained and no longer spreading is
removed from the map"; cfg.drop_contained_incidents, default OFF). After the
staleness gate, every FEED incident the feed declares contained
(contained_signals: a source at containment_pct >= 100, or a containment /
control / out timestamp in meta) with no source below 100 and no acreage
growth since it was removed leaves the incident set before assessment: no
domain, no ETAs, no dispatch, absent from /api/state incidents and the world
summary; its pairs take the SEMANTICS 6 absence law exactly like a vanished
feed incident's. CycleResult.removed_incidents lists them and ONE note
counts them every cycle. The monitor remembers each removed fire in-process
(first removal time, per-source acreage baseline): a source reporting < 100
brings it back that cycle; so does growth, and then it stays until
cfg.containment_no_growth_h pass without growth; a declaration that merely
goes quiet (the fire left the WFIGS Current layer, a feed went blind) keeps
it removed — only contradiction or growth returns a fire. A restart forgets
the memory (a fire whose declaration went quiet returns until its record
ages out — the pessimistic direction). Seeded incidents are never removed.

CONFIRMED PERIMETERS (SAFETY SEMANTICS 13e-f; cfg.perimeter_confirmed_anchor,
default OFF). The F3 rule above seeds a polygon at its OWN time; with no
ceiling, a days-old polygon re-confirmed by a fresh record of the same size
was spread forward for days (the W6 national run). Under the knob, a polygon
whose record reports the same size (meta reported_acres vs the polygon's own
perimeter_acres, within cfg.perimeter_confirm_tolerance_frac) LATER than the
polygon's time, with no newer polygon in any fused source, is seeded at that
report time (clamped to now). A record reporting more acres beyond the
tolerance — a growing fire the polygon lags — keeps the legacy seeding, as
do a newer polygon elsewhere, a smaller size beyond the tolerance and a
missing size; every decision on a domain incident is one note. The
staleness gate still reads the record's own time.

THE OFF-DOMAIN HORIZON IS PER FIRE (SAFETY SEMANTICS 15; founder rule
2026-09-27; cfg.corridor_offraster_per_fire, default OFF). After the alerts
and before dispatch, every domain incident gets its contracts.
OffDomainHorizon from wgresponse.offmap — its behaviour status from the feed
(known / rapid), the P90 front speed where its raw P10 crosses its domain
edge, the crossing-time margin — with one note each; the table rides
CycleResult.offmap and dispatch.plan(offmap=...), where it replaces the flat
1.7 m/s off-raster bound for that fire only. Off = {} and no kwarg: byte-
identical cycles.

SPREAD READS THE LOCAL WEATHER FORECAST (SAFETY SEMANTICS 17; founder
2026-09-27, "ensure that fire spread includes local weather forecast";
cfg.weather_grid_km, default 0 = OFF). After the fire-centroid forecast
(fetched exactly as before — its failure is the legacy provider failure),
a FEED incident's domain also gets the forecasts of the global lattice nodes
inside it (wgresponse.wxfield.build_field, through the same provider; a
failed node's zone falls back to the centroid forecast with a note naming
it) and spreads on the per-cell nearest-point zones
(spread.ensemble_arrival(weather_field=...)). The centroid forecast stays
the ONE forecast for everything else: the extreme-regime posture, the
dispatch gel windows (forecasts=...), the off-domain horizon (it reads the
P10 raster the zones produced). Seeded (drill) incidents keep their single
point (SEMANTICS 12f). The field rides CycleResult.weather_fields; notes say
what was asked and what failed. Off = no field, no note: byte-identical.

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

TWO WORLDS (2026-09-27; contracts SAFETY SEMANTICS 16, wgresponse.worlds):
the watch daemon's monitor is the LIVE world's and is locked (lock_live():
every seed is refused with seeded.LiveWorldRefused and no cycle ever unions
one); seeds run in the TEST world's own monitor (worlds._TestWorldMonitor,
a subclass driving this same cycle() over the live cycle's snapshot). After
the planner, every cycle records a WorldSnapshot (last_snapshot): the post-
gate incident set, assessments, ensembles, in-domain clients, ETAs,
forecasts, eroded rasters, regime flags, registry rosters, failed incidents,
feed health, notes and feed-blind events — shallow container copies around
the cycle's own objects, never read by the live path (byte-neutral).

FIRE BELIEF (contracts SAFETY SEMANTICS 14; wgresponse.fire_belief,
docs/FIRE_BELIEF.md): with cfg.fire_belief_track the monitor owns a
FireBeliefStore (``fire_beliefs``; injectable — a TEST world passes a
copy()). Three thin hooks: observe() after the staleness gate (growth
observations -> windows -> posterior; a surprise reset is a note +
OperatorEvent('fire_belief')), before_spread()/after_spread() around each
domain's ensemble (weather log, new-pattern rating, the nominal growth
curve; with cfg.fire_belief_apply the per-hour member multipliers), and
statuses() into CycleResult.fire_status. Any belief failure is a note and
the legacy physics. Track off = byte-identical cycles.

Units: times unix seconds, distances meters, config durations hours/minutes
per contracts.
"""
from __future__ import annotations

import dataclasses
import logging
import math
import threading
import time
from types import SimpleNamespace
from typing import Optional

from wgresponse import feedsanity
from wgresponse import fire_belief as fire_belief_mod
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
    RemovedIncident,
    ResponseConfig,
    ThreatAssessment,
    is_seeded_id,
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

# FORECAST FRESHNESS TIERS (contracts forecast_tiers): a client pair at one of
# these states puts its incident in the THREAT tier — the ladder's medium rung
# and up, the fire has clients in its path
_THREAT_TIER_STATES = frozenset({
    AlertState.WATCH, AlertState.EXTREME_24H, AlertState.IMPACTED,
})


def _fire_key(perimeter, provenance_s, seeds) -> tuple:
    """FORECAST FRESHNESS TIERS: the fire geometry a kept forecast was made
    from — its perimeter rings, the perimeter's own time, its seeds."""
    def pts(ps):
        return tuple((round(p.lat, 6), round(p.lon, 6)) for p in ps)
    return (tuple(pts(r) for r in (perimeter or ())), provenance_s,
            pts(seeds))


class _KeptForecast:
    """FORECAST FRESHNESS TIERS: one incident's forecast kept between
    cycles — the domain it covers (clients_key), the geometry it was made
    from (fire_key), when (at_s), what the cycle registers from it (inputs:
    the centroid forecast, the weather field, the regime flag), the ensemble,
    the tiered eroded P10, each source's acreage then — and the background
    refresh in flight (pending future + its inputs)."""
    __slots__ = ("clients_key", "fire_key", "at_s", "inputs", "ens",
                 "eroded", "acres", "pending", "pending_meta")

    def __init__(self, clients_key, fire_key, at_s, inputs, ens, eroded,
                 acres):
        self.clients_key = clients_key
        self.fire_key = fire_key
        self.at_s = at_s
        self.inputs = inputs
        self.ens = ens
        self.eroded = eroded
        self.acres = acres
        self.pending = None
        self.pending_meta = None


class _LearnJob:
    """LEARNING FORECASTS (SAFETY SEMANTICS 40): one fire's learning
    forecast in flight — the future, the observations it is for (the
    belief's curve_token when it was asked), the incident as the cycle saw
    it, and the cycle time it was made at."""
    __slots__ = ("future", "token", "inc", "at_s")

    def __init__(self, future, token, inc, at_s):
        self.future = future
        self.token = token
        self.inc = inc
        self.at_s = at_s


class _TerrainTurn:
    """THE TERRAIN TURN (SAFETY SEMANTICS 40): the cycle holds it while it
    assesses — its own LANDFIRE fetches run then — and a learning forecast
    starts only once it is free. The live LANDFIRE service ran one job at a
    time (2026-10-30: ~3 min each, two per domain), so a learning fetch in
    front of the cycle's held every domain behind it."""

    def __init__(self):
        self._free = threading.Event()
        self._free.set()

    def hold(self) -> None:
        self._free.clear()

    def release(self) -> None:
        self._free.set()

    @property
    def held(self) -> bool:
        return not self._free.is_set()

    def wait(self) -> None:
        """Block until the cycle is not assessing."""
        self._free.wait()


def _snap_bbox(bb: BBox, deg: float) -> BBox:
    """bb snapped outward to a `deg`-degree grid (the terrain cache key: a
    shape that stays inside one snapped box asks one tile); deg <= 0 = bb."""
    if deg <= 0.0:
        return bb
    return BBox(west=round(math.floor(bb.west / deg) * deg, 6),
                south=round(math.floor(bb.south / deg) * deg, 6),
                east=round(math.ceil(bb.east / deg) * deg, 6),
                north=round(math.ceil(bb.north / deg) * deg, 6))


def _satellite_only(inc: FireIncident) -> bool:
    """SATELLITE HEAT ONLY (SAFETY SEMANTICS 41): an incident only the
    satellite reports — a FIRMS incident no reported feed contributed to
    (39 puts "firms" into a reported fire's source ids, never the reverse),
    never a seeded fire."""
    return (inc.source is IncidentSource.FIRMS
            and not is_seeded_id(inc.incident_id)
            and set((inc.source_ids or {}).keys()) <= {"firms"})


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


# ------------------------------------------------ SAFETY SEMANTICS 13 helpers

def _finite_num(v) -> Optional[float]:
    """A finite float from an int/float (never a bool); else None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _source_name(inc: FireIncident) -> str:
    src = inc.source
    return str(getattr(src, "value", None) or src)


#: containment / control / out declarations (feeds meta key, note label)
_DECLARATIONS = (("contained_at_s", "declared contained"),
                 ("controlled_at_s", "declared controlled"),
                 ("out_at_s", "declared out"))


def contained_signals(inc: FireIncident,
                      now_s: Optional[float] = None) -> "tuple[list, list]":
    """(signals, contradictions): the feed's containment declarations for
    one incident (contracts SAFETY SEMANTICS 13a).

    signals — every source reporting containment_pct >= 100 ('wfigs reports
    100% contained') and every containment / control / out timestamp in
    meta ('declared out 3.0 h ago' when now_s is given); contradictions —
    every source reporting containment BELOW 100. Per-source values come
    from fusion's meta['containment_by_source'] when present (a multi-source
    group), else the incident's own containment_pct. The feed declares the
    incident contained iff signals and not contradictions: one source below
    100 keeps a fire on the map whatever the others say."""
    meta = inc.meta or {}
    by_src = meta.get("containment_by_source")
    if not isinstance(by_src, dict) or not by_src:
        by_src = ({_source_name(inc): inc.containment_pct}
                  if inc.containment_pct is not None else {})
    signals: list = []
    contra: list = []
    for src in sorted(by_src):
        pct = _finite_num(by_src[src])
        if pct is None:
            continue
        (signals if pct >= 100.0 else contra).append(
            f"{src} reports {pct:g}% contained")
    for key, label in _DECLARATIONS:
        t = _finite_num(meta.get(key))
        if t is None or t <= 0.0:
            continue
        signals.append(f"{label} {max(0.0, now_s - t) / 3600.0:.1f} h ago"
                       if now_s is not None else label)
    return signals, contra


def _acres_by_source(inc: FireIncident) -> dict:
    """{source: acres} — fusion's meta['acres_by_source'] (each source's
    freshest value) when present, else the incident's own acreage."""
    by = (inc.meta or {}).get("acres_by_source")
    if not isinstance(by, dict) or not by:
        by = ({_source_name(inc): inc.acreage}
              if inc.acreage is not None else {})
    out = {}
    for src, v in by.items():
        f = _finite_num(v)
        if f is not None:
            out[str(src)] = f
    return out


def _colocated_note(merges: list) -> str:
    """The cycle note for the unions co-located fusion made this cycle
    (SAFETY SEMANTICS 24d: nothing merges silently) — every union's
    surviving id and name, the incidents it merged and the evidence."""
    parts = []
    for m in merges:
        merged = ", ".join(f"{iid} ({name})" for iid, name
                           in zip(m["merged"], m["names"]))
        parts.append(f"{m['incident_id']} '{m['name']}' <- {merged} "
                     f"[{'+'.join(m['evidence'])}]")
    return (f"co-located fusion merged {len(merges)} duplicate incident "
            f"group(s) into one fire each (SAFETY SEMANTICS 24): "
            + "; ".join(parts))


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
        fire_beliefs: optional wgresponse.fire_belief.FireBeliefStore
            (SAFETY SEMANTICS 14) — a TEST world injects a copy of the live
            store; None creates one when cfg.fire_belief_track is on.
    """

    #: TWO WORLDS (SAFETY SEMANTICS 16a): False once lock_live() made this
    #: the LIVE world's monitor — seeds refused, none ever unioned.
    accepts_seeds = True
    #: TWO WORLDS (16b): the last completed cycle's WorldSnapshot (or None).
    _last_snapshot = None

    def __init__(self, feeds: list, weather_provider, terrain_provider,
                 registry, alert_engine, cfg: ResponseConfig, safe_zones: list,
                 crew_tracker=None, dispatcher=None, aoi=None,
                 fire_beliefs=None) -> None:
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
        # SAFETY SEMANTICS 13a memory (cfg.drop_contained_incidents only):
        # incident_id -> {since_s, acres {source: baseline}, last_growth_s,
        # growth, removed, signals, seen_s}; in-process (module docstring).
        self._contained: dict = {}
        # FIRE BELIEF (SAFETY SEMANTICS 14; wgresponse.fire_belief): consulted
        # only with cfg.fire_belief_track. `fire_beliefs` injects a store —
        # a TEST (drill) world passes live_monitor.fire_beliefs.copy()
        # (persistent=False: it learns, it never writes back).
        if fire_beliefs is None and getattr(cfg, "fire_belief_track", False):
            fire_beliefs = fire_belief_mod.FireBeliefStore()
        self._fire_beliefs = fire_beliefs
        # FEED SANITY (SAFETY SEMANTICS 18; cfg.feed_sanity only): each
        # fire's last plausible polygon (in-process), the incidents found
        # implausible this / last cycle (an OperatorEvent on first finding)
        # and this cycle's events, drained by cycle().
        self._perimeter_memory = feedsanity.PerimeterMemory()
        self._sanity_now: set = set()
        self._sanity_prev: set = set()
        self._sanity_events: list = []
        # FORECAST FRESHNESS TIERS (cfg.forecast_tiers only): incident_id ->
        # _KeptForecast, the one background refresh worker (made on first
        # use), the alert states this cycle's tiers read, and the refreshes
        # whose inputs failed this cycle (drained into operator events).
        self._kept: dict = {}
        self._refresher = None
        self._tier_states: dict = {}
        self._kept_failures: list = []
        # LEARNING FORECASTS (cfg.fire_belief_learn only; SAFETY SEMANTICS
        # 40): incident_id -> _LearnJob in flight, and their ONE background
        # worker (made on first use), and the terrain turn the cycle holds
        # while it assesses.
        self._learning: dict = {}
        self._learner = None
        self._terrain_turn = _TerrainTurn()
        # SATELLITE HEAT ONLY (cfg.hotspot_heat_only only; SAFETY SEMANTICS
        # 41): this cycle's heat near protected homes (rows for the
        # operator) and the clusters already announced while in range.
        self._heat_near: list = []
        self._heat_noted: set = set()
        # HOTSPOT SCREENING STEP 0 (cfg.screen_labels only; SAFETY SEMANTICS
        # 43): this cycle's shadow-label counts.
        self._screen_last: Optional[dict] = None

    @property
    def fire_beliefs(self):
        """The per-fire belief store (None unless tracking or injected)."""
        return self._fire_beliefs

    def use_fire_beliefs(self, store) -> None:
        """Swap in a belief store (TWO WORLDS, SAFETY SEMANTICS 14/16): the
        TEST world hands its monitor a fresh non-persistent copy of the LIVE
        store before every drill cycle, so drills start from the rates the
        live world has learned and never write back."""
        self._fire_beliefs = store

    def _belief_on(self) -> bool:
        return (self._fire_beliefs is not None
                and bool(getattr(self._cfg, "fire_belief_track", False)))

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
        ignition) / seeded.SeedCapacityError (too many active) /
        seeded.LiveWorldRefused (this is the locked LIVE world — SAFETY
        SEMANTICS 16a)."""
        self._refuse_if_live()
        return self._seeded.create(lat, lon, t_ignite_s, **kw)

    def add_seed(self, ign) -> "object":
        """Store a pre-built contracts.SeededIgnition (tests, tooling).
        seeded.LiveWorldRefused on the locked LIVE world."""
        self._refuse_if_live()
        return self._seeded.add(ign)

    # --------------------------------------------- TWO WORLDS (SEMANTICS 16)

    def lock_live(self) -> None:
        """Make this the LIVE world's monitor (SAFETY SEMANTICS 16a): every
        seed is refused from now on (seeded.LiveWorldRefused) and no cycle
        unions one. Idempotent; the watch daemon calls it on its monitor."""
        self.accepts_seeds = False

    @property
    def last_snapshot(self):
        """The last completed cycle's WorldSnapshot (SAFETY SEMANTICS 16b) —
        what the TEST world builds on; None before the first cycle."""
        return self._last_snapshot

    def _refuse_if_live(self) -> None:
        if not self.accepts_seeds:
            raise seeded_mod.LiveWorldRefused()

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
        number restored (malformed rows are skipped loudly, never raised).
        seeded.LiveWorldRefused on the locked LIVE world (its persisted
        seeds are migrated to the TEST world instead — SEMANTICS 16a)."""
        self._refuse_if_live()
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
        cfg.incident_stale_h are excluded from the cycle with a note — and,
        under cfg.drop_contained_incidents, the SAFETY SEMANTICS 13 removal
        of contained, not-growing feed fires, listed in removed_incidents),
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
        removed: list = []
        if self._cfg.drop_contained_incidents:
            # SAFETY SEMANTICS 13a-d: contained, not-growing feed fires leave
            # the set here — before any domain — and are counted, never
            # silently; their pairs then take the absence law below.
            active, removed = self._drop_contained_incidents(active, now_s,
                                                             notes)
        if self._cfg.fuse_hotspots:
            # SAFETY SEMANTICS 39: satellite detections on a reported fire
            # join it (after 9 and 13: a removed fire takes only its own
            # interior heat); the rest stay FIRMS fires
            active, removed = self._attach_hotspots(active, removed, notes)
        if self._cfg.screen_labels:
            # SAFETY SEMANTICS 43: shadow labels on what only a satellite
            # sees — a known heat site, a registered burn, its looks and
            # growth; nothing they touch changes
            active = self._screen_label(active, now_s, notes)
        heat: list = []
        if self._cfg.hotspot_heat_only:
            # SAFETY SEMANTICS 41: what only a satellite sees is heat on the
            # map — no domain, alert or crew; a pair it raised before takes
            # the absence law (never the blind one)
            active, heat = self._split_heat(active, now_s, notes,
                                            operator_events)
        learn = None
        if self._learn_on():
            # SAFETY SEMANTICS 40: finished learning forecasts are pinned
            # BEFORE this cycle's observations — to the ones they were for
            learn = self._learn_collect(notes)
        if self._belief_on():
            # SAFETY SEMANTICS 14: this cycle's growth observations (the
            # belief learns BEFORE the spread it may inform)
            self._belief_observe(active, now_s, notes, operator_events)

        assessments: list = []
        ensembles: dict = {}
        clients_seen: dict = {}
        etas: dict = {}
        forecasts: dict = {}            # incident_id -> WeatherForecast (C5)
        failed_incidents: set = set()
        roster_seen: dict = {}          # incident_id -> {client_id} registry returned
        extreme_flags: dict = {}        # incident_id -> bool (extreme spread regime)
        eroded_map: dict = {}           # incident_id -> tiered eroded raster
        weather_fields: dict = {}       # incident_id -> WeatherField (SEMANTICS 17)
        if self._cfg.feed_sanity:
            # SAFETY SEMANTICS 18: this cycle's implausible-perimeter findings
            self._sanity_now = set()
            self._sanity_events = []
        freshness: dict = {}            # incident_id -> forecast freshness
        if self._cfg.forecast_tiers:
            # FORECAST FRESHNESS TIERS: the tiers read the states the cycle
            # starts from (a client at WATCH or above = THREAT)
            try:
                self._tier_states = dict(self._alert_engine.states())
            except Exception as exc:
                self._tier_states = {}
                notes.append(f"forecast tiers: alert states unavailable "
                             f"({exc}) — no incident is in the threat tier "
                             f"this cycle")
            self._kept_failures = []
        no_domain = [] if learn is not None else None
        if learn is not None:
            # SAFETY SEMANTICS 40: the TERRAIN TURN — no learning forecast
            # starts while the cycle fetches its own domains' terrain
            self._terrain_turn.hold()
        try:
            for inc in active:
                try:
                    self._assess_incident(inc, now_s, assessments, ensembles,
                                          clients_seen, etas, notes,
                                          roster_seen, forecasts,
                                          extreme_flags, eroded_map,
                                          timings=timings,
                                          weather_fields=weather_fields,
                                          freshness=freshness,
                                          no_domain=no_domain)
                except _ProviderFailure as pf:
                    failed_incidents.add(inc.incident_id)
                    notes.append(f"incident {inc.incident_id}: assessment "
                                 f"skipped ({pf})")
                    operator_events.append(OperatorEvent(
                        t_s=now_s, kind="provider_error",
                        message=(f"{pf.step} provider failed for incident "
                                 f"{inc.incident_id}: {pf.cause}"),
                        incident_id=inc.incident_id,
                        detail={"step": pf.step}))
                except Exception as exc:  # degrade, never raise out of cycle()
                    failed_incidents.add(inc.incident_id)
                    notes.append(f"incident {inc.incident_id}: assessment "
                                 f"skipped ({exc})")
                    operator_events.append(OperatorEvent(
                        t_s=now_s, kind="provider_error",
                        message=(f"assessment failed for incident "
                                 f"{inc.incident_id}: {exc}"),
                        incident_id=inc.incident_id,
                        detail={"step": "assess"}))
        finally:
            self._terrain_turn.release()
        if self._cfg.forecast_tiers:
            # FORECAST FRESHNESS TIERS: a refresh that could not get its
            # inputs is a provider error (the kept forecast stays served);
            # forecasts of incidents gone from the active set are dropped
            for iid, step, cause in self._kept_failures:
                operator_events.append(OperatorEvent(
                    t_s=now_s, kind="provider_error",
                    message=(f"{step} provider failed refreshing incident "
                             f"{iid}'s forecast: {cause} — its previous "
                             f"forecast stays served"),
                    incident_id=iid, detail={"step": step, "kept": True}))
            self._kept_failures = []
            live = {inc.incident_id for inc in active}
            for iid in [k for k in self._kept if k not in live]:
                del self._kept[iid]
        if learn is not None:
            # SAFETY SEMANTICS 40: fires that built no domain and hold an
            # observation without a curve get a learning forecast
            self._learn_enqueue(no_domain, ensembles, now_s, learn)
            self._learn_note(learn, notes, timings)
        if self._cfg.feed_sanity:
            # SAFETY SEMANTICS 18c: first findings are operator events (never
            # a degradation); the memory forgets polygons past its window
            operator_events.extend(self._sanity_events)
            self._sanity_events = []
            self._sanity_prev = self._sanity_now
            self._perimeter_memory.prune(now_s, self._cfg)

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

        belief_status = None
        if self._belief_on():
            # SAFETY SEMANTICS 14: the per-fire status, computed BEFORE the
            # planner so the off-domain horizon (SEMANTICS 15) OR-combines
            # the belief's rapid / known with the feed's behaviour report
            try:
                belief_status = self._fire_beliefs.statuses(active, now_s,
                                                            self._cfg)
            except Exception as exc:
                notes.append(f"fire belief: status failed ({exc})")
        offmap: dict = {}
        if self._cfg.corridor_offraster_per_fire and ensembles:
            # SAFETY SEMANTICS 15: each domain fire's off-domain horizon
            offmap = self._offmap_horizons(active, ensembles, now_s, notes,
                                           status=belief_status)
        plan = self._maybe_dispatch(now_s, ensembles, clients_seen, etas,
                                    notes, forecasts,
                                    eroded_map=(eroded_map or None),
                                    offmap=(offmap or None))
        timings["dispatch_s"] = time.monotonic() - _t
        # TWO WORLDS (SAFETY SEMANTICS 16b): the read-only record the TEST
        # world builds on — shallow container copies around this cycle's own
        # objects (the rasters are shared, never copied). Nothing on the
        # live path reads it: the CycleResult below is unchanged, and a
        # failure here is logged, never raised (cycle() never raises).
        try:
            from wgresponse.contracts import WorldSnapshot
            self._last_snapshot = WorldSnapshot(
                t_s=now_s, incidents=tuple(active),
                assessments=tuple(assessments), ensembles=dict(ensembles),
                clients=dict(clients_seen), etas=dict(etas),
                forecasts=dict(forecasts), eroded=dict(eroded_map),
                extreme_flags=dict(extreme_flags),
                roster_seen={k: frozenset(v) for k, v in roster_seen.items()},
                failed_incidents=frozenset(failed_incidents),
                feeds_healthy=bool(feeds_healthy), notes=tuple(notes),
                feed_events=tuple(ev for ev in operator_events
                                  if getattr(ev, "kind", None)
                                  == "feed_blind"))
        except Exception:
            log.exception("world snapshot not recorded this cycle (the TEST "
                          "world keeps the previous one)")

        degraded = (not feeds_healthy) or bool(failed_incidents) or any(
            ev.kind in ("degraded", "feed_blind", "provider_error")
            for ev in operator_events)
        # SAFETY SEMANTICS 41: the heat is listed after the forecast fires
        res = CycleResult(t_s=now_s, incidents=active + heat,
                          assessments=assessments,
                          events=events, plan=plan, ensembles=ensembles,
                          notes=notes, operator_events=operator_events,
                          degraded=degraded, timings=timings,
                          removed_incidents=removed, offmap=offmap,
                          weather_fields=weather_fields)
        if belief_status is not None:
            # SAFETY SEMANTICS 14: the per-fire status (off-map rule seam)
            res.fire_status = belief_status
        if self._cfg.forecast_tiers:
            res.freshness = freshness   # FORECAST FRESHNESS TIERS
        return res

    # ------------------------------------------------ fire belief (SEM 14)

    def _belief_observe(self, active: list, now_s: float, notes: list,
                        operator_events: list) -> None:
        """Feed this cycle's incidents to the belief store; a surprise reset
        is a note + OperatorEvent('fire_belief'); a failure is a note (the
        belief never breaks a cycle — the prior simply keeps applying)."""
        try:
            evs = self._fire_beliefs.observe(active, now_s, self._cfg)
        except Exception as exc:
            notes.append(f"fire belief: observe failed ({exc})")
            return
        for ev in evs:
            msg = f"fire belief {ev['incident_id']}: {ev['message']}"
            notes.append(msg)
            if ev["kind"] == "sanity":
                # SAFETY SEMANTICS 18 (cfg.feed_sanity only): what the belief
                # skipped or held is a note; the seed side raises the alarm
                continue
            operator_events.append(OperatorEvent(
                t_s=now_s, kind="fire_belief", message=msg,
                incident_id=ev["incident_id"], detail={"event": ev["kind"]}))

    def _belief_before_spread(self, inc, tile, wxf, now_s: float, cfg,
                              notes: list):
        """member_hour_scale for this incident's ensemble, or None (legacy
        physics — also on any belief failure: fail-safe to the prior)."""
        try:
            return self._fire_beliefs.before_spread(inc, tile, wxf, now_s, cfg)
        except Exception as exc:
            notes.append(f"fire belief {inc.incident_id}: prior applies "
                         f"({exc})")
            return None

    def _belief_after_spread(self, inc, ens, now_s: float, cfg, anchor_s,
                             point: bool, notes: list) -> None:
        try:
            self._fire_beliefs.after_spread(inc, ens, now_s, cfg, anchor_s,
                                            point)
        except Exception as exc:
            notes.append(f"fire belief {inc.incident_id}: growth curve not "
                         f"kept ({exc})")

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
        merged. The locked LIVE world (SEMANTICS 16a) unions nothing, ever."""
        if not self.accepts_seeds:
            return []
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

        CO-LOCATED FUSION (cfg.fuse_colocated_incidents, contracts SAFETY
        SEMANTICS 24): the call carries the config and this cycle's clock
        (the pass only joins groups the staleness gate keeps) and every
        union it makes is one cycle note. Knob off = the legacy one-argument
        call, byte-identical.
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
            if getattr(self._cfg, "fuse_colocated_incidents", False):
                merges: list = []
                fused = list(fuse_incidents(batches, cfg=self._cfg,
                                            now_s=now_s, merges=merges))
                if merges:
                    notes.append(_colocated_note(merges))
                return fused, healthy
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

    def _drop_contained_incidents(self, active: list, now_s: float,
                                  notes: list) -> "tuple[list, list]":
        """REMOVAL RULE (contracts SAFETY SEMANTICS 13a-d; founder ruling
        2026-09-27): split this cycle's post-staleness-gate set into (kept,
        [RemovedIncident]). Per FEED incident (seeds pass through):

          contradiction (a source reports containment < 100)
              -> on the map; the memory is dropped (a later declaration
                 re-baselines); "back on the map" note if it was removed
          growth since removal (a source's acreage above its baseline)
              -> on the map this cycle and until cfg.containment_no_growth_h
                 pass without further growth (note every cycle)
          declared contained now (contained_signals)
              -> REMOVED (the first removal fixes since_s + the per-source
                 acreage baseline)
          removed before, declaration merely quiet now (the fire left the
          WFIGS Current layer, a feed is blind)
              -> stays REMOVED: only contradiction or growth returns a fire
          otherwise -> on the map (legacy)

        Memory of fires not seen for cfg.incident_stale_h is forgotten. One
        note per cycle counts and lists the removed ids with their reasons
        (13c). Never raises on dirty values (non-finite sizes are ignored)."""
        cfg = self._cfg
        hold_s = max(0.0, float(cfg.containment_no_growth_h)) * 3600.0
        kept: list = []
        removed: list = []
        for inc in active:
            if (inc.source is IncidentSource.SEEDED
                    or is_seeded_id(inc.incident_id)):
                kept.append(inc)            # 13a: a seeded fire is never removed
                continue
            iid = inc.incident_id
            signals, contra = contained_signals(inc, now_s)
            mem = self._contained.get(iid)
            if contra:
                if mem is not None:
                    if mem.get("removed"):
                        notes.append(
                            f"incident {iid}: back on the map — {contra[0]} "
                            f"(removed as contained since "
                            f"{(now_s - mem['since_s']) / 3600.0:.1f} h ago; "
                            f"SAFETY SEMANTICS 13d)")
                    del self._contained[iid]
                kept.append(inc)
                continue
            if mem is None:
                if not signals:
                    kept.append(inc)        # never declared: the legacy path
                    continue
                mem = {"since_s": float(now_s),
                       "acres": _acres_by_source(inc),
                       "last_growth_s": None, "growth": None,
                       "removed": False, "signals": list(signals)}
                self._contained[iid] = mem
            else:
                grown = []
                for src, a in sorted(_acres_by_source(inc).items()):
                    base = mem["acres"].get(src)
                    if base is None:
                        mem["acres"][src] = a     # a new source: its baseline
                    elif a > base + 1e-9 * max(1.0, abs(base)):
                        grown.append((src, base, a))
                        mem["acres"][src] = a     # growth measured from here
                if grown:
                    mem["last_growth_s"] = float(now_s)
                    mem["growth"] = grown
                if signals:
                    mem["signals"] = list(signals)
            mem["seen_s"] = float(now_s)
            lg = mem["last_growth_s"]
            if lg is not None and (lg >= now_s or now_s - lg < hold_s):
                src, a0, a1 = mem["growth"][0]
                verb = "back on the map" if mem["removed"] else "kept on the map"
                notes.append(
                    f"incident {iid}: {verb} — the feed reports it contained "
                    f"({'; '.join(mem['signals'])}) but it GREW ({src} "
                    f"{a0:,.1f} -> {a1:,.1f} ac, {(now_s - lg) / 3600.0:.1f} "
                    f"h ago): on the map until {cfg.containment_no_growth_h:g} "
                    f"h pass without growth (SAFETY SEMANTICS 13a/d)")
                mem["removed"] = False
                kept.append(inc)
                continue
            if not signals and not mem["removed"]:
                # returned by growth earlier and no longer declared: legacy
                del self._contained[iid]
                kept.append(inc)
                continue
            reason = ("; ".join(signals) if signals else
                      f"last reported contained ({'; '.join(mem['signals'])}); "
                      f"no source contradicts it")
            mem["removed"] = True
            removed.append(RemovedIncident(
                incident=inc, reason=reason,
                signals=tuple(signals or mem["signals"]),
                removed_since_s=mem["since_s"]))
        stale_s = float(cfg.incident_stale_h) * 3600.0
        for iid in [k for k, m in self._contained.items()
                    if now_s - m.get("seen_s", now_s) > stale_s]:
            del self._contained[iid]
        if removed:
            listed = ", ".join(
                f"{r.incident.incident_id} ({r.incident.name or '?'}: "
                f"{r.reason})" for r in removed)
            note = (f"removed {len(removed)} contained incident(s) from the "
                    f"map — reported contained by the feed and not growing "
                    f"(cfg.drop_contained_incidents; SAFETY SEMANTICS 13, "
                    f"founder ruling 2026-09-27): no domain, no ETAs, no "
                    f"dispatch; their client pairs follow the absence law "
                    f"(toward CLEARED while every feed is healthy, frozen "
                    f"while any is blind; IMPACTED stays terminal): {listed}")
            notes.append(note)
            log.info("%s", note)
        return kept, removed

    # ------------------------------------------- learning forecasts (SEM 40)

    def _learn_on(self) -> bool:
        return (self._belief_on()
                and bool(getattr(self._cfg, "fire_belief_learn", False)))

    def learning_status(self) -> Optional[dict]:
        """LEARNING FORECASTS (SAFETY SEMANTICS 40): the rule and how many
        are in flight; None when the knob is off."""
        if not self._learn_on():
            return None
        return {"pad_m": float(self._cfg.fire_belief_learn_pad_m),
                "snap_deg": float(self._cfg.fire_belief_learn_snap_deg),
                "in_flight": len(self._learning)}

    def _learn_pool(self):
        """The ONE background worker learning forecasts run on (never the
        kept-forecast refresher's: a cold terrain fetch here must not delay
        a client's refresh)."""
        if self._learner is None:
            from concurrent.futures import ThreadPoolExecutor
            self._learner = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="wg-belief-learn")
        return self._learner

    def _learn_candidate(self, inc: FireIncident, ensembles: dict) -> bool:
        """A feed fire that built no domain this cycle; never a FIRMS
        cluster (no size reports to learn from) or a seeded fire."""
        return (inc.source not in (IncidentSource.FIRMS,
                                   IncidentSource.SEEDED)
                and not is_seeded_id(inc.incident_id)
                and inc.incident_id not in ensembles)

    def _learn_domain(self, geom_pts: list) -> BBox:
        """The fire's geometry padded fire_belief_learn_pad_m, snapped
        outward to fire_belief_learn_snap_deg (the terrain cache key)."""
        cfg = self._cfg
        bb = geo.bbox_around(geom_pts, float(cfg.fire_belief_learn_pad_m))
        return _snap_bbox(bb, float(cfg.fire_belief_learn_snap_deg))

    def _learn_forecast(self, inc: FireIncident, now_s: float, ign: list,
                        perimeter, perimeter_at_s, provenance_s, seeds: list,
                        geom_pts: list):
        """ON THE LEARNING WORKER: the nominal member alone over the
        learning domain, on the cycle's own input law — started only once
        the cycle is not assessing (the terrain turn). Returns (ensemble,
        spread cfg, curve anchor, point start)."""
        self._terrain_turn.wait()
        domain = self._learn_domain(geom_pts)
        b = self._forecast_inputs(inc, now_s, domain, self._pick_cell_m(domain),
                                  ign, perimeter, perimeter_at_s, provenance_s,
                                  [], {})
        b.spread_cfg = dataclasses.replace(b.spread_cfg, ensemble_n=1)
        ens = self._forecast_spread(inc, b, seeds, perimeter, now_s)
        anchor = b.perimeter_at_s if perimeter is not None else now_s
        return ens, b.spread_cfg, anchor, perimeter is None

    def _learn_collect(self, notes: list) -> dict:
        """Pin each finished learning forecast to the observations it was
        made for — only while they are still the newest without a curve
        (a newer observation, or a curve pinned meanwhile, drops it). A
        failure is counted for the cycle note: never a provider error."""
        stats = {"kept": [], "dropped": [], "failed": [], "queued": []}
        for iid in sorted(self._learning):
            job = self._learning[iid]
            if not job.future.done():
                continue
            del self._learning[iid]
            try:
                ens, spread_cfg, anchor, point = job.future.result()
            except _ProviderFailure as pf:
                stats["failed"].append(f"{iid} ({pf.step}: {pf.cause})")
                continue
            except Exception as exc:
                stats["failed"].append(f"{iid} ({exc})")
                continue
            if self._fire_beliefs.curve_token(iid, job.at_s) != job.token:
                stats["dropped"].append(iid)
                continue
            self._belief_after_spread(job.inc, ens, job.at_s, spread_cfg,
                                      anchor, point, notes)
            stats["kept"].append(iid)
        return stats

    def _learn_enqueue(self, no_domain: list, ensembles: dict, now_s: float,
                       stats: dict) -> None:
        """Queue a learning forecast for each fire without a domain whose
        newest observation has no curve (one in flight per fire)."""
        for inc, ign, perimeter, pat, prov, seeds, geom_pts in no_domain:
            iid = inc.incident_id
            if iid in self._learning or not self._learn_candidate(
                    inc, ensembles):
                continue
            token = self._fire_beliefs.curve_token(iid, now_s)
            if token is None:
                continue
            fut = self._learn_pool().submit(
                self._learn_forecast, inc, now_s, ign, perimeter, pat, prov,
                seeds, geom_pts)
            self._learning[iid] = _LearnJob(fut, token, inc, now_s)
            stats["queued"].append(iid)

    def _learn_note(self, stats: dict, notes: list, timings: dict) -> None:
        """One cycle note when anything happened; counts in timings."""
        timings["learning"] = {k: len(v) for k, v in stats.items()}
        timings["learning"]["in_flight"] = len(self._learning)
        parts = []
        if stats["kept"]:
            shown = ", ".join(stats["kept"][:8])
            more = (f" +{len(stats['kept']) - 8} more"
                    if len(stats["kept"]) > 8 else "")
            parts.append(f"kept {len(stats['kept'])} ({shown}{more})")
        if stats["dropped"]:
            parts.append(f"dropped {len(stats['dropped'])} (a newer "
                         f"observation arrived first)")
        if stats["failed"]:
            parts.append(f"failed {len(stats['failed'])} "
                         f"({'; '.join(stats['failed'][:3])})")
        if stats["queued"]:
            parts.append(f"queued {len(stats['queued'])}")
        if parts:
            notes.append(
                "fire belief learning: " + ", ".join(parts)
                + f"; {len(self._learning)} in flight — learning forecasts "
                  f"for fires without a protected client in range (the "
                  f"nominal member, its own area; SAFETY SEMANTICS 40)")

    def _attach_hotspots(self, active: list, removed: list,
                         notes: list) -> "tuple[list, list]":
        """HOTSPOT ATTACHMENT (contracts SAFETY SEMANTICS 39) over this
        cycle's kept set and its removed contained fires; one note when any
        detection joined a fire. A failure keeps both lists as they were (a
        note — the FIRMS clusters stay fires: nothing is hidden)."""
        try:
            from wgresponse.feeds import attach_hotspots
            kept, cont, st = attach_hotspots(
                active, [r.incident for r in removed], self._cfg)
        except Exception as exc:
            notes.append(f"hotspot attachment failed ({exc}) — every FIRMS "
                         f"cluster stays its own fire this cycle")
            return active, removed
        removed = [dataclasses.replace(r, incident=inc)
                   for r, inc in zip(removed, cont)]
        if st["attached"]:
            note = (f"hotspots: {st['attached']} of {st['detections']} FIRMS "
                    f"detection(s) joined {len(st['fires'])} reported "
                    f"fire(s) ({', '.join(st['fires'])})")
            if st["inside_contained"]:
                note += (f"; {st['inside_contained']} inside fires removed as "
                         f"contained (their own interior heat)")
            if st["clusters_absorbed"]:
                note += (f"; {len(st['clusters_absorbed'])} FIRMS cluster(s) "
                         f"wholly absorbed")
            if st["clusters_split"]:
                note += (f"; {len(st['clusters_split'])} split (the rest "
                         f"stays a FIRMS fire)")
            notes.append(note + " — SAFETY SEMANTICS 39")
        return kept, removed

    # ------------------------------------- screening shadow labels (SEM 43)

    def screen_status(self) -> Optional[dict]:
        """HOTSPOT SCREENING STEP 0 (SAFETY SEMANTICS 43): the last cycle's
        shadow-label counts and the five-day memory; None when the knob is
        off (or before the first cycle)."""
        if not self._cfg.screen_labels or self._screen_last is None:
            return None
        return dict(self._screen_last)

    def _screen_sources(self) -> tuple:
        """(the prescribed-burn records, the heat-site memory) the feeds
        hold this cycle: records None unless every adapter that keeps them
        (WfigsFeed keep_rx) answered whole; memory None without one."""
        holders = [f for f in self._feeds if getattr(f, "keeps_rx", False)]
        rx = None
        if holders and all(f.rx_records is not None for f in holders):
            rx = [r for f in holders for r in f.rx_records]
        memory = next((m for m in (getattr(f, "site_memory", None)
                                   for f in self._feeds) if m is not None),
                      None)
        return rx, memory

    def _screen_label(self, active: list, now_s: float, notes: list) -> list:
        """SAFETY SEMANTICS 43: the incident set with each satellite-only
        incident replaced by a copy whose meta carries "screen" (the feed's
        own record is never written: an adapter may hand the same object
        back next cycle), and one note with the counts. Same incidents,
        same order."""
        from wgresponse import screening
        rx, memory = self._screen_sources()
        try:
            rx_index = screening.prepare_rx(rx, now_s, self._cfg)
        except Exception as exc:              # noqa: BLE001 — noted
            rx_index = None
            notes.append(f"screening: prescribed-burn records unusable "
                         f"({exc}) — labelled unknown")
        labels, out = [], []
        for inc in active:
            if not _satellite_only(inc):
                out.append(inc)
                continue
            try:
                lab = screening.label(inc, memory, rx_index)
            except Exception as exc:          # noqa: BLE001 — noted
                notes.append(f"incident {inc.incident_id}: screening labels "
                             f"failed ({exc})")
                out.append(inc)
                continue
            out.append(dataclasses.replace(
                inc, meta={**(inc.meta or {}), "screen": lab}))
            labels.append(lab)
        self._screen_last = screening.summary(labels, memory, rx_index)
        notes.append(screening.summary_note(self._screen_last))
        return out

    # ------------------------------------- satellite heat only (SEM 41)

    def heat_status(self) -> Optional[dict]:
        """SATELLITE HEAT ONLY (SAFETY SEMANTICS 41): the rule and this
        cycle's heat near protected homes, nearest first; None when the
        knob is off."""
        if not self._cfg.hotspot_heat_only:
            return None
        return {"note_m": float(self._cfg.hotspot_heat_note_m),
                "near": [dict(r) for r in self._heat_near]}

    def _split_heat(self, active: list, now_s: float, notes: list,
                    operator_events: list) -> "tuple[list, list]":
        """SATELLITE HEAT ONLY (contracts SAFETY SEMANTICS 41): the
        incidents only the satellite reports leave the forecast set — the
        heat, each with a note saying why it has no domain. Heat within
        hotspot_heat_note_m of a protected home is this cycle's operator
        list, and one OperatorEvent('satellite_heat') when a cluster comes
        into range. Returns (the forecast set, the heat)."""
        keep, heat = [], []
        for inc in active:
            (heat if _satellite_only(inc) else keep).append(inc)
        radius = float(self._cfg.hotspot_heat_note_m)
        rows = []
        for inc in heat:
            notes.append(f"incident {inc.incident_id}: satellite heat only "
                         f"— no forecast or alerts until screened; spread "
                         f"skipped (SAFETY SEMANTICS 41)")
            if radius <= 0.0:
                continue
            pts = list(inc.hotspots or []) or [inc.origin]
            try:
                cands = list(self._registry.protected_clients(
                    geo.bbox_around(pts, radius)))
            except Exception as exc:
                notes.append(f"incident {inc.incident_id}: heat near homes "
                             f"not checked (registry: {exc})")
                continue
            near = sorted(((min(geo.haversine_m(c.loc, p) for p in pts), c)
                           for c in cands),
                          key=lambda dc: (dc[0], dc[1].client_id))
            near = [(d, c) for d, c in near if d <= radius]
            if not near:
                continue
            d0, c0 = near[0]
            meta = inc.meta or {}
            ts = [t for t in (meta.get("hotspot_t_s") or [inc.updated_at_s])
                  if isinstance(t, (int, float)) and math.isfinite(t)]
            rows.append({
                "incident_id": inc.incident_id, "name": inc.name,
                "lat": round(inc.origin.lat, 5),
                "lon": round(inc.origin.lon, 5),
                "nearest_client_id": c0.client_id,
                "nearest_client": c0.name or c0.client_id,
                "nearest_m": round(d0),
                "homes_in_range": len(near),
                "n_detections": len(pts),
                "newest_s": max(ts) if ts else float(inc.updated_at_s),
                "satellites": list(meta.get("satellites") or []),
                "max_frp": meta.get("max_frp")})
            if "screen" in meta:
                # SAFETY SEMANTICS 43: the shadow labels ride along
                rows[-1]["screen"] = meta["screen"]
        rows.sort(key=lambda r: (r["nearest_m"], r["incident_id"]))
        for r in rows:
            if r["incident_id"] in self._heat_noted:
                continue
            more = r["homes_in_range"] - 1
            operator_events.append(OperatorEvent(
                t_s=now_s, kind="satellite_heat",
                message=(f"satellite heat {r['nearest_m'] / 1000.0:.1f} km "
                         f"from {r['nearest_client']}"
                         + (f" (+{more} more home(s) within "
                            f"{radius / 1000.0:g} km)" if more else "")
                         + f" — {r['name']}: {r['n_detections']} "
                           f"detection(s), newest "
                           f"{max(0.0, now_s - r['newest_s']) / 3600.0:.1f} h "
                           f"ago; unscreened, no forecast or alerts (SAFETY "
                           f"SEMANTICS 41)"),
                incident_id=r["incident_id"], detail=dict(r)))
        self._heat_noted = {r["incident_id"] for r in rows}
        self._heat_near = rows
        if heat:
            notes.append(
                f"satellite heat only: {len(heat)} FIRMS cluster(s) on the "
                f"map without a forecast or alerts (unscreened)"
                + (f"; {len(rows)} within {radius / 1000.0:g} km of a "
                   f"protected home" if radius > 0.0 else "")
                + " — SAFETY SEMANTICS 41")
        return keep, heat

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
                         timings: Optional[dict] = None,
                         weather_fields: Optional[dict] = None,
                         freshness: Optional[dict] = None,
                         no_domain: Optional[list] = None) -> None:
        """Spread one incident and append per-client ThreatAssessments.

        ``no_domain`` (LEARNING FORECASTS, SAFETY SEMANTICS 40; a list when
        cfg.fire_belief_learn is armed): an incident with no protected
        client in range is appended with its seed geometry, (inc, ign,
        perimeter, perimeter_at_s, provenance_s, seeds, geom_pts) — after
        the feed-sanity step, so a learning forecast seeds what a domain
        forecast would have.

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
        tests/test_threat.py). Under cfg.perimeter_confirmed_anchor a polygon
        the feed has since CONFIRMED is seeded at the confirming report
        instead of its own time (_confirmed_perimeter_at_s; SAFETY SEMANTICS
        13e/f) — decided after the client-range filter, so only domain
        incidents carry the note.

        LOCAL WEATHER (cfg.weather_grid_km > 0; SAFETY SEMANTICS 17): after
        the centroid forecast, a feed incident's domain gets its weather
        field (_local_weather_field) and spreads on it; ``weather_fields``
        (when given) records it. The centroid forecast still feeds
        ``forecasts`` and the extreme-regime posture.

        FEED SANITY (cfg.feed_sanity; SAFETY SEMANTICS 18b/c): before any of
        the above, a feed incident's perimeter passes _sane_seed_incident —
        an implausible polygon time falls back to the record's, and a
        polygon collapsed under its record's size (or dropped below the
        remembered one beyond noise) is replaced by the fire's last plausible
        polygon for the geometry, the domain and the seed alike.
        """
        if (self._cfg.feed_sanity and inc.perimeter
                and inc.source is not IncidentSource.SEEDED):
            inc = self._sane_seed_incident(inc, now_s, notes)
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
            provenance_s = None
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
            if no_domain is not None:
                no_domain.append((inc, ign, perimeter, perimeter_at_s,
                                  provenance_s, seeds, geom_pts))
            return

        domain = geo.bbox_around(geom_pts + [c.loc for c in near],
                                 _DOMAIN_PAD_M)
        if self._cfg.forecast_domain_snap_deg > 0.0:
            # SAFETY SEMANTICS 42: snapped outward, so a shape that moves a
            # little keeps its domain and the terrain tile cached for it
            domain = _snap_bbox(domain, self._cfg.forecast_domain_snap_deg)
        cell_m = self._pick_cell_m(domain)
        if self._cfg.forecast_tiers:
            # FORECAST FRESHNESS TIERS: the kept forecast, refreshed by tier
            self._assess_kept(inc, now_s, near, domain, cell_m, ign,
                              perimeter, perimeter_at_s, provenance_s, seeds,
                              assessments, ensembles, clients_seen, etas,
                              notes, forecasts, extreme_flags, eroded_map,
                              timings, weather_fields, freshness)
            return
        _tm = {}
        b = self._forecast_inputs(inc, now_s, domain, cell_m, ign, perimeter,
                                  perimeter_at_s, provenance_s, notes, _tm,
                                  forecasts, extreme_flags, weather_fields)
        ens = self._forecast_spread(inc, b, seeds, perimeter, now_s)
        if self._belief_on():
            self._belief_after_spread(
                inc, ens, now_s, b.spread_cfg,
                b.perimeter_at_s if perimeter is not None else now_s,
                perimeter is None, notes)
        _tm["spread_s"] = time.monotonic() - b.t_spread
        _tm["cells"] = b.tile.spec.nrows * b.tile.spec.ncols
        _tm["clients"] = len(near)
        if timings is not None:
            timings.setdefault("incidents", {})[inc.incident_id] = _tm
        if (eroded_map is not None
                and getattr(self._cfg, "corridor_erosion_tiered", False)):
            # FUEL-TIERED EROSION: built here because this is where the fuel
            # tile lives; dispatch consumes it via the eroded_rasters seam.
            eroded_map[inc.incident_id] = spread.tiered_eroded_p10(
                ens.p10, b.tile.fuel, self._cfg,
                canopy=getattr(b.tile, "canopy", None))
        self._register_forecast(inc, ens, near, now_s, assessments,
                                ensembles, clients_seen, etas)

    def _forecast_inputs(self, inc: FireIncident, now_s: float, domain,
                         cell_m: float, ign: list, perimeter,
                         perimeter_at_s, provenance_s, notes: list, _tm: dict,
                         forecasts: Optional[dict] = None,
                         extreme_flags: Optional[dict] = None,
                         weather_fields: Optional[dict] = None):
        """Everything one incident's spread needs, in _assess_incident's
        order: terrain tile, fire-centroid forecast (+ the local weather
        field), the extreme-regime posture, the confirmed perimeter time and
        the belief's member multipliers; each given map is filled at its
        step. Returns a namespace (tile, wxf, wfield, spread_cfg, flag,
        perimeter_at_s, zoned, mhs, t_spread)."""
        _t = time.monotonic()
        try:
            tile = self._terrain.tile(domain, cell_m)
        except Exception as exc:
            raise _ProviderFailure("terrain", exc)
        _tm["terrain_s"] = time.monotonic() - _t
        pop = getattr(self._terrain, "pop_fallbacks", None)
        if pop is not None:
            # TERRAIN FALLBACK (watch --terrain-fallback): said per fire
            for name, err in pop():
                notes.append(
                    f"incident {inc.incident_id}: terrain from the bundled "
                    f"LANDFIRE tile {name} — the live terrain service failed "
                    f"({err[:160]})")
                _tm["terrain_fallback"] = name
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
        wfield = None
        if self._cfg.weather_grid_km > 0.0:
            # SAFETY SEMANTICS 17: the lattice forecasts across the domain
            # (the centroid forecast above stays the primary and the one
            # forecast everything below this block consumes).
            wfield = self._local_weather_field(inc, wxf, fire_centroid, tile,
                                               notes)
            if wfield is not None:
                _tm["weather_points"] = len(wfield.points)
                if weather_fields is not None:
                    weather_fields[inc.incident_id] = wfield
        _tm["weather_s"] = time.monotonic() - _t
        t_spread = time.monotonic()
        if forecasts is not None:
            # Sprint 2 C5 (Council-1 dispatch F5): the SAME fire-centroid
            # forecast that drives spread is threaded to dispatch.plan(
            # forecasts=...) so gel windows under cfg.gel_env_model_enabled
            # reflect the actual fire weather, not the 25 C reference.
            forecasts[inc.incident_id] = wxf

        spread_cfg = self._cfg
        flag = None
        if self._cfg.extreme_regime_enabled:
            # EXTREME SPREAD REGIME (contracts knob block): computed per
            # incident from the SAME tile + fire-centroid forecast that drive
            # spread, so the posture and the forecast see one world. With
            # extreme_regime_floor_frac > 0 the posture also carries the
            # spread physics for THIS incident (regime_spread_cfg).
            spread_cfg, flag = regime_spread_cfg(tile, wxf, now_s, self._cfg)
            if extreme_flags is not None:
                extreme_flags[inc.incident_id] = flag

        if perimeter and self._cfg.perimeter_confirmed_anchor:
            # SAFETY SEMANTICS 13e/f: a polygon the feed has since confirmed
            # (same size, later report, no newer polygon) burns from that
            # report, not from its own age; a growing fire keeps the legacy.
            perimeter_at_s = self._confirmed_perimeter_at_s(
                inc, provenance_s, perimeter_at_s, now_s, notes)
        # SAFETY SEMANTICS 17d: per-(zone, hour) weather; no field (knob off,
        # a seeded fire) or a domain with no lattice node inside spreads on
        # the centroid alone — the legacy call, no extra argument.
        zoned = ({"weather_field": wfield}
                 if wfield is not None and len(wfield.forecasts) > 1 else {})
        mhs = None
        if self._belief_on():
            # SAFETY SEMANTICS 14: weather log + novelty; with
            # fire_belief_apply the per-hour member multipliers (None = the
            # unchanged legacy call below)
            mhs = self._belief_before_spread(inc, tile, wxf, now_s,
                                             spread_cfg, notes)
        return SimpleNamespace(tile=tile, wxf=wxf, wfield=wfield,
                               spread_cfg=spread_cfg, flag=flag,
                               perimeter_at_s=perimeter_at_s, zoned=zoned,
                               mhs=mhs, t_spread=t_spread)

    @staticmethod
    def _forecast_spread(inc: FireIncident, b, seeds: list, perimeter,
                         now_s: float):
        """The ensemble itself from _forecast_inputs' namespace — a pure
        function of its arguments (FORECAST FRESHNESS TIERS run it on the
        background worker)."""
        if b.mhs is not None:
            # the belief drives the members; P10 guarded unless
            # fire_belief_apply_p10 (fire_belief.applied_ensemble)
            return fire_belief_mod.applied_ensemble(
                b.tile, seeds, b.wxf, now_s, b.spread_cfg, inc.incident_id,
                perimeter, b.perimeter_at_s, b.mhs, **b.zoned)
        return spread.ensemble_arrival(b.tile, seeds, b.wxf, now_s,
                                       b.spread_cfg,
                                       incident_id=inc.incident_id,
                                       perimeter=perimeter,
                                       perimeter_at_s=b.perimeter_at_s,
                                       **b.zoned)

    def _register_forecast(self, inc: FireIncident, ens, near: list,
                           now_s: float, assessments: list, ensembles: dict,
                           clients_seen: dict, etas: dict) -> None:
        """The incident's ensemble and every in-range client's ETA."""
        ens.incident_id = inc.incident_id
        ensembles[inc.incident_id] = ens
        for client in near:
            eta = spread.eta_at(ens, client.loc)
            assessments.append(ThreatAssessment(
                client_id=client.client_id, incident_id=inc.incident_id,
                eta=eta, computed_at_s=now_s))
            clients_seen[client.client_id] = client
            etas[(client.client_id, inc.incident_id)] = eta

    # ------------------------------------- forecast freshness tiers

    def _forecast_tier(self, inc: FireIncident, kept) -> tuple:
        """FORECAST FRESHNESS TIERS (contracts forecast_tiers): (tier,
        limit_s, why) for one incident. THREAT — a client pair of it at
        WATCH or above in the alert states the cycle started from; else
        CONTROLLED — every source reporting at least
        forecast_controlled_containment_pct and no source's acreage above
        the size its kept forecast was made at; else GROWING."""
        cfg = self._cfg
        iid = inc.incident_id
        hot = sorted({cid for (cid, pid), rec in self._tier_states.items()
                      if pid == iid
                      and getattr(rec, "state", rec) in _THREAT_TIER_STATES})
        if hot:
            return ("threat", cfg.forecast_stale_threat_min * 60.0,
                    f"{len(hot)} client(s) at watch or above")
        by_src = (inc.meta or {}).get("containment_by_source")
        if not isinstance(by_src, dict) or not by_src:
            by_src = ({_source_name(inc): inc.containment_pct}
                      if inc.containment_pct is not None else {})
        pcts = [p for p in (_finite_num(v) for v in by_src.values())
                if p is not None]
        if not pcts:
            return ("growing", cfg.forecast_stale_growing_min * 60.0,
                    "containment not reported")
        low = min(pcts)
        if low < cfg.forecast_controlled_containment_pct:
            return ("growing", cfg.forecast_stale_growing_min * 60.0,
                    f"{low:g}% contained")
        grew = []
        if kept is not None:
            for src, a in sorted(_acres_by_source(inc).items()):
                base = kept.acres.get(src)
                if base is not None and a > base + 1e-9 * max(1.0, abs(base)):
                    grew.append(f"{src} {base:,.0f} -> {a:,.0f} ac")
        if grew:
            return ("growing", cfg.forecast_stale_growing_min * 60.0,
                    f"{low:g}% contained but grew ({'; '.join(grew)})")
        return ("controlled", cfg.forecast_stale_controlled_min * 60.0,
                f"{low:g}% contained, not growing")

    def _refresh_pool(self):
        """The ONE background worker kept forecasts refresh on."""
        if self._refresher is None:
            from concurrent.futures import ThreadPoolExecutor
            self._refresher = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="wg-forecast-refresh")
        return self._refresher

    def _forecast_post(self, inc: FireIncident, ens, b, perimeter, at_s: float,
                       notes: list):
        """After a kept forecast's spread (in the cycle): the belief's
        after-spread step and the tiered eroded P10 (None when that knob is
        off)."""
        if self._belief_on():
            self._belief_after_spread(
                inc, ens, at_s, b.spread_cfg,
                b.perimeter_at_s if perimeter is not None else at_s,
                perimeter is None, notes)
        if getattr(self._cfg, "corridor_erosion_tiered", False):
            return spread.tiered_eroded_p10(
                ens.p10, b.tile.fuel, self._cfg,
                canopy=getattr(b.tile, "canopy", None))
        return None

    def _collect_refresh(self, inc: FireIncident, kept, notes: list) -> None:
        """A finished background refresh becomes the kept forecast (its
        after-spread steps run here, in the cycle); a failed one is noted and
        the previous forecast stays."""
        fut, meta = kept.pending, kept.pending_meta
        kept.pending = kept.pending_meta = None
        at_s, b, perimeter, fire_key, acres, clients_key = meta
        try:
            ens = fut.result()
        except Exception as exc:
            notes.append(f"incident {inc.incident_id}: forecast refresh "
                         f"failed ({exc}) — the forecast made "
                         f"{(at_s - kept.at_s) / 60.0:.0f} min before it "
                         f"stays")
            self._kept_failures.append((inc.incident_id, "spread", exc))
            return
        kept.eroded = self._forecast_post(inc, ens, b, perimeter, at_s, notes)
        kept.ens = ens
        kept.inputs = SimpleNamespace(wxf=b.wxf, wfield=b.wfield, flag=b.flag)
        kept.at_s = at_s
        kept.fire_key = fire_key
        kept.acres = acres
        kept.clients_key = clients_key

    def _assess_kept(self, inc: FireIncident, now_s: float, near: list,
                     domain, cell_m: float, ign: list, perimeter,
                     perimeter_at_s, provenance_s, seeds: list,
                     assessments: list, ensembles: dict, clients_seen: dict,
                     etas: dict, notes: list, forecasts, extreme_flags,
                     eroded_map, timings, weather_fields, freshness) -> None:
        """FORECAST FRESHNESS TIERS (contracts forecast_tiers): serve the
        incident's kept forecast, refreshing it — (a) in the cycle when there
        is none or its domain no longer fits (the protected homes in range
        changed, or one lies off its grid); (b)/(c) on the background worker
        when the fire's geometry changed or the forecast's age reaches its
        tier's limit less forecast_refresh_margin_min (the previous forecast
        is served until the new one lands). A refresh whose inputs fail is a
        provider error (drained by cycle()); the kept forecast stays served.
        Fills freshness[iid] with tier, limit_s, age_s, computed_at_s,
        refreshing, stale (age past the limit) and why."""
        cfg = self._cfg
        iid = inc.incident_id
        clients_key = (tuple(sorted(c.client_id for c in near)),
                       float(cell_m))
        fire_key = _fire_key(perimeter, provenance_s, seeds)
        kept = self._kept.get(iid)
        if (kept is not None and kept.pending is not None
                and kept.pending.done()):
            self._collect_refresh(inc, kept, notes)
        tier, limit_s, why = self._forecast_tier(inc, kept)
        _tm = {"tier": tier}
        fits = (kept is not None and kept.clients_key == clients_key
                and all(geo.latlon_to_rc(kept.ens.spec, c.loc) is not None
                        for c in near))
        if not fits:
            b = self._forecast_inputs(inc, now_s, domain, cell_m, ign,
                                      perimeter, perimeter_at_s,
                                      provenance_s, notes, _tm)
            ens = self._forecast_spread(inc, b, seeds, perimeter, now_s)
            eroded = self._forecast_post(inc, ens, b, perimeter, now_s, notes)
            _tm["spread_s"] = time.monotonic() - b.t_spread
            _tm["cells"] = b.tile.spec.nrows * b.tile.spec.ncols
            kept = _KeptForecast(
                clients_key, fire_key, now_s,
                SimpleNamespace(wxf=b.wxf, wfield=b.wfield, flag=b.flag),
                ens, eroded, _acres_by_source(inc))
            self._kept[iid] = kept
            _tm["forecast"] = "computed"
        else:
            age = now_s - kept.at_s
            due = []
            if kept.fire_key != fire_key:
                due.append("the fire's geometry changed")
            if age >= limit_s - cfg.forecast_refresh_margin_min * 60.0:
                due.append(f"{age / 60.0:.0f} min old (limit "
                           f"{limit_s / 60.0:g} min)")
            if due and kept.pending is None:
                try:
                    b = self._forecast_inputs(inc, now_s, domain, cell_m, ign,
                                              perimeter, perimeter_at_s,
                                              provenance_s, notes, _tm)
                except _ProviderFailure as pf:
                    self._kept_failures.append((iid, pf.step, pf.cause))
                    _tm["forecast"] = f"kept — refresh failed ({pf.step})"
                else:
                    kept.pending = self._refresh_pool().submit(
                        self._forecast_spread, inc, b, seeds, perimeter, now_s)
                    kept.pending_meta = (now_s, b, perimeter, fire_key,
                                         _acres_by_source(inc), clients_key)
                    _tm["forecast"] = "refreshing (" + "; ".join(due) + ")"
            else:
                _tm["forecast"] = ("refreshing" if kept.pending is not None
                                   else "kept")
        k = kept.inputs
        if k.wfield is not None and weather_fields is not None:
            weather_fields[iid] = k.wfield
        if forecasts is not None:
            forecasts[iid] = k.wxf
        if k.flag is not None and extreme_flags is not None:
            extreme_flags[iid] = k.flag
        if kept.eroded is not None and eroded_map is not None:
            eroded_map[iid] = kept.eroded
        self._register_forecast(inc, kept.ens, near, now_s, assessments,
                                ensembles, clients_seen, etas)
        age = max(0.0, now_s - kept.at_s)
        if freshness is not None:
            freshness[iid] = {
                "tier": tier, "limit_s": limit_s, "age_s": age,
                "computed_at_s": kept.at_s,
                "refreshing": kept.pending is not None,
                "stale": age > limit_s, "why": why}
        _tm["age_s"] = round(age, 1)
        _tm["clients"] = len(near)
        if timings is not None:
            timings.setdefault("incidents", {})[iid] = _tm

    def _local_weather_field(self, inc: FireIncident, wxf, centroid: LatLon,
                             tile, notes: list):
        """SAFETY SEMANTICS 17: the WeatherField of one domain (wgresponse.
        wxfield.build_field: the centroid forecast `wxf` + the global lattice
        nodes on the tile's grid, fetched through the monitor's provider),
        with its notes; None for a SEEDED incident (single-point drill
        weather, SEMANTICS 12f/17d — noted). A failed NODE is inside the
        field (its zone carries `wxf`, noted by name); anything else going
        wrong here is noted and the incident spreads on `wxf` alone (the
        legacy law) — never a raise, never a silent fallback."""
        from wgresponse import wxfield
        if inc.source is IncidentSource.SEEDED:
            notes.append(f"incident {inc.incident_id}: local weather not "
                         f"applied — a seeded (drill) fire keeps its "
                         f"single-point weather (SAFETY SEMANTICS 17d)")
            return None
        try:
            field = wxfield.build_field(self._weather, wxf, centroid,
                                        tile.spec, self._cfg,
                                        self._cfg.horizon_h)
        except Exception as exc:  # degrade loudly to the legacy single point
            notes.append(f"incident {inc.incident_id}: local weather failed "
                         f"({exc}); spread prices the fire-centroid forecast "
                         f"alone (legacy; SAFETY SEMANTICS 17)")
            return None
        notes.extend(wxfield.field_notes(inc.incident_id, field, self._cfg))
        return field

    def _confirmed_perimeter_at_s(self, inc: FireIncident,
                                  provenance_s: float, perimeter_at_s: float,
                                  now_s: float, notes: list) -> float:
        """The seeding time of a perimeter under cfg.perimeter_confirmed_
        anchor (contracts SAFETY SEMANTICS 13e/f). `provenance_s` is the
        polygon's own time (F3), `perimeter_at_s` the legacy seeding time
        (provenance clamped to now_s). Returns min(reported_at_s, now_s)
        when the record's reported size is fresher than the polygon, equals
        the polygon's own area within cfg.perimeter_confirm_tolerance_frac
        either way, and no fused source carries a newer polygon; else the
        legacy perimeter_at_s. Every decision taken on a record fresher than
        its polygon is ONE note in the "incident <id>: ..." format (the
        world summary shows it per incident); a record no fresher than its
        polygon has nothing to confirm and is silent."""
        meta = inc.meta or {}
        rep_at = _finite_num(meta.get("reported_at_s"))
        if rep_at is None or min(rep_at, float(now_s)) <= perimeter_at_s:
            return perimeter_at_s
        age_h = lambda t: max(0.0, float(now_s) - t) / 3600.0  # noqa: E731
        head = (f"incident {inc.incident_id}: polygon "
                f"{age_h(provenance_s):.1f} h old, record "
                f"{age_h(rep_at):.1f} h old — ")
        newest = _finite_num(meta.get("perimeter_newest_at_s"))
        if newest is not None and newest > provenance_s + 1.0:
            notes.append(head + f"a NEWER polygon ({age_h(newest):.1f} h old) "
                         f"exists in another source: seeded at this polygon's "
                         f"own time (legacy; SAFETY SEMANTICS 13f)")
            return perimeter_at_s
        rep = _finite_num(meta.get("reported_acres"))
        poly = _finite_num(meta.get("perimeter_acres"))
        if rep is None or poly is None or poly <= 0.0:
            notes.append(head + "no size to compare (the record's reported "
                         "acres or the polygon's own area is unknown): seeded "
                         "at the polygon's own time (legacy; SAFETY "
                         "SEMANTICS 13f)")
            return perimeter_at_s
        tol = float(self._cfg.perimeter_confirm_tolerance_frac)
        rel = (rep - poly) / poly
        if rel > tol:
            notes.append(head + f"the record reports {rep:,.0f} ac, {rel:+.1%} "
                         f"over the polygon's {poly:,.0f} ac: the polygon "
                         f"lags a GROWING fire — seeded at the polygon's own "
                         f"time (legacy, pessimistic; SAFETY SEMANTICS 13f)")
            return perimeter_at_s
        if rel < -tol:
            notes.append(head + f"the record reports {rep:,.0f} ac, {rel:+.1%} "
                         f"under the polygon's {poly:,.0f} ac: the sizes "
                         f"disagree — seeded at the polygon's own time "
                         f"(legacy; SAFETY SEMANTICS 13f)")
            return perimeter_at_s
        anchored = min(rep_at, float(now_s))
        notes.append(head + f"the record reports {rep:,.0f} ac, the polygon "
                     f"{poly:,.0f} ac ({rel:+.1%}, within {tol:.0%}): the "
                     f"perimeter is CONFIRMED as of the record and seeded "
                     f"then, not {age_h(provenance_s):.1f} h ago "
                     f"(cfg.perimeter_confirmed_anchor; SAFETY SEMANTICS 13e)")
        return anchored

    def _sane_seed_incident(self, inc: FireIncident, now_s: float,
                            notes: list) -> FireIncident:
        """The incident whose perimeter this cycle's spread seeds under
        cfg.feed_sanity (contracts SAFETY SEMANTICS 18b/c; the feed's own
        record is never modified — a replacement is a dataclasses.replace
        copy with its own meta).

          18b  the polygon's own time (meta perimeter_at_s, else the
               record's updated_at_s — the F3 rule) is implausible
               (feedsanity.time_problem): it becomes the record's own time
               (now when that is implausible too); the SEMANTICS 9 clamp and
               the 13e anchor still apply after it.
          18c  the polygon's geometry covers under feed_sanity_collapse_frac
               of its record's reported size (feedsanity.collapse_problem),
               or drops below the remembered polygon beyond noise
               (feedsanity.is_drop): the fire's LAST PLAUSIBLE polygon — its
               rings, own time and perimeter_acres — replaces it while the
               memory holds one (feed_sanity_memory_h); with none the polygon
               is seeded as received.
          else the polygon is plausible and becomes the fire's memory.

        Every finding is one note; a fire newly found implausible this cycle
        also queues an OperatorEvent('feed_sanity') that cycle() emits."""
        cfg = self._cfg
        meta = inc.meta or {}
        iid = inc.incident_id
        head = f"incident {iid}: "
        age_h = lambda t: (float(now_s) - t) / 3600.0  # noqa: E731
        raw_pat = _finite_num(meta.get("perimeter_at_s"))
        provenance_s = (raw_pat if raw_pat is not None and raw_pat > 0
                        else float(inc.updated_at_s))
        new_meta = None
        findings = []
        why_t = feedsanity.time_problem(provenance_s, inc.discovered_at_s,
                                        now_s, cfg)
        if why_t is not None:
            rec_t = _finite_num(inc.updated_at_s)
            if rec_t is None or not rec_t > 0 or feedsanity.time_problem(
                    rec_t, inc.discovered_at_s, now_s, cfg) is not None:
                rec_t = float(now_s)
            new_meta = dict(meta)
            new_meta["perimeter_at_s"] = rec_t
            provenance_s = rec_t
            notes.append(head + f"perimeter time implausible ({why_t}): "
                         f"seeded at the record's own time "
                         f"({age_h(rec_t):.1f} h ago) instead (SAFETY "
                         f"SEMANTICS 18b)")
            findings.append(f"implausible perimeter time ({why_t})")
        geom_acres = (fire_belief_mod.rings_area_m2(inc.perimeter)
                      / feedsanity.ACRE_M2)
        why_s = feedsanity.collapse_problem(geom_acres,
                                            meta.get("reported_acres"), cfg)
        rem = self._perimeter_memory.get(iid, now_s, cfg)
        if why_s is None and rem is not None and feedsanity.is_drop(
                geom_acres * feedsanity.ACRE_M2,
                rem.geom_acres * feedsanity.ACRE_M2, cfg):
            why_s = (f"its {geom_acres:,.1f} ac dropped below the "
                     f"{rem.geom_acres:,.1f} ac polygon seen "
                     f"{age_h(rem.seen_s):.1f} h ago, beyond measurement "
                     f"noise")
        out = inc if new_meta is None else dataclasses.replace(
            inc, meta=new_meta)
        if why_s is None:
            self._perimeter_memory.remember(iid, feedsanity.RememberedPerimeter(
                rings=inc.perimeter, perimeter_at_s=provenance_s,
                perimeter_acres=_finite_num(meta.get("perimeter_acres")),
                geom_acres=geom_acres, seen_s=float(now_s)))
        elif rem is not None:
            m2 = dict(out.meta or {})
            m2["perimeter_at_s"] = rem.perimeter_at_s
            if rem.perimeter_acres is None:
                m2.pop("perimeter_acres", None)
            else:
                m2["perimeter_acres"] = rem.perimeter_acres
            notes.append(head + f"perimeter implausible ({why_s}): seeded the "
                         f"fire's last plausible polygon instead "
                         f"({rem.geom_acres:,.1f} ac, its own time "
                         f"{age_h(rem.perimeter_at_s):.1f} h ago, last seen "
                         f"{age_h(rem.seen_s):.1f} h ago; SAFETY SEMANTICS "
                         f"18c)")
            findings.append(f"implausible perimeter ({why_s}); seeded its "
                            f"last plausible polygon")
            out = dataclasses.replace(out, perimeter=rem.rings, meta=m2)
        else:
            notes.append(head + f"perimeter implausible ({why_s}) and no "
                         f"plausible polygon of this fire is remembered: "
                         f"seeded as received — its forecast may run LATE "
                         f"near the real front (SAFETY SEMANTICS 18c)")
            findings.append(f"implausible perimeter ({why_s}) seeded as "
                            f"received: no plausible polygon remembered")
        if findings:
            self._sanity_now.add(iid)
            if iid not in self._sanity_prev:
                self._sanity_events.append(OperatorEvent(
                    t_s=now_s, kind="feed_sanity",
                    message=(f"incident {inc.name or iid}: "
                             + "; ".join(findings)
                             + " (SAFETY SEMANTICS 18)"),
                    incident_id=iid, detail={"findings": list(findings)}))
        return out

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

    def _offmap_horizons(self, active: list, ensembles: dict, now_s: float,
                         notes: list, status: Optional[dict] = None) -> dict:
        """SAFETY SEMANTICS 15 (cfg.corridor_offraster_per_fire): the per-
        fire off-domain horizon of every domain incident (wgresponse.offmap)
        — one note each — handed to dispatch.plan(offmap=...). INTEGRATOR
        SEAM: another per-fire status table (the spread belief's
        {incident_id: FireStatus}) OR-combines with the feed's via
        offmap.horizons(..., status=that_table). A failure here is a note and
        the flat legacy bound (never a raise out of cycle())."""
        from wgresponse import offmap as offmap_mod
        try:
            table = offmap_mod.horizons(active, ensembles, now_s, self._cfg,
                                        status=status)
        except Exception as exc:  # degrade to the legacy law, loudly
            notes.append(f"off-domain horizons failed ({exc}); every fire "
                         f"keeps the flat {self._cfg.corridor_offraster_ros_mps:g}"
                         f" m/s bound (SAFETY SEMANTICS 15)")
            return {}
        for iid in sorted(table):
            notes.append(offmap_mod.horizon_note(table[iid]))
        return table

    def _maybe_dispatch(self, now_s: float, ensembles: dict,
                        clients_seen: dict, etas: dict,
                        notes: list,
                        forecasts: Optional[dict] = None,
                        eroded_map: Optional[dict] = None,
                        offmap: Optional[dict] = None) -> Optional[object]:
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
            if offmap:
                # SAFETY SEMANTICS 15 (cfg.corridor_offraster_per_fire only):
                # a dispatcher without the kwarg keeps the flat law, loudly.
                try:
                    return self._dispatcher.plan(
                        crews, jobs, dict(ensembles), list(self._safe_zones),
                        self._cfg, now_s, incumbent=self._incumbent,
                        forecasts=dict(forecasts or {}),
                        eroded_rasters=eroded_map, offmap=dict(offmap))
                except TypeError as exc:
                    if "offmap" not in str(exc):
                        raise
                    notes.append("dispatch: this dispatcher takes no per-fire "
                                 "off-domain horizons; every fire keeps the "
                                 "flat bound (SAFETY SEMANTICS 15)")
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
