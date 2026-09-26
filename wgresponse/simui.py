"""wgresponse.simui — SimSession: the interactive-simulator engine harness.

Contract seam (contracts MODULE SEAMS; founder request 2026-07-09;
docs/SIMULATOR_DESIGN.md is the spec of record):

    LOCATIONS                    real presets (five originals, Rancho Santa Fe,
                                 the six Vail->Aspen corridor communities, the
                                 whole-valley Eagle->Vail corridor)
                                 -> SimLocation(name, center,
                                 canonical hazard wind dir_deg, tile path
                                 data/tiles/<key>.wgt, 2-3 safe-zone anchors
                                 (the Eagle->Vail corridor: 6), optional
                                 per-location tile geometry / road-proximity
                                 placement — see SimLocation)
    load_tile(key, ...)          cached ascii tile if present, else a
                                 SyntheticTerrain fallback CLEARLY flagged
                                 (banner 'SYNTHETIC TERRAIN — rehearsal grade')
    wui_cells(tile)              the interface/intermix band (see below)
    place_clients(tile, n, seed) seeded-random distinct WUI cells -> clients
    class SimSession             .configure(location_key, n_clients, n_crews,
                                 seed, cfg_overrides, fire_zone) / .ignite(lat, lon,
                                 intensity, wind_dir_deg=None) / .set_wind /
                                 .set_buffer / .set_slack / .set_speed /
                                 .pause / .resume / .reset / .tick / .state

REAL ENGINE PATHS ONLY (design rule 1): every tick runs spread.ensemble_arrival
(perimeter-seeded), spread.eta_at, alerts.AlertEngine.evaluate,
dispatch.plan(..., forecasts=...) and safety.latest_departure_s; crew
kinematics and the per-tick SAFETY AUDIT reuse sim.py's own machinery
(_CrewSim / ServicedStop / SafetyAudit) — no forked physics, no canned
animation. The audit runs EVERY tick over every serviced stop and its
violation count must stay 0.

Sim-world ground truth: the fire "actually" follows the NOMINAL ensemble
member (members[0], the engine's unperturbed best-estimate run). Each tick
the burned mask is {cells with nominal arrival <= sim_now}; its boundary ring
becomes the incident perimeter. When the world changes (ignite / set_wind —
the wind dial) or the anchor ages toward the horizon, the forecast
RE-ANCHORS from that EXACT burned mask: every consumed cell seeds at sim-now
(spread's ignition-point path, seed_spotting=False — each already threw its
one ember), so nothing that burned un-burns, nothing unburned is stamped
burned, and all new growth runs under the new weather (a mid-run wind turn
then matches one continuous run with the same wind shift, IoU 0.97-1.00).
(Until 2026-09-25 the re-anchor filled the angle-sorted boundary ring as a
perimeter polygon; spot-fire islands and concave burns made that ring self-
intersect, so a wind change dropped up to half of a 3-h-old burn and
stamped unburned wedges — scratch_review/wind_direction_probe.py.)
Ensembles then express forecast uncertainty around that ground truth
exactly as in operations.

ROADS (contracts wgresponse.roads seam; founder UX 2026-07-10): the session
loads the location's RoadGraph ONCE at configure via roads.load_road_graph
(imported LAZILY — the sibling module may land independently; module missing,
no cached graph, or an unreadable graph file -> STRAIGHT-LEG MODE with the
original tier-0 kinematics, noted LOUDLY in state.notes, never silent).
Every planned leg (crew->stop, stop->stop) and every egress leg (stop->safe
zone, including safety withdrawals) resolves through roads.route(...); crew
kinematics follow the returned RoadRoute POLYLINE point-to-point at the
route's implied speed (dist_m/time_s), never the chord. DIVISION OF
AUTHORITY (contracts roads seam): dispatch.plan keeps the calibrated tier-0
travel model (circuity x fire-traffic) and each tick re-plans from ACTUAL
crew positions, so model-vs-road divergence self-corrects cycle to cycle;
the per-tick SAFETY AUDIT judges actual departure times exactly as before.
ROAD-EGRESS DEPARTURE GUARD (the conservative nuance that keeps
violations=0 legitimate): with a road graph present, a deploy may run to
completion only if its projected departure plus the EFFECTIVE egress time —
max(tier-0 model time, actual road-route time) — still reaches the safe
zone by the stop's LIVE latest-depart (current p10 minus the stop's rule
minutes). A projected breach withdraws the crew immediately (cause
'road_egress'): deploy aborted unfinished, immediate road egress, loud
state()['withdrawn'] record + scoreboard count + unserved reason
'withdrawn_unsafe' + note. A fast road route never loosens the decision
below tier-0; a slow one tightens it. Checked at arrival, at every tick of
an in-flight deploy, and via the _recheck_deploy path (which subtracts the
same effective egress in road sessions).

FIRE ZONE (contracts wgresponse.simui FIRE ZONE block; founder request
2026-07-10 — "allow the fire zone to be increased so ignition can be
further from the clients homes"): sessions take fire_zone='standard'
(default) | 'extended'. standard = the ~18x18 km tile data/tiles/<key>.wgt;
extended = the wide-area tile data/tiles/<key>_ext.wgt (~36-48 km, coarser
cells) so ignition can sit tens of km out and the full 72h NOTICE -> WATCH
-> 24h EXTREME arc plays out. CLIENTS ARE ALWAYS PLACED IN THE CENTRAL
~18 km COMMUNITY WINDOW regardless of zone: on extended tiles the WUI
placement mask is restricted to the sub-grid of cells whose CENTERS fall
inside the standard-tile footprint (the location's tile_half_m —
STANDARD_ZONE_HALF_M for every preset with an extended tile — around the
location center, computed from coordinates, never hardcoded indices).
Ignition may be anywhere on the loaded tile; safe zones and crew staging
are unchanged (lat/lon anchors). Extended sessions default cfg.horizon_h
to max(horizon_h, 120.0) UNLESS the caller explicitly set horizon_h in
cfg_overrides (an explicit override always wins — see configure()).
A missing _ext tile is a LOUD state.notes entry + fallback to the standard
tile; state()['fire_zone'] always reflects what ACTUALLY loaded. An
extended session routes on data/roads/<key>_ext.wgr (EXT_ROAD_SUFFIX: the
whole extended tile, tools/fetch_sim_roads.py --extended — before
2026-09-25 it used the standard ~18 km graph, so a crew fleeing past the
core drove a straight chord); when that graph is not cached the session
falls back LOUDLY to the standard graph. A leg endpoint outside graph
coverage falls back to the straight tier-0 leg per the roads seam
(on_roads=False, never silent). Standard sessions never load it. Extended
sessions also default cfg.escape_proactive_lawful_only=True (contracts;
explicit override wins): the wider graph opens proactive-escape routes whose
exposed transit the 10c audit counts.

STATE VISUAL CONTRACT (contracts wgresponse.simui block; founder UX
2026-07-10; "shade the fire" 2026-09-26): the shaded fire is the SIMULATED
ACTUAL FIRE — state.burned = rings (closed [[lat, lon], ...] polylines) of
the union over fires of cells whose NOMINAL arrival <= sim-now (the ground
truth above), growing tick over tick; state.burned_p50 = the old fill (each
fire's P50 <= sim-now rings), kept under an explicit name; state.fire_bands
= {'h1','h5','h24'} rings of (actual burn UNION P50 <= sim-now +1/+5/+24 h)
— the ladder's median projection bounded by the actual fire, recomputed
EVERY tick so they advance with it; state.fire_view = the exact per-cell
picture the page shades (classes, strokes, areas — SimSession._fire_visuals;
the median lags the nominal fire badly at high wind, which is why the fill
moved: docs/SIMULATOR_DESIGN.md "Shading the fire");
crews[i].route_polyline / egress_polyline are
the road-following paths above (with route_on_roads/egress_on_roads flags
and eta_s). The on-roads flags are TRI-STATE: True = road-following leg,
False = a REAL straight-line leg (honest fallback the UI chips), None = no
corresponding polyline exists at all (idle crew at rest) — False is
reserved for actual legs. Rings are traced on a <=120x120 min-pooled copy
of their raster (payload budget), ordered by TRUE BOUNDARY FOLLOWING
(clockwise Moore neighbor-trace per 8-connected boundary component, so
concave fronts render as clean outlines instead of centroid-angle
starbursts) and stride-capped per ring; the old absolute-hour isochrones
are gone from state (the UI renders the bands). ROADS LEGIBILITY (roads
inspection 2026-09-25): crews[i].map_view — display-only, additive — hands
the page the road path driven this tick (moved_path, the marker's tween),
the driven trail, jumps (a reinforcement's off-map arrival), a deploying
crew's queued legs on roads, the road leg a just-relocated crew drives
next, and a one-line reason for every straight spur / chord (see the
section above SimSession.state).

REGIONAL / MUTUAL-AID CREWS (contracts FOUNDER ROUND 5 SEMANTICS 9): the
session takes n_crews (IN-ZONE, staged at local safe zones, available now)
plus n_crews_1h / n_crews_3h out-of-region reinforcements. Each state.crews[i]
carries four extra fields the UI draws the inbound marker + arrival countdown
off:
  region         'in_zone' | 'reg_1h' | 'reg_3h'   (fixed at construction)
  inbound        bool — True exactly while status == 'inbound' (an
                 out-of-region reinforcement that has not yet arrived; its
                 lat/lon is a DETERMINISTIC staging-entry point on the tile
                 EDGE toward its region — reg_1h east edge, reg_3h west edge —
                 fanned evenly when a region has several crews)
  available_at_s unix s the crew becomes available, or null. null until the
                 FIRST ignition arms the mobilization clock; in-zone crews are
                 always null (they are local, never "arriving")
  arrival_eta_s  max(0, available_at_s - sim_now) seconds, or null (null
                 whenever available_at_s is null) — counts down to 0 at arrival
On arrival (sim clock reaches available_at_s) an inbound crew flips to
AVAILABLE at its staging safe zone (lat/lon = the zone) and behaves as an
ordinary dispatch-eligible crew; an INBOUND crew is NEVER in a dispatch plan
and never counts as an idle full-utilization failure. Arrival is a pure
function of (first-ignition time + delay), so scrub replay is byte-identical.

WUI band definition (SIMULATOR_DESIGN.md): developed cells (FBFM40 91/93)
within 3 cells (Chebyshev) of burnable fuel, UNION burnable cells within
1 cell of a developed cell. place_clients draws distinct band cells with
contracts.seeded_rng; values are lognormal (median ~$1.2M, sigma 0.55)
clamped to [$600k, $5M]. If the band has fewer than n cells the remainder
lands on burnable-adjacent cells and a note is recorded (never silent).

SAFETY AUDIT semantics: identical to sim.py's — for every serviced stop,
margin_h = (eta_p10_now - rule_buffer - depart_s)/3600 against the CURRENT
p10 raster of the stop's incident; violation iff finite and < 0. Two
documented refinements for the interactive setting (with an unchanged
config the audit is byte-equal to sim.py's):

  * COMPLETED stops are audited under the buffer/ember minutes IN EFFECT
    WHEN THE STOP WAS ADOPTED (captured per stop and echoed on every audit
    entry as rule_buffer_min/rule_ember_min), so moving the slider after a
    departure demonstrates the new policy without retroactively re-labeling
    finished history.
  * An IN-PROGRESS deploy is NEVER grandfathered (2026-07-09 verifier
    finding, blocking). set_buffer/set_slack re-check the frozen stop's
    projected departure against the NEW rule at the current forecast; the
    stop's audit rule only ever TIGHTENS (max of adoption/new — a live
    loosening never relaxes in-flight work); a projected violation
    WITHDRAWS the crew immediately: the deploy is aborted unfinished (NO
    ServicedStop), the crew turns straight onto its planned egress leg, and
    the withdrawal lands loudly in state()['withdrawn'], the scoreboard
    'withdrawn' count, the client's unserved reason 'withdrawn_unsafe',
    and a session note. While a tightened-rule deploy stays in flight it is
    re-checked EVERY tick against the newest p10 ('eta_drift'), so a
    forecast update can never let it complete past the new rule. Policy
    changes resolve toward safety: withdraw, never silently keep.

DETERMINISM (design rule 3): all randomness flows through contracts.seeded_rng
on explicit seed strings; the sim clock is anchored at a fixed epoch (never
wall-clock); a SimSession given (seed, command script) replays to an
identical state() sequence. Caveat inherited from dispatch.plan: bytes are
guaranteed while the solver finishes inside cfg.solver_budget_s (tiny
scenarios always do; the exact micro-solver covers <= 8 free jobs x <= 4
crews).

Units (contracts law): absolute times unix seconds `_s`; durations hours `_h`
/ minutes `_min`; distances meters `_m`; speeds m/s `_mps`; compass degrees,
wind_dir_deg blowing FROM. Stdlib only.
"""
from __future__ import annotations

import copy
import math
import threading
import weakref
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from wgresponse import dispatch, geo, safety, spread
from wgresponse.alerts import AlertEngine
from wgresponse.contracts import (
    UNREACHED,
    AlertState,
    ClientProperty,
    Crew,
    CrewBeacon,
    CrewStatus,
    CrewPlan,
    DispatchJob,
    DispatchPlan,
    FireIncident,
    GridSpec,
    IncidentSource,
    LatLon,
    PlannedStop,
    ResponseConfig,
    SCHEMA_VERSION,
    SafeZone,
    TerrainTile,
    ThreatAssessment,
    seeded_rng,
    validate_config,
)
from wgresponse.crews import CrewTracker
from wgresponse.fuels import cold_black_minutes, is_burnable
from wgresponse.sim import (
    SafetyAudit,
    ServicedStop,
    _CrewSim,
    _eff_ground_speed_mps,
    _haversine_m,
    _interp,
    jsonable,
)
from wgresponse.terrain import SyntheticTerrain, load_ascii_tile
from wgresponse.weather import MockWeather

SYNTHETIC_BANNER = "SYNTHETIC TERRAIN — rehearsal grade"

#: FBFM40 codes treated as "developed" for the WUI band (NB1 urban, NB3 ag).
DEVELOPED_CODES = (91, 93)

MPH_TO_MPS = 0.44704            # exact (1 mph = 0.44704 m/s by definition)
GUST_FACTOR = 1.3               # design: gusts +30% over sustained

#: Design-doc clock ceiling (SIMULATOR_DESIGN.md: speed 1x-600x). The server
#: /api/sim config path enforces the same bound (SimSessionManager.
#: MAX_SPEED_X); set_speed enforces it at the SimSession seam too so command
#: scripts cannot request arbitrarily coarse multi-cycle jumps.
MAX_SPEED_MULT = 600.0

#: Intensity presets (SIMULATOR_DESIGN.md): sustained wind mph, RH %.
#: Temperatures are NOT pinned by the design doc; the ramp below is a
#: documented choice (hotter with intensity) and part of the scenario law.
INTENSITY_WEATHER = {
    "low":      {"wind_mph": 8.0,  "rh_pct": 35.0, "temp_c": 28.0},
    "moderate": {"wind_mph": 18.0, "rh_pct": 22.0, "temp_c": 31.0},
    "high":     {"wind_mph": 28.0, "rh_pct": 12.0, "temp_c": 35.0},
    "extreme":  {"wind_mph": 40.0, "rh_pct": 6.0,  "temp_c": 39.0},
}

#: Fixed simulation epoch (unix s). Deterministic law: never wall-clock.
T0_S = 1_790_000_000.0

#: PLAN-ON-ROADS session route-cache bound (entries; cleared whole on
#: overflow). ~110 m buckets x (clients^2 + crews x clients per bucket-move)
#: stays well under this in any bench/live session; the cap only guards
#: pathological long-run growth.
_ROAD_PLAN_CACHE_MAX = 50_000

#: SEM-10d escape-path COLD-BLACK window (seconds): a cell the nominal front
#: consumed at least this long before a fleeing crew would pass it counts as
#: OLD BURN — passable (the hazard is the front's flaming residence, minutes
#: in grass/shrub; 45 min is a conservative bound incl. heavy fuels) — so a
#: crew stranded inside a large burn scar can drive OUT through the black
#: (park_city finding: 44 ticks standing in the burn with zero candidates).
#: ROUTE-SELECTION policy only; the 10c audit still reports any crew standing
#: on a front-passed cell, and cells inside the window remain undriveable.
_COLD_BLACK_PASS_S = 45.0 * 60.0

#: Synthetic-fallback tile edge length (km). Real cached tiles are ~18x18 km;
#: the rehearsal-grade fallback defaults smaller to keep the dep-free pure-
#: Python spread loop responsive. Overridable per session (tests use ~6-16).
DEFAULT_SYNTH_SIZE_KM = 12.0

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TILE_DIR = _REPO_ROOT / "data" / "tiles"

#: FIRE ZONE (contracts wgresponse.simui): the two legal zone values and the
#: cached-tile suffix of the extended wide-area tile (tools/fetch_sim_tiles.py
#: --extended writes data/tiles/<key>_ext.wgt + provenance_ext.json).
FIRE_ZONES = ("standard", "extended")
EXT_TILE_SUFFIX = "_ext"

#: Half-extent (meters) of the STANDARD tile footprint — the ~18x18 km
#: community window (tools/fetch_sim_tiles.py HALF_M). On extended tiles the
#: WUI client-placement mask is restricted to the central sub-grid covering
#: this same geographic extent around the location center.
STANDARD_ZONE_HALF_M = 9_000.0

#: Extended sessions must keep a far fire inside the forecast horizon:
#: default cfg.horizon_h floors at this unless the caller explicitly
#: overrides horizon_h (configure() documents the precedence).
EXT_MIN_HORIZON_H = 120.0

_MAX_PERIMETER_VERTICES = 200   # threat.py ring-subsampling convention

#: STATE VISUAL CONTRACT payload budget: rasters are min-pooled to at most
#: this many rows/cols before ring tracing, and each traced ring is stride-
#: capped to _RING_MAX_VERTICES points.
_RING_MAX_DIM = 120
_RING_MAX_VERTICES = 240

#: Projection-band leads (STATE VISUAL CONTRACT): +1 h red, +5 h orange,
#: +24 h yellow — rendering is the UI's business; the keys are the law.
_BAND_LEADS = (("h1", 1.0), ("h5", 5.0), ("h24", 24.0))

#: Sentinel for SimSession(roads_module=...): "resolve the real
#: wgresponse.roads lazily at configure time" (the production default).
_ROADS_AUTO = object()

#: FOUNDER ROUND 3 law 2 (ROADS ADHERENCE): the SimSession default off-network
#: spur ceiling. ResponseConfig.road_spur_max_m defaults to 2000 m, but the
#: real cached graphs place some homes/zones farther from the network (the
#: roads sibling measured a 5544 m max terminal spur on paradise_ca; 6000 m
#: already yields 0% whole-leg chords on all five tiles). 8000 m covers that
#: max with margin so the crews render on the road network with an honest
#: capped spur instead of a whole-leg straight chord. A caller who pins
#: road_spur_max_m in cfg_overrides always wins (configure() precedence).
SIM_ROAD_SPUR_MAX_M = 8000.0

#: PROCESS-WIDE ROAD-GRAPH SHARING (2026-09-25): the public service holds up
#: to WGRESP_SIM_MAX_SESSIONS sessions, and an extended Boulder graph is
#: ~80 MB in RAM — loaded per session that was ~1.3 GB of duplicates on a
#: 2 GB instance. Sessions never mutate a RoadGraph (adjacency is built at
#: construction; the lazy snap index is an idempotent cache), so every
#: session on the same graph FILE shares one object. Weak values: the graph
#: is freed when the last session using it goes away. Keyed by the resolved
#: path + mtime + size, so a refetched file is reloaded.
_SHARED_ROAD_GRAPHS = weakref.WeakValueDictionary()
_SHARED_ROAD_GRAPHS_LOCK = threading.Lock()


def _load_road_graph_shared(mod, key: str):
    """``mod.load_road_graph(key)``, shared across sessions when ``mod`` is
    the real wgresponse.roads module with its own loader (an injected or
    monkeypatched loader is always called directly — tests that fake a
    missing or failing graph are never bypassed). Missing file -> the
    loader's None (the caller's loud note stands); never caches None."""
    loader = getattr(mod, "load_road_graph", None)
    road_file = getattr(mod, "road_file", None)
    if (loader is None or road_file is None
            or getattr(loader, "__module__", None) != "wgresponse.roads"
            or getattr(road_file, "__module__", None) != "wgresponse.roads"):
        return mod.load_road_graph(key)
    try:
        path = Path(road_file(key))
        st = path.stat()
        ck = (str(path.resolve()), st.st_mtime_ns, st.st_size)
    except (OSError, ValueError, TypeError):
        return loader(key)
    with _SHARED_ROAD_GRAPHS_LOCK:
        g = _SHARED_ROAD_GRAPHS.get(ck)
    if g is not None:
        return g
    g = loader(key)
    if g is not None:
        try:
            with _SHARED_ROAD_GRAPHS_LOCK:
                g = _SHARED_ROAD_GRAPHS.setdefault(ck, g)
        except TypeError:              # a graph class without __weakref__
            pass
    return g

#: FOUNDER ROUND 4 law 6 (EMBER / ADJACENCY THREAT): the SimSession default
#: ember-threat radius (meters) threaded into every spread.eta_at call. The
#: surface-spread model can NEVER reach a home sitting on a non-burnable
#: developed (91/93) or water (98) cell, so such homes otherwise get no ETA /
#: alert / dispatch even when fire surrounds them (~23-46% of placed sim
#: clients — the founder's c34). At 200 m (~2-3 cells on the 90 m real tiles)
#: a developed home inherits the earliest arrival among burnable cells within
#: the radius (spread.eta_at fallback_radius_m -> geo.erode_min), so
#: fire-adjacent developed homes DO alert and become dispatch jobs while
#: isolated ones stay unthreatened. Polarity is strictly MORE homes threatened
#: (conservative for an alerting product). ResponseConfig.ember_threat_radius_m
#: defaults to 0.0 (the live product is byte-identical); the SIMULATOR sets
#: this unless a caller pins ember_threat_radius_m in cfg_overrides
#: (configure() precedence, exactly like the extended-zone horizon floor and
#: the road-spur ceiling).
SIM_EMBER_THREAT_RADIUS_M = 200.0
#: CALIBRATED CORRIDOR STACK sim default (founder flip 2026-07-30): escape-
#: target hysteresis threshold (minutes). See the configure() block.
SIM_ESCAPE_HYSTERESIS_MIN = 20.0
#: SEM-10e sim default (moderate-wind census attribution 2026-08-02):
#: proactive pre-pocket escape lead (minutes). 120 fires ~8 ticks ahead of
#: the imminent flag — on the measured paradise 8000/6000 m traces that is
#: 5-7 ticks BEFORE the last clean-at-arrival road out of the SW pocket
#: burns over (the flag itself came 2-3 ticks AFTER). See configure() block.
SIM_ESCAPE_PROACTIVE_LEAD_MIN = 120.0
#: DETERMINISTIC SOLVER BUDGET (contracts solver_budget_evals, fix program
#: 2026-09-11): consult count that lets every measured simulator plan
#: COMPLETE (largest natural spend measured 2,008 consults ~2.3 s on the
#: 100-client RSF scenario; scratch_review/w9_budget_calib.py) so plans
#: replay byte for byte regardless of machine load; the 10x wall-clock
#: runaway guard still bounds a pathological case.
SIM_SOLVER_BUDGET_EVALS = 20_000

#: ROADS LEGIBILITY (roads inspection 2026-09-25, founder "there are times
#: that the response team does not use roads";
#: docs/review/roads_inspection_2026-09-25.md). Every crew carries a
#: DISPLAY-ONLY state()['crews'][i]['map_view'] block — the engine never
#: reads it, every pre-existing state key is unchanged. The road path a crew
#: drove during a tick is Douglas-Peucker simplified at MAP_PATH_EPS_M
#: (well under a map pixel at street zoom) and capped at MAP_PATH_MAX_PTS
#: vertices (the tolerance doubles until it fits). The driven trail keeps up
#: to MAP_TRAIL_TICKS ticks (the crews[i].track window) within
#: MAP_TRAIL_MAX_PTS vertices per crew: each tick's path is simplified on
#: MAP_TRAIL_EPS_LADDER — the last MAP_TRAIL_FRESH_TICKS ticks at the first
#: rung, older ticks at the coarsest rung that fits — and when even that
#: does not fit, the OLDEST ticks are dropped: the trail gets shorter, never
#: straighter. Planned queued-stop legs are simplified at MAP_QUEUE_EPS_M.
#: Coordinates are rounded to 1e-6 deg, like every other state polyline.
MAP_PATH_EPS_M = 8.0
MAP_PATH_MAX_PTS = 160
MAP_TRAIL_TICKS = 30
MAP_TRAIL_EPS_LADDER = (20.0, 40.0, 80.0)
MAP_TRAIL_FRESH_TICKS = 4
MAP_TRAIL_MAX_PTS = 400
MAP_QUEUE_EPS_M = 15.0

#: FIRE ZONE road coverage: an EXTENDED session loads the road graph cached
#: as data/roads/<key>_ext.wgr (tools/fetch_sim_roads.py --extended: the
#: whole ~48 km extended tile) and falls back LOUDLY to the standard ~18 km
#: graph when it is missing. Standard sessions never touch it.
EXT_ROAD_SUFFIX = "_ext"


def _dp_keep(xy: list, eps_m: float) -> list:
    """Douglas-Peucker: sorted indices of the vertices of planar polyline
    `xy` (metres) kept at tolerance eps_m. Iterative (no recursion limit);
    the two endpoints are always kept. Deterministic."""
    n = len(xy)
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ax, ay = xy[i]
        bx, by = xy[j]
        vx, vy = bx - ax, by - ay
        l2 = vx * vx + vy * vy
        best, bi = -1.0, -1
        for k in range(i + 1, j):
            px, py = xy[k]
            if l2 <= 0.0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / l2))
                d = math.hypot(px - (ax + t * vx), py - (ay + t * vy))
            if d > best:
                best, bi = d, k
        if bi >= 0 and best > eps_m:
            keep[bi] = True
            stack.append((i, bi))
            stack.append((bi, j))
    return [k for k in range(n) if keep[k]]


def _simplify_ll(points, eps_m: float = MAP_PATH_EPS_M,
                 max_pts: int = MAP_PATH_MAX_PTS) -> list:
    """[[lat, lon], ...] (rounded 1e-6 deg, consecutive duplicates dropped)
    of a LatLon / [lat, lon] sequence, Douglas-Peucker simplified at eps_m
    metres; the tolerance doubles until at most max_pts vertices remain
    (endpoints always kept). A display helper for the ROADS LEGIBILITY
    map_view — never used by the engine."""
    pts = []
    for p in points:
        q = ((round(p.lat, 6), round(p.lon, 6)) if isinstance(p, LatLon)
             else (round(float(p[0]), 6), round(float(p[1]), 6)))
        if not pts or pts[-1] != q:
            pts.append(q)
    if len(pts) <= 2:
        return [list(q) for q in pts]
    lat0, lon0 = pts[0]
    kx = math.cos(math.radians(lat0)) * geo.M_PER_DEG_LAT
    xy = [((q[1] - lon0) * kx, (q[0] - lat0) * geo.M_PER_DEG_LAT) for q in pts]
    eps = max(0.0, float(eps_m))
    keep = _dp_keep(xy, eps)
    while len(keep) > max(2, int(max_pts)) and eps < 1e7:
        eps = eps * 2.0 if eps > 0.0 else 1.0
        keep = _dp_keep(xy, eps)
    return [list(pts[k]) for k in keep]


def _edge_kinds_from_route(rr, n_edges: int) -> list:
    """Per-EDGE render kind list ('road'|'spur'|'chord'), length n_edges,
    derived from a RoadRoute's SPUR METADATA (contracts wgresponse.roads
    seam). Each RouteSegment of k points contributes k-1 identical edge kinds
    (mirrors RoadRoute.spur_mask). Falls back to the whole-leg on_roads flag
    when the route carries no segments (the fake-roads test double / any
    legacy route object): a clean on-road leg is all 'road', an off-network
    straight leg is all 'chord'. Length-guarded — padded with 'spur' (dashed,
    the conservative choice for an unclassified straight connector) or
    truncated — so the returned list ALWAYS aligns 1:1 with the rendered
    polyline's edges."""
    kinds: list = []
    segs = getattr(rr, "segments", None)
    if segs:
        for seg in segs:
            pts = getattr(seg, "points", ())
            kinds.extend([seg.kind] * max(0, len(pts) - 1))
    if not kinds:
        base = "road" if getattr(rr, "on_roads", False) else "chord"
        kinds = [base] * n_edges
    if len(kinds) < n_edges:
        kinds = kinds + ["spur"] * (n_edges - len(kinds))
    elif len(kinds) > n_edges:
        kinds = kinds[:n_edges]
    return kinds


def _segments_from_kinds(polyline: list, edge_kinds: list) -> list:
    """Group a rendered polyline (list of [lat, lon]) + its per-edge kind list
    into an ordered, gap-free cover of maximal same-kind RouteSegment-shaped
    dicts {points, kind, spur} (contracts SPUR METADATA shape: consecutive
    segments share their boundary vertex, so the flat polyline reconstructs
    exactly). Empty when fewer than 2 points."""
    if len(polyline) < 2 or not edge_kinds:
        return []
    segments = []
    run_kind = edge_kinds[0]
    run_start = 0
    for i in range(1, len(edge_kinds) + 1):
        if i == len(edge_kinds) or edge_kinds[i] != run_kind:
            pts = polyline[run_start:i + 1]           # i+1 vertices span i edges
            segments.append({"points": [list(p) for p in pts],
                             "kind": run_kind, "spur": run_kind != "road"})
            if i < len(edge_kinds):
                run_kind = edge_kinds[i]
                run_start = i
    return segments


def _spur_render(polyline: list, edge_kinds):
    """(spur_mask, segments) for a rendered polyline + its per-edge kinds
    (ROADS ADHERENCE spur metadata), or (None, None) when there is no drawable
    leg. spur_mask[i] is True iff edge polyline[i]->polyline[i+1] is a straight
    off-network part (spur or chord) — the UI's dashed-style key; segments is
    the grouped same-kind cover. Length-guarded so mask/segments always align
    with polyline's edges even if the kind list drifted."""
    n = len(polyline) - 1
    if n < 1 or not edge_kinds:
        return None, None
    k = list(edge_kinds)
    if len(k) < n:
        k = k + ["spur"] * (n - len(k))
    elif len(k) > n:
        k = k[:n]
    return [kind != "road" for kind in k], _segments_from_kinds(polyline, k)

# Display ranking for the per-client "worst alert state" rollup (UI concern
# only — ladder legality lives in contracts/alerts, never here).
_DISPLAY_RANK = {
    AlertState.NONE: 0,
    AlertState.CLEARED: 1,
    AlertState.NOTICE_72H: 2,
    AlertState.WATCH: 3,
    AlertState.EXTREME_24H: 4,
    AlertState.IMPACTED: 5,
}


def _idsort(ids):
    """Deterministic numeric-friendly id ordering: sim-c2 before sim-c10."""
    return sorted(ids, key=lambda s: (len(s), s))


# ---------------------------------------------------------------------------
# FOUNDER ROUND 4 law 8: per-client OUTCOME TAG (a pure projection of tick
# state — no engine behaviour change; state.clients[i].outcome explains WHY
# each home is/isn't protected so the map is never arbitrary).
# ---------------------------------------------------------------------------

#: The dispatch/simui unserved-reason strings that mean a THREATENED home could
#: not be gel-protected before the fire — a correct-triage 'abandoned_unsavable'
#: loss (deadline/slack/corridor/egress gave out, the gel window closed, the
#: fire arrived, or a crew had to withdraw for safety). NOT here, deliberately:
#: 'triaged' (the objective's own count-max deprioritization — its own outcome)
#: and 'capacity'/'dropped_by_optimizer' (transient: feasible-for-someone, just
#: not this cycle -> read as 'pending', not a terminal loss). Vocabulary from
#: dispatch.diagnose() + the simui safety/arrival reasons (docstring SAFETY
#: AUDIT / LAW-6 job pool).
_UNSAVABLE_REASONS = frozenset({
    "infeasible_deadline", "infeasible_slack", "gel_window_closed",
    "no_safe_corridor", "no_safe_egress", "fire_arrived", "withdrawn_unsafe",
})

#: The full outcome vocabulary (contracts FOUNDER ROUND 4 law 8). Listed so the
#: scoreboard rollup carries every tag with a 0 even before it first occurs
#: (a stable breakdown for the UI). Order is display order, not precedence.
#: 'monitoring' (round-4 verifier finding, 2026-07-10) sits between 'pending'
#: and 'not_threatened': a home the forecast DOES project a finite arrival for
#: but that is still beyond the notice horizon (not yet an alert / dispatch
#: job) — its ETA shows on the map, so it must not read "not threatened".
OUTCOME_TAGS = ("saved", "protected_then_impacted", "abandoned_unsavable",
                "triaged", "in_progress", "pending", "monitoring",
                "not_threatened")


