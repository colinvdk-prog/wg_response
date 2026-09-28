"""hindcast_lib — pure logic for the Sprint-2 D1 hindcast + quantile-calibration
harness (Council-1 model F3+F8). See tools/hindcast_harness.py for the CLI and
the (live, read-only) fetch layer; everything in THIS module is deterministic,
network-free, and unit-tested by tests/test_hindcast.py.

What the harness measures
=========================
1. HINDCAST REPLAY: seed the shipped engine (spread.ensemble_arrival, default
   ResponseConfig, perimeter-interior seeding path) with an observed WFIGS
   perimeter snapshot T0 + observed (reanalysis) weather, run it forward, and
   score the predicted arrival-time quantile field against the NEXT observed
   perimeter T1. Because a perimeter pair only brackets each newly-burned
   cell's true burn time inside (T0, T1], every coverage statistic is reported
   as a CERTAIN/POSSIBLE bound pair, never a point estimate — the honest-
   numbers discipline.
2. LANDMARK ANCHORS (offline): parameterized Tubbs-run and Camp-growth
   scenario checks of the engine's core GR2/SH-mix ROS against the documented
   observed rates (docs/review/model_fidelity_review.md F3 measured the
   point-ROS ratio at ~0.47x observed Tubbs on Santa Ana grass).

Data sources (fetch layer; documented here because the URL builders live here)
==============================================================================
- WFIGS *Daily* fire perimeters: NIFC ArcGIS `WFIGS_Daily_Perimeters_Public`
  FeatureServer layer 0 — researched live 2026-07-09: unlike the CURRENT /
  YearToDate / InterAgencyFirePerimeterHistory layers (one latest/final
  polygon per fire), the Daily layer keeps EVERY published capture as its own
  row (measured up to 110 rows per IRWIN id), keyed by `poly_PolygonDateTime`
  (epoch ms). This is the perimeter TIME SERIES the ticket needs.
  Licensing: NIFC/WFIGS open data (public domain, keyless); server is Esri
  ArcGIS Online — read-only queries, modest volume, User-Agent sent.
- Observed weather: Open-Meteo HISTORICAL archive API
  (archive-api.open-meteo.com, keyless, ERA5-family reanalysis) at the fire
  centroid. LICENSING NOTE (mirrors wgresponse.weather.OpenMeteoWeather): the
  free tier is NON-COMMERCIAL ONLY and the data is CC-BY-4.0. This harness is
  an offline R&D calibration study, not the shipped product loop; any
  production/commercial use requires the paid tier or self-hosting.
- Terrain/fuel: LANDFIRE LFPS (lfps.usgs.gov, keyless, public domain) via the
  engine's own wgresponse.terrain.LfpsTerrain with a disk cache, so each
  incident tile is fetched exactly once. LF2024_FBFM40 fuels predate the
  replayed 2025 fires (pre-fire landscape — correct for hindcasting).

Units per contracts law: unix seconds `_s`, meters `_m`, m/s, compass degrees
(wind FROM), Celsius, percent RH. Grids row-major, row 0 = north.
"""
from __future__ import annotations

import json
import math
import statistics
import urllib.parse
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from wgresponse import geo, rothermel, spread
from wgresponse.contracts import (
    BBox,
    GridSpec,
    LatLon,
    Raster,
    ResponseConfig,
    TerrainTile,
    UNREACHED,
    WeatherForecast,
    WeatherSample,
)
from wgresponse.fuels import FUEL_TABLE, dead_fuel_moisture_frac, is_burnable

# ---------------------------------------------------------------------------
# Endpoints (fetch layer uses these; pinned here so tests can pin the URLs)
# ---------------------------------------------------------------------------

DAILY_PERIMETERS_URL = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
    "WFIGS_Daily_Perimeters_Public/FeatureServer/0/query")

# NON-COMMERCIAL free tier — see the module-docstring licensing note.
ARCHIVE_WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"

_ARCHIVE_HOURLY = ("temperature_2m,relative_humidity_2m,wind_speed_10m,"
                   "wind_direction_10m,wind_gusts_10m")

# Perimeter rings are subsampled to <= this many vertices before being handed
# to spread — the SAME law threat.py applies to live feed perimeters
# (contracts: "rings subsampled <= 200 vertices upstream").
MAX_RING_VERTS = 200
# Snapshot rings are STORED at a finer cap so the T1 scoring mask keeps more
# boundary fidelity than the engine seed needs (engine gets MAX_RING_VERTS).
STORE_RING_VERTS = 400

_MS_EPOCH_CUTOFF = 1e11          # epoch ms vs s discriminator (feeds law)


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------

@dataclass
class PerimeterSnapshot:
    """One observed perimeter capture: rings (list[list[LatLon]], outer rings
    and holes mixed — even-odd rule applies) at capture time t_s (unix s)."""
    t_s: float
    rings: list
    acres: float = 0.0
    oids: list = field(default_factory=list)   # source OBJECTIDs (provenance)


@dataclass
class HindcastIncident:
    irwin: str
    name: str
    snapshots: list = field(default_factory=list)   # PerimeterSnapshot, by t_s
    max_acres: float = 0.0
    # Capture times (t_s) of snapshots a pre-fix (hindcast-incident/1) file
    # built by unioning same-time rows — see SNAPSHOT RULE. Non-empty means
    # the file must be rebuilt (`hindcast_harness.py repair`) before replay.
    stacked_legacy: list = field(default_factory=list)
    # (west, south, east, north) pinned by `repair` when the rebuilt
    # snapshots would move the replay domain off the cached terrain tile.
    domain_bbox: tuple = None


@dataclass
class PairMetrics:
    """Scores for one (T0 -> T1) replay. All cell counts are cells of the
    replay grid; areas derive from cell_m^2. Interval-censored observation:
    a perimeter pair only brackets each newly-burned cell's true burn time
    inside (T0, T1].

    HONEST (two-sided) fields:
      late_miss         p10 > T1  (band entirely after the window — engine
                        definitively LATE for that cell; the DANGEROUS
                        polarity: a late p10 delays crew deadlines and
                        EXTREME triggers).
      never_reached     p10 = inf (no pessimistic arrival within the horizon
                        at all — counted inside late_miss as its worst case).
      p10_not_after_window  p10 <= T1 — NECESSARY for p10 <= true burn time;
                        this is the real, one-sided statistic the old
                        `covered_possible` field silently was. pool_metrics
                        reports its pooled fraction as `p10_adequacy_frac`.
      head_m_p10/p50/p90/nominal, modeled_new_area_ac_p10/p50 vs
                        obs_head_m / obs_new_area_ac give two-sided
                        head-ratio / area-ratio statistics (>1 = engine runs
                        farther/bigger than observed; <1 = engine short).
      null_new_covered  isotropic-dilation null-model score — see the
                        module-level `squared_edt` / `null_model_covered`
                        docstrings and pool_metrics' `null_adequacy_frac`.
      pit_hist          PIT bin counts (approx, at window midpoint) — never
                        reported before this fix even though it was always
                        computed; pool_metrics adds an extreme-mass +
                        flatness summary.

    DEPRECATED (tri-model review 2026-08-31,
    docs/TRI_MODEL_REVIEW_2026-08-31.md fix-program item 5) — kept ONLY for
    JSON compatibility with old readers, computed exactly as before, but
    NEVER presented as calibration evidence (see docs/calibration/SCORECARD.md):
      covered_certain   [p10,p90] contains ALL of [T0,T1]. Needs p10<=T0,
                        which is ~0 BY CONSTRUCTION (the engine seeds at T0;
                        no member can arrive before its own seed) — measured
                        ~0 in every pool ever scored.
      covered_possible  [p10,p90] intersects [T0,T1]. Because the engine
                        seeds at T0, p90 >= T0 ALWAYS holds for any reached
                        cell, so this collapses EXACTLY to `p10 <= T1`
                        (= p10_not_after_window) — a ONE-SIDED statistic
                        dressed as a two-sided interval check.
      early_miss        p90 < T0. STRUCTURALLY IMPOSSIBLE: the engine never
                        reports an arrival before its own T0 seed, so
                        p90 >= T0 always and this field is 0 in every pool
                        ever scored (mutation-tested: even a 10x-wind config
                        still scores 0 here). "0% early-miss" is a tautology
                        of the replay design, not evidence of calibration.
    """
    incident: str = ""
    name: str = ""
    t0_s: float = 0.0
    t1_s: float = 0.0
    dt_h: float = 0.0
    cell_m: float = 0.0
    label: str = "default"          # config variant label
    n_new: int = 0                  # newly-burned cells (observed growth)
    n_new_burnable: int = 0         # ... with a burnable FBFM40 code (scored)
    n_new_nonburnable: int = 0      # ... LANDFIRE non-burnable (urban/etc.; unscorable)
    covered_certain: int = 0
    covered_possible: int = 0
    late_miss: int = 0
    early_miss: int = 0
    never_reached: int = 0
    p90_unreached: int = 0          # p90 = inf: 'covered' only because the band
                                    # stretches to infinity — reported, not hidden
    p50_in_window: int = 0
    p10_not_after_window: int = 0   # p10 <= T1: NECESSARY for p10 <= true time
    p10_before_window: int = 0      # p10 <= T0: SUFFICIENT for p10 <= true time
    pit_hist: list = field(default_factory=list)    # PIT bins (approx, midpoint)
    obs_head_m: float = 0.0
    head_m_p50: float = 0.0
    head_m_p10: float = 0.0
    head_m_p90: float = 0.0
    head_m_nominal: float = 0.0
    obs_new_area_ac: float = 0.0
    modeled_new_area_ac_p50: float = 0.0
    modeled_new_area_ac_p10: float = 0.0
    # NULL-MODEL SCORE (tri-model review 2026-08-31, item 5; ported from the
    # PASS-2 independent verification scratch_review/rv2sci_a2_null.py):
    # isotropic (Euclidean nearest-first) dilation of the engine-T0 interior
    # out to the SAME cell count as the engine's own p10 new-area budget
    # (modeled_new_area_ac_p10, i.e. cells with p10<=T1 excluding engine_t0)
    # — a physics-free "just grow a circle at the engine's own area budget"
    # baseline. null_new_covered = how many of the truly newly-burned
    # burnable cells that equal-area circle would have covered. Compare
    # against p10_not_after_window at the SAME denominator (n_new_burnable):
    # skill = p10_adequacy / null_adequacy — see pool_metrics.
    null_new_covered: int = 0
    by_fuel_class: dict = field(default_factory=dict)  # class -> {n, late, cert, poss}
    notes: list = field(default_factory=list)

    # -- derived, guarded against n=0 --
    def frac(self, count: int) -> float:
        return count / self.n_new_burnable if self.n_new_burnable else 0.0

    def head_ratio_p50(self) -> float:
        return self.head_m_p50 / self.obs_head_m if self.obs_head_m > 0 else 0.0

    def head_ratio_p10(self) -> float:
        return self.head_m_p10 / self.obs_head_m if self.obs_head_m > 0 else 0.0

    def head_ratio_p90(self) -> float:
        return self.head_m_p90 / self.obs_head_m if self.obs_head_m > 0 else 0.0

    def head_ratio_nominal(self) -> float:
        return self.head_m_nominal / self.obs_head_m if self.obs_head_m > 0 else 0.0

    def area_ratio_p50(self) -> float:
        return (self.modeled_new_area_ac_p50 / self.obs_new_area_ac
                if self.obs_new_area_ac > 0 else 0.0)

    def area_ratio_p10(self) -> float:
        return (self.modeled_new_area_ac_p10 / self.obs_new_area_ac
                if self.obs_new_area_ac > 0 else 0.0)

    def null_adequacy(self) -> float:
        return self.frac(self.null_new_covered)

    def p10_adequacy(self) -> float:
        """The honest rename of the old covered_possible: P(p10 <= T1)."""
        return self.frac(self.p10_not_after_window)

    def skill(self):
        """Engine p10-adequacy / null-model adequacy at equal modeled area.
        > 1 = the physics beats an equal-area isotropic circle; None when
        the null model covered nothing (avoid a /0 — not the same as 0
        skill)."""
        na = self.null_adequacy()
        return (self.p10_adequacy() / na) if na > 0 else None


