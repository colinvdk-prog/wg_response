"""wgresponse.fire_belief — the per-fire spread-rate belief (contracts SAFETY
SEMANTICS 14; founder 2026-09-27; docs/FIRE_BELIEF.md).

The founder asked for two things this module answers:
  "as a fire ages and becomes contained, the rate of fire spread should be
   bayesian unless a new weather pattern emerges", and
  "the map ... updated with the live data when it becomes available ... so
   that simulations are always corrected by the real data ... a bayesian
   feedback loop for each fire".

WHAT IT LEARNS. One number per fire: theta = log of a WHOLE-FIRE multiplier
on the engine's head rate of spread (the same place the ensemble's
model-error term acts, spread.compute_arrival ros_scale). Prior N(0,
prior_sd^2) with prior_sd = the engine's own measured model error
(cfg.fire_belief_prior_sd_log, else cfg.model_error_sd_log, else 0.71). The
posterior lives on a fixed 1-D grid over theta in [ln 0.01, ln 5] (step
0.02) — exact for the heavy-tailed and censored likelihood terms, and cheap.

OBSERVATIONS (FireBeliefStore.observe, every cycle, every active non-drill
incident). Two series per fire, never mixed:
  perimeter  a polygon with a NEWER own time (meta perimeter_at_s, else the
             record time; clamped <= now): its area (meta perimeter_acres when
             the feed carries it, else the rings' area in an equal-area
             sinusoidal projection, holes by even-odd depth), time = the
             polygon's own time.
  reported   the record's reported acreage (meta reported_acres, else
             FireIncident.acreage) when it CHANGES, time = meta reported_at_s
             else updated_at_s. An UNCHANGED size becomes a zero-growth
             observation only after the record kept being modified for
             cfg.fire_belief_static_h since the size last changed, with
             STATIC_ERR_INFLATION x the area error — a record's modification
             time can advance without the size being re-measured (WFIGS
             re-states IncidentSize in discrete updates).
Two observations of one series closer than cfg.fire_belief_min_obs_spacing_h
are not both kept (the later is skipped; the next one spans both).
Consecutive observations of one series form a growth WINDOW.

THE MODEL SIDE. After each spread the NOMINAL member's growth curve is kept
(FireBeliefStore.after_spread): G(s) = burned area with arrival <= t_anchor +
s, hourly over the horizon (t_anchor = the perimeter's time, or now for a
point incident), the time the nominal first reached the domain edge, and the
nominal's per-hour multiplier (1.0 unless apply). The curve is PINNED to the
newest observation of each series that has none yet (at most PIN_MAX_LAG_S
after it was seen) — so a window's model rate is the forecast the engine
made at (or just after) the window's start, never a hindcast with hindsight.

LIKELIHOOD (size-independent: point and perimeter fires both learn). Areas
become equivalent-circle radii r = sqrt(A / pi). Observed radial rate
v_obs = (r_b - r_a) / (t_b - t_a). Model rate v_nom = the slope of r(G(s))
over the same window on the pinned curve (point curves skip the first
POINT_TRANSIENT_S: a fire started from a point is not yet a fire front).
TIME-SCALING APPROXIMATION: under multiplier exp(theta) every arrival time
scales ~1/m, so the model rate scales ~m: v(theta) = v_nom * exp(theta) /
m_nom, m_nom = the nominal's own mean multiplier over the window. Noise: the
area errors (relative cfg.fire_belief_area_rel_err, absolute
cfg.fire_belief_area_abs_err_acres) propagated to r at both ends, plus a
model discrepancy of cfg.fire_belief_discrepancy_frac x v(theta), under a
Student-t with cfg.fire_belief_noise_nu degrees of freedom (heavy tails: one
re-stated acreage cannot swing the fire). When the curve ends (horizon) or
the nominal reaches the domain edge inside the window, the model's growth is
only a LOWER bound: the window then only rules out multipliers that would
make the model outrun the observation even at that bound (a censored,
one-sided term — the profile likelihood: flat wherever the bound is at or
below the observation, the ordinary density at the bound above it; it can
slow a fire's belief, never speed it). Each window's
log-likelihood is weighted 0.5^(age / cfg.fire_belief_half_life_h) (age from
its end); where a perimeter window and a reported window cover the same
hours, each counts 1/(1 + overlap fraction) (one fire's growth is not two
independent facts).

NEW WEATHER PATTERN ("unless a new weather pattern emerges"). Each cycle the
fire-centroid forecast's CURRENT hour (effective wind with the cfg gust
blend, direction, RH, temperature) is logged hourly per fire (bounded to
WEATHER_LOG_MAX hours). Severity S = the engine's own Rothermel head ROS for
the incident tile's dominant burnable fuel on flat ground under that hour's
wind and dead-fuel moisture (fuels.dead_fuel_moisture_frac, as spread uses).
Evidence severity = the weighted P90 of S over the logged hours inside the
posterior's windows (weights = the windows'); evidence direction = the
speed-weighted circular mean. A forecast hour is NEW if S > cfg.fire_belief_
novel_severity_ratio x the evidence severity, or its wind is >=
cfg.fire_belief_novel_min_wind_mps and turns more than cfg.fire_belief_novel_
shift_deg from the evidence direction (or the evidence winds had no steady
direction: resultant < DIR_MIN_RESULTANT). From the FIRST new hour to the
end of the horizon the PRIOR applies. No logged weather inside the evidence
=> every hour is new.

SURPRISE RESET ("slow to trust, quick to distrust"). Before a new window
joins the posterior, the belief's posterior-predictive P90 for that window's
growth is computed; growth beyond it resets the fire to the prior for
cfg.fire_belief_reset_h (sticky, extended by a later surprise) and drops
every window older than the surprising one.

RAPID + KNOWN (the off-map rule's inputs). known = at least one completed
window. rapid = a window ending within cfg.fire_belief_rapid_lookback_h grew
>= cfg.fire_belief_rapid_acres_per_day (24-h equivalent), or >=
cfg.fire_belief_rapid_frac_per_day of its starting area per 24 h with >=
cfg.fire_belief_rapid_min_acres grown; or a YOUNG fire (discovered within
the lookback) reached that rate since discovery (elapsed floored at
YOUNG_FIRE_MIN_ELAPSED_S); or a surprise reset is active.

V2 RULES (2026-09-28; docs/FIRE_BELIEF.md "v2"; every knob's default is the
rule above, byte-identical; watch --fire-belief-v2 arms all three). Measured
on 11 real 2026 fires (docs/review/belief_vs_physics_2026-09-27.md): the
hourly new-weather test fired on the ordinary afternoon wind turn (median
13 h into a forecast), and the area-fraction noise hid a big fire's growth
(Little Giant, ~80,000 ac, 3,000-5,000 ac/day: not rapid at the forecast
before its 14,780-ac run).
  cfg.fire_belief_novelty = 'daily': forecast DAYS are compared with
  evidence DAYS (the fire's local solar days; longitude of the forecast
  point). Per day: its PEAK severity and its DRIVING wind (the speed-
  weighted circular mean over its hours at >= DAY_DRIVE_FRAC x the day's
  peak). The reference is the harshest evidence day (the weighted P90 of the
  evidence days' peaks, weights = the windows'). A forecast day is NEW if
  its peak exceeds cfg.fire_belief_novel_day_severity_ratio x the reference,
  or if -- on a day that can drive the fire (peak >= DAY_DRIVE_FRAC x the
  reference) -- its steady driving wind of >= cfg.fire_belief_novel_day_min_
  wind_mps comes more than cfg.fire_belief_novel_day_shift_deg from EVERY
  evidence day's driving wind (evidence days that were calm, unsteady or too
  mild to drive the fire give no sector; with no sector at all any such
  forecast day is new). The current day's summary includes its logged hours
  before now. The PRIOR applies from the first qualifying hour of the first
  new day to the end of the horizon; night and morning winds never define a
  day's driving wind, so the diurnal turn is not a new pattern.
  cfg.fire_belief_rapid_noise = 'calibrated': a PERIMETER window's growth is
  judged beyond a MEASURED re-mapping noise sd = hypot(k x sqrt(A),
  sqrt(2) x cfg.fire_belief_area_abs_err_acres) acres (A = the larger end in
  acres, k = cfg.fire_belief_rapid_perim_noise_k): consecutive perimeters
  share most of their boundary, so the noise scales with the edge (~sqrt A),
  not with the area. Reported-acreage windows keep the legacy area-fraction
  noise (no real-fire data to calibrate them).
  cfg.fire_belief_rapid_persist_windows = N >= 2: N consecutive perimeter
  windows each growing at a rapid rate RAW (no noise discount), the latest
  ending inside the lookback, make the fire rapid even inside single-window
  noise (consecutive windows share an end, so noise alone rarely lifts both).

APPLYING (cfg.fire_belief_apply; with cfg.fire_belief_apply_p10 also the
P10 safety-law change). before_spread
returns a spread.ensemble_arrival member_hour_scale callable: in same-pattern
hours the nominal member runs at the posterior median, member k at the
posterior quantile of its model-error stratum (q_k), the fast side (z_k > 0)
never narrower than cfg.fire_belief_fast_sd_floor in log space around the
median, a rapid fire at the LARGER of prior and posterior at each quantile,
nothing below cfg.fire_belief_mult_floor; in new-pattern hours every member
runs its exact legacy multiplier. It returns None (every member the legacy
call) whenever the state is 'prior' or 'reset', or the first forecast hour
is already new. The monitor then runs applied_ensemble(): unless
cfg.fire_belief_apply_p10, P10 is GUARDED — min(belief P10, physics P10)
from a second, legacy ensemble — so the belief moves the nominal, P50 (the
ladder) and P90 but never makes P10 later than today's engine (measured:
letting it do so is unsafe for contained fires, docs/FIRE_BELIEF.md §5).

FEED SANITY (contracts SAFETY SEMANTICS 18; cfg.feed_sanity, default OFF;
wgresponse.feedsanity; docs/review/feed_sanity_2026-09-27.md). With the
knob, observe() runs the sanity-checked twins of the two observers: a
perimeter or reported size whose own time is in the future (beyond
cfg.feed_sanity_future_h) or before the fire is not observed (never clamped
to now); a polygon's feed area figure is used only while it agrees with its
geometry (cfg.feed_sanity_area_ratio); a polygon under
cfg.feed_sanity_collapse_frac of its own record's reported size is not
observed; a polygon within cfg.feed_sanity_restamp_frac of the last observed
one is a re-stamp that becomes a static zero-growth observation only once
cfg.fire_belief_static_h has passed; a size below the series' last one by
more than cfg.feed_sanity_drop_z x the sd of the difference is HELD (no
observation) until the series comes back up or the lower size persists for
cfg.fire_belief_static_h — then the series restarts there, with no window
across the drop. What it skipped or held returns as events of kind
'sanity' (the monitor's notes). The hold clocks are per process (not
persisted: a restart holds a drop afresh). Knob off = the legacy observers.

PERSISTENCE + ISOLATION. The store is JSON (to_json/from_json); BeliefFile
writes <state-dir>/fire_beliefs.json atomically after each cycle and mirrors
it through persist.S3Mirror as '<prefix>/fire_beliefs.json'; load_into
restores local first, then the mirror. copy() is a deep copy with
persistent=False: a TEST (drill) world gets one through ThreatMonitor(...,
fire_beliefs=store.copy()) and BeliefFile.save refuses to write it — drills
never change the live world's belief. Seeded (drill) incidents are never
observed. Beliefs unseen for PRUNE_UNSEEN_S are dropped; observations,
windows and the weather log are capped per fire.

Deterministic (no RNG, no wall-clock reads: every time comes from the
caller). Stdlib only. Units: unix seconds, meters, m^2 internally, acres at
the display seam, radial rates in meters per hour.
"""
from __future__ import annotations

