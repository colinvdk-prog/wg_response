"""wgresponse.safety — hard crew-safety math (contracts SAFETY SEMANTICS).

Public seam (contracts.MODULE SEAMS):
    latest_departure_s(eta_p10_s, cfg) -> float
    corridor_safe(a, b, depart_s, speed_mps, eroded_p10, cfg) -> CorridorVerdict
    egress_ok(stop_loc, depart_s, safe_zones, eroded_p10, cfg) -> CorridorVerdict

Supplemental public helpers (documented additions; the contract seam above is
unchanged):
    latest_service_start_s(eta_p10_s, cfg) -> float
    derive_job_windows(job, cfg, wx=None) -> DispatchJob
    egress_choice(stop_loc, depart_s, safe_zones, eroded_p10, cfg)
        -> (CorridorVerdict, zone_id)   # dispatch needs the winning zone id
    gel_life_h(temp_c, rh_pct, wind_mps) -> float
    class LegOracle (Sprint 2 B1)       # per-leg corridor precompute:
        .latest_safe_depart_s           #   T* = min_i(arrival_i - margin - d_i/speed)
        .verdict(depart_s)              #   byte-identical corridor_safe replay

Units: absolute times unix seconds (_s), durations hours (_h) / minutes (_min),
distances meters (_m), speeds m/s (_mps), temperatures Celsius, RH percent.

Safety rules implemented here (contracts SAFETY SEMANTICS 2-4):
  latest_depart_s        = eta_p10_s - (egress_buffer_min + ember_buffer_min)*60
  latest_service_start_s = latest_depart_s - deploy_min*60
  corridor legs are checked against the ERODED P10 arrival raster: at every
  sample (each corridor_sample_m along the leg) the eroded arrival time must
  exceed the crew's pass time by route_margin_min. Cells outside the raster or
  UNREACHED are safe.

geo.py is built concurrently by a sibling cluster: geo.haversine_m /
geo.sample_polyline / geo.erode_min are resolved lazily with stdlib fallbacks
so this module is testable before geo lands. Lat/lon -> cell resolution uses
the GridSpec formula spelled out in contracts (M_PER_DEG_LAT-based), locally.
"""
from __future__ import annotations

import math

from wgresponse.contracts import (
    UNREACHED,
    CorridorVerdict,
    DispatchJob,
    GridSpec,
    LatLon,
    Raster,
    ResponseConfig,
    SafeZone,
    WeatherForecast,
)
from wgresponse.travel import travel_time_s

# Meters per degree of latitude used by the local GridSpec index math
# (contracts.GridSpec docstring formula; geo.py owns the authoritative value).
_M_PER_DEG_LAT = 111_320.0

_GEO = None
_GEO_RESOLVED = False


def _geo():
    """Lazily resolve wgresponse.geo (built concurrently); None if unavailable."""
    global _GEO, _GEO_RESOLVED
    if not _GEO_RESOLVED:
        try:
            from wgresponse import geo as _g  # type: ignore
            _GEO = _g
        except Exception:
            _GEO = None
        _GEO_RESOLVED = True
    return _GEO


# ---------------------------------------------------------------------------
# Local geo fallbacks (used until geo.py lands; same contracted semantics)
# ---------------------------------------------------------------------------

def _haversine_m(a: LatLon, b: LatLon) -> float:
    """Meters between a and b; geo.haversine_m when available, else local."""
    g = _geo()
    if g is not None and hasattr(g, "haversine_m"):
        return g.haversine_m(a, b)
    lat1, lon1 = math.radians(a.lat), math.radians(a.lon)
    lat2, lon2 = math.radians(b.lat), math.radians(b.lon)
    h = (math.sin((lat2 - lat1) / 2.0) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2.0) ** 2)
    return 2.0 * 6_371_000.0 * math.asin(min(1.0, math.sqrt(h)))


