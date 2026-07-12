"""wgresponse.spread — fire arrival-time field via time-dependent minimum travel
time (Finney 2002 MTT), per docs/research/spread_model.md §3.

Method: Dijkstra label-setting on a 16-neighbor lattice over the TerrainTile
grid. Edge traversal u->v evaluates weather at the arrival time at u (bucketed
to the hour), builds the cell's max-spread direction from the effective wind
(true wind vector-combined with a slope-equivalent virtual wind pointing
upslope), and takes the elliptical directional rate of spread along the edge
bearing (rothermel.directional_ros_mps).

16-neighbor rationale: the shortest lattice path can miss the true straight
line by up to half the angular spacing, overestimating travel time by
1/cos(dtheta/2): 8 neighbors (45 deg) -> up to +8.2%; adding the 8 knight
moves (offsets (+-1,+-2)/(+-2,+-1), d = sqrt5 cells) tightens the spacing to
18.4-26.6 deg -> worst-case ~+2-3% late bias. The bias direction is LATE
(predicted arrival after truth), so downstream safety margins must never rely
on grid error as headroom (digest §3.3).

Units (contracts law): times unix seconds, distances meters, speeds m/s,
angles compass degrees (0 = north, CW); wind_dir_deg blows FROM. Arrival
rasters hold absolute unix seconds; contracts.UNREACHED = inf for cells not
reached within cfg.horizon_h.

Sprint 2 (Council-1 model F2/F6/F4): compute_arrival grew a dir_offset_deg
wind-rotation knob and perimeter/perimeter_at_s already-burned-interior
seeding; ensemble_arrival is v2 — members seeded on (incident_id, k) only
(never t0), Latin-hypercube-stratified wind/moisture/direction draws, and
Hazen-position quantiles with a NOMINAL-BOUNDED p10 (p10 never later than
the unperturbed member — Session-2 verifier fix). Perimeter burned-mask
seeding detects the boundary over the full 16-offset edge set (knight moves
included; 8-neighbor detection shipped a late exterior front — Session-2
verifier fix). See the per-function docstrings.

ROS MEMO (Sprint 2 ticket B3, Council-1 perf S1+S2): compute_arrival memoizes
the Rothermel evaluation per INVOCATION — three dicts created inside the call
and dead at return (no cross-run state, no memory accretion across incidents):
  hour_wx    hour-bucket -> effective weather (wind10, m_dead, wdir) AFTER
             wind_scale / moisture_scale / dir_offset_deg are applied, so
             ensemble members can never share a wrongly-scaled entry;
  bed_cache  (fuel_code, m_dead) -> rothermel.fuel_bed intermediate (S1 —
             the bed varies per hour, not per cell: 2 builds/settled cell
             collapse to ~fuels x hours);
  ros_cache  (fuel_code, slope_deg, aspect_deg, wind10, m_dead, wdir) ->
             (head_ros, lb, bearing, spot) — the full directional-input ROS
             (S2). EVERY input that can change the value is in the key, so a
             hit returns exactly what recomputation would produce: arrival
             rasters are byte-identical with the memo on or off (gate:
             tests/test_spread.py memo matrix + the pinned legacy
             fingerprints). Capped at _ROS_CACHE_MAX entries (beyond the cap
             new keys just recompute — determinism unaffected).
The per-(cell, hour) cache this replaces measured a 0.0% hit rate — each cell
settles exactly once in Dijkstra (docs/review/perf_review.md §1.7); on
LANDFIRE integer-degree slope/aspect tiles the new key dedupes ~83% of ROS
evaluations. ESCAPE HATCH: env WGRESP_NO_ROS_MEMO=1 selects the unmemoized
reference path (every quantity recomputed per settled cell, the pre-B3
structure); read per call, it exists for the byte-identity tests and for
A/B-ing suspected memo bugs in the field.

Stdlib only; deterministic (ensemble perturbations via contracts.seeded_rng).
"""
from __future__ import annotations

import heapq
import logging
import math
import os

from wgresponse import geo, rothermel
from wgresponse.contracts import (
    EnsembleArrival,
    LatLon,
    Raster,
    ResponseConfig,
    TerrainTile,
    ThreatEta,
    UNREACHED,
    WeatherForecast,
    seeded_rng,
)
from wgresponse.fuels import (
    FUEL_TABLE,
    dead_fuel_moisture_frac,
    is_burnable,
    is_grass,
    is_grass_shrub,
    is_timber_litter,
    is_timber_understory,
)

_ROS_FLOOR_MPS = 1e-4        # traversal-time floor divisor (m/s)
_SQRT2 = math.sqrt(2.0)
_SQRT5 = math.sqrt(5.0)

# B3 ROS-memo bounds (module docstring). _ROS_CACHE_MAX only ever binds on
# pathological continuous-slope synthetic tiles (~91% unique keys); LANDFIRE
# integer-degree tiles need a few 10k entries. Not a config knob: past the cap
# the engine recomputes (identical values), it never changes results.
_ROS_CACHE_MAX = 200_000
_BED_MISS = object()         # bed_cache sentinel: a cached bed may be None

_LOG = logging.getLogger(__name__)

# NO-BARRIER GUARANTEE (Sprint 2 ticket A3, Council-1 model F1): a code that
# is_burnable() classifies burnable but that has no FUEL_TABLE row must NEVER
# behave as non-spreading — Council-1 exp1 measured a 270 m band of missing-row
# GR3(103) turning a 23.1 h client ETA into inf (alert never fires; LATE, the
# worst polarity for an alerting product). Unknown burnable codes fall back to
# GR2 (102) parameters: fast cured grass, conservative-FAST (EARLY-biased —
# false alarms, never suppressed alerts). With the full 40-row FBFM40 table
# this path is unreachable for valid codes; it guards future/dynamic codes.
_FALLBACK_FUEL_CODE = 102
_WARNED_UNKNOWN_CODES: set = set()   # warn once per unknown code per process


def _fuel_for_code(code: int):
    """FuelModel for a burnable FBFM40 `code`; GR2 fallback for unknown codes
    (never None for a burnable code — see the no-barrier guarantee above)."""
    fm = FUEL_TABLE.get(code)
    if fm is None:
        if code not in _WARNED_UNKNOWN_CODES:
            _WARNED_UNKNOWN_CODES.add(code)
            _LOG.warning(
                "burnable FBFM40 code %d has no FUEL_TABLE row; using "
                "conservative-fast GR2 fallback params (no-barrier guarantee)",
                code)
        fm = FUEL_TABLE[_FALLBACK_FUEL_CODE]
    return fm