import bisect
import json
import logging
import math
import os
import threading
import time
from pathlib import Path
from statistics import NormalDist
from typing import Optional

from wgresponse import feedsanity, fuels, rothermel
from wgresponse.contracts import (
    FireObservation,
    FireStatus,
    IncidentSource,
    UNREACHED,
    is_seeded_id,
)

log = logging.getLogger(__name__)

SCHEMA = "wgresponse-fire-beliefs/1"
BELIEF_FILENAME = "fire_beliefs.json"
ACRE_M2 = 4046.8564224
H_S = 3600.0
DEFAULT_PRIOR_SD = 0.71            # the hindcast PIT model error (docs/calibration)
KINDS = ("perimeter", "reported")

# theta grid: log multiplier in [ln 0.01, ln 5], step 0.02 (312 nodes)
THETA_MIN = math.log(0.01)
THETA_MAX = math.log(5.0)
THETA_STEP = 0.02
THETA = tuple(THETA_MIN + i * THETA_STEP for i in range(
    int(math.floor((THETA_MAX - THETA_MIN) / THETA_STEP + 1e-9)) + 1))

# Heuristics (module constants; docs/FIRE_BELIEF.md "Thresholds")
POINT_TRANSIENT_S = 2.0 * H_S      # a point start is not yet a fire front
MIN_MODEL_SPAN_S = 1.0 * H_S       # shortest model span a rate is read over
PIN_MAX_LAG_S = 6.0 * H_S          # a curve pins to an observation seen <= this ago
STATIC_ERR_INFLATION = 3.0         # zero-growth-by-silence area error multiplier
DIR_MIN_RESULTANT = 0.3            # evidence winds steadier than this have a direction
YOUNG_FIRE_MIN_ELAPSED_S = 3.0 * H_S   # floor on time-since-discovery (rapid rule)
PRUNE_UNSEEN_S = 14.0 * 86400.0    # forget fires unseen this long
MAX_OBS_PER_KIND = 16
MAX_WINDOWS = 64
MIN_WINDOW_WEIGHT = 1e-3           # windows lighter than this leave the posterior
WEATHER_LOG_MAX = 240              # hourly rows (10 days)
MAX_CURVE_STEPS = 24 * 14 + 1      # hourly growth-curve points kept per curve
_FALLBACK_FUEL = 102               # GR2 (spread's no-barrier fallback)
# v2 daily new-weather test (cfg.fire_belief_novelty == 'daily'): a day's
# DRIVING hours are those at >= this x its peak severity, and a day whose
# peak is below this x the evidence reference is too mild to drive the fire
# (its wind cannot change the pattern)
DAY_DRIVE_FRAC = 0.5
DAY_S = 86400.0

_ND = NormalDist()


# ---------------------------------------------------------------------------
# Student-t helpers (stdlib: lgamma + the incomplete-beta continued fraction)
# ---------------------------------------------------------------------------

def _t_logpdf_const(nu: float) -> float:
    return (math.lgamma((nu + 1.0) / 2.0) - math.lgamma(nu / 2.0)
            - 0.5 * math.log(nu * math.pi))


def t_logpdf(z: float, nu: float) -> float:
    """log density of the standard Student-t with nu degrees of freedom."""
    return _t_logpdf_const(nu) - (nu + 1.0) / 2.0 * math.log1p(z * z / nu)


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction of the incomplete beta (modified Lentz)."""
    fpmin = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > fpmin else fpmin)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > fpmin else fpmin)
        c = 1.0 + aa / c
        c = c if abs(c) > fpmin else fpmin
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > fpmin else fpmin)
        c = 1.0 + aa / c
        c = c if abs(c) > fpmin else fpmin
        de = d * c
        h *= de
        if abs(de - 1.0) < 3e-15:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
           + a * math.log(x) + b * math.log1p(-x))
    bt = math.exp(lbt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_cdf(z: float, nu: float) -> float:
    """CDF of the standard Student-t (closed forms for nu = 1, 2, 4)."""
    if nu == 4.0:
        u = z * z / 4.0
        s = z / math.sqrt(1.0 + u)
        return 0.5 + 0.375 * s * (1.0 - u / (3.0 * (1.0 + u)))
    if nu == 1.0:
        return 0.5 + math.atan(z) / math.pi
    if nu == 2.0:
        return 0.5 + z / (2.0 * math.sqrt(2.0 + z * z))
    p = 0.5 * betainc(nu / 2.0, 0.5, nu / (nu + z * z))
    return 1.0 - p if z > 0.0 else p


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

_R_EARTH = 6_371_008.8


def _ring_xy(ring) -> list:
    """Sinusoidal (equal-area) projection of a LatLon ring, meters."""
    k = _R_EARTH * math.pi / 180.0
    return [(p.lon * math.cos(math.radians(p.lat)) * k, p.lat * k)
            for p in ring]


def _shoelace(xy) -> float:
    s = 0.0
    n = len(xy)
    for i in range(n):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def _in_ring(x: float, y: float, xy) -> bool:
    inside = False
    n = len(xy)
    j = n - 1
    for i in range(n):
        xi, yi = xy[i]
        xj, yj = xy[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def rings_area_m2(rings) -> float:
    """Area (m^2) of a perimeter's rings under the EVEN-ODD rule the spread
    rasterizer uses: a ring nested inside an odd number of other rings is a
    hole. Equal-area sinusoidal projection (exact for the projected
    polygon; km-scale fires are well inside its accurate band)."""
    rings = [r for r in (rings or []) if r and len(r) >= 3]
    if not rings:
        return 0.0
    polys = []
    for r in rings:
        xy = _ring_xy(r)
        a = abs(_shoelace(xy))
        xs = [p[0] for p in xy]
        ys = [p[1] for p in xy]
        polys.append((a, xy, (min(xs), min(ys), max(xs), max(ys))))
    total = 0.0
    for i, (a, xy, _bb) in enumerate(polys):
        if a <= 0.0:
            continue
        x0, y0 = xy[0]
        depth = 0
        for j, (aj, xyj, bb) in enumerate(polys):
            if j == i or aj <= a:
                continue
            if bb[0] <= x0 <= bb[2] and bb[1] <= y0 <= bb[3] and \
                    _in_ring(x0, y0, xyj):
                depth += 1
        total += -a if depth % 2 else a
    return max(0.0, total)


def _radius(area_m2: float) -> float:
    return math.sqrt(max(0.0, area_m2) / math.pi)


def _radius_sd(area_m2: float, sd_area: float) -> float:
    """sd of the equivalent radius: the half-width of the radius interval
    of area +- sd (robust at small areas where dr/dA diverges)."""
    return 0.5 * (_radius(area_m2 + sd_area) - _radius(area_m2 - sd_area))


def _area_sd(area_m2: float, cfg, static: bool) -> float:
    sd = math.hypot(cfg.fire_belief_area_rel_err * area_m2,
                    cfg.fire_belief_area_abs_err_acres * ACRE_M2)
    return sd * (STATIC_ERR_INFLATION if static else 1.0)


def _interp(g: list, s: float) -> float:
    """G at s seconds after the anchor (hourly nodes, linear, clamped)."""
    if s <= 0.0:
        return g[0]
    x = s / H_S
    i = int(math.floor(x))
    if i >= len(g) - 1:
        return g[-1]
    f = x - i
    return g[i] + f * (g[i + 1] - g[i])


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _num(v) -> Optional[float]:
    """A finite real number or None (bools and junk are None)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _valid_curve(c) -> bool:
    """A growth curve restored from JSON is usable (else it is dropped and
    its window stays unmodeled — no likelihood, never a crash)."""
    if not isinstance(c, dict):
        return False
    g = c.get("g")
    if _num(c.get("anchor")) is None or _num(c.get("t0")) is None or \
            not isinstance(g, list) or len(g) < 2 or \
            any(_num(x) is None for x in g):
        return False
    if c.get("sat") is not None and _num(c.get("sat")) is None:
        return False
    m = c.get("mult")
    if m is not None and not (isinstance(m, list) and m and all(
            (_num(x) or 0.0) > 0.0 for x in m)):
        return False
    return True


def _valid_window(w) -> bool:
    if not isinstance(w, dict) or w.get("kind") not in KINDS:
        return False
    for k in ("ta", "tb", "aa", "ab", "vobs", "sdv"):
        if _num(w.get(k)) is None:
            return False
    if w.get("vnom") is not None:
        if _num(w.get("vnom")) is None or not (_num(w.get("mnom")) or 0) > 0:
            return False
    return w["tb"] > w["ta"] and w["sdv"] > 0.0


def is_drill(inc) -> bool:
    """A seeded (drill) incident: never observed, never learned from."""
    return (getattr(inc, "source", None) is IncidentSource.SEEDED
            or is_seeded_id(getattr(inc, "incident_id", "")))


