"""wgresponse.worldview — the W6 thin world viewer's read model.

docs/LIVE_WORLD_PLAN.md section 3, step 1: "a thin W6 over the existing
watcher — set its AOI to CONUS and put a zoomable map over /api/state"
(W6 thin viewer, 2026-09-26). The page is console/world.html, served at
/world by the watch daemon. It draws everything /api/state already carries
(incidents, clients with their worst alert state and best ETA, crews, safe
zones, the dispatch plan, the event feed, the cycle clock). It needs four
things /api/state does not carry; GET /api/world/summary (authed,
read-only; cli._WatchDaemon.world_summary) supplies them, computed ONCE
per completed cycle from the daemon's last CycleResult:

  1. DOMAINS — which incidents built a spread domain this cycle (the keys
     of CycleResult.ensembles: threat._assess_incident spreads ONLY
     incidents with protected clients inside the threat radius), each
     domain's client count, earliest client P10 / P50 arrival over this
     cycle's assessments, grid size and phase times (CycleResult.timings).
  2. WHY NO DOMAIN — the cycle's own per-incident note lines ("incident
     <id>: no protected clients within 60 km; spread skipped", an
     assessment failure, a registry-roster alarm), matched by incident id.
  3. GEOMETRY for the regional tier — each incident's feed perimeter
     SIMPLIFIED FOR DISPLAY (largest rings first, Douglas-Peucker, a vertex
     and ring cap — the rings the engine spreads from are untouched) and,
     for domain incidents, the P50 isochrone rings at the cycle time +
     P50_LEADS_H traced from the ensemble's p50 raster with the simulator's
     own tracer (simui._pool_min + simui._threshold_rings: min-pooled to
     <= 120 cells a side, boundary-following rings).
  4. COUNTS and the RANKED list "incidents threatening clients": domain
     incidents first by earliest client P10 arrival (a domain whose clients
     are all unreached within the horizon after every domain with a finite
     arrival), then every other incident by acreage, largest first (no
     acreage last); ties by incident id — deterministic.

summary_from_state(state) builds the same shape from a bare /api/state
dict (no domains, no geometry) so the page also works over `serve --demo`
or any state provider without a watch daemon behind it.

Read-only by construction: nothing here mutates the CycleResult, the alert
engine, the registry or the physics — pure functions over their outputs.
Units: times unix seconds `_s`, hours `_h`, distances meters, acreage acres.
"""
from __future__ import annotations

import math
from typing import Optional

SCHEMA = "wgresponse-world-summary/1"

#: Zoom tiers (the page's law, echoed in every summary): national <= 6,
#: regional 7..11, local >= 12 (Leaflet / web-mercator zoom levels).
TIER_NATIONAL_MAX_ZOOM = 6
TIER_REGIONAL_MAX_ZOOM = 11

#: P50 isochrone leads (hours after the cycle time). 0 = the modeled burn at
#: the cycle time (perimeter interior + growth since the perimeter's own
#: timestamp); the rest are the ladder median's forecast front.
P50_LEADS_H = (0.0, 6.0, 12.0, 24.0, 48.0, 72.0)

#: Display perimeter budget: WFIGS carried 4,513 rings / 126,102 vertices
#: for 129 CONUS perimeters on 2026-09-26 (complex fires ship hundreds of
#: island rings) — the map needs the outline, not every island.
PERIM_MAX_RINGS = 12
PERIM_MAX_VERTS = 120
PERIM_EPS_M = 60.0
#: rings under this fraction of the incident's largest ring are dropped
#: (counted in perimeter_rings_total, never silently: the row says so).
PERIM_MIN_AREA_FRAC = 0.001

#: P50 ring display budget (vertices per ring after simplification).
RING_MAX_VERTS = 120

_NO_DOMAIN_MARKERS = ("spread skipped", "assessment skipped")


def incident_notes(notes, incident_ids) -> dict:
    """{incident_id: [note tail, ...]} for every cycle note shaped
    "incident <id>: <tail>" whose id is in `incident_ids` (the monitor's
    per-incident note format, threat.py). Ids may contain ':' (wfigs:,
    firms:, calfire:) but never ': ', so the first ': ' after the prefix
    ends the id. Order preserved; unmatched notes ignored."""
    ids = set(incident_ids)
    out: dict = {}
    for note in notes or ():
        if not isinstance(note, str) or not note.startswith("incident "):
            continue
        head, sep, tail = note[len("incident "):].partition(": ")
        if sep and head in ids:
            out.setdefault(head, []).append(tail)
    return out