# Sardoy/ELMFIRE spotting parameterization (digest §4): mean spot distance
# m = a * Q'^b * u20^c with Q' fireline intensity (kW/m) and u20 the 20-ft
# wind (m/s). ELMFIRE defaults a=5.0, b=0.3, c=0.7.
_SPOT_A = 5.0
_SPOT_FLIN_EXP = 0.3
_SPOT_WS_EXP = 0.7
# FUNCTIONAL SPOTTING (contracts SAFETY SEMANTICS 11, founder 2026-07-10). The
# pre-2026-07-10 hook threw ONE ember at the Sardoy MEAN distance, so every
# burning cell spotted the same distance and the front simply caught up —
# measured negligible (spotting on-vs-off reach 7.95 vs 7.96 km, 0.01 km no-op).
# The mean is now the MEAN of a LOGNORMAL landing-distance distribution with
# variance _SPOT_VAR_COEF * mean (digest §4 spread); exactly ONE landing
# distance is sampled per burning cell from
# seeded_rng('spot', incident_id, member_k, cell_index) — deterministic,
# member-seeded, NEVER wall-clock — so the heavy tail lands some brands
# 0.5-2 km downwind AHEAD of the front and ignites there. One draw per cell (the
# long tail, not many spots, makes the jumps). Default OFF => byte-identical.
_SPOT_VAR_COEF = 250.0

# FUEL-CLASS HEAD-ROS REALISM FLOOR (SIMULATOR only;
# docs/review/fire_speed_review.md — the validated grass prototype, EXTENDED
# 2026-07-11 to the developed/timber tiles). Rothermel structurally under-drives
# the fine/sheltered surface fuels at low-to-moderate midflame wind, so the
# demonstrated head crawls on the DEVELOPED/TIMBER tiles (santa_rosa_ca,
# paradise_ca) whose burnable mosaics are ~19-30% timber-litter + timber-
# understory, NOT grass — the grass-only floor barely helped them (~0.4-0.5 km/h
# span rate vs the real Tubbs/Camp several km/h). cfg.grass_ros_floor_frac (the
# master knob; default 0.0 => live byte-identical, sim 0.10) now sets a sim-only
# head-ROS floor PER FUEL CLASS, BEFORE the ellipse step:
#     head_ros = max(rothermel_ros, _floor_ratio(class) * frac * U10)
# ratios measured from each class's realized %U10 gap (measure_ros diagnostic):
#   GR grass             (101-109)  1.00 -> 0.10*U10  Cruz & Alexander cured-grass 10%-rule
#   GS grass-shrub       (121-124)  0.70 -> 0.07*U10  live-woody load; slower than open grass
#   TU timber-understory (161-165)  0.50 -> 0.05*U10  understory ~0.3-2.2%U10; wind-driven but sheltered
#   TL timber-litter     (181-189)  0.30 -> 0.03*U10  compact litter ~0.1-1.0%U10; low NON-crawling floor
# The class ordering GR > GS > TU > TL mirrors fuel structure (open+fine ->
# sheltered+compact). max() only ever RAISES a too-slow ROS: it defers to
# Rothermel at extreme wind (where the model is already right, e.g. TU3 hits
# 5.6%U10) and on slope-driven runs, NEVER lowers any ROS. SH shrub / SB slash /
# NB non-burnable are NEVER touched — SH5/SH7 chaparral already run 9-15%U10 (the
# model gets chaparral right; the malibu tile stays byte-identical), and the
# developed NB breaks are real (spotting, not surface ROS, jumps them).
_FLOOR_RATIO_GS = 0.7    # grass-shrub      : 0.07*U10 at the sim's 0.10 (review anchor)
_FLOOR_RATIO_TU = 0.5    # timber-understory: 0.05*U10 (understory carries; sheltered)
_FLOOR_RATIO_TL = 0.3    # timber-litter    : 0.03*U10 (slowest class; non-crawling floor)


def _ros_floor_frac(code: int, cfg) -> float:
    """Sim-only head-ROS floor as a fraction of the 10-m wind for FBFM40 `code`,
    keyed on fuel CLASS off the single cfg.grass_ros_floor_frac master knob
    (historical name — grass was the first floored class): GR (101-109) at frac,
    GS (121-124) at _FLOOR_RATIO_GS*frac, TU (161-165) at _FLOOR_RATIO_TU*frac,
    TL (181-189) at _FLOOR_RATIO_TL*frac, and 0.0 for every other fuel
    (SH / SB / NB, deliberately untouched) and whenever the knob is off (default
    0.0 => live byte-identical). See the _FLOOR_RATIO_* block above + the
    fire-speed review."""
    base = cfg.grass_ros_floor_frac
    if base <= 0.0:
        return 0.0
    if is_grass(code):
        return base
    if is_grass_shrub(code):
        return base * _FLOOR_RATIO_GS
    if is_timber_understory(code):
        return base * _FLOOR_RATIO_TU
    if is_timber_litter(code):
        return base * _FLOOR_RATIO_TL
    return 0.0


def _spot_distance(mean_m: float, incident_id: str, member_k: int,
                   cell_index: int) -> float:
    """One deterministic ember landing distance (m) for a burning cell.

    SAFETY SEMANTICS 11: mean_m (the Sardoy mean spot distance) is the MEAN of a
    LOGNORMAL with variance _SPOT_VAR_COEF * mean_m (digest §4). A single draw is
    taken from seeded_rng('spot', incident_id, member_k, cell_index) —
    deterministic, member-seeded, never wall-clock — so the heavy tail lands some
    brands 0.5-2 km downwind AHEAD of the front. Same seed => identical draw. The
    lognormal parameters map the (mean, var) moments exactly:
    sigma^2 = ln(1 + var/mean^2), mu = ln(mean) - sigma^2/2."""
    if mean_m <= 0.0:
        return 0.0
    sigma_sq = math.log(1.0 + _SPOT_VAR_COEF / mean_m)   # = ln(1 + var/mean^2)
    mu = math.log(mean_m) - 0.5 * sigma_sq
    rng = seeded_rng("spot", incident_id, member_k, cell_index)
    return rng.lognormvariate(mu, math.sqrt(sigma_sq))