def prior_sd(cfg) -> float:
    """The prior sd of theta: cfg.fire_belief_prior_sd_log when > 0, else
    the ensemble model-error term when on, else DEFAULT_PRIOR_SD."""
    v = float(getattr(cfg, "fire_belief_prior_sd_log", 0.0) or 0.0)
    if v > 0.0:
        return v
    v = float(getattr(cfg, "model_error_sd_log", 0.0) or 0.0)
    return v if v > 0.0 else DEFAULT_PRIOR_SD


def horizon_hours(cfg) -> int:
    return int(math.ceil(float(cfg.horizon_h)))


def _fmt_t(t_s: Optional[float]) -> str:
    if t_s is None:
        return "?"
    return time.strftime("%a %d %b %H:%M UTC", time.gmtime(float(t_s)))


def _angdiff(a: float, b: float) -> float:
    d = abs((a - b) % 360.0)
    return 360.0 - d if d > 180.0 else d


def _fuel_model(code: Optional[int]):
    if code is None:
        return None
    return fuels.FUEL_TABLE.get(code) or fuels.FUEL_TABLE.get(_FALLBACK_FUEL)


def dominant_fuel(tile) -> Optional[int]:
    """The most common burnable FBFM40 code on the tile (ties: lowest)."""
    counts: dict = {}
    for v in tile.fuel.data:
        c = int(round(v))
        if fuels.is_burnable(c):
            counts[c] = counts.get(c, 0) + 1
    if not counts:
        return None
    return min(counts, key=lambda c: (-counts[c], c))


def severity_mps(fuel, wind10_mps: float, rh_pct: float, temp_c: float,
                 cfg) -> float:
    """Weather severity index: the engine's Rothermel head ROS (m/s) for
    `fuel` on flat ground under this wind and dead-fuel moisture."""
    m_dead = max(0.01, fuels.dead_fuel_moisture_frac(temp_c, rh_pct))
    u = rothermel.midflame_wind_mps(max(0.0, wind10_mps), fuel)
    return rothermel.surface_ros_mps(fuel, m_dead, cfg.live_herb_frac,
                                     cfg.live_woody_frac, u, 0.0).ros_mps


def _eff_wind(s, cfg) -> float:
    blend = float(getattr(cfg, "wind_gust_blend", 0.0) or 0.0)
    return s.wind_speed_mps + blend * max(0.0, s.wind_gust_mps
                                          - s.wind_speed_mps)


def _day_summary(hours: list) -> tuple:
    """(peak severity, driving wind speed, driving direction, resultant) of
    one day's hours [(severity, wind, dir)] (v2 daily new-weather test):
    DRIVING hours = those at >= DAY_DRIVE_FRAC x the day's peak (all hours
    when the day cannot spread fire at all); the driving wind is their mean
    speed and speed-weighted circular mean direction (resultant = its
    steadiness, 0..1)."""
    peak = max(h[0] for h in hours)
    drv = ([h for h in hours if h[0] >= DAY_DRIVE_FRAC * peak]
           if peak > 0.0 else list(hours))
    spd = sum(h[1] for h in drv) / len(drv)
    sx = sum(h[1] * math.sin(math.radians(h[2])) for h in drv)
    sy = sum(h[1] * math.cos(math.radians(h[2])) for h in drv)
    sw = sum(h[1] for h in drv)
    res = math.hypot(sx, sy) / sw if sw > 0.0 else 0.0
    return peak, spd, math.degrees(math.atan2(sx, sy)) % 360.0, res


def perimeter_growth_sd_acres(aa_m2: float, ab_m2: float, cfg) -> float:
    """v2 CALIBRATED perimeter re-mapping noise of one window's growth
    (cfg.fire_belief_rapid_noise == 'calibrated'), acres: hypot(k x
    sqrt(A), sqrt(2) x cfg.fire_belief_area_abs_err_acres), A = the larger
    end in acres, k = cfg.fire_belief_rapid_perim_noise_k. Measured on 11
    real 2026 fires (scratch_review/belief_vs_physics/v2_noise.py): the
    noise follows the re-drawn EDGE (~sqrt A), not the area."""
    a = max(aa_m2, ab_m2, 0.0) / ACRE_M2
    k = float(cfg.fire_belief_rapid_perim_noise_k)
    return math.hypot(k * math.sqrt(a),
                      math.sqrt(2.0) * float(cfg.fire_belief_area_abs_err_acres))


def _persistent_growth(windows: list, lb_s: float, n: int, cfg) -> str:
    """v2 PERSISTENCE trigger (cfg.fire_belief_rapid_persist_windows = n >=
    2): the reason text when n consecutive PERIMETER windows (each starting
    where the previous ended) each grew at a rapid rate RAW -- >= cfg.
    fire_belief_rapid_acres_per_day (24-h equivalent), or >= cfg.fire_belief_
    rapid_frac_per_day of its start with >= cfg.fire_belief_rapid_min_acres
    -- and the latest of them ended at or after lb_s; '' otherwise.
    Consecutive windows share an end, so a re-mapping error that inflates
    one deflates the next: noise alone rarely lifts both."""
    per_day_thr = float(cfg.fire_belief_rapid_acres_per_day)
    frac_thr = float(cfg.fire_belief_rapid_frac_per_day)
    min_ac = float(cfg.fire_belief_rapid_min_acres)
    run: list = []
    best: list = []
    for w in sorted((w for w in windows if w.get("kind") == "perimeter"),
                    key=lambda w: w["tb"]):
        days = (w["tb"] - w["ta"]) / 86400.0
        grown = (w["ab"] - w["aa"]) / ACRE_M2
        a0 = w["aa"] / ACRE_M2
        ok = days > 0.0 and grown > 0.0 and (
            grown / days >= per_day_thr
            or (grown >= min_ac and a0 > 0.0
                and grown / a0 / days >= frac_thr))
        if not ok:
            run = []
            continue
        if run and abs(run[-1]["tb"] - w["ta"]) < 1e-6:
            run.append(w)
        else:
            run = [w]
        if len(run) >= n and run[-1]["tb"] >= lb_s:
            best = run[-n:]
    if not best:
        return ""
    rates = ", ".join(
        f"{(w['ab'] - w['aa']) / ACRE_M2 / ((w['tb'] - w['ta']) / 86400.0):,.0f}"
        for w in best)
    return (f"perimeter growth at a rapid rate in {len(best)} consecutive "
            f"windows ({rates} ac/24 h, raw)")


def _covered_fraction(ta: float, tb: float, spans: list) -> float:
    """Fraction of [ta, tb] covered by the union of `spans`."""
    if tb <= ta or not spans:
        return 0.0
    segs = sorted((max(ta, a), min(tb, b)) for a, b in spans
                  if min(tb, b) > max(ta, a))
    cov = 0.0
    cur_a = cur_b = None
    for a, b in segs:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                cov += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        cov += cur_b - cur_a
    return min(1.0, cov / (tb - ta))


# ---------------------------------------------------------------------------
# The posterior grid
# ---------------------------------------------------------------------------

class Posterior:
    """Normalized probabilities on THETA with a CDF-interpolated quantile
    (each node's mass spread uniformly over its step)."""

    __slots__ = ("p", "cum")

    def __init__(self, logp: list) -> None:
        m = max(logp)
        w = [math.exp(v - m) if v - m > -745.0 else 0.0 for v in logp]
        s = sum(w)
        self.p = [x / s for x in w]
        c = 0.0
        cum = []
        for x in self.p:
            c += x
            cum.append(c)
        self.cum = cum

    def quantile(self, q: float) -> float:
        q = min(max(q, 0.0), 1.0)
        i = bisect.bisect_left(self.cum, q)
        if i >= len(THETA):
            return THETA_MAX
        prev = self.cum[i - 1] if i > 0 else 0.0
        frac = (q - prev) / self.p[i] if self.p[i] > 0.0 else 0.5
        th = THETA[i] - 0.5 * THETA_STEP + frac * THETA_STEP
        return min(max(th, THETA_MIN), THETA_MAX)

    def mult(self, q: float) -> float:
        return math.exp(self.quantile(q))


def _prior_logp(cfg) -> list:
    sd = prior_sd(cfg)
    return [-0.5 * (t / sd) ** 2 for t in THETA]


def _window_ll(w: dict, cfg) -> list:
    """Per-node log-likelihood of one modeled window (cached on the window
    under '_ll', keyed by the noise knobs; never persisted)."""
    nu = float(cfg.fire_belief_noise_nu)
    disc = float(cfg.fire_belief_discrepancy_frac)
    key = (nu, disc)
    cached = w.get("_ll")
    if cached is not None and cached[0] == key:
        return cached[1]
    vobs = w["vobs"]
    sdv = w["sdv"]
    k0 = w["vnom"] / w["mnom"]
    c = _t_logpdf_const(nu)
    half = (nu + 1.0) / 2.0
    cens = bool(w.get("cens"))
    out = []
    for th in THETA:
        mu = k0 * math.exp(th)
        if cens and mu <= vobs:
            # CENSORED (the model's growth is only a lower bound mu): a model
            # at least this fast CAN match the observation exactly — the
            # profile likelihood is the density's peak at mu = v_obs, so the
            # term is flat here and only penalizes multipliers whose lower
            # bound already outruns the observation (it can slow, never
            # speed). Continuous at mu = v_obs.
            mu = vobs
        sig = math.sqrt(sdv * sdv + (disc * mu) ** 2)
        if sig <= 1e-12:
            sig = 1e-12
        z = (vobs - mu) / sig
        out.append(c - half * math.log1p(z * z / nu) - math.log(sig))
    w["_ll"] = (key, out)
    return out


def _predictive_rates(post: Posterior, w: dict, cfg) -> tuple:
    """(median, P90) of the observed radial rate the posterior predicts for
    window w (the posterior mixture of the t noise model, bisection)."""
    nu = float(cfg.fire_belief_noise_nu)
    disc = float(cfg.fire_belief_discrepancy_frac)
    k0 = w["vnom"] / w["mnom"]
    sdv = w["sdv"]
    comps = []
    for p, th in zip(post.p, THETA):
        if p < 1e-10:
            continue
        mu = k0 * math.exp(th)
        sig = max(math.sqrt(sdv * sdv + (disc * mu) ** 2), 1e-12)
        comps.append((p, mu, sig))
    tot = sum(p for p, _m, _s in comps)
    lo = min(mu - 80.0 * sig for _p, mu, sig in comps)
    hi = max(mu + 80.0 * sig for _p, mu, sig in comps)

    def cdf(v):
        return sum(p * t_cdf((v - mu) / sig, nu) for p, mu, sig in comps) / tot

    def qtl(q):
        a, b = lo, hi
        for _ in range(80):
            mid = 0.5 * (a + b)
            if cdf(mid) < q:
                a = mid
            else:
                b = mid
        return 0.5 * (a + b)

    return qtl(0.5), qtl(0.9)


