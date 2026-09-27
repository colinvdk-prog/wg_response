"""wgresponse.offmap — the per-fire OFF-DOMAIN HORIZON (contracts SAFETY
SEMANTICS 15; docs/LIVE_WORLD_PLAN.md ticket 1.5, "The off-domain horizon is
per fire (2026-09-27)").

Founder rule, 2026-09-27 (verbatim): "the cap at extrapolation should include
the rate of fire spread vs. drive time through the region. the pathway only
becomes unaccessible when the crossing time between fire and road is less
than 1 hour unless the fire has shown rapid rate of spread"

What it changes. Under --fail-closed (contracts corridor_fail_closed /
corridor_unreached_bound) a corridor sample OFF a fire's forecast raster, or
UNREACHED inside it, gets the arrival

    min over the fire's reached boundary runs of  t_run + distance / speed

(safety._bound_arrival_s) and is unsafe when that arrival minus the crew's
pass time — the crossing time between fire and road against the drive — is
below the leg's margin. Legacy: ONE speed for every fire, the 1.7 m/s
(~6 km/h) plausible-maximum ceiling. Under cfg.corridor_offraster_per_fire
this module gives each fire with a domain its own (speed, margin):

  STATUS (status_for; the feed, today WFIGS incident points): KNOWN = a
      behaviour report (FireBehaviorGeneral + FireBehaviorGeneral1-3, meta
      fire_behavior / fire_behavior_descriptors / fire_behavior_at_s) in the
      recognized IRWIN vocabulary, no older than cfg.fire_behavior_max_age_h
      by its record's ModifiedOnDateTime. RAPID = a known report whose
      general behaviour is Very Active / Extreme or with any descriptor
      Running / Wind Driven Runs / Uphill Runs / Crowning / Long-range
      Spotting. Every contradiction resolves toward rapid (a 100 %-contained
      fire still reported Extreme; "Minimal" beside "Running"); a term the
      vocabulary does not know never vouches for a slow fire.
  SPEED (horizon_for): rapid or unknown -> the ceiling (unknown defaults to
      rapid: a new or unreported fire — every seeded fire — has not shown it
      is slow). Known and not rapid -> clamp(front, floor, ceiling) with
      front = the P90 (nearest rank) of the outward front speed over the
      fire's RAW (un-eroded) P10 raster's reached boundary cells,
      cell_m / (arrival(boundary cell) - arrival(inward neighbour)),
      counting only cells whose inward neighbour burned earlier: the
      forecast's own speed where the fire crosses its domain edge.
      Measured against max in the plan section: a single spotting teleport
      or an edge-parallel (oblique) front makes one cell absurdly fast; P90
      needs 10 % of the crossing cells to agree, and the ceiling still clamps
      what is left. No crossing cell -> the ceiling ('no_front').
  MARGIN: every such fire's BOUNDED samples clear max(leg margin,
      cfg.corridor_offraster_margin_min) — the founder's hour (watch
      --offmap-per-fire sets 60). Modelled samples keep their margins.

The table rides CycleResult.offmap and dispatch.plan(offmap=...) as
contracts.OffDomainHorizon rows; one cycle note per fire says what it got.

STATUS SEAM (for the per-fire spread belief built in parallel, contracts
FireStatus): horizons(..., status={incident_id: other_status}) OR-combines
another status with the feed's through merge_status — rapid if any says
rapid, known if any knows. A status is a (known, rapid, reasons) tuple or
any object with .known / .rapid / .reasons.

Pure and read-only: nothing here mutates an incident, an ensemble or the
config. Units: m/s (km/h only in human text), minutes, unix seconds.
"""
from __future__ import annotations

import math
from typing import Optional

from wgresponse.contracts import OffDomainHorizon

#: IRWIN FireBehaviorGeneral values (normalized: lower case, '-'/'_' -> ' ').
GENERAL_KNOWN = frozenset({"minimal", "moderate", "active", "very active",
                           "extreme"})
GENERAL_RAPID = frozenset({"very active", "extreme"})
#: FireBehaviorGeneral1-3 descriptors: every value the live WFIGS layer
#: carried on 2026-09-27 (283 CONUS wildfire points) plus the brief's rapid
#: set. A descriptor outside this set makes the report UNTRUSTED as slow.
DESCRIPTORS_KNOWN = frozenset({
    "smoldering", "creeping", "backing", "flanking", "single tree torching",
    "isolated torching", "group torching", "torching", "spotting",
    "running", "uphill runs", "wind driven runs", "crowning",
    "long range spotting"})