def _sample_polyline(a: LatLon, b: LatLon, step_m: float) -> list:
    """Points every ~step_m from a to b inclusive of both endpoints.
    Uses geo.sample_polyline when available; fallback interpolates linearly
    in lat/lon (adequate at corridor scales of a few tens of km)."""
    g = _geo()
    if g is not None and hasattr(g, "sample_polyline"):
        return g.sample_polyline(a, b, step_m)
    dist = _haversine_m(a, b)
    n = max(1, int(math.ceil(dist / max(step_m, 1.0))))
    return [LatLon(a.lat + (b.lat - a.lat) * i / n, a.lon + (b.lon - a.lon) * i / n)
            for i in range(n + 1)]


def _latlon_to_rc(spec: GridSpec, p: LatLon):
    """(row, col) of the cell containing p, or None if outside the grid.
    Inverse of the cell-center formula in contracts.GridSpec (row 0 = north)."""
    r = int(math.floor((spec.north_lat - p.lat) * _M_PER_DEG_LAT / spec.cell_m))
    m_per_deg_lon = _M_PER_DEG_LAT * math.cos(math.radians(p.lat))
    if m_per_deg_lon <= 0.0:
        return None
    c = int(math.floor((p.lon - spec.west_lon) * m_per_deg_lon / spec.cell_m))
    if 0 <= r < spec.nrows and 0 <= c < spec.ncols:
        return (r, c)
    return None


def _rc_center(spec: GridSpec, r: int, c: int) -> LatLon:
    """Cell-center lat/lon per the contracts.GridSpec formula (test helper)."""
    lat = spec.north_lat - (r + 0.5) * spec.cell_m / _M_PER_DEG_LAT
    lon = spec.west_lon + (c + 0.5) * spec.cell_m / (_M_PER_DEG_LAT * math.cos(math.radians(lat)))
    return LatLon(lat, lon)


def _raster_value_at(raster: Raster, p: LatLon):
    """Raster value at p, or None when p is outside the grid. Delegates to
    geo.raster_sample (same containing-cell/None-outside semantics) when
    geo.py is available."""
    g = _geo()
    if g is not None and hasattr(g, "raster_sample"):
        return g.raster_sample(raster, p)
    rc = _latlon_to_rc(raster.spec, p)
    if rc is None:
        return None
    return raster.get(rc[0], rc[1])


def _erode_min_local(r: Raster, radius_m: float) -> Raster:
    """Morphological min filter fallback. Square (Chebyshev) window of
    ceil(radius_m / cell_m) cells — a superset of the radius_m disk, hence
    CONSERVATIVE (never later than the true disk minimum). Separable two-pass,
    O(n * k)."""
    spec = r.spec
    k = int(math.ceil(radius_m / spec.cell_m)) if radius_m > 0 else 0
    if k <= 0:
        return Raster(spec, list(r.data))
    nr, nc, d = spec.nrows, spec.ncols, r.data
    tmp = [0.0] * (nr * nc)
    for rr in range(nr):
        base = rr * nc
        for cc in range(nc):
            lo, hi = max(0, cc - k), min(nc, cc + k + 1)
            tmp[base + cc] = min(d[base + lo:base + hi])
    out = [0.0] * (nr * nc)
    for cc in range(nc):
        for rr in range(nr):
            lo, hi = max(0, rr - k), min(nr, rr + k + 1)
            out[rr * nc + cc] = min(tmp[x * nc + cc] for x in range(lo, hi))
    return Raster(spec, out)


def _erode(r: Raster, radius_m: float) -> Raster:
    """geo.erode_min when available, else the local conservative fallback."""
    g = _geo()
    if g is not None and hasattr(g, "erode_min"):
        return g.erode_min(r, radius_m)
    return _erode_min_local(r, radius_m)