# ---------------------------------------------------------------------------
# Per-incident belief
# ---------------------------------------------------------------------------

class IncidentBelief:
    """Everything the store keeps for one fire (JSON round-trippable)."""

    def __init__(self, incident_id: str, first_seen_s: float) -> None:
        self.incident_id = incident_id
        self.first_seen_s = float(first_seen_s)
        self.last_seen_s = float(first_seen_s)
        self.discovered_s: Optional[float] = None
        self.latest_acres: Optional[float] = None
        self.obs: dict = {k: [] for k in KINDS}
        self.perim_last_t: Optional[float] = None
        self.rep_value: Optional[float] = None
        self.rep_changed_s: Optional[float] = None
        self.rep_record_s: Optional[float] = None
        self.windows: list = []
        self.wx_log: list = []
        self.fuel_code: Optional[int] = None
        self.reset_until_s: Optional[float] = None
        self.reset_reason = ""
        self.floor_s: Optional[float] = None
        # per-cycle (not persisted)
        self.novel_from_s: Optional[float] = None
        self.novel_reason = ""
        self.novel_at_s: Optional[float] = None
        self.pending_nominal: Optional[list] = None
        # FEED SANITY (SEMANTICS 18e; per process, not persisted): the monitor
        # time a size drop was first held, per series; and what was last
        # said per sanity key (one note per new finding, not per cycle)
        self.hold_since: dict = {}
        self.sanity_said: dict = {}

    _PERSIST = ("first_seen_s", "last_seen_s", "discovered_s", "latest_acres",
                "perim_last_t", "rep_value", "rep_changed_s", "rep_record_s",
                "fuel_code", "reset_until_s", "reset_reason", "floor_s")

    def to_json(self) -> dict:
        d = {k: getattr(self, k) for k in self._PERSIST}
        d["obs"] = {k: [dict(o) for o in self.obs[k]] for k in KINDS}
        d["windows"] = [{k: v for k, v in w.items() if not k.startswith("_")}
                        for w in self.windows]
        d["wx_log"] = [list(r) for r in self.wx_log]
        return d

    @classmethod
    def from_json(cls, incident_id: str, d: dict) -> "IncidentBelief":
        first = _num(d.get("first_seen_s"))
        if first is None:
            raise ValueError("first_seen_s missing")
        b = cls(str(incident_id), first)
        for k in cls._PERSIST:
            if k in ("reset_reason",):
                b.reset_reason = str(d.get(k) or "")
            elif k == "fuel_code":
                v = d.get(k)
                b.fuel_code = int(v) if isinstance(v, int) and not \
                    isinstance(v, bool) else None
            elif k != "first_seen_s":
                setattr(b, k, _num(d.get(k)))
        if b.last_seen_s is None:
            b.last_seen_s = first
        obs = d.get("obs") or {}
        for k in KINDS:
            rows = obs.get(k) or []
            keep = []
            for o in rows:
                if not (isinstance(o, dict) and _num(o.get("t")) is not None
                        and _num(o.get("a")) is not None):
                    continue
                o = dict(o)
                o["kind"] = k
                o.setdefault("seen", o["t"])
                if not _valid_curve(o.get("curve")):
                    o["curve"] = None
                keep.append(o)
            b.obs[k] = keep
        b.windows = [dict(w) for w in (d.get("windows") or [])
                     if _valid_window(w)]
        b.wx_log = [list(r) for r in (d.get("wx_log") or [])
                    if isinstance(r, (list, tuple)) and len(r) == 5
                    and all(_num(x) is not None for x in r)]
        return b


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

