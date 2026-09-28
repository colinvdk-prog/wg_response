"""wgresponse.feedsanity — plausibility checks on the live feeds' perimeters
and sizes before they teach the per-fire belief or seed the spread
(contracts SAFETY SEMANTICS 18; cfg.feed_sanity, default OFF; watch
--feed-sanity; the audit and its measurement:
docs/review/feed_sanity_2026-09-27.md).

What the audit found on the live path (feeds.py -> fusion -> the monitor's
staleness gate -> fire_belief.observe -> the perimeter seed):

  collapse    a fire's record carried a polygon under 10 % of its own size
              (0-8,183 ac while it reported 10,524-156,699 ac; WFIGS Daily
              history: 12 of 918 edits on 6 of 11 fires, lasting minutes to
              ~2 days), each under a NEWER poly_DateCurrent — the exact field
              feeds.py stamps perimeter_at_s from. The belief read a -99.99 %
              growth window, then a +150,000 ac jump (rapid, surprise reset);
              the seed spread a 2 ac fire where 150,000 ac burn — every
              arrival near the real front far too LATE.
  re-stamp    WFIGS re-issues an unchanged polygon under a newer
              poly_DateCurrent (median 90 h, up to 58 days after its
              poly_PolygonDateTime): 306 of the 674 perimeter windows the
              belief would have formed on those fires were zero-growth
              re-stamps, and the deployed watcher's first window was one
              (Little Giant, 172,891 ac, 9 h apart) — each makes a fire
              'known' and pulls it slow.
  area figure 23 of 560 live polygons carry an area figure (poly_GISAcres /
              FIRIS area_acres) more than 2x off their own geometry
              ('Auto-generated for InFORM' polygons keep their first size:
              1 ac on 2,850 ac of rings); the belief prefers that figure.
  drops       reported sizes reset (Hagen 15,798 -> 700 ac) and fused sources
              disagree (JUNIPER 0.01 vs 73 ac, the fused size following
              whichever record is fresher).
  time        no implausible own time on the fields the adapters read today
              (the Daily layer's 2002 / 2020 stamps were poly_PolygonDateTime,
              which no adapter requests); but a future stamp is clamped to
              now and so re-observed as a new capture every spacing interval,
              and an ancient one would burn the whole domain before now —
              defense in depth.

This module is PURE: numbers in, verdicts out (no feed, no raster, no
clock). fire_belief applies the belief side, threat.ThreatMonitor the seed
side, both only under cfg.feed_sanity. Units: unix seconds, acres, m^2.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional

ACRE_M2 = 4046.8564224
H_S = 3600.0
#: an own time this far before the fire's discovery is still plausible
#: (discovery times are entered by hand, often rounded or late)
PRE_DISCOVERY_SLACK_S = 24.0 * H_S
#: with no discovery time, an own time older than this is implausible (the
#: current layers hold active fires; SEMANTICS 9 gates records a week old)
MAX_AGE_S = 365.0 * 86400.0


def _num(v) -> Optional[float]:
    """A finite real number or None (bools and junk are None)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _fmt_t(t_s: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(float(t_s)))
    except (OverflowError, OSError, ValueError):
        return f"{t_s:.0f} s"


def time_problem(t_s, discovered_s, now_s: float, cfg) -> Optional[str]:
    """Why an own time cannot be what the feed meant (SEMANTICS 18b), or None
    when it is plausible: later than now + cfg.feed_sanity_future_h, or
    earlier than the fire's discovery less PRE_DISCOVERY_SLACK_S (MAX_AGE_S
    before now when discovery is unknown or itself implausible)."""
    t = _num(t_s)
    if t is None:
        return None                      # absent: the callers' legacy fallbacks
    now_s = float(now_s)
    ahead = t - now_s
    if ahead > float(cfg.feed_sanity_future_h) * H_S:
        return (f"its time {_fmt_t(t)} is {ahead / H_S:,.1f} h in the "
                f"future")
    disc = _num(discovered_s)
    if disc is not None and 0.0 < disc <= now_s:
        floor = max(now_s - MAX_AGE_S, disc - PRE_DISCOVERY_SLACK_S)
        if t < floor:
            return (f"its time {_fmt_t(t)} is before the fire was "
                    f"discovered ({_fmt_t(disc)})")
    elif t < now_s - MAX_AGE_S:
        return f"its time {_fmt_t(t)} is more than a year old"
    return None