def why_no_domain(tails) -> Optional[str]:
    """The cycle's own reason an incident built no domain: the first note
    tail naming a skip ("no protected clients within N km; spread skipped",
    "assessment skipped (...)"), else None."""
    for t in tails or ():
        if any(m in t for m in _NO_DOMAIN_MARKERS):
            return t
    return None


def count_excluded_stale(notes) -> int:
    """Incidents the monitor's staleness gate excluded this cycle (SAFETY
    SEMANTICS 9 note 'excluded N stale incident(s) ...'); 0 when none."""
    for note in notes or ():
        if isinstance(note, str) and note.startswith("excluded "):
            parts = note.split()
            if len(parts) > 2 and parts[1].isdigit() and parts[2] == "stale":
                return int(parts[1])
    return 0


def _finite(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def domain_stats(incident_id: str, result) -> Optional[dict]:
    """Domain facts for one incident, or None when it built no domain this
    cycle. clients = assessed clients; eta_p10_s / eta_p50_s = the earliest
    FINITE client arrival (None when every client is unreached within the
    horizon); grid + phase times from the ensemble spec and timings."""
    from wgresponse import geo
    ens = (getattr(result, "ensembles", None) or {}).get(incident_id)
    if ens is None:
        return None
    n = 0
    best_p10 = best_p50 = None
    first_client = None
    for a in getattr(result, "assessments", None) or ():
        if a.incident_id != incident_id:
            continue
        n += 1
        p10 = _finite(a.eta.eta_p10_s)
        p50 = _finite(a.eta.eta_p50_s)
        if p10 is not None and (best_p10 is None or p10 < best_p10):
            best_p10, first_client = p10, a.client_id
        if p50 is not None and (best_p50 is None or p50 < best_p50):
            best_p50 = p50
    spec = ens.spec
    # the grid's own edges (contracts GridSpec law: row latitude sets the
    # metres-per-degree of longitude, so the higher-|lat| edge is widest)
    south = spec.north_lat - spec.nrows * spec.cell_m / geo.M_PER_DEG_LAT
    east = spec.west_lon + spec.ncols * spec.cell_m / min(
        geo.m_per_deg_lon(spec.north_lat), geo.m_per_deg_lon(south))
    tm = ((getattr(result, "timings", None) or {}).get("incidents") or {}).get(
        incident_id) or {}
    out = {
        "clients": n,
        "eta_p10_s": best_p10,
        "eta_p50_s": best_p50,
        "first_client_id": first_client,
        "cells": spec.nrows * spec.ncols,
        "cell_m": spec.cell_m,
        "bbox": [round(south, 5), round(spec.west_lon, 5),
                 round(spec.north_lat, 5), round(east, 5)],
    }
    for k in ("terrain_s", "weather_s", "spread_s"):
        v = _finite(tm.get(k))
        if v is not None:
            out[k] = round(v, 3)
    return out


def _ring_area_m2(ring) -> float:
    """Planar shoelace area (m^2) of a LatLon ring, cos(lat)-scaled."""
    if len(ring) < 3:
        return 0.0
    lat0 = ring[0].lat
    kx = math.cos(math.radians(lat0)) * 111_320.0
    xy = [((p.lon - ring[0].lon) * kx, (p.lat - lat0) * 111_320.0)
          for p in ring]
    s = 0.0
    for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]):
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def display_perimeter(rings) -> "tuple[list, int]":
    """([[[lat, lon], ...closed], ...], total ring count) — a DISPLAY copy
    of an incident's feed perimeter: at most PERIM_MAX_RINGS rings, largest
    area first, rings under PERIM_MIN_AREA_FRAC of the largest dropped,
    each Douglas-Peucker simplified (PERIM_EPS_M, doubling until <=
    PERIM_MAX_VERTS vertices) and rounded to 1e-5 deg. The engine's own
    rings (spread seeds) are never touched."""
    from wgresponse.simui import _simplify_ll   # display helper (lazy)
    rings = [r for r in (rings or []) if r and len(r) >= 3]
    total = len(rings)
    if not rings:
        return [], total
    sized = sorted(((_ring_area_m2(r), i) for i, r in enumerate(rings)),
                   key=lambda t: (-t[0], t[1]))
    biggest = sized[0][0]
    out = []
    for area, i in sized[:PERIM_MAX_RINGS]:
        if biggest > 0.0 and area < PERIM_MIN_AREA_FRAC * biggest:
            break
        pts = _simplify_ll(rings[i], eps_m=PERIM_EPS_M,
                           max_pts=PERIM_MAX_VERTS)
        ring = [[round(p[0], 5), round(p[1], 5)] for p in pts]
        if len(ring) < 3:
            continue
        if ring[0] != ring[-1]:
            ring.append(list(ring[0]))
        out.append(ring)
    return out, total