class FireBeliefStore:
    """Per-fire beliefs for one world. Owned by threat.ThreatMonitor
    (.fire_beliefs) and consulted every cycle only with cfg.fire_belief_track.
    Thread-safe (the watch daemon persists from HTTP threads too).

    persistent=False marks a DRILL/TEST copy: BeliefFile.save refuses to
    write it, so a test world can never change the live belief."""

    def __init__(self, persistent: bool = True) -> None:
        self.persistent = bool(persistent)
        self._beliefs: dict = {}
        self._lock = threading.RLock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._beliefs)

    def belief(self, incident_id: str) -> Optional[IncidentBelief]:
        """The live IncidentBelief (tests / tooling; do not mutate)."""
        with self._lock:
            return self._beliefs.get(incident_id)

    # ------------------------------------------------------------ observe

    def observe(self, incidents, now_s: float, cfg) -> list:
        """Record this cycle's growth observations for every non-drill
        incident; completes windows (likelihood + surprise test). Returns
        event dicts {incident_id, kind, message} (kind 'reset' on a surprise
        reset) for the monitor's notes / operator events."""
        events: list = []
        now_s = float(now_s)
        with self._lock:
            for inc in incidents:
                if is_drill(inc):
                    continue
                iid = inc.incident_id
                bel = self._beliefs.get(iid)
                if bel is None:
                    bel = self._beliefs[iid] = IncidentBelief(iid, now_s)
                bel.last_seen_s = now_s
                disc = _num(inc.discovered_at_s)
                if disc is not None and 0.0 < disc <= now_s:
                    bel.discovered_s = disc
                if getattr(cfg, "feed_sanity", False):
                    # SAFETY SEMANTICS 18: the sanity-checked observers
                    events += self._observe_perimeter_sane(bel, inc, now_s,
                                                           cfg)
                    events += self._observe_reported_sane(bel, inc, now_s,
                                                          cfg)
                else:
                    events += self._observe_perimeter(bel, inc, now_s, cfg)
                    events += self._observe_reported(bel, inc, now_s, cfg)
        return events

    def _observe_perimeter(self, bel, inc, now_s, cfg) -> list:
        if not inc.perimeter:
            return []
        meta = inc.meta or {}
        pat = _num(meta.get("perimeter_at_s"))
        if pat is None or pat <= 0.0:
            pat = _num(inc.updated_at_s)
        if pat is None or pat <= 0.0:
            return []
        pat = min(pat, now_s)
        if bel.perim_last_t is not None and pat <= bel.perim_last_t:
            return []
        bel.perim_last_t = pat
        area = rings_area_m2(inc.perimeter)
        if not area > 0.0:
            return []
        feed_acres = _num(meta.get("perimeter_acres"))
        if feed_acres is not None and feed_acres <= 0.0:
            feed_acres = None
        return self._record(bel, "perimeter", pat, area, now_s, cfg,
                            feed_acres=feed_acres)

    def _observe_reported(self, bel, inc, now_s, cfg) -> list:
        meta = inc.meta or {}
        acres = _num(meta.get("reported_acres"))
        if acres is None:
            acres = _num(inc.acreage)
        t_rep = _num(meta.get("reported_at_s"))
        if t_rep is None or t_rep <= 0.0:
            t_rep = _num(inc.updated_at_s)
        if acres is None or acres <= 0.0 or t_rep is None or t_rep <= 0.0:
            return []
        bel.latest_acres = acres
        t_rep = min(t_rep, now_s)
        upd = _num(inc.updated_at_s)
        t_mod = max(t_rep, min(upd, now_s) if upd is not None else t_rep)
        if bel.rep_value is None or abs(acres - bel.rep_value) > \
                1e-9 * max(1.0, acres):
            bel.rep_value = acres
            bel.rep_changed_s = t_rep
            bel.rep_record_s = t_mod
            return self._record(bel, "reported", t_rep, acres * ACRE_M2,
                                now_s, cfg)
        # UNCHANGED size: zero growth only after the record kept being
        # modified for the static window since the size last changed
        if bel.rep_record_s is None or t_mod > bel.rep_record_s:
            bel.rep_record_s = t_mod
        static_s = float(cfg.fire_belief_static_h) * H_S
        series = bel.obs["reported"]
        last_t = series[-1]["t"] if series else None
        if bel.rep_changed_s is not None and \
                t_mod - bel.rep_changed_s >= static_s and \
                (last_t is None or t_mod - last_t >= static_s):
            return self._record(bel, "reported", t_mod, acres * ACRE_M2,
                                now_s, cfg, static=True)
        return []

    # ------------------------------------------------ feed sanity (SEM 18)

    def _sanity(self, bel, key: str, value, message: str) -> list:
        """One 'sanity' event per new finding: `key` names the check, `value`
        what it looked at (a time, an area); the same finding again (the
        same polygon every cycle) says nothing."""
        if bel.sanity_said.get(key) == value:
            return []
        bel.sanity_said[key] = value
        return [{"incident_id": bel.incident_id, "kind": "sanity",
                 "message": f"{message} (SAFETY SEMANTICS 18)"}]

    @staticmethod
    def _release(bel, kind: str) -> None:
        """A held drop of `kind` is over (the size came back): forget the
        hold and its note, so the next drop is held AND said afresh."""
        bel.hold_since.pop(kind, None)
        bel.sanity_said.pop(f"{kind}_drop", None)

    def _observe_perimeter_sane(self, bel, inc, now_s, cfg) -> list:
        """_observe_perimeter under cfg.feed_sanity (SAFETY SEMANTICS 18a-e).
        Order: the own time (18b: implausible = no observation, the newer-
        time mark untouched), the newer-time rule, the area figure vs the
        geometry (18a), collapse under the record's own size (18c: no
        observation), re-stamp (18d: a static observation once
        fire_belief_static_h has passed), drop (18e: held), else observed."""
        if not inc.perimeter:
            return []
        meta = inc.meta or {}
        pat = _num(meta.get("perimeter_at_s"))
        if pat is None or pat <= 0.0:
            pat = _num(inc.updated_at_s)
        if pat is None or pat <= 0.0:
            return []
        why = feedsanity.time_problem(pat, inc.discovered_at_s, now_s, cfg)
        if why is not None:
            return self._sanity(bel, "perimeter_time", pat,
                                f"perimeter not observed: {why}")
        pat = min(pat, now_s)
        if bel.perim_last_t is not None and pat <= bel.perim_last_t:
            return []
        bel.perim_last_t = pat
        area = rings_area_m2(inc.perimeter)
        if not area > 0.0:
            return []
        events = []
        feed_acres = _num(meta.get("perimeter_acres"))
        if feed_acres is not None and feed_acres <= 0.0:
            feed_acres = None
        if feed_acres is not None and not feedsanity.feed_area_stands(
                feed_acres, area / ACRE_M2, cfg):
            events += self._sanity(
                bel, "perimeter_area", pat,
                f"the polygon's area figure {feed_acres:,.1f} ac is more than "
                f"{float(cfg.feed_sanity_area_ratio):g}x off its geometry "
                f"({area / ACRE_M2:,.1f} ac): the geometry's area is used")
            feed_acres = None
        best = feed_acres * ACRE_M2 if feed_acres is not None else area
        why = feedsanity.collapse_problem(best / ACRE_M2,
                                          meta.get("reported_acres"), cfg)
        if why is not None:
            return events + self._sanity(bel, "perimeter_collapse", pat,
                                         f"perimeter not observed: {why}")
        series = bel.obs["perimeter"]
        if series:
            last = series[-1]
            if feed_acres is not None and last.get("feed_acres"):
                new_m2 = feed_acres * ACRE_M2
                ref_m2 = last["feed_acres"] * ACRE_M2
            else:
                new_m2, ref_m2 = area, last["a"]
            if feedsanity.is_restamp(new_m2, ref_m2, cfg):
                # the same capture re-issued under a newer time (18d)
                self._release(bel, "perimeter")
                if pat - last["t"] < float(cfg.fire_belief_static_h) * H_S:
                    return events
                return events + self._record(bel, "perimeter", pat, area,
                                             now_s, cfg, static=True,
                                             feed_acres=feed_acres)
            if feedsanity.is_drop(new_m2, ref_m2, cfg):
                since = bel.hold_since.get("perimeter")
                if since is None:
                    bel.hold_since["perimeter"] = now_s
                    return events + self._sanity(
                        bel, "perimeter_drop", pat,
                        f"perimeter held: {new_m2 / ACRE_M2:,.1f} ac is below "
                        f"the last observed {ref_m2 / ACRE_M2:,.1f} ac beyond "
                        f"measurement noise; it counts only if it persists "
                        f"{float(cfg.fire_belief_static_h):g} h")
                if now_s - since < float(cfg.fire_belief_static_h) * H_S:
                    return events
                # the lower size persisted: a correction, not negative growth
                del bel.hold_since["perimeter"]
                bel.obs["perimeter"] = []
                events += self._sanity(
                    bel, "perimeter_drop", ("restart", pat),
                    f"perimeter series restarts at {new_m2 / ACRE_M2:,.1f} ac: "
                    f"the drop from {ref_m2 / ACRE_M2:,.1f} ac persisted "
                    f"{(now_s - since) / H_S:.1f} h (a correction; no window "
                    f"across it)")
                return events + self._record(bel, "perimeter", pat, area,
                                             now_s, cfg, feed_acres=feed_acres)
        self._release(bel, "perimeter")
        return events + self._record(bel, "perimeter", pat, area, now_s, cfg,
                                     feed_acres=feed_acres)

    def _observe_reported_sane(self, bel, inc, now_s, cfg) -> list:
        """_observe_reported under cfg.feed_sanity (SAFETY SEMANTICS 18b/e):
        an implausible report time is not observed; a size that drops below
        the last one beyond noise is HELD (the young-fire clause keeps the
        pre-drop size) until it comes back or persists fire_belief_static_h
        (then the series restarts there); everything else is the legacy
        observer's rule, including the static unchanged-size rule."""
        meta = inc.meta or {}
        acres = _num(meta.get("reported_acres"))
        if acres is None:
            acres = _num(inc.acreage)
        t_rep = _num(meta.get("reported_at_s"))
        if t_rep is None or t_rep <= 0.0:
            t_rep = _num(inc.updated_at_s)
        if acres is None or acres <= 0.0 or t_rep is None or t_rep <= 0.0:
            return []
        why = feedsanity.time_problem(t_rep, inc.discovered_at_s, now_s, cfg)
        if why is not None:
            return self._sanity(bel, "reported_time", t_rep,
                                f"reported size not observed: {why}")
        t_rep = min(t_rep, now_s)
        upd = _num(inc.updated_at_s)
        t_mod = max(t_rep, min(upd, now_s) if upd is not None else t_rep)
        static_s = float(cfg.fire_belief_static_h) * H_S
        changed = bel.rep_value is None or abs(acres - bel.rep_value) > \
            1e-9 * max(1.0, acres)
        if changed and bel.rep_value is not None and feedsanity.is_drop(
                acres * ACRE_M2, bel.rep_value * ACRE_M2, cfg):
            since = bel.hold_since.get("reported")
            if since is None:
                bel.hold_since["reported"] = now_s
                return self._sanity(
                    bel, "reported_drop", acres,
                    f"reported size held: {acres:,.1f} ac is below the last "
                    f"{bel.rep_value:,.1f} ac beyond measurement noise; it "
                    f"counts only if it persists "
                    f"{float(cfg.fire_belief_static_h):g} h")
            if now_s - since < static_s:
                return []
            # the lower size persisted: a correction, not negative growth
            del bel.hold_since["reported"]
            events = self._sanity(
                bel, "reported_drop", ("restart", acres),
                f"reported series restarts at {acres:,.1f} ac: the drop from "
                f"{bel.rep_value:,.1f} ac persisted {(now_s - since) / H_S:.1f}"
                f" h (a correction; no window across it)")
            bel.obs["reported"] = []
            bel.latest_acres = acres
            bel.rep_value = acres
            bel.rep_changed_s = t_rep
            bel.rep_record_s = t_mod
            return events + self._record(bel, "reported", t_rep,
                                         acres * ACRE_M2, now_s, cfg)
        self._release(bel, "reported")
        bel.latest_acres = acres
        if changed:
            bel.rep_value = acres
            bel.rep_changed_s = t_rep
            bel.rep_record_s = t_mod
            return self._record(bel, "reported", t_rep, acres * ACRE_M2,
                                now_s, cfg)
        if bel.rep_record_s is None or t_mod > bel.rep_record_s:
            bel.rep_record_s = t_mod
        series = bel.obs["reported"]
        last_t = series[-1]["t"] if series else None
        if bel.rep_changed_s is not None and \
                t_mod - bel.rep_changed_s >= static_s and \
                (last_t is None or t_mod - last_t >= static_s):
            return self._record(bel, "reported", t_mod, acres * ACRE_M2,
                                now_s, cfg, static=True)
        return []

    def _record(self, bel, kind, t_s, area_m2, now_s, cfg, static=False,
                feed_acres=None) -> list:
        series = bel.obs[kind]
        spacing = float(cfg.fire_belief_min_obs_spacing_h) * H_S
        if series and t_s < series[-1]["t"] + spacing:
            return []          # too close to (or older than) the last one
        ob = {"kind": kind, "t": float(t_s), "a": float(area_m2),
              "static": bool(static), "seen": float(now_s), "curve": None}
        if feed_acres is not None:
            ob["feed_acres"] = float(feed_acres)
        series.append(ob)
        events = []
        if len(series) >= 2:
            events = self._complete(bel, series[-2], ob, now_s, cfg)
            # the window is modeled: its start's curve is spent (at most one
            # curve per series is ever kept — the newest observation's)
            series[-2]["curve"] = None
        if len(series) > MAX_OBS_PER_KIND:
            del series[:len(series) - MAX_OBS_PER_KIND]
        return events

    # ------------------------------------------------------------ windows

    def _complete(self, bel, a, b, now_s, cfg) -> list:
        kind = b["kind"]
        if a.get("feed_acres") and b.get("feed_acres"):
            A_a, A_b = a["feed_acres"] * ACRE_M2, b["feed_acres"] * ACRE_M2
        else:
            A_a, A_b = a["a"], b["a"]
        dt_h = (b["t"] - a["t"]) / H_S
        if not dt_h > 0.0:
            return []
        sda = _area_sd(A_a, cfg, bool(a.get("static")))
        sdb = _area_sd(A_b, cfg, bool(b.get("static")))
        ra, rb = _radius(A_a), _radius(A_b)
        w = {"kind": kind, "ta": a["t"], "tb": b["t"], "aa": A_a, "ab": A_b,
             "static": bool(b.get("static")),
             "vobs": (rb - ra) / dt_h,
             "sdv": math.hypot(_radius_sd(A_a, sda),
                               _radius_sd(A_b, sdb)) / dt_h,
             "vnom": None, "cens": False, "mnom": 1.0,
             "pred50": None, "pred90": None, "surprise": False}
        events = []
        model = model_rate(a.get("curve"), a["t"], b["t"])
        if model is not None:
            vnom, cens, mnom = model
            w["vnom"], w["cens"], w["mnom"] = vnom, cens, mnom
            if not cens:
                # the belief's forecast for this window, made BEFORE it joins
                wins = self._evidence(bel, now_s, cfg)
                post = None
                if wins and not self._reset_active(bel, now_s):
                    post = self._posterior_from(wins, cfg)
                if post is None:
                    post = Posterior(_prior_logp(cfg))
                v50, v90 = _predictive_rates(post, w, cfg)
                w["pred50"] = _growth_acres(ra, v50, dt_h, A_a)
                w["pred90"] = _growth_acres(ra, v90, dt_h, A_a)
                if w["vobs"] > v90:
                    w["surprise"] = True
                    until = now_s + float(cfg.fire_belief_reset_h) * H_S
                    bel.reset_until_s = max(bel.reset_until_s or until, until)
                    bel.floor_s = w["ta"]
                    grown = (A_b - A_a) / ACRE_M2
                    bel.reset_reason = (
                        f"{kind} growth {grown:,.0f} ac over {dt_h:.1f} h "
                        f"beat the belief's P90 prediction "
                        f"({w['pred90']:,.0f} ac)")
                    events.append({
                        "incident_id": bel.incident_id, "kind": "reset",
                        "message": (f"RESET to physics until "
                                    f"{_fmt_t(bel.reset_until_s)}: "
                                    f"{bel.reset_reason}")})
        bel.windows.append(w)
        if len(bel.windows) > MAX_WINDOWS:
            del bel.windows[:len(bel.windows) - MAX_WINDOWS]
        return events

    def _reset_active(self, bel, now_s) -> bool:
        return bel.reset_until_s is not None and now_s < bel.reset_until_s

    def _evidence(self, bel, now_s, cfg) -> list:
        """[(window, weight)] the posterior uses: modeled windows at or after
        the evidence floor, weight = 0.5^(age/half-life) / (1 + overlap with
        the other series), lighter than MIN_WINDOW_WEIGHT dropped."""
        hl = float(cfg.fire_belief_half_life_h) * H_S
        cand = [w for w in bel.windows if w.get("vnom") is not None
                and (bel.floor_s is None or w["ta"] >= bel.floor_s - 1e-6)]
        out = []
        for w in cand:
            decay = 0.5 ** (max(0.0, now_s - w["tb"]) / hl)
            other = [(v["ta"], v["tb"]) for v in cand if v["kind"] != w["kind"]]
            wt = decay / (1.0 + _covered_fraction(w["ta"], w["tb"], other))
            if wt >= MIN_WINDOW_WEIGHT:
                out.append((w, wt))
        return out

    def _posterior_from(self, wins, cfg) -> Posterior:
        logp = _prior_logp(cfg)
        for w, wt in wins:
            ll = _window_ll(w, cfg)
            for i in range(len(logp)):
                logp[i] += wt * ll[i]
        return Posterior(logp)

    def posterior(self, incident_id: str, now_s: float, cfg):
        """The posterior in effect (None = the prior: no evidence or a
        reset) — for tests and the measurement scripts."""
        with self._lock:
            bel = self._beliefs.get(incident_id)
            if bel is None or self._reset_active(bel, now_s):
                return None
            wins = self._evidence(bel, now_s, cfg)
            return self._posterior_from(wins, cfg) if wins else None

    # ------------------------------------------------------------ rapid

    def _rapid(self, bel, now_s, cfg) -> tuple:
        reasons = []
        if self._reset_active(bel, now_s):
            reasons.append(f"surprise reset until {_fmt_t(bel.reset_until_s)}"
                           f": {bel.reset_reason}")
        lb = now_s - float(cfg.fire_belief_rapid_lookback_h) * H_S
        per_day_thr = float(cfg.fire_belief_rapid_acres_per_day)
        # NOISE-AWARE: the growth that counts is its lower bound
        # grown - z * sd(grown) under the belief's own area-error model
        # (z = cfg.fire_belief_rapid_noise_z; 0 = the raw observed growth).
        # 3 % of a 20,000-ac fire is +-600 ac per report: raw 12-h windows
        # of a fire truly growing ~600 ac/day read 1,400-2,500 ac/day.
        z = float(getattr(cfg, "fire_belief_rapid_noise_z", 0.0) or 0.0)
        # v2 (cfg.fire_belief_rapid_noise == 'calibrated'): a PERIMETER
        # window's noise is the measured re-mapping noise (~sqrt of its
        # area), not 3 % of the area; reported windows keep the legacy sd
        calibrated = (getattr(cfg, "fire_belief_rapid_noise", "area_frac")
                      == "calibrated")
        for w in reversed(bel.windows):
            if w["tb"] < lb:
                continue
            grown = (w["ab"] - w["aa"]) / ACRE_M2
            if calibrated and w["kind"] == "perimeter":
                sd_g = perimeter_growth_sd_acres(w["aa"], w["ab"], cfg)
            else:
                sd_g = math.hypot(_area_sd(w["aa"], cfg, False),
                                  _area_sd(w["ab"], cfg,
                                           bool(w.get("static")))) / ACRE_M2
            lo = grown - z * sd_g
            days = (w["tb"] - w["ta"]) / 86400.0
            if not (days > 0.0 and lo > 0.0):
                continue
            per_day = lo / days
            a0 = w["aa"] / ACRE_M2
            beyond = (f", >= {lo:,.0f} beyond noise" if z > 0.0 else "")
            if per_day >= per_day_thr:
                reasons.append(f"{w['kind']} growth {grown:,.0f} ac in "
                               f"{days * 24.0:.1f} h{beyond} "
                               f"({per_day:,.0f} ac/24 h)")
            elif (lo >= float(cfg.fire_belief_rapid_min_acres) and a0 > 0.0
                  and lo / a0 / days
                  >= float(cfg.fire_belief_rapid_frac_per_day)):
                reasons.append(f"{w['kind']} growth {grown:,.0f} ac = "
                               f"{100.0 * grown / a0:.0f}% in "
                               f"{days * 24.0:.1f} h{beyond}")
        n_persist = int(getattr(cfg, "fire_belief_rapid_persist_windows", 0)
                        or 0)
        if n_persist >= 2:
            why = _persistent_growth(bel.windows, lb, n_persist, cfg)
            if why:
                reasons.append(why)
        if bel.discovered_s is not None and bel.latest_acres and \
                now_s - bel.discovered_s <= float(
                    cfg.fire_belief_rapid_lookback_h) * H_S:
            el = max(now_s - bel.discovered_s, YOUNG_FIRE_MIN_ELAPSED_S)
            lo = bel.latest_acres - z * _area_sd(
                bel.latest_acres * ACRE_M2, cfg, False) / ACRE_M2
            per_day = lo / (el / 86400.0)
            if per_day >= per_day_thr:
                reasons.append(f"young fire: {bel.latest_acres:,.0f} ac "
                               f"{el / 3600.0:.1f} h after discovery "
                               f"({per_day:,.0f} ac/24 h)")
        return bool(reasons), tuple(reasons)

    # ------------------------------------------------------------ weather

    def _log_weather(self, bel, wxf, now_s, cfg) -> None:
        try:
            s = wxf.at(now_s)
        except Exception:
            return
        t_h = math.floor(now_s / H_S) * H_S
        row = [t_h, round(_eff_wind(s, cfg), 3),
               round(s.wind_dir_deg % 360.0, 2), round(s.rh_pct, 2),
               round(s.temp_c, 2)]
        if bel.wx_log and bel.wx_log[-1][0] == t_h:
            bel.wx_log[-1] = row
        elif not bel.wx_log or t_h > bel.wx_log[-1][0]:
            bel.wx_log.append(row)
        if len(bel.wx_log) > WEATHER_LOG_MAX:
            del bel.wx_log[:len(bel.wx_log) - WEATHER_LOG_MAX]

    def _novelty(self, bel, wins, wxf, now_s, cfg) -> tuple:
        """(first NEW-pattern forecast hour, reason); horizon_hours(cfg) + 1
        = none within the horizon; 0 = the prior from the first hour."""
        if getattr(cfg, "fire_belief_novelty", "hourly") == "daily":
            return self._novelty_daily(bel, wins, wxf, now_s, cfg)
        H = horizon_hours(cfg)
        fuel = _fuel_model(bel.fuel_code)
        if fuel is None:
            return 0, "no burnable fuel on the domain to rate the weather"
        if not wins:
            return 0, "no growth evidence: every hour is new (physics)"
        ev = []
        for row in bel.wx_log:
            # the logged hour [t_h, t_h + 1 h) overlaps an evidence window
            wmax = 0.0
            for w, wt in wins:
                if row[0] < w["tb"] and row[0] + H_S > w["ta"] and wt > wmax:
                    wmax = wt
            if wmax > 0.0:
                ev.append((severity_mps(fuel, row[1], row[3], row[4], cfg),
                           row[1], row[2], wmax))
        if not ev:
            return 0, ("no logged weather inside the evidence windows: every "
                       "hour is new (physics)")
        ev_sorted = sorted(ev, key=lambda e: e[0])
        tot = sum(e[3] for e in ev_sorted)
        acc = 0.0
        s90 = ev_sorted[-1][0]
        for e in ev_sorted:
            acc += e[3]
            if acc >= 0.9 * tot - 1e-12:
                s90 = e[0]
                break
        sx = sum(e[1] * e[3] * math.sin(math.radians(e[2])) for e in ev)
        sy = sum(e[1] * e[3] * math.cos(math.radians(e[2])) for e in ev)
        sw = sum(e[1] * e[3] for e in ev)
        resultant = math.hypot(sx, sy) / sw if sw > 0.0 else 0.0
        dir_ev = math.degrees(math.atan2(sx, sy)) % 360.0
        ratio = float(cfg.fire_belief_novel_severity_ratio)
        shift = float(cfg.fire_belief_novel_shift_deg)
        wmin = float(cfg.fire_belief_novel_min_wind_mps)
        for h in range(H + 1):
            try:
                s = wxf.at(now_s + h * H_S)
            except Exception:
                return h, "forecast unavailable"
            ws = _eff_wind(s, cfg)
            sev = severity_mps(fuel, ws, s.rh_pct, s.temp_c, cfg)
            if sev > ratio * s90:
                why = (f"severity {sev / s90:.1f}x the evidence P90"
                       if s90 > 0.0 else "spreading weather where the "
                       "evidence weather could not carry fire")
                return h, why
            if ws >= wmin:
                if resultant < DIR_MIN_RESULTANT:
                    return h, (f"{ws:.1f} m/s wind where the evidence winds "
                               f"had no steady direction")
                d = _angdiff(s.wind_dir_deg, dir_ev)
                if d > shift:
                    return h, (f"wind turns {d:.0f} deg from the evidence "
                               f"direction ({dir_ev:.0f} deg) at "
                               f"{ws:.1f} m/s")
        return H + 1, ""

    def _novelty_daily(self, bel, wins, wxf, now_s, cfg) -> tuple:
        """The v2 DAILY new-weather test (cfg.fire_belief_novelty ==
        'daily'; module docstring "V2 RULES"): forecast days against
        evidence days, diurnal-aware. Same contract as _novelty."""
        H = horizon_hours(cfg)
        fuel = _fuel_model(bel.fuel_code)
        if fuel is None:
            return 0, "no burnable fuel on the domain to rate the weather"
        if not wins:
            return 0, "no growth evidence: every hour is new (physics)"
        loc = getattr(wxf, "loc", None)
        off_s = float(getattr(loc, "lon", 0.0) or 0.0) / 15.0 * H_S

        def day_of(t: float) -> int:
            """The fire's local SOLAR day (longitude / 15 h from UTC)."""
            return int(math.floor((t + off_s) / DAY_S))

        # evidence hours = the hourly rule's set (logged hours overlapping a
        # window, weight = the heaviest such window), grouped by day
        ev_days: dict = {}
        for row in bel.wx_log:
            wmax = 0.0
            for w, wt in wins:
                if row[0] < w["tb"] and row[0] + H_S > w["ta"] and wt > wmax:
                    wmax = wt
            if wmax > 0.0:
                d = ev_days.setdefault(day_of(row[0]), [[], 0.0])
                d[0].append((severity_mps(fuel, row[1], row[3], row[4], cfg),
                             row[1], row[2]))
                d[1] = max(d[1], wmax)
        if not ev_days:
            return 0, ("no logged weather inside the evidence windows: every "
                       "hour is new (physics)")
        ev = [(*_day_summary(hrs), wt) for hrs, wt in ev_days.values()]
        # the reference: the harshest evidence day (weighted P90 of peaks)
        ev_sorted = sorted(ev, key=lambda e: e[0])
        tot = sum(e[4] for e in ev_sorted)
        acc = 0.0
        s_ref = ev_sorted[-1][0]
        for e in ev_sorted:
            acc += e[4]
            if acc >= 0.9 * tot - 1e-12:
                s_ref = e[0]
                break
        ratio = float(cfg.fire_belief_novel_day_severity_ratio)
        shift = float(cfg.fire_belief_novel_day_shift_deg)
        wmin = float(cfg.fire_belief_novel_day_min_wind_mps)
        drive_floor = DAY_DRIVE_FRAC * s_ref
        # the sectors the evidence days' driving winds came from (days that
        # were calm, unsteady or too mild to drive the fire give none)
        sectors = [e[2] for e in ev if e[0] >= drive_floor and e[1] >= wmin
                   and e[3] >= DIR_MIN_RESULTANT]
        fc = []
        for h in range(H + 1):
            t = now_s + h * H_S
            try:
                s = wxf.at(t)
            except Exception:
                return h, "forecast unavailable"
            ws = _eff_wind(s, cfg)
            fc.append((h, day_of(t),
                       severity_mps(fuel, ws, s.rh_pct, s.temp_c, cfg), ws,
                       s.wind_dir_deg % 360.0))
        today = day_of(now_s)
        past_today = [(severity_mps(fuel, r[1], r[3], r[4], cfg), r[1], r[2])
                      for r in bel.wx_log
                      if day_of(r[0]) == today and r[0] + H_S <= now_s]
        days: list = []
        for r in fc:
            if not days or days[-1] != r[1]:
                days.append(r[1])
        for day in days:
            hrs = [r for r in fc if r[1] == day]
            summ = [(r[2], r[3], r[4]) for r in hrs]
            if day == today:
                summ = past_today + summ
            peak, spd, dirn, res = _day_summary(summ)
            thr = ratio * s_ref
            if peak > thr:
                first = next((r for r in hrs if r[2] > thr), hrs[0])
                why = (f"a day of severity {peak / s_ref:.1f}x the harshest "
                       f"evidence day" if s_ref > 0.0 else
                       "spreading weather where the evidence weather could "
                       "not carry fire")
                return first[0], why
            if peak < drive_floor or spd < wmin or res < DIR_MIN_RESULTANT:
                continue       # too mild, too calm or no steady driving wind
            if sectors:
                off = min(_angdiff(dirn, sec) for sec in sectors)
                if off <= shift:
                    continue
                why = (f"a day whose driving wind ({spd:.1f} m/s from "
                       f"{dirn:.0f} deg) is {off:.0f} deg from every evidence "
                       f"day's")
            else:
                why = (f"a day with a driving wind ({spd:.1f} m/s from "
                       f"{dirn:.0f} deg) where no evidence day had one")
            drv = [r for r in hrs if r[2] >= DAY_DRIVE_FRAC * peak] or hrs
            first = next((r for r in drv if r[3] >= wmin and (
                not sectors or min(_angdiff(r[4], sec) for sec in sectors)
                > shift)), drv[0])
            return first[0], why
        return H + 1, ""

    # ------------------------------------------------------------ spread

    def before_spread(self, inc, tile, wxf, now_s: float, cfg):
        """Called by the monitor for a DOMAIN incident right before its
        ensemble: logs the current weather hour, rates the forecast hours
        for a new pattern, and (cfg.fire_belief_apply only) returns the
        spread.ensemble_arrival member_hour_scale callable — None whenever
        the prior applies (state prior / reset, or hour 0 already new)."""
        now_s = float(now_s)
        with self._lock:
            if is_drill(inc):
                return None
            bel = self._beliefs.get(inc.incident_id)
            if bel is None:
                return None
            bel.pending_nominal = None
            bel.fuel_code = dominant_fuel(tile)
            self._log_weather(bel, wxf, now_s, cfg)
            wins = self._evidence(bel, now_s, cfg)
            h_new, why = self._novelty(bel, wins, wxf, now_s, cfg)
            H = horizon_hours(cfg)
            bel.novel_at_s = now_s
            bel.novel_from_s = None if h_new > H else now_s + h_new * H_S
            bel.novel_reason = why
            if not getattr(cfg, "fire_belief_apply", False) or not wins or \
                    self._reset_active(bel, now_s) or h_new == 0:
                return None
            post = self._posterior_from(wins, cfg)
            rapid, _r = self._rapid(bel, now_s, cfg)
            fn = member_scale_fn(post, h_new, H, cfg, rapid)
            bel.pending_nominal = fn(0, 0.5, 1.0)
            return fn

    def after_spread(self, inc, ens, now_s: float, cfg, anchor_s: float,
                     point: bool) -> None:
        """Keep the NOMINAL member's growth curve and pin it to the newest
        observation of each series that has none (seen <= PIN_MAX_LAG_S
        ago)."""
        now_s = float(now_s)
        with self._lock:
            if is_drill(inc):
                return
            bel = self._beliefs.get(inc.incident_id)
            if bel is None:
                return
            curve = growth_curve(ens, float(anchor_s), cfg, bool(point),
                                 bel.pending_nominal)
            bel.pending_nominal = None
            for kind in KINDS:
                series = bel.obs[kind]
                if series and series[-1].get("curve") is None and \
                        now_s - series[-1]["seen"] <= PIN_MAX_LAG_S:
                    series[-1]["curve"] = curve

    # ------------------------------------------------------------ status

    def statuses(self, incidents, now_s: float, cfg) -> dict:
        """{incident_id: FireStatus} for this cycle's active incidents, then
        prunes fires unseen for PRUNE_UNSEEN_S."""
        now_s = float(now_s)
        out = {}
        with self._lock:
            for inc in incidents:
                iid = inc.incident_id
                if is_drill(inc):
                    out[iid] = FireStatus(
                        incident_id=iid, known=False, rapid=False,
                        reasons=("seeded (drill) incident: never observed, "
                                 "physics applies",))
                    continue
                bel = self._beliefs.get(iid)
                if bel is None:
                    out[iid] = FireStatus(incident_id=iid, known=False,
                                          rapid=False,
                                          reasons=("no observations yet",))
                    continue
                out[iid] = self._status(bel, now_s, cfg)
            gone = [k for k, b in self._beliefs.items()
                    if now_s - b.last_seen_s > PRUNE_UNSEEN_S]
            for k in gone:
                del self._beliefs[k]
        return out

    def _status(self, bel, now_s, cfg) -> FireStatus:
        wins = self._evidence(bel, now_s, cfg)
        reset = self._reset_active(bel, now_s)
        post = self._posterior_from(wins, cfg) if wins and not reset else None
        state = "reset" if reset else ("learned" if post is not None
                                       else "prior")
        rapid, rreasons = self._rapid(bel, now_s, cfg)
        reasons = list(rreasons)
        p10 = p50 = p90 = None
        if post is not None:
            p10, p50, p90 = post.mult(0.1), post.mult(0.5), post.mult(0.9)
            reasons.append(f"spread rate learned from {len(wins)} growth "
                           f"window(s): {p50:.2f}x physics "
                           f"({p10:.2f}x-{p90:.2f}x)")
        elif not reset:
            reasons.append("no usable growth evidence yet: physics + model "
                           "error (the prior)")
        for kind in sorted(bel.hold_since):
            # SAFETY SEMANTICS 18e (cfg.feed_sanity only: nothing else
            # ever holds a drop)
            reasons.append(f"feed sanity: a {kind} size drop has been held "
                           f"since {_fmt_t(bel.hold_since[kind])} (it "
                           f"counts only if it persists)")
        novel = None
        if bel.novel_at_s == now_s:
            novel = bel.novel_from_s
            if state == "learned":
                if novel is None:
                    reasons.append("same weather pattern through the "
                                   "forecast horizon")
                elif novel <= now_s:
                    reasons.append(f"new weather pattern now "
                                   f"({bel.novel_reason}): physics applies")
                else:
                    reasons.append(f"same weather pattern until "
                                   f"{_fmt_t(novel)}, then new "
                                   f"({bel.novel_reason}): physics from then")
        elif state == "learned":
            reasons.append("no forecast consulted this cycle (no domain): "
                           "novel_from_s unknown")
        last = None
        if bel.windows:
            w = bel.windows[-1]
            last = FireObservation(
                t_s=w["tb"], acres=w["ab"] / ACRE_M2, kind=w["kind"],
                growth_acres=(w["ab"] - w["aa"]) / ACRE_M2,
                predicted_growth_acres=w.get("pred50"))
        return FireStatus(
            incident_id=bel.incident_id, known=bool(bel.windows),
            rapid=rapid, reasons=tuple(reasons), mult_p10=p10, mult_p50=p50,
            mult_p90=p90, n_obs=len(wins), novel_from_s=novel, state=state,
            last_obs=last)

    # ------------------------------------------------------------ JSON

    def to_json(self) -> dict:
        with self._lock:
            return {"schema": SCHEMA,
                    "incidents": {iid: b.to_json() for iid, b in
                                  sorted(self._beliefs.items())}}

    def load(self, d: dict) -> int:
        """Replace the contents from a to_json() dict; malformed incidents
        are skipped loudly. Returns the number restored."""
        if not isinstance(d, dict) or d.get("schema") != SCHEMA:
            raise ValueError(f"unknown fire-belief schema "
                             f"{d.get('schema') if isinstance(d, dict) else d!r}")
        beliefs = {}
        for iid, row in (d.get("incidents") or {}).items():
            try:
                beliefs[str(iid)] = IncidentBelief.from_json(iid, row)
            except Exception as exc:
                log.error("fire belief: skipping malformed incident %r (%s); "
                          "it restarts from the prior", iid, exc)
        with self._lock:
            self._beliefs = beliefs
        return len(beliefs)

    @classmethod
    def from_json(cls, d: dict, persistent: bool = True) -> "FireBeliefStore":
        store = cls(persistent=persistent)
        store.load(d)
        return store

    def copy(self) -> "FireBeliefStore":
        """A deep copy that never persists (persistent=False): the TEST /
        drill world's belief — it may learn, it can never write back."""
        return FireBeliefStore.from_json(
            json.loads(json.dumps(self.to_json())), persistent=False)