@dataclass
class AnchorResult:
    """One landmark-anchor scenario outcome. ratio = engine / observed —
    < 1 means the engine is SLOWER than the historical fire (LATE polarity)."""
    anchor: str                     # 'tubbs' | 'camp'
    fuel_label: str                 # 'GR2' | 'SH5' | 'GR2/SH5 mix'
    spotting: bool
    metric: str                     # e.g. 'head km/h over 3 h'
    observed: float
    engine: float
    ratio: float
    point_ros_kmh: float = 0.0      # single-cell Rothermel head ROS (no grid)
    notes: str = ""


# ---------------------------------------------------------------------------
# Small helpers (mirrors of engine laws, kept dependency-light)
# ---------------------------------------------------------------------------

def parse_iso8601_s(s: str) -> float:
    """ISO-8601 timestamp -> unix seconds; naive treated as UTC ('Z' ok)."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def epoch_s(v) -> float:
    """ArcGIS epoch (ms or s) -> unix seconds. Raises on None."""
    f = float(v)
    return f / 1000.0 if f > _MS_EPOCH_CUTOFF else f


def subsample_ring(ring: list, max_verts: int = MAX_RING_VERTS) -> list:
    """Every ceil(n/max_verts)-th vertex, first kept — the same subsample law
    wgresponse.threat applies to live perimeters before seeding spread."""
    n = len(ring)
    if n <= max_verts:
        return list(ring)
    step = -(-n // max_verts)
    return list(ring[::step])


def rings_bbox(rings_list: list, pad_m: float) -> BBox:
    """BBox around every vertex of every ring in a list of ring-lists."""
    pts = [p for rings in rings_list for ring in rings for p in ring]
    return geo.bbox_around(pts, pad_m)


def pick_cell_m(bbox: BBox, cfg: ResponseConfig) -> float:
    """cfg.grid_cell_m, coarsened so the domain fits cfg.grid_max_cells —
    the same sizing law as threat.ThreatMonitor._pick_cell_m."""
    ctr = bbox.center()
    w_m = max((bbox.east - bbox.west) * geo.m_per_deg_lon(ctr.lat),
              cfg.grid_cell_m)
    h_m = max((bbox.north - bbox.south) * geo.M_PER_DEG_LAT, cfg.grid_cell_m)
    needed = math.sqrt((w_m * h_m) / float(max(1, cfg.grid_max_cells)))
    return max(cfg.grid_cell_m, needed)


def polygon_interior_mask(spec: GridSpec, rings: list) -> set:
    """Row-major indices of cells whose CENTER is inside the polygon —
    even-odd scanline rule, same convention as spread._perimeter_interior_cells
    (independent implementation, unit-tested; sub-cell boundary divergence from
    the engine's rasterizer is possible and handled by callers via the
    engine-seeded-mask subtraction, never assumed away)."""
    edges = []
    for ring in rings or ():
        pts = list(ring)
        if len(pts) >= 2 and pts[0].lat == pts[-1].lat and pts[0].lon == pts[-1].lon:
            pts = pts[:-1]
        if len(pts) < 3:
            continue
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            if a.lat != b.lat:
                edges.append((a.lat, a.lon, b.lat, b.lon))
    out: set = set()
    if not edges:
        return out
    ncols = spec.ncols
    for r in range(spec.nrows):
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
            c_lo = max(0, math.ceil((xs[i] - spec.west_lon) / step - 0.5))
            c_hi = min(ncols - 1, math.floor((xs[i + 1] - spec.west_lon) / step - 0.5))
            for c in range(c_lo, c_hi + 1):
                out.add(base + c)
    return out


def boundary_cells(mask: set, spec: GridSpec) -> list:
    """Sorted indices of mask cells with at least one 4-neighbor outside the
    mask (or on the grid edge) — the discrete boundary."""
    ncols, nrows = spec.ncols, spec.nrows
    out = []
    for idx in mask:
        r, c = divmod(idx, ncols)
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            if not (0 <= nr < nrows and 0 <= nc < ncols) or \
                    (nr * ncols + nc) not in mask:
                out.append(idx)
                break
    return sorted(out)


def max_dist_m(from_cells: list, to_cells: list, spec: GridSpec,
               max_from: int = 400, max_to: int = 400) -> float:
    """max over `from_cells` of (min distance to `to_cells`), meters, on the
    metric grid (cells are cell_m squares — planar distance is exact for the
    grid geometry). Both sets are deterministically strided down to the caps
    (max of a min can only shrink with subsampling — reported values are
    therefore conservative LOWER bounds on observed head run and modeled head
    run alike, same bias on both sides of the ratio)."""
    if not from_cells or not to_cells:
        return 0.0
    fr = from_cells[::max(1, len(from_cells) // max_from)]
    to = to_cells[::max(1, len(to_cells) // max_to)]
    ncols = spec.ncols
    to_rc = [divmod(i, ncols) for i in to]
    best_overall = 0.0
    for idx in fr:
        r, c = divmod(idx, ncols)
        d2min = min((r - tr) * (r - tr) + (c - tc) * (c - tc)
                    for tr, tc in to_rc)
        if d2min > best_overall:
            best_overall = d2min
    return math.sqrt(best_overall) * spec.cell_m


FUEL_CLASSES = (("GR", range(101, 110)), ("GS", range(121, 125)),
                ("SH", range(141, 150)), ("TU", range(161, 166)),
                ("TL", range(181, 190)), ("SB", range(201, 205)))


def fuel_class(code: int) -> str:
    """FBFM40 class letter-group for a fuel code ('NB' for non-burnable)."""
    if not is_burnable(code):
        return "NB"
    for label, rng in FUEL_CLASSES:
        if code in rng:
            return label
    return "??"


# ---------------------------------------------------------------------------
# Null-model baseline (tri-model review 2026-08-31, fix-program item 5):
# isotropic (Euclidean nearest-first) dilation of the engine-T0 interior,
# ported from the PASS-2 independent verification
# scratch_review/rv2sci_a2_null.py (`edt2` / `_dt1`) so this baseline uses
# the SAME exact distance transform that was checked there. That script
# pools a global radius across all pairs via interpolation over a fixed
# radius list; here each pair gets its OWN exact budget-matched dilation
# (the smallest isotropic region reaching the SAME cell count as the
# engine's p10 new-area), which is a more precise per-pair statement of the
# identical idea, not a different method.
# ---------------------------------------------------------------------------

def _dt1(fv: list) -> list:
    """1-D lower envelope of parabolas (Felzenszwalb & Huttenlocher 2004).
    Internal helper for squared_edt; ported line-for-line from
    scratch_review/rv2sci_a2_null.py's `_dt1` (verified there against a
    from-scratch PASS-2 reimplementation)."""
    n = len(fv)
    d = [0.0] * n
    v = [0] * n
    z = [0.0] * (n + 1)
    k = 0
    v[0] = 0
    z[0] = -math.inf
    z[1] = math.inf
    for q in range(1, n):
        if fv[q] == math.inf:
            continue
        while True:
            p = v[k]
            if fv[p] == math.inf:
                if k > 0:
                    k -= 1
                    continue
                else:
                    break
            s = ((fv[q] + q * q) - (fv[p] + p * p)) / (2.0 * q - 2.0 * p)
            if s <= z[k] and k > 0:
                k -= 1
            else:
                break
        p = v[k]
        s = (-math.inf if fv[p] == math.inf
             else ((fv[q] + q * q) - (fv[p] + p * p)) / (2.0 * q - 2.0 * p))
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = math.inf
    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        p = v[k]
        d[q] = math.inf if fv[p] == math.inf else (q - p) * (q - p) + fv[p]
    return d


def squared_edt(seed_mask: set, nrows: int, ncols: int) -> list:
    """Exact squared Euclidean distance transform, in CELL units: for every
    cell of an nrows x ncols row-major grid, the squared distance to the
    nearest cell in `seed_mask`. Two 1-D passes (columns then rows) of the
    Felzenszwalb-Huttenlocher lower envelope — O(nrows*ncols), exact (no
    approximation, unlike a BFS/chamfer distance). `seed_mask` must be
    non-empty. Ported from scratch_review/rv2sci_a2_null.py's `edt2`."""
    n = nrows * ncols
    binary = [(i in seed_mask) for i in range(n)]
    f = [0.0 if binary[i] else math.inf for i in range(n)]
    for c in range(ncols):
        col = _dt1([f[r * ncols + c] for r in range(nrows)])
        for r in range(nrows):
            f[r * ncols + c] = col[r]
    for r in range(nrows):
        row = _dt1(f[r * ncols:(r + 1) * ncols])
        f[r * ncols:(r + 1) * ncols] = row
    return f


def null_model_covered(seed_mask: set, burnable: list, target_cells,
                       nrows: int, ncols: int, budget_n: int) -> int:
    """How many cells of `target_cells` fall inside the smallest isotropic
    (Euclidean nearest-first) dilation of `seed_mask` that reaches
    `budget_n` BURNABLE non-seed cells — the physics-free "just grow a
    circle to the engine's own area budget" null baseline. Deterministic
    tie-break for cells equidistant from the seed: (distance, cell index)
    ascending, so re-runs are byte-identical. Non-burnable cells and seed
    cells themselves can never be selected into the dilated region (mirrors
    the engine, which can never report an arrival for a non-burnable cell).
    Returns 0 if `seed_mask` is empty or `budget_n` <= 0 (no dilation)."""
    if not seed_mask or budget_n <= 0:
        return 0
    d2 = squared_edt(seed_mask, nrows, ncols)
    candidates = sorted(
        (i for i in range(nrows * ncols) if i not in seed_mask and burnable[i]),
        key=lambda i: (d2[i], i))
    dilated = set(candidates[:budget_n])
    return sum(1 for i in target_cells if i in dilated)


# ---------------------------------------------------------------------------
# ArcGIS daily-perimeter query builders + parsers (pure)
# ---------------------------------------------------------------------------

def candidates_query_url(min_acres: float = 5000.0, state: str = "US-CA",
                         since_iso: str = "2025-01-01 00:00:00",
                         limit: int = 25) -> str:
    """Discovery query: incidents grouped by IRWIN id with snapshot counts,
    max acres, and first/last capture time — large `state` fires since
    `since_iso`, most snapshots first."""
    stats = [
        {"statisticType": "count", "onStatisticField": "OBJECTID",
         "outStatisticFieldName": "n_rows"},
        {"statisticType": "max", "onStatisticField": "poly_GISAcres",
         "outStatisticFieldName": "max_acres"},
        {"statisticType": "min", "onStatisticField": "poly_PolygonDateTime",
         "outStatisticFieldName": "t_min"},
        {"statisticType": "max", "onStatisticField": "poly_PolygonDateTime",
         "outStatisticFieldName": "t_max"},
    ]
    q = {
        "where": (f"poly_GISAcres >= {min_acres:g} AND "
                  f"attr_POOState = '{state}' AND "
                  f"poly_PolygonDateTime >= TIMESTAMP '{since_iso}'"),
        "outStatistics": json.dumps(stats),
        "groupByFieldsForStatistics": "attr_IrwinID,attr_IncidentName",
        "orderByFields": "n_rows DESC",
        "f": "json",
        "resultRecordCount": str(limit),
    }
    return DAILY_PERIMETERS_URL + "?" + urllib.parse.urlencode(q)