def _corridor_speed_mps(cfg: ResponseConfig) -> float:
    """Straight-line-equivalent ground speed in m/s, consistent with
    travel.travel_time_s: covering haversine distance D in time
    D*circuity*fire_traffic_factor/road_speed_mps means the along-segment
    speed is road_speed_mps / (circuity * fire_traffic_factor)."""
    return cfg.road_speed_mps / (cfg.circuity * cfg.fire_traffic_factor)


# ---------------------------------------------------------------------------
# Gel coating life (environment-scaled)
# ---------------------------------------------------------------------------

def gel_life_h(temp_c: float, rh_pct: float, wind_mps: float) -> float:
    """Gel coating effective life in HOURS under given weather.

    life_h = 6.0 * exp(-0.08*(temp_c-25)) * (rh_pct/50)^0.3 / (1 + 0.05*wind_mps),
    floored at 1.0 h. Provenance: wgdefense gel.py coating-life model —
    UNVERIFIED PRODUCT DATA per contracts SAFETY SEMANTICS 5 (placeholder
    pending GraphGel supplier confirmation). Reference conditions
    (25 C, 50 %RH, 0 m/s) give exactly 6.0 h.
    """
    rh = max(rh_pct, 1e-9)
    wind = max(wind_mps, 0.0)
    life = 6.0 * math.exp(-0.08 * (temp_c - 25.0)) * (rh / 50.0) ** 0.3 / (1.0 + 0.05 * wind)
    return max(1.0, life)


# ---------------------------------------------------------------------------
# Departure / service-start deadlines (contracts SAFETY SEMANTICS 2)
# ---------------------------------------------------------------------------

def latest_departure_s(eta_p10_s: float, cfg: ResponseConfig) -> float:
    """Latest safe departure (unix SECONDS) from a property whose pessimistic
    fire arrival is eta_p10_s:  eta_p10 - (egress_buffer_min + ember_buffer_min)*60.
    UNREACHED (inf) eta yields inf (no deadline)."""
    return eta_p10_s - (cfg.egress_buffer_min + cfg.ember_buffer_min) * 60.0


def latest_service_start_s(eta_p10_s: float, cfg: ResponseConfig) -> float:
    """Latest gel-service start (unix SECONDS): latest_departure_s minus the
    deploy_min gel application time."""
    return latest_departure_s(eta_p10_s, cfg) - cfg.deploy_min * 60.0


def derive_job_windows(job: DispatchJob, cfg: ResponseConfig,
                       wx: "WeatherForecast | None" = None) -> DispatchJob:
    """Fill the derived safety/usefulness window fields on job (unix SECONDS)
    and return it.

    latest_depart_s        = eta_p10 - (egress_buffer_min + ember_buffer_min)*60
    latest_service_start_s = latest_depart_s - deploy_min*60
    earliest_useful_start_s = eta_p50 - effective_gel_window_h*3600

    effective_gel_window_h = cfg.gel_window_h, except when
    cfg.gel_env_model_enabled: it is capped by the coating life gel_life_h(...)
    (wgdefense gel.py formula, UNVERIFIED product data). Weather is evaluated
    at the intended service time — the as-late-as-safe latest_service_start_s
    — via wx.at(...) when a forecast is provided; without a forecast the model
    uses its reference conditions (25 C, 50 %RH, 0 m/s => 6.0 h base life).

    UNREACHED etas propagate: eta_p10=inf => no departure deadline (inf);
    eta_p50=inf => earliest_useful_start_s=inf (an empty window — the fire is
    not forecast to arrive at the median, so a gel deploy has no value).
    """
    job.latest_depart_s = latest_departure_s(job.eta.eta_p10_s, cfg)
    job.latest_service_start_s = job.latest_depart_s - cfg.deploy_min * 60.0

    window_h = cfg.gel_window_h
    if cfg.gel_env_model_enabled:
        if wx is not None:
            t_ref = job.latest_service_start_s
            if not math.isfinite(t_ref):
                t_ref = job.eta.eta_p50_s
            if not math.isfinite(t_ref) and wx.samples:
                t_ref = wx.samples[-1].t_s
            s = wx.at(t_ref)
            life_h = gel_life_h(s.temp_c, s.rh_pct, s.wind_speed_mps)
        else:
            life_h = gel_life_h(25.0, 50.0, 0.0)  # reference-condition base life
        window_h = min(window_h, life_h)

    eta50 = job.eta.eta_p50_s
    job.earliest_useful_start_s = (eta50 - window_h * 3600.0) if math.isfinite(eta50) else UNREACHED
    return job