def p50_rings(ens, t0_s: float, leads_h=P50_LEADS_H) -> list:
    """[{"h": lead, "rings": [[[lat, lon], ...], ...]}, ...] — the P50
    isochrones of one domain at t0_s + lead hours, traced by the
    simulator's own ring tracer on the min-pooled p50 raster (a block is
    reached iff any member cell is — the conservative visual union), each
    ring simplified to <= RING_MAX_VERTS. Leads with no reached cells are
    omitted."""
    from wgresponse.simui import _pool_min, _simplify_ll, _threshold_rings
    spec, vals = _pool_min(ens.p50)
    out = []
    for h in leads_h:
        rings = _threshold_rings(spec, vals, float(t0_s) + float(h) * 3600.0)
        if not rings:
            continue
        simp = []
        for ring in rings:
            pts = _simplify_ll(ring, eps_m=spec.cell_m * 0.5,
                               max_pts=RING_MAX_VERTS)
            r2 = [[round(p[0], 5), round(p[1], 5)] for p in pts]
            if len(r2) >= 3:
                if r2[0] != r2[-1]:
                    r2.append(list(r2[0]))
                simp.append(r2)
        if simp:
            out.append({"h": float(h), "rings": simp})
    return out


def rank_key(row: dict) -> tuple:
    """Sort key of the ranked list: domain incidents first by earliest
    client P10 arrival (unreached last among them), then the rest by
    acreage descending (None last), ties by incident id."""
    dom = row.get("domain")
    if dom:
        eta = dom.get("eta_p10_s")
        return (0, 0 if eta is not None else 1,
                eta if eta is not None else 0.0, 0.0, str(row["incident_id"]))
    acres = _finite(row.get("acreage"))
    return (1, 0 if acres is not None else 1, 0.0,
            -(acres or 0.0), str(row["incident_id"]))


def rank_incidents(rows: list) -> list:
    """rows sorted by rank_key with a 1-based "rank" stamped on each."""
    ranked = sorted(rows, key=rank_key)
    for k, row in enumerate(ranked, start=1):
        row["rank"] = k
    return ranked


def _source_of(inc) -> str:
    src = getattr(inc, "source", "")
    return str(getattr(src, "value", None) or src)


def _aoi_name(desc: str) -> str:
    """Short AOI label for the header: the zone names of a zones file
    ('conus'), else the description itself."""
    if isinstance(desc, str) and desc.startswith("zones file ") and "(" in desc:
        inner = desc.split("(", 1)[1]
        return inner.split(";", 1)[0].strip() or desc
    return desc or "AOI"


def _counts(state: dict, rows: list, notes) -> dict:
    by_source: dict = {}
    for r in rows:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
    clients = [c for c in state.get("clients") or [] if isinstance(c, dict)]
    alerted = sum(1 for c in clients
                  if c.get("state") not in (None, "none", "cleared"))
    out = {
        "incidents": len(rows),
        "with_domain": sum(1 for r in rows if r.get("domain")),
        "with_perimeter": sum(1 for r in rows if r.get("perimeter")),
        "by_source": dict(sorted(by_source.items())),
        "excluded_stale": count_excluded_stale(notes),
        "clients": len(clients),
        "clients_in_domains": sum((r.get("domain") or {}).get("clients", 0)
                                  for r in rows),
        "clients_alerted": alerted,
        "crews": len(state.get("crews") or []),
        "safe_zones": len(state.get("safe_zones") or []),
    }
    if "removed_contained" in state:
        # SAFETY SEMANTICS 13c (the daemon's --drop-contained): contained
        # fires removed from the map this cycle — additive, only when armed
        out["removed_contained"] = int(state.get("removed_contained") or 0)
    return out