def parse_candidates(payload: dict) -> list:
    """[(irwin, name, n_rows, max_acres, t_min_s, t_max_s)] sorted by n_rows
    desc; rows with a null IRWIN id are dropped (unjoinable bulk uploads)."""
    out = []
    for f in payload.get("features") or []:
        a = f.get("attributes") or {}
        irwin = a.get("attr_IrwinID")
        if not irwin:
            continue
        try:
            out.append((str(irwin), str(a.get("attr_IncidentName") or ""),
                        int(a.get("n_rows") or 0),
                        float(a.get("max_acres") or 0.0),
                        epoch_s(a.get("t_min")), epoch_s(a.get("t_max"))))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda t: -t[2])
    return out


def series_attrs_url(irwin: str) -> str:
    """Attribute-only time series for one incident (no geometry — cheap)."""
    q = {
        "where": f"attr_IrwinID = '{irwin}'",
        "outFields": ("OBJECTID,poly_PolygonDateTime,poly_DateCurrent,"
                      "poly_GISAcres,attr_IncidentName"),
        "returnGeometry": "false",
        "orderByFields": "poly_PolygonDateTime ASC",
        "f": "json",
    }
    return DAILY_PERIMETERS_URL + "?" + urllib.parse.urlencode(q)


def parse_series_attrs(payload: dict) -> list:
    """[(oid, t_s, acres)] ascending by time. Capture time is
    poly_PolygonDateTime, falling back to poly_DateCurrent; rows with neither
    (or no OBJECTID) are dropped."""
    rows = []
    for f in payload.get("features") or []:
        a = f.get("attributes") or {}
        oid = a.get("OBJECTID")
        t_raw = a.get("poly_PolygonDateTime") or a.get("poly_DateCurrent")
        if oid is None or t_raw is None:
            continue
        try:
            rows.append((int(oid), epoch_s(t_raw),
                         float(a.get("poly_GISAcres") or 0.0)))
        except (TypeError, ValueError):
            continue
    rows.sort(key=lambda r: (r[1], r[0]))
    return rows


# SNAPSHOT RULE (fix of 2026-09-27; record and measurements in
# docs/calibration/stacked_rings_fix_2026-09-27.md)
# ------------------------------------------------------------------------
# WFIGS_Daily_Perimeters_Public appends a NEW row every time a source
# perimeter record changes, so the rows that share one poly_PolygonDateTime
# are versions of ONE polygon, not pieces of one: re-copies of an unchanged
# record, in-place edits that were never re-stamped, and re-uploads under a
# new record id. Measured on the cached corpora: Crosswhite's 2026-07-17 06:43
# record grew 13,883 -> 134,128 ac over 19 versions in six days, Tartar's
# 2026-07-25 15:33 record 1,937 -> 97,737 ac over nine; Crosswhite
# 2026-07-24 09:04 is two records of 175,079 ac each. The pre-fix law unioned
# every such row into one snapshot, stacking near-duplicate rings that the
# even-odd rasterizers (engine seed and scoring mask alike) cancel: Madre's
# 2025-07-02 capture of 8,396 ac rasterized to 0 cells. The rule now:
#   1. A capture time is represented by its FIRST-PUBLISHED row (smallest
#      OBJECTID; OBJECTID order is the layer's append order). That version is
#      the one nearest its own stamp (its poly_DateCurrent trails the stamp
#      by minutes to hours; later edits trail it by hours to weeks), the one
#      a live system could have seen at the time, and it never changes as the
#      layer accumulates edits, so a re-fetch reproduces the corpus. The
#      latest revision (largest OBJECTID / latest poly_DateCurrent) was
#      rejected: it dates days of later growth to the original stamp.
#   2. A later row under the same capture time is kept only as a genuinely
#      separate piece: its interior and the rows already kept overlap by at
#      most SAME_TIME_OVERLAP_TOL of either one's area (same_time_overlap_
#      frac). Overlapping rows are versions and are dropped (`dropped_oids`).
#      A kept row's own rings (multipolygon parts and holes) stay whole.
#   3. Growth gating (pick_snapshot_times) uses the first-published row's
#      acreage, i.e. the polygon rule 1 keeps, never a later revision's.
# Rows stamped with a time older than an already-published capture (out-of-
# order publication) are NOT dropped here: on these corpora most are genuine
# captures published hours late (Gifford 2025-08-01..03); the growth gate and
# the replay's dt window exclude the bogus-year ones.

SAME_TIME_OVERLAP_TOL = 0.05     # overlap above this => versions, not pieces
_OVERLAP_GRID_CELLS = 64         # raster resolution of the overlap test


def pick_snapshot_times(rows: list, min_dt_h: float = 6.0,
                        max_dt_h: float = 90.0,
                        min_growth_frac: float = 0.08,
                        min_growth_ac: float = 300.0,
                        max_snapshots: int = 5) -> list:
    """Greedy snapshot selection from an attrs time series.

    `rows` = [(oid, t_s, acres)]. Rows sharing one capture time are versions
    of one polygon (SNAPSHOT RULE above), so each capture time is gated on
    its FIRST-PUBLISHED row (smallest OBJECTID). Starting from the earliest
    capture, the next snapshot is the first capture >= min_dt_h later whose
    acreage grew by >= max(min_growth_frac * prev, min_growth_ac) — pairs
    closer than min_dt_h can't be scored against 90 m cells, and no-growth
    pairs score nothing. Gaps longer than max_dt_h exceed the shipped forecast
    horizon (cfg.horizon_h = 96 h) and BREAK the chain: the long-gap capture
    starts a fresh T0 (the pair across the gap is never emitted).

    Returns [(t_s, [oids], acres)] — at most max_snapshots entries. `oids`
    lists every row published under that capture time in OBJECTID order
    (first publication first; assemble_snapshot needs the later rows to
    recognise a separate piece); `acres` is the first publication's.
    """
    groups: dict = {}
    for oid, t_s, acres in rows:
        groups.setdefault(t_s, []).append((oid, acres))
    times = sorted(groups)
    if not times:
        return []

    def _capture(t):
        members = sorted(groups[t])
        return (t, [oid for oid, _ac in members], members[0][1])

    picked = [_capture(times[0])]
    for t in times[1:]:
        if len(picked) >= max_snapshots:
            break
        prev_t, _oids, prev_ac = picked[-1]
        dt_h = (t - prev_t) / 3600.0
        if dt_h < min_dt_h:
            continue
        cap = _capture(t)
        if dt_h > max_dt_h:
            picked.append(cap)                           # fresh T0
            continue
        if cap[2] - prev_ac >= max(min_growth_frac * prev_ac, min_growth_ac):
            picked.append(cap)
    return picked


def _grid_over(rings: list, grid_cells: int) -> GridSpec:
    """A square-cell grid over the rings' bbox, grid_cells across its longer
    side (so even a small polygon is resolved). One spare column: the
    rasterizer's column step follows each row's own latitude, so the
    southern rows reach slightly less far east than the mid-latitude width."""
    lats = [p.lat for ring in rings for p in ring]
    lons = [p.lon for ring in rings for p in ring]
    mid_lat = 0.5 * (min(lats) + max(lats))
    h_m = (max(lats) - min(lats)) * geo.M_PER_DEG_LAT
    w_m = (max(lons) - min(lons)) * geo.m_per_deg_lon(mid_lat)
    cell_m = max(h_m, w_m, 1.0) / float(grid_cells)
    return GridSpec(north_lat=max(lats), west_lon=min(lons), cell_m=cell_m,
                    nrows=max(1, math.ceil(h_m / cell_m)),
                    ncols=max(1, math.ceil(w_m / cell_m)) + 1)


def _rings_near(rings: list, spec: GridSpec) -> list:
    """The rings whose bbox meets the grid's extent. A ring that does not
    cannot contain any cell center of the grid, so dropping it leaves every
    even-odd parity on the grid unchanged."""
    south = spec.north_lat - spec.nrows * spec.cell_m / geo.M_PER_DEG_LAT
    east = spec.west_lon + spec.ncols * spec.cell_m / geo.m_per_deg_lon(
        spec.north_lat if abs(spec.north_lat) > abs(south) else south)
    out = []
    for ring in rings:
        la = [p.lat for p in ring]
        lo = [p.lon for p in ring]
        if (max(la) >= south and min(la) <= spec.north_lat
                and max(lo) >= spec.west_lon and min(lo) <= east):
            out.append(ring)
    return out


def same_time_overlap_frac(rings_a: list, rings_b: list,
                           grid_cells: int = _OVERLAP_GRID_CELLS,
                           stop_above: float = None) -> float:
    """How far two same-time polygons overlap: max over the two of (share of
    that polygon's even-odd interior lying inside the other), each share
    measured on a grid_cells-wide raster over that polygon's own bbox.
    1.0 for copies, a revision, or one polygon inside the other; ~0 for
    separate pieces (touching pieces share a boundary sliver only). A
    polygon too thin to cover any cell center of its own grid counts as
    fully inside the other, so a degenerate row is never kept as a 'piece'.
    With `stop_above`, returns as soon as rings_a's share exceeds it (the
    caller only needs the decision; rings_a is measured first)."""
    if not rings_a or not rings_b:
        return 0.0
    a_lat = [p.lat for r in rings_a for p in r]
    a_lon = [p.lon for r in rings_a for p in r]
    b_lat = [p.lat for r in rings_b for p in r]
    b_lon = [p.lon for r in rings_b for p in r]
    if (max(a_lat) < min(b_lat) or max(b_lat) < min(a_lat)
            or max(a_lon) < min(b_lon) or max(b_lon) < min(a_lon)):
        return 0.0
    best = 0.0
    for own, other in ((rings_a, rings_b), (rings_b, rings_a)):
        spec = _grid_over(own, grid_cells)
        own_mask = polygon_interior_mask(spec, own)
        if not own_mask:
            return 1.0
        inside = polygon_interior_mask(spec, _rings_near(other, spec))
        best = max(best, len(own_mask & inside) / len(own_mask))
        if stop_above is not None and best > stop_above:
            return best
    return best


def assemble_snapshot(oids: list, geoms: dict, acres_by_oid: dict = None,
                      overlap_tol: float = SAME_TIME_OVERLAP_TOL) -> tuple:
    """SNAPSHOT RULE 1-2 for the rows published under ONE capture time.

    `oids`: those rows (any order; OBJECTID order = publication order);
    `geoms`: parse_snapshot_features output {oid: (t_s, rings)};
    `acres_by_oid`: {oid: poly_GISAcres} from the attrs series.

    Returns (rings, kept_oids, dropped_oids, acres): the first-published row
    that has geometry, plus each later row whose overlap with the rows kept
    so far is <= overlap_tol (a separate piece). Every other row is a version
    of a kept polygon, or had no geometry, and is listed in dropped_oids.
    `acres` sums the kept rows' acreage (a missing entry counts 0).
    """
    acres_by_oid = acres_by_oid or {}
    rings, kept, dropped = [], [], []
    for oid in sorted(oids):
        got = geoms.get(oid)
        if not got or not got[1]:
            dropped.append(oid)
            continue
        own = list(got[1])
        if kept and same_time_overlap_frac(
                own, rings, stop_above=overlap_tol) > overlap_tol:
            dropped.append(oid)
            continue
        kept.append(oid)
        rings.extend(own)
    acres = sum(float(acres_by_oid.get(oid) or 0.0) for oid in kept)
    return rings, kept, dropped, acres