# ---------------------------------------------------------------------------
# Model-side helpers (module level: the measurement scripts use them too)
# ---------------------------------------------------------------------------

def _growth_acres(ra: float, v: float, dt_h: float, A_a: float) -> float:
    r = max(0.0, ra + v * dt_h)
    return (math.pi * r * r - A_a) / ACRE_M2


def growth_curve(ens, anchor_s: float, cfg, point: bool,
                 nominal_mult: Optional[list] = None) -> dict:
    """The nominal member's growth curve: G(s) (m^2) at s = 0, 1, 2 ... h
    after anchor_s up to the ensemble horizon (<= MAX_CURVE_STEPS), the
    offset (s) at which the nominal first reached the grid edge (None =
    never), whether it started from a point, and the nominal's per-hour
    multiplier list relative to ens.t0_s (None = 1.0)."""
    nom = ens.members[0]
    spec = nom.spec
    data = nom.data
    fin = sorted(v for v in data if v != UNREACHED and math.isfinite(v))
    cell_a = float(spec.cell_m) ** 2
    t_end = float(ens.t0_s) + float(cfg.horizon_h) * H_S
    steps = int(math.floor((t_end - anchor_s) / H_S)) + 1
    steps = max(2, min(MAX_CURVE_STEPS, steps))
    g = [bisect.bisect_right(fin, anchor_s + s * H_S) * cell_a
         for s in range(steps)]
    nr, nc = spec.nrows, spec.ncols
    edge = UNREACHED
    for c in range(nc):
        edge = min(edge, data[c], data[(nr - 1) * nc + c])
    for r in range(nr):
        edge = min(edge, data[r * nc], data[r * nc + nc - 1])
    sat = None if not math.isfinite(edge) else max(0.0, edge - anchor_s)
    return {"anchor": float(anchor_s), "t0": float(ens.t0_s), "g": g,
            "sat": sat, "point": bool(point),
            "mult": list(nominal_mult) if nominal_mult else None}