DESCRIPTORS_RAPID = frozenset({"running", "wind driven runs", "uphill runs",
                               "crowning", "long range spotting"})

_KMH = 3.6


def _norm(term: str) -> str:
    return " ".join(str(term).replace("-", " ").replace("_", " ").lower().split())


def _finite(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def behavior_report(inc) -> "tuple[str, list, Optional[float]]":
    """(general, [descriptors], at_s) from an incident's meta (SAFETY
    SEMANTICS 15b keys; '' / [] / None when absent or malformed)."""
    meta = getattr(inc, "meta", None) or {}
    gen = meta.get("fire_behavior")
    gen = gen.strip() if isinstance(gen, str) else ""
    raw = meta.get("fire_behavior_descriptors")
    descs = ([d.strip() for d in raw if isinstance(d, str) and d.strip()]
             if isinstance(raw, (list, tuple)) else [])
    at = _finite(meta.get("fire_behavior_at_s"))
    return gen, descs, (at if at is not None and at > 0 else None)


def behavior_label(gen: str, descs: list) -> str:
    """'Minimal | Smoldering, Creeping, Smoldering' (the report as the feed
    phrases it; '-' for an empty general)."""
    return (gen or "-") + (" | " + ", ".join(descs) if descs else "")


def status_for(inc, now_s: float, cfg) -> "tuple[bool, bool, tuple]":
    """(known, rapid, reasons) — the FEED's behaviour status of one incident
    (SAFETY SEMANTICS 15b). Never raises on a malformed record: anything it
    cannot read is 'unknown' (the ceiling applies)."""
    gen, descs, at = behavior_report(inc)
    if not gen and not descs:
        return (False, False, ("no fire-behaviour report in the feed",))
    label = behavior_label(gen, descs)
    if at is None:
        return (False, False, (f"behaviour '{label}' carries no report time",))
    age_h = max(0.0, float(now_s) - at) / 3600.0
    max_age = float(cfg.fire_behavior_max_age_h)
    if age_h > max_age:
        return (False, False, (f"behaviour '{label}' is {age_h:.1f} h old "
                               f"(stale beyond {max_age:g} h)",))
    g = _norm(gen) if gen else ""
    rapid_terms = ([gen] if g in GENERAL_RAPID else []) + [
        d for d in descs if _norm(d) in DESCRIPTORS_RAPID]
    unknown_terms = ([gen] if g and g not in GENERAL_KNOWN else []) + [
        d for d in descs if _norm(d) not in DESCRIPTORS_KNOWN]
    if rapid_terms:
        reasons = [f"reported {', '.join(dict.fromkeys(rapid_terms))} "
                   f"('{label}', {age_h:.1f} h ago)"]
        contained = _finite(getattr(inc, "containment_pct", None))
        if contained is not None and contained >= 100.0:
            reasons.append(f"{contained:g}% contained yet reported rapid: "
                           f"the contradiction resolves toward rapid")
        if g in GENERAL_KNOWN and g not in GENERAL_RAPID:
            reasons.append(f"general '{gen}' contradicts a rapid descriptor: "
                           f"resolved toward rapid")
        return (True, True, tuple(reasons))
    if unknown_terms:
        return (False, False, (
            f"behaviour '{label}' ({age_h:.1f} h ago) has term(s) outside the "
            f"known vocabulary ({', '.join(repr(t) for t in unknown_terms)}): "
            f"not trusted as slow",))
    return (True, False, (f"reported '{label}' {age_h:.1f} h ago",))


def _status_parts(st) -> "tuple[bool, bool, tuple]":
    if hasattr(st, "known") or hasattr(st, "rapid"):
        k = bool(getattr(st, "known", False))
        r = bool(getattr(st, "rapid", False))
        rs = getattr(st, "reasons", ()) or ()
    else:
        k, r = bool(st[0]), bool(st[1])
        rs = st[2] if len(st) > 2 else ()
    if isinstance(rs, str):
        rs = (rs,)
    return k, r, tuple(str(x) for x in rs)


def merge_status(*statuses) -> "tuple[bool, bool, tuple]":
    """OR-combine behaviour statuses (the integrator seam, SAFETY SEMANTICS
    15b): known if ANY knows, rapid if ANY says rapid, reasons concatenated
    in argument order. Each status is a (known, rapid[, reasons]) tuple or
    an object with .known / .rapid / .reasons (contracts FireStatus); None
    entries are skipped. No status at all = unknown."""
    known = rapid = False
    reasons: list = []
    for st in statuses:
        if st is None:
            continue
        k, r, rs = _status_parts(st)
        known = known or k
        rapid = rapid or r
        reasons.extend(rs)
    return known, rapid, tuple(reasons)


def feed_statuses(incidents, now_s: float, cfg) -> dict:
    """{incident_id: status_for(...)} for every incident (the dict seam)."""
    return {inc.incident_id: status_for(inc, now_s, cfg) for inc in incidents}


# ------------------------------------------------------------ front speed

def front_speeds_mps(raster) -> list:
    """Outward front speed at every reached boundary cell of `raster` (a RAW
    arrival raster, unix s) whose inward neighbour burned EARLIER:
    cell_m / (arrival(boundary) - arrival(inward neighbour)), m/s. Each
    edge is walked once (corner cells therefore count once per edge they
    sit on). Empty when the raster is under 2x2 or nothing crosses."""
    spec = raster.spec
    nr, nc = spec.nrows, spec.ncols
    if nr < 2 or nc < 2:
        return []
    d = raster.data
    cell = float(spec.cell_m)
    isfin = math.isfinite
    out = []

    def _pair(b, n):
        tb = d[b]
        tn = d[n]
        if isfin(tb) and isfin(tn) and tn < tb:
            out.append(cell / (tb - tn))

    last = (nr - 1) * nc
    for c in range(nc):
        _pair(c, nc + c)                          # north edge
        _pair(last + c, last - nc + c)            # south edge
    for r in range(nr):
        base = r * nc
        _pair(base, base + 1)                     # west edge
        _pair(base + nc - 1, base + nc - 2)       # east edge
    return out


def percentile_nearest_rank(values, q: float) -> Optional[float]:
    """Nearest-rank percentile (q in (0, 1]): the ceil(q n)-th smallest."""
    s = sorted(values)
    if not s:
        return None
    k = max(1, int(math.ceil(q * len(s))))
    return s[k - 1]


def front_speed_stats(raster) -> "tuple[Optional[float], Optional[float], int]":
    """(P90, max, n) of front_speeds_mps(raster); (None, None, 0) when no
    boundary cell crosses."""
    s = front_speeds_mps(raster)
    if not s:
        return None, None, 0
    return percentile_nearest_rank(s, 0.9), max(s), len(s)


def _band_cells(spec, cfg) -> int:
    """Edge band (cells) inside which a finite RAW arrival can put a finite
    value on the ERODED raster's boundary: the widest corridor erosion
    radius in play, in cells, plus one (a superset of every erosion shape)."""
    r = float(getattr(cfg, "corridor_erosion_m", 0.0) or 0.0)
    if getattr(cfg, "corridor_erosion_tiered", False):
        r = max(r, float(cfg.corridor_erosion_fast_m),
                float(cfg.corridor_erosion_shrub_m),
                float(cfg.corridor_erosion_slow_m))
    return int(math.ceil(max(0.0, r) / float(spec.cell_m))) + 1


def edge_band_reached(raster, band: int) -> bool:
    """True when any cell within `band` cells of the grid edge has a finite
    arrival — else the eroded raster reaches no boundary cell and the fail-
    closed law extrapolates nothing from this fire."""
    spec = raster.spec
    nr, nc = spec.nrows, spec.ncols
    d = raster.data
    isfin = math.isfinite
    b = max(1, int(band))
    rows = set(range(min(b, nr))) | set(range(max(0, nr - b), nr))
    for r in rows:
        base = r * nc
        for c in range(nc):
            if isfin(d[base + c]):
                return True
    cols = sorted(set(range(min(b, nc))) | set(range(max(0, nc - b), nc)))
    for r in range(nr):
        base = r * nc
        for c in cols:
            if isfin(d[base + c]):
                return True
    return False


def _kmh(v_mps: float) -> str:
    return f"{v_mps * _KMH:.1f}"


def horizon_for(incident_id: str, p10, status, cfg) -> OffDomainHorizon:
    """The OffDomainHorizon of one fire from its RAW P10 raster and its
    (merged) status (SAFETY SEMANTICS 15a/c; module docstring)."""
    known, rapid, reasons = merge_status(status)
    ceiling = float(cfg.corridor_offraster_ros_mps)
    floor = min(float(cfg.corridor_offraster_ros_floor_mps), ceiling)
    margin = float(cfg.corridor_offraster_margin_min)
    p90, mx, n = front_speed_stats(p10)
    if not edge_band_reached(p10, _band_cells(p10.spec, cfg)):
        kind, ros = "no_bound", ceiling
        reason = ("no bound: the forecast stays inside its domain "
                  "(nothing is extrapolated)")
    elif rapid:
        kind, ros, reason = "rapid", ceiling, "ceiling: rapid"
    elif not known:
        kind, ros, reason = "unknown", ceiling, "ceiling: unknown"
    elif p90 is None:
        kind, ros = "no_front", ceiling
        reason = "ceiling: no front crosses the domain edge to measure"
    elif p90 < floor:
        kind, ros = "floor", floor
        reason = (f"floor: P90 front speed {_kmh(p90)} km/h is under the "
                  f"{_kmh(floor)} km/h floor")
    elif p90 > ceiling:
        kind, ros = "ceiling_front", ceiling
        reason = (f"ceiling: P90 front speed {_kmh(p90)} km/h is over the "
                  f"{_kmh(ceiling)} km/h ceiling")
    else:
        kind, ros = "per_fire", p90
        reason = f"per-fire: P90 front speed {_kmh(p90)} km/h"
    return OffDomainHorizon(incident_id=incident_id, ros_mps=float(ros),
                            margin_min=margin, reason=reason, kind=kind,
                            known=bool(known), rapid=bool(rapid),
                            status_reasons=tuple(reasons),
                            front_p90_mps=p90, front_max_mps=mx, front_n=n)


def horizons(incidents, ensembles: dict, now_s: float, cfg,
             status: "Optional[dict]" = None) -> dict:
    """{incident_id: OffDomainHorizon} for every incident with an ensemble
    (a domain) this cycle, in incident-id order. The feed's status_for is
    OR-combined with status.get(incident_id) when given (the per-fire
    spread belief seam; module docstring)."""
    by_id = {inc.incident_id: inc for inc in incidents}
    extra = status or {}
    out = {}
    for iid in sorted(ensembles):
        inc = by_id.get(iid)
        feed = (status_for(inc, now_s, cfg) if inc is not None else
                (False, False, ("no incident record this cycle",)))
        out[iid] = horizon_for(iid, ensembles[iid].p10,
                               merge_status(feed, extra.get(iid)), cfg)
    return out


def margin_text(h: OffDomainHorizon) -> str:
    return (f"bounded samples clear max(leg margin, {h.margin_min:g} min)"
            if h.margin_min > 0 else "bounded samples clear the leg margin")


def horizon_note(h: OffDomainHorizon) -> str:
    """The cycle note for one fire ("incident <id>: ..." — the world
    summary's per-incident note format)."""
    if h.kind == "no_bound":
        return (f"incident {h.incident_id}: off-domain horizon — {h.reason} "
                f"(SAFETY SEMANTICS 15)")
    why = "; ".join(h.status_reasons) or "no status"
    return (f"incident {h.incident_id}: off-domain horizon "
            f"{_kmh(h.ros_mps)} km/h ({h.reason}); {margin_text(h)} — "
            f"behaviour: {why} (SAFETY SEMANTICS 15)")


def horizon_row(h: OffDomainHorizon) -> dict:
    """JSON-able row for /api/state and /api/world/summary."""
    def kmh(v):
        return None if v is None else round(v * _KMH, 2)
    return {"ros_mps": round(h.ros_mps, 4), "ros_kmh": kmh(h.ros_mps),
            "kind": h.kind, "reason": h.reason, "margin_min": h.margin_min,
            "margin": margin_text(h), "known": h.known, "rapid": h.rapid,
            "status": list(h.status_reasons),
            "front_p90_kmh": kmh(h.front_p90_mps),
            "front_max_kmh": kmh(h.front_max_mps),
            "front_cells": h.front_n}


def rule_summary(cfg) -> dict:
    """The armed rule's parameters (the /api/state 'offmap_rule' block)."""
    return {"ceiling_mps": float(cfg.corridor_offraster_ros_mps),
            "floor_mps": float(cfg.corridor_offraster_ros_floor_mps),
            "margin_min": float(cfg.corridor_offraster_margin_min),
            "fire_behavior_max_age_h": float(cfg.fire_behavior_max_age_h),
            "statistic": "P90 (nearest rank) of the raw-P10 boundary front "
                         "speed",
            "semantics": "SAFETY SEMANTICS 15"}