def feed_area_stands(feed_acres, geom_acres, cfg) -> bool:
    """True when the feed's own area figure agrees with the polygon's
    geometry within a factor of cfg.feed_sanity_area_ratio (SEMANTICS 18a).
    A missing or non-positive figure never stands."""
    fa = _num(feed_acres)
    ga = _num(geom_acres)
    if fa is None or ga is None or not fa > 0.0 or not ga > 0.0:
        return False
    k = float(cfg.feed_sanity_area_ratio)
    r = fa / ga
    return 1.0 / k <= r <= k


def collapse_problem(area_acres, reported_acres, cfg) -> Optional[str]:
    """Why a polygon of `area_acres` is not the perimeter of a fire whose own
    record reports `reported_acres` (SEMANTICS 18c), or None: the polygon
    covers under cfg.feed_sanity_collapse_frac of a reported size of at
    least cfg.feed_sanity_min_acres."""
    rep = _num(reported_acres)
    area = _num(area_acres)
    if rep is None or area is None or rep < float(cfg.feed_sanity_min_acres):
        return None
    frac = float(cfg.feed_sanity_collapse_frac)
    if area < frac * rep:
        return (f"its {area:,.1f} ac are under {frac:.0%} of the "
                f"{rep:,.0f} ac its record reports")
    return None


def area_sd_m2(area_m2: float, cfg) -> float:
    """The belief's own area measurement error (sd, m^2): the relative part
    cfg.fire_belief_area_rel_err and the absolute part
    cfg.fire_belief_area_abs_err_acres, in quadrature (fire_belief._area_sd,
    non-static)."""
    return math.hypot(float(cfg.fire_belief_area_rel_err) * max(0.0, area_m2),
                      float(cfg.fire_belief_area_abs_err_acres) * ACRE_M2)


def is_drop(new_m2: float, ref_m2: float, cfg) -> bool:
    """True when `new_m2` is below `ref_m2` by more than
    cfg.feed_sanity_drop_z x the sd of the difference (SEMANTICS 18c/e):
    beyond measurement noise, so not a re-measurement."""
    sd = math.hypot(area_sd_m2(new_m2, cfg), area_sd_m2(ref_m2, cfg))
    return float(ref_m2) - float(new_m2) > float(cfg.feed_sanity_drop_z) * sd


def is_restamp(new_m2: float, ref_m2: float, cfg) -> bool:
    """True when `new_m2` is within cfg.feed_sanity_restamp_frac of `ref_m2`
    (SEMANTICS 18d): the same polygon re-issued, not a new capture."""
    ref = float(ref_m2)
    return ref > 0.0 and \
        abs(float(new_m2) - ref) <= float(cfg.feed_sanity_restamp_frac) * ref


# ---------------------------------------------------------------------------
# The seed side's memory (threat.ThreatMonitor, in-process)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RememberedPerimeter:
    """A fire's last plausible polygon as the monitor seeded it: the rings,
    the polygon's own time (after any 18b fallback), its meta
    perimeter_acres (None when the feed had none), its geometry's area and
    when it was last seen (unix s)."""
    rings: list
    perimeter_at_s: float
    perimeter_acres: Optional[float]
    geom_acres: float
    seen_s: float


class PerimeterMemory:
    """{incident_id: RememberedPerimeter} — the newest polygon of each fire
    that passed the SEMANTICS 18 checks. In-process by design (a restart
    forgets it; contracts 18f)."""

    def __init__(self) -> None:
        self._by_id: dict = {}

    def __len__(self) -> int:
        return len(self._by_id)

    def get(self, incident_id: str, now_s: float,
            cfg) -> Optional[RememberedPerimeter]:
        """The remembered polygon, or None when there is none or it was last
        seen more than cfg.feed_sanity_memory_h ago."""
        rec = self._by_id.get(incident_id)
        if rec is None:
            return None
        if float(now_s) - rec.seen_s > float(cfg.feed_sanity_memory_h) * H_S:
            return None
        return rec

    def remember(self, incident_id: str, rec: RememberedPerimeter) -> None:
        self._by_id[incident_id] = rec

    def prune(self, now_s: float, cfg) -> None:
        """Forget polygons last seen more than cfg.feed_sanity_memory_h ago."""
        horizon = float(cfg.feed_sanity_memory_h) * H_S
        for iid in [k for k, r in self._by_id.items()
                    if float(now_s) - r.seen_s > horizon]:
            del self._by_id[iid]