# ---------------------------------------------------------------------------
# Corridor checks (contracts SAFETY SEMANTICS 3)
# ---------------------------------------------------------------------------

class LegOracle:
    """Precomputed corridor verdicts for ONE leg a->b at speed_mps against ONE
    eroded P10 raster (Sprint 2 B1 — Council-1 perf review D1,
    docs/review/perf_review.md section 3/4: 94% of dispatch.plan() was
    re-sampling identical leg geometry for shifting departure times).

    corridor_safe's verdict is MONOTONE in depart_s: the sample geometry
    (polyline points, cumulative distances d_i, raster arrivals) does not
    depend on the departure time, and each sampled constraint

        arrival_i - (depart_s + d_i/speed) >= margin

    rearranges to  depart_s <= arrival_i - margin - d_i/speed.  The leg is
    therefore safe iff  depart_s <= latest_safe_depart_s  where

        latest_safe_depart_s (T*) = min_i (arrival_i - margin - d_i/speed)

    over samples with a finite arrival — samples outside the raster or
    UNREACHED impose no bound (T* = +inf when nothing constrains the leg).

    verdict(depart_s) replays the exact Sprint-1 corridor_safe loop over the
    precomputed samples — same floats, same operations, same order — so the
    returned CorridorVerdict (safe / reason / worst_margin_h) is byte-identical
    to sampling the leg from scratch (property-tested in tests/test_safety.py
    against a verbatim port of the Sprint-1 loop) while doing no polyline /
    haversine / raster work per call.

    latest_safe_depart_s is the O(1) feasibility fast path used by
    wgresponse.dispatch insertion scans; it agrees with verdict(depart_s).safe
    up to float rounding of the rearranged expression (~1e-7 s at unix-time
    scale; property-tested with a 1 ms guard band).
    """

    __slots__ = ("n_pts", "samples", "margin_s", "latest_safe_depart_s")

    def __init__(self, a: LatLon, b: LatLon, speed_mps: float,
                 eroded_p10: "Raster | None", cfg: ResponseConfig):
        if speed_mps <= 0.0:
            raise ValueError("speed_mps must be > 0")
        self.margin_s = cfg.route_margin_min * 60.0
        samples = []                      # (orig index, point, arrival_s, dt_s)
        n_pts = 0
        if eroded_p10 is not None:
            pts = _sample_polyline(a, b, cfg.corridor_sample_m)
            n_pts = len(pts)
            cum_m = 0.0
            prev = None
            for i, p in enumerate(pts):
                if prev is not None:
                    cum_m += _haversine_m(prev, p)
                prev = p
                v = _raster_value_at(eroded_p10, p)
                if v is None or math.isinf(v):   # outside raster or UNREACHED
                    continue
                # dt = cum_m / speed matches the in-loop `depart + cum/speed`
                # rounding exactly (the quotient is rounded once either way).
                samples.append((i, p, v, cum_m / speed_mps))
        self.n_pts = n_pts
        self.samples = samples
        tstar = UNREACHED                 # unconstrained: any departure is safe
        for _i, _p, v, dt in samples:
            t = v - self.margin_s - dt
            if t < tstar:
                tstar = t
        self.latest_safe_depart_s = tstar

    def verdict(self, depart_s: float) -> CorridorVerdict:
        """Exact CorridorVerdict for a departure at unix depart_s — identical
        samples, arithmetic, and first-failure reason string as the Sprint-1
        corridor_safe loop."""
        margin_s = self.margin_s
        worst_h = UNREACHED
        safe = True
        reason = ""
        for i, p, v, dt in self.samples:
            t_pass = depart_s + dt
            m_h = (v - t_pass) / 3600.0
            if m_h < worst_h:
                worst_h = m_h
            if safe and (v - t_pass) < margin_s:
                safe = False
                reason = (f"corridor unsafe at sample {i + 1}/{self.n_pts} "
                          f"({p.lat:.5f},{p.lon:.5f}): eroded p10 arrival {v:.0f}s < "
                          f"pass {t_pass:.0f}s + margin {margin_s:.0f}s")
        return CorridorVerdict(safe, reason, worst_h)