def _mean_mult(curve: dict, t_a: float, t_b: float) -> float:
    m = curve.get("mult")
    if not m:
        return 1.0
    t0 = curve["t0"]
    n = max(1, int(math.ceil((t_b - t_a) / H_S)))
    step = (t_b - t_a) / n
    tot = 0.0
    for j in range(n):
        h = int(math.floor((t_a + (j + 0.5) * step - t0) / H_S))
        h = 0 if h < 0 else (len(m) - 1 if h >= len(m) else h)
        tot += m[h]
    return tot / n


def model_rate(curve: Optional[dict], t_a: float, t_b: float):
    """(v_nom m/h, censored, m_nom) of window [t_a, t_b] on a pinned
    curve, or None when the curve cannot model it (absent, or spent —
    horizon end / domain edge — before the window starts)."""
    if not curve or not curve.get("g") or len(curve["g"]) < 2:
        return None
    g = curve["g"]
    anchor = curve["anchor"]
    s_lo = max(t_a - anchor, POINT_TRANSIENT_S if curve.get("point") else 0.0)
    s_hi = max(t_b - anchor, s_lo + MIN_MODEL_SPAN_S)
    end = (len(g) - 1) * H_S
    cut = end if curve.get("sat") is None else min(end, curve["sat"])
    if cut < s_lo + MIN_MODEL_SPAN_S:
        return None
    r_lo = _radius(_interp(g, s_lo))
    span_h = (s_hi - s_lo) / H_S
    if s_hi <= cut:
        v, cens = (_radius(_interp(g, s_hi)) - r_lo) / span_h, False
    else:
        v, cens = (_radius(_interp(g, cut)) - r_lo) / span_h, True
    mnom = _mean_mult(curve, t_a, t_b)
    return max(0.0, v), cens, (mnom if mnom > 0.0 else 1.0)