def _client_outcome(gelled: bool, impacted: bool, threatened: bool,
                    in_progress: bool, assigned: bool, reason,
                    has_eta: bool = False) -> str:
    """One client's law-8 outcome tag from already-computed tick state.

    PRECEDENCE (contracts law 8): a GELLED home wins over every unserved
    reason (gel + impacted = protected_then_impacted, the intended GraphGel
    outcome; gel + not-impacted = saved). An un-jobbed home splits on whether
    the forecast projects an arrival for it (has_eta = finite eta_p50): a home
    the fire is projected to reach but which is still beyond the notice horizon
    (has_eta, not yet jobbed) is 'monitoring' — its ETA is on the map, so
    tagging it 'not_threatened' would self-contradict (round-4 verifier
    finding); ONLY a home with NO projected arrival (eta_p50 UNREACHED/None) is
    'not_threatened'. Otherwise, for a threatened ungelled home: a crew actively
    en_route/deploying on it is in_progress; the fire having already reached it
    (impacted, no crew on it, no gel) is abandoned_unsavable — fire beat every
    feasible crew — decided BEFORE any stale unserved reason; a live future
    assignment is pending (mirrors state()'s assigned-beats-stale-reason rule);
    a terminal unsavable reason is abandoned_unsavable; the objective's
    deliberate skip is triaged; anything else still jobbed and feasible is
    pending (awaiting a free crew). Pure function -> deterministic; identical
    inputs give an identical tag."""
    if gelled:
        return "protected_then_impacted" if impacted else "saved"
    if not threatened:
        # monitoring (finite projected ETA, beyond the notice horizon so not
        # yet jobbed) vs not_threatened (no projected arrival at all)
        return "monitoring" if has_eta else "not_threatened"
    if in_progress:
        return "in_progress"
    if impacted:
        return "abandoned_unsavable"        # fire beat every feasible crew
    if assigned:
        return "pending"                    # a future stop in a live plan
    if reason in _UNSAVABLE_REASONS:
        return "abandoned_unsavable"
    if reason == "triaged":
        return "triaged"
    return "pending"                        # jobbed + feasible, awaiting a crew


# ---------------------------------------------------------------------------
# The five real locations (SIMULATOR_DESIGN.md geography)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SimLocation:
    """One simulator preset. center per the design table; hazard_wind_dir_deg
    is the canonical hazard wind (meteorological, blowing FROM); tile_path is
    repo-relative; safe_zones are staging/egress anchors at plausible
    town/highway nodes ~5-12 km from center (coords documented inline)."""
    key: str
    name: str
    center: LatLon
    hazard_wind_dir_deg: float
    tile_path: str
    safe_zones: tuple
    #: Optional CITY-LIMITS placement polygon (founder 2026-08-02 "rancho
    #: santa fe needs to be 100 homes within the city limits"): when
    #: non-empty, the seeded WUI client draw is restricted to cells whose
    #: centers fall inside this polygon (geo.point_in_poly). Empty (default)
    #: = whole-tile WUI band, byte-identical for every existing location.
    #: PLANNING-GRADE community boundaries, not surveyed parcels.
    bounds_poly: tuple = ()
    #: PER-LOCATION TILE GEOMETRY (founder 2026-09-25 "expand the area for
    #: Vail so that the entire Eagle to Vail corridor can be modeled at
    #: once"): half-extent and cell size (meters) of this preset's STANDARD
    #: tile as fetched by tools/fetch_sim_tiles.py (the TILE_GEOMETRY rung
    #: that won; tests/test_sim_tiles.py pins the committed tile against
    #: both). Defaults = the ~18x18 km @ 90 m law of every other preset.
    #: tile_half_m also sizes the FIRE ZONE central client window on an
    #: extended tile and the road-fetch bbox (tools/fetch_sim_roads.py) — at
    #: the default both are byte-identical to the pre-field behaviour.
    tile_half_m: float = STANDARD_ZONE_HALF_M
    tile_cell_m: float = 90.0
    #: FIRE ZONE: False = NO extended (_ext) tile BY DESIGN — the standard
    #: tile already spans far ignitions (tools/fetch_sim_tiles NO_EXTENDED).
    #: A session requesting 'extended' still falls back LOUDLY to the
    #: standard tile; the note just states the real reason.
    extended_zone: bool = True
    #: ROAD-PROXIMITY PLACEMENT (meters): > 0 restricts the seeded WUI client
    #: draw to band cells whose centers lie within this distance of a road-
    #: graph node — homes with a driveway, not LANDFIRE 'developed' cells on
    #: the ski mountains (lift stations, on-mountain restaurants: 25% of the
    #: corridor grid's Vail homes sat > 2 km from any road, program §3.7).
    #: 0.0 (default) = the whole WUI band, byte-identical for every existing
    #: preset. No road graph loaded -> whole-band fallback, noted LOUDLY.
    #: A different field from bounds_poly (a road rule, not a boundary).
    place_near_road_m: float = 0.0


#: ROAD-PROXIMITY PLACEMENT of the six Vail -> Aspen corridor community
#: presets (roads inspection 2026-09-25): the same 300 m driveway rule as
#: the whole-valley eagle_vail_co and the program's `_road` grid arms. The
#: five originals and rancho_santa_fe_ca stay on the whole WUI band
#: (place_near_road_m = 0.0, byte-identical).
CORRIDOR_PLACE_NEAR_ROAD_M = 300.0


LOCATIONS = {
    # Santa Ana alignment: NE-ENE downslope wind through the canyons.
    "malibu_ca": SimLocation(
        key="malibu_ca", name="Malibu / Santa Monica Mtns, CA",
        center=LatLon(34.055, -118.72), hazard_wind_dir_deg=50.0,
        tile_path="data/tiles/malibu_ca.wgt",
        safe_zones=(
            # Zuma Beach lot / PCH west node, ~10.9 km W of center
            SafeZone("malibu_ca-z1", LatLon(34.021, -118.831), "Zuma Beach / PCH west"),
            # Carbon Beach / PCH east node, ~6.4 km ESE
            SafeZone("malibu_ca-z2", LatLon(34.035, -118.655), "Carbon Beach / PCH east"),
            # US-101 x Las Virgenes (Calabasas), ~10.2 km N
            SafeZone("malibu_ca-z3", LatLon(34.146, -118.705), "US-101 / Las Virgenes"),
        )),
    # Camp Fire geography: Jarbo Gap NE foehn wind.
    "paradise_ca": SimLocation(
        key="paradise_ca", name="Paradise / Magalia, CA",
        center=LatLon(39.775, -121.60), hazard_wind_dir_deg=45.0,
        tile_path="data/tiles/paradise_ca.wgt",
        safe_zones=(
            # Skyway toward Chico, SW descent node, ~11.5 km SW
            SafeZone("paradise_ca-z1", LatLon(39.71, -121.705), "Skyway / Chico SW node"),
            # Neal Rd x Hwy 99, ~10.2 km S
            SafeZone("paradise_ca-z2", LatLon(39.685, -121.62), "Neal Rd / Hwy 99"),
            # Hwy 70 x Pentz Rd, ~10.8 km SE
            SafeZone("paradise_ca-z3", LatLon(39.70, -121.52), "Hwy 70 / Pentz Rd"),
        )),
    # Tubbs corridor: Diablo NE wind-driven grass/oak.
    "santa_rosa_ca": SimLocation(
        key="santa_rosa_ca", name="Santa Rosa NE / Mark West, CA",
        center=LatLon(38.50, -122.65), hazard_wind_dir_deg=45.0,
        tile_path="data/tiles/santa_rosa_ca.wgt",
        safe_zones=(
            # US-101 / downtown Santa Rosa, ~8.8 km SW
            SafeZone("santa_rosa_ca-z1", LatLon(38.44, -122.715), "US-101 / Santa Rosa"),
            # Hwy 12 x Calistoga Rd, ~8.0 km S
            SafeZone("santa_rosa_ca-z2", LatLon(38.44, -122.60), "Hwy 12 / Calistoga Rd"),
            # US-101 / Windsor, ~11.0 km NW
            SafeZone("santa_rosa_ca-z3", LatLon(38.53, -122.77), "US-101 / Windsor"),
        )),
    # Marshall Fire: violent W downslope wind onto grass WUI.
    "boulder_co": SimLocation(
        key="boulder_co", name="Boulder / Superior, CO",
        center=LatLon(39.955, -105.16), hazard_wind_dir_deg=270.0,
        tile_path="data/tiles/boulder_co.wgt",
        safe_zones=(
            # US-36 SE Boulder node, ~10.8 km NW of center
            SafeZone("boulder_co-z1", LatLon(40.015, -105.26), "US-36 / Boulder SE"),
            # US-287 / Lafayette, ~7.5 km NE
            SafeZone("boulder_co-z2", LatLon(39.995, -105.09), "US-287 / Lafayette"),
            # US-36 / Broomfield, ~8.6 km SE
            SafeZone("boulder_co-z3", LatLon(39.92, -105.07), "US-36 / Broomfield"),
        )),
    # Witch Creek (2007) corridor: NE Santa Ana through Del Dios / San
    # Pasqual onto estate-scale WUI (eucalyptus/chaparral, large parcels).
    # Safe zones sit COASTWARD (downwind-safe for the NE hazard wind, the
    # real evacuation direction). PLANNING-GRADE anchors — plausible highway
    # /arterial nodes, not field-verified staging areas.
    "rancho_santa_fe_ca": SimLocation(
        key="rancho_santa_fe_ca", name="Rancho Santa Fe / Covenant, CA",
        center=LatLon(33.020, -117.200), hazard_wind_dir_deg=60.0,
        tile_path="data/tiles/rancho_santa_fe_ca.wgt",
        safe_zones=(
            # I-5 x Via de la Valle (Del Mar), ~6.2 km SW
            SafeZone("rancho_santa_fe_ca-z1", LatLon(32.977, -117.244),
                     "I-5 / Via de la Valle"),
            # Encinitas Blvd x El Camino Real, ~6.4 km NW
            SafeZone("rancho_santa_fe_ca-z2", LatLon(33.047, -117.261),
                     "Encinitas Blvd / El Camino Real"),
            # Del Mar Heights Rd x El Camino Real, ~8.4 km SSW
            SafeZone("rancho_santa_fe_ca-z3", LatLon(32.950, -117.235),
                     "Del Mar Heights / El Camino Real"),
        ),
        # RSF proper (planning-grade 92067 core: the Covenant + village +
        # RSF Farms + Fairbanks Ranch edge; excludes Encinitas/Olivenhain
        # north, 4S/Rancho Bernardo east, Del Mar Heights south, Solana
        # Beach west). Clients seed ONLY inside this boundary.
        bounds_poly=(
            LatLon(33.048, -117.228), LatLon(33.048, -117.185),
            LatLon(33.037, -117.160), LatLon(33.008, -117.152),
            LatLon(32.984, -117.168), LatLon(32.980, -117.208),
            LatLon(32.996, -117.230), LatLon(33.025, -117.235),
        )),
    # Gambel-oak/aspen mountain WUI; prefrontal SW flow is the hazard wind.
    "park_city_ut": SimLocation(
        key="park_city_ut", name="Park City / Summit Park, UT",
        center=LatLon(40.72, -111.55), hazard_wind_dir_deg=225.0,
        tile_path="data/tiles/park_city_ut.wgt",
        safe_zones=(
            # Kimball Junction / I-80, ~6.3 km E
            SafeZone("park_city_ut-z1", LatLon(40.724, -111.476), "Kimball Junction / I-80"),
            # Park City Old Town / SR-224, ~9.3 km SE
            SafeZone("park_city_ut-z2", LatLon(40.646, -111.498), "Park City Old Town"),
            # Jeremy Ranch / I-80 west node, ~9.9 km NW
            SafeZone("park_city_ut-z3", LatLon(40.757, -111.657), "Jeremy Ranch / I-80 west"),
        )),
    # ---------------------------------------------------------------------
    # VAIL -> ASPEN CORRIDOR (founder 2026-09-23 "build the response program
    # from Vail to Aspen"): the PD Mountain Resorts territory served from the
    # Eagle, CO forward base -- six community tiles along I-70 (Eagle Valley)
    # and SH-82 (Roaring Fork Valley). Hazard winds are the western-slope
    # dry-cold-front / prefrontal SW-WSW flow behind every analog fire named
    # below (Storm King 1994, Coal Seam 2002, Lake Christine 2018, Grizzly
    # Creek 2020, Sylvan 2021): the E-W Eagle Valley tiles use 240 (WSW,
    # channelled along the valley), the Roaring Fork tiles 225 (SW). Safe
    # zones are PLANNING-GRADE highway / arterial nodes (interchanges, SH-82
    # junctions, park-and-rides), NOT field-verified staging areas -- the
    # HOA_40_PLAYBOOK ground-verification gate applies before any dispatch.
    # No city-limits polygon: the mountain WUI band IS the towns. Program of
    # record: docs/vail_aspen_response_program.md. Clients seed only on WUI
    # cells within 300 m of a road node (roads inspection 2026-09-25): the
    # resort tiles' LANDFIRE 'developed' cells include ski-mountain lift
    # stations with no road — crews drove multi-km straight spurs up the
    # mountain to them — and the program's headline grid already uses this
    # road placement (§3, §3.7).
    # ---------------------------------------------------------------------
    "vail_co": SimLocation(
        key="vail_co", name="Vail / East Vail / Minturn, CO",
        center=LatLon(39.635, -106.360), hazard_wind_dir_deg=240.0,
        tile_path="data/tiles/vail_co.wgt",
        safe_zones=(
            # I-70 exit 180 / East Vail (Bighorn Rd), ~6.3 km E
            SafeZone("vail_co-z1", LatLon(39.6305, -106.2870),
                     "I-70 exit 180 / East Vail"),
            # I-70 exit 171 / Dowd Junction (US-24), ~7.0 km WSW
            SafeZone("vail_co-z2", LatLon(39.6150, -106.4370),
                     "I-70 exit 171 / Dowd Junction"),
            # Minturn, US-24 / Main St, ~8.1 km SW
            SafeZone("vail_co-z3", LatLon(39.5865, -106.4305),
                     "Minturn / US-24"),
        ),
        place_near_road_m=CORRIDOR_PLACE_NEAR_ROAD_M),
    "beaver_creek_co": SimLocation(
        key="beaver_creek_co", name="Beaver Creek / Avon / Edwards, CO",
        center=LatLon(39.625, -106.560), hazard_wind_dir_deg=240.0,
        tile_path="data/tiles/beaver_creek_co.wgt",
        safe_zones=(
            # I-70 exit 167 / Avon Rd, ~3.4 km NE
            SafeZone("beaver_creek_co-z1", LatLon(39.6365, -106.5230),
                     "I-70 exit 167 / Avon"),
            # I-70 exit 163 / Edwards Access Rd, ~4.2 km NW
            SafeZone("beaver_creek_co-z2", LatLon(39.6495, -106.5985),
                     "I-70 exit 163 / Edwards"),
            # I-70 exit 169 / Eagle-Vail (US-6), ~7.6 km E
            SafeZone("beaver_creek_co-z3", LatLon(39.6210, -106.4715),
                     "I-70 exit 169 / Eagle-Vail"),
        ),
        place_near_road_m=CORRIDOR_PLACE_NEAR_ROAD_M),
    "eagle_co": SimLocation(
        key="eagle_co", name="Eagle / Gypsum, CO (corridor base)",
        center=LatLon(39.650, -106.890), hazard_wind_dir_deg=240.0,
        tile_path="data/tiles/eagle_co.wgt",
        safe_zones=(
            # I-70 exit 147 / Eby Creek Rd (Eagle), ~5.4 km E -- the
            # corridor base node (PD Mountain Resorts HQ, Eagle)
            SafeZone("eagle_co-z1", LatLon(39.6605, -106.8285),
                     "I-70 exit 147 / Eagle (corridor base)"),
            # I-70 exit 140 / Gypsum, Cooley Mesa Rd (airport), ~4.5 km W
            SafeZone("eagle_co-z2", LatLon(39.6465, -106.9425),
                     "I-70 exit 140 / Gypsum, airport"),
            # Brush Creek Rd / Eagle Ranch, ~6.1 km SE
            SafeZone("eagle_co-z3", LatLon(39.6215, -106.8295),
                     "Brush Creek Rd / Eagle Ranch"),
        ),
        place_near_road_m=CORRIDOR_PLACE_NEAR_ROAD_M),
    "glenwood_springs_co": SimLocation(
        key="glenwood_springs_co", name="Glenwood Springs, CO",
        center=LatLon(39.545, -107.320), hazard_wind_dir_deg=240.0,
        tile_path="data/tiles/glenwood_springs_co.wgt",
        safe_zones=(
            # I-70 exit 114 / West Glenwood (Mel Ray Rd), ~3.3 km NW
            SafeZone("glenwood_springs_co-z1", LatLon(39.5595, -107.3530),
                     "I-70 exit 114 / West Glenwood"),
            # I-70 exit 119 / No Name, ~3.9 km NE
            SafeZone("glenwood_springs_co-z2", LatLon(39.5665, -107.2835),
                     "I-70 exit 119 / No Name"),
            # SH-82 / CR-154 Cattle Creek, ~9.2 km SSE
            SafeZone("glenwood_springs_co-z3", LatLon(39.4695, -107.2765),
                     "SH-82 / Cattle Creek"),
        ),
        place_near_road_m=CORRIDOR_PLACE_NEAR_ROAD_M),
    "basalt_carbondale_co": SimLocation(
        key="basalt_carbondale_co", name="Basalt / El Jebel / Carbondale, CO",
        center=LatLon(39.395, -107.125), hazard_wind_dir_deg=225.0,
        tile_path="data/tiles/basalt_carbondale_co.wgt",
        safe_zones=(
            # SH-82 x SH-133 (Carbondale), ~7.7 km W
            SafeZone("basalt_carbondale_co-z1", LatLon(39.4105, -107.2135),
                     "SH-82 / SH-133 Carbondale"),
            # SH-82 x Basalt Ave (Basalt), ~8.5 km ESE
            SafeZone("basalt_carbondale_co-z2", LatLon(39.3705, -107.0315),
                     "SH-82 / Basalt Ave"),
            # SH-82 x Willits Ln, ~4.7 km ESE
            SafeZone("basalt_carbondale_co-z3", LatLon(39.3835, -107.0725),
                     "SH-82 / Willits Ln"),
        ),
        place_near_road_m=CORRIDOR_PLACE_NEAR_ROAD_M),
    "aspen_co": SimLocation(
        key="aspen_co", name="Aspen / Snowmass Village, CO",
        center=LatLon(39.210, -106.870), hazard_wind_dir_deg=225.0,
        tile_path="data/tiles/aspen_co.wgt",
        safe_zones=(
            # SH-82 x Brush Creek Rd intercept lot, ~4.5 km N
            SafeZone("aspen_co-z1", LatLon(39.2485, -106.8845),
                     "SH-82 / Brush Creek intercept lot"),
            # Aspen, Main St x Mill St (SH-82), ~4.9 km ESE
            SafeZone("aspen_co-z2", LatLon(39.1905, -106.8195),
                     "Aspen Main St / Mill St"),
            # Snowmass Village, Town Park / Brush Creek Rd, ~5.8 km W
            SafeZone("aspen_co-z3", LatLon(39.2125, -106.9370),
                     "Snowmass Village Town Park"),
        ),
        place_near_road_m=CORRIDOR_PLACE_NEAR_ROAD_M),
    # ---------------------------------------------------------------------
    # EAGLE -> VAIL CORRIDOR (founder 2026-09-25 "expand the area for Vail
    # so that the entire Eagle to Vail corridor can be modeled at once"):
    # ONE ~60x60 km tile @ 200 m (300x300) over the whole I-70 Eagle Valley,
    # Gypsum / Eagle County airport (W edge -106.970) to East Vail (E edge
    # -106.270), so a fire near Eagle and crews based at the exit-147 base
    # play against homes in Edwards, Avon, Beaver Creek and Vail in ONE
    # session. The per-community presets above (vail_co, beaver_creek_co,
    # eagle_co) are untouched. Six PLANNING-GRADE I-70 interchange anchors,
    # the Eagle base FIRST (in-zone crew 1 stages there; crews round-robin
    # west to east). Hazard wind 240 as on every Eagle Valley tile. Clients
    # seed only on WUI cells within 300 m of a road node (the resort tiles'
    # 'developed' ski-mountain cells are not homes; program §3.7). No
    # extended tile by design: the standard tile already reaches ~30 km.
    # ---------------------------------------------------------------------
    "eagle_vail_co": SimLocation(
        key="eagle_vail_co", name="Eagle → Vail corridor, CO",
        center=LatLon(39.635, -106.620), hazard_wind_dir_deg=240.0,
        tile_path="data/tiles/eagle_vail_co.wgt",
        safe_zones=(
            # I-70 exit 147 / Eby Creek Rd (Eagle) -- the corridor base
            # (PD Mountain Resorts HQ), ~18.1 km W
            SafeZone("eagle_vail_co-z1", LatLon(39.6605, -106.8285),
                     "I-70 exit 147 / Eagle (corridor base)"),
            # I-70 exit 140 / Gypsum, Cooley Mesa Rd (airport), ~27.6 km W
            SafeZone("eagle_vail_co-z2", LatLon(39.6465, -106.9425),
                     "I-70 exit 140 / Gypsum, airport"),
            # I-70 exit 163 / Edwards Access Rd, ~2.4 km NE
            SafeZone("eagle_vail_co-z3", LatLon(39.6495, -106.5985),
                     "I-70 exit 163 / Edwards"),
            # I-70 exit 167 / Avon Rd, ~8.3 km E
            SafeZone("eagle_vail_co-z4", LatLon(39.6365, -106.5230),
                     "I-70 exit 167 / Avon"),
            # I-70 exit 171 / Dowd Junction (US-24), ~15.8 km E
            SafeZone("eagle_vail_co-z5", LatLon(39.6150, -106.4370),
                     "I-70 exit 171 / Dowd Junction"),
            # I-70 exit 180 / East Vail (Bighorn Rd), ~28.5 km E
            SafeZone("eagle_vail_co-z6", LatLon(39.6305, -106.2870),
                     "I-70 exit 180 / East Vail"),
        ),
        tile_half_m=30_000.0, tile_cell_m=200.0, extended_zone=False,
        place_near_road_m=300.0),
}


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------

def tile_file(key: str, tile_dir=None, fire_zone: str = "standard") -> Path:
    """Absolute path of the cached ascii tile for `key` (data/tiles/<key>.wgt
    under the repo root unless tile_dir overrides — tests point this at a tmp
    dir so logic tests never depend on the concurrently-fetched real tiles).
    fire_zone='extended' names the wide-area tile <key>_ext.wgt instead."""
    if key not in LOCATIONS:
        raise ValueError(f"unknown location {key!r}; known: {sorted(LOCATIONS)}")
    if fire_zone not in FIRE_ZONES:
        raise ValueError(f"unknown fire_zone {fire_zone!r}; "
                         f"known: {list(FIRE_ZONES)}")
    base = Path(tile_dir) if tile_dir is not None else DEFAULT_TILE_DIR
    suffix = EXT_TILE_SUFFIX if fire_zone == "extended" else ""
    return base / f"{key}{suffix}.wgt"