def geometry_query_url(oids: list, offset_deg: float = 0.0003) -> str:
    """Geometry pull for specific OBJECTIDs, generalized to ~offset_deg
    (0.0003 deg ~ 30 m — below the working cell size) to keep payloads small."""
    q = {
        "objectIds": ",".join(str(o) for o in oids),
        "outFields": "OBJECTID,poly_PolygonDateTime,poly_GISAcres",
        "returnGeometry": "true",
        "outSR": "4326",
        "geometryPrecision": "5",
        "maxAllowableOffset": f"{offset_deg:g}",
        "f": "json",
    }
    return DAILY_PERIMETERS_URL + "?" + urllib.parse.urlencode(q)


def parse_snapshot_features(payload: dict) -> dict:
    """{oid: (t_s, rings)} from a geometry query. Esri rings are [lon, lat]
    vertex lists; dirty vertices are skipped defensively (feeds.py law);
    rings are stored subsampled to STORE_RING_VERTS."""
    out: dict = {}
    for f in payload.get("features") or []:
        a = f.get("attributes") or {}
        oid = a.get("OBJECTID")
        t_raw = a.get("poly_PolygonDateTime")
        if oid is None:
            continue
        rings = []
        for raw in (f.get("geometry") or {}).get("rings") or []:
            ring = []
            for v in raw:
                if not (isinstance(v, (list, tuple)) and len(v) >= 2):
                    continue
                try:
                    lon, lat = float(v[0]), float(v[1])
                except (TypeError, ValueError):
                    continue
                ring.append(LatLon(lat, lon))
            if len(ring) >= 3:
                rings.append(subsample_ring(ring, STORE_RING_VERTS))
        if rings:
            out[int(oid)] = (epoch_s(t_raw) if t_raw is not None else None,
                             rings)
    return out


# ---------------------------------------------------------------------------
# Open-Meteo archive (observed weather) — builder + parser (pure)
# ---------------------------------------------------------------------------

def archive_weather_url(loc: LatLon, start_date: str, end_date: str) -> str:
    """Hourly reanalysis request, UTC, wind already in m/s (engine units)."""
    q = {
        "latitude": f"{loc.lat:.4f}",
        "longitude": f"{loc.lon:.4f}",
        "start_date": start_date,
        "end_date": end_date,
        "hourly": _ARCHIVE_HOURLY,
        "wind_speed_unit": "ms",
        "timezone": "UTC",
    }
    return ARCHIVE_WEATHER_URL + "?" + urllib.parse.urlencode(q)


def parse_archive_weather(payload: dict, loc: LatLon) -> WeatherForecast:
    """Open-Meteo archive JSON -> WeatherForecast (hourly WeatherSamples,
    unix s ascending). Same conventions as weather.OpenMeteoWeather: rows with
    a null core variable are skipped; gust = max(gust, wind); raises
    RuntimeError when the payload yields no samples."""
    try:
        hourly = payload["hourly"]
        times = hourly["time"]
        temp = hourly["temperature_2m"]
        rh = hourly["relative_humidity_2m"]
        wind = hourly["wind_speed_10m"]
        wdir = hourly["wind_direction_10m"]
        gust = hourly["wind_gusts_10m"]
    except (KeyError, TypeError) as e:
        raise RuntimeError("archive response missing hourly arrays") from e
    n = min(len(times), len(temp), len(rh), len(wind), len(wdir), len(gust))
    samples = []
    for i in range(n):
        if None in (wind[i], temp[i], rh[i], wdir[i]):
            continue
        w = float(wind[i])
        g = float(gust[i]) if gust[i] is not None else w
        samples.append(WeatherSample(
            t_s=parse_iso8601_s(times[i]), wind_speed_mps=w,
            wind_gust_mps=max(g, w), wind_dir_deg=float(wdir[i]) % 360.0,
            rh_pct=float(rh[i]), temp_c=float(temp[i])))
    if not samples:
        raise RuntimeError("archive decode produced no samples")
    return WeatherForecast(loc=loc, samples=samples, source="open-meteo-archive")


# ---------------------------------------------------------------------------
# Replay + scoring
# ---------------------------------------------------------------------------

def replay_pair(tile: TerrainTile, wx: WeatherForecast,
                snap0: PerimeterSnapshot, snap1: PerimeterSnapshot,
                cfg: ResponseConfig, incident_id: str,
                name: str = "", label: str = "default") -> PairMetrics:
    """Seed the engine with snap0's perimeter at snap0.t_s (the production A5
    interior-seeding path, rings subsampled per the threat law, no exterior
    seeds) and score the ensemble against snap1. Deterministic: the ensemble
    seeds on (incident_id, member_k) only, per contracts."""
    seed_rings = [subsample_ring(r, MAX_RING_VERTS) for r in snap0.rings]
    regime = False
    cfg0 = cfg
    if cfg.extreme_regime_enabled:
        # EXTREME SPREAD REGIME posture on the spread physics — the SAME
        # rule the live watcher applies (threat.regime_spread_cfg), so the
        # calibration replays and the daemon see one world.
        from wgresponse.threat import regime_spread_cfg
        cfg, regime = regime_spread_cfg(tile, wx, snap0.t_s, cfg)
    ens = spread.ensemble_arrival(tile, [], wx, snap0.t_s, cfg,
                                  incident_id=incident_id,
                                  perimeter=seed_rings,
                                  perimeter_at_s=snap0.t_s)
    m = score_pair(tile, ens, snap0, snap1, incident_id, name, label)
    if regime:
        applied = float(getattr(cfg0, "extreme_regime_floor_frac", 0.0) or 0.0) > 0.0
        m.notes.append(
            "extreme spread regime detected"
            + (f": blow-up floor {cfg.grass_ros_floor_frac:g} applied on all "
               f"classes" if applied else " (physics unchanged: no regime floor)"))
    return m


def score_pair(tile: TerrainTile, ens, snap0: PerimeterSnapshot,
               snap1: PerimeterSnapshot, incident_id: str, name: str = "",
               label: str = "default") -> PairMetrics:
    """Pure scoring of an EnsembleArrival against the observed T1 perimeter.

    Engine-T0 mask = cells whose NOMINAL arrival == snap0.t_s exactly — by
    construction those are precisely the cells compute_arrival pre-burned
    (interior cells are FINAL at perimeter_at_s; every exterior cell is
    strictly later), so subtracting it can never misclassify a spread cell.
    Newly-burned = (interior of T1 by the even-odd mask) minus engine-T0.
    """
    spec = tile.spec
    t0_s, t1_s = snap0.t_s, snap1.t_s
    m = PairMetrics(incident=incident_id, name=name, t0_s=t0_s, t1_s=t1_s,
                    dt_h=(t1_s - t0_s) / 3600.0, cell_m=spec.cell_m,
                    label=label)
    nominal = ens.members[0].data
    p10, p50, p90 = ens.p10.data, ens.p50.data, ens.p90.data
    member_cols = [mem.data for mem in ens.members]
    n_members = len(member_cols)

    t1_mask = polygon_interior_mask(spec, snap1.rings)
    engine_t0 = {i for i in range(spec.nrows * spec.ncols)
                 if nominal[i] == t0_s}
    if not engine_t0:
        m.notes.append("engine seeded no T0 interior cells (degenerate ring?)")
    # Monotonicity guard: T1 must (mostly) contain T0 — a partial-lobe upload
    # would otherwise fabricate "unburned" former-interior cells.
    if engine_t0:
        contained = len(engine_t0 & t1_mask) / len(engine_t0)
        if contained < 0.85:
            m.notes.append(
                f"NON-MONOTONE pair: only {contained:.0%} of the T0 interior "
                f"is inside T1 (partial mapping upload?) — pair UNSCORED")
            return m

    new_cells = sorted(t1_mask - engine_t0)
    m.n_new = len(new_cells)
    cell_ac = spec.cell_m * spec.cell_m / 4046.8564224
    m.obs_new_area_ac = m.n_new * cell_ac

    fdata = tile.fuel.data
    mid = 0.5 * (t0_s + t1_s)
    pit_bins = [0] * (n_members + 1)
    by_class: dict = {}
    new_burnable_idx: list = []     # feeds the null-model dilation below
    for i in new_cells:
        code = int(round(fdata[i]))
        cls = fuel_class(code)
        rec = by_class.setdefault(cls, {"n": 0, "late": 0, "cert": 0, "poss": 0})
        rec["n"] += 1
        if cls == "NB":
            m.n_new_nonburnable += 1
            continue
        m.n_new_burnable += 1
        new_burnable_idx.append(i)
        lo, hi = p10[i], p90[i]
        if hi == UNREACHED and lo != UNREACHED:
            m.p90_unreached += 1
        if lo == UNREACHED:
            m.never_reached += 1
            m.late_miss += 1
            rec["late"] += 1
        elif lo > t1_s:
            m.late_miss += 1
            rec["late"] += 1
        elif hi < t0_s:
            m.early_miss += 1
        if lo <= t0_s and hi >= t1_s:
            m.covered_certain += 1
            rec["cert"] += 1
        if lo <= t1_s and hi >= t0_s:
            m.covered_possible += 1
            rec["poss"] += 1
        if lo <= t1_s:
            m.p10_not_after_window += 1
        if lo <= t0_s:
            m.p10_before_window += 1
        if t0_s < p50[i] <= t1_s:
            m.p50_in_window += 1
        rank = sum(1 for col in member_cols if col[i] <= mid)
        pit_bins[rank] += 1
    m.pit_hist = pit_bins
    m.by_fuel_class = by_class

    # Head-run lengths: observed = farthest newly-burned boundary cell from
    # the T0 boundary; modeled = same measure over the modeled burned region.
    t0_b = boundary_cells(engine_t0, spec)
    new_set = set(new_cells)
    obs_b = [i for i in boundary_cells(t1_mask, spec) if i in new_set]
    m.obs_head_m = max_dist_m(obs_b, t0_b, spec)

    def _model_head(vals) -> float:
        region = engine_t0 | {i for i in range(len(vals))
                              if vals[i] <= t1_s and vals[i] != UNREACHED}
        return max_dist_m(boundary_cells(region, spec), t0_b, spec)

    m.head_m_p50 = _model_head(p50)
    m.head_m_p10 = _model_head(p10)
    m.head_m_p90 = _model_head(p90)
    m.head_m_nominal = _model_head(nominal)
    n_model_new = sum(1 for i in range(len(p50))
                      if p50[i] <= t1_s and p50[i] != UNREACHED
                      and i not in engine_t0)
    m.modeled_new_area_ac_p50 = n_model_new * cell_ac
    n_model_new_p10 = sum(1 for i in range(len(p10))
                          if p10[i] <= t1_s and p10[i] != UNREACHED
                          and i not in engine_t0)
    m.modeled_new_area_ac_p10 = n_model_new_p10 * cell_ac

    # NULL MODEL (tri-model review item 5): isotropic dilation of engine_t0
    # to the SAME cell count as the engine's own p10 new-area budget — see
    # null_model_covered's docstring and PairMetrics.null_new_covered.
    if engine_t0 and n_model_new_p10 > 0 and new_burnable_idx:
        burnable_grid = [is_burnable(int(round(v))) for v in fdata]
        m.null_new_covered = null_model_covered(
            engine_t0, burnable_grid, new_burnable_idx,
            spec.nrows, spec.ncols, n_model_new_p10)
    return m