def member_scale_fn(post: Posterior, h_new: int, H: int, cfg, rapid: bool):
    """The spread.ensemble_arrival member_hour_scale callable for a learned
    posterior whose first NEW-pattern hour is h_new (H + 1 = none): member
    k's same-pattern multiplier, then its exact legacy multiplier."""
    sd0 = prior_sd(cfg)
    floor_sd = float(cfg.fire_belief_fast_sd_floor)
    mfloor = float(cfg.fire_belief_mult_floor)
    med = post.quantile(0.5)
    n_same = min(max(0, int(h_new)), H + 1)

    def fn(k: int, q: float, legacy: float):
        if k == 0:
            z, th = 0.0, med
        else:
            q = min(max(float(q), 1e-6), 1.0 - 1e-6)
            z = _ND.inv_cdf(q)
            th = post.quantile(q)
            if z > 0.0:
                th = max(th, med + floor_sd * z)
        m = math.exp(th)
        if rapid:
            m = max(m, math.exp(sd0 * z))
        m = max(m, mfloor)
        return [m] * n_same + [float(legacy)] * (H + 1 - n_same)

    return fn


def applied_ensemble(tile, seeds, wxf, now_s: float, cfg, incident_id: str,
                     perimeter, perimeter_at_s, member_hour_scale,
                     weather_field=None):
    """The ensemble of a fire whose belief is APPLIED (before_spread returned
    a member_hour_scale): every member runs the belief's per-hour
    multipliers, and — unless cfg.fire_belief_apply_p10 — the P10 raster is
    GUARDED: p10 = min(belief p10, physics p10), the physics ensemble being
    the exact legacy call. So the belief drives the nominal, P50 (the alert
    ladder) and P90 but never makes P10 (crew safety, corridors) later than
    today's engine; p10 <= p50 <= p90 is preserved (the min only lowers
    p10). Measured why (docs/FIRE_BELIEF.md §5): a slow fire's front is
    part-way across its next cells when a perimeter is drawn and every
    forecast restarts it there, so at a learned slow rate the first ring's
    P10 comes late — knowing the average rate does not shrink the front's
    position uncertainty the P10 must cover."""
    from wgresponse import spread
    from wgresponse.contracts import Raster
    # WEATHER ZONES (SAFETY SEMANTICS 17): both runs price per (zone, hour)
    # when the caller has a field; None = the one-forecast call, unchanged.
    zoned = ({"weather_field": weather_field}
             if weather_field is not None else {})
    ens = spread.ensemble_arrival(tile, seeds, wxf, now_s, cfg,
                                  incident_id=incident_id,
                                  perimeter=perimeter,
                                  perimeter_at_s=perimeter_at_s,
                                  member_hour_scale=member_hour_scale,
                                  **zoned)
    if getattr(cfg, "fire_belief_apply_p10", False):
        return ens
    phys = spread.ensemble_arrival(tile, seeds, wxf, now_s, cfg,
                                   incident_id=incident_id,
                                   perimeter=perimeter,
                                   perimeter_at_s=perimeter_at_s, **zoned)
    ens.p10 = Raster(ens.spec, [a if a <= b else b for a, b in
                                zip(ens.p10.data, phys.p10.data)])
    return ens


def status_row(st: FireStatus) -> dict:
    """The JSON row the world summary / /api/state carry for one fire."""
    def r(v, nd=3):
        return None if v is None else round(float(v), nd)
    lo = st.last_obs
    return {
        "state": st.state, "n_obs": st.n_obs,
        "mult_p10": r(st.mult_p10), "mult_p50": r(st.mult_p50),
        "mult_p90": r(st.mult_p90),
        "last_obs": None if lo is None else {
            "t_s": lo.t_s, "acres": r(lo.acres, 1), "kind": lo.kind,
            "growth_acres": r(lo.growth_acres, 1),
            "predicted_growth_acres": r(lo.predicted_growth_acres, 1)},
        "novel_from_s": st.novel_from_s, "rapid": st.rapid,
        "known": st.known, "reasons": list(st.reasons),
    }


# ---------------------------------------------------------------------------
# Persistence: its OWN file beside the state file (+ the S3 mirror)
# ---------------------------------------------------------------------------

class BeliefFile:
    """<state-dir>/fire_beliefs.json, mirrored to s3://<bucket>/<prefix>/
    fire_beliefs.json when s3_uri is given (persist.S3Mirror). save() and
    load_into() NEVER raise: a failure is a loud log and the belief simply
    starts from the prior (the safe direction). A store with
    persistent=False (a drill copy) is never written."""

    def __init__(self, dir_path, s3_uri: Optional[str] = None,
                 s3_client: Optional[object] = None) -> None:
        self._dir = Path(dir_path)
        self._path = self._dir / BELIEF_FILENAME
        self._mirror = None
        self._key = None
        if s3_uri:
            from wgresponse.persist import S3Mirror
            self._mirror = S3Mirror(s3_uri, client=s3_client)
            self._key = self._mirror.key_for(BELIEF_FILENAME)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def s3_key(self) -> Optional[str]:
        return self._key

    def save(self, store: FireBeliefStore, now_s: Optional[float] = None) -> bool:
        if not getattr(store, "persistent", True):
            return False
        try:
            payload = store.to_json()
            payload["saved_at_s"] = now_s
            body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = Path(str(self._path) + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
            if self._mirror is not None:
                self._mirror.upload(self._key, body.encode("utf-8"),
                                    now_s=now_s)
            return True
        except Exception:
            log.exception("fire belief: save to %s FAILED (beliefs kept in "
                          "memory; a restart would start from the prior)",
                          self._path)
            return False

    def load_into(self, store: FireBeliefStore) -> bool:
        try:
            raw = None
            if self._path.exists():
                raw = self._path.read_text(encoding="utf-8")
            elif self._mirror is not None:
                body = self._mirror.download(self._key)
                if body is not None:
                    raw = body.decode("utf-8")
            if raw is None:
                log.info("fire belief: no saved beliefs at %s — every fire "
                         "starts from the prior", self._path)
                return False
            n = store.load(json.loads(raw))
            log.info("fire belief: restored %d fire(s) from %s", n,
                     self._path if self._path.exists() else self._key)
            return True
        except Exception:
            log.exception("fire belief: restore FAILED — every fire starts "
                          "from the prior (the safe direction)")
            return False