def build_summary(state: dict, result, geometry: bool = True) -> dict:
    """The /api/world/summary payload for one completed cycle.

    state: the daemon's /api/state dict (counts, AOI, cycle clock);
    result: the cycle's contracts.CycleResult (incidents, ensembles,
    assessments, notes, timings). geometry=False skips perimeters and P50
    rings (tests / light callers). Pure: reads, never mutates."""
    t_s = float(result.t_s)
    notes = list(getattr(result, "notes", None) or [])
    incs = list(getattr(result, "incidents", None) or [])
    note_map = incident_notes(notes, [i.incident_id for i in incs])
    rows = []
    for inc in incs:
        iid = inc.incident_id
        tails = note_map.get(iid, [])
        dom = domain_stats(iid, result)
        row = {
            "incident_id": iid,
            "name": inc.name,
            "source": _source_of(inc),
            "sources": sorted((inc.source_ids or {}).keys()),
            "lat": round(inc.origin.lat, 5),
            "lon": round(inc.origin.lon, 5),
            "acreage": _finite(inc.acreage),
            "containment_pct": _finite(inc.containment_pct),
            "updated_at_s": _finite(inc.updated_at_s),
            "age_h": (round(max(0.0, t_s - inc.updated_at_s) / 3600.0, 2)
                      if _finite(inc.updated_at_s) else None),
            "domain": dom,
            "why_no_domain": None if dom else (
                why_no_domain(tails) or "no domain this cycle"),
            "notes": tails,
            "perimeter": None,
            "perimeter_rings_total": 0,
            "p50_rings": [],
        }
        if geometry:
            if inc.perimeter:
                ring_list, total = display_perimeter(inc.perimeter)
                row["perimeter"] = ring_list or None
                row["perimeter_rings_total"] = total
            ens = (result.ensembles or {}).get(iid)
            if ens is not None:
                row["p50_rings"] = p50_rings(ens, t_s)
        rows.append(row)
    rows = rank_incidents(rows)
    return {
        "schema": SCHEMA,
        "t_s": t_s,
        "cycles_run": state.get("cycles_run"),
        "aoi": state.get("aoi") or state.get("bbox"),
        "aoi_desc": state.get("aoi_desc", ""),
        "aoi_name": _aoi_name(state.get("aoi_desc", "")),
        "tiers": {"national_max_zoom": TIER_NATIONAL_MAX_ZOOM,
                  "regional_max_zoom": TIER_REGIONAL_MAX_ZOOM},
        "p50_leads_h": list(P50_LEADS_H),
        "counts": _counts(state, rows, notes),
        "incidents": rows,
    }


def summary_from_state(state: dict) -> dict:
    """The same shape from a bare /api/state dict (no CycleResult behind
    it: `serve --demo`, a sim console file, a stub provider). No domains
    and no geometry are claimed — every incident ranks by acreage and says
    so in why_no_domain."""
    state = state if isinstance(state, dict) else {}
    rows = []
    for inc in state.get("incidents") or []:
        if not isinstance(inc, dict) or inc.get("incident_id") is None:
            continue
        lat, lon = _finite(inc.get("lat")), _finite(inc.get("lon"))
        rows.append({
            "incident_id": str(inc["incident_id"]),
            "name": inc.get("name") or "",
            "source": str(inc.get("source") or "unknown"),
            "sources": [],
            "lat": lat, "lon": lon,
            "acreage": _finite(inc.get("acreage")),
            "containment_pct": _finite(inc.get("containment_pct")),
            "updated_at_s": _finite(inc.get("updated_at_s")),
            "age_h": None,
            "domain": None,
            "why_no_domain": "no watch-daemon cycle behind this state "
                             "(domains unknown)",
            "notes": [],
            "perimeter": None,
            "perimeter_rings_total": 0,
            "p50_rings": [],
        })
    rows = rank_incidents(rows)
    t = state.get("last_cycle_t_s", state.get("now_s"))
    return {
        "schema": SCHEMA,
        "t_s": _finite(t),
        "cycles_run": state.get("cycles_run"),
        "aoi": state.get("aoi") or state.get("bbox"),
        "aoi_desc": state.get("aoi_desc", ""),
        "aoi_name": _aoi_name(state.get("aoi_desc", "")),
        "tiers": {"national_max_zoom": TIER_NATIONAL_MAX_ZOOM,
                  "regional_max_zoom": TIER_REGIONAL_MAX_ZOOM},
        "p50_leads_h": list(P50_LEADS_H),
        "counts": _counts(state, rows, state.get("notes")),
        "incidents": rows,
    }