def pool_metrics(pairs: list) -> dict:
    """Cell-weighted pooled scorecard over scored pairs (n_new_burnable > 0).

    Two-sided, honest metrics (tri-model review 2026-08-31, fix-program item
    5 — full definitions in docs/calibration/SCORECARD.md):
      p10_adequacy_frac        P(p10 <= T1), cell-weighted — the honest
                               rename of the old `covered_possible_frac`
                               (identical value; see the PairMetrics
                               docstring for why they coincide exactly).
      late_miss_frac           the DANGEROUS polarity (p10 > T1).
      never_reached_frac       p10 = inf, the worst case of late_miss.
      median_head_ratio_*, median_area_ratio_*   TRUE medians via
                               statistics.median. The published record used
                               sorted[n//2], the UPPER-MIDDLE value on an
                               even n, not the median — always overstated,
                               always in the flattering direction.
      pit_summary               PIT histogram extreme-bin mass + a
                               chi-square-vs-uniform flatness statistic.
                               pit_hist was always computed by score_pair
                               but never surfaced in the report before this
                               fix.
      null_adequacy_frac, skill  the engine's p10-adequacy against a
                               physics-free isotropic-dilation baseline at
                               the SAME modeled area budget (see
                               null_model_covered). skill > 1 means the
                               physics beats an equal-area circle; skill is
                               None when the null model covered nothing.
      window_length_bins        dt_h <= 6 / 6-12 / 12-24 / > 24, each with
                               its own pairs/cells/p10_adequacy/late_miss.
                               The published August 93.8% headline is a
                               long-window average (dt>12h scored ~99.8%;
                               the only dt<=12h bin the corpus has scored
                               was ~69.8%) — score dt<=12h as the primary
                               bin, not the pooled-all-windows number.
      n_incidents, equal_incident_mean_p10_adequacy,
      loo_incident_p10_adequacy_min/max, largest_incident_share_of_*
                               incident concentration: cell count is NOT
                               inferential sample size — a pooled 18-pair
                               corpus can be 3-4 effective incidents, and a
                               single incident's pairs can carry most of the
                               late-miss mass.

    DEPRECATED keys retained ONLY for JSON compatibility (never present
    these as calibration evidence — see PairMetrics' docstring and
    docs/calibration/SCORECARD.md): covered_certain_frac,
    covered_possible_frac, early_miss_frac. `deprecated_metrics` carries a
    one-line reason for each, for readers who load this dict directly.

    Every key from the pre-fix schema is still present with the same
    meaning (old readers do not break); `median_head_ratio_p50`'s VALUE
    changed because it was computed with the sorted[n//2] bug — see above.
    """
    scored = [p for p in pairs if p.n_new_burnable > 0]
    tot = sum(p.n_new_burnable for p in scored)
    if tot == 0:
        return {"n_pairs": 0, "n_cells": 0}
    s = lambda attr: sum(getattr(p, attr) for p in scored)
    n_bins = max(len(p.pit_hist) for p in scored)
    pit = [0] * n_bins
    for p in scored:
        for k, v in enumerate(p.pit_hist):
            pit[k] += v
    by_fuel: dict = {}
    for p in scored:
        for cls, rec in p.by_fuel_class.items():
            agg = by_fuel.setdefault(cls, {"n": 0, "late": 0, "cert": 0,
                                           "poss": 0})
            for k in agg:
                agg[k] += rec.get(k, 0)

    # -- TRUE medians (statistics.median, not sorted[n//2]) --
    def _median(vals: list) -> float:
        return statistics.median(vals) if vals else 0.0

    head_p50 = [p.head_ratio_p50() for p in scored if p.obs_head_m > 0]
    head_p10 = [p.head_ratio_p10() for p in scored if p.obs_head_m > 0]
    head_nom = [p.head_ratio_nominal() for p in scored if p.obs_head_m > 0]
    area_p50 = [p.area_ratio_p50() for p in scored if p.obs_new_area_ac > 0]
    area_p10 = [p.area_ratio_p10() for p in scored if p.obs_new_area_ac > 0]

    # -- PIT summary: extreme-bin mass + a chi-square flatness statistic --
    pit_total = sum(pit)
    if n_bins and pit_total:
        lo_frac = pit[0] / pit_total
        hi_frac = pit[-1] / pit_total
        expected = pit_total / n_bins
        chi2 = (sum((v - expected) ** 2 / expected for v in pit)
                if expected > 0 else 0.0)
    else:
        lo_frac = hi_frac = chi2 = 0.0
    pit_summary = {
        "n_bins": n_bins,
        "n_total": pit_total,
        "lo_bin_frac": lo_frac,          # observed burn before EVERY member (engine late)
        "hi_bin_frac": hi_frac,          # observed burn after EVERY member (engine early)
        "extreme_bins_frac": lo_frac + hi_frac,
        "chi2_flatness": chi2,           # 0 = perfectly flat/uniform PIT; larger = worse
        "chi2_df": max(0, n_bins - 1),
    }

    # -- null-model skill at equal area --
    p10_adequacy_frac = s("p10_not_after_window") / tot
    null_adequacy_frac = s("null_new_covered") / tot
    skill = (p10_adequacy_frac / null_adequacy_frac
             if null_adequacy_frac > 0 else None)

    # -- per-window-length bins --
    def _bin_label(dt_h: float) -> str:
        if dt_h <= 6.0:
            return "<=6h"
        if dt_h <= 12.0:
            return "6-12h"
        if dt_h <= 24.0:
            return "12-24h"
        return ">24h"

    bin_order = ["<=6h", "6-12h", "12-24h", ">24h"]
    bins = {lbl: {"pairs": 0, "cells": 0, "p10_ok": 0, "late": 0}
            for lbl in bin_order}
    for p in scored:
        b = bins[_bin_label(p.dt_h)]
        b["pairs"] += 1
        b["cells"] += p.n_new_burnable
        b["p10_ok"] += p.p10_not_after_window
        b["late"] += p.late_miss
    window_length_bins = [
        {"label": lbl,
         "pairs": bins[lbl]["pairs"],
         "cells": bins[lbl]["cells"],
         "p10_adequacy": (bins[lbl]["p10_ok"] / bins[lbl]["cells"]
                          if bins[lbl]["cells"] else 0.0),
         "late_miss": (bins[lbl]["late"] / bins[lbl]["cells"]
                       if bins[lbl]["cells"] else 0.0)}
        for lbl in bin_order]

    # -- incident concentration (cell count is not inferential N) --
    by_incident: dict = {}
    for p in scored:
        agg = by_incident.setdefault(p.incident,
                                     {"cells": 0, "p10_ok": 0, "late": 0})
        agg["cells"] += p.n_new_burnable
        agg["p10_ok"] += p.p10_not_after_window
        agg["late"] += p.late_miss
    n_incidents = len(by_incident)
    equal_incident_mean = (
        statistics.mean(agg["p10_ok"] / agg["cells"]
                        for agg in by_incident.values() if agg["cells"] > 0)
        if by_incident else 0.0)
    loo_vals = []
    total_p10_ok = s("p10_not_after_window")
    for agg in by_incident.values():
        rest_cells = tot - agg["cells"]
        if rest_cells > 0:
            loo_vals.append((total_p10_ok - agg["p10_ok"]) / rest_cells)
    loo_min = min(loo_vals) if loo_vals else None
    loo_max = max(loo_vals) if loo_vals else None
    total_late = s("late_miss")
    largest_cells = max((agg["cells"] for agg in by_incident.values()),
                        default=0)
    largest_late = max((agg["late"] for agg in by_incident.values()),
                       default=0)

    return {
        # -- pre-fix schema, unchanged keys/meanings (median_head_ratio_p50's
        # VALUE is now a true median -- see the docstring above) --
        "n_pairs": len(scored),
        "n_cells": tot,
        "nonburnable_frac": s("n_new_nonburnable") /
                            max(1, s("n_new_nonburnable") + tot),
        "covered_certain_frac": s("covered_certain") / tot,
        "covered_possible_frac": s("covered_possible") / tot,
        "late_miss_frac": s("late_miss") / tot,
        "early_miss_frac": s("early_miss") / tot,
        "never_reached_frac": s("never_reached") / tot,
        "p90_unreached_frac": s("p90_unreached") / tot,
        "p50_in_window_frac": s("p50_in_window") / tot,
        "p10_not_after_window_frac": s("p10_not_after_window") / tot,
        "p10_before_window_frac": s("p10_before_window") / tot,
        "pit_hist": pit,
        "median_head_ratio_p50": _median(head_p50),
        "by_fuel_class": by_fuel,
        # -- new honest metrics (fix-program item 5) --
        "p10_adequacy_frac": p10_adequacy_frac,
        "median_head_ratio_p10": _median(head_p10),
        "median_head_ratio_nominal": _median(head_nom),
        "median_area_ratio_p50": _median(area_p50),
        "median_area_ratio_p10": _median(area_p10),
        "pit_summary": pit_summary,
        "null_adequacy_frac": null_adequacy_frac,
        "skill": skill,
        "window_length_bins": window_length_bins,
        "n_incidents": n_incidents,
        "equal_incident_mean_p10_adequacy": equal_incident_mean,
        "loo_incident_p10_adequacy_min": loo_min,
        "loo_incident_p10_adequacy_max": loo_max,
        "largest_incident_share_of_cells": (largest_cells / tot) if tot else 0.0,
        "largest_incident_share_of_late_miss": (largest_late / total_late
                                                if total_late else 0.0),
        # -- deprecated-metric notes (values above unchanged; read the notes) --
        "deprecated_metrics": {
            "covered_certain_frac": (
                "p10<=T0 is structurally ~0 in this replay design (no "
                "modeled arrival can precede the T0 seed) -- not "
                "calibration evidence, kept for JSON compatibility only."),
            "covered_possible_frac": (
                "identical value to p10_adequacy_frac (p90>=T0 always, "
                "since the engine seeds at T0) -- a ONE-SIDED statistic, "
                "not a two-sided [p10,p90] interval check. Use "
                "p10_adequacy_frac instead."),
            "early_miss_frac": (
                "p90<T0 is structurally impossible in this replay design "
                "(p90>=T0 always) -- always 0, proves nothing about "
                "ensemble width or centering."),
        },
    }


# ---------------------------------------------------------------------------
# Landmark anchors (offline-capable; F3)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AnchorScenario:
    """Parameterized landmark scenario. Weather values are the documented
    fire-day conditions used by the Council-1 review (exp3 'Santa Ana core'
    for Tubbs); tweak fields to re-anchor without touching code."""
    name: str
    wind10_mps: float
    rh_pct: float
    temp_c: float
    duration_h: float
    observed_head_kmh: float = 0.0      # Tubbs-style sustained-head anchor
    observed_area_ach: float = 0.0      # Camp-style area-growth anchor
    cell_m: float = 100.0
    width_km: float = 24.0
    height_km: float = 10.0