def _offset_bearing_deg(dr: int, dc: int) -> float:
    """Compass bearing of lattice offset (dr, dc); row 0 is NORTH so +dr points
    south and +dc points east (grid treated as locally planar — the sub-degree
    bearing distortion across an operational domain is negligible)."""
    return math.degrees(math.atan2(dc, -dr)) % 360.0


def _build_offsets() -> tuple:
    """(dr, dc, dist_cells, bearing_deg, gate_offsets) for the 16 neighbors.

    gate_offsets are the cells straddling the edge's midline: the edge is
    traversable only if at least ONE gate cell is burnable (fire funnels
    through gaps — the fail-dangerous-fast polarity for an alerting product;
    requiring both would delay arrival through 1-cell gaps). Rook edges need
    no gates; diagonals gate on the two corner-adjacent cells; knight moves
    gate on the two cells beside the jump midpoint so a 1-cell-wide
    non-burnable barrier row/column cannot be leapt.
    """
    offs = []
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        offs.append((dr, dc, 1.0, _offset_bearing_deg(dr, dc), ()))
    for dr, dc in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
        gates = ((0, dc), (dr, 0))
        offs.append((dr, dc, _SQRT2, _offset_bearing_deg(dr, dc), gates))
    for dr, dc in ((-2, -1), (-2, 1), (2, -1), (2, 1),
                   (-1, -2), (-1, 2), (1, -2), (1, 2)):
        if abs(dr) == 2:
            gates = ((dr // 2, 0), (dr // 2, dc))
        else:
            gates = ((0, dc // 2), (dr, dc // 2))
        offs.append((dr, dc, _SQRT5, _offset_bearing_deg(dr, dc), gates))
    return tuple(offs)


_OFFSETS = _build_offsets()


def _perimeter_interior_cells(spec, rings: list) -> list:
    """Sorted row-major indices of cells whose CENTER lies inside the
    perimeter polygon (Sprint 2 ticket A5, Council-1 model F4).

    Point-in-polygon rule: EVEN-ODD (scanline crossing parity), so inner
    rings toggle back to outside and holes are honored without needing ring
    orientation — WFIGS/FIRIS rings carry no winding guarantee. Rings are
    treated as straight segments in (lon, lat) degree space; a spread domain
    spans well under a degree, so the planar approximation is sub-cell.
    One scanline per grid row at that row's cell-center latitude; the
    half-open vertical rule (lat_lo <= y < lat_hi) counts shared vertices
    exactly once. Rings with < 3 vertices are ignored (degenerate); rings are
    already subsampled to <= 200 vertices upstream (threat.py) per contracts.
    """
    nrows, ncols = spec.nrows, spec.ncols
    edges = []
    for ring in rings or ():
        pts = list(ring)
        if len(pts) >= 2 and pts[0].lat == pts[-1].lat and pts[0].lon == pts[-1].lon:
            pts = pts[:-1]                        # drop explicit ring closure
        if len(pts) < 3:
            continue
        for i in range(len(pts)):
            a = pts[i]
            b = pts[(i + 1) % len(pts)]
            if a.lat != b.lat:
                edges.append((a.lat, a.lon, b.lat, b.lon))
    if not edges:
        return []
    out = set()
    for r in range(nrows):
        lat = spec.north_lat - (r + 0.5) * spec.cell_m / geo.M_PER_DEG_LAT
        xs = []
        for alat, alon, blat, blon in edges:
            if (alat <= lat < blat) or (blat <= lat < alat):
                t = (lat - alat) / (blat - alat)
                xs.append(alon + t * (blon - alon))
        if not xs:
            continue
        xs.sort()
        step = spec.cell_m / geo.m_per_deg_lon(lat)
        base = r * ncols
        for i in range(0, len(xs) - 1, 2):
            # cells whose center lon lies in [xs[i], xs[i+1]]
            c_lo = math.ceil((xs[i] - spec.west_lon) / step - 0.5)
            c_hi = math.floor((xs[i + 1] - spec.west_lon) / step - 0.5)
            if c_lo < 0:
                c_lo = 0
            if c_hi > ncols - 1:
                c_hi = ncols - 1
            for c in range(c_lo, c_hi + 1):
                out.add(base + c)
    return sorted(out)


def compute_arrival(tile: TerrainTile, ignition: list, wx: WeatherForecast,
                    t0_s: float, cfg: ResponseConfig, moisture_scale: float = 1.0,
                    wind_scale: float = 1.0, dir_offset_deg: float = 0.0,
                    perimeter: list = None, perimeter_at_s: float = None,
                    incident_id: str = "", member_k: int = 0) -> Raster:
    """Fire arrival-time raster (absolute unix SECONDS per cell; UNREACHED=inf).

    ignition: list[LatLon] seed points (hotspots / origin / legacy perimeter
    vertices), all seeded at t0_s (unix seconds). Points outside the tile are
    ignored; no in-grid seed AND no perimeter => all-UNREACHED raster. Weather
    for an edge leaving cell u is wx.at(...) bucketed to the hour of the
    arrival time at u (time-dependent Dijkstra; non-FIFO effects at 1-h
    buckets are negligible, digest §3.3). moisture_scale multiplies the
    dead-fuel moisture fraction and wind_scale the 10-m wind speed
    (dimensionless ensemble knobs).

    dir_offset_deg (Sprint 2 ticket A4, Council-1 model F2): rotates the wind
    direction by the given compass offset everywhere weather is consumed —
    both the max-spread-direction vector math and the spotting bearing. The
    0.0 default is byte-identical to the pre-Sprint-2 behavior (pinned by
    tests/test_spread.py fingerprints).

    perimeter / perimeter_at_s (Sprint 2 ticket A5, Council-1 model F4): when
    perimeter rings (list[list[LatLon]], outer first) are given, every cell
    whose center falls INSIDE them (even-odd rule — see
    _perimeter_interior_cells) is already burned: arrival = perimeter_at_s
    (the FEED's updated_at_s, not now; defaults to t0_s if None) and the cell
    is FINAL — no later relaxation, no spot landing, no re-burn, even from an
    exterior seed with an earlier time. The Dijkstra front advances at
    perimeter_at_s from every mask cell whose 16-offset reach (the full edge
    set, knight moves included) holds a non-final cell — cells one ring
    inside the boundary own legal knight edges across it, so an 8-neighbor
    boundary test would ship a LATE exterior field (Session-2 verifier
    finding); with spotting enabled every mask cell seeds (the teleport edge
    has unbounded reach). A stale perimeter front is thus correctly ahead of
    a seeded-at-now front, while `ignition`
    points (e.g. FIRMS hotspots newer than the perimeter) still seed at t0_s
    — the union of both seed sets drives the search. Mask cells burning
    before t0_s consume the forecast's earliest sample (wx.at clamps) until
    the front crosses t0_s. A polygon so degenerate that no cell center falls
    inside (sub-cell sliver, < 3 vertices) falls back to marking the cells
    under its ring VERTICES as the burned mask.

    Cells whose FBFM40 code is non-burnable (NB 91-99, nodata) never receive
    arrival except as a direct seed and never propagate. Burnable codes absent
    from fuels.FUEL_TABLE spread with conservative-fast GR2 fallback params
    (never as barriers — the no-barrier guarantee, Sprint 2 ticket A3; a
    logging.warning fires once per unknown code per process).

    Max-spread direction per cell/hour: wind-blows-to bearing
    (wind_dir_deg + 180) vector-combined with a slope-equivalent virtual wind
    pointing upslope; contracts pins aspect_deg as the DOWNslope-facing
    direction, so upslope = aspect_deg + 180. The combined vector's magnitude
    feeds rothermel.surface_ros_mps as a pure wind on flat ground (FARSITE
    virtual-wind convention — slope enters only via its equivalent wind; the
    scalar-collinear path inside surface_ros_mps is bypassed).

    CONVENTION NOTE (physics review): this vector-combines the equivalent WINDS
    (midflame wind + slope-equivalent virtual wind, the latter from
    rothermel.effective_wind_mps(fuel, 0.0, slope, ...)), whereas digest §3.2 and
    rothermel.effective_wind_mps combine the PHI FACTORS
    (phi_E = sqrt(phi_w^2 + phi_s^2 + 2 phi_w phi_s cos angle)). Because
    phi_w = C*U^B is nonlinear in U, the two coincide EXACTLY only when one
    component is zero (pure slope or pure wind on flat ground — which is the case
    this borrows effective_wind_mps for); off-axis with both nonzero they diverge
    modestly (~8% high on the aligned head axis, ~10% low when wind opposes
    slope, measured SH5 @ 8 km/h + 25 deg). The head-axis bias is conservative-
    fast (correct polarity for an alerting product). spread.py deliberately does
    NOT route the combined case through effective_wind_mps's phi-vector seam;
    reconciling the two conventions is an integrator decision (see the physics
    review finding), not a correctness bug.

    Search is bounded at t0_s + cfg.horizon_h hours: later arrivals are
    UNREACHED (the forecast does not extend there). Cost O(cells * 16 * log)
    with one full Rothermel evaluation per unique (fuel_code, slope, aspect,
    hour-weather) combination and one fuel-bed build per (fuel_code, m_dead)
    — the B3 ROS memo (module docstring; env WGRESP_NO_ROS_MEMO=1 restores
    one evaluation per settled cell).

    Spotting (cfg.spotting_enabled, default OFF => zero effect; SAFETY
    SEMANTICS 11): each burning cell gets ONE deterministic downwind teleport
    edge. The Sardoy MEAN distance (a=5, b=0.3, c=0.7, ELMFIRE defaults) is the
    MEAN of a LOGNORMAL landing distribution (variance _SPOT_VAR_COEF * mean,
    digest §4); ONE landing distance is SAMPLED per burning cell from
    seeded_rng('spot', incident_id, member_k, cell_index) — deterministic,
    member-seeded, never wall-clock — so the heavy tail lands brands 0.5-2 km
    AHEAD of the front (the pre-2026-07-10 mean-only hook never got ahead:
    measured 0.01 km on-vs-off). The teleport lands at the sampled distance in
    the downwind bearing with traversal time distance / 20-ft wind (ember flight
    ~ wind speed). The per-cell draw lives at the teleport-edge site, NOT in the
    ROS memo, so cells sharing a memo key still draw independent distances.
    incident_id / member_k seed those draws (ensemble_arrival passes the member
    index) and are otherwise inert; with spotting OFF they are byte-identical
    no-ops.

    Fuel-class ROS realism floor (cfg.grass_ros_floor_frac, default 0.0 => zero
    effect; SIMULATOR only, docs/review/fire_speed_review.md): the head ROS is
    raised to max(rothermel_ros, ratio*frac * U10) BEFORE the ellipse step per
    fuel class — GR (1.0), GS (0.7), TU timber-understory (0.5), TL timber-litter
    (0.3) — so the developed/timber tiles (santa_rosa_ca, paradise_ca), not just
    grass, reach a plausible wind-driven head. The max() only ever RAISES a
    too-slow ROS and never touches SH / SB / NB fuels — see _ros_floor_frac.
    """
    spec = tile.spec
    nrows, ncols = spec.nrows, spec.ncols
    n = nrows * ncols
    cell_m = spec.cell_m

    fuel_of = [None] * n
    fdata = tile.fuel.data
    for i in range(n):
        code = int(round(fdata[i]))
        if is_burnable(code):
            fuel_of[i] = _fuel_for_code(code)

    best = [UNREACHED] * n
    heap = []
    final = None                 # bytearray flags for already-burned cells (A5)
    if perimeter:
        burn_t = t0_s if perimeter_at_s is None else float(perimeter_at_s)
        mask = _perimeter_interior_cells(spec, perimeter)
        if not mask:
            # Degenerate polygon fallback: cells under the ring vertices.
            vcells = set()
            for ring in perimeter:
                for p in ring:
                    rc = geo.latlon_to_rc(spec, p)
                    if rc is not None:
                        vcells.add(rc[0] * ncols + rc[1])
            mask = sorted(vcells)
        if mask:
            final = bytearray(n)
            for idx in mask:
                best[idx] = burn_t
                final[idx] = 1
            # Seed the search from every mask cell whose 16-offset REACH
            # (rook/diagonal AND knight moves — the exact edge set the
            # relaxation loop uses) contains a non-final cell. Session-2
            # verifier finding (scratch_review/attack_s2_probe_knightseed.py;
            # regression tests/test_attack_s2_spread.py): the previous
            # 8-neighbor boundary test dropped the knight edges owned by
            # cells one ring INSIDE the mask — legal relaxations that cross
            # the boundary — leaving exterior arrival fields LATE by up to
            # 14.4 min (100 m cells, 14 m/s wind), the anti-safe polarity
            # (late p10 => later latest_depart; late p50 => later EXTREME).
            # A mask cell with no non-final cell among its 16 offsets cannot
            # improve any label BY EDGE RELAXATION; the spotting teleport
            # edge has unbounded reach, so with spotting enabled EVERY mask
            # cell seeds (burned cells are burning cells — each owns one
            # deterministic spot edge, wherever it sits in the mask).
            seed_all = bool(cfg.spotting_enabled)
            for idx in mask:
                if seed_all:
                    heapq.heappush(heap, (burn_t, idx))
                    continue
                r, c = divmod(idx, ncols)
                for dr, dc, _dist, _brg, _gates in _OFFSETS:
                    nr = r + dr
                    nc = c + dc
                    if 0 <= nr < nrows and 0 <= nc < ncols and \
                            not final[nr * ncols + nc]:
                        heapq.heappush(heap, (burn_t, idx))
                        break
    for p in ignition:
        rc = geo.latlon_to_rc(spec, p)
        if rc is None:
            continue
        idx = rc[0] * ncols + rc[1]
        if final is not None and final[idx]:
            continue             # already burned at perimeter_at_s; final
        if t0_s < best[idx]:
            best[idx] = t0_s
            heapq.heappush(heap, (t0_s, idx))

    horizon_end_s = t0_s + cfg.horizon_h * 3600.0
    herb = cfg.live_herb_frac
    woody = cfg.live_woody_frac
    slope_d = tile.slope_deg.data
    aspect_d = tile.aspect_deg.data
    spotting = bool(cfg.spotting_enabled)

    def _max_spread_vector(u_wind: float, u_slope: float, wdir: float,
                           aspect: float):
        """(u_eff_mps, bearing_deg) of the max-spread direction: midflame wind
        vector-combined with the slope-equivalent virtual wind pointing
        upslope (see the CONVENTION NOTE above). wdir already carries the A4
        dir_offset_deg rotation."""
        to_wind = math.radians((wdir + 180.0) % 360.0)
        to_upslope = math.radians((aspect + 180.0) % 360.0)
        vx = u_wind * math.sin(to_wind) + u_slope * math.sin(to_upslope)
        vy = u_wind * math.cos(to_wind) + u_slope * math.cos(to_upslope)
        u_eff = math.hypot(vx, vy)
        brg = math.degrees(math.atan2(vx, vy)) % 360.0 if u_eff > 1e-12 else 0.0
        return u_eff, brg

    def _pack_params(res, wind10: float, wdir: float, brg: float, code: int):
        """(head_ros_mps, lb, bearing_deg, spot) from a RosResult + effective
        weather (shared tail of the memo and reference paths).

        Two sim-only calibrations live HERE so the memo and reference paths
        apply them identically (byte-identical by construction):
          * the fuel-class head-ROS FLOOR raises head_ros to
            max(rothermel, ratio*frac*U10) for GR/GS/TU/TL (_ros_floor_frac); it
            never lowers ros, and lb (from the unfloored effective wind) and spot
            (from unfloored intensity) are untouched, so only the head magnitude
            scales — the ellipse anisotropy is preserved through the directional
            step;
          * spot carries the cell-INDEPENDENT (mean_m, bearing, u20): the Sardoy
            MEAN distance, downwind bearing, and 20-ft wind. The per-cell
            lognormal landing DRAW happens at the teleport-edge site keyed on the
            source cell (SEMANTICS 11) — NOT here — because cells sharing a
            ROS-memo key must draw independent landing distances."""
        lb = rothermel.ellipse_lb(res.effective_wind_mps)
        spot = None
        if spotting and wind10 > 0.0 and res.intensity_kwm > 0.0:
            u20 = wind10 / 1.15
            mean_m = _SPOT_A * res.intensity_kwm ** _SPOT_FLIN_EXP * u20 ** _SPOT_WS_EXP
            spot = (mean_m, (wdir + 180.0) % 360.0, u20)
        head_ros = res.ros_mps
        ff = _ros_floor_frac(code, cfg)
        if ff > 0.0 and wind10 > 0.0:
            floor = ff * wind10
            if head_ros < floor:
                head_ros = floor
        return (head_ros, lb, brg, spot)

    # B3 ROS memo (module docstring; replaces the (cell_idx, hour) cache that
    # measured 0.0% hits — each cell settles exactly once). Per-invocation
    # dicts: created here, garbage at return. herb/woody/spotting/scales/
    # dir_offset are invocation constants; everything else that can move a
    # float is in the keys, so hits are byte-identical to recomputation.
    hour_wx = {}     # hour -> (wind10, m_dead, wdir) after scaling + offset
    bed_cache = {}   # (fuel_code, m_dead) -> fuel-bed intermediate (or None)
    ros_cache = {}   # (fuel_code, slope, aspect, wind10, m_dead, wdir) -> p

    def cell_params(idx: int, hour: int):
        fuel = fuel_of[idx]
        wt = hour_wx.get(hour)
        if wt is None:
            s = wx.at(t0_s + hour * 3600.0)
            # A4 ensemble knob: rotate the wind direction wherever weather is
            # consumed (max-spread vector AND spotting bearing). +0.0 is an
            # exact float identity, keeping the default byte-identical.
            wt = (max(0.0, s.wind_speed_mps * wind_scale),
                  max(0.01, dead_fuel_moisture_frac(s.temp_c, s.rh_pct)
                      * moisture_scale),
                  s.wind_dir_deg + dir_offset_deg)
            hour_wx[hour] = wt
        wind10, m_dead, wdir = wt
        slope = slope_d[idx]
        aspect = aspect_d[idx]
        key = (fuel.code, slope, aspect, wind10, m_dead, wdir)
        p = ros_cache.get(key)
        if p is None:
            bkey = (fuel.code, m_dead)
            bed = bed_cache.get(bkey, _BED_MISS)
            if bed is _BED_MISS:
                bed = rothermel.fuel_bed(fuel, m_dead, herb, woody)
                bed_cache[bkey] = bed
            u_wind = rothermel.midflame_wind_mps(wind10, fuel)
            u_slope = rothermel.effective_wind_from_bed(bed, 0.0, slope)
            u_eff, brg = _max_spread_vector(u_wind, u_slope, wdir, aspect)
            p = _pack_params(rothermel.surface_ros_from_bed(bed, u_eff, 0.0),
                             wind10, wdir, brg, fuel.code)
            if len(ros_cache) < _ROS_CACHE_MAX:
                ros_cache[key] = p
        return p

    def cell_params_reference(idx: int, hour: int):
        """WGRESP_NO_ROS_MEMO=1 escape hatch: the pre-B3 structure — every
        quantity computed fresh per settled cell through the classic rothermel
        seams. The byte-identity oracle for the memo path above."""
        s = wx.at(t0_s + hour * 3600.0)
        fuel = fuel_of[idx]
        wind10 = max(0.0, s.wind_speed_mps * wind_scale)
        m_dead = max(0.01, dead_fuel_moisture_frac(s.temp_c, s.rh_pct)
                     * moisture_scale)
        u_wind = rothermel.midflame_wind_mps(wind10, fuel)
        u_slope = rothermel.effective_wind_mps(fuel, 0.0, slope_d[idx],
                                               m_dead, herb, woody)
        wdir = s.wind_dir_deg + dir_offset_deg
        u_eff, brg = _max_spread_vector(u_wind, u_slope, wdir, aspect_d[idx])
        res = rothermel.surface_ros_mps(fuel, m_dead, herb, woody, u_eff, 0.0)
        return _pack_params(res, wind10, wdir, brg, fuel.code)

    if os.environ.get("WGRESP_NO_ROS_MEMO") == "1":
        cell_params = cell_params_reference

    while heap:
        t_u, idx = heapq.heappop(heap)
        if t_u > best[idx]:
            continue
        if fuel_of[idx] is None:
            continue          # seeded non-burnable cell: recorded, no spread
        r, c = divmod(idx, ncols)
        hour = int((t_u - t0_s) // 3600.0)
        head, lb, brg, spot = cell_params(idx, hour)
        for dr, dc, dist_cells, ebrg, gates in _OFFSETS:
            nr = r + dr
            nc = c + dc
            if not (0 <= nr < nrows and 0 <= nc < ncols):
                continue
            nidx = nr * ncols + nc
            if fuel_of[nidx] is None or best[nidx] <= t_u:
                continue
            if final is not None and final[nidx]:
                continue         # interior cells are final; never relaxed
            if gates:
                passable = False
                for gr, gc in gates:
                    ar = r + gr
                    ac = c + gc
                    if 0 <= ar < nrows and 0 <= ac < ncols and \
                            fuel_of[ar * ncols + ac] is not None:
                        passable = True
                        break
                if not passable:
                    continue
            ros = rothermel.directional_ros_mps(head, lb, ebrg - brg)
            t_v = t_u + (dist_cells * cell_m) / max(ros, _ROS_FLOOR_MPS)
            if t_v < best[nidx] and t_v <= horizon_end_s:
                best[nidx] = t_v
                heapq.heappush(heap, (t_v, nidx))
        if spot is not None:
            mean_m, sbrg, u20 = spot
            # SEMANTICS 11: draw ONE lognormal landing distance for THIS burning
            # cell (seeded on the source cell => cells sharing a ROS-memo key
            # still draw independently; the heavy tail lands brands ahead of the
            # front). Draws below one cell are self-cell no-ops.
            dist_m = _spot_distance(mean_m, incident_id, member_k, idx)
            if dist_m >= cell_m:
                land = geo.latlon_to_rc(
                    spec, geo.destination(geo.rc_to_latlon(spec, r, c), sbrg,
                                          dist_m))
                if land is not None:
                    nidx = land[0] * ncols + land[1]
                    t_v = t_u + dist_m / max(u20, 0.1)   # ember flight ~ 20-ft wind
                    if fuel_of[nidx] is not None and t_v < best[nidx] \
                            and t_v <= horizon_end_s \
                            and not (final is not None and final[nidx]):
                        best[nidx] = t_v
                        heapq.heappush(heap, (t_v, nidx))

    return Raster(spec, best)


def _quantile(sorted_vals: list, q: float) -> float:
    """Linear-interpolated quantile of pre-sorted values (may contain inf),
    using HAZEN plotting positions (sample i sits at quantile (i+0.5)/n).

    Sprint 2 ticket A4 rationale: ensemble members draw at the evenly spaced
    quantile midpoints (j+0.5)/m of the perturbation range (see
    _stratified_member_draws), so Hazen positions make the sample quantiles
    line up with the strata instead of interpolating them toward the median
    as the previous q*(n-1) convention did. The median is IDENTICAL under
    both conventions (0.5*n - 0.5 == 0.5*(n-1)); the tails move outward —
    p10 earlier, p90 later — which is the fail-safe polarity for every
    consumer (crew safety keys off p10 being pessimistic-EARLY).

    Any interpolation step toward UNREACHED yields UNREACHED (never inf-inf)."""
    npts = len(sorted_vals)
    pos = q * npts - 0.5
    if pos < 0.0:
        pos = 0.0
    elif pos > npts - 1:
        pos = float(npts - 1)
    lo = int(math.floor(pos))
    hi = lo + 1 if lo + 1 < npts else npts - 1
    frac = pos - lo
    a = sorted_vals[lo]
    b = sorted_vals[hi]
    if frac <= 0.0 or a == b:
        return a
    if math.isinf(b):
        return UNREACHED
    return a + frac * (b - a)


# Direction-error growth saturates at this lead time (ticket A4:
# offset(t) = draw_k * cfg.dir_perturb_deg * min(1, lead_h / 48)).
_DIR_RAMP_SATURATION_H = 48.0


def _stratified_member_draws(incident_id: str, m: int) -> list:
    """m Latin-hypercube-style perturbation triples (wind_u, moisture_u,
    dir_u), each component in [-1, 1] (Sprint 2 ticket A4, Council-1 F2+F6).

    Seeding law (contracts): every member's randomness comes ONLY from
    contracts.seeded_rng('ensemble-v2', incident_id, k) for member index
    k = 1..m — never wall-clock and never t0 — so consecutive forecast cycles
    of the same incident reuse IDENTICAL perturbations (the F6 jitter fix).

    Stratification: each dimension's values are the m evenly spaced quantile
    midpoints of [-1, 1] (2*(j+0.5)/m - 1, j = 0..m-1). The assignment of
    midpoint j to member k is the rank of member k's per-dimension sort key
    drawn from its own RNG — a deterministic shuffle keyed on
    (incident_id, k) that decorrelates the three dimensions while using every
    stratum exactly once per dimension (true Latin hypercube), unlike the v1
    iid uniforms whose 6-sample quantiles caused the measured 12-16% cycle
    jitter. m = 1 degenerates to the single midpoint 0.0 (no perturbation):
    dispersion needs ensemble_n >= 3.
    """
    keys = []
    for k in range(1, m + 1):
        rng = seeded_rng("ensemble-v2", incident_id, k)
        keys.append((rng.random(), rng.random(), rng.random()))
    draws = [[0.0, 0.0, 0.0] for _ in range(m)]
    for d in range(3):
        order = sorted(range(m), key=lambda i: keys[i][d])
        for rank, i in enumerate(order):
            draws[i][d] = 2.0 * (rank + 0.5) / m - 1.0
    return [tuple(t) for t in draws]


def ensemble_arrival(tile: TerrainTile, ignition: list, wx: WeatherForecast,
                     t0_s: float, cfg: ResponseConfig, incident_id: str = "",
                     perimeter: list = None,
                     perimeter_at_s: float = None) -> EnsembleArrival:
    """Ensemble v2 (Sprint 2 ticket A4, Council-1 model F2+F6): nominal member
    (k=0, unperturbed) + (cfg.ensemble_n - 1) perturbed members, plus per-cell
    p10/p50/p90 quantile rasters (unix seconds; p10 = EARLIEST/pessimistic).

    p10 is NOMINAL-BOUNDED (Session-2 verifier blocking finding): per cell,
    p10 = min(Hazen sample p10, nominal arrival). A pessimistic arrival that
    trails the engine's own best-estimate run is anti-safe — every crew
    deadline (latest_depart = p10 - egress buffer), the eroded-p10 corridor
    rasters, and the extreme_p10_h ladder trigger inherit it — and the Hazen
    estimator does exactly that at head-axis clients whenever the stratified
    pairing (a pure function of incident_id) makes the nominal run the strict
    member minimum (~49% of incident ids; measured +4.1 to +10.8 min at
    3-8 km, growing with lead). See the inline comment at the quantile loop.

    Seeding law (contracts): member k's draws come from
    contracts.seeded_rng('ensemble-v2', incident_id, k) — NEVER from t0_s or
    the ignition geometry — so consecutive forecast cycles of one incident
    reuse identical perturbations and per-client ETAs are cycle-stable (F6:
    was 12-16% jitter from re-seeding on round(t0_s) with iid draws).

    Member k draws a stratified Latin-hypercube triple in [-1, 1]^3
    (_stratified_member_draws) applied as:
      wind_scale     = 1 + u_w * cfg.wind_perturb_frac     * 0.5
      moisture_scale = 1 + u_m * cfg.moisture_perturb_frac * 0.5
      dir_offset_deg = u_d * cfg.dir_perturb_deg * min(1, (horizon_h/2) / 48)
    Speed/moisture keep the v1 lead treatment: error grows ~linearly with
    lead up to the config fraction at max lead, collapsed to a constant equal
    to the horizon-MEAN of the ramp (half the max). Scales floored at 0.05.

    Direction (F2) is applied as a CONSTANT per-member rotation, not the
    ideal time-varying offset(t) = u_d * dir_perturb_deg * min(1, lead_h/48):
    rotating the wind mid-run would make edge costs depend on when Dijkstra
    relaxes them, breaking the label-setting invariant (a settled label could
    become improvable — not label-safe). The constant uses the ramp evaluated
    at the MID-HORIZON lead (horizon_h/2, saturating at 48 h), preserving the
    horizon-average direction spread; at the default 96 h horizon the factor
    is 1.0, i.e. member offsets span the full +-dir_perturb_deg.

    perimeter / perimeter_at_s are forwarded verbatim to every member's
    compute_arrival (ticket A5): the burned interior is FIXED across members
    (arrival = perimeter_at_s in every member, hence in every quantile).

    NOTE: ensemble_n counts TOTAL members (nominal + n-1 perturbed), so
    ensemble_n=1 is nominal-only, matching the v1 convention the suite pins;
    contracts' config comment reads it as perturbed-count — the discrepancy
    predates Sprint 2 and TOTAL is kept for continuity. ensemble_n=2's single
    perturbed member sits at the stratum midpoint 0.0 (= a nominal duplicate).
    Returned EnsembleArrival.incident_id carries the incident_id argument.
    """
    pts = list(ignition)
    n_members = max(1, int(cfg.ensemble_n))
    # member_k threads the spotting seed (SEMANTICS 11): each member draws its
    # own lognormal ember distances (member_k), stable across cycles because the
    # seed is (incident_id, k) — never t0. With spotting OFF it is an inert
    # no-op, so every member stays byte-identical to the pre-SEMANTICS-11 field.
    members = [compute_arrival(tile, pts, wx, t0_s, cfg,
                               perimeter=perimeter,
                               perimeter_at_s=perimeter_at_s,
                               incident_id=incident_id, member_k=0)]
    draws = _stratified_member_draws(incident_id, n_members - 1)
    dir_lead = min(1.0, (cfg.horizon_h * 0.5) / _DIR_RAMP_SATURATION_H)
    for k in range(1, n_members):
        u_w, u_m, u_d = draws[k - 1]
        w_scale = max(0.05, 1.0 + u_w * cfg.wind_perturb_frac * 0.5)
        m_scale = max(0.05, 1.0 + u_m * cfg.moisture_perturb_frac * 0.5)
        d_off = u_d * cfg.dir_perturb_deg * dir_lead
        members.append(compute_arrival(tile, pts, wx, t0_s, cfg,
                                       moisture_scale=m_scale, wind_scale=w_scale,
                                       dir_offset_deg=d_off,
                                       perimeter=perimeter,
                                       perimeter_at_s=perimeter_at_s,
                                       incident_id=incident_id, member_k=k))

    ncells = tile.spec.nrows * tile.spec.ncols
    if len(members) == 1:
        p10d = list(members[0].data)
        p50d = list(members[0].data)
        p90d = list(members[0].data)
    else:
        cols = [m.data for m in members]
        nom = cols[0]
        p10d = [0.0] * ncells
        p50d = [0.0] * ncells
        p90d = [0.0] * ncells
        for i in range(ncells):
            vals = sorted(col[i] for col in cols)
            q10 = _quantile(vals, 0.10)
            # Nominal-bounded pessimism (Session-2 verifier BLOCKING finding;
            # scratch_review/attack_s2_ensemble.py + attack_s2_probe_idscan.py,
            # regression tests/test_attack_s2_spread.py): the Hazen p10
            # interpolates ABOVE the sample minimum (6 members: s0 +
            # 0.1*(s1 - s0)), so whenever the LHS pairing — a pure function of
            # incident_id — leaves the NOMINAL run the strict minimum along
            # some bearing (head-axis clients; ~49% of ids in the analytic
            # screen), the raw sample quantile lands LATER than the engine's
            # own best-estimate arrival, and the A4 cycle-stable seeding
            # freezes that anti-safe bias for the incident's entire life.
            # Every crew deadline keys off p10 (SAFETY SEMANTICS 2/3), so the
            # PESSIMISTIC quantile may never trail the nominal member: clamp
            # p10 = min(Hazen p10, nominal). The clamp binds exactly where the
            # hazard exists (nominal the strict member minimum — bytes are
            # unchanged everywhere else), never drops p10 below the earliest
            # member, and preserves p10 <= p50 <= p90 (it can only lower p10).
            p10d[i] = q10 if q10 <= nom[i] else nom[i]
            p50d[i] = _quantile(vals, 0.50)
            p90d[i] = _quantile(vals, 0.90)
    return EnsembleArrival(
        spec=tile.spec,
        members=members,
        p10=Raster(tile.spec, p10d),
        p50=Raster(tile.spec, p50d),
        p90=Raster(tile.spec, p90d),
        t0_s=t0_s,
        incident_id=incident_id,
    )


# EMBER / ADJACENCY THREAT fallback (FOUNDER ROUND 4 law 6). Attached lazily to
# the EnsembleArrival instance (a plain dataclass, no __slots__): the eroded
# quantile rasters for a given radius, computed once per (ensemble, radius).
_EMBER_ERODE_ATTR = "_ember_erode_cache"


def _ember_eroded(ens: EnsembleArrival, radius_m: float):
    """(er10, er50, er90): the ensemble p10/p50/p90 rasters passed through
    geo.erode_min at `radius_m`, MEMOISED on `ens` keyed by radius.

    FOUNDER ROUND 4 law 6 (EMBER / ADJACENCY THREAT). geo.erode_min is the same
    corridor-erosion morphological MINIMUM filter used for P10 corridor safety:
    over a square window of ceil(radius_m/cell_m) cells it writes the EARLIEST
    (minimum) arrival within the window into every cell, so a non-burnable
    developed/water cell (own arrival UNREACHED=inf, which is the max and never
    the window min while any burnable neighbour is finite) inherits the soonest
    arrival among burnable cells within the radius.

    The result is cached on `ens` so a whole-roster tick (e.g. 48 clients on one
    fire's ensemble) erodes each quantile ONCE per radius, not once per client
    (erode_min is O(n*k); re-eroding per client would be 48x the work). Each ens
    instance owns its own cache dict, so there is no cross-incident leakage.

    Erosion preserves p10 <= p50 <= p90: erode_min is a per-cell window minimum
    applied to each quantile independently, and min over a window of a pointwise-
    smaller field is <= min of the larger one, so the cell-wise p10<=p50<=p90
    ordering of the inputs carries to the eroded outputs. Asserted here — once
    per radius, not per client."""
    cache = getattr(ens, _EMBER_ERODE_ATTR, None)
    if cache is None:
        cache = {}
        setattr(ens, _EMBER_ERODE_ATTR, cache)
    trip = cache.get(radius_m)
    if trip is None:
        er10 = geo.erode_min(ens.p10, radius_m)
        er50 = geo.erode_min(ens.p50, radius_m)
        er90 = geo.erode_min(ens.p90, radius_m)
        assert all(a <= b <= c for a, b, c in
                   zip(er10.data, er50.data, er90.data)), \
            "ember erosion violated p10 <= p50 <= p90 monotonicity"
        trip = (er10, er50, er90)
        cache[radius_m] = trip
    return trip


def eta_at(ens: EnsembleArrival, loc: LatLon,
           fallback_radius_m: float = 0.0) -> ThreatEta:
    """ThreatEta (absolute unix SECONDS) at `loc` from the ensemble quantile
    rasters (nearest-cell sample). Outside the grid, or in cells the fire
    never reaches within the horizon, values are contracts.UNREACHED (inf).

    EMBER / ADJACENCY THREAT fallback (FOUNDER ROUND 4 law 6). fallback_radius_m
    defaults to 0.0 => the direct nearest-cell sample only, BYTE-IDENTICAL to the
    pre-law behaviour (this is the live-product path; the pinned spread suite
    pins it). The SIMULATOR passes cfg.ember_threat_radius_m (~200 m): when the
    client's OWN cell is entirely UNREACHED (its p10 is inf, hence p50/p90 are
    too since p10<=p50<=p90 — the surface-spread model can NEVER reach a home
    sitting ON a non-burnable developed 91/93 or water 98 cell, so it gets no
    ETA / alert / dispatch even when fire surrounds it), the ETA is taken instead
    from geo.erode_min(quantile, fallback_radius_m): the EARLIEST arrival among
    burnable cells within the radius.

    THREAT SEMANTICS: the returned time is when the fire reaches an ADJACENT
    burnable cell within `fallback_radius_m` — an ADJACENCY / EMBER PROXY for a
    developed parcel surrounded by fire, NOT a modeled surface-fire arrival at
    the developed cell itself (a surface fire cannot burn pavement). Polarity is
    strictly MORE homes threatened, which is conservative for an alerting
    product. A home whose OWN cell is reached is unaffected — the own-cell value
    wins (the fallback fires only when the own cell is UNREACHED), so burnable
    clients are byte-identical with or without the fallback. The eroded rasters
    are memoised on `ens` per radius (see _ember_eroded), which also preserves
    p10 <= p50 <= p90."""
    def _sample(raster: Raster) -> float:
        v = geo.raster_sample(raster, loc)
        return UNREACHED if v is None else v

    p10 = _sample(ens.p10)
    p50 = _sample(ens.p50)
    p90 = _sample(ens.p90)

    # FOUNDER ROUND 4 law 6: own cell entirely UNREACHED (isinf(p10) => p50/p90
    # inf too) AND a fallback radius is configured => inherit the adjacency/ember
    # threat time from the earliest burnable arrival within fallback_radius_m.
    if fallback_radius_m > 0.0 and math.isinf(p10):
        er10, er50, er90 = _ember_eroded(ens, fallback_radius_m)
        p10 = _sample(er10)
        p50 = _sample(er50)
        p90 = _sample(er90)

    return ThreatEta(eta_p10_s=p10, eta_p50_s=p50, eta_p90_s=p90)