def _synthetic_tile(key: str, cell_m: float, size_km: float) -> TerrainTile:
    """Deterministic rehearsal-grade fallback tile centered on the location.

    SyntheticTerrain 'mixed' fuel patches (GR2/GS2/SH5/TL3) with a seeded
    scatter of small FBFM40-91 (urban) hamlet blocks stamped on top so the
    WUI interface band exists (a fallback with no developed cells would leave
    place_clients nothing to bite on). Same (key, cell_m, size_km) always
    yields byte-identical tiles."""
    loc = LOCATIONS[key]
    bbox = geo.bbox_around([loc.center], pad_m=size_km * 1000.0 / 2.0)
    tile = SyntheticTerrain(seed=f"simui|{key}", kind="mixed").tile(bbox, cell_m)
    spec = tile.spec
    rng = seeded_rng("simui", key, "hamlets")
    n_hamlets = max(3, min(14, (spec.nrows * spec.ncols) // 120))
    for _ in range(n_hamlets):
        r0 = rng.randrange(spec.nrows)
        c0 = rng.randrange(spec.ncols)
        h = rng.randint(2, 4)
        w = rng.randint(2, 5)
        for r in range(r0, min(spec.nrows, r0 + h)):
            for c in range(c0, min(spec.ncols, c0 + w)):
                tile.fuel.set(r, c, 91.0)
    tile.source = f"synthetic:simui:{key}"
    return tile


def load_tile(key: str, cell_m: float = 90.0, tile_dir=None,
              synth_size_km: float = DEFAULT_SYNTH_SIZE_KM):
    """(TerrainTile, synthetic_fallback: bool) for a location key.

    The cached ascii tile data/tiles/<key>.wgt is used verbatim when present
    (its own grid/cell size governs; cell_m is ignored). When missing, a
    SyntheticTerrain fallback at cell_m over a synth_size_km square is built
    and the returned flag is True — SimSession surfaces it as the loud
    state banner SYNTHETIC_BANNER (never silent, per the design doc)."""
    path = tile_file(key, tile_dir)
    if path.exists():
        return load_ascii_tile(str(path)), False
    return _synthetic_tile(key, cell_m, synth_size_km), True


def load_zone_tile(key: str, fire_zone: str = "standard", cell_m: float = 90.0,
                   tile_dir=None, synth_size_km: float = DEFAULT_SYNTH_SIZE_KM,
                   notes: Optional[list] = None):
    """(TerrainTile, synthetic_fallback, effective_zone) for a location+zone.

    standard: exactly load_tile(). extended: loads the wide-area cached tile
    data/tiles/<key>_ext.wgt when present (effective_zone='extended'); a
    MISSING extended tile appends a LOUD note to `notes` and falls back to
    the standard-zone path (effective_zone='standard' — the caller's state
    must reflect what actually loaded, per the contracts FIRE ZONE block).
    There is deliberately NO synthetic extended fallback: a rehearsal-grade
    tile at wide-area scale would demo nothing real."""
    if fire_zone not in FIRE_ZONES:
        raise ValueError(f"unknown fire_zone {fire_zone!r}; "
                         f"known: {list(FIRE_ZONES)}")
    if fire_zone == "extended":
        path = tile_file(key, tile_dir, fire_zone="extended")
        if path.exists():
            return load_ascii_tile(str(path)), False, "extended"
        loc = LOCATIONS[key]
        if notes is not None and not getattr(loc, "extended_zone", True):
            # NO extended product BY DESIGN (SimLocation.extended_zone):
            # same loud fallback, honest reason — no refetch hint.
            notes.append(
                f"FIRE ZONE: {key} has NO extended tile by design "
                f"({path.name} is never fetched) — its standard tile already "
                f"spans ~{2.0 * loc.tile_half_m / 1000.0:.0f} km; falling "
                f"back to the STANDARD tile")
        elif notes is not None:
            notes.append(
                f"FIRE ZONE: extended tile {path.name} is not cached — "
                f"falling back to the STANDARD ~18 km tile (fetch with "
                f"py -3 tools/fetch_sim_tiles.py --extended)")
    tile, synthetic = load_tile(key, cell_m=cell_m, tile_dir=tile_dir,
                                synth_size_km=synth_size_km)
    return tile, synthetic, "standard"


def central_window_bbox(center: LatLon,
                        half_m: float = STANDARD_ZONE_HALF_M) -> "BBox":
    """The ~(2*half_m)-wide geographic window around `center` — the
    standard-tile footprint used to restrict WUI client placement on
    extended tiles. Computed FROM COORDINATES (geo.bbox_around, the same
    meter->degree law the tile fetch uses), never from grid indices."""
    return geo.bbox_around([center], pad_m=half_m)


# ---------------------------------------------------------------------------
# WUI band + client placement
# ---------------------------------------------------------------------------

def wui_cells(tile: TerrainTile) -> list:
    """Sorted (row, col) cells of the WUI interface/intermix band.

    Band = developed cells (FBFM40 91/93) within 3 cells of burnable fuel,
    UNION burnable cells within 1 cell of a developed cell (Chebyshev
    distances on the tile grid) — SIMULATOR_DESIGN.md's placement law."""
    spec = tile.spec
    nr, nc = spec.nrows, spec.ncols
    codes = [int(round(v)) for v in tile.fuel.data]
    burn = [is_burnable(v) for v in codes]
    dev = [v in DEVELOPED_CODES for v in codes]

    def _window_any(flags, r, c, k):
        for rr in range(max(0, r - k), min(nr, r + k + 1)):
            base = rr * nc
            for cc in range(max(0, c - k), min(nc, c + k + 1)):
                if flags[base + cc]:
                    return True
        return False

    out = set()
    for r in range(nr):
        base = r * nc
        for c in range(nc):
            i = base + c
            if dev[i] and _window_any(burn, r, c, 3):
                out.add((r, c))
            elif burn[i] and _window_any(dev, r, c, 1):
                out.add((r, c))
    return sorted(out)


def _burnable_adjacent_cells(tile: TerrainTile) -> list:
    """Sorted (row, col) cells within 1 cell (Chebyshev, incl. self) of
    burnable fuel — the documented overflow pool for place_clients."""
    spec = tile.spec
    nr, nc = spec.nrows, spec.ncols
    burn = [is_burnable(int(round(v))) for v in tile.fuel.data]
    out = []
    for r in range(nr):
        for c in range(nc):
            hit = False
            for rr in range(max(0, r - 1), min(nr, r + 2)):
                base = rr * nc
                for cc in range(max(0, c - 1), min(nc, c + 2)):
                    if burn[base + cc]:
                        hit = True
                        break
                if hit:
                    break
            if hit:
                out.append((r, c))
    return out


def place_clients(tile: TerrainTile, n: int, seed: object,
                  notes: Optional[list] = None, window=None,
                  bounds_poly=(), road_graph=None,
                  near_road_m: float = 0.0) -> list:
    """n ClientProperty records on seeded-random DISTINCT WUI cells.

    client_id sim-c<k> (k = 1..n, placement order); loc = cell center;
    value_usd lognormal with median ~$1.2M (sigma 0.55) clamped to
    [$600k, $5M]; priority 1.0; graphgel_enrolled True. When the WUI band
    holds fewer than n cells the remainder is drawn from burnable-adjacent
    cells and a note is appended to `notes` (and never silently ignored).
    `window` (a contracts.BBox or None) restricts BOTH pools to cells whose
    CENTERS fall inside it — the FIRE ZONE central community window on
    extended tiles; None (the default, every standard session) is
    byte-identical to the pre-fire-zone behavior.
    ROAD-PROXIMITY PLACEMENT (SimLocation.place_near_road_m): near_road_m >
    0 together with a `road_graph` (wgresponse.roads.RoadGraph) restricts
    BOTH pools to cells whose centers lie within near_road_m of a graph
    node (RoadGraph.nearest_node) and notes the candidate count; the default
    (0.0 / no graph) leaves the pools untouched — byte-identical. The same
    seeded draw law applies to whatever pool survives.
    Deterministic: same (tile, n, seed, window, bounds_poly, road_graph,
    near_road_m) -> identical list."""
    if n < 1:
        raise ValueError("n must be >= 1")
    near_road = road_graph is not None and near_road_m > 0.0

    def _in_window(rc):
        p = geo.rc_to_latlon(tile.spec, rc[0], rc[1])
        if window is not None and not window.contains(p):
            return False
        # CITY-LIMITS polygon (SimLocation.bounds_poly): empty = whole band,
        # byte-identical legacy; non-empty restricts the draw (and the
        # burnable-adjacent fallback below, which uses this same predicate).
        if bounds_poly and not geo.point_in_poly(p, bounds_poly):
            return False
        # ROAD-PROXIMITY rule: off (default) = whole band, byte-identical.
        if near_road and road_graph.nearest_node(p, max_m=near_road_m) is None:
            return False
        return True

    rng = seeded_rng("simui-clients", str(seed))
    pool = [rc for rc in wui_cells(tile) if _in_window(rc)]
    if bounds_poly and notes is not None:
        notes.append(f"client placement bounded to the location's city-limits "
                     f"polygon ({len(pool)} candidate WUI cells)")
    if near_road and notes is not None:
        notes.append(f"client placement restricted to WUI cells within "
                     f"{near_road_m:g} m of a road node ({len(pool)} candidate "
                     f"WUI cells) — homes with a driveway, not roadless "
                     f"'developed' cells")
    chosen = rng.sample(pool, min(n, len(pool)))
    if len(chosen) < n:
        taken = set(chosen)
        overflow_pool = [rc for rc in _burnable_adjacent_cells(tile)
                         if rc not in taken and _in_window(rc)]
        extra = rng.sample(overflow_pool, min(n - len(chosen), len(overflow_pool)))
        if notes is not None:
            notes.append(
                f"WUI band has only {len(pool)} cells; placed {len(extra)} of "
                f"{n} clients on burnable-adjacent fallback cells")
        chosen = chosen + extra
    if len(chosen) < n:
        raise ValueError(
            f"tile too small to place {n} clients "
            f"({len(chosen)} placeable cells)")
    mu = math.log(1_200_000.0)
    clients = []
    for k, (r, c) in enumerate(chosen, 1):
        value = min(5_000_000.0, max(600_000.0, rng.lognormvariate(mu, 0.55)))
        clients.append(ClientProperty(
            client_id=f"sim-c{k}",
            loc=geo.rc_to_latlon(tile.spec, r, c),
            name=f"Sim client {k}",
            value_usd=round(value, 2),
            graphgel_enrolled=True,
            priority_weight=1.0,
        ))
    return clients


# ---------------------------------------------------------------------------
# Fire rings (STATE VISUAL CONTRACT: burned + fire_bands)
# ---------------------------------------------------------------------------

def _pool_min(raster, max_dim: int = _RING_MAX_DIM):
    """(GridSpec, values) of `raster` MIN-pooled to <= max_dim per side.

    Min = EARLIEST arrival in each k x k block: a block counts as reached-by-t
    iff ANY member cell is — the conservative visual union — and min-pooling
    commutes with thresholding, so nested thresholds trace nested rings.
    k == 1 returns the raster's own spec/data (no copy cost on small grids)."""
    return _pool_min_data(raster.spec, raster.data, max_dim)


def _pool_min_data(spec, data, max_dim: int = _RING_MAX_DIM):
    """_pool_min on a bare (GridSpec, row-major values) pair — the fire
    view's union-over-fires fields have no Raster of their own."""
    k = max(1, math.ceil(max(spec.nrows, spec.ncols) / max_dim))
    if k == 1:
        return spec, data
    nr2 = (spec.nrows + k - 1) // k
    nc2 = (spec.ncols + k - 1) // k
    out = [UNREACHED] * (nr2 * nc2)
    nc = spec.ncols
    for r in range(spec.nrows):
        base = r * nc
        base2 = (r // k) * nc2
        for c in range(nc):
            v = data[base + c]
            j = base2 + c // k
            if v < out[j]:
                out[j] = v
    spec2 = GridSpec(north_lat=spec.north_lat, west_lon=spec.west_lon,
                     cell_m=spec.cell_m * k, nrows=nr2, ncols=nc2)
    return spec2, out


#: Clockwise (north-up) Moore neighborhood: N, NE, E, SE, S, SW, W, NW as
#: (dr, dc) on the contracts grid (row 0 = north). Consecutive entries are
#: Chebyshev-adjacent, which _trace_boundary's backtrack bookkeeping relies on.
_TRACE_DIRS = ((-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1),
               (0, -1), (-1, -1))
_TRACE_DIR_INDEX = {d: i for i, d in enumerate(_TRACE_DIRS)}


def _trace_boundary(cells) -> list:
    """Ordered CLOSED walk over one 8-connected boundary-cell set — true
    boundary FOLLOWING (clockwise Moore / radial neighbor-trace), replacing
    the old centroid-angle sort that starburst-zigzagged on concave fronts
    (h5-band finding, 2026-07-10).

    cells: set[(r, c)], 8-connected, len >= 2. Start at the WESTMOST-then-
    NORTHMOST cell (its west neighbor is guaranteed background); repeatedly
    scan the 8 neighbors clockwise starting just past the backtrack cell and
    step to the first member found, carrying the last-examined background
    cell as the new backtrack. The walk ends when a (cell, backtrack) state
    repeats — determinism makes the repeat a closed cycle, returned from its
    first occurrence (consecutive cells are 8-adjacent; the last cell is
    8-adjacent to the first; thin spurs are traced out-and-back rather than
    crossed). Bounded by the state count 8*len(cells), so it always
    terminates. Deterministic: min() start, fixed direction table."""
    start = min(cells, key=lambda rc: (rc[1], rc[0]))
    p = start
    b = (start[0], start[1] - 1)          # west neighbor: background
    walk = [p]
    seen = {(p, b): 0}
    for _ in range(8 * len(cells) + 16):
        bd = _TRACE_DIR_INDEX[(b[0] - p[0], b[1] - p[1])]
        nxt = None
        for k in range(1, 9):
            di = (bd + k) % 8
            d = _TRACE_DIRS[di]
            cand = (p[0] + d[0], p[1] + d[1])
            if cand in cells:
                nxt = cand
                pd = _TRACE_DIRS[(di - 1) % 8]   # last background examined
                b = (p[0] + pd[0], p[1] + pd[1])
                break
        if nxt is None:
            break                          # isolated cell (callers filter)
        p = nxt
        key = (p, b)
        if key in seen:
            i = seen[key]
            return walk[i:]                # the closed cycle
        seen[key] = len(walk)
        walk.append(p)
    return walk


def _threshold_rings(spec, vals, thr_s: float) -> list:
    """Closed rings ([[lat, lon], ..., first-point-again]) around the mask
    {cells with vals <= thr_s}: boundary cells (a 4-neighbor beyond thr or
    off-grid) are split into 8-connected components — one ring per blob —
    each ordered by TRUE BOUNDARY FOLLOWING (_trace_boundary's clockwise
    Moore neighbor-trace, so concave fronts trace as clean non-crossing
    outlines) and stride-capped to _RING_MAX_VERTICES. Coarse by design (the
    UI paints; the engine judges); deterministic (row-major component
    discovery, deterministic trace). Components under 3 cells (a too-young
    fire) trace nothing."""
    nr, nc = spec.nrows, spec.ncols
    bset = set()
    for r in range(nr):
        base = r * nc
        for c in range(nc):
            i = base + c
            if vals[i] > thr_s:
                continue
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                rr, cc = r + dr, c + dc
                if (not (0 <= rr < nr and 0 <= cc < nc)
                        or vals[rr * nc + cc] > thr_s):
                    bset.add(i)
                    break
    rings = []
    seen = set()
    for start in sorted(bset):                    # row-major, deterministic
        if start in seen:
            continue
        comp = []
        stack = [start]
        seen.add(start)
        while stack:
            j = stack.pop()
            comp.append(j)
            jr, jc = divmod(j, nc)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    rr, cc = jr + dr, jc + dc
                    if 0 <= rr < nr and 0 <= cc < nc:
                        jj = rr * nc + cc
                        if jj in bset and jj not in seen:
                            seen.add(jj)
                            stack.append(jj)
        if len(comp) < 3:
            continue
        walk = _trace_boundary({divmod(j, nc) for j in comp})
        pts = [geo.rc_to_latlon(spec, r, c) for (r, c) in walk]
        if len(pts) < 3:
            continue
        if len(pts) > _RING_MAX_VERTICES:
            stride = math.ceil(len(pts) / _RING_MAX_VERTICES)
            pts = pts[::stride]
        ring = [[round(p.lat, 5), round(p.lon, 5)] for p in pts]
        ring.append(list(ring[0]))
        rings.append(ring)
    return rings


# ---------------------------------------------------------------------------
# Fire view (STATE VISUAL CONTRACT: the shaded fire + truth-bounded bands;
# founder "shade the fire", 2026-09-26)
# ---------------------------------------------------------------------------

#: state.fire_view display classes (code = index). burned + burning = the
#: SIMULATED ACTUAL FIRE — the union over fires of cells whose NOMINAL-member
#: arrival is <= sim-now, the engine's ground truth (the mask the crews are
#: audited against and every re-anchor seeds from), spot-fire islands
#: included; burning = the part of it still inside its fuel's cold-black
#: (flaming) window. h1/h5/h24 = the ladder's MEDIAN (P50) projection at
#: sim-now +1/+5/+24 h, BOUNDED by the actual fire: never on a burned cell,
#: and a cell the median says has already burned but the nominal front has
#: not reached is drawn in h1 ("overdue per the forecast").
FIRE_VIEW_CLASSES = ("none", "burned", "burning", "h1", "h5", "h24")
_FV_BURNED, _FV_BURNING, _FV_H1, _FV_H5, _FV_H24 = 1, 2, 3, 4, 5

#: fire-view strokes are EXACT cell-edge contours on the grid-corner lattice
#: (row 0 = the north edge, col 0 = the west edge; cell (r, c) spans rows
#: r..r+1, cols c..c+1), so they sit on the raster's own cell edges at every
#: zoom. Each stroke is [row, col, code]: the start corner plus a run-length
#: CHAIN CODE of unit steps — 'E3S2W' = 3 east, 2 south, 1 west (a count of 1
#: is omitted); a closed loop ends on its start corner. fire_view_corners()
#: decodes one. A closed band-front loop enclosing fewer cells
#: than this (a lone non-burnable cell or a speck of band), or a hole in the
#: fire that small (an unburned speck), is not stroked — the raster still
#: paints it; the fire's outline keeps every island however small (each
#: spot fire is the truth).
_FV_MIN_LOOP_CELLS = 9

#: unit steps on the corner lattice, clockwise: E, S, W, N as (drow, dcol)
_FV_DIRS = ((0, 1), (1, 0), (0, -1), (-1, 0))
_FV_DIR_CHARS = "ESWN"


def fire_view_corners(stroke) -> list:
    """Decode one state.fire_view stroke [row, col, chain code] into its
    corner points [(row, col), ...] on the grid-corner lattice (see
    _FV_MIN_LOOP_CELLS' block for the format). lat = north_lat - row *
    cell_m / m_per_deg_lat and lon = west_lon + col * cell_m /
    (m_per_deg_lat * cos(lat)) place a corner on the map (fire_view.grid)."""
    r, c, code = int(stroke[0]), int(stroke[1]), stroke[2]
    pts = [(r, c)]
    i = 0
    while i < len(code):
        dr, dc = _FV_DIRS[_FV_DIR_CHARS.index(code[i])]
        j = i + 1
        while j < len(code) and code[j].isdigit():
            j += 1
        n = int(code[i + 1:j]) if j > i + 1 else 1
        r += dr * n
        c += dc * n
        pts.append((r, c))
        i = j
    return pts


def _edge_contours(edges) -> list:
    """Directed unit cell edges [((row, col), dir)] — each with its inside
    cell on the RIGHT of travel — linked into chain-coded strokes on the
    corner lattice: open chains (a band front that stops where the boundary
    continues along the fire) start at corners with no incoming edge; the
    rest close into loops, each started at a convex corner (out-degree 1).
    At a saddle corner the walk turns RIGHT first (then straight, then
    left), which keeps diagonal-touching cells as separate loops. Collinear
    steps merge into one run. Returns [([row, col, code], closed,
    area_cells)] — area > 0 for an outer boundary, < 0 for a hole, 0 for an
    open chain. Deterministic."""
    out = {}
    indeg = {}
    for s, d in edges:
        out.setdefault(s, []).append(d)
        e = (s[0] + _FV_DIRS[d][0], s[1] + _FV_DIRS[d][1])
        indeg[e] = indeg.get(e, 0) + 1
    for ds in out.values():
        ds.sort()
    deg0 = {v: len(ds) for v, ds in out.items()}

    def walk(start):
        pts = [start]
        dirs = []
        v = start
        din = None
        while True:
            ds = out.get(v)
            if not ds:
                break
            d = ds[0]
            if din is not None and len(ds) > 1:
                for turn in (1, 0, 3):            # right, straight, left
                    if (din + turn) % 4 in ds:
                        d = (din + turn) % 4
                        break
            ds.remove(d)
            v = (v[0] + _FV_DIRS[d][0], v[1] + _FV_DIRS[d][1])
            pts.append(v)
            dirs.append(d)
            din = d
        closed = len(pts) > 2 and pts[-1] == pts[0]
        if closed and dirs[0] == dirs[-1]:
            # start on a corner, not mid-run, so the runs below are clean
            k = next((i for i in range(1, len(dirs))
                      if dirs[i] != dirs[i - 1]), 0)
            dirs = dirs[k:] + dirs[:k]
            pts = pts[k:-1] + pts[:k] + [pts[k]]
        code = []
        area = 0.0
        r0, c0 = pts[0]
        i = 0
        while i < len(dirs):
            j = i
            while j < len(dirs) and dirs[j] == dirs[i]:
                j += 1
            n = j - i
            code.append(_FV_DIR_CHARS[dirs[i]] + (str(n) if n > 1 else ""))
            r1 = r0 + _FV_DIRS[dirs[i]][0] * n
            c1 = c0 + _FV_DIRS[dirs[i]][1] * n
            area += c0 * r1 - c1 * r0                  # shoelace, x=col y=row
            r0, c0 = r1, c1
            i = j
        return ([pts[0][0], pts[0][1], "".join(code)], closed,
                0.5 * area if closed else 0.0)

    res = []
    for v in sorted(out):                          # open chains first
        if indeg.get(v, 0) == 0:
            while out[v]:
                res.append(walk(v))
    for v in sorted(out):                          # then the loops
        if deg0[v] == 1 and out[v]:
            res.append(walk(v))
    for v in sorted(out):                          # defensive leftovers
        while out[v]:
            res.append(walk(v))
    return res


# ---------------------------------------------------------------------------
# Crew kinematics (sim.py's _CrewSim + live-replan revocation)
# ---------------------------------------------------------------------------

class _SimCrew(_CrewSim):
    """sim.py's schedule-following crew plus (a) revoke_unstarted() so a
    config change (safety slider) can cancel not-yet-started stops, and
    (b) ROAD-FOLLOWING legs (module docstring ROADS section): every travel
    and egress leg is resolved through the session's route_fn (the
    wgresponse.roads seam) and followed POINT-TO-POINT along the returned
    polyline at the route's implied speed (dist_m / time_s). route_fn
    returning None (no graph loaded / roads disabled) reproduces the
    original straight tier-0 leg byte-for-byte. Deploy dwell, ServicedStop
    bookkeeping and the mode machine are identical to _CrewSim.

    road_guard=True (a road graph is present) arms the ROAD-EGRESS DEPARTURE
    GUARD: at arrival the projected departure (deploy end) must satisfy
        projected + max(tier-0 egress, road egress) <= live latest-depart
    (latest_fn, the same p10-minus-rule law the audit enforces). CONSERVATIVE
    by construction — the max() means a fast road route never loosens the
    decision below the calibrated tier-0 model, while a slow road route
    forces an earlier departure. A breach aborts the deploy unfinished and
    turns the crew onto its ROAD egress; the session drains
    pending_withdrawals into the loud state()['withdrawn'] channel."""

    def __init__(self, crew, speed_mps: float, route_fn=None, latest_fn=None,
                 road_guard: bool = False):
        super().__init__(crew, speed_mps)
        self._route_fn = route_fn        # (a, b) -> RoadRoute | None
        self._latest_fn = latest_fn      # (stop dict) -> live latest-depart s
        self._road_guard = bool(road_guard)
        self._leg = None                 # active leg (see _begin_leg)
        self._egress_plan = None         # CURRENT stop's escape route
        self.pending_withdrawals = []    # road-guard aborts, session-drained
        # ROADS LEGIBILITY (display only): every position this crew occupied
        # during the CURRENT advance() call, in driving order — the exact
        # road path it drove this tick (state map_view.moved_path). Pure
        # bookkeeping: never read by the kinematics.
        self.driven = []

    # -- legs (roads seam) ---------------------------------------------------

    def _begin_leg(self, kind: str, target) -> None:
        """Resolve the (pos -> target) leg via the roads seam. None from
        route_fn = the original straight tier-0 leg at the effective ground
        speed. pts always ends exactly at `target` (nearest-node snap gaps
        are driven through, never teleported).

        FOUNDER ROUND 3 law 2 (ROADS ADHERENCE): edge_kind carries the
        per-edge render kind ('road'/'spur'/'chord') aligned 1:1 with the
        RENDERED polyline edges [pos->pts[0], pts[0]->pts[1], ...], derived
        from the RoadRoute's SPUR METADATA. _move_leg pops it in lockstep with
        pts so state() can hand the UI a spur mask / segments that match the
        crew's REMAINING path exactly (road solid, spur/chord dashed)."""
        rr = self._route_fn(self.pos, target) if self._route_fn else None
        if rr is None:
            self._leg = {"kind": kind, "target": target, "pts": [target],
                         "speed": self.speed, "on_roads": False,
                         "edge_kind": ["chord"],
                         # display-only (ROADS LEGIBILITY map_view.*_why)
                         "origin": self.pos, "note": None}
            return
        pts = list(rr.polyline)
        if pts and pts[0] == self.pos:
            pts = pts[1:]                      # no zero-length lead segment
        if not pts or pts[-1] != target:
            pts.append(target)
        speed = self.speed
        if rr.time_s and rr.time_s > 0.0 and rr.dist_m and rr.dist_m > 0.0:
            speed = rr.dist_m / rr.time_s      # the route's implied speed
        self._leg = {"kind": kind, "target": target, "pts": pts,
                     "speed": speed, "on_roads": bool(rr.on_roads),
                     "edge_kind": _edge_kinds_from_route(rr, len(pts)),
                     # display-only (ROADS LEGIBILITY map_view.*_why): where
                     # the leg began and the roads seam's chord reason
                     "origin": self.pos, "note": getattr(rr, "note", None)}

    def _move_leg(self, t: float, t_end_s: float):
        """Advance along the active leg point-to-point; (t_new, arrived).
        The position always lies ON the polyline (waypoint or interpolated
        on the current segment), never on the chord."""
        leg = self._leg
        speed = max(leg["speed"], 0.1)
        pts = leg["pts"]
        kinds = leg.get("edge_kind")
        while pts:
            seg = _haversine_m(self.pos, pts[0])
            budget = (t_end_s - t) * speed
            if budget < seg:
                if seg > 1e-9:
                    self.pos = _interp(self.pos, pts[0], budget / seg)
                    self.driven.append(self.pos)
                return t_end_s, False
            t += seg / speed
            self.pos = pts.pop(0)
            self.driven.append(self.pos)
            if kinds:                          # consume this edge's render kind
                kinds.pop(0)
        return t, True

    def _leg_remaining_m(self) -> float:
        if self._leg is None:
            return 0.0
        d = 0.0
        prev = self.pos
        for p in self._leg["pts"]:
            d += _haversine_m(prev, p)
            prev = p
        return d

    def _refresh_egress_plan(self) -> None:
        """Precompute the emergency escape route for the CURRENT stop (stop
        location -> adopted safe zone): state()'s crews[i].egress_polyline
        while the crew is committed/on-site, and the max(tier-0, road)
        effective egress time the road departure guard keys off. None when
        the crew holds no committed stop or no egress zone."""
        if not self.itinerary or self.egress_loc is None:
            self._egress_plan = None
            return
        origin = self.itinerary[0]["loc"]
        tier0_s = _haversine_m(origin, self.egress_loc) / max(self.speed, 0.1)
        rr = (self._route_fn(origin, self.egress_loc)
              if self._route_fn else None)
        if rr is None:
            self._egress_plan = {"points": [origin, self.egress_loc],
                                 "on_roads": False, "time_s": tier0_s,
                                 "tier0_s": tier0_s, "eff_s": tier0_s,
                                 "edge_kind": ["chord"], "note": None}
            return
        pts = list(rr.polyline)
        if not pts or pts[0] != origin:
            pts.insert(0, origin)
        if pts[-1] != self.egress_loc:
            pts.append(self.egress_loc)
        road_s = rr.time_s if (rr.time_s and rr.time_s > 0.0) else tier0_s
        self._egress_plan = {
            "points": pts, "on_roads": bool(rr.on_roads), "time_s": road_s,
            "tier0_s": tier0_s,
            # per-edge render kinds aligned to `points` (ROADS ADHERENCE spur
            # metadata) so the UI dashes the spur/chord and solids the road.
            "edge_kind": _edge_kinds_from_route(rr, len(pts) - 1),
            # CONSERVATIVE (module docstring ROADS nuance): the effective
            # egress time never drops below the calibrated tier-0 model.
            "eff_s": max(tier0_s, road_s),
            # display-only: the roads seam's chord reason (map_view)
            "note": getattr(rr, "note", None)}

    def road_eff_egress_s(self) -> float:
        """The departure guard's effective egress seconds (0 when no plan)."""
        return self._egress_plan["eff_s"] if self._egress_plan else 0.0

    def _road_depart_check(self, t_now_s: float) -> bool:
        """ROAD-EGRESS DEPARTURE GUARD (class docstring): True iff the crew
        withdrew. Only meaningful mid-deploy with road_guard armed."""
        if (not self._road_guard or self._latest_fn is None
                or self._mode != "deploy" or not self.itinerary):
            return False
        stop = self.itinerary[0]
        deadline = self._latest_fn(stop) - self.road_eff_egress_s()
        if math.isinf(deadline) or self._deploy_end_s <= deadline:
            return False
        dropped = [s["client_id"] for s in self.itinerary]
        self.pending_withdrawals.append({
            "client_id": stop["client_id"],
            "incident_id": stop["incident_id"], "at_s": t_now_s,
            "projected_depart_s": self._deploy_end_s,
            "latest_depart_s": deadline, "dropped": dropped,
            "road_eff_egress_min": self.road_eff_egress_s() / 60.0})
        self.itinerary = []
        self._egress_plan = None
        if self.egress_loc is not None:
            self._mode = "egress"
            self.status = CrewStatus.EGRESSING
            self._begin_leg("egress", self.egress_loc)
        else:
            self._mode = "idle"
            self.status = CrewStatus.AVAILABLE
            self._leg = None
        return True

    def drain_withdrawals(self) -> list:
        out = self.pending_withdrawals
        self.pending_withdrawals = []
        return out

    # -- schedule management ---------------------------------------------------

    def adopt(self, stops: list, egress_loc, egress_zone_id: str) -> None:
        super().adopt(stops, egress_loc, egress_zone_id)
        self._leg = None                 # legs re-resolve from actual pos
        self._refresh_egress_plan()

    def revoke_unstarted(self):
        if self._mode == "deploy":
            dropped = [s["client_id"] for s in self.itinerary[1:]]
            del self.itinerary[1:]
            return dropped
        dropped = [s["client_id"] for s in self.itinerary]
        if dropped:
            self.itinerary = []
            self.egress_loc = None
            self.egress_zone_id = ""
            self._egress_plan = None
            if self._mode == "travel":
                self._mode = "idle"
                self.status = CrewStatus.AVAILABLE
                self._leg = None
        return dropped

    def abort_deploy(self):
        """SAFETY WITHDRAWAL: abandon the IN-PROGRESS gel deploy right now.

        The current stop is dropped UNFINISHED — no ServicedStop is ever
        recorded for it — along with anything still queued behind it, and
        the crew turns straight onto its planned egress leg (idles in place
        if no egress zone was adopted; adoption always sets one). The egress
        leg resolves through the roads seam like every other leg (a
        withdrawal follows the ROAD egress). Returns the dropped client ids
        (aborted stop first), [] when the crew was not mid-deploy."""
        if self._mode != "deploy" or not self.itinerary:
            return []
        dropped = [s["client_id"] for s in self.itinerary]
        self.itinerary = []
        self._egress_plan = None
        if self.egress_loc is not None:
            self._mode = "egress"
            self.status = CrewStatus.EGRESSING
            self._begin_leg("egress", self.egress_loc)
        else:
            self._mode = "idle"
            self.status = CrewStatus.AVAILABLE
            self._leg = None
        return dropped

    def relocate_to(self, zone_loc, zone_zone_id: str):
        """CONTINUOUS CREW SAFETY (contracts SAFETY SEMANTICS 10a): drop any
        stop AND any current leg and turn onto a FRESH egress leg toward a
        currently-safe zone. The NON-deploying counterpart of abort_deploy (a
        deploying crew withdraws first, via _overrun_withdraw, then relocates):
        an idle / en_route / egressing crew whose cell the fire threatens flees
        immediately. The leg re-resolves through the roads seam from the crew's
        ACTUAL position (self._leg = None), so the escape route is recomputed
        from where the crew is now. Returns the dropped client ids (the
        abandoned itinerary, empty when the crew held no stop)."""
        dropped = [s["client_id"] for s in self.itinerary]
        self.itinerary = []
        self.egress_loc = zone_loc
        self.egress_zone_id = zone_zone_id
        self._egress_plan = None
        self._mode = "egress"
        self.status = CrewStatus.EGRESSING
        self._leg = None                 # re-resolve the egress leg from pos
        return dropped

    def retarget_egress(self, zone_loc, zone_zone_id: str) -> None:
        """DYNAMIC SAFE ZONES (contracts SAFETY SEMANTICS 10b): repoint a
        COMMITTED crew's escape at a currently-safe zone WITHOUT disturbing its
        in-flight deploy — the itinerary and mode are untouched, only the egress
        terminus + precomputed plan move (and the active egress leg, if any,
        re-resolves from the new target next advance). Used when the fire
        overruns the zone a deploying crew adopted, so it never egresses into the
        fire when the gel job completes."""
        self.egress_loc = zone_loc
        self.egress_zone_id = zone_zone_id
        if self._leg is not None and self._leg.get("kind") == "egress":
            self._leg = None             # stale egress leg -> re-resolve
        self._refresh_egress_plan()

    # -- kinematics (sim.py's event loop, legs on roads) -----------------------

    def advance(self, t_start_s: float, t_end_s: float, serviced_out: list) -> None:
        """_CrewSim.advance with ROAD-FOLLOWING travel/egress legs. Deploy
        dwell + ServicedStop bookkeeping are byte-identical to sim.py; with
        route_fn=None every leg degenerates to the original straight tier-0
        leg (same physics as before roads existed)."""
        self.driven = [self.pos]         # ROADS LEGIBILITY: this tick's drive
        t = t_start_s
        guard = 0
        while t < t_end_s and guard < 1000:
            guard += 1
            if self._mode == "idle":
                if self.itinerary:
                    self._mode = "travel"
                    self.status = CrewStatus.EN_ROUTE
                elif self.egress_loc is not None:
                    self._mode = "egress"
                    self.status = CrewStatus.EGRESSING
                else:
                    self._leg = None
                    return
            elif self._mode == "travel":
                target = self.itinerary[0]["loc"]
                if (self._leg is None or self._leg["kind"] != "ingress"
                        or self._leg["target"] != target):
                    self._begin_leg("ingress", target)
                t, arrived = self._move_leg(t, t_end_s)
                if not arrived:
                    continue
                self.pos = target
                self._leg = None
                self._arrive_s = t
                stop = self.itinerary[0]
                service_start = max(t, stop["service_start_s"])
                self._deploy_end_s = service_start + stop["deploy_s"]
                stop["actual_arrive_s"] = t
                stop["actual_service_start_s"] = service_start
                self._mode = "deploy"
                self.status = CrewStatus.DEPLOYING
                self._refresh_egress_plan()
                # ROAD-EGRESS DEPARTURE GUARD at arrival: closes the
                # same-tick hole where an arrival + full deploy would fit
                # inside one coarse step with no session re-check between.
                self._road_depart_check(t)
            elif self._mode == "deploy":
                if self._deploy_end_s <= t_end_s:
                    t = max(t, self._deploy_end_s)
                    stop = self.itinerary.pop(0)
                    serviced_out.append(ServicedStop(
                        crew_id=self.crew.crew_id, client_id=stop["client_id"],
                        incident_id=stop["incident_id"],
                        arrive_s=stop["actual_arrive_s"],
                        service_start_s=stop["actual_service_start_s"],
                        depart_s=t, latest_depart_s=stop["latest_depart_s"],
                        margin_h=(stop["latest_depart_s"] - t) / 3600.0,
                        gel_fresh_frac=stop["gel_fresh_frac"]))
                    self._mode = "idle"
                    self._refresh_egress_plan()
                else:
                    t = t_end_s
            elif self._mode == "egress":
                target = self.egress_loc
                if (self._leg is None or self._leg["kind"] != "egress"
                        or self._leg["target"] != target):
                    self._begin_leg("egress", target)
                t, arrived = self._move_leg(t, t_end_s)
                if not arrived:
                    continue
                self.pos = target
                self._leg = None
                self.egress_loc = None
                self._mode = "idle"
                self.status = CrewStatus.AVAILABLE


@dataclass
class _SimFire:
    """One user-ignited incident. ens is the fire's PERSISTED absolute
    arrival field (EnsembleArrival; members[0] is sim ground truth), anchored
    at anchor_t0_s and recomputed ONLY when the world changes (weather epoch
    bump from ignite/set_wind, or the anchor nearing the forecast horizon) —
    NEVER on a mere clock tick. Re-basing every tick discarded sub-tick front
    progress, so any fire whose per-cell traverse time exceeded the tick
    period froze forever (founder-visible bug, 2026-07-10, twice: point-seed
    phase and ring phase). Burned area and the h1/h5/h24 bands are threshold
    sweeps of this persisted field against sim-now."""
    incident_id: str
    origin: LatLon
    t_ignite_s: float
    intensity: str
    wind_dir_deg: float
    ens: object = None
    wx_epoch: int = -1
    anchor_t0_s: float = 0.0
    # CONTINUOUS CREW SAFETY (contracts SAFETY SEMANTICS 10): the eroded-P10
    # arrival raster (geo.erode_min(ens.p10, corridor_erosion_m) — the exact
    # transform the corridor law uses) memoised on the fire keyed by its CURRENT
    # ensemble object, so the crew-safety sweep + audit recompute it ONLY when
    # the persisted field is (re)built (world change / horizon rebase), never
    # per tick or per crew.
    _eroded_src: object = None
    _eroded_p10: object = None
    # FIRE VIEW bookkeeping (display only, never read by the engine): a
    # re-anchor seeds every burned cell at the anchor time, which would make
    # the whole old burn read "burning now" again. _fv_burn_t keeps each such
    # cell's TRUE nominal arrival from the field it burned in (flat index ->
    # unix s), recorded by SimSession._fire_visuals when it first sees the
    # new field (_fv_seen = the field it last drew).
    _fv_seen: object = None
    _fv_burn_t: object = None


# ---------------------------------------------------------------------------
# SimSession
# ---------------------------------------------------------------------------

class SimSession:
    """Interactive accelerated simulation over the real engine (contract seam).

    Construction == configure(): SimSession(location_key, n_clients, n_crews,
    seed, cfg_overrides=None, fire_zone='standard'). fire_zone (contracts
    FIRE ZONE block) picks the tile: 'standard' = the ~18 km community tile;
    'extended' = the wide-area <key>_ext.wgt tile so ignition can sit tens
    of km from the homes (clients stay in the central community window;
    missing _ext tile -> loud note + standard fallback; state()['fire_zone']
    reflects what actually loaded). Keyword-only test seams (documented
    additions):
    tile_dir (tile lookup dir; default repo data/tiles), synth_size_km
    (synthetic-fallback tile edge) and roads_module — default resolves the
    real wgresponse.roads LAZILY at configure time (the sibling module may
    not have landed; absence -> straight-leg mode, noted loudly); None pins
    the session to straight-leg mode regardless of what is installed (the
    kinematics-pinned logic tests use this, same discipline as tile_dir);
    any other object is used as the roads module (in-test fakes conforming
    to the contracts wgresponse.roads seam). place_near_road_m (HARNESS
    seam, roads inspection 2026-09-25): None (default) = the location's own
    SimLocation.place_near_road_m; a number overrides it for this session
    (0.0 = the whole WUI band) — how scratch_review/corridor_grid.py keeps
    its whole-band arms reproducible now that the corridor presets are
    road-placed. Every command appends to cmd_log — the reproducibility
    script echoed in state()."""

    def __init__(self, location_key: str, n_clients: int, n_crews: int,
                 seed: object, cfg_overrides: Optional[dict] = None,
                 fire_zone: str = "standard", n_crews_1h: int = 0,
                 n_crews_3h: int = 0, *,
                 tile_dir=None, synth_size_km: float = DEFAULT_SYNTH_SIZE_KM,
                 roads_module: object = _ROADS_AUTO,
                 client_locs: Optional[list] = None,
                 crew_locs: Optional[list] = None,
                 place_near_road_m: Optional[float] = None):
        self._tile_dir = tile_dir
        self._synth_size_km = float(synth_size_km)
        self._roads_module_arg = roads_module
        if place_near_road_m is not None and float(place_near_road_m) < 0.0:
            raise ValueError("place_near_road_m must be >= 0")
        self._place_near_road_override = (
            None if place_near_road_m is None else float(place_near_road_m))
        self.configure(location_key, n_clients, n_crews, seed, cfg_overrides,
                       fire_zone=fire_zone, n_crews_1h=n_crews_1h,
                       n_crews_3h=n_crews_3h, client_locs=client_locs,
                       crew_locs=crew_locs)

    # -- lifecycle -----------------------------------------------------------

    def configure(self, location_key: str, n_clients: int, n_crews: int,
                  seed: object, cfg_overrides: Optional[dict] = None,
                  fire_zone: str = "standard", n_crews_1h: int = 0,
                  n_crews_3h: int = 0, client_locs: Optional[list] = None,
                  crew_locs: Optional[list] = None):
        """(Re)build the whole scenario deterministically (contract seam).

        client_locs (ONBOARDING/BENCH seam, 2026-07-12): optional list of
        LatLon — place clients at EXACTLY these coordinates (deterministic
        ids sim-c1..N in input order, flat $1.2M value, priority 1.0) instead
        of the seeded WUI draw; n_clients is then ignored in favor of
        len(client_locs). Off-tile coordinates fail loud. This is how the
        onboarding tool and the interface-geometry benchmarks evaluate REAL
        candidate homes; None (default) = the seeded WUI placement,
        byte-identical.

        fire_zone (contracts FIRE ZONE block): 'standard' | 'extended';
        anything else fails loud. HORIZON PRECEDENCE on extended sessions
        whose extended tile actually loaded: cfg.horizon_h defaults to
        max(cfg.horizon_h, EXT_MIN_HORIZON_H=120.0) so a far fire stays
        inside the forecast horizon — UNLESS 'horizon_h' appears in
        cfg_overrides, in which case the caller's explicit value ALWAYS
        wins (even below 120). A session that requested 'extended' but fell
        back to the standard tile (missing _ext cache: loud note) is a
        standard session and gets NO horizon floor.

        REGIONAL / MUTUAL-AID CREWS (contracts FOUNDER ROUND 5 SEMANTICS 9):
        n_crews is the IN-ZONE count (stages at local safe zones, available
        now, region='in_zone'); n_crews_1h / n_crews_3h are OUT-OF-REGION
        reinforcements that stand by (status INBOUND, region 'reg_1h'/'reg_3h')
        until the FIRST ignition mobilizes them (available_at_s =
        first_ignition_s + cfg.crew_reinforce_1h_min / crew_reinforce_3h_min
        minutes; see ignite()). Both default 0 (byte-identical to a
        no-reinforcement session) and are validated to 0..12; total crews =
        n_crews + n_crews_1h + n_crews_3h. reset() / reroll preserve the tiers
        (they flow through _args)."""
        if location_key not in LOCATIONS:
            raise ValueError(f"unknown location {location_key!r}; "
                             f"known: {sorted(LOCATIONS)}")
        if fire_zone not in FIRE_ZONES:
            raise ValueError(f"unknown fire_zone {fire_zone!r}; "
                             f"known: {list(FIRE_ZONES)}")
        n_clients = int(n_clients)
        n_crews = int(n_crews)
        n_crews_1h = int(n_crews_1h)
        n_crews_3h = int(n_crews_3h)
        if not (1 <= n_clients <= 200):
            raise ValueError("n_clients must be in 1..200")
        if not (1 <= n_crews <= 12):
            raise ValueError("n_crews must be in 1..12")
        if not (0 <= n_crews_1h <= 12):
            raise ValueError("n_crews_1h must be in 0..12")
        if not (0 <= n_crews_3h <= 12):
            raise ValueError("n_crews_3h must be in 0..12")
        overrides = dict(cfg_overrides or {})
        self._args = (location_key, n_clients, n_crews, str(seed), overrides,
                      fire_zone, n_crews_1h, n_crews_3h)
        # ONBOARDING/BENCH seam: exact client coordinates survive reset()
        # (same custom homes on every replay of the scenario).
        self._client_locs = list(client_locs) if client_locs else None
        # STAGING seam (founder 2026-07-29 "assume all crews are within 30
        # minutes or less"): optional list of LatLon — in-zone crew i STARTS
        # PARKED at crew_locs[i % len] (beacon + sim position) instead of its
        # staging safe zone. home_base and the egress anchor STAY the round-
        # robin safe zone — escape targets are never client homes; the SEM-10
        # safety sweep governs a threatened start position exactly as it
        # governs any parked crew. None (default) = staging at safe zones,
        # byte-identical.
        self._crew_locs = list(crew_locs) if crew_locs else None
        self._cmd_log = [{
            "cmd": "configure", "location": location_key,
            "n_clients": n_clients, "n_crews": n_crews, "seed": str(seed),
            "cfg_overrides": {k: overrides[k] for k in sorted(overrides)},
            "fire_zone": fire_zone,
            "n_crews_1h": n_crews_1h, "n_crews_3h": n_crews_3h,
        }]
        self._init_state()
        return self

    def reset(self):
        """Re-arm the scenario exactly as configured (fires, crews, alerts,
        clock, cfg overrides — everything back to the post-configure state);
        the command is appended to cmd_log so the script stays replayable."""
        self._cmd_log.append({"cmd": "reset"})
        self._init_state()
        return self

    def _init_state(self) -> None:
        (location_key, n_clients, n_crews, seed, overrides,
         fire_zone_req, n_crews_1h, n_crews_3h) = self._args
        cfg = ResponseConfig()
        for k in sorted(overrides):
            if not hasattr(cfg, k):
                raise ValueError(f"unknown ResponseConfig field {k!r}")
            setattr(cfg, k, overrides[k])
        problems = validate_config(cfg)
        if problems:
            raise ValueError("invalid config: " + "; ".join(problems))
        self._cfg = cfg
        self._loc = LOCATIONS[location_key]
        self._seed = seed

        self._notes = []
        tile, synthetic, fire_zone = load_zone_tile(
            location_key, fire_zone=fire_zone_req, cell_m=cfg.grid_cell_m,
            tile_dir=self._tile_dir, synth_size_km=self._synth_size_km,
            notes=self._notes)
        self._tile = tile
        self._synthetic = synthetic
        self._fire_zone = fire_zone        # what ACTUALLY loaded (FIRE ZONE)
        if synthetic:
            self._notes.append(SYNTHETIC_BANNER)

        # FIRE ZONE horizon precedence (configure() docstring): an extended
        # session keeps a far fire inside the forecast unless the caller
        # explicitly pinned horizon_h in cfg_overrides.
        if fire_zone == "extended" and "horizon_h" not in overrides:
            cfg.horizon_h = max(cfg.horizon_h, EXT_MIN_HORIZON_H)
        # EXTENDED sessions route on the extended road graph (roads
        # inspection 2026-09-25), which opens proactive-escape routes beyond
        # the 18 km core that the standard graph never offered; the SEM-10e
        # exposed-transit fallback would ride them through audit-counted
        # pessimistic exposure (contracts escape_proactive_lawful_only). Same
        # precedence as the horizon floor: an explicit override always wins;
        # standard sessions are untouched (byte-identical).
        if (fire_zone == "extended"
                and "escape_proactive_lawful_only" not in overrides):
            cfg.escape_proactive_lawful_only = True

        # ROADS ADHERENCE (FOUNDER ROUND 3 law 2): lift the off-network spur
        # ceiling to SIM_ROAD_SPUR_MAX_M so the real cached graphs (terminal
        # spurs up to ~5.5 km on paradise_ca) render crews on the road network
        # with an honest capped spur instead of a whole-leg straight chord.
        # An explicit cfg_overrides['road_spur_max_m'] always wins.
        if "road_spur_max_m" not in overrides:
            cfg.road_spur_max_m = SIM_ROAD_SPUR_MAX_M

        # FOUNDER ROUND 4 law 6 (EMBER / ADJACENCY THREAT): the simulator sets
        # cfg.ember_threat_radius_m so developed (non-burnable) homes the fire
        # has reached the edge of inherit a threat ETA and DO alert + dispatch
        # (threaded into every spread.eta_at call below). Default
        # SIM_EMBER_THREAT_RADIUS_M; an explicit cfg_overrides value ALWAYS
        # wins (same precedence as the extended-zone horizon floor and the
        # road-spur ceiling — a caller may pin 0.0 to reproduce the raw
        # surface-only behaviour). ResponseConfig's own default is 0.0, so the
        # live product path is untouched.
        if "ember_threat_radius_m" not in overrides:
            cfg.ember_threat_radius_m = SIM_EMBER_THREAT_RADIUS_M

        # CALIBRATED CORRIDOR STACK (founder "flip calibrated stack"
        # 2026-07-30; calibration commit d7f490f, Grok 0-mismatch verify):
        # the SIMULATOR defaults to fuel-tiered erosion (contracts radii
        # 550/200/150 — FAST=550 is the validated grass floor; 500 burned
        # two crews on the paradise catch scenario) + escape-target
        # hysteresis 20 min (provably free: byte-identical outcomes, kills
        # the 5.5 h retarget zigzag). Explicit cfg_overrides ALWAYS win;
        # ResponseConfig defaults stay OFF so the live product path is
        # untouched.
        if "corridor_erosion_tiered" not in overrides:
            cfg.corridor_erosion_tiered = True
        if "escape_hysteresis_margin_min" not in overrides:
            cfg.escape_hysteresis_margin_min = SIM_ESCAPE_HYSTERESIS_MIN
        # TRANSIT-HAZARD STAGE 1 (validated 2026-07-30: per-class windows
        # provably active, byte-identical outcomes at 4 and 8 crews, 0
        # viol/ovt — no harm proven; the benefit regime is escape through
        # cooling grass, which this suite does not exercise).
        if "transit_cold_black_by_class" not in overrides:
            cfg.transit_cold_black_by_class = True
        # SEM-10e PROACTIVE PRE-POCKET ESCAPE (moderate-wind census
        # attribution 2026-08-02): the SIMULATOR defaults idle crews to
        # decamping ahead of a closing road pocket when a durable clean exit
        # still exists (contracts escape_proactive_lead_min; ResponseConfig
        # default stays 0.0 so the live product path is byte-identical).
        # Explicit cfg_overrides ALWAYS win.
        if "escape_proactive_lead_min" not in overrides:
            cfg.escape_proactive_lead_min = SIM_ESCAPE_PROACTIVE_LEAD_MIN
        # CORRIDOR FAIL-CLOSED (tri-model review 2026-08-31, fix-program item
        # 1): the SIMULATOR bounds off-raster corridor samples instead of
        # skipping them. corridor_unreached_bound stays OFF here on purpose —
        # the sim tile IS its world (nothing arrives from outside it), so an
        # in-tile UNREACHED cell is honestly unreached; the LIVE daemon arms
        # both via --fail-closed. Explicit cfg_overrides ALWAYS win.
        if "corridor_fail_closed" not in overrides:
            cfg.corridor_fail_closed = True
        # DETERMINISTIC SOLVER BUDGET (fix program 2026-09-11, review held
        # item): counted consults instead of the wall clock, so the same
        # scenario replays byte for byte under any load. Explicit override
        # wins; the engine default stays 0 (legacy wall clock).
        if "solver_budget_evals" not in overrides:
            cfg.solver_budget_evals = SIM_SOLVER_BUDGET_EVALS

        # --- roads (contracts wgresponse.roads seam; loaded ONCE per
        # configure). LAZY import: the sibling module lands independently —
        # a session must never hard-depend on it. Every offline path is
        # noted LOUDLY (module docstring ROADS section), never silent.
        # Loaded BEFORE client placement so the ROAD-PROXIMITY rule
        # (SimLocation.place_near_road_m) can consult the graph; its notes
        # are collected in road_notes and appended AFTER the placement notes
        # (below), so state.notes keeps its established order.
        road_notes = []
        mod = self._roads_module_arg
        if mod is _ROADS_AUTO:
            try:
                from wgresponse import roads as mod  # lazy (sibling seam)
            except Exception:
                mod = None
                road_notes.append(
                    "ROADS OFFLINE (wgresponse.roads not importable) — "
                    "crews move on straight tier-0 legs")
        elif mod is None:
            road_notes.append(
                "ROADS OFFLINE (disabled for this session) — crews move on "
                "straight tier-0 legs")
        self._roads = mod
        self._road_graph = None
        # PLAN-ON-ROADS session route cache (see _plan_road_provider)
        self._road_plan_cache = {}
        if mod is not None:
            g = None
            ext_loaded = False
            try:
                if fire_zone == "extended":
                    # FIRE ZONE road coverage (EXT_ROAD_SUFFIX): the extended
                    # tile's own graph; missing -> LOUD fallback below.
                    g = _load_road_graph_shared(
                        mod, location_key + EXT_ROAD_SUFFIX)
                    ext_loaded = g is not None
                    if g is None:
                        road_notes.append(
                            f"ROADS: no extended road graph cached for "
                            f"{location_key} ({location_key}"
                            f"{EXT_ROAD_SUFFIX}.wgr) — this extended session "
                            f"routes on the STANDARD ~18 km road graph; legs "
                            f"beyond it fall back to straight chords (fetch "
                            f"with py -3 tools/fetch_sim_roads.py --extended)")
                if g is None:
                    g = _load_road_graph_shared(mod, location_key)
            except Exception as e:       # unreadable cache: loud, non-fatal
                g = None
                road_notes.append(
                    f"ROADS OFFLINE (road graph load failed: {e}) — crews "
                    f"move on straight tier-0 legs")
            else:
                if g is None:
                    road_notes.append(
                        f"ROADS OFFLINE (no cached road graph for "
                        f"{location_key}) — crews move on straight tier-0 "
                        f"legs")
            self._road_graph = g
            if g is not None and ext_loaded:
                road_notes.append(
                    f"extended road graph loaded for {location_key} "
                    f"({len(g.nodes)} nodes, the whole extended fire-zone "
                    f"tile) — crews follow road routes for ingress and "
                    f"egress; departure decisions use max(tier-0, road) "
                    f"egress time")
            elif g is not None:
                road_notes.append(
                    f"road graph loaded for {location_key} "
                    f"({len(g.nodes)} nodes) — crews follow road routes for "
                    f"ingress and egress; departure decisions use "
                    f"max(tier-0, road) egress time")
        # display-only (ROADS LEGIBILITY): the loaded graph's node bbox
        # (south, west, north, east) — "outside the road graph" explanations
        self._road_bbox = None
        g_nodes = getattr(self._road_graph, "nodes", None)
        if g_nodes and isinstance(next(iter(g_nodes.values()), None), LatLon):
            lats = [p.lat for p in g_nodes.values()]
            lons = [p.lon for p in g_nodes.values()]
            self._road_bbox = (min(lats), min(lons), max(lats), max(lons))

        # FIRE ZONE placement law: clients ALWAYS live in the central
        # community window (the location's tile_half_m — ~18 km for every
        # preset with an extended tile); on the standard tile that IS the
        # tile (window=None keeps the pre-fire-zone bytes), on extended tiles
        # the WUI mask is restricted to the coordinate-computed central
        # sub-grid.
        window = (central_window_bbox(
                      self._loc.center,
                      half_m=getattr(self._loc, "tile_half_m",
                                     STANDARD_ZONE_HALF_M))
                  if fire_zone == "extended" else None)
        # ROAD-PROXIMITY PLACEMENT (SimLocation.place_near_road_m): 0 = off
        # (every existing preset, byte-identical). Armed but no graph loaded
        # -> whole-band draw, said LOUDLY (never a silent downgrade).
        near_road_m = float(getattr(self._loc, "place_near_road_m", 0.0) or 0.0)
        if self._place_near_road_override is not None:
            near_road_m = self._place_near_road_override     # harness seam
        if (near_road_m > 0.0 and self._road_graph is None
                and self._client_locs is None):
            self._notes.append(
                f"ROAD-PROXIMITY PLACEMENT ({near_road_m:g} m) is set for "
                f"{location_key} but no road graph is loaded — clients are "
                f"drawn from the WHOLE WUI band (some may sit on roadless "
                f"'developed' cells)")
        if self._client_locs is not None:
            # ONBOARDING/BENCH seam: exact caller-supplied home coordinates
            # (real candidate parcels / synthetic geometries). Off-tile fails
            # loud — a silently dropped candidate would corrupt every
            # downstream serviceability number.
            if not self._client_locs:
                raise ValueError("client_locs must be non-empty when given")
            self._clients = []
            for k, p in enumerate(self._client_locs, start=1):
                if geo.latlon_to_rc(tile.spec, p) is None:
                    raise ValueError(
                        f"client_locs[{k - 1}] ({p.lat:.5f},{p.lon:.5f}) is "
                        f"outside the {location_key} tile")
                self._clients.append(ClientProperty(
                    client_id=f"sim-c{k}", loc=p, name=f"Sim client {k}",
                    value_usd=1_200_000.0, graphgel_enrolled=True,
                    priority_weight=1.0))
        else:
            self._clients = place_clients(
                tile, n_clients, seed=f"{location_key}|{seed}",
                notes=self._notes, window=window,
                bounds_poly=getattr(self._loc, "bounds_poly", ()),
                road_graph=self._road_graph, near_road_m=near_road_m)
        self._client_by_id = {c.client_id: c for c in self._clients}
        # Honest-model note (never silent): the WUI band deliberately includes
        # the developed side of the interface, and the overflow pool can land
        # clients on ANY non-burnable code (91 urban, 92 snow/ice, 93 ag, 98
        # water, 99 barren) — the surface-spread model treats every NB cell as
        # a fire break, so their OWN cell keeps an UNREACHED ETA. The census
        # counts ALL NB codes (the eta_gap_report latent finding: the old
        # 91/93-only count undercounted homes the overflow pool placed on
        # 92/98/99). FOUNDER ROUND 4 law 6: with the ember-threat radius
        # threaded into eta_at, a developed home the fire has reached the edge
        # of now inherits an adjacent burnable arrival and DOES alert +
        # dispatch; only homes with no burnable cell within the radius stay
        # unthreatened.
        n_nb = 0
        for c in self._clients:
            rc = geo.latlon_to_rc(tile.spec, c.loc)
            if rc is not None and not is_burnable(int(round(tile.fuel.get(*rc)))):
                n_nb += 1
        if n_nb:
            r_m = self._cfg.ember_threat_radius_m
            if r_m > 0.0:
                self._notes.append(
                    f"{n_nb} of {len(self._clients)} homes sit on developed "
                    f"(non-burnable 91/92/93/98/99) cells — now covered by the "
                    f"~{r_m:g} m ember-threat radius where fire reaches an "
                    f"adjacent burnable cell (they alert + dispatch); isolated "
                    f"homes with no burnable neighbour in range remain "
                    f"unthreatened (a surface fire cannot burn the developed "
                    f"cell itself; ember/structure spread not modeled)")
            else:
                self._notes.append(
                    f"{n_nb} of {len(self._clients)} homes sit on developed "
                    f"(non-burnable 91/92/93/98/99) cells and the ember-threat "
                    f"radius is disabled (ember_threat_radius_m=0): the "
                    f"surface-spread model cannot reach them, so they will "
                    f"not alert or dispatch (ember/structure spread not "
                    f"modeled)")

        # The roads notes land HERE, after the placement notes — the order
        # every existing session's state.notes has always had (the graph
        # itself is loaded above, before placement; see the ROADS block).
        self._notes.extend(road_notes)

        zones = list(self._loc.safe_zones)
        self._zone_by_id = {z.zone_id: z for z in zones}
        speed = _eff_ground_speed_mps(cfg)
        self._tracker = CrewTracker(cfg)
        self._crew_sims = {}
        self._tracks = {}
        self._crew_anchor = {}
        # REGIONAL / MUTUAL-AID CREWS (contracts FOUNDER ROUND 5 SEMANTICS 9).
        # region: 'in_zone' | 'reg_1h' | 'reg_3h'. delay_min: minutes after the
        # FIRST ignition a reinforcement arrives (0 for in-zone). available_at:
        # the mobilization clock — None until ignite() stamps it (in-zone crews
        # stay None: they are local, never "arriving"). All three are keyed by
        # crew_id and snapshotted into state.crews[] every tick.
        self._crew_region = {}
        self._crew_reinforce_delay_min = {}
        self._crew_available_at = {}
        tile_bbox = self._tile_bbox()
        k = 0
        # in-zone crews (staged round-robin at safe zones, AVAILABLE now) —
        # construction is byte-identical to the pre-SEMANTICS-9 loop.
        for _j in range(n_crews):
            k += 1
            zone = zones[(k - 1) % len(zones)]
            crew = Crew(crew_id=f"sim-g{k}", name=f"GraphGel {k}",
                        status=CrewStatus.AVAILABLE, home_base=zone.loc)
            cs = _SimCrew(
                crew, speed, route_fn=self._leg_route,
                latest_fn=self._stop_latest_depart_s,
                road_guard=(self._road_graph is not None))
            # STAGING seam: park the crew at its custom start (position +
            # beacon only; home_base/egress anchor stay the safe zone above).
            start = zone.loc
            if self._crew_locs:
                start = self._crew_locs[(k - 1) % len(self._crew_locs)]
                cs.pos = start
            self._crew_sims[crew.crew_id] = cs
            self._tracks[crew.crew_id] = deque(maxlen=240)
            self._crew_anchor[crew.crew_id] = zone.zone_id
            self._crew_region[crew.crew_id] = "in_zone"
            self._crew_reinforce_delay_min[crew.crew_id] = 0.0
            self._crew_available_at[crew.crew_id] = None
            self._tracker.ingest(CrewBeacon(crew_id=crew.crew_id, loc=start,
                                            t_s=T0_S, status=CrewStatus.AVAILABLE))
        # out-of-region reinforcements: created INBOUND, staged for arrival at a
        # round-robin safe zone (crew.home_base / anchor_zone_id), but PARKED at
        # a deterministic staging-entry point on the tile EDGE toward their
        # region until they mobilize + arrive. available_at stays None (standby)
        # until the first ignition (ignite() -> _mobilize_reinforcements).
        for region, n_reg, delay_min in (
                ("reg_1h", n_crews_1h, cfg.crew_reinforce_1h_min),
                ("reg_3h", n_crews_3h, cfg.crew_reinforce_3h_min)):
            for i in range(n_reg):
                k += 1
                zone = zones[(k - 1) % len(zones)]
                crew = Crew(crew_id=f"sim-g{k}", name=f"GraphGel {k}",
                            status=CrewStatus.INBOUND, home_base=zone.loc)
                cs = _SimCrew(
                    crew, speed, route_fn=self._leg_route,
                    latest_fn=self._stop_latest_depart_s,
                    road_guard=(self._road_graph is not None))
                cs.status = CrewStatus.INBOUND      # base ctor defaults AVAILABLE
                cs.pos = self._edge_entry_point(tile_bbox, region, i, n_reg)
                self._crew_sims[crew.crew_id] = cs
                self._tracks[crew.crew_id] = deque(maxlen=240)
                self._crew_anchor[crew.crew_id] = zone.zone_id
                self._crew_region[crew.crew_id] = region
                self._crew_reinforce_delay_min[crew.crew_id] = float(delay_min)
                self._crew_available_at[crew.crew_id] = None
                self._tracker.ingest(CrewBeacon(
                    crew_id=crew.crew_id, loc=cs.pos, t_s=T0_S,
                    status=CrewStatus.INBOUND))

        # ROADS LEGIBILITY (display only; module constants MAP_*): the road
        # path each crew drove during the last tick, its driven trail, and
        # any NON-driven position change (a reinforcement's arrival — a jump
        # the map must never draw as a drive). Never read by the engine.
        self._map_last_pos = {cid: cs.pos
                              for cid, cs in self._crew_sims.items()}
        self._map_moved = {}
        self._map_jumped = {}
        self._map_trail = {cid: deque(maxlen=MAP_TRAIL_TICKS)
                           for cid in self._crew_sims}
        self._map_trail_runs = {}
        self._map_route_cache = {}
        self._map_preview_cache = {}
        self._map_place_idx = None        # rebuilt lazily (clients changed)
        self._map_gap_cache = {}

        self._alerts = AlertEngine(cfg, clients_by_id=dict(self._client_by_id))
        self._t0_s = T0_S
        self._now_s = T0_S
        self._tick_n = 0
        self._speed = 1.0
        self._paused = False
        self._fires = {}
        self._fire_order = []
        self._n_fires = 0
        self._wind_dir_deg = self._loc.hazard_wind_dir_deg
        self._intensity = None
        self._wx = None
        self._wx_epoch = 0            # bumped by _rebuild_weather (world changes)
        self._serviced = []
        self._assigned = set()
        self._jobbed = {}
        self._unserved_reason = {}
        self._stop_rule = {}
        self._withdrawn = []          # loud safety-withdrawal records (state)
        self._superseded = set()      # in-flight stops under a TIGHTENED rule
        self._alert_feed = deque(maxlen=400)
        self._plan_snapshot = None
        self._client_etas = {}
        self._audit = []
        self._violations = []
        self._min_margin_h = None
        # CONTINUOUS CREW SAFETY (contracts SAFETY SEMANTICS 10). _relocating:
        # crew_id -> reason str while a crew is repositioning to a safe zone
        # (10a; surfaced as crews[i].relocating/relocate_reason). _n_relocations:
        # cumulative relocation-EVENT counter (scoreboard.relocations, kept
        # DISTINCT from safety withdrawals). _crew_overruns: this tick's audit
        # crew-overrun rows (10c backstop; folded into scoreboard.violations so
        # the count can never read 0 while a crew stands in the burn).
        # _no_safe_zone: latched True once the front is within the egress buffer
        # of EVERY zone (10b fail-closed alarm; emitted once on transition).
        self._relocating = {}
        self._n_relocations = 0
        self._crew_overruns = []
        self._no_safe_zone = False
        # SEM-10d: one-shot alarm latch for the area-escape note (crews leaving
        # the operating area because no catalog zone is safely reachable).
        self._area_escape_alarmed = False
        # FOUNDER ROUND 5 COATING HOLD state: released latches True the first
        # time the feasibility gate fails (no stop-start churn); _hold_noted
        # one-shots the activation note; _preposition_target avoids re-issuing
        # the same staging move every tick.
        self._hold_released = False
        self._hold_noted = False
        self._hold_active_n = 0
        self._preposition_target = {}
        # CREW SHIFTS (ROUND 6b): duty_start = unix s this crew's current
        # shift began (armed at first ignition / reinforcement arrival);
        # shift_budget = seconds of this shift (first cycle halved for odd
        # crews — A/B stagger); resting_until = unix s rest ends.
        self._duty_start = {}
        self._shift_budget = {}
        self._resting_until = {}
        # LAW 1 TIME-SCRUB HISTORY (FOUNDER ROUND 3): a bounded ring of DEEP
        # snapshots of every emitted tick's state() (oldest evicted at
        # cfg.history_ticks). Recorded ONLY at tick emit (never on a bare
        # state() poll), so state_at(k) replays byte-identically what was
        # served live at tick k. reset()/configure() rebuild _init_state, so
        # the ring clears with the scenario. Entries are (tick_no, snapshot);
        # tick_no is contiguous (paused ticks neither advance nor record).
        self._history = deque(maxlen=max(0, int(cfg.history_ticks)))

    # -- regional / mutual-aid crews (SEMANTICS 9) ---------------------------

    def _tile_bbox(self):
        """(west, south, east, north) degrees of the LOADED tile — the same
        rectangle state()['terrain']['bbox'] reports, computed straight from
        the GridSpec (contracts row 0 = north). Used to park INBOUND
        reinforcements on the tile edge toward their region."""
        spec = self._tile.spec
        lat_mid = (spec.north_lat
                   - spec.nrows * spec.cell_m / (2.0 * geo.M_PER_DEG_LAT))
        west = spec.west_lon
        north = spec.north_lat
        south = spec.north_lat - spec.nrows * spec.cell_m / geo.M_PER_DEG_LAT
        east = (spec.west_lon
                + spec.ncols * spec.cell_m / geo.m_per_deg_lon(lat_mid))
        return (west, south, east, north)

    @staticmethod
    def _edge_entry_point(bbox, region: str, i: int, n: int) -> LatLon:
        """A DETERMINISTIC staging-entry marker on the tile EDGE for the i-th
        (of n) reinforcement of `region` (SEMANTICS 9 STATE EMISSION): reg_1h
        enters from the EAST edge, reg_3h from the WEST edge, and the n crews of
        a region are fanned evenly down that edge (interior fractions, never a
        corner) so multiple inbound markers stay distinct. Pure function of the
        bbox + indices -> byte-identical on replay; the UI draws the inbound
        marker + countdown here until arrival flips the crew to its safe zone."""
        west, south, east, north = bbox
        frac = (i + 1) / (n + 1)
        lat = north - frac * (north - south)
        lon = east if region == "reg_1h" else west
        return LatLon(lat, lon)

    def _mobilize_reinforcements(self, ignition_s: float) -> None:
        """FIRST-IGNITION MOBILIZATION (SEMANTICS 9): stamp every reinforcement
        crew's arrival clock available_at_s = ignition_s + delay (reg_1h:
        cfg.crew_reinforce_1h_min * 60; reg_3h: crew_reinforce_3h_min * 60).
        In-zone crews are skipped (they are already local). Called ONLY on the
        first ignition — later ignitions never re-stamp (the incident that
        summons mutual aid is the first one)."""
        for cid, region in self._crew_region.items():
            if region == "in_zone":
                continue
            delay_s = self._crew_reinforce_delay_min.get(cid, 0.0) * 60.0
            self._crew_available_at[cid] = ignition_s + delay_s

    # -- commands --------------------------------------------------------------

    def ignite(self, lat: float, lon: float, intensity: str = "moderate",
               wind_dir_deg: Optional[float] = None) -> str:
        """Start a fire at (lat, lon) with an intensity preset. Returns the
        incident id sim-f<k>. Weather is DOMAIN-SHARED (design): the newest
        ignite's intensity (+ optional wind override, default = the location's
        canonical hazard wind) rebuilds the one MockWeather script every fire
        forecasts under. The point must land on the loaded tile (fail loud)."""
        if intensity not in INTENSITY_WEATHER:
            raise ValueError(f"intensity must be one of "
                             f"{sorted(INTENSITY_WEATHER)}, got {intensity!r}")
        p = LatLon(float(lat), float(lon))
        rc = geo.latlon_to_rc(self._tile.spec, p)
        if rc is None:
            raise ValueError(f"ignition {p.lat:.5f},{p.lon:.5f} is outside "
                             f"the {self._loc.key} tile")
        code = int(round(self._tile.fuel.get(*rc)))
        if not is_burnable(code):
            # A Rothermel surface fire cannot start or spread on NB fuel; a
            # silently dead fire would look like a broken simulator, so the
            # rejection is loud (the UI can surface it as a click hint).
            raise ValueError(
                f"ignition cell is non-burnable (FBFM40 {code}); pick a "
                f"burnable cell — the surface-spread model cannot ignite "
                f"urban/agriculture/water/barren fuel")
        if wind_dir_deg is not None:
            self._wind_dir_deg = float(wind_dir_deg) % 360.0
        self._intensity = intensity
        # REGIONAL / MUTUAL-AID CREWS (SEMANTICS 9): the FIRST ignition is what
        # summons out-of-region aid — mobilize reinforcements exactly once, off
        # this ignition's time. Later ignitions never re-arm the arrival clock.
        first_ignition = (self._n_fires == 0)
        self._n_fires += 1
        fid = f"sim-f{self._n_fires}"
        self._fires[fid] = _SimFire(incident_id=fid, origin=p,
                                    t_ignite_s=self._now_s, intensity=intensity,
                                    wind_dir_deg=self._wind_dir_deg)
        self._fire_order.append(fid)
        if first_ignition:
            self._mobilize_reinforcements(self._now_s)
        self._rebuild_weather()
        self._cmd_log.append({"cmd": "ignite", "lat": float(lat),
                              "lon": float(lon), "intensity": intensity,
                              "wind_dir_deg": (None if wind_dir_deg is None
                                               else float(wind_dir_deg) % 360.0)})
        return fid

    def set_wind(self, dir_deg: float) -> None:
        """Turn the domain wind dial (meteorological FROM degrees); forecasts
        rebuild and every fire re-runs the ensemble next tick — re-anchored
        at that tick's clock from its EXACT burned mask, so the burn so far
        is kept cell for cell and all further growth runs under the new wind
        (tick() step 2). The server's /api/sim/<id>/config {wind_dir_deg}
        routes the page's wind dial here (founder 2026-09-25)."""
        self._wind_dir_deg = float(dir_deg) % 360.0
        for f in self._fires.values():
            f.wind_dir_deg = self._wind_dir_deg
        if self._intensity is not None:
            self._rebuild_weather()
        self._cmd_log.append({"cmd": "set_wind", "dir_deg": self._wind_dir_deg})

    def set_buffer(self, egress_buffer_min: float) -> None:
        """THE SAFETY SLIDER: live-update cfg.egress_buffer_min (SAFETY
        SEMANTICS 2). Validates, mutates the shared cfg, revokes every
        not-yet-started stop so the next tick re-plans under the new rule,
        and re-checks any IN-PROGRESS deploy against the new rule — a frozen
        stop whose projected departure would violate it is withdrawn on the
        spot (see _policy_changed; never silently kept)."""
        trial = replace(self._cfg, egress_buffer_min=float(egress_buffer_min))
        problems = validate_config(trial)
        if problems:
            raise ValueError("invalid config: " + "; ".join(problems))
        self._cfg.egress_buffer_min = float(egress_buffer_min)
        self._policy_changed()
        self._cmd_log.append({"cmd": "set_buffer",
                              "egress_buffer_min": float(egress_buffer_min)})

    def set_deploy_min(self, deploy_min: float) -> None:
        """FOUNDER ROUND 6 COAT-TIME SLIDER: live-update cfg.deploy_min (the
        minutes one crew spends coating one home — the 3-person crew model is
        15, a 2-person crew ~30). Validates, mutates the shared cfg, and
        revokes every not-yet-started stop so the next tick re-plans under
        the new coat time. IN-PROGRESS deploys keep their captured deploy_s
        (adoption-time rule — the crew on a roof finishes the job it started;
        the new time governs every stop planned after the change)."""
        trial = replace(self._cfg, deploy_min=float(deploy_min))
        problems = validate_config(trial)
        if problems:
            raise ValueError("invalid config: " + "; ".join(problems))
        if not (1.0 <= float(deploy_min) <= 120.0):
            raise ValueError("deploy_min must be in [1, 120] minutes")
        self._cfg.deploy_min = float(deploy_min)
        self._policy_changed()
        self._cmd_log.append({"cmd": "set_deploy_min",
                              "deploy_min": float(deploy_min)})

    def set_slack(self, slack_min: float) -> None:
        """Live-update cfg.slack_min (SAFETY SEMANTICS 7 planned-slack floor);
        revokes unstarted stops to force a re-plan under the new floor and
        re-checks any in-progress deploy (same withdraw-toward-safety law as
        set_buffer; the audit rule itself is unchanged by slack)."""
        if float(slack_min) < 0.0:
            raise ValueError("slack_min must be >= 0")
        trial = replace(self._cfg, slack_min=float(slack_min))
        problems = validate_config(trial)
        if problems:
            raise ValueError("invalid config: " + "; ".join(problems))
        self._cfg.slack_min = float(slack_min)
        self._policy_changed()
        self._cmd_log.append({"cmd": "set_slack", "slack_min": float(slack_min)})

    def set_speed(self, mult: float) -> None:
        """Clock acceleration: tick() advances cfg.replan_period_s * mult.
        Bounded to (0, MAX_SPEED_MULT] — the design-doc clock ceiling (600x),
        the same law the server /api/sim config path enforces; an unbounded
        multiplier would let command scripts take arbitrarily coarse
        multi-cycle jumps."""
        m = float(mult)
        if not (0.0 < m <= MAX_SPEED_MULT):
            raise ValueError(f"speed must be in (0, {MAX_SPEED_MULT:g}]")
        self._speed = m
        self._cmd_log.append({"cmd": "set_speed", "mult": m})

    def pause(self) -> None:
        """Freeze the clock: tick() becomes a no-op until resume()."""
        self._paused = True
        self._cmd_log.append({"cmd": "pause"})

    def resume(self) -> None:
        """Release a pause()."""
        self._paused = False
        self._cmd_log.append({"cmd": "resume"})

    def _rebuild_weather(self) -> None:
        spec = INTENSITY_WEATHER[self._intensity]
        mock = MockWeather(seed=f"simui|{self._seed}|wx",
                           base_wind_mps=spec["wind_mph"] * MPH_TO_MPS,
                           dir_deg=self._wind_dir_deg,
                           rh_pct=spec["rh_pct"], temp_c=spec["temp_c"],
                           gust_factor=GUST_FACTOR, t0_s=self._t0_s)
        self._wx = mock.forecast(self._loc.center, self._cfg.horizon_h)
        # World-change epoch: every fire's persisted arrival field re-anchors
        # on its next tick (see _SimFire docstring). Ticks themselves never
        # bump this.
        self._wx_epoch = getattr(self, "_wx_epoch", 0) + 1

    def _leg_route(self, a: LatLon, b: LatLon):
        """One movement leg through the contracts wgresponse.roads seam:
        roads.route(graph, a, b, cfg) when a graph is loaded, else None (the
        crew falls back to the original straight tier-0 leg). Contracts law:
        roads.route itself NEVER silently pretends a road route — a
        disconnected pair comes back as the straight leg with
        on_roads=False, which the crew follows verbatim.

        CORRIDOR HONESTY (cfg.route_alternatives > 1): the leg resolves
        through the candidate policy at the CURRENT clock — crews departing
        now drive the fastest candidate that passes the corridor law today,
        re-chosen with fresh fire state at every departure. (Crew legs use
        the ingress rule; live egress safety is owned by the SEM-10 sweep.)"""
        if self._roads is None or self._road_graph is None:
            return None
        if int(getattr(self._cfg, "route_alternatives", 1) or 1) > 1:
            return self._route_choose(a, b, "ingress", self._now_s)
        return self._roads.route(self._road_graph, a, b, self._cfg)

    def _corridor_clock_s(self, p: LatLon) -> float:
        """Earliest ERODED-P10 arrival (unix s) at p across all fires — the
        exact grids the corridor law samples; UNREACHED when no fire reaches
        the point. Feeds route-candidate generation (roads.route_candidates
        node_clock_fn) so 'hot' means what the law means by hot."""
        best = UNREACHED
        for f in self._fires.values():
            er = self._eroded_p10(f)
            if er is None:
                continue
            rc = geo.latlon_to_rc(er.spec, p)
            if rc is None:
                continue
            v = er.data[rc[0] * er.spec.ncols + rc[1]]
            if v < best:
                best = v
        return best

    def _route_choose(self, a: LatLon, b: LatLon, mode: str,
                      depart_s: float):
        """CORRIDOR HONESTY route choice (contracts route_alternatives;
        founder 2026-07-29). Generates up to 3 candidates (fastest /
        widest-clock / avoid-hot) and judges EVERY one with the unchanged
        LegOracle corridor law on its own polyline:
          ingress -> the FASTEST candidate whose latest-safe-depart T* covers
                     depart_s (deadlines still matter on the way in);
          egress  -> the SAFEST candidate (max T*; founder rule — safety
                     routes are chosen to be safe from fire, not for speed).
        When nothing passes, returns the fastest candidate — the caller's
        law then rejects it exactly as the legacy single-route planner did
        (candidates can only ADD feasible work, never mask a violation)."""
        cands = self._roads.route_candidates(
            self._road_graph, a, b, self._cfg, self._corridor_clock_s)
        if len(cands) == 1:
            return cands[0]
        judged = []
        for rr in cands:
            spd = rr.dist_m / rr.time_s if rr.time_s > 0 else None
            tstar = UNREACHED
            if spd:
                for f in self._fires.values():
                    er = self._eroded_p10(f)
                    if er is None:
                        continue
                    t = safety.LegOracle(
                        a, b, spd, er, self._cfg, path=rr.polyline,
                        margin_min=self._cfg.route_margin_roads_min
                    ).latest_safe_depart_s
                    if t < tstar:
                        tstar = t
            judged.append((rr, tstar))
        if mode == "egress":
            judged.sort(key=lambda rt: (-rt[1], rt[0].time_s))
            return judged[0][0]
        passing = sorted((rt for rt in judged if depart_s <= rt[1]),
                         key=lambda rt: rt[0].time_s)
        return passing[0][0] if passing else cands[0]

    def _plan_road_provider(self):
        """PLAN-ON-ROADS provider for dispatch.plan (cfg.dispatch_plan_on_roads;
        founder build order 2026-07-12 after the chords-only control measured
        the chord-vs-road mismatch at ~73% of relocations + the residual
        overrun tail). Returns a session-cached (a, b) -> RoadRoute callable,
        or None when the flag is off / no road graph is loaded (dispatch then
        plans tier-0 chords, byte-identical to the legacy planner).

        Cache keyed on ~110 m position buckets (round to 3 decimals): client
        locations are fixed so client<->client routes compute ONCE per
        session; a crew's rows re-compute only when it moves out of its
        bucket (~12 s of drive), keeping per-tick A* work small. Bounded to
        _ROAD_PLAN_CACHE_MAX entries (cleared whole on overflow — simple,
        deterministic)."""
        if (not getattr(self._cfg, "dispatch_plan_on_roads", False)
                or self._roads is None or self._road_graph is None):
            return None
        cache = self._road_plan_cache
        alts = int(getattr(self._cfg, "route_alternatives", 1) or 1)

        def provider(a: LatLon, b: LatLon, mode: str = "ingress"):
            # CORRIDOR HONESTY: candidate choice depends on fire state and
            # the plan clock, so the alt-mode cache key carries mode + tick
            # time (within-plan reuse only — a stale choice must never
            # outlive the fire state it was judged against). Legacy path
            # keeps the original tick-free key, byte-identical.
            if alts > 1:
                k = (round(a.lat, 3), round(a.lon, 3),
                     round(b.lat, 3), round(b.lon, 3), mode,
                     round(self._now_s))
            else:
                k = (round(a.lat, 3), round(a.lon, 3),
                     round(b.lat, 3), round(b.lon, 3))
            if k in cache:
                return cache[k]
            if len(cache) >= _ROAD_PLAN_CACHE_MAX:
                cache.clear()
            if alts > 1:
                rr = self._route_choose(a, b, mode, self._now_s)
            else:
                rr = self._roads.route(self._road_graph, a, b, self._cfg)
            cache[k] = rr
            return rr

        return provider

    def _stop_latest_depart_s(self, stop: dict) -> float:
        """LIVE latest-depart for one itinerary stop — this tick's p10 at the
        client minus the stop's captured rule minutes (the exact law
        _run_audit enforces). UNREACHED (no deadline) when there is no
        forecast yet. Pure read of tick-frozen session state (deterministic);
        the crews' road departure guard keys off this."""
        fire = self._fires.get(stop["incident_id"])
        cl = self._client_by_id.get(stop["client_id"])
        if fire is None or fire.ens is None or cl is None:
            return UNREACHED
        buf_min, ember_min = self._stop_rule.get(
            stop["client_id"], (self._cfg.egress_buffer_min,
                                self._cfg.ember_buffer_min))
        # law 6: ember/adjacency fallback so a developed-cell stop keeps the
        # SAME finite deadline the audit/live loop see (all eta_at call sites
        # thread the radius, so the departure guard never disagrees with the
        # audit for a fire-adjacent developed home).
        eta = spread.eta_at(fire.ens, cl.loc,
                            fallback_radius_m=self._cfg.ember_threat_radius_m)
        return eta.eta_p10_s - (buf_min + ember_min) * 60.0

    def _revoke_unstarted(self) -> None:
        for cid in _idsort(self._crew_sims):
            for dropped in self._crew_sims[cid].revoke_unstarted():
                self._assigned.discard(dropped)
        if self._plan_snapshot is not None:
            self._plan_snapshot["revoked"] = True

    def _policy_changed(self) -> None:
        """A live safety-policy change (set_buffer/set_slack) took effect.

        1. Every not-yet-started stop is revoked (next tick re-plans under
           the new rule) — the original SIM-2 semantics.
        2. Every IN-PROGRESS deploy is re-checked NOW instead of being
           grandfathered (2026-07-09 verifier blocking finding): the stop's
           audit rule becomes the TIGHTER of its adoption-time rule and the
           new rule (a live loosening never relaxes in-flight work — never
           weaken), tightened stops are marked policy-superseded so every
           later tick re-checks them against the newest forecast, and a
           projected departure that violates the effective rule withdraws
           the crew immediately (_recheck_deploy: abort unfinished, egress
           now, loud record). Deterministic: pure function of session state
           and the fires' current ensembles."""
        self._revoke_unstarted()
        cfg = self._cfg
        new_rule = (cfg.egress_buffer_min, cfg.ember_buffer_min)
        for cid in _idsort(self._crew_sims):
            cs = self._crew_sims[cid]
            if cs._mode != "deploy" or not cs.itinerary:
                continue
            client_id = cs.itinerary[0]["client_id"]
            old_rule = self._stop_rule.get(client_id, new_rule)
            if new_rule[0] + new_rule[1] > old_rule[0] + old_rule[1]:
                # Tightened: in-flight work adopts the stricter rule and
                # stays under per-tick re-checks until it completes.
                self._stop_rule[client_id] = new_rule
                self._superseded.add(client_id)
            self._recheck_deploy(cid, cause="policy_change")

    def _recheck_deploy(self, crew_id: str, cause: str) -> bool:
        """Re-check one crew's IN-PROGRESS stop: projected departure
        (the scheduled deploy end) vs the stop's OWN audit rule — the same
        latest-depart law _run_audit enforces — at the newest p10. On a
        projected violation the crew WITHDRAWS immediately: the deploy is
        aborted unfinished (no ServicedStop), the crew turns onto its
        planned egress leg, the client is freed for re-planning with reason
        'withdrawn_unsafe', and a loud record lands in state()['withdrawn']
        + notes. Returns True iff a withdrawal happened. Resolve toward
        safety: leaving early is always legal; staying past the rule never
        is."""
        cs = self._crew_sims[crew_id]
        if cs._mode != "deploy" or not cs.itinerary:
            return False
        stop = cs.itinerary[0]
        client_id = stop["client_id"]
        fire = self._fires.get(stop["incident_id"])
        cl = self._client_by_id.get(client_id)
        if fire is None or fire.ens is None or cl is None:
            return False                 # no forecast yet -> no deadline
        buf_min, ember_min = self._stop_rule.get(
            client_id, (self._cfg.egress_buffer_min,
                        self._cfg.ember_buffer_min))
        eta = spread.eta_at(fire.ens, cl.loc,       # law 6 ember/adjacency
                            fallback_radius_m=self._cfg.ember_threat_radius_m)
        latest = eta.eta_p10_s - (buf_min + ember_min) * 60.0
        # ROAD sessions (module docstring ROADS nuance): every departure
        # decision keys off the EFFECTIVE egress time max(tier-0, road) —
        # the projected departure must still clear the safe zone by
        # latest-depart. 0 in straight-leg sessions (semantics unchanged).
        road_slack_s = 0.0
        if self._road_graph is not None:
            road_slack_s = cs.road_eff_egress_s()
        latest -= road_slack_s
        projected = cs._deploy_end_s
        if math.isinf(latest) or projected <= latest:
            return False
        for dropped in cs.abort_deploy():
            self._assigned.discard(dropped)
            self._superseded.discard(dropped)
        self._stop_rule.pop(client_id, None)
        self._unserved_reason[client_id] = "withdrawn_unsafe"
        deficit_min = (projected - latest) / 60.0
        self._withdrawn.append({
            "crew_id": crew_id, "client_id": client_id,
            "incident_id": stop["incident_id"], "at_s": self._now_s,
            "cause": cause,
            "projected_depart_s": projected, "latest_depart_s": latest,
            "rule_buffer_min": buf_min, "rule_ember_min": ember_min,
            "road_eff_egress_min": round(road_slack_s / 60.0, 4),
            "deficit_min": round(deficit_min, 4),
        })
        self._notes.append(
            f"SAFETY WITHDRAWAL ({cause}): {crew_id} aborted its in-progress "
            f"deploy on {client_id} — projected departure would violate the "
            f"{buf_min:g}+{ember_min:g} min rule by {deficit_min:.1f} min "
            f"against the current P10; crew egressing now, gel NOT applied")
        return True

    # -- CONTINUOUS CREW SAFETY (contracts SAFETY SEMANTICS 10) -----------------

    def _eroded_p10(self, fire):
        """The eroded (corridor-erosion) P10 arrival raster for one fire —
        geo.erode_min(fire.ens.p10, cfg.corridor_erosion_m), the EXACT transform
        the corridor/departure law uses (SAFETY SEMANTICS 3). Memoised on the
        fire keyed by its CURRENT ensemble object, so it is recomputed only when
        the fire's persisted field is (re)built (world change / horizon rebase),
        never per tick and never per crew. None until the fire has an ensemble."""
        ens = fire.ens
        if ens is None:
            return None
        if fire._eroded_src is not ens:
            if getattr(self._cfg, "corridor_erosion_tiered", False):
                # FUEL-TIERED EROSION: the session owns the fuel tile, so
                # the one memoized eroded raster every consumer shares (SEM-10
                # sweep, LegOracle judge, dispatch via the eroded_rasters
                # seam, server overlay) is the tiered field.
                fire._eroded_p10 = spread.tiered_eroded_p10(
                    ens.p10, self._tile.fuel, self._cfg)
            else:
                fire._eroded_p10 = geo.erode_min(
                    ens.p10, self._cfg.corridor_erosion_m,
                    shape=("euclidean"
                           if getattr(self._cfg, "corridor_erosion_euclidean",
                                      False) else "chebyshev"))
            fire._eroded_src = ens
        return fire._eroded_p10

    def _cold_black_pass_s(self, p) -> float:
        """Cold-black window (seconds) at point p for the SEM-10 route-
        selection carve-out. Legacy: the flat 45-min _COLD_BLACK_PASS_S.
        Under cfg.transit_cold_black_by_class (TRANSIT-HAZARD STAGE 1): the
        point's own fuel class sets the window (fuels.cold_black_minutes —
        grass minutes, timber STRICTER than the flat rule). Off-tile or
        unreadable fuel falls back to the flat window. The 10c audit never
        consults this."""
        if not getattr(self._cfg, "transit_cold_black_by_class", False):
            return _COLD_BLACK_PASS_S
        rc = geo.latlon_to_rc(self._tile.spec, p)
        if rc is None:
            return _COLD_BLACK_PASS_S
        code = int(self._tile.fuel.data[rc[0] * self._tile.spec.ncols
                                        + rc[1]])
        return cold_black_minutes(code) * 60.0

    def _escape_switch_ok(self, cs, now: float, lead_s: float,
                          new_loc, new_id: str) -> bool:
        """ESCAPE-TARGET HYSTERESIS (contracts escape_hysteresis_margin_min):
        True = adopt the proposed escape target; False = keep driving to the
        current one. Once fleeing, a crew switches only when the proposal
        improves the ROUTED destination margin by more than the threshold,
        or trades a burned-crossing route for a clean one — re-picking every
        tick zigzagged a crew between near-equal escape rays for ~5.5 h
        (caught-crew dissection, commit 26db201). Committed motion beats
        oscillation. Threshold 0 = legacy always-switch, byte-identical."""
        hyst_s = self._cfg.escape_hysteresis_margin_min * 60.0
        if (hyst_s <= 0.0 or cs.egress_loc is None
                or new_id == cs.egress_zone_id):
            return True
        b_cur, _e, _w, _t, dm_cur = self._route_metrics(
            cs.pos, cs.egress_loc, now, cs.speed, lead_s)
        b_new, _e2, _w2, _t2, dm_new = self._route_metrics(
            cs.pos, new_loc, now, cs.speed, lead_s)
        if b_cur and not b_new:
            return True                 # clean route beats burned route
        if b_new and not b_cur:
            return False
        return (dm_new - dm_cur) > hyst_s

    def _plan_eroded_rasters(self):
        """eroded_rasters seam for dispatch.plan (FUEL-TIERED EROSION): the
        session's memoized per-fire eroded fields — tiered when the flag is
        on, so planner, SEM-10 sweep, route judge and overlay all see ONE
        corridor field. None when tiering is off (plan()'s legacy internal
        erosion, byte-identical)."""
        if not getattr(self._cfg, "corridor_erosion_tiered", False):
            return None
        out = {}
        for iid, f in self._fires.items():
            er = self._eroded_p10(f)
            if er is not None:
                out[iid] = er
        return out

    def _crew_exposure(self, pos):
        """(min_nominal_arrival_s, min_eroded_p10_arrival_s) at `pos` across all
        fires, using the SAME engine samplers the audit/corridor law use. The
        nominal member (members[0]) is sim ground truth — a cell is BURNED once
        it is <= now. The eroded P10 is the pessimistic corridor front — a cell
        is IMMINENT once it is within the egress buffer of now (SAFETY SEMANTICS
        10). The erosion already folds in the earliest burnable arrival within
        corridor_erosion_m, so a crew parked on a developed / non-burnable cell
        still inherits the adjacent front. inf when no fire reaches the cell."""
        nom = UNREACHED
        p10 = UNREACHED
        for fid in self._fire_order:
            f = self._fires[fid]
            if f.ens is None:
                continue
            nv = geo.raster_sample(f.ens.members[0], pos)
            if nv is not None and nv < nom:
                nom = nv
            er = self._eroded_p10(f)
            if er is not None:
                ev = geo.raster_sample(er, pos)
                if ev is not None and ev < p10:
                    p10 = ev
        return nom, p10

    def _cell_unsafe(self, pos, now, lead_s: float = 0.0):
        """(unsafe, burned, imminent) for a cell at time `now` (SAFETY SEMANTICS
        10): BURNED iff the nominal front has already passed (nom <= now),
        IMMINENT iff the eroded P10 front is within egress_buffer_min (+ optional
        proactive `lead_s`) of now. lead_s>0 is the sweep's look-ahead so a crew
        LEAVES a threatened cell one step BEFORE the zero-lead audit would flag
        it — keeping the audit (SEMANTICS 10c) an honest backstop that fires only
        on genuine, unavoidable overrun."""
        nom, p10 = self._crew_exposure(pos)
        buffer_s = self._cfg.egress_buffer_min * 60.0
        burned = (not math.isinf(nom)) and nom <= now
        imminent = (not math.isinf(p10)) and p10 < now + buffer_s + lead_s
        return (burned or imminent), burned, imminent

    def _zone_currently_safe(self, zone, now, lead_s: float = 0.0) -> bool:
        """DYNAMIC SAFE-ZONE VALIDITY (SAFETY SEMANTICS 10b): a zone is a valid
        egress / staging / anchor target only if its OWN cell is not burned and
        its eroded-P10 arrival is at least the egress buffer (+ optional
        proactive `lead_s`) beyond now. A zone the pessimistic front has reached
        (or reaches within the buffer) is INVALID and must never be selected.
        Selection sites (dispatch / adopt) use zero lead; the sweep uses one tick
        of lead so a crew is sent to a zone that will still be safe when it
        arrives, not one about to be overrun itself."""
        if zone is None:
            return False
        unsafe, _b, _i = self._cell_unsafe(zone.loc, now, lead_s=lead_s)
        return not unsafe

    def _currently_safe_zones(self, now, lead_s: float = 0.0):
        """(safe_zones, fail_closed) — the location safe zones still valid at
        `now` (SAFETY SEMANTICS 10b). When at least one is safe, that filtered
        list (never empty). When NONE is safe, FAIL CLOSED to the single
        LATEST-arrival (safest available) zone so a crew is never routed to a
        nearer burned/imminent zone (mirrors safety.egress_choice's no_safe_egress
        fail-closed) — the loud operator alarm is emitted by the sweep."""
        zones = list(self._loc.safe_zones)
        safe = [z for z in zones if self._zone_currently_safe(z, now, lead_s)]
        if safe:
            return safe, False
        # fail closed: the safest zone is the one the pessimistic front reaches
        # LATEST (inf = never reached sorts safest); ties by zone_id.
        best = max(zones, key=lambda z: (self._crew_exposure(z.loc)[1], z.zone_id))
        return [best], True

    @staticmethod
    def _nearest_zone(pos, zones):
        """Nearest zone to `pos` (haversine, tie by zone_id) — the shortest
        escape from a pre-filtered currently-safe candidate list."""
        return min(zones, key=lambda z: (geo.haversine_m(pos, z.loc), z.zone_id))

    # -- SEM-10d ROUTED ESCAPE (founder finding 2026-07-11: two crews burned
    # -- WHILE EGRESSING on the extended-zone Camp-Fire-scale run — the sweep
    # -- picked the NEAREST safe zone without checking the crew could REACH it
    # -- ahead of the front, and when the fire consumed every catalog zone the
    # -- crews sat at the fail-closed zone until overrun) ----------------------

    def _route_points_with_times(self, a, b, speed_mps: float):
        """The ACTUAL escape path a->b as [(LatLon, pass_time_offset_s)] —
        the road route (wgresponse.roads seam, exactly what the crew will
        drive) when a graph is loaded, else the straight tier-0 leg; long
        spans subdivided to ~275 m so the front cannot slip between samples.
        Pass times are cumulative distance / crew ground speed."""
        pts = []
        rr = self._leg_route(a, b)
        if rr is not None and getattr(rr, "segments", None):
            for seg in rr.segments:
                for p in seg.points:
                    q = p if isinstance(p, LatLon) else LatLon(p[0], p[1])
                    if not pts or (pts[-1].lat, pts[-1].lon) != (q.lat, q.lon):
                        pts.append(q)
        if len(pts) < 2:
            pts = [a, b]
        out = [(pts[0], 0.0)]
        t = 0.0
        step = 275.0
        for i in range(1, len(pts)):
            p0, p1 = pts[i - 1], pts[i]
            d = geo.haversine_m(p0, p1)
            if d <= 0.0:
                continue
            n_sub = max(1, int(d // step))
            for k in range(1, n_sub + 1):
                f = k / n_sub
                q = LatLon(p0.lat + (p1.lat - p0.lat) * f,
                           p0.lon + (p1.lon - p0.lon) * f)
                t += (d / n_sub) / max(speed_mps, 0.1)
                out.append((q, t))
        return out

    def _route_metrics(self, a, b, now, speed_mps: float, lead_s: float):
        """(burned, exposure_s, worst_s, total_s, dest_margin_s) for
        traversing a->b along the crew's ACTUAL route, each sample judged at
        its PASS time by the same cell law the sweep applies to a standing
        crew. burned: some sample's nominal front has passed by the crew's
        pass time (a route through the burn is never chosen while any
        alternative exists). exposure_s: total seconds spent on cells whose
        eroded-P10 margin (p10 − (pass + egress buffer + lead)) is negative —
        the exact condition the 10c audit counts as a live overrun.
        worst_s: minimum margin anywhere. total_s: route duration.
        dest_margin_s: the DESTINATION's margin at arrival — the standing
        state after the dash; a zero-length route has zero transit exposure
        but its dest_margin says whether staying there is lawful (scoring by
        transit exposure alone let a crew 'choose' its own unsafe position
        and stand in the alarm forever). (not burned and worst_s >= 0) == the
        whole route is fully lawful (SEM-10 extended from the crew's cell to
        every cell of its escape route)."""
        buffer_s = self._cfg.egress_buffer_min * 60.0
        pts = self._route_points_with_times(a, b, speed_mps)
        burned = False
        exposure = 0.0
        worst = math.inf
        dest_margin = math.inf
        prev_dt = 0.0
        for q, dt in pts:
            pass_t = now + dt
            nom, p10 = self._crew_exposure(q)
            # BURNED = the FLAMING FRONT crosses this cell within the cold-
            # black window of the crew's pass time. Two deliberate carve-outs
            # (both from the park_city stranded-crew finding, 44 ticks in the
            # burn with zero candidates):
            #   dt > 0 — the STARTING cell is exempt (a crew standing in the
            #   burn is exactly the crew that MUST drive out);
            #   COLD BLACK — a cell the front consumed >= _COLD_BLACK_PASS_S
            #   before the crew passes is OLD BURN, passable (the hazard is
            #   the front's flaming residence, minutes — not the black behind
            #   it, which is the classic survival ground). The 10c AUDIT is
            #   unchanged — standing in ANY front-passed cell still reports —
            #   this only lets a black-stranded crew LEAVE.
            if ((not math.isinf(nom)) and nom <= pass_t and dt > 0.0
                    and (pass_t - nom) < self._cold_black_pass_s(q)):
                burned = True
            m = math.inf
            if not math.isinf(p10):
                m = p10 - (pass_t + buffer_s + lead_s)
                worst = min(worst, m)
                if m < 0.0:
                    exposure += max(0.0, dt - prev_dt)
            dest_margin = m                 # last sample = the destination
            prev_dt = dt
        total = pts[-1][1] if pts else 0.0
        return burned, exposure, worst, total, dest_margin

    def _reachable_zone(self, pos, zones, now, speed_mps: float, lead_s: float):
        """LONGEVITY-FIRST lawful zone pick (founder 2026-07-11 'better
        projection so less rerouting'): the fire's persisted arrival field
        already says WHEN every zone will be threatened, so use it AT
        SELECTION time instead of bouncing reactively later. Among zones
        whose ROUTE from `pos` is fully lawful (not burned, worst margin
        >= 0): prefer zones the eroded-P10 front NEVER reaches ('forever'
        zones), nearest first; else the LONGEST-LIVED zone (latest P10
        arrival, distance tiebreak). Nearest-first alone sent crews to zones
        the engine already knew would be overrun within the hour — the
        measured bounce chains (7 relocations per crew on extended Paradise,
        383 across the reactive sweep). None when no catalog zone is safely
        reachable."""
        lawful = []
        for z in sorted(zones,
                        key=lambda z: (geo.haversine_m(pos, z.loc), z.zone_id)):
            burned, _e, worst, _t, _dm = self._route_metrics(
                pos, z.loc, now, speed_mps, lead_s)
            if not burned and worst >= 0.0:
                _nom, p10 = self._crew_exposure(z.loc)
                lawful.append((z, p10, geo.haversine_m(pos, z.loc)))
        if not lawful:
            return None
        forever = [c for c in lawful if math.isinf(c[1])]
        if forever:
            return min(forever, key=lambda c: (c[2], c[0].zone_id))[0]
        return max(lawful, key=lambda c: (c[1], -c[2], c[0].zone_id))[0]

    def _escape_ray_targets(self, pos, now, speed_mps: float):
        """Outward AREA-ESCAPE proposals (SEM-10d): walk 12 fixed bearings
        (deterministic) in ~550 m straight samples out to 12 km (clamped
        inside the tile), ending each ray at the first sample the nominal
        front reaches by the crew's pass time. Returns (bearing, target)
        proposals with >= 2 km of driveable length — GEOMETRY only; the
        caller scores every proposal over the crew's ACTUAL route seam (the
        straight ray is not what the crew drives, which is exactly how the
        original nearest-zone bug slipped through)."""
        spec = self._tile.spec
        lat_hi = spec.north_lat - 2.0 * spec.cell_m / 111_320.0
        lat_lo = spec.north_lat - (spec.nrows - 2) * spec.cell_m / 111_320.0
        lon_w = geo.rc_to_latlon(spec, 0, 1).lon
        lon_e = geo.rc_to_latlon(spec, 0, spec.ncols - 2).lon
        # SEM-10d AREA-ESCAPE ROAD SNAP (contracts escape_road_snap_m,
        # default 0 = off, byte-identical): snap each proposal to a road node
        snap_m = float(getattr(self._cfg, "escape_road_snap_m", 0.0) or 0.0)
        g = (self._road_graph if snap_m > 0.0 and self._road_bbox is not None
             else None)
        out = []
        on_road = []                          # snapped proposals (snap only)
        for bearing in range(0, 360, 30):
            last_ok = None
            safe = [] if g is not None else None     # safe samples (snap only)
            d = 550.0
            while d <= 12_000.0:
                q = geo.destination(pos, float(bearing), d)
                if not (lat_lo < q.lat < lat_hi and lon_w < q.lon < lon_e):
                    break
                pass_t = now + d / max(speed_mps, 0.1)
                nom, _p10 = self._crew_exposure(q)
                if ((not math.isinf(nom)) and nom <= pass_t
                        and (pass_t - nom) < self._cold_black_pass_s(q)):
                    break        # the FRONT is here at pass time: ray ends
                last_ok = q
                if safe is not None:
                    safe.append(q)
                d += 550.0
            if last_ok is not None and geo.haversine_m(pos, last_ok) >= 2_000.0:
                if safe:
                    node = self._snap_escape_target(pos, safe, g, snap_m)
                    if node is not None:
                        on_road.append((bearing, node))
                out.append((bearing, last_ok))
        # snap armed: the flee is ROAD-BOUND whenever any bearing reaches a
        # road — a proposal with no road node in reach (open ground, e.g. the
        # roadless Gore Range NE of East Vail) survives only when none does
        if on_road:
            return on_road
        return out

    @staticmethod
    def _snap_escape_target(pos, safe_samples, g, snap_m: float):
        """SEM-10d AREA-ESCAPE ROAD SNAP (contracts escape_road_snap_m): the
        road-graph node nearest a safe ray sample within snap_m, scanning
        from the ray end (the farthest safe point) back toward the crew, that
        still leaves >= 2 km of drive; None when no sample has one (the raw
        ray point stands). Deterministic (nearest_node ties -> smaller id)."""
        for q in reversed(safe_samples):
            hit = g.nearest_node(q, max_m=snap_m)
            if hit is None:
                continue
            node = g.nodes[hit[0]]
            if geo.haversine_m(pos, node) >= 2_000.0:
                return node
        return None

    def _best_escape(self, pos, now, speed_mps: float, lead_s: float):
        """(target_loc, target_id) for a crew that must move (SEM-10d) — the
        LEAST-EXPOSURE ROUTED choice, never a blind nearest-zone hop:
        1. the nearest catalog zone whose ACTUAL route is fully lawful, else
        2. among ALL catalog zones and the outward escape-ray proposals, the
           candidate minimizing (seconds spent on audit-unsafe cells, then
           route duration) over the crew's ACTUAL route seam, burned-through
           routes excluded. Exposure-time is the objective because it is what
           the 10c audit counts — a max-worst-margin rule was measured to
           pick long detours whose extra time-at-risk TRIPLED standard-tile
           overrun ticks (43 -> 158 across the 60-scenario sweep), else
        3. (every candidate route crosses the burn) the nearest catalog zone —
           genuinely cornered; the audit reports what follows honestly."""
        zones = list(self._loc.safe_zones)
        safe_zones, fail_closed = self._currently_safe_zones(now, lead_s=lead_s)
        if not fail_closed:
            z = self._reachable_zone(pos, safe_zones, now, speed_mps, lead_s)
            if z is not None:
                return z.loc, z.zone_id
        cands = []
        for z in sorted(zones, key=lambda z: z.zone_id):
            burned, expo, _w, total, dm = self._route_metrics(
                pos, z.loc, now, speed_mps, lead_s)
            if not burned:
                cands.append((dm, expo, total, 0, z.zone_id, z.loc))
        for _bearing, tgt in self._escape_ray_targets(pos, now, speed_mps):
            burned, expo, _w, total, dm = self._route_metrics(
                pos, tgt, now, speed_mps, lead_s)
            if not burned:
                cands.append((dm, expo, total, 1, "escape", tgt))
        # DESTINATION SAFETY FIRST (a zero-length route has zero transit
        # exposure — without this gate a crew 'chose' its own unsafe position
        # and stood in the alarm): prefer candidates whose destination is
        # lawful AT ARRIVAL, among them the least transit exposure then the
        # shortest dash; when no destination is lawful, the one that stays
        # lawful LONGEST (max dest margin) with exposure as tiebreak.
        safe_dest = [c for c in cands if c[0] >= 0.0]
        if safe_dest:
            _dm, _e, _t, _k, zid, loc = min(
                safe_dest, key=lambda c: (c[1], c[2], c[3], c[4]))
            return loc, zid
        if cands:
            _dm, _e, _t, _k, zid, loc = min(
                cands, key=lambda c: (-c[0], c[1], c[2], c[3], c[4]))
            return loc, zid
        zfb = self._nearest_zone(pos, zones)
        return zfb.loc, zfb.zone_id

    def _proactive_escape_zone(self, pos, now, speed_mps: float,
                               lead_s: float, pro_s: float):
        """SEM-10e candidate (contracts escape_proactive_lead_min): the zone
        an IDLE crew should decamp to AHEAD of the escape law's imminent
        flag, or None when no qualifying exit exists (then current behavior
        stands — this hook NEVER least-bad hops; that stays with the
        imminent law). Measured motivation (moderate-wind census attribution,
        paradise 8000 m): z1's imminent flag fired at t30 but the last
        fully-lawful road out of the SW pocket died at t12 and the last
        clean-at-arrival one at t27 — the arrival field knew all three times
        from ignition.

        DURABLE: only zones whose own eroded-P10 arrival is beyond the SAME
        proactive horizon (inf = never reached; a zone merely outside the
        45-min window is the treadmill the census trace measured — hop,
        idle, re-flag, hop, each cycle with less room). CLEAN: prefer the
        fully-lawful longevity pick (_reachable_zone: route not burned,
        worst margin >= 0); else accept a route that is not burned AND never
        crosses a nominal-passed cell at its pass time (STRICTER than the
        10d cold-black carve-out — a proactive move must not buy audit
        overrun ticks riding old black; a cornered crew under the imminent
        law still may) with a lawful DESTINATION (dm >= 0 — pessimistic-
        exposed transit is what egressing crews already ride under 10d).
        Longevity-first both times: forever zones nearest-first, else latest
        P10, zone_id tiebreaks — deterministic."""
        bar = now + self._cfg.egress_buffer_min * 60.0 + lead_s + pro_s
        durable = []
        for z in sorted(self._loc.safe_zones, key=lambda z: z.zone_id):
            _nom, zp10 = self._crew_exposure(z.loc)
            if math.isinf(zp10) or zp10 >= bar:
                durable.append((z, zp10))
        if not durable:
            return None
        z = self._reachable_zone(pos, [z for z, _ in durable], now,
                                 speed_mps, lead_s)
        if z is not None:
            return z
        if getattr(self._cfg, "escape_proactive_lawful_only", False):
            # contracts SEM-10e LAWFUL-ONLY: a voluntary move never rides
            # audit-counted (pessimistic-exposed) transit
            return None
        lawful = []
        for z, zp10 in durable:
            burned, _e, _w, _t, dm = self._route_metrics(
                pos, z.loc, now, speed_mps, lead_s)
            if burned or dm < 0.0:
                continue
            # strict nominal-crossing reject (window-free burned test): the
            # 10c audit flags a crew STANDING on any front-passed cell no
            # matter how cold, so a proactive route may not touch one.
            crosses = False
            for q, dt in self._route_points_with_times(pos, z.loc, speed_mps):
                if dt <= 0.0:
                    continue
                qnom, _qp10 = self._crew_exposure(q)
                if (not math.isinf(qnom)) and qnom <= now + dt:
                    crosses = True
                    break
            if crosses:
                continue
            lawful.append((z, zp10, geo.haversine_m(pos, z.loc)))
        if not lawful:
            return None
        forever = [c for c in lawful if math.isinf(c[1])]
        if forever:
            return min(forever, key=lambda c: (c[2], c[0].zone_id))[0]
        return max(lawful, key=lambda c: (c[1], -c[2], c[0].zone_id))[0]

    def _overrun_withdraw(self, cid, now) -> None:
        """SAFETY SEMANTICS 10a: a DEPLOYING crew whose cell the fire has
        overrun aborts its in-progress gel deploy via the existing withdrawal
        law (cause 'zone_overrun') — no ServicedStop, the client is freed
        'withdrawn_unsafe', a loud record lands in state()['withdrawn'] +
        scoreboard.withdrawn + notes. The subsequent flee to a safe zone is done
        by the caller (relocate). Distinct from the departure-deadline
        withdrawals (_recheck_deploy): this fires on the crew's CURRENT cell
        being overrun, not on a projected late departure."""
        cs = self._crew_sims[cid]
        if cs._mode != "deploy" or not cs.itinerary:
            return
        stop = cs.itinerary[0]
        victim = stop["client_id"]
        incident_id = stop["incident_id"]
        buf_min, ember_min = self._stop_rule.get(
            victim, (self._cfg.egress_buffer_min, self._cfg.ember_buffer_min))
        projected = cs._deploy_end_s
        nom, p10 = self._crew_exposure(cs.pos)
        arr = p10 if not math.isinf(p10) else nom
        latest = (arr - (buf_min + ember_min) * 60.0
                  if not math.isinf(arr) else UNREACHED)
        road_eff = cs.road_eff_egress_s() if self._road_graph is not None else 0.0
        for d in cs.abort_deploy():
            self._assigned.discard(d)
            self._superseded.discard(d)
            self._stop_rule.pop(d, None)
        self._unserved_reason[victim] = "withdrawn_unsafe"
        deficit_min = ((projected - latest) / 60.0
                       if not math.isinf(latest) else 0.0)
        self._withdrawn.append({
            "crew_id": cid, "client_id": victim, "incident_id": incident_id,
            "at_s": now, "cause": "zone_overrun",
            "projected_depart_s": projected,
            "latest_depart_s": (None if math.isinf(latest) else latest),
            "rule_buffer_min": buf_min, "rule_ember_min": ember_min,
            "road_eff_egress_min": round(road_eff / 60.0, 4),
            "deficit_min": round(deficit_min, 4),
        })
        self._notes.append(
            f"SAFETY WITHDRAWAL (zone_overrun): {cid} aborted its in-progress "
            f"deploy on {victim} — the pessimistic fire front has reached the "
            f"crew's cell (within the {buf_min:g}+{ember_min:g} min egress "
            f"buffer of the current P10); crew repositioning to a safe zone "
            f"now, gel NOT applied")

    def _crew_safety_sweep(self, lead_s: float) -> None:
        """CONTINUOUS CREW-SAFETY MONITOR (contracts SAFETY SEMANTICS 10a/10b),
        run every tick AFTER advance() and BEFORE _run_audit, over every ARRIVED
        crew (INBOUND reinforcements are off-tile and skipped until arrival).

        Per crew: a DEPLOYING crew is left to the departure-deadline withdrawal
        law (dispatch feasibility + road-egress guard + policy/eta-drift
        re-checks) and force-aborted here ONLY when its cell is physically
        OVERRUN (nominal front passed — 10a 'zone_overrun'), the emergency the
        projected-departure test cannot pre-empt; it then flees. An idle /
        en_route / egressing crew whose cell is unsafe (burned, or eroded-P10
        front within egress_buffer + the proactive `lead_s`) RELOCATES to a
        currently-safe zone (10b — never a burned/imminent zone; fail-closed to
        the latest-arrival zone + a loud alarm when none is safe). Deterministic
        (crews in id order, deterministic zone pick). A crew resting safely
        clears its relocating flag; the leg re-resolves from actual position each
        tick so a relocating crew keeps advancing toward the zone. EXTENDS the
        P10 / corridor / buffer law to every crew's live cell; never weakens it."""
        now = self._now_s
        # relocation TARGETS use one tick of lead too, so a fleeing crew is sent
        # to a zone that will still be safe when it arrives (not one about to be
        # overrun itself); fail-closed to the latest-arrival zone + a loud alarm.
        safe_zones, fail_closed = self._currently_safe_zones(now, lead_s=lead_s)
        if fail_closed and not self._no_safe_zone:
            self._notes.append(
                "CREW SAFETY ALARM (SEMANTICS 10b no_safe_zone): the pessimistic "
                "fire front is within the egress buffer of EVERY configured safe "
                f"zone — pulling all crews to the latest-arrival (safest "
                f"available) zone {safe_zones[0].zone_id}; escape may be "
                "impossible (fail-closed)")
        self._no_safe_zone = fail_closed
        buffer_s = self._cfg.egress_buffer_min * 60.0
        pro_s = getattr(self._cfg, "escape_proactive_lead_min", 0.0) * 60.0
        for cid in _idsort(self._crew_sims):
            cs = self._crew_sims[cid]
            if cs.status == CrewStatus.INBOUND:
                continue
            # a crew at rest is not (any longer) repositioning — telemetry off.
            if cs._mode == "idle":
                self._relocating.pop(cid, None)
            nom, p10 = self._crew_exposure(cs.pos)
            burned = (not math.isinf(nom)) and nom <= now
            imminent = (not math.isinf(p10)) and p10 < now + buffer_s + lead_s
            # DYNAMIC SAFE ZONES (10b): keep a committed crew's escape pointed at
            # a currently-safe zone. A crew whose cell is still safe but whose
            # ADOPTED egress zone the front has overrun (a deploying crew, or one
            # egressing to a zone that just went) is retargeted to the nearest
            # currently-safe zone — its deploy is untouched; only the escape
            # moves. A crew whose own cell is unsafe is handled by the relocate
            # path below instead.
            if (not (burned or imminent) and cs.egress_zone_id
                    and cs.egress_zone_id != "escape"
                    and cs._mode in ("deploy", "egress")):
                z = self._zone_by_id.get(cs.egress_zone_id)
                if z is None or not self._zone_currently_safe(z, now):
                    # SEM-10d: retarget along the LEAST-BAD ROUTED choice
                    # (lawful-route zone, else max-worst-margin zone/escape
                    # ray) — never blindly at the nearest catalog zone.
                    # ESCAPE-TARGET HYSTERESIS: only when meaningfully better.
                    nloc, nid = self._best_escape(cs.pos, now, cs.speed, lead_s)
                    if (nid != cs.egress_zone_id
                            and self._escape_switch_ok(cs, now, lead_s,
                                                       nloc, nid)):
                        cs.retarget_egress(nloc, nid)
            if cs._mode == "deploy":
                # A committed deploy is governed by the departure-deadline
                # withdrawal law (dispatch feasibility + the road-egress guard +
                # the policy/eta-drift re-checks) — a crew that will still finish
                # AND egress within the rule is NOT force-aborted just because the
                # front is within the buffer (it is leaving on time). The sweep
                # force-aborts ONLY when the fire has physically OVERRUN the cell
                # (10a 'zone_overrun'), the emergency the projected-departure
                # test cannot pre-empt; then it flees like any withdrawn crew.
                if not burned:
                    continue
                self._overrun_withdraw(cid, now)     # -> egressing
            # idle / en_route / egressing crew (or a just-overrun ex-deployer):
            # RELOCATE when the live cell is unsafe (burned, or eroded-P10 within
            # the buffer + one tick of proactive lead so it leaves ahead of the
            # zero-lead audit line).
            if not (burned or imminent):
                # SEM-10e PROACTIVE PRE-POCKET ESCAPE (contracts
                # escape_proactive_lead_min): an IDLE crew inside the
                # proactive horizon leaves NOW — while a durable clean exit
                # still exists — instead of waiting for the imminent flag,
                # which the moderate-wind census measured firing 2-3 ticks
                # AFTER the last lawful road out of the paradise pocket
                # burned over (4 crews then idled 11 ticks in the flaming
                # front). No qualifying exit -> unchanged behavior; the
                # imminent law below still owns the emergency.
                if (pro_s > 0.0 and cs._mode == "idle"
                        and (not math.isinf(p10))
                        and p10 < now + buffer_s + lead_s + pro_s):
                    z = self._proactive_escape_zone(cs.pos, now, cs.speed,
                                                    lead_s, pro_s)
                    if z is not None and cs.pos != z.loc:
                        for d in cs.relocate_to(z.loc, z.zone_id):
                            self._assigned.discard(d)
                            self._superseded.discard(d)
                            self._stop_rule.pop(d, None)
                        self._n_relocations += 1
                        self._notes.append(
                            f"crew {cid} proactive escape — the pessimistic "
                            f"front reaches this cell within "
                            f"{(buffer_s + lead_s + pro_s) / 3600.0:.2g}h "
                            f"and a durable exit is still clean (relocating "
                            f"to {z.zone_id})")
                        self._relocating[cid] = "proactive_escape"
                continue
            # SEM-10d ROUTED RELOCATION: the LEAST-EXPOSURE ROUTED choice —
            # never a blind nearest-zone hop through the front (the founder's
            # two-crews-burned-egressing finding on the extended Camp-Fire-
            # scale run). HYSTERESIS: a crew already fleeing keeps its current
            # target while that target is still lawful at (re)arrival —
            # re-picking every tick let alternating 'best' targets zigzag a
            # crew in place for hours (44 exposed ticks on park_city extreme).
            if cid in self._relocating and cs.egress_loc is not None:
                _b, _e, _w, _t, dm_cur = self._route_metrics(
                    cs.pos, cs.egress_loc, now, cs.speed, lead_s)
                if not _b and dm_cur >= 0.0:
                    continue                    # keep fleeing to the same target
            zone_loc, zone_id = self._best_escape(cs.pos, now, cs.speed, lead_s)
            # ESCAPE-TARGET HYSTERESIS (founder churn fix): when the current
            # target is unlawful but the best alternative is not MEANINGFULLY
            # better, keep committed motion instead of zigzagging between
            # near-equal bad options (the audit still owns the scoreboard).
            if (cid in self._relocating
                    and not self._escape_switch_ok(cs, now, lead_s,
                                                   zone_loc, zone_id)):
                continue
            if zone_id == "escape" and not self._area_escape_alarmed:
                self._notes.append(
                    "CREW SAFETY (SEMANTICS 10d area escape): no catalog safe "
                    "zone is safely REACHABLE ahead of the front — crews are "
                    "leaving the operating area along the safest outward bearing")
                self._area_escape_alarmed = True
            if cs.pos == zone_loc:
                # nowhere safer to go (already AT the chosen — possibly
                # fail-closed — zone): let the audit (10c) report the unavoidable
                # overrun; don't churn or claim a phantom relocation.
                continue
            dropped = cs.relocate_to(zone_loc, zone_id)
            for d in dropped:
                # freed for re-planning next tick (a not-yet-serviced stop the
                # fleeing crew abandoned); no terminal reason — it flows back to
                # the job pool and is re-offered while still threatened.
                self._assigned.discard(d)
                self._superseded.discard(d)
                self._stop_rule.pop(d, None)
            if cid not in self._relocating:
                self._n_relocations += 1
                self._notes.append(
                    f"crew {cid} repositioning — zone threatened (relocating to "
                    f"{zone_id})")
            self._relocating[cid] = "zone_overrun" if burned else "zone_threatened"

    # -- CREW SHIFTS (FOUNDER ROUND 6b): fatigue law for multi-day events ------

    def _crew_shift_sweep(self, now: float) -> None:
        """Rest cycles (contracts CREW SHIFTS): a crew that has been on duty
        longer than its shift budget goes OFF_DUTY for cfg.crew_shift_rest_h
        at its current position — but NEVER mid-job (rest starts at its first
        idle tick after the budget runs out; the crew on a roof finishes).
        A resting crew is not dispatchable (_eligible_replan_ids) yet remains
        under the SEM-10 safety sweep — if its cell is threatened it FLEES
        (status flips to EGRESSING for the escape) and resumes resting on
        arrival. First-cycle A/B stagger: odd crews get a half first shift so
        a simultaneous mobilization cannot bed the whole fleet at once.
        Deterministic (id order, pure clock arithmetic). No-op at
        crew_shift_max_on_h == 0 (byte-identical)."""
        cfg = self._cfg
        max_on_s = cfg.crew_shift_max_on_h * 3600.0
        if max_on_s <= 0.0:
            return
        rest_s = cfg.crew_shift_rest_h * 3600.0
        fire_active = bool(self._fires)
        for idx, cid in enumerate(_idsort(self._crew_sims)):
            cs = self._crew_sims[cid]
            if cs.status == CrewStatus.INBOUND:
                continue
            # rest complete -> new full shift
            rest_end = self._resting_until.get(cid)
            if rest_end is not None and now >= rest_end:
                self._resting_until.pop(cid, None)
                self._duty_start[cid] = now
                self._shift_budget[cid] = max_on_s
                if cs.status == CrewStatus.OFF_DUTY:
                    cs.status = CrewStatus.AVAILABLE
                self._notes.append(
                    f"crew {cid} BACK ON SHIFT (rested "
                    f"{cfg.crew_shift_rest_h:g}h)")
                continue
            # still resting: re-impose OFF_DUTY once idle (a fleeing resting
            # crew is EGRESSING mid-escape; it resumes resting on arrival)
            if rest_end is not None:
                if cs.idle() and cs.status != CrewStatus.OFF_DUTY:
                    cs.status = CrewStatus.OFF_DUTY
                continue
            # arm the duty clock at first mobilization
            if fire_active and cid not in self._duty_start:
                self._duty_start[cid] = now
                self._shift_budget[cid] = (max_on_s if idx % 2 == 0
                                           else max_on_s * 0.5)
            start = self._duty_start.get(cid)
            if start is None:
                continue
            budget = self._shift_budget.get(cid, max_on_s)
            if (now - start) >= budget and cs.idle():
                # HOLD-UNTIL-RELIEVED: never empty the fleet — rest waits for
                # another on-duty crew unless this crew is 2x over budget (the
                # hard fatigue cap; then it rests regardless, loudly). Earlier
                # ids decide first each tick (deterministic), so a later crew
                # sees the earlier one's fresh rest and defers.
                others_on = any(
                    oid != cid
                    and oid not in self._resting_until   # a fleeing resting
                    # crew is transiently EGRESSING but is NOT on duty
                    and self._crew_sims[oid].status not in
                    (CrewStatus.INBOUND, CrewStatus.OFF_DUTY)
                    for oid in self._crew_sims)
                relief_coming = any(oid != cid for oid in self._resting_until)
                # the hard cap bypasses hold-until-relieved ONLY when relief
                # is impossible (nobody else on duty AND nobody resting who
                # will return) — a resting teammate IS incoming relief, so the
                # over-budget crew stands watch until the swap (real ops).
                hard_over = ((now - start) >= 2.0 * budget
                             and not relief_coming)
                if not others_on and not hard_over:
                    continue                     # hold until relieved
                self._resting_until[cid] = now + rest_s
                cs.status = CrewStatus.OFF_DUTY
                self._notes.append(
                    f"crew {cid} OFF SHIFT (fatigue law: "
                    f"{(now - start) / 3600.0:.1f}h on duty"
                    f"{', HARD CAP — no relief available' if hard_over and not others_on else ''}"
                    f") — resting {cfg.crew_shift_rest_h:g}h at its current "
                    f"safe position")

    # -- FOUNDER ROUND 5: COATING HOLD (>24h defer + preposition) --------------

    def _apply_coating_hold(self, jobs, crew_args, ens_map, plan_zones,
                            forecasts, now):
        """Split `jobs` per the COATING HOLD law (contracts FOUNDER ROUND 5):
        a job whose fire is more than cfg.dispatch_hold_h hours away (P50) is
        HELD — not coated yet — as long as the deferred coverage plan still
        serves EVERY home the coat-now plan would serve, with held deadlines
        tightened by cfg.dispatch_hold_weather_buffer_h (forecast-change
        buffer). Returns (plan_jobs, probe_plan_or_None, held_ids).

        The gate runs BOTH probes with the real planner (same cfg, zones,
        road provider): probe NOW = all jobs as-is; probe HOLD = held jobs
        pushed to their release time (p50 − hold_h) with buffered deadlines.
        served(NOW) ⊆ served(HOLD) keeps the hold; any home the deferral
        would sacrifice RELEASES it — latched (self._hold_released), so
        coating never stop-starts. The probe-HOLD plan is also the
        prepositioning source (each crew's deferred first stop)."""
        cfg = self._cfg
        hold_s = cfg.dispatch_hold_h * 3600.0
        if hold_s <= 0.0 or self._hold_released or not jobs:
            self._hold_active_n = 0
            return jobs, None, set()
        held = [j for j in jobs if (j.eta.eta_p50_s - now) > hold_s]
        if not held:
            self._hold_active_n = 0
            return jobs, None, set()
        held_ids = {j.client.client_id for j in held}
        buf_s = cfg.dispatch_hold_weather_buffer_h * 3600.0
        probe_now = dispatch.plan(crew_args, jobs, ens_map, plan_zones, cfg,
                                  now, None, forecasts=forecasts,
                                  road_provider=self._plan_road_provider(),
                                  eroded_rasters=self._plan_eroded_rasters())
        # The probe must encode deferral + buffer through job.eta — plan()
        # RE-DERIVES every job's windows from eta via
        # safety.derive_job_windows (field edits are overwritten):
        #   p10' = p10 − buffer      -> latest_depart' = latest − buffer
        #   p50' = p50 + (gel_window_h − hold_h)
        #                            -> earliest_useful' = true_p50 − hold_h
        #                               (the hold-release moment)
        # p90 keeps its offset from p50 (ordering preserved). Count credit is
        # freshness-independent, so the inflated p50' cannot change which
        # homes the probe serves — only WHEN they may start. (Caveat: under
        # gel_env_model_enabled the derived window is wx-scaled and the p50
        # offset is approximate; that model is default-OFF and inapplicable
        # to GraphGel's dried-film mechanism per contracts block 5.)
        p50_shift = (cfg.gel_window_h - cfg.dispatch_hold_h) * 3600.0
        deferred = []
        for j in jobs:
            if j.client.client_id in held_ids:
                e = j.eta
                deferred.append(replace(j, eta=replace(
                    e,
                    eta_p10_s=e.eta_p10_s - buf_s,
                    eta_p50_s=e.eta_p50_s + p50_shift,
                    eta_p90_s=e.eta_p90_s + p50_shift)))
            else:
                deferred.append(j)
        probe_hold = dispatch.plan(crew_args, deferred, ens_map, plan_zones,
                                   cfg, now, None, forecasts=forecasts,
                                   road_provider=self._plan_road_provider(),
                                   eroded_rasters=self._plan_eroded_rasters())
        def served_ids(p):
            return {s.client_id for cp in p.plans.values() for s in cp.stops}
        s_now = served_ids(probe_now)
        s_hold = served_ids(probe_hold)
        if not (s_now <= s_hold):
            # deferral would sacrifice a coatable home -> release, forever
            self._hold_released = True
            self._hold_active_n = 0
            lost = sorted(s_now - s_hold)
            self._notes.append(
                f"COATING HOLD RELEASED — deferring past the "
                f"{cfg.dispatch_hold_h:g}h line (with the "
                f"{cfg.dispatch_hold_weather_buffer_h:g}h weather buffer) "
                f"would sacrifice {len(lost)} home(s) ({', '.join(lost[:4])}"
                f"{'…' if len(lost) > 4 else ''}) — coating begins now")
            return jobs, None, set()
        if not self._hold_noted:
            self._notes.append(
                f"COATING HOLD active (FOUNDER ROUND 5): fire > "
                f"{cfg.dispatch_hold_h:g}h out for {len(held_ids)} home(s) — "
                f"crews on alert and prepositioning; full coverage verified "
                f"each cycle with a "
                f"{cfg.dispatch_hold_weather_buffer_h:g}h weather buffer")
            self._hold_noted = True
        self._hold_active_n = len(held_ids)
        for cid in held_ids:
            self._unserved_reason[cid] = "held"
        active = [j for j in jobs if j.client.client_id not in held_ids]
        return active, probe_hold, held_ids

    def _preposition_crews(self, probe_hold, eligible_ids, now, step):
        """Move IDLE crews toward their deferred work while a COATING HOLD is
        active: each crew with a first stop in the probe-HOLD plan stages at
        the currently-safe, lawfully-reachable zone NEAREST that stop (tie by
        zone_id; >250 m improvement required so staging never churns). Uses
        the ordinary egress movement machinery — SEM-10 continues to govern
        (an unsafe staging cell still triggers the sweep's relocation, and a
        prepositioning crew's route was lawfulness-checked here first)."""
        zones, fail_closed = self._currently_safe_zones(now)
        if fail_closed:
            return
        for cid in eligible_ids:
            cs = self._crew_sims[cid]
            if not cs.idle():
                continue
            cp = probe_hold.plans.get(cid)
            if cp is None or not cp.stops:
                continue
            first_cl = self._client_by_id.get(cp.stops[0].client_id)
            if first_cl is None:
                continue
            best = None
            for z in zones:
                burned, _e, worst, _t, _dm = self._route_metrics(
                    cs.pos, z.loc, now, cs.speed, step)
                if burned or worst < 0.0:
                    continue
                key = (geo.haversine_m(z.loc, first_cl.loc), z.zone_id)
                if best is None or key < best[0]:
                    best = (key, z)
            if best is None:
                continue
            z = best[1]
            if self._preposition_target.get(cid) == z.zone_id:
                continue
            if geo.haversine_m(cs.pos, z.loc) <= 250.0:
                self._preposition_target[cid] = z.zone_id
                continue
            cs.relocate_to(z.loc, z.zone_id)
            self._preposition_target[cid] = z.zone_id
            self._notes.append(
                f"crew {cid} PREPOSITIONING to {z.zone_id} (coating hold — "
                f"staging near its deferred first stop "
                f"{cp.stops[0].client_id})")

    # -- the tick (the REAL cycle path) ----------------------------------------

    def _eligible_replan_ids(self) -> list:
        """Crews that may be (re)adopted this tick (LAW 3 FULL UTILIZATION):
        AVAILABLE (idle) or EN_ROUTE (travelling, not yet on-site). A DEPLOYING
        crew's in-progress stop is sacred (never yanked) and an EGRESSING crew
        is committed to its escape, so neither is eligible — their committed
        work stays put. SEMANTICS 9: an INBOUND reinforcement is NOT available
        (still mobilizing / off-map), so it is excluded even though its mode is
        'idle' — it never enters the job pool or a dispatch plan and is never an
        idle-with-work failure of the full-utilization law."""
        return [cid for cid in _idsort(self._crew_sims)
                if self._crew_sims[cid]._mode in ("idle", "travel")
                and self._crew_sims[cid].status != CrewStatus.INBOUND
                # CREW SHIFTS (ROUND 6b): a resting crew is never dispatchable
                and self._crew_sims[cid].status != CrewStatus.OFF_DUTY]

    def _incumbent_from_crews(self, eligible_ids, now: float):
        """Reconstruct a DispatchPlan from the eligible crews' CURRENT
        itineraries so dispatch's commitment horizon / churn / anti-
        concentration actually engage (LAW 3): near-term committed stops
        (service_start_s < now + commitment_horizon) FREEZE in place while
        later stops rebalance onto crews that would otherwise idle. Only
        eligible crews contribute — their non-frozen stops are exactly the ones
        the job pool re-offers. None when no eligible crew holds any stop (a
        cold plan — byte-identical to the pre-LAW-3 incumbent=None path)."""
        plans = {}
        horizon_s = now + self._cfg.commitment_horizon_min * 60.0
        for cid in eligible_ids:
            cs = self._crew_sims[cid]
            if not cs.itinerary:
                continue
            stops = [PlannedStop(
                client_id=s["client_id"], incident_id=s["incident_id"],
                arrive_s=s.get("planned_arrive_s", s["service_start_s"]),
                service_start_s=s["service_start_s"],
                depart_s=s.get("planned_depart_s",
                               s["service_start_s"] + s["deploy_s"]),
                latest_depart_s=s["latest_depart_s"],
                margin_h=s.get("planned_margin_h", 0.0),
                gel_fresh_frac=s["gel_fresh_frac"]) for s in cs.itinerary]
            plans[cid] = CrewPlan(crew_id=cid, stops=stops,
                                  egress_zone_id=cs.egress_zone_id,
                                  committed_until_s=horizon_s)
        if not plans:
            return None
        return DispatchPlan(computed_at_s=now, plans=plans)

    def tick(self) -> dict:
        """Advance one cfg.replan_period_s step (scaled by speed) through the
        real engine cycle: beacons -> perimeter-seeded ensemble_arrival ->
        in-flight-deploy safety re-checks (policy-superseded stops + the
        road-egress departure guard in road sessions) -> ThreatAssessments ->
        AlertEngine.evaluate -> dispatch.plan(forecasts=) -> kinematic crew
        movement on road-following legs -> SAFETY AUDIT. Returns state()."""
        self._cmd_log.append({"cmd": "tick"})
        if self._paused:
            return self.state()
        cfg = self._cfg
        now = self._now_s
        step = cfg.replan_period_s * self._speed

        # 0. REINFORCEMENT ARRIVAL (SEMANTICS 9): an INBOUND crew whose
        # mobilization clock has elapsed (now >= available_at_s) ARRIVES —
        # flip to AVAILABLE and PLACE it at its staging safe zone (home_base,
        # round-robin assigned at construction). From here it is an ordinary
        # dispatch-eligible crew: it moves, deploys and is audited like any
        # in-zone crew. Pure function of (ignition time + delay) vs the tick
        # clock -> deterministic and byte-identical on scrub replay. Runs
        # before the beacon ingest so the tracker/trails record the arrival.
        arrived = set()
        for cid in _idsort(self._crew_sims):
            cs = self._crew_sims[cid]
            if cs.status != CrewStatus.INBOUND:
                continue
            avail = self._crew_available_at.get(cid)
            if avail is None or now < avail:
                continue
            cs.pos = cs.crew.home_base          # staging safe zone
            cs.status = CrewStatus.AVAILABLE
            cs._mode = "idle"
            cs._leg = None
            arrived.add(cid)                    # ROADS LEGIBILITY: a jump

        # 1. crew beacons -> the real tracker (+ console trails)
        for cid in _idsort(self._crew_sims):
            cs = self._crew_sims[cid]
            self._tracker.ingest(CrewBeacon(crew_id=cid, loc=cs.pos, t_s=now,
                                            status=cs.status,
                                            speed_mps=cs.speed))
            self._tracks[cid].append({"t_s": now, "lat": round(cs.pos.lat, 6),
                                      "lon": round(cs.pos.lon, 6),
                                      "status": cs.status.value})

        # 2. incident snapshots + PERSISTED absolute arrival fields.
        # The fire's ens is anchored (point seed at the TRUE ignition time)
        # and reused tick over tick — burned/bands are threshold sweeps of it
        # against sim-now, so growth can never stall on tick granularity.
        # Recompute ONLY on: first tick of a fire, a world change
        # (ignite/set_wind bumped _wx_epoch), or the anchor nearing the
        # forecast horizon. Re-basing every tick discarded sub-tick front
        # progress and froze slow-fuel/coarse-cell fires (founder bug
        # 2026-07-10, both the point-seed and ring phases).
        # EXACT RE-ANCHOR (wind-dial finding 2026-09-25): a fire's FIRST
        # field starts from its ignition point at its ignition time; every
        # later recompute re-anchors at `now` from the EXACT nominal burned
        # mask — every consumed cell seeds at `now` (_burned_seed_points)
        # without re-throwing its ember (seed_spotting=False), so the burn so
        # far carries over cell for cell and all new growth runs under the
        # new weather. The traced ring stays the incident's perimeter RECORD
        # only: filled as a perimeter polygon it self-intersected on spot-
        # fire islands / concave burns (angle-sorted around the origin), so a
        # mid-run wind change un-burned 13-54% of a 1-3 h burn and stamped
        # unburned wedges burned; and a fire too young for a ring (< 3
        # boundary cells) was re-run from its ignition time, rewriting its
        # past under the new wind (scratch_review/wind_direction_probe.py).
        incidents = []
        horizon_rebase_s = 0.8 * cfg.horizon_h * 3600.0
        for fid in self._fire_order:
            f = self._fires[fid]
            rings = self._perimeter_rings(f, now)
            inc = FireIncident(
                incident_id=fid, name=f"SIM {self._loc.key} {fid}",
                source=IncidentSource.MOCK, origin=f.origin,
                updated_at_s=now, discovered_at_s=f.t_ignite_s, active=True,
                perimeter=rings)
            stale_anchor = (f.ens is not None
                            and (now - f.anchor_t0_s) > horizon_rebase_s)
            if f.ens is None or f.wx_epoch != self._wx_epoch or stale_anchor:
                if f.ens is None:
                    # first field: point seed at the TRUE ignition time
                    # (rings is None here — no field, no ring)
                    anchor = f.t_ignite_s
                    f.ens = spread.ensemble_arrival(
                        self._tile, inc.ignition_points(), self._wx, anchor,
                        cfg, incident_id=fid, perimeter=None,
                        perimeter_at_s=None)
                else:
                    anchor = now
                    seeds = self._burned_seed_points(f, now) or [f.origin]
                    # seed_spotting=False: each kept cell threw its one ember
                    # in the field it burned in; re-throwing them fired an
                    # ember shower from the whole burn at every wind change
                    f.ens = spread.ensemble_arrival(
                        self._tile, seeds, self._wx, anchor, cfg,
                        incident_id=fid, seed_spotting=False)
                f.wx_epoch = self._wx_epoch
                f.anchor_t0_s = anchor
            incidents.append(inc)

        # 2b. SAFETY RE-CHECK of in-flight deploys against THIS tick's fresh
        # forecasts (BEFORE crew movement, so the decision and this tick's
        # audit share the same p10 rasters):
        #   * policy-superseded stops (tightened rule): eta_drift — the
        #     newest forecast must still allow the projected departure; the
        #     moment it would not, the crew withdraws NOW (unchanged
        #     semantics);
        #   * ROAD sessions: the road-egress departure guard on EVERY
        #     in-flight deploy — projected departure + max(tier-0, road)
        #     egress must still clear the safe zone by the live
        #     latest-depart (module docstring ROADS nuance).
        if self._superseded or self._road_graph is not None:
            for cid in _idsort(self._crew_sims):
                cs = self._crew_sims[cid]
                if cs._mode != "deploy" or not cs.itinerary:
                    continue
                if cs.itinerary[0]["client_id"] in self._superseded:
                    if self._recheck_deploy(cid, cause="eta_drift"):
                        continue
                if self._road_graph is not None:
                    self._recheck_deploy(cid, cause="road_egress")

        # 3. threat assessments -> REAL alert ladder
        pairs = []
        etas = {}
        for inc in incidents:
            ens = self._fires[inc.incident_id].ens
            for cl in self._clients:
                # law 6 (EMBER / ADJACENCY THREAT): a developed (NB 91/93) home
                # whose OWN cell is UNREACHED inherits the earliest burnable
                # arrival within cfg.ember_threat_radius_m, so fire-adjacent
                # developed homes get a finite ETA -> alerts -> dispatch jobs.
                eta = spread.eta_at(
                    ens, cl.loc,
                    fallback_radius_m=cfg.ember_threat_radius_m)
                etas[(cl.client_id, inc.incident_id)] = eta
                pairs.append(ThreatAssessment(client_id=cl.client_id,
                                              incident_id=inc.incident_id,
                                              eta=eta, computed_at_s=now))
        meta = {inc.incident_id: {"name": inc.name} for inc in incidents}
        for ev in self._alerts.evaluate(pairs, now, incident_meta=meta):
            self._alert_feed.append(ev)

        # per-client rollup eta (earliest across fires) for the UI
        self._client_etas = {}
        for (cid, _iid), eta in etas.items():
            cur = self._client_etas.get(cid)
            if cur is None or eta.eta_p10_s < cur.eta_p10_s:
                self._client_etas[cid] = eta

        # 4. jobs for threatened clients -> REAL dispatch.plan (LAW 3 FULL
        # UTILIZATION). The job pool is every threatened, gel-enrolled,
        # not-yet-serviced client whose stop is NOT "protected" this tick:
        # protected = committed to a crew we will NOT re-adopt (deploying /
        # egressing) OR a FROZEN near-term stop on an eligible crew
        # (service_start_s < now + commitment_horizon). Everything else — the
        # genuinely unassigned AND an eligible (idle/en_route) crew's non-frozen
        # backlog — is offered, so a backlog committed to one crew rebalances
        # onto crews that would otherwise idle. Dispatch keeps the frozen /
        # in-flight prefixes via the incumbent (below).
        serviced_ids = {s.client_id for s in self._serviced}
        eligible_ids = self._eligible_replan_ids()
        eligible_set = set(eligible_ids)
        frozen_cut_s = now + cfg.commitment_horizon_min * 60.0
        protected = set()
        for cid in _idsort(self._crew_sims):
            cs = self._crew_sims[cid]
            for st in cs.itinerary:
                if (cid not in eligible_set
                        or st["service_start_s"] < frozen_cut_s):
                    protected.add(st["client_id"])
        jobs = []
        for inc in incidents:
            for cl in self._clients:
                if not cl.graphgel_enrolled:
                    continue
                if cl.client_id in serviced_ids or cl.client_id in protected:
                    continue
                eta = etas[(cl.client_id, inc.incident_id)]
                if math.isinf(eta.eta_p50_s) or math.isinf(eta.eta_p10_s):
                    continue
                if (eta.eta_p50_s - now) / 3600.0 > cfg.notice_h:
                    continue
                self._jobbed.setdefault(cl.client_id, inc.incident_id)
                if eta.eta_p50_s <= now:
                    self._unserved_reason.setdefault(cl.client_id, "fire_arrived")
                    continue
                latest_depart = safety.latest_departure_s(eta.eta_p10_s, cfg)
                jobs.append(DispatchJob(
                    client=cl, incident_id=inc.incident_id, eta=eta,
                    latest_depart_s=latest_depart,
                    latest_service_start_s=latest_depart - cfg.deploy_min * 60.0,
                    earliest_useful_start_s=max(
                        now, eta.eta_p50_s - cfg.gel_window_h * 3600.0)))
        # Re-plan when an eligible crew is IDLE and a feasible pending job
        # exists (no eligible crew may idle while savable work waits). The
        # trigger stays keyed on an idle crew — a fully busy fleet is left on
        # its committed plan (no churn), which keeps single-busy-crew scenarios
        # byte-identical to the pre-LAW-3 behaviour.
        idle_exists = any(self._crew_sims[cid].idle() for cid in eligible_ids)
        if jobs and eligible_ids and idle_exists:
            crew_args = []
            for cid in eligible_ids:
                cs = self._crew_sims[cid]
                crew_args.append(Crew(
                    crew_id=cid, name=cs.crew.name, status=CrewStatus.AVAILABLE,
                    last_beacon=CrewBeacon(crew_id=cid, loc=cs.pos, t_s=now,
                                           status=CrewStatus.AVAILABLE),
                    home_base=cs.crew.home_base))
            ens_map = {fid: self._fires[fid].ens for fid in self._fire_order}
            forecasts = {fid: self._wx for fid in self._fire_order}
            incumbent = self._incumbent_from_crews(eligible_ids, now)
            # DYNAMIC SAFE ZONES (SAFETY SEMANTICS 10b): dispatch may only stage
            # / egress crews at zones that are STILL currently safe; a zone the
            # front has overrun is filtered out (fail-closed to the safest zone
            # when none remains) so a plan never routes a crew into the fire.
            plan_zones, _fc = self._currently_safe_zones(now)
            # FOUNDER ROUND 5 COATING HOLD: jobs whose fire is > hold_h away
            # are withheld from the emitted plan while (and only while) the
            # per-cycle feasibility gate proves deferral costs no home; idle
            # crews preposition toward their deferred first stops instead.
            plan_jobs, probe_hold, held_ids = self._apply_coating_hold(
                jobs, crew_args, ens_map, plan_zones, forecasts, now)
            if plan_jobs:
                plan = dispatch.plan(crew_args, plan_jobs, ens_map,
                                     plan_zones, cfg, now,
                                     incumbent, forecasts=forecasts,
                                     road_provider=self._plan_road_provider(),
                                     eroded_rasters=self._plan_eroded_rasters())
                self._adopt(plan, plan_jobs, now, eligible_ids)
                for uj in plan.unserved:
                    self._unserved_reason[uj.client_id] = uj.reason
            if probe_hold is not None:
                self._preposition_crews(probe_hold, eligible_ids, now, step)

        # 5. kinematic crew movement (sim.py physics; legs follow roads when
        # a graph is loaded) + service completion
        jumps = self._map_jumps(arrived)     # display only (ROADS LEGIBILITY)
        for cid in _idsort(self._crew_sims):
            self._crew_sims[cid].advance(now, now + step, self._serviced)
        self._record_map_paths(jumps)        # display only (ROADS LEGIBILITY)

        # 5b. drain ROAD-EGRESS departure-guard withdrawals raised inside
        # advance() (arrival-time guard) into the loud withdrawal channel —
        # same bookkeeping as _recheck_deploy: resolve toward safety, never
        # silently keep, never silently drop.
        for cid in _idsort(self._crew_sims):
            for w in self._crew_sims[cid].drain_withdrawals():
                victim = w["client_id"]
                buf_min, ember_min = self._stop_rule.get(
                    victim, (cfg.egress_buffer_min, cfg.ember_buffer_min))
                for d in w["dropped"]:
                    self._assigned.discard(d)
                    self._superseded.discard(d)
                self._stop_rule.pop(victim, None)
                self._unserved_reason[victim] = "withdrawn_unsafe"
                deficit_min = (w["projected_depart_s"]
                               - w["latest_depart_s"]) / 60.0
                self._withdrawn.append({
                    "crew_id": cid, "client_id": victim,
                    "incident_id": w["incident_id"], "at_s": w["at_s"],
                    "cause": "road_egress",
                    "projected_depart_s": w["projected_depart_s"],
                    "latest_depart_s": w["latest_depart_s"],
                    "rule_buffer_min": buf_min, "rule_ember_min": ember_min,
                    "road_eff_egress_min": round(
                        w["road_eff_egress_min"], 4),
                    "deficit_min": round(deficit_min, 4),
                })
                self._notes.append(
                    f"SAFETY WITHDRAWAL (road_egress): {cid} aborted its "
                    f"deploy on {victim} — the ROAD egress "
                    f"({w['road_eff_egress_min']:.1f} min effective) cannot "
                    f"clear the safe zone by latest-depart (deficit "
                    f"{deficit_min:.1f} min vs current P10); crew egressing "
                    f"now, gel NOT applied")

        # policy-superseded marks die with their deploy (completed above;
        # withdrawn ones were already discarded by _recheck_deploy / 5b)
        if self._superseded:
            self._superseded &= {
                cs.itinerary[0]["client_id"]
                for cs in self._crew_sims.values()
                if cs._mode == "deploy" and cs.itinerary}

        # 6. clock advance FIRST — the CONTINUOUS CREW-SAFETY sweep and the
        # SAFETY AUDIT both judge each crew at its CURRENT post-advance time
        # (= the sim-now the UI and the founder's repro see), so a crew that
        # moved into the fire this step is evaluated where and WHEN it actually
        # is. The serviced-stop audit is time-reference-free (absolute departure
        # vs absolute p10-minus-rule), so moving the clock ahead of it is
        # byte-identical for that half; only the new crew-overrun half reads now.
        self._now_s = now + step
        self._tick_n += 1

        # 6a. CREW SHIFTS (ROUND 6b fatigue law): rest/return transitions run
        # BEFORE the safety sweep so a crew that just went OFF_DUTY is already
        # in its resting state when the sweep checks its cell (a threatened
        # resting crew still flees — the sweep outranks rest).
        self._crew_shift_sweep(self._now_s)

        # 6b. CONTINUOUS CREW-SAFETY SWEEP (SAFETY SEMANTICS 10a/10b): relocate
        # every arrived crew whose live cell the fire threatens, BEFORE the
        # audit, with one tick of proactive lead (step) so a crew leaves a
        # threatened zone ahead of the zero-lead audit line — the sweep's job is
        # to keep the 10c backstop from firing except on genuinely unavoidable
        # overrun.
        self._crew_safety_sweep(lead_s=step)

        # 6c. SAFETY AUDIT (SAFETY SEMANTICS 10c backstop): every serviced stop's
        # departure AND any arrived crew standing in the burn — scoreboard
        # violations can therefore never read 0 while a crew is overrun.
        self._run_audit()

        st = self.state()
        self._record_history(st)   # LAW 1 TIME-SCRUB: record the emitted tick
        return st

    def _adopt(self, plan, jobs, now: float, eligible_ids) -> None:
        """Apply a global re-plan (LAW 3 FULL UTILIZATION) to the ELIGIBLE
        crews (idle / en_route) and capture the per-stop safety rule for the
        audit. Deploying/egressing crews are never eligible, so their in-flight
        stop and committed backlog are never touched here.

        Preserves the frozen-stop commitment law via dispatch (the incumbent
        keeps every near-term / in-flight prefix first on its crew) and adds a
        SKIP-IF-UNCHANGED guard: a crew whose client sequence is identical
        keeps its LIVE leg (no kinematic reset — byte-identical continuation),
        a changed crew re-adopts from its actual position, and a crew dropped
        to empty returns cleanly to idle. Old eligible-crew assignments are
        released first, so a client the new plan abandons (triage/capacity)
        falls back to unassigned with the dispatch reason."""
        cfg = self._cfg
        eligible = set(eligible_ids)
        job_by_client = {j.client.client_id: j for j in jobs}
        rule = (cfg.egress_buffer_min, cfg.ember_buffer_min)

        # new sim-stop itinerary per eligible crew (fresh dispatch timings)
        new_by_crew = {cid: [] for cid in eligible_ids}
        snapshot_stops = []
        for cid in _idsort(plan.plans):
            if cid not in eligible:
                continue
            for st in plan.plans[cid].stops:
                cl = self._client_by_id.get(st.client_id)
                job = job_by_client.get(st.client_id)
                if cl is None:
                    continue
                new_by_crew[cid].append({
                    "client_id": st.client_id, "incident_id": st.incident_id,
                    "loc": cl.loc, "service_start_s": st.service_start_s,
                    "deploy_s": cfg.deploy_min * 60.0,
                    "latest_depart_s": st.latest_depart_s,
                    "gel_fresh_frac": st.gel_fresh_frac,
                    "eta_p50_s": (job.eta.eta_p50_s if job is not None
                                  else st.service_start_s),
                    "planned_arrive_s": st.arrive_s,
                    "planned_depart_s": st.depart_s,
                    "planned_margin_h": st.margin_h,
                })
                snapshot_stops.append({
                    "crew_id": cid, "client_id": st.client_id,
                    "incident_id": st.incident_id, "arrive_s": st.arrive_s,
                    "service_start_s": st.service_start_s,
                    "depart_s": st.depart_s,
                    "latest_depart_s": st.latest_depart_s,
                    "margin_h": round(st.margin_h, 4),
                    "gel_fresh_frac": round(st.gel_fresh_frac, 4),
                })

        # release the eligible crews' OLD assignments (re-registered below from
        # whatever each crew actually ends up holding)
        for cid in eligible_ids:
            for st in self._crew_sims[cid].itinerary:
                self._assigned.discard(st["client_id"])

        egress_by_crew = {}
        for cid in eligible_ids:
            cs = self._crew_sims[cid]
            new_stops = new_by_crew.get(cid, [])
            old_seq = [s["client_id"] for s in cs.itinerary]
            new_seq = [s["client_id"] for s in new_stops]
            if new_seq != old_seq:
                if new_stops:
                    zone = self._zone_by_id.get(plan.plans[cid].egress_zone_id)
                    # DYNAMIC SAFE ZONES (SAFETY SEMANTICS 10b): the fallback
                    # egress anchor must be a CURRENTLY-SAFE zone (dispatch was
                    # already handed only safe zones; this guards the no-match
                    # path) — never one the front has overrun.
                    if zone is None or not self._zone_currently_safe(zone, now):
                        safe_now, _fc = self._currently_safe_zones(now)
                        zone = min(safe_now,
                                   key=lambda z: (geo.haversine_m(
                                       new_stops[-1]["loc"], z.loc), z.zone_id))
                    cs.adopt(new_stops, zone.loc, zone.zone_id)
                    # back to productive work -> no longer repositioning (10a)
                    self._relocating.pop(cid, None)
                else:
                    cs.revoke_unstarted()      # dropped -> clean return to idle
            # else: unchanged sequence -> keep the live leg untouched.
            # re-register _assigned + the audit rule for whatever the crew holds
            for s in cs.itinerary:
                self._assigned.add(s["client_id"])
                self._stop_rule[s["client_id"]] = rule
            egress_by_crew[cid] = cs.egress_zone_id

        self._plan_snapshot = {
            "computed_at_s": now,
            "stops": snapshot_stops,
            "egress_zone_by_crew": egress_by_crew,
            "unserved": [{"client_id": u.client_id,
                          "incident_id": u.incident_id, "reason": u.reason}
                         for u in plan.unserved],
        }

    def _burned_seed_points(self, fire: _SimFire, now: float) -> list:
        """EXACT re-anchor seed set (tick() step 2): the center of every cell
        the NOMINAL member has consumed by `now`, row-major (deterministic).

        Seeded at `now` through spread's ignition-point path, each such cell
        holds arrival == now in every ensemble member (nothing can arrive
        before the anchor, so a seed is as final as a perimeter-interior
        cell — never relaxed, never re-burned by a spot landing) and the
        front advances from the mask's true edge. tick() passes
        seed_spotting=False: every seed already threw its one ember in the
        field it burned in (re-throwing them showered the new downwind side
        from the whole burn at each wind change). Cell centers round-trip
        exactly through geo.latlon_to_rc, so the carried-over burned mask
        equals the old one cell for cell — what the traced ring filled as a
        polygon did not (see tick())."""
        nominal = fire.ens.members[0]
        spec = nominal.spec
        nc = spec.ncols
        return [geo.rc_to_latlon(spec, i // nc, i % nc)
                for i, v in enumerate(nominal.data) if v <= now]

    def _perimeter_rings(self, fire: _SimFire, now: float):
        """Growing-incident perimeter: boundary cells of the burned mask
        (nominal-member arrival <= now), angle-sorted around the origin and
        subsampled to <= 200 vertices (threat.py convention). None while the
        fire is too young to have a >= 3-vertex ring (point ignition).
        The incident's perimeter RECORD only: re-anchors seed from
        _burned_seed_points, never from filling this ring — angle-sorting
        spot-fire islands and concave burns into one ring makes it self-
        intersect (tick() step 2)."""
        if fire.ens is None:
            return None
        nominal = fire.ens.members[0]
        spec = nominal.spec
        nr, nc = spec.nrows, spec.ncols
        data = nominal.data
        pts = []
        for r in range(nr):
            base = r * nc
            for c in range(nc):
                if data[base + c] > now:
                    continue
                edge = False
                for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    rr, cc = r + dr, c + dc
                    if not (0 <= rr < nr and 0 <= cc < nc):
                        edge = True
                        break
                    if data[rr * nc + cc] > now:
                        edge = True
                        break
                if edge:
                    pts.append(geo.rc_to_latlon(spec, r, c))
        if len(pts) < 3:
            return None
        o = fire.origin
        cosl = math.cos(math.radians(o.lat))
        pts.sort(key=lambda p: (math.atan2(p.lat - o.lat,
                                           (p.lon - o.lon) * cosl),
                                p.lat, p.lon))
        if len(pts) > _MAX_PERIMETER_VERTICES:
            stride = math.ceil(len(pts) / _MAX_PERIMETER_VERTICES)
            pts = pts[::stride]
        if len(pts) < 3:
            return None
        return [pts]

    def _run_audit(self) -> None:
        """sim.py's SAFETY AUDIT, every tick: each serviced stop's departure
        vs the CURRENT pessimistic arrival minus the stop's captured rule —
        adoption-time minutes, TIGHTENED in place if the slider tightened
        while the stop was still in flight (module docstring; the captured
        rule is echoed on every state() audit row).

        CONTINUOUS CREW SAFETY (SAFETY SEMANTICS 10c, the no-reship invariant):
        after the serviced-stop loop this ALSO iterates the live crews and emits
        a crew-overrun record for any ARRIVED crew (INBOUND reinforcements are
        off-tile, skipped) whose CURRENT cell is BURNED (nominal front passed) or
        IMMINENT (eroded-P10 within the egress buffer). scoreboard.violations
        folds these in, so it can NEVER read 0 while a crew stands in the burn.
        A STRICT SUPERSET of the old audit — byte-identical when no crew is
        exposed. The continuous sweep (10a) is what keeps this backstop empty in
        an escapable scenario; when it fires, escape was genuinely impossible."""
        cfg = self._cfg
        now = self._now_s
        buffer_s = cfg.egress_buffer_min * 60.0
        audits = []
        violations = []
        min_margin = None
        for st in self._serviced:
            fire = self._fires.get(st.incident_id)
            cl = self._client_by_id.get(st.client_id)
            if fire is None or fire.ens is None or cl is None:
                continue
            # law 6: the audit samples through the SAME ember/adjacency fallback
            # as dispatch + live state, so a serviced developed-cell home is
            # judged against the finite deadline it was dispatched under (the
            # own-cell-reached case is byte-identical — fallback_radius_m only
            # fires when the own cell is UNREACHED).
            eta = spread.eta_at(fire.ens, cl.loc,
                                fallback_radius_m=cfg.ember_threat_radius_m)
            buf_min, ember_min = self._stop_rule.get(
                st.client_id, (cfg.egress_buffer_min, cfg.ember_buffer_min))
            latest = eta.eta_p10_s - (buf_min + ember_min) * 60.0
            margin_h = (UNREACHED if math.isinf(latest)
                        else (latest - st.depart_s) / 3600.0)
            entry = SafetyAudit(
                crew_id=st.crew_id, client_id=st.client_id,
                depart_s=st.depart_s, eta_p10_s=eta.eta_p10_s,
                latest_depart_s=latest, margin_h=margin_h,
                violation=(not math.isinf(margin_h)) and margin_h < 0.0)
            audits.append(entry)
            if entry.violation:
                violations.append(entry)
            if not math.isinf(margin_h) and (min_margin is None
                                             or margin_h < min_margin):
                min_margin = margin_h
        self._audit = audits
        self._violations = violations
        self._min_margin_h = min_margin

        # SAFETY SEMANTICS 10c backstop: flag any ARRIVED crew genuinely CAUGHT.
        # BURNED (nominal front already at the cell) and IMMINENT (eroded-P10
        # within the egress buffer, not yet burned) both count IN EVERY MODE.
        # Until 2026-09-03 the imminent branch required cs._mode == "idle"
        # ("a fleeing crew is not caught") — the tri-model review's finding
        # A8: 106 crew-ticks in the 64-scenario sweep, up to 5.6 h past
        # eroded-P10, all in egress mode, never entered the count, and the
        # only guarding test pinned the zero-exposure case. A lawful route
        # never stands on an imminent cell at its pass time (the corridor
        # margin equals the egress buffer), so anything counted here is a real
        # exposure the sweep failed to prevent; the row's `mode` attributes
        # idle vs moving exposure. Independent of the sweep by construction
        # (still fires with relocation disabled), so the scoreboard can never
        # silently read 0 while a crew sits in the burn. Empty (byte-identical
        # audit) when nothing is exposed.
        overruns = []
        for cid in _idsort(self._crew_sims):
            cs = self._crew_sims[cid]
            if cs.status == CrewStatus.INBOUND:
                continue
            nom, p10 = self._crew_exposure(cs.pos)
            burned = (not math.isinf(nom)) and nom <= now
            imminent = (not math.isinf(p10)) and p10 < now + buffer_s
            if not (burned or imminent):
                continue
            overruns.append({
                "crew_id": cid, "kind": "crew_overrun",
                "reason": "burned" if burned else "imminent",
                "status": cs.status.value, "mode": cs._mode,
                "lat": round(cs.pos.lat, 6), "lon": round(cs.pos.lon, 6),
                "nominal_arrival_s": (None if math.isinf(nom) else nom),
                "eroded_p10_s": (None if math.isinf(p10) else p10),
            })
        self._crew_overruns = overruns

    # -- state ------------------------------------------------------------------

    def _history_bounds(self):
        """(first_tick, last_tick) retrievable via state_at right now, or
        (None, None) before any tick / with history disabled. Derived from the
        tick counter and the ring capacity; PROVABLY matches the ring contents
        because exactly one snapshot is recorded per (non-paused) tick and the
        ring evicts at its maxlen, so it holds precisely ticks
        [max(1, tick_n - H + 1) .. tick_n]."""
        cap = self._history.maxlen or 0
        if cap <= 0 or self._tick_n <= 0:
            return None, None
        return max(1, self._tick_n - cap + 1), self._tick_n

    def _record_history(self, st: dict) -> None:
        """Store a DEEP, independent snapshot of the just-emitted tick's state
        (LAW 1 TIME-SCRUB). deepcopy severs every reference into live session
        structures (tracks/audit/serviced lists) so a later tick can never
        mutate a recorded snapshot — state_at(k) stays byte-identical to what
        tick k served."""
        if self._history.maxlen:
            self._history.append((self._tick_n, copy.deepcopy(st)))

    def state_at(self, tick):
        """Recorded state() snapshot for `tick`, VERBATIM (LAW 1 TIME-SCRUB
        read-only replay), or None when the tick is evicted (older than the
        ring), still in the future, or history is disabled — the server maps
        None to 404. Non-int ticks also return None. The returned object is
        the stored snapshot (already independent of live state); treat it as
        read-only."""
        if isinstance(tick, bool) or not isinstance(tick, int):
            return None
        ring = self._history
        if not ring:
            return None
        first = ring[0][0]
        last = ring[-1][0]
        if tick < first or tick > last:
            return None
        entry = ring[tick - first]              # contiguous by construction
        if entry[0] == tick:
            return entry[1]
        for tn, snap in ring:                   # defensive (invariant holds)
            if tn == tick:
                return snap
        return None

    def _burned_rle(self) -> dict:
        """Burned mask (any fire's nominal arrival <= sim_now) as run-length
        counts alternating 0/1 runs, first run counting 0-cells."""
        spec = self._tile.spec
        n = spec.nrows * spec.ncols
        mask = bytearray(n)
        for fid in self._fire_order:
            ens = self._fires[fid].ens
            if ens is None:
                continue
            data = ens.members[0].data
            now = self._now_s
            for i in range(n):
                if data[i] <= now:
                    mask[i] = 1
        runs = []
        cur = 0
        count = 0
        for v in mask:
            if v == cur:
                count += 1
            else:
                runs.append(count)
                cur = v
                count = 1
        runs.append(count)
        return {"encoding": "rle01", "first_value": 0,
                "nrows": spec.nrows, "ncols": spec.ncols, "runs": runs}

    def _fv_record_reanchors(self, fires) -> None:
        """FIRE VIEW bookkeeping (display only, never read by the engine).
        A re-anchor (tick() step 2: ignite / set_wind / horizon rebase)
        seeds every cell the nominal member had consumed at the anchor
        time, so the NEW field holds arrival == anchor over the whole old
        burn. The burned/burning split needs each cell's TRUE arrival: the
        first time this builder sees a new field it records, for every
        seeded cell (old nominal <= the new anchor) not recorded yet, the old
        field's nominal arrival — chained through successive re-anchors (a
        cell recorded earlier keeps its earlier time). tick() always ends in
        state(), so every field is seen before it can be replaced; the
        record is a pure function of the command script (deterministic)."""
        for f in fires:
            seen = f._fv_seen
            if seen is f.ens:
                continue
            if seen is not None:
                anchor = f.anchor_t0_s
                rec = f._fv_burn_t if f._fv_burn_t is not None else {}
                for i, v in enumerate(seen.members[0].data):
                    if v <= anchor and i not in rec:
                        rec[i] = v
                f._fv_burn_t = rec
            f._fv_seen = f.ens

    def _fire_visuals(self):
        """STATE VISUAL CONTRACT (module docstring; founder "shade the fire"
        2026-09-26): (burned, fire_bands, burned_p50, fire_view).

        THE SHADED FIRE IS THE SIMULATED ACTUAL FIRE: the union over fires
        of cells whose NOMINAL-member arrival is <= sim-now — exactly the
        mask the engine treats as ground truth (terrain.burned, the 10c
        crew audit's 'burned', the re-anchor seed set), spot-fire islands
        included. Until 2026-09-26 the fill was each fire's ensemble P50 <=
        now, which under-drew a wind-driven fire ~20x (park_city_windstorm,
        T+1h45: 0.6 vs 12.4 km2; scratch_review/p50_vs_nominal_probe.py)
        and jumped up to the true burn at every re-anchor.

          burned      rings of that nominal union, traced on a <=120x120
                      min-pooled copy (payload budget): shape unchanged,
                      MEANING changed from the P50 to the actual fire.
          burned_p50  the previous `burned`, verbatim — each fire's P50 <=
                      sim-now rings, concatenated in fire order: the ladder
                      median's own burned area, under an explicit name.
          fire_bands  {'h1','h5','h24'}: rings of (actual burn UNION
                      {P50 <= sim-now + 1/5/24 h}) — the ladder's median
                      projection BOUNDED by the actual fire, so a band's
                      outer edge never lies inside the burn; nested (h1 in
                      h5 in h24), min-pooled like burned; keys ALWAYS present.
          fire_view   the exact picture the page shades, at FULL tile
                      resolution: FIRE_VIEW_CLASSES per cell as row-major
                      runs. burned/burning split each fire cell by its TRUE
                      nominal arrival against its fuel's cold-black window
                      (the SEM-10 transit rule: per fuel class under
                      cfg.transit_cold_black_by_class, else the flat 45
                      min). Band cells never overlap the fire; a cell the
                      median says has burned but the nominal front has not
                      reached is h1. strokes (exact cell-edge contours,
                      chain-coded on the grid-corner lattice — see
                      fire_view_corners): the fire's outline loops and each
                      band's FRONT — its outer edge only where it lies
                      beyond the fire. areas_km2 carries the P50's own
                      burned area, so the median-vs-truth gap stays visible.
        """
        spec = self._tile.spec
        nr, nc = spec.nrows, spec.ncols
        n = nr * nc
        now = self._now_s
        fires = [self._fires[fid] for fid in self._fire_order
                 if self._fires[fid].ens is not None]
        per_class = bool(getattr(self._cfg, "transit_cold_black_by_class",
                                 False))
        # one build per (sim-now, fields): state() also runs on every server
        # poll between ticks. The cache holds the field OBJECTS (identity
        # compared) so a replaced field can never alias a stale entry.
        cache = getattr(self, "_fv_cache", None)
        if (cache is not None and cache[0] == now and cache[1] is self._tile
                and cache[2] == per_class and len(cache[3]) == len(fires)
                and all(e is f.ens for e, f in zip(cache[3], fires))):
            return cache[4]
        burned_p50 = []
        pooled_p50 = []
        for f in fires:
            spec2, vals = _pool_min(f.ens.p50)
            pooled_p50.append(vals)
            burned_p50.extend(_threshold_rings(spec2, vals, now))
        strokes = {"fire": [], "h1": [], "h5": [], "h24": []}
        view = {
            "encoding": "rle_class",
            "classes": list(FIRE_VIEW_CLASSES),
            "nrows": nr, "ncols": nc,
            "grid": {"north_lat": spec.north_lat, "west_lon": spec.west_lon,
                     "cell_m": spec.cell_m,
                     "m_per_deg_lat": geo.M_PER_DEG_LAT},
            "burning_window": "fuel_class" if per_class else "flat_45_min",
            # strokes are [row, col, chain code] on the cell-CORNER lattice
            # (fire_view_corners decodes one); a corner sits at lat = north
            # - row*cell_m/m_per_deg_lat, lon = west + col*cell_m/
            # (m_per_deg_lat*cos(lat)) — the GridSpec law, exact to the cell
            "stroke_coords": "grid_corner_chain",
        }
        if not fires:
            view.update(values=[0], runs=[n], strokes=strokes,
                        areas_km2={k: 0.0 for k in (
                            "fire", "burned", "burning", "h1", "h5", "h24",
                            "p50_burned")})
            return ([], {key: [] for key, _h in _BAND_LEADS}, burned_p50,
                    view)
        self._fv_record_reanchors(fires)

        # union over fires: earliest nominal (the actual fire) and earliest
        # median (the ladder's projection) per cell
        nom = fires[0].ens.members[0].data
        p50 = fires[0].ens.p50.data
        for f in fires[1:]:
            nom = [a if a <= b else b
                   for a, b in zip(nom, f.ens.members[0].data)]
            p50 = [a if a <= b else b for a, b in zip(p50, f.ens.p50.data)]

        # the actual fire: burning while the cell's TRUE arrival is inside
        # its fuel's cold-black window, burned (cold black) after
        cls = bytearray(n)
        fire_idx = [i for i, a in enumerate(nom) if a <= now]
        recs = [(f.ens.members[0].data, f._fv_burn_t or {}) for f in fires]
        fuel = self._tile.fuel.data
        win_memo = {}
        for i in fire_idx:
            t = UNREACHED
            for data, rec in recs:
                v = rec.get(i, data[i])
                if v < t:
                    t = v
            if per_class:
                code = int(round(fuel[i]))
                w = win_memo.get(code)
                if w is None:
                    w = win_memo[code] = cold_black_minutes(code) * 60.0
            else:
                w = _COLD_BLACK_PASS_S
            cls[i] = _FV_BURNING if (now - t) < w else _FV_BURNED

        # the median projection, bounded by the actual fire (overdue -> h1)
        t1 = now + 3600.0
        t5 = now + 5.0 * 3600.0
        t24 = now + 24.0 * 3600.0
        band_idx = []
        n_p50 = 0
        for i in [i for i, q in enumerate(p50) if q <= t24]:
            q = p50[i]
            if q <= now:
                n_p50 += 1
            if cls[i]:
                continue
            cls[i] = _FV_H1 if q <= t1 else (_FV_H5 if q <= t5 else _FV_H24)
            band_idx.append(i)

        # row-major runs over the sparse non-empty cells
        nz = sorted(fire_idx + band_idx)
        values = []
        runs = []
        pos = 0
        counts = [0] * len(FIRE_VIEW_CLASSES)
        for i in nz:
            v = cls[i]
            counts[v] += 1
            if i > pos:
                values.append(0)
                runs.append(i - pos)
                values.append(v)
                runs.append(1)
            elif values and values[-1] == v:
                runs[-1] += 1
            else:
                values.append(v)
                runs.append(1)
            pos = i + 1
        if pos < n:
            values.append(0)
            runs.append(n - pos)

        # strokes, as exact cell edges: the fire's outline = every edge of a
        # fire cell facing a non-fire cell; band k's FRONT = every edge of a
        # band cell of class <= k facing a cell outside (fire U band k) —
        # empty, a farther band, or off the tile. A fire cell facing outside
        # is never a front: where the median does not reach past the actual
        # fire, the band simply is not drawn.
        fire_edges = []
        front_edges = {_FV_H1: [], _FV_H5: [], _FV_H24: []}
        for i in nz:
            c = cls[i]
            r, q = divmod(i, nc)
            for n_cls, start, d in (
                    (cls[i - nc] if r > 0 else 0, (r, q), 0),
                    (cls[i + 1] if q < nc - 1 else 0, (r, q + 1), 1),
                    (cls[i + nc] if r < nr - 1 else 0, (r + 1, q + 1), 2),
                    (cls[i - 1] if q > 0 else 0, (r + 1, q), 3)):
                if c <= _FV_BURNING:
                    if not 0 < n_cls <= _FV_BURNING:
                        fire_edges.append((start, d))
                else:
                    for k in range(c, _FV_H24 + 1):
                        if n_cls == 0 or n_cls > k:
                            front_edges[k].append((start, d))
        # every outer loop (each spot-fire island, however small); an
        # unburned pocket only when it is not a speck (the raster shows it)
        strokes["fire"] = [s for s, _closed, a
                           in _edge_contours(fire_edges)
                           if a >= 0.0 or -a >= _FV_MIN_LOOP_CELLS]
        for key, k in (("h1", _FV_H1), ("h5", _FV_H5), ("h24", _FV_H24)):
            strokes[key] = [s for s, closed, a
                            in _edge_contours(front_edges[k])
                            if not closed or abs(a) >= _FV_MIN_LOOP_CELLS]

        cell_km2 = (spec.cell_m / 1000.0) ** 2
        view.update(values=values, runs=runs, strokes=strokes, areas_km2={
            "fire": round((counts[_FV_BURNED] + counts[_FV_BURNING])
                          * cell_km2, 4),
            "burned": round(counts[_FV_BURNED] * cell_km2, 4),
            "burning": round(counts[_FV_BURNING] * cell_km2, 4),
            "h1": round(counts[_FV_H1] * cell_km2, 4),
            "h5": round(counts[_FV_H5] * cell_km2, 4),
            "h24": round(counts[_FV_H24] * cell_km2, 4),
            "p50_burned": round(n_p50 * cell_km2, 4),
        })

        # compat rings (payload budget: min-pooled like before). A pooled
        # block holds a burned cell iff its pooled nominal is <= now, and
        # then it is inside every band threshold (>= now) whatever its P50;
        # otherwise its (fire U band) value is its pooled median.
        if len(fires) == 1:
            spec2, pn = _pool_min(fires[0].ens.members[0])
            pq = pooled_p50[0]
        else:
            spec2, pn = _pool_min_data(spec, nom)
            _spec, pq = _pool_min_data(spec, p50)
        burned = _threshold_rings(spec2, pn, now)
        disp = [a if a <= now else q for a, q in zip(pn, pq)]
        bands = {key: _threshold_rings(spec2, disp, now + h * 3600.0)
                 for key, h in _BAND_LEADS}
        out = (burned, bands, burned_p50, view)
        self._fv_cache = (now, self._tile, per_class,
                          [f.ens for f in fires], out)
        return out

    # -- ROADS LEGIBILITY (display only; roads inspection 2026-09-25) ----------
    # The founder watched crews "not use roads". The engine drove the road
    # polylines all along; the PAGE drew straight lines: the marker tween
    # slid between 15-min tick positions, the breadcrumb joined one point per
    # tick, a deploying crew's queued stops were one straight red chord, a
    # relocating crew's first tick had no leg yet (a synthesized chord) and a
    # reinforcement's arrival was slid across the map from the tile edge.
    # crews[i].map_view hands the page the real geometry for each of those,
    # plus a one-line reason for every straight stretch that IS real (a home,
    # a zone anchor or an escape point off the road network; a chord outside
    # the road graph). Nothing here feeds back into the engine.

    def _map_jumps(self, arrived) -> dict:
        """{crew_id: (from, to, reason)} for every crew whose position changed
        since the end of its last drive WITHOUT driving — today only a
        reinforcement's arrival (tick() step 0 places it at its safe zone
        from the tile-edge staging marker). The page snaps such a crew; it
        must never slide it across the terrain."""
        out = {}
        for cid in _idsort(self._crew_sims):
            cs = self._crew_sims[cid]
            last = self._map_last_pos.get(cid)
            if last is not None and _haversine_m(last, cs.pos) > 1.0:
                out[cid] = (last, cs.pos, ("reinforcement_arrival"
                                           if cid in arrived else
                                           "repositioned"))
        return out

    def _record_map_paths(self, jumps: dict) -> None:
        """After tick() step 5: keep each crew's simplified driven path for
        this tick (map_view.moved_path), its jump record, and rebuild its
        driven trail."""
        for cid in _idsort(self._crew_sims):
            cs = self._crew_sims[cid]
            moved = _simplify_ll(cs.driven) if len(cs.driven) >= 2 else []
            if len(moved) < 2:
                moved = []
            self._map_moved[cid] = moved
            j = jumps.get(cid)
            self._map_jumped[cid] = self._jump_record(cid, j) if j else None
            # each tick's drive simplified ONCE per trail rung
            self._map_trail[cid].append((j is not None, {
                eps: (_simplify_ll(moved, eps, max_pts=10 ** 9) if moved
                      else []) for eps in MAP_TRAIL_EPS_LADDER}))
            self._map_trail_runs[cid] = self._build_trail(cid)
            self._map_last_pos[cid] = cs.pos

    def _build_trail(self, cid) -> list:
        """The driven trail as runs of [lat, lon] — consecutive ticks' paths
        joined, a NEW run after every jump (never a line across it). The
        last MAP_TRAIL_FRESH_TICKS ticks use the finest MAP_TRAIL_EPS_LADDER
        rung; older ticks the finest rung that keeps the crew within
        MAP_TRAIL_MAX_PTS; failing even the coarsest, the OLDEST ticks are
        dropped (shorter, never straighter)."""
        entries = list(self._map_trail.get(cid, ()))
        n = len(entries)
        fine = MAP_TRAIL_EPS_LADDER[0]

        def pts_of(i, eps_old):
            eps = fine if n - 1 - i < MAP_TRAIL_FRESH_TICKS else eps_old
            return entries[i][1].get(eps) or []

        choice = None
        for start in range(n):
            for eps_old in MAP_TRAIL_EPS_LADDER:
                if sum(len(pts_of(i, eps_old))
                       for i in range(start, n)) <= MAP_TRAIL_MAX_PTS:
                    choice = (start, eps_old)
                    break
            if choice is not None:
                break
        if choice is None:
            choice = (max(0, n - 1), MAP_TRAIL_EPS_LADDER[-1])
        start, eps_old = choice
        runs = []
        cur = None
        for i in range(start, n):
            if entries[i][0]:                 # a jump: never join across it
                cur = None
            pts = pts_of(i, eps_old)
            if len(pts) < 2:
                continue
            if cur is None or cur[-1] != pts[0]:
                cur = list(pts)
                runs.append(cur)
            else:
                cur.extend(pts[1:])
        return runs

    def _jump_record(self, cid, j) -> dict:
        frm, to, reason = j
        rec = {"from": [round(frm.lat, 6), round(frm.lon, 6)],
               "to": [round(to.lat, 6), round(to.lon, 6)],
               "reason": reason}
        if reason == "reinforcement_arrival":
            edge = ("east" if self._crew_region.get(cid) == "reg_1h"
                    else "west")
            delay = self._crew_reinforce_delay_min.get(cid, 0.0)
            rec["why"] = (
                f"mutual-aid arrival: the {delay:g} min transit from the "
                f"{edge}-edge staging point happens off the map, so the crew "
                f"appears at safe zone {self._crew_anchor.get(cid, '')} — no "
                f"drive is simulated for it and none is drawn")
        else:
            rec["why"] = ("repositioned without a simulated drive — no path "
                          "is drawn")
        return rec

    def _node_gap_m(self, p):
        """Metres from p to the nearest node of the loaded road graph — the
        snap roads.route charges as a straight spur — or None (no graph)."""
        if self._road_bbox is None:
            return None
        key = (round(p.lat, 7), round(p.lon, 7))
        if key not in self._map_gap_cache:
            if len(self._map_gap_cache) >= 4096:
                self._map_gap_cache.clear()
            hit = self._road_graph.nearest_node(p, max_m=float("inf"))
            self._map_gap_cache[key] = None if hit is None else hit[1]
        return self._map_gap_cache[key]

    def _map_place(self, p):
        """('home', client_id) | ('zone', SafeZone) | ('other', None) for a
        leg endpoint (exact coordinates: homes and zones are fixed points)."""
        idx = self._map_place_idx
        if idx is None:
            idx = {}
            for z in self._loc.safe_zones:
                idx[(round(z.loc.lat, 7), round(z.loc.lon, 7))] = ("zone", z)
            for cl in self._clients:
                idx[(round(cl.loc.lat, 7), round(cl.loc.lon, 7))] = (
                    "home", cl.client_id)
            self._map_place_idx = idx
        return idx.get((round(p.lat, 7), round(p.lon, 7)), ("other", None))

    def _why_spur(self, p, end: str, target_kind: str = "") -> str:
        d = self._node_gap_m(p)
        gap = "an unknown distance" if d is None else f"{d:,.0f} m"
        if end == "dest" and target_kind == "escape":
            return (f"area escape (SEM-10d): no catalog safe zone is "
                    f"reachable ahead of the fire, so the crew heads for open "
                    f"ground {gap} from the nearest mapped road junction — "
                    f"this last stretch is straight")
        place, obj = self._map_place(p)
        if place == "home":
            if end == "dest":
                return (f"last-mile spur: home {obj} sits {gap} from the "
                        f"nearest mapped road junction — no road or driveway "
                        f"is mapped closer, so the final stretch is straight")
            return (f"leaving home {obj}, {gap} from the nearest mapped road "
                    f"junction — no road or driveway is mapped closer")
        if place == "zone":
            return (f"safe zone {obj.zone_id} ({obj.name}) is anchored {gap} "
                    f"from the nearest mapped road junction "
                    f"(planning-grade coordinate) — straight connector")
        return (f"the crew is {gap} from the nearest mapped road junction — "
                f"straight connector to the road")

    def _why_chord(self, a, b, note) -> str:
        if self._road_graph is None:
            return ("roads offline for this session — every leg is a "
                    "straight tier-0 leg")
        why = f"no road route ({note})" if note else "no road route"
        bb = self._road_bbox
        if bb is not None and not all(
                bb[0] <= p.lat <= bb[2] and bb[1] <= p.lon <= bb[3]
                for p in (a, b)):
            why += (" — an end of this leg lies outside the loaded road "
                    "graph's coverage")
        return why + "; drawn as a straight tier-0 leg"

    def _explain_segments(self, segs, origin, target, target_kind: str,
                          note) -> Optional[list]:
        """why[i] per display segment {points, kind, spur}: None for road,
        the mechanism for every straight stretch (a leg's LAST spur is its
        destination connector, any other spur its origin connector)."""
        if not segs:
            return None
        n = len(segs)
        out = []
        for i, sg in enumerate(segs):
            kind = sg.get("kind")
            if kind == "road":
                out.append(None)
            elif kind == "chord":
                a = origin if origin is not None else LatLon(*sg["points"][0])
                why = self._why_chord(a, target, note)
                if target_kind == "escape":
                    why = ("area escape (SEM-10d): no catalog safe zone is "
                           "reachable ahead of the fire and the escape point "
                           "is open ground with no road near it — " + why)
                out.append(why)
            elif i == n - 1:
                out.append(self._why_spur(target, "dest", target_kind))
            else:
                p = origin if origin is not None else LatLon(*sg["points"][0])
                out.append(self._why_spur(p, "origin"))
        return out

    def _map_route(self, a: LatLon, b: LatLon):
        """Cached fastest road route a -> b for DISPLAY of a deploying crew's
        queued legs (roads.route, exact-coordinate key — homes are fixed);
        None in straight-leg mode. Its own cache: the dispatch route cache
        is never touched."""
        if self._roads is None or self._road_graph is None:
            return None
        key = (a.lat, a.lon, b.lat, b.lon)
        rr = self._map_route_cache.get(key)
        if rr is None:
            if len(self._map_route_cache) >= _ROAD_PLAN_CACHE_MAX:
                self._map_route_cache.clear()
            rr = self._roads.route(self._road_graph, a, b, self._cfg)
            self._map_route_cache[key] = rr
        return rr

    @staticmethod
    def _display_segments(a: LatLon, b: LatLon, rr, eps_m: float) -> list:
        """Display cover {points, kind, spur} of one leg a -> b from a
        RoadRoute (road runs simplified at eps_m, boundaries kept) or, with
        no route, the honest straight chord [a, b]."""
        if rr is None:
            pts, kinds = [a, b], ["chord"]
        else:
            pts = list(rr.polyline) or [a, b]
            if pts[0] != a:
                pts.insert(0, a)
            if pts[-1] != b:
                pts.append(b)
            kinds = _edge_kinds_from_route(rr, len(pts) - 1)
        poly = [[round(p.lat, 6), round(p.lon, 6)] for p in pts]
        segs = _segments_from_kinds(poly, kinds)
        for sg in segs:
            if sg["kind"] == "road" and len(sg["points"]) > 2:
                sg["points"] = _simplify_ll(sg["points"], eps_m,
                                            max_pts=10 ** 9)
        return segs

    def _queue_view(self, cs) -> Optional[dict]:
        """The ROAD route through a DEPLOYING crew's queued stops
        (itinerary[0] -> [1] -> ...) — the page used to draw [crew, stops]
        as one straight red 'no road route' chord. None otherwise."""
        if cs._mode != "deploy" or len(cs.itinerary) < 2:
            return None
        segs, why = [], []
        for s0, s1 in zip(cs.itinerary, cs.itinerary[1:]):
            a, b = s0["loc"], s1["loc"]
            rr = self._map_route(a, b)
            leg = self._display_segments(a, b, rr, MAP_QUEUE_EPS_M)
            why.extend(self._explain_segments(
                leg, a, b, "home", getattr(rr, "note", None)) or [])
            segs.extend(leg)
        return {"segments": segs, "why": why}

    def _egress_preview(self, cid, cs) -> Optional[dict]:
        """A crew the safety sweep relocated THIS tick is egressing with no
        leg yet (_SimCrew resolves it at the start of its next drive), so it
        has no egress polyline and the page synthesized a straight 'no road
        route' chord. Preview the exact leg that drive resolves —
        _leg_route(pos, egress_loc), the call _begin_leg makes. None
        otherwise. Cached per (crew, tick)."""
        if cs._mode != "egress" or cs._leg is not None or cs.egress_loc is None:
            return None
        key = (cid, self._tick_n, cs.pos, cs.egress_loc, self._now_s)
        view = self._map_preview_cache.get(key)
        if view is None:
            rr = self._leg_route(cs.pos, cs.egress_loc)
            segs = self._display_segments(cs.pos, cs.egress_loc, rr,
                                          MAP_PATH_EPS_M)
            kind = "escape" if cs.egress_zone_id == "escape" else "zone"
            view = {"segments": segs,
                    "why": self._explain_segments(
                        segs, cs.pos, cs.egress_loc, kind,
                        getattr(rr, "note", None))}
            if len(self._map_preview_cache) > 256:
                self._map_preview_cache.clear()
            self._map_preview_cache[key] = view
        return view

    def _map_view(self, cid, cs, route_segments, egress_segments) -> dict:
        """crews[i].map_view (display only; see the section comment)."""
        leg = cs._leg
        route_why = egress_why = None
        if route_segments and leg is not None:
            route_why = self._explain_segments(
                route_segments, leg.get("origin"), leg["target"], "home",
                leg.get("note"))
        kind = "escape" if cs.egress_zone_id == "escape" else "zone"
        if egress_segments:
            if cs._mode == "egress" and leg is not None:
                egress_why = self._explain_segments(
                    egress_segments, leg.get("origin"), leg["target"], kind,
                    leg.get("note"))
            elif cs._egress_plan is not None:
                plan = cs._egress_plan
                egress_why = self._explain_segments(
                    egress_segments, plan["points"][0], plan["points"][-1],
                    kind, plan.get("note"))
        return {
            "moved_path": self._map_moved.get(cid, []),
            "jumped": self._map_jumped.get(cid),
            "trail": self._map_trail_runs.get(cid, []),
            "route_why": route_why,
            "egress_why": egress_why,
            "egress_preview": self._egress_preview(cid, cs),
            "queue": self._queue_view(cs),
        }

    def state(self) -> dict:
        """JSON-safe UI snapshot (design doc panel-by-panel). Deterministic
        for a deterministic command script; json.dumps(..., allow_nan=False)
        always succeeds on it (no inf/nan leak)."""
        cfg = self._cfg
        spec = self._tile.spec
        lat_mid = spec.north_lat - spec.nrows * spec.cell_m / (2.0 * geo.M_PER_DEG_LAT)
        terrain = {
            "bbox": {
                "west": spec.west_lon,
                "south": spec.north_lat - spec.nrows * spec.cell_m / geo.M_PER_DEG_LAT,
                "east": spec.west_lon + spec.ncols * spec.cell_m / geo.m_per_deg_lon(lat_mid),
                "north": spec.north_lat,
            },
            "cell_m": spec.cell_m, "nrows": spec.nrows, "ncols": spec.ncols,
            "synthetic": self._synthetic,
            "banner": SYNTHETIC_BANNER if self._synthetic else "",
            "source": self._tile.source,
            "burned": self._burned_rle(),
        }
        fires = [{
            "incident_id": fid,
            "lat": round(self._fires[fid].origin.lat, 6),
            "lon": round(self._fires[fid].origin.lon, 6),
            "t_ignite_s": self._fires[fid].t_ignite_s,
            "intensity": self._fires[fid].intensity,
            "wind_dir_deg": self._fires[fid].wind_dir_deg,
        } for fid in self._fire_order]

        state_by_client = {}
        for (cid, _iid), rec in self._alerts.states().items():
            prev = state_by_client.get(cid)
            if prev is None or _DISPLAY_RANK[rec.state] > _DISPLAY_RANK[prev]:
                state_by_client[cid] = rec.state
        serviced_ids = {s.client_id for s in self._serviced}
        # LAW 5 GEL RING (FOUNDER ROUND 3): a serviced home carries gel_applied
        # + gelled_at_s (the deploy-complete / job-done time). Keyed off the
        # append-only _serviced ledger, so the flag PERSISTS through later
        # alert-state changes (a gelled home that later goes EXTREME still
        # shows gel_applied=true; the UI keys its blue ring off it).
        gel_at = {}
        for s in self._serviced:
            gel_at.setdefault(s.client_id, s.depart_s)
        # LAW 8 OUTCOME TAG (FOUNDER ROUND 4): the clients a crew is ACTIVELY
        # en_route to / deploying on right now (the current itinerary head of a
        # travelling/deploying crew) — 'in_progress'. Future stops on a crew's
        # plan are merely `assigned` (they read as 'pending'). Pure read of the
        # post-advance crew modes (tick-frozen), so it is deterministic and
        # replays byte-identically from history.
        now = self._now_s
        in_progress_ids = set()
        for _cid in self._crew_sims:
            cs = self._crew_sims[_cid]
            if cs._mode in ("travel", "deploy") and cs.itinerary:
                in_progress_ids.add(cs.itinerary[0]["client_id"])
        outcome_counts = {}
        clients = []
        for cl in self._clients:
            eta = self._client_etas.get(cl.client_id)
            gelled = gel_at.get(cl.client_id)
            astate = state_by_client.get(cl.client_id, AlertState.NONE)
            # LAW 8 OUTCOME TAG derivation (pure projection of existing tick
            # state — the serviced ledger, the alert state, the rollup ETA, the
            # assignment sets and the unserved-reason map): a home is IMPACTED
            # once the fire's median has arrived (alert IMPACTED, whose engine
            # definition is finite eta_p50 <= now, OR the rollup p50 <= now for
            # a fire not yet laddered to IMPACTED); THREATENED once it has ever
            # entered the dispatch job pool (self._jobbed — the same signal the
            # unserved list keys off, and which the law-6 ember fallback now
            # lets fire-adjacent developed homes reach).
            p50 = eta.eta_p50_s if eta is not None else UNREACHED
            impacted = (astate is AlertState.IMPACTED
                        or (not math.isinf(p50) and p50 <= now))
            outcome = _client_outcome(
                gelled=gelled is not None,
                impacted=impacted,
                threatened=cl.client_id in self._jobbed,
                in_progress=cl.client_id in in_progress_ids,
                assigned=cl.client_id in self._assigned,
                reason=self._unserved_reason.get(cl.client_id),
                # finite projected arrival (eta_p50) -> a not-yet-jobbed home is
                # 'monitoring', not 'not_threatened' (round-4 verifier fix): the
                # payload below shows this same finite eta_p50_s, so the tag must
                # agree with it. UNREACHED/None -> not_threatened.
                has_eta=not math.isinf(p50))
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
            clients.append({
                "client_id": cl.client_id, "name": cl.name,
                "lat": round(cl.loc.lat, 6), "lon": round(cl.loc.lon, 6),
                "value_usd": cl.value_usd,
                "state": astate.value,
                "eta_p10_s": (None if eta is None or math.isinf(eta.eta_p10_s)
                              else eta.eta_p10_s),
                "eta_p50_s": (None if eta is None or math.isinf(eta.eta_p50_s)
                              else eta.eta_p50_s),
                "serviced": cl.client_id in serviced_ids,
                "assigned": cl.client_id in self._assigned,
                "gel_applied": gelled is not None,
                "gelled_at_s": gelled,
                # LAW 8: why this home is / isn't protected (map self-explains)
                "outcome": outcome,
            })

        crews = []
        for cid in _idsort(self._crew_sims):
            cs = self._crew_sims[cid]
            leg = cs._leg
            # remaining ingress path (STATE VISUAL CONTRACT): the crew marker
            # follows these points between ticks. route_kinds is the per-edge
            # render kind aligned to [pos->pts[0], pts[0]->pts[1], ...] (ROADS
            # ADHERENCE spur metadata), surfaced below as spur_mask/segments.
            route_poly = []
            route_on = False
            route_kinds = None
            if cs._mode == "travel" and leg is not None:
                route_poly = ([[round(cs.pos.lat, 6), round(cs.pos.lon, 6)]]
                              + [[round(p.lat, 6), round(p.lon, 6)]
                                 for p in leg["pts"]])
                route_on = leg["on_roads"]
                route_kinds = leg.get("edge_kind")
            # planned emergency egress for the CURRENT stop (always present
            # while committed/on-site); while actually egressing it is the
            # remaining active egress leg
            egress_poly = []
            egress_on = False
            egress_kinds = None
            if cs._mode == "egress" and leg is not None:
                egress_poly = ([[round(cs.pos.lat, 6), round(cs.pos.lon, 6)]]
                               + [[round(p.lat, 6), round(p.lon, 6)]
                                  for p in leg["pts"]])
                egress_on = leg["on_roads"]
                egress_kinds = leg.get("edge_kind")
            elif cs._egress_plan is not None:
                egress_poly = [[round(p.lat, 6), round(p.lon, 6)]
                               for p in cs._egress_plan["points"]]
                egress_on = cs._egress_plan["on_roads"]
                egress_kinds = cs._egress_plan.get("edge_kind")
            route_spur_mask, route_segments = _spur_render(route_poly, route_kinds)
            egress_spur_mask, egress_segments = _spur_render(egress_poly,
                                                             egress_kinds)
            # eta_s: absolute unix s — arrival at the next stop (en_route),
            # projected departure (deploying), arrival at the safe zone
            # (egressing); None when idle
            if cs._mode in ("travel", "egress") and leg is not None:
                eta_s = (self._now_s
                         + cs._leg_remaining_m() / max(leg["speed"], 0.1))
            elif cs._mode == "deploy":
                eta_s = cs._deploy_end_s
            else:
                eta_s = None
            # REGIONAL / MUTUAL-AID CREWS (SEMANTICS 9): the tier fields the UI
            # keys the inbound marker + arrival countdown off. region is fixed
            # at construction; inbound is True exactly while status is INBOUND
            # (parked at the edge-entry lat/lon above, off-map); available_at_s
            # is None until the first ignition arms the mobilization clock;
            # arrival_eta_s counts down to 0 at arrival (None for a crew with no
            # mobilization clock — i.e. every in-zone crew, and any
            # reinforcement before the first ignition).
            region = self._crew_region.get(cid, "in_zone")
            avail_at = self._crew_available_at.get(cid)
            arrival_eta_s = (None if avail_at is None
                             else max(0.0, avail_at - self._now_s))
            crews.append({
                "crew_id": cid, "name": cs.crew.name,
                "status": cs.status.value,
                "region": region,
                "inbound": cs.status == CrewStatus.INBOUND,
                "available_at_s": avail_at,
                "arrival_eta_s": arrival_eta_s,
                # CONTINUOUS CREW SAFETY (SAFETY SEMANTICS 10a): True while the
                # crew is repositioning to a safe zone because the fire threatens
                # its position/zone; relocate_reason is 'zone_overrun' (front
                # already on it) or 'zone_threatened' (front within the buffer),
                # else null. The UI shows the crew "repositioning — zone
                # threatened"; snapshotted so scrub replays the relocation.
                "relocating": cid in self._relocating,
                "relocate_reason": self._relocating.get(cid),
                "lat": round(cs.pos.lat, 6), "lon": round(cs.pos.lon, 6),
                "anchor_zone_id": self._crew_anchor[cid],
                "egress_zone_id": cs.egress_zone_id,
                "route_polyline": route_poly,
                # tri-state flags (module docstring STATE VISUAL CONTRACT):
                # None when there is NO corresponding polyline (idle at
                # rest), so False is reserved for a REAL straight-line leg
                "route_on_roads": (route_on if route_poly else None),
                # ROADS ADHERENCE (FOUNDER ROUND 3 law 2): per-edge spur mask +
                # grouped segments aligned to route_polyline so the UI draws
                # the road portion SOLID and the spur/chord DASHED. None (like
                # the flag) when there is no route polyline.
                "route_spur_mask": route_spur_mask,
                "route_segments": route_segments,
                "egress_polyline": egress_poly,
                "egress_on_roads": (egress_on if egress_poly else None),
                "egress_spur_mask": egress_spur_mask,
                "egress_segments": egress_segments,
                "eta_s": (None if eta_s is None or math.isinf(eta_s)
                          else round(eta_s, 3)),
                "track": list(self._tracks[cid])[-30:],
                "itinerary": [{
                    "client_id": s["client_id"],
                    "incident_id": s["incident_id"],
                    "lat": round(s["loc"].lat, 6),
                    "lon": round(s["loc"].lon, 6),
                    "planned_arrive_s": s["planned_arrive_s"],
                    "planned_depart_s": s["planned_depart_s"],
                    "latest_depart_s": s["latest_depart_s"],
                    "planned_margin_h": round(s["planned_margin_h"], 4),
                    # in-flight stop kept through a live tightening: audited
                    # under the NEW rule + re-checked every tick (withdraws
                    # on projected violation) — never a silent grandfather
                    "policy_superseded": s["client_id"] in self._superseded,
                } for s in cs.itinerary],
                # ROADS LEGIBILITY (display only, additive — every key above
                # is unchanged): the road path driven this tick, the driven
                # trail, jumps, planned queued legs on roads, the relocation
                # preview and a reason for every straight stretch.
                "map_view": self._map_view(cid, cs, route_segments,
                                           egress_segments),
            })

        unserved = []
        by_reason = {}
        for cid in _idsort(self._jobbed):
            if cid in serviced_ids:
                continue
            if cid in self._assigned:
                reason = "in_progress"
            else:
                reason = self._unserved_reason.get(cid, "capacity")
            unserved.append({"client_id": cid,
                             "incident_id": self._jobbed[cid],
                             "reason": reason})
            by_reason[reason] = by_reason.get(reason, 0) + 1

        scoreboard = {
            "served": len(self._serviced),
            "unserved_by_reason": {k: by_reason[k] for k in sorted(by_reason)},
            "min_margin_h": (None if self._min_margin_h is None
                             else round(self._min_margin_h, 4)),
            # SAFETY SEMANTICS 10c: serviced-stop departure violations PLUS live
            # crew-overrun violations, so the count can never read 0 while a crew
            # stands in the burn (crew_overruns breaks it out for the UI).
            "violations": len(self._violations) + len(self._crew_overruns),
            "crew_overruns": len(self._crew_overruns),
            "withdrawn": len(self._withdrawn),
            # SAFETY SEMANTICS 10a: cumulative crew RELOCATION events (a crew
            # repositioned to a safe zone because the fire threatened it) — kept
            # DISTINCT from safety withdrawals (aborted gel deploys).
            "relocations": self._n_relocations,
            # CREW SHIFTS (ROUND 6b): crews currently resting (fatigue law)
            "crews_resting": sum(
                1 for c in self._crew_sims.values()
                if c.status == CrewStatus.OFF_DUTY),
            "buffer_min": cfg.egress_buffer_min,
            "slack_min": cfg.slack_min,
            "deploy_min": cfg.deploy_min,
            # LAW 8 OUTCOME TAG (FOUNDER ROUND 4): the per-home outcome
            # breakdown {tag: count}. Every tag is present (0 before it first
            # occurs) so the UI block is stable; the counts sum to len(clients)
            # (every home gets exactly one outcome). Snapshotted with state(),
            # so a scrub replay shows the tick's breakdown too.
            "outcomes": {t: outcome_counts.get(t, 0) for t in OUTCOME_TAGS},
        }

        # audit rows carry the rule they were checked under, so a stop
        # completed before a slider move is VISIBLY under its adoption-time
        # rule (never a silent mismatch with scoreboard.buffer_min)
        audit_rows = []
        for a in self._audit:
            row = jsonable(a)
            rb, re_ = self._stop_rule.get(
                a.client_id, (cfg.egress_buffer_min, cfg.ember_buffer_min))
            row["rule_buffer_min"] = rb
            row["rule_ember_min"] = re_
            audit_rows.append(row)

        burned_rings, fire_bands, burned_p50, fire_view = self._fire_visuals()
        first_tick, last_tick = self._history_bounds()
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "wgresponse-simui-state",
            "location": self._loc.key,
            "location_name": self._loc.name,
            "fire_zone": self._fire_zone,   # what ACTUALLY loaded (FIRE ZONE)
            "seed": self._seed,
            "t0_s": self._t0_s,
            "sim_now_s": self._now_s,
            "tick": self._tick_n,
            # LAW 1 TIME-SCRUB HISTORY: the range of ticks a client can scrub
            # back to via GET /api/sim/<id>/state?tick=k (state_at). first/last
            # are None before the first tick; current_tick == tick.
            "history": {"first_tick": first_tick, "last_tick": last_tick,
                        "current_tick": self._tick_n},
            "speed": self._speed,
            "paused": self._paused,
            "wind_dir_deg": self._wind_dir_deg,
            "intensity": self._intensity,
            "roads_on": self._road_graph is not None,
            # PHYSICS TOGGLES (contracts SAFETY SEMANTICS 11 ember spotting +
            # cfg.grass_ros_floor_frac grass head-ROS floor): echo the two
            # realistic-fire switches so the UI reflects the per-session toggle
            # (and a scrubbed frame replays the toggle state it was served
            # under). Both read straight off cfg: spotting = spotting_enabled;
            # grass_realism = the head-ROS floor being armed (frac > 0). Direct
            # SimSession construction leaves both at the ResponseConfig product
            # defaults (OFF); the server /api/sim/new path turns them ON.
            "spotting": bool(cfg.spotting_enabled),
            "grass_realism": cfg.grass_ros_floor_frac > 0.0,
            # COAT-EARLY dispatch lever (founder loop 2026-07-11): crews coat as
            # early as feasible instead of the gel-freshness late-shift, freeing
            # them sooner (~+1% more homes). Echoes cfg.dispatch_late_shift.
            "coat_early": not cfg.dispatch_late_shift,
            # PLAN-ON-ROADS (2026-07-12): dispatch travel + corridor T* follow
            # the actual road polyline (True only when the flag is on AND a
            # road graph loaded — the echo reports what the planner really did).
            "plan_on_roads": bool(
                getattr(cfg, "dispatch_plan_on_roads", False)
                and self._road_graph is not None),
            # FOUNDER ROUND 5 COATING HOLD observability: enabled (knob > 0),
            # released (gate failed once — latched), held (homes currently
            # deferred), prepositioned crew->zone map.
            "hold": {
                "enabled": cfg.dispatch_hold_h > 0.0,
                "hold_h": cfg.dispatch_hold_h,
                "weather_buffer_h": cfg.dispatch_hold_weather_buffer_h,
                "released": self._hold_released,
                "held": self._hold_active_n,
                "prepositioned": dict(sorted(self._preposition_target.items())),
            },
            "terrain": terrain,
            "fires": fires,
            # STATE VISUAL CONTRACT ("shade the fire", 2026-09-26): burned =
            # the ACTUAL fire (nominal), burned_p50 = the old median fill,
            # fire_bands bounded by the actual fire, fire_view = the exact
            # per-cell picture the page shades (see _fire_visuals)
            "burned": burned_rings,
            "burned_p50": burned_p50,
            "fire_bands": fire_bands,
            "fire_view": fire_view,
            "clients": clients,
            "crews": crews,
            "plan": self._plan_snapshot,
            "unserved": unserved,
            "alert_feed": [jsonable(e) for e in list(self._alert_feed)[-50:]],
            "serviced": [jsonable(s) for s in self._serviced],
            "audit": audit_rows,
            "withdrawn": [dict(w) for w in self._withdrawn],
            # SAFETY SEMANTICS 10c: this tick's live crew-overrun rows (crew_id,
            # reason burned|imminent, mode, position, arrivals) — the honest
            # backstop the founder's "crews in the fire, violations=0" bug needed.
            "crew_overruns": [dict(o) for o in self._crew_overruns],
            "scoreboard": scoreboard,
            "safe_zones": [{"zone_id": z.zone_id, "name": z.name,
                            "lat": round(z.loc.lat, 6),
                            "lon": round(z.loc.lon, 6)}
                           for z in self._loc.safe_zones],
            "notes": list(self._notes),
            "cmd_log": [dict(c) for c in self._cmd_log],
        }