# Tubbs (1917-10-08/09): ~12 mi Calistoga->Santa Rosa in ~3 h => ~6.4 km/h
# sustained head run under Diablo winds. Review exp3 'Santa Ana core'
# conditions: wind10 12 m/s, RH 10%, 35 C -> GR2 point ROS 2.98 km/h (0.47x).
TUBBS = AnchorScenario(name="tubbs", wind10_mps=12.0, rh_pct=10.0,
                       temp_c=35.0, duration_h=3.0, observed_head_kmh=6.4)
# Camp (2018-11-08): ~6,600 ac/h average over the first 90 minutes (NIST
# timeline: Pulga ignition 06:30 to Paradise fully involved) under ~11 m/s
# sustained Jarbo Gap NE winds, RH ~23%, cold morning.
CAMP = AnchorScenario(name="camp", wind10_mps=11.0, rh_pct=23.0,
                      temp_c=12.0, duration_h=1.5, observed_area_ach=6600.0,
                      cell_m=60.0, width_km=14.0, height_km=8.0)

_ANCHOR_FUELS = (("GR2", 102.0), ("SH5", 145.0), ("GR2/SH5 mix", None))


def _anchor_tile(scn: AnchorScenario, fuel_code, anchor_lat: float = 39.0) -> TerrainTile:
    """Flat uniform (or 4-cell-checkerboard GR2/SH5 mix when fuel_code is
    None) tile centered near anchor_lat. Deterministic, no RNG needed."""
    ncols = max(8, int(round(scn.width_km * 1000.0 / scn.cell_m)))
    nrows = max(8, int(round(scn.height_km * 1000.0 / scn.cell_m)))
    spec = GridSpec(north_lat=anchor_lat + (nrows * scn.cell_m / 2.0)
                    / geo.M_PER_DEG_LAT,
                    west_lon=-122.0, cell_m=scn.cell_m,
                    nrows=nrows, ncols=ncols)
    n = nrows * ncols
    if fuel_code is not None:
        fuel = Raster(spec, [float(fuel_code)] * n)
    else:
        data = [0.0] * n
        for r in range(nrows):
            for c in range(ncols):
                data[r * ncols + c] = 102.0 if ((r // 4) + (c // 4)) % 2 == 0 \
                    else 145.0
        fuel = Raster(spec, data)
    zeros = lambda fill: Raster(spec, [fill] * n)
    return TerrainTile(spec=spec, fuel=fuel, slope_deg=zeros(0.0),
                       aspect_deg=zeros(-1.0), elev_m=zeros(100.0),
                       source=f"anchor:{scn.name}")


def _anchor_wx(scn: AnchorScenario, t0_s: float, loc: LatLon) -> WeatherForecast:
    """Constant observed-condition series, wind FROM the west (pushes the head
    due east across the tile). Direction is arbitrary on flat uniform fuel."""
    samples = [WeatherSample(t_s=t0_s + h * 3600.0,
                             wind_speed_mps=scn.wind10_mps,
                             wind_gust_mps=scn.wind10_mps * 1.4,
                             wind_dir_deg=270.0, rh_pct=scn.rh_pct,
                             temp_c=scn.temp_c)
               for h in range(int(scn.duration_h) + 3)]
    return WeatherForecast(loc=loc, samples=samples, source="anchor")


def point_ros_kmh(fuel_code: int, scn: AnchorScenario,
                  cfg: ResponseConfig) -> float:
    """Single-cell Rothermel head ROS (km/h) at the scenario conditions via
    the production chain (10-m wind -> WAF midflame -> surface_ros_mps) —
    the review's F3 measurement, reproduced."""
    fuel = FUEL_TABLE[int(fuel_code)]
    m_dead = dead_fuel_moisture_frac(scn.temp_c, scn.rh_pct)
    u_mid = rothermel.midflame_wind_mps(scn.wind10_mps, fuel)
    res = rothermel.surface_ros_mps(fuel, m_dead, cfg.live_herb_frac,
                                    cfg.live_woody_frac, u_mid, 0.0)
    return res.ros_mps * 3.6


def run_anchor(scn: AnchorScenario, cfg: ResponseConfig,
               spotting: bool) -> list:
    """Run one anchor scenario over the GR2 / SH5 / mix tiles; returns
    AnchorResults. compute_arrival nominal member only — the anchor measures
    the CORE spread model, not ensemble dispersion. Deterministic."""
    t0_s = 1_750_000_000.0
    out = []
    # Horizon needs only to cover the anchor duration: the metric reads the
    # arrival field at t0 + duration_h, which is identical under any horizon
    # >= duration (a longer horizon just burns the rest of the tile for
    # nothing). Everything else stays at the passed (shipped) config.
    run_cfg = replace(cfg, spotting_enabled=spotting,
                      horizon_h=scn.duration_h + 2.0)
    for fuel_label, code in _ANCHOR_FUELS:
        tile = _anchor_tile(scn, code)
        spec = tile.spec
        # Ignite mid-height on the west edge (head runs east with the wind).
        ign_r, ign_c = spec.nrows // 2, 1
        ign = [geo.rc_to_latlon(spec, ign_r, ign_c)]
        wx = _anchor_wx(scn, t0_s, ign[0])
        arr = spread.compute_arrival(tile, ign, wx, t0_s, run_cfg)
        t_end = t0_s + scn.duration_h * 3600.0
        burned = [i for i, v in enumerate(arr.data)
                  if v <= t_end and v != UNREACHED]
        if scn.observed_head_kmh > 0.0:
            # Max downwind (eastward) run from the ignition column.
            max_dc = max((i % spec.ncols) - ign_c for i in burned) if burned else 0
            head_kmh = (max_dc * spec.cell_m / 1000.0) / scn.duration_h
            observed, engine = scn.observed_head_kmh, head_kmh
            metric = f"head km/h over {scn.duration_h:g} h"
        else:
            cell_ac = spec.cell_m * spec.cell_m / 4046.8564224
            ach = len(burned) * cell_ac / scn.duration_h
            observed, engine = scn.observed_area_ach, ach
            metric = f"area ac/h over {scn.duration_h:g} h"
        notes = ""
        if burned and scn.observed_head_kmh > 0.0:
            max_dc = max((i % spec.ncols) - ign_c for i in burned)
            if max_dc >= spec.ncols - ign_c - 2:
                notes = "head reached tile edge — engine value is a floor"
        out.append(AnchorResult(
            anchor=scn.name, fuel_label=fuel_label, spotting=spotting,
            metric=metric, observed=observed, engine=engine,
            ratio=engine / observed if observed else 0.0,
            point_ros_kmh=(point_ros_kmh(int(code), scn, cfg)
                           if code is not None else 0.0),
            notes=notes))
    return out


def run_all_anchors(cfg: ResponseConfig) -> list:
    """Tubbs + Camp, spotting OFF (shipped default) and ON (F3 option c)."""
    out = []
    for scn in (TUBBS, CAMP):
        for spotting in (False, True):
            out.extend(run_anchor(scn, cfg, spotting))
    return out


# ---------------------------------------------------------------------------
# Recommendation + report rendering
# ---------------------------------------------------------------------------

# The F8 review target: >= 90% of observed arrivals at/after modeled p10.
P10_COVERAGE_TARGET = 0.90


def recommendations(pooled: dict, pooled_wide: dict, anchors: list) -> list:
    """Data-driven recommendation lines (strings) from the measured stats.
    Every line is conditional on the numbers actually observed — no
    unconditional tuning advice ever comes out of this function."""
    recs = []
    n = pooled.get("n_cells", 0)
    if n == 0:
        recs.append(
            "NO REPLAY EVIDENCE THIS RUN — no perturbation-fraction or "
            "grass-ROS change is justified by this document. Keep "
            "wind_perturb_frac=0.25, moisture_perturb_frac=0.20, "
            "dir_perturb_deg=30 as shipped; re-run the harness once live "
            "perimeter pairs are reachable.")
        return recs

    p10_nec = pooled["p10_not_after_window_frac"]
    late = pooled["late_miss_frac"]
    recs.append(
        f"P10 pessimism (F8 target >= {P10_COVERAGE_TARGET:.0%} of observed "
        f"arrivals at/after p10): best measurable bound {p10_nec:.0%} "
        f"(p10 <= T1; the sufficient bound p10 <= T0 is STRUCTURALLY ~0 in "
        f"this replay — the observation window opens at the seed time and "
        f"p10 can never precede the seed, so it carries no information) on "
        f"{n} cells / {pooled['n_pairs']} pairs.")
    wide_nec = pooled_wide.get("p10_not_after_window_frac")
    if p10_nec < P10_COVERAGE_TARGET:
        if wide_nec is not None and (wide_nec - p10_nec) < 0.05:
            recs.append(
                f"The necessary bound ({p10_nec:.0%}) misses the "
                f"{P10_COVERAGE_TARGET:.0%} bar, and the WIDENED variant "
                f"(wind_perturb 0.50 / dir 45 deg) moved it only to "
                f"{wide_nec:.0%} (+{100 * (wide_nec - p10_nec):.1f} pp): "
                f"widening the perturbation fractions ALONE does NOT close "
                f"the late tail. The deficit is in the ROS LEVEL (Council-1 "
                f"F3), not the band width — do NOT chase coverage by "
                f"inflating wind_perturb_frac / dir_perturb_deg; keep them "
                f"at shipped defaults pending the F3 level decision.")
        else:
            wide_txt = (f" Widened variant (wind 0.50 / dir 45 deg) measured "
                        f"{wide_nec:.0%} on the same pairs."
                        if wide_nec is not None else "")
            recs.append(
                f"The necessary bound ({p10_nec:.0%}) misses the "
                f"{P10_COVERAGE_TARGET:.0%} bar and widening helps "
                f"materially: INCREASE wind_perturb_frac and/or "
                f"dir_perturb_deg (crew-safety-conservative; an earlier p10 "
                f"is fail-safe polarity), then re-run this harness.{wide_txt}")
    else:
        recs.append(
            f"The necessary bound ({p10_nec:.0%}) clears the bar; the "
            f"censored windows cannot prove the sufficient side. Needed: "
            f"shorter-dt pairs (FIRIS intra-day perimeters) before changing "
            f"any perturbation knob.")
    if late > 0.10:
        recs.append(
            f"LATE-dominant misses: {late:.0%} of newly-burned cells score "
            f"late_miss (p10 > T1) — the dangerous, missed-alert polarity: "
            f"a late p10 delays crew deadlines and EXTREME triggers. "
            f"(early_miss is RETIRED as a comparison baseline: it is "
            f"structurally forced to ~0 by this replay's T0-seeded design "
            f"— no modeled arrival can precede its own seed — so '0% "
            f"early' is not evidence the ensemble is well-centered or too "
            f"wide; see the deprecated-metrics appendix.) Consistent with "
            f"the F3 slow finding.")
    pit = pooled.get("pit_hist") or []
    if len(pit) >= 3 and n:
        lo_mass = pit[0] / n
        hi_mass = pit[-1] / n
        if lo_mass > 0.30 and hi_mass > 0.30:
            recs.append(
                f"PIT mass piles at BOTH extremes (rank 0: {lo_mass:.0%}, "
                f"top rank: {hi_mass:.0%}): the ensemble is under-dispersed "
                f"in both directions — some sectors burn before every "
                f"member, others after every member. Suppression (unmodeled, "
                f"pushes 'engine early' on contained flanks) and the slow "
                f"core ROS (pushes 'engine late' on head runs) both "
                f"contribute; a pure band-width fix cannot repair a "
                f"level-and-shape error.")
    fuel = pooled.get("by_fuel_class") or {}
    gr = fuel.get("GR") or {}
    sh = fuel.get("SH") or {}
    if gr.get("n", 0) >= 500 and sh.get("n", 0) >= 500:
        gr_late = gr["late"] / gr["n"]
        sh_late = sh["late"] / sh["n"]
        if gr_late > sh_late + 0.10:
            t_off = {a.fuel_label: a.ratio for a in anchors
                     if a.anchor == "tubbs" and not a.spotting}
            anchor_txt = ""
            if "GR2" in t_off and "SH5" in t_off:
                anchor_txt = (f" (matches the Tubbs anchor: GR2 "
                              f"{t_off['GR2']:.2f}x vs SH5 "
                              f"{t_off['SH5']:.2f}x)")
            recs.append(
                f"Fuel-class split: GR (grass) late-miss {gr_late:.0%} vs "
                f"SH (shrub) {sh_late:.0%} on the SAME incidents/weather — "
                f"direct hindcast corroboration that the slow bias "
                f"concentrates in grass-class fuels{anchor_txt}. This is "
                f"where a per-fuel ADJ multiplier would act IF later "
                f"evidence justifies one.")

    tubbs_off = [a for a in anchors
                 if a.anchor == "tubbs" and not a.spotting]
    if tubbs_off:
        worst = min(tubbs_off, key=lambda a: a.ratio)
        best = max(tubbs_off, key=lambda a: a.ratio)
        recs.append(
            f"Tubbs anchor (spotting OFF): engine/observed head ratio "
            f"{worst.ratio:.2f}x ({worst.fuel_label}) to {best.ratio:.2f}x "
            f"({best.fuel_label}) vs the review's 0.47x point-ROS measurement "
            f"— the systematic-slow finding stands. A grass-ROS multiplier "
            f"(ELMFIRE-style ADJ) is DIRECTIONALLY supported by the anchors "
            f"but NOT yet by enough hindcast coverage evidence to pick a "
            f"number; do not ship one from this document alone.")
    return recs


DO_NOT_SHIP_NOTE = """\
## DO NOT SHIP A FUDGE

No ROS multiplier, perturbation widening, or quantile remap may ship from
this document alone. The firerisk honest-numbers discipline applies:

- The replay sample here is small (a handful of incidents, interval-censored
  observation windows, one fire season, one state) and the landmark anchors
  are two hand-parameterized scenarios. That is EVIDENCE OF DIRECTION, not a
  calibration fit.
- Any proposed `ResponseConfig` change must first be re-run through this
  harness on an EXPANDED incident set (>= 10 incidents, multiple fuel
  regimes, both wind-driven and terrain-driven fires) and show coverage
  moving toward the target WITHOUT wrecking the early-miss side (a p10 that
  is pessimistic-everywhere is a false-alarm machine that erodes crew trust
  and burns dispatch capacity).
- A grass-ROS ADJ multiplier additionally needs the spotting decision made
  first (F3: spotting is the mechanism that made Camp/Tubbs beat every
  no-spotting model) — tuning surface ROS to absorb missing spotting physics
  bakes ember transport into grass parameters where it does not belong.
- Per SAFETY SEMANTICS, any change lands as config + regression pins, with
  before/after coverage tables appended here, and byte-identity pins
  re-baselined explicitly — never silently.
"""


def _pct(x: float) -> str:
    return f"{100.0 * x:.1f}%"


def _pct_or_dash(x) -> str:
    return _pct(x) if x is not None else "—"


def render_report(incidents: list, pairs: list, pairs_wide: list,
                  anchors: list, fetch_summary: dict,
                  cfg: ResponseConfig, wx_note: str = "",
                  variant_label: str = "", variant_config: dict = None) -> str:
    """The docs/calibration/hindcast_first_pass.md content. Pure string
    building from measured inputs — regenerating with identical inputs yields
    identical bytes (no wall-clock). `wx_note` is an optional measured
    parenthetical about the replay weather (e.g. observed wind maxima),
    computed by the caller from the cached series — never hardcoded here.
    `variant_label`/`variant_config` are the optional calibration-matrix
    header (tools/hindcast_harness.py `replay --label` / the 7 config
    flags) — printed near the top when present; omitted (as before) when
    this is the shipped-default run."""
    L = []
    add = L.append
    add("# Hindcast first pass — engine vs observed perimeter growth")
    add("")
    add("Sprint-2 ticket D1 (Council-1 model F3+F8). Generated by "
        "`py -3 tools/hindcast_harness.py all`; inputs cached under "
        "`tools/hindcast_data/` so the replay re-runs offline and "
        "deterministically. **Recommendations only — this document changes "
        "no engine behavior.**")
    add("")
    add("Metric definitions, which polarity is dangerous, and why "
        "`covered_certain` / `covered_possible` / `early_miss` were "
        "retired: see `docs/calibration/SCORECARD.md`.")
    if variant_label or variant_config:
        cfg_bits = ", ".join(f"{k}={v}" for k, v in (variant_config or {}).items())
        add("")
        add(f"**Config variant:** label=`{variant_label or '(none)'}`"
            + (f" — {cfg_bits}" if cfg_bits else "") + ".")
    add("")
    add("## Method")
    add("")
    add("- **Perimeter time series:** NIFC ArcGIS `WFIGS_Daily_Perimeters_"
        "Public` FeatureServer layer 0 — researched live: unlike the "
        "`_Current` / `_YearToDate` / `InterAgencyFirePerimeterHistory` "
        "layers (one latest/final polygon per fire), the Daily layer keeps "
        "every published capture as its own row keyed by "
        "`poly_PolygonDateTime` (up to 110 rows per IRWIN id measured). "
        "Public domain (NIFC open data), keyless.")
    add("- **Observed weather:** Open-Meteo HISTORICAL archive "
        "(`archive-api.open-meteo.com`, ERA5-family reanalysis) hourly at "
        "the fire centroid, wind in m/s, UTC. **Licensing:** free tier is "
        "NON-COMMERCIAL and data is CC-BY-4.0 — this harness is an offline "
        "R&D calibration study; production use requires the paid tier or "
        "self-hosting (same posture as `wgresponse.weather`).")
    add("- **Terrain/fuel:** live LANDFIRE LFPS tiles (LF2024_FBFM40 + "
        "LF2020 slope/aspect/elev) through the engine's own "
        "`terrain.LfpsTerrain`, disk-cached. LF2024 fuels predate the "
        "replayed fires (pre-fire landscape — correct for hindcasting).")
    add("- **Replay:** for each consecutive snapshot pair (T0, T1): seed "
        "`spread.ensemble_arrival` with the T0 rings via the production "
        "perimeter-interior path (rings subsampled to <= 200 vertices, "
        "`perimeter_at_s = t0 = T0`, no exterior seeds), shipped default "
        f"`ResponseConfig` (ensemble_n={cfg.ensemble_n}, wind_perturb_frac="
        f"{cfg.wind_perturb_frac}, moisture_perturb_frac="
        f"{cfg.moisture_perturb_frac}, dir_perturb_deg={cfg.dir_perturb_deg}"
        f", spotting={'ON' if cfg.spotting_enabled else 'OFF'}), observed "
        "weather as the forecast.")
    add("- **Scoring under censoring (CORRECTED 2026-08-31 — tri-model "
        "review, fix-program item 5; full definitions in "
        "docs/calibration/SCORECARD.md):** a perimeter pair only brackets "
        "each newly-burned cell's true burn time inside (T0, T1]. "
        "`late_miss` = p10 > T1 (band entirely after the window — engine "
        "definitively LATE; the DANGEROUS, missed-alert polarity) is the "
        "honest statistic. `covered_certain`, `covered_possible`, and "
        "`early_miss` are DEPRECATED: because the engine seeds at T0, no "
        "modeled arrival can precede T0, so p90 >= T0 ALWAYS holds. That "
        "makes `early_miss` (p90 < T0) STRUCTURALLY IMPOSSIBLE (~0 in "
        "every pool ever scored — a tautology of the replay design, not "
        "evidence the ensemble is well-centered) and collapses "
        "`covered_possible` (band intersects [T0,T1]) EXACTLY onto "
        "`p10 <= T1` — a ONE-SIDED statistic reported honestly below as "
        "`p10_adequacy`. `covered_certain` needs `p10 <= T0`, which is "
        "likewise ~0 by the same construction. All three still appear, "
        "clearly labelled, in a deprecated appendix table for schema "
        "continuity — never as calibration evidence. The informative "
        "statistics are p10_adequacy, late_miss, head-ratio and area-ratio "
        "at p10/p50/p90/nominal, the PIT histogram (approximated at the "
        "window midpoint, summarized by extreme-bin mass + a flatness "
        "statistic), the null-model skill, window-length bins, and "
        "incident concentration. Cells whose LANDFIRE code is non-burnable "
        "(urban etc.) can never receive a modeled arrival — they are "
        "counted and reported, never silently dropped.")
    add("- **Null-model baseline:** an isotropic (Euclidean nearest-first) "
        "dilation of the engine-T0 interior, grown to the SAME cell count "
        "as the engine's own p10 new-area budget (`squared_edt` / "
        "`null_model_covered` in tools/hindcast_lib.py, ported from the "
        "tri-model review's independent verification script "
        "`scratch_review/rv2sci_a2_null.py`) — a physics-free 'just grow a "
        "circle' control. `skill` = p10_adequacy / null_adequacy at that "
        "equal area; skill > 1x means the physics beats the circle.")
    add("- **Head run:** farthest newly-burned boundary cell from the T0 "
        "boundary (straight-line, metric grid), observed vs the modeled "
        "p50/p10 burned region at T1. Boundary sets are strided to caps, so "
        "both numbers are conservative floors (same bias both sides of the "
        "ratio).")
    add("")
    add("## Incidents used")
    add("")
    if incidents:
        add("| incident | irwin | snapshots used | max acres | span |")
        add("|---|---|---|---|---|")
        for inc in incidents:
            if not inc.snapshots:
                continue
            t_a = datetime.fromtimestamp(inc.snapshots[0].t_s, tz=timezone.utc)
            t_b = datetime.fromtimestamp(inc.snapshots[-1].t_s, tz=timezone.utc)
            add(f"| {inc.name} | `{inc.irwin}` | {len(inc.snapshots)} | "
                f"{inc.max_acres:,.0f} | {t_a:%Y-%m-%d} .. {t_b:%Y-%m-%d} |")
    else:
        add("*(none — live pulls unavailable this run; see Reachability)*")
    add("")
    add("## Per-pair scorecard (shipped default config)")
    add("")
    scored = [p for p in pairs if p.n_new_burnable > 0]
    if scored:
        header = ["incident", "T0 (UTC)", "dt h", "cell m",
                  "new cells (burnable/NB)", "p10 adequacy (p10<=T1)",
                  "late miss", "never", "head ratio p50/p10/nom",
                  "area ratio p50/p10", "null adeq", "skill"]
        add("| " + " | ".join(header) + " |")
        add("|" + "---|" * len(header))
        for p in pairs:
            t0 = datetime.fromtimestamp(p.t0_s, tz=timezone.utc)
            if p.n_new_burnable == 0:
                note = p.notes[0] if p.notes else "no scorable growth"
                cells = [p.name, f"{t0:%m-%d %H:%M}", f"{p.dt_h:.1f}",
                         f"{p.cell_m:.0f}",
                         f"{p.n_new} ({p.n_new_burnable}/"
                         f"{p.n_new_nonburnable})", f"— {note}",
                         "", "", "", "", "", ""]
                add("| " + " | ".join(cells) + " |")
                continue
            sk = p.skill()
            cells = [
                p.name, f"{t0:%m-%d %H:%M}", f"{p.dt_h:.1f}",
                f"{p.cell_m:.0f}",
                f"{p.n_new} ({p.n_new_burnable}/{p.n_new_nonburnable})",
                _pct(p.p10_adequacy()), _pct(p.frac(p.late_miss)),
                _pct(p.frac(p.never_reached)),
                f"{p.head_ratio_p50():.2f}/{p.head_ratio_p10():.2f}/"
                f"{p.head_ratio_nominal():.2f}",
                f"{p.area_ratio_p50():.2f}/{p.area_ratio_p10():.2f}",
                _pct(p.null_adequacy()),
                (f"{sk:.2f}x" if sk is not None else "—"),
            ]
            add("| " + " | ".join(cells) + " |")
    else:
        add("*(no scorable pairs this run — see Reachability)*")
    add("")
    add("### Deprecated one-sided metrics (appendix — not calibration evidence)")
    add("")
    add("Kept for JSON/schema compatibility with old readers only. "
        "`covered_certain` / `covered_possible` / `early_miss` are all "
        "~0-or-collapsed BY CONSTRUCTION in this replay design (the engine "
        "seeds at T0, so no modeled arrival can precede T0) — see the "
        "PairMetrics docstring and `docs/calibration/SCORECARD.md`. Do NOT "
        "read this table as a two-sided [p10,p90] interval calibration "
        "check.")
    add("")
    if scored:
        add("| incident | dt h | coverage [certain..possible] (deprecated) | "
            "p10<=T0 'sufficient' (deprecated, ~0 by construction) | "
            "early miss (deprecated, ~0 by construction) |")
        add("|---|---|---|---|---|")
        for p in pairs:
            if p.n_new_burnable == 0:
                continue
            add(f"| {p.name} | {p.dt_h:.1f} | "
                f"[{_pct(p.frac(p.covered_certain))} .. "
                f"{_pct(p.frac(p.covered_possible))}] | "
                f"{_pct(p.frac(p.p10_before_window))} | "
                f"{_pct(p.frac(p.early_miss))} |")
    else:
        add("*(no scorable pairs this run)*")
    add("")
    pooled = pool_metrics(pairs)
    pooled_wide = pool_metrics(pairs_wide)
    if pooled.get("n_cells"):
        add("### Pooled (cell-weighted)")
        add("")
        add(f"- pairs scored: **{pooled['n_pairs']}** across "
            f"**{pooled['n_incidents']}** distinct incidents, newly-burned "
            f"burnable cells: **{pooled['n_cells']:,}** (plus "
            f"{_pct(pooled['nonburnable_frac'])} of new cells non-burnable "
            f"in LANDFIRE — structure-to-structure spread the surface model "
            f"cannot represent). Cell count is NOT inferential sample size "
            f"— see Incident concentration below.")
        add(f"- **p10 adequacy** (P(p10 <= T1); the honest one-sided stat "
            f"the deprecated `covered_possible` silently was): "
            f"**{_pct(pooled['p10_adequacy_frac'])}** (F8 target >= "
            f"{P10_COVERAGE_TARGET:.0%}). Equal-incident mean: "
            f"{_pct(pooled['equal_incident_mean_p10_adequacy'])}. "
            f"Leave-one-incident-out range: "
            f"[{_pct_or_dash(pooled['loo_incident_p10_adequacy_min'])} .. "
            f"{_pct_or_dash(pooled['loo_incident_p10_adequacy_max'])}].")
        add(f"- **late misses (the DANGEROUS polarity):** "
            f"**{_pct(pooled['late_miss_frac'])}** (of which never-reached "
            f"{_pct(pooled['never_reached_frac'])}). p50 inside window: "
            f"{_pct(pooled['p50_in_window_frac'])}.")
        skill_txt = (f"**{pooled['skill']:.2f}x**"
                    if pooled['skill'] is not None
                    else "**n/a** (null model covered 0 cells)")
        add(f"- **null-model skill** (p10_adequacy / isotropic-dilation-"
            f"at-equal-area baseline): {skill_txt} — null baseline "
            f"adequacy {_pct(pooled['null_adequacy_frac'])} at the "
            f"engine's own p10 area budget. > 1x = the physics beats a "
            f"physics-free equal-area circle.")
        add(f"- **true medians** (statistics.median, not the deprecated "
            f"upper-middle `sorted[n//2]`): head ratio p50 "
            f"**{pooled['median_head_ratio_p50']:.2f}x**, p10 "
            f"{pooled['median_head_ratio_p10']:.2f}x, nominal "
            f"{pooled['median_head_ratio_nominal']:.2f}x; area ratio p50 "
            f"{pooled['median_area_ratio_p50']:.2f}x, p10 "
            f"{pooled['median_area_ratio_p10']:.2f}x.")
        ps = pooled["pit_summary"]
        add(f"- **PIT histogram** ({ps['n_bins']} bins, midpoint approx): "
            f"{pooled['pit_hist']} — extreme-bin mass "
            f"{_pct(ps['extreme_bins_frac'])} (rank-0 "
            f"{_pct(ps['lo_bin_frac'])} = observed burn before every "
            f"member [engine late], top-rank {_pct(ps['hi_bin_frac'])} = "
            f"after every member [engine early]); flatness "
            f"chi2={ps['chi2_flatness']:.1f} on {ps['chi2_df']} df (0 = "
            f"perfectly flat/well-calibrated; large = under-dispersed).")
        if pooled_wide.get("n_cells"):
            add(f"- widened variant (wind_perturb 0.50 / dir 45 deg): "
                f"p10 adequacy {_pct(pooled_wide['p10_adequacy_frac'])}, "
                f"late {_pct(pooled_wide['late_miss_frac'])}.")
        add("")
        add("### Window-length bins")
        add("")
        add("Score **dt<=12h as the primary bin** — the pooled-all-windows "
            "average over-weights long, well-fought windows (see Incident "
            "concentration below and `docs/calibration/SCORECARD.md`).")
        add("")
        add("| dt window | pairs | cells | p10 adequacy | late miss |")
        add("|---|---|---|---|---|")
        for b in pooled["window_length_bins"]:
            if b["pairs"] == 0:
                add(f"| {b['label']} | 0 | 0 | — | — |")
                continue
            add(f"| {b['label']} | {b['pairs']} | {b['cells']:,} | "
                f"{_pct(b['p10_adequacy'])} | {_pct(b['late_miss'])} |")
        add("")
        add("### Incident concentration")
        add("")
        add(f"- **{pooled['n_incidents']}** distinct incidents contribute "
            f"the {pooled['n_cells']:,} pooled cells. The largest single "
            f"incident carries **"
            f"{_pct(pooled['largest_incident_share_of_cells'])}** of all "
            f"scored cells and **"
            f"{_pct(pooled['largest_incident_share_of_late_miss'])}** of "
            f"all late-miss cells — a pooled percentage can be dominated "
            f"by one fire's behavior, not a representative sample. Use the "
            f"equal-incident mean and leave-one-out range above alongside "
            f"the cell-weighted headline; cell count is not inferential "
            f"sample size.")
        add("")
        add("### Reading these numbers (interpretation caveats)")
        add("")
        add("- **Suppression is unmodeled.** These fires were actively "
            "fought; on contained flanks and late-stage pairs the engine "
            "over-runs the observed growth (head/area ratios well above 1, "
            "PIT mass at the top rank read 'engine early'). That residual "
            "is firefighting, NOT evidence to slow the engine down — and it "
            "is the fail-safe direction for an alerting product.")
        add("- **The safety-relevant evidence is the explosive-run pairs** "
            "(first hours/day of each fire, low head-run ratios, high late "
            "miss): there the engine is definitively LATE — no member "
            "reaches cells that actually burned inside the window. Those "
            "are the pairs a missed alert would have come from.")
        add("- **ERA5 reanalysis wind is smooth.** Hourly 10-m reanalysis at "
            "the fire centroid underestimates canyon/gap gusts and local "
            f"fire-weather extremes{wx_note}, so part of the late bias may "
            "be input-side, not physics-side. A HRRR-analysis or "
            "RAWS-station replay is the follow-up that would split the two.")
        add("")
        cls_tot: dict = pooled.get("by_fuel_class") or {}
        if cls_tot:
            add("### By FBFM40 fuel class (pooled)")
            add("")
            add("| class | new cells | late-miss | covered [certain..possible] |")
            add("|---|---|---|---|")
            for cls in sorted(cls_tot):
                rec = cls_tot[cls]
                if cls == "NB":
                    add(f"| NB (non-burnable) | {rec['n']:,} | — | — (unscorable "
                        f"by the surface model) |")
                    continue
                nn = max(1, rec["n"])
                add(f"| {cls} | {rec['n']:,} | {_pct(rec['late'] / nn)} | "
                    f"[{_pct(rec['cert'] / nn)} .. {_pct(rec['poss'] / nn)}] |")
            add("")
    add("## Landmark anchors (offline scenario checks)")
    add("")
    add("Parameterized replays of the two documented conflagration anchors "
        "on flat uniform synthetic fuel at the review's fire-day conditions "
        "(`hindcast_lib.TUBBS` / `CAMP`). `ratio` = engine/observed; < 1 = "
        "engine SLOWER than the historical fire (LATE polarity). The Camp "
        "anchor scores AREA growth, which squares any ROS error (area ~ "
        "ROS^2) — expect a lower ratio than the head-run anchors. Point ROS "
        "= the single-cell Rothermel chain (the review's F3 measurement).")
    add("")
    add("| anchor | observed | fuel | spotting | engine | ratio | point ROS km/h |")
    add("|---|---|---|---|---|---|---|")
    for a in anchors:
        obs = (f"{a.observed:g} km/h head" if "head" in a.metric
               else f"{a.observed:,.0f} ac/h")
        eng = (f"{a.engine:.2f} km/h" if "head" in a.metric
               else f"{a.engine:,.0f} ac/h")
        note = f" ({a.notes})" if a.notes else ""
        pros = f"{a.point_ros_kmh:.2f}" if a.point_ros_kmh > 0.0 else "—"
        add(f"| {a.anchor} | {obs} | {a.fuel_label} | "
            f"{'ON' if a.spotting else 'OFF'} | {eng}{note} | "
            f"**{a.ratio:.2f}x** | {pros} |")
    add("")
    add("## RECOMMENDATION")
    add("")
    for r in recommendations(pooled, pooled_wide, anchors):
        add(f"- {r}")
    add("")
    add(DO_NOT_SHIP_NOTE)
    add("## Reachability / degradation log")
    add("")
    if fetch_summary:
        add(f"- HTTP requests, cumulative across fetch invocations: "
            f"**{fetch_summary.get('n_requests', 0)}** (read-only GETs, "
            f"keyless public services only; hard per-invocation budget "
            f"{fetch_summary.get('budget', '?')}; the full per-URL log is "
            f"`tools/hindcast_data/fetch_log.json`).")
        for line in fetch_summary.get("notes", []):
            add(f"- {line}")
        fetched = fetch_summary.get("fetched_at_utc")
        if fetched:
            add(f"- Data fetched (UTC): {fetched} — cached under "
                f"`tools/hindcast_data/`; replay + this report regenerate "
                f"offline from the cache.")
    else:
        add("- No fetch log found (offline run against a pre-existing cache).")
    add("")
    return "\n".join(L) + "\n"