def corridor_safe(a: LatLon, b: LatLon, depart_s: float, speed_mps: float,
                  eroded_p10: Raster, cfg: ResponseConfig) -> CorridorVerdict:
    """Check the a->b leg (departing at unix depart_s, traveling at speed_mps
    along the segment) against the ERODED pessimistic arrival raster.

    Samples every cfg.corridor_sample_m; at each sample the eroded P10 arrival
    must be >= pass_time + cfg.route_margin_min*60. Samples outside the raster
    or with UNREACHED arrival are safe. Returns:
      safe            — all samples clear the margin
      reason          — first-failure description ('' when safe)
      worst_margin_h  — min(arrival - pass_time) over sampled cells, HOURS
                        (UNREACHED when no sampled cell has a finite arrival);
                        safe <=> worst_margin_h*60 >= route_margin_min.
    eroded_p10=None (no forecast raster available) is treated as everywhere-
    unreached, i.e. safe.

    Sprint 2 B1: implemented as LegOracle(a, b, speed, raster, cfg)
    .verdict(depart_s) — ONE shared sample->verdict path, byte-identical to the
    Sprint-1 inline loop (property-tested in tests/test_safety.py). Callers
    that re-check the same leg at many departure times (dispatch insertion
    scans) should build the LegOracle once and reuse it.
    """
    return LegOracle(a, b, speed_mps, eroded_p10, cfg).verdict(depart_s)


def egress_choice(stop_loc: LatLon, depart_s: float, safe_zones: list,
                  eroded_p10: Raster, cfg: ResponseConfig):
    """Egress corridor verdict plus the chosen zone id.

    Zones are tried nearest-first by travel.travel_time_s (ties by zone_id);
    the first zone with a safe corridor at depart_s wins. Returns
    (CorridorVerdict, zone_id); zone_id='' when no zone is safely reachable
    (verdict then carries reason 'no_safe_egress: ...' and the nearest zone's
    worst margin; -inf margin when no zones are configured — fail closed).
    """
    if not safe_zones:
        return CorridorVerdict(False, "no_safe_egress: no safe zones configured", -UNREACHED), ""
    speed = _corridor_speed_mps(cfg)
    ordered = sorted(safe_zones, key=lambda z: (travel_time_s(stop_loc, z.loc, cfg), z.zone_id))
    nearest_verdict = None
    for z in ordered:
        v = corridor_safe(stop_loc, z.loc, depart_s, speed, eroded_p10, cfg)
        if nearest_verdict is None:
            nearest_verdict = v
        if v.safe:
            return v, z.zone_id
    return CorridorVerdict(False,
                           "no_safe_egress: " + (nearest_verdict.reason or "all zones unsafe"),
                           nearest_verdict.worst_margin_h), ""


def egress_ok(stop_loc: LatLon, depart_s: float, safe_zones: list,
              eroded_p10: Raster, cfg: ResponseConfig) -> CorridorVerdict:
    """Contract seam: corridor_safe to the nearest safely-reachable SafeZone
    (nearest by travel time, falling back to farther zones); fails when no
    zone is reachable safely at depart_s (unix seconds)."""
    verdict, _zone = egress_choice(stop_loc, depart_s, safe_zones, eroded_p10, cfg)
    return verdict
