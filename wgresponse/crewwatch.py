"""wgresponse.crewwatch — THE LIVE CREW WATCH (contracts SAFETY SEMANTICS 45).

The founder's crew-program review (scratch_review/crew_program_review_
2026-11-11, lane C finding F1, verified by the orchestrator) measured that the
live watcher plans for a crew and never checks it again: a crew reporting
DEPLOYING at a home whose P10 lead had fallen to 24 min (law: 30) got no
withdrawal, no operator event and no note, and the plan silently dropped it.
This module is the check, run by ThreatMonitor.cycle() every cycle under
cfg.crew_watch. It moves no crew and sends nothing to a crew or a client:
what it finds is OPERATOR-facing (OperatorEvents, cycle notes, /api/state
crew_watch).

WHO IS WATCHED: every crew whose last report says EN_ROUTE, DEPLOYING or
EGRESSING — while the report is fresh (cfg.beacon_stale_s), and for
SILENT_WATCH_S after it went silent (a crew that went quiet in the field is
the one the operator most needs to hear about; it is judged at its last
reported spot and listed UNJUDGED). AVAILABLE / OFF_DUTY / INBOUND crews and
crews that never reported are not watched.

WHAT IS JUDGED (each crew, against EVERY forecast fire this cycle — the
dispatcher's own corridor rasters, dispatch.corridor_rasters: each fire's P10
eroded by corridor_erosion_m, the fuel-tiered rasters where the cycle built
them):
  SPOT   (SEMANTICS 10a) the crew's reported position: the line is the eroded
         P10 arrival less egress_buffer_min (a bounded off-domain sample, the
         fail-closed law, less max(that, its SEMANTICS 15 crossing margin));
         spot margin = line - now.
  HOME   (SEMANTICS 2 / 7) a DEPLOYING crew's home — the report's client_id,
         and every enrolled home within crew_work_radius_m (+ the fix's
         accuracy) of its spot: the home's latest lawful departure, P10 less
         the egress and ember buffers, over every fire threatening it (the
         earliest home when several qualify); home margin = that - now.
  WAY OUT (SEMANTICS 3 / 4 / 45c) a lawful egress leg to a safe zone NOW,
         by the planner's own law (dispatch.egress_table: the corridor law
         on tier-0 chords, with 15 / 20a / 37 / 38 wherever they are armed).
         way-out margin = the latest lawful departure over the zones - now.
LEVELS (ascending urgency): clear < at_risk (some margin under slack_min,
SEMANTICS 7's planned-slack floor: no plan would leave a crew this little) <
unjudged < must_leave (the spot or the home margin is negative: past the
law's line) < no_way_out (no zone's leg is lawful now: by the planner's law
the crew should already have left; THE MOST URGENT). Every finding is in the
row and the message whatever the level.

FAIL CLOSED: a crew the watch cannot judge is UNJUDGED, never clear —
silent; a fix less accurate than the corridor erosion; no forecast fire this
cycle; a spot no forecast raster covers and no fail-closed bound reaches; a
fire whose assessment failed this cycle within the plausible threat radius;
an exception (the row says which).

EVENTS: one OperatorEvent per crew per crossing — when its level rises above
the level it held last cycle (kind crew_at_risk / crew_unjudged /
crew_must_leave / crew_no_way_out; detail: crew, status, place, margin, fire,
way out). A crew whose level falls and rises again is told again. The memory
is the monitor's, in process (a restart re-raises once: the pessimistic
side). Every cycle each crew above clear is one note.
"""
from __future__ import annotations

import math
from typing import Optional

from wgresponse import dispatch, geo, safety
from wgresponse.contracts import UNREACHED, CrewStatus, OperatorEvent

#: Statuses whose report puts a crew under the watch.
FIELD_STATUSES = frozenset({CrewStatus.EN_ROUTE, CrewStatus.DEPLOYING,
                            CrewStatus.EGRESSING})
#: A crew silent in the field is watched (unjudged) this long (s) after its
#: last report; then it leaves the watch with the cycle's notes saying so.
SILENT_WATCH_S = 12 * 3600.0

LEVELS = ("clear", "at_risk", "unjudged", "must_leave", "no_way_out")
RANK = {lv: i for i, lv in enumerate(LEVELS)}
EVENT_KIND = {"at_risk": "crew_at_risk", "unjudged": "crew_unjudged",
              "must_leave": "crew_must_leave",
              "no_way_out": "crew_no_way_out"}
_LEVEL_TEXT = {"clear": "clear", "at_risk": "AT RISK",
               "unjudged": "UNJUDGED", "must_leave": "MUST LEAVE",
               "no_way_out": "NO LAWFUL WAY OUT"}


def _status(b) -> CrewStatus:
    st = getattr(b, "status", None)
    try:
        return CrewStatus(getattr(st, "value", st))
    except ValueError:
        return CrewStatus.UNKNOWN


def watched(crews, now_s: float) -> list:
    """The crews the watch covers at now_s (see the module docstring), in
    the given (crew id) order."""
    out = []
    for c in crews:
        b = getattr(c, "last_beacon", None)
        if b is None or _status(b) not in FIELD_STATUSES:
            continue
        if not math.isfinite(getattr(b, "t_s", math.nan)):
            continue
        if now_s - b.t_s > SILENT_WATCH_S:
            continue
        out.append(c)
    return out


def _mins(dt_s: float) -> Optional[float]:
    return None if not math.isfinite(dt_s) else round(dt_s / 60.0, 2)


def homes_of(beacon, clients: dict, cfg) -> list:
    """The homes a DEPLOYING report says (client_id, "report") or puts the
    crew at (client_id, "nearby": every client in `clients` within
    crew_work_radius_m + the fix's accuracy), report first, then by
    distance and id. [] for any other status."""
    if _status(beacon) is not CrewStatus.DEPLOYING:
        return []
    out = []
    said = (getattr(beacon, "client_id", "") or "").strip()
    if said:
        out.append((said, "report"))
    radius = float(getattr(cfg, "crew_work_radius_m", 0.0) or 0.0)
    if radius > 0.0:
        reach = radius + max(0.0, float(getattr(beacon, "accuracy_m", 0.0)))
        near = []
        for cid, c in clients.items():
            if cid == said:
                continue
            d = geo.haversine_m(beacon.loc, c.loc)
            if d <= reach:
                near.append((d, cid))
        out += [(cid, "nearby") for _d, cid in sorted(near)]
    return out


def _home_deadline(cid: str, etas: dict, cfg):
    """(latest lawful departure s, binding incident) over every fire this
    cycle assessed for client cid (SEMANTICS 2); (None, None) when none
    did."""
    best, inc = None, None
    for (c, iid), eta in etas.items():
        if c != cid:
            continue
        ld = safety.latest_departure_s(eta.eta_p10_s, cfg)
        if best is None or ld < best or (ld == best and iid < (inc or "")):
            best, inc = ld, iid
    return best, inc


def _spot(loc, now_s, rasters, cfg, offmap):
    """(line_s, binding incident, covered) — the crew's spot judged by the
    corridor law with egress_buffer_min as its margin (SEMANTICS 10a; a
    bounded sample takes max(that, its 15c crossing margin)): line_s is the
    latest moment the spot is clear of the line, UNREACHED when nothing
    bounds it; covered = some fire's raster contains the spot."""
    speed = safety._corridor_speed_mps(cfg)
    line, inc, covered = UNREACHED, None, False
    for iid, ras in rasters:
        if safety._latlon_to_rc(ras.spec, loc) is not None:
            covered = True
        om = (offmap or {}).get(iid)
        if om is None:
            t = safety.LegOracle(loc, loc, speed, ras, cfg,
                                 margin_min=cfg.egress_buffer_min
                                 ).latest_safe_depart_s
        else:
            t = safety.LegOracle(loc, loc, speed, ras, cfg,
                                 margin_min=cfg.egress_buffer_min,
                                 offraster_ros_mps=om[0],
                                 offraster_margin_min=om[1]
                                 ).latest_safe_depart_s
        if t < line:
            line, inc = t, iid
    return line, inc, covered


def judge_crew(crew, now_s: float, cfg, *, rasters, safe_zones, offmap=None,
               etas=None, clients=None, failed=(), threat_radius_m=0.0,
               has_forecast=True) -> dict:
    """One crew's watch row (see the module docstring). Raises only on a
    programming error; judge() turns any exception into an unjudged row."""
    b = crew.last_beacon
    status = _status(b)
    age = now_s - b.t_s
    fresh = age <= cfg.beacon_stale_s
    row = {"crew_id": crew.crew_id, "name": crew.name or crew.crew_id,
           "status": status.value, "report_t_s": b.t_s,
           "report_age_s": round(age, 1), "fresh": bool(fresh),
           "lat": b.loc.lat, "lon": b.loc.lon,
           "accuracy_m": b.accuracy_m,
           "client_id": (getattr(b, "client_id", "") or None)}
    unjudged = []
    reasons = []
    if not fresh:
        unjudged.append(f"silent {age / 60.0:.0f} min (reports go stale after "
                        f"{cfg.beacon_stale_s / 60.0:g} min): judged at its "
                        f"last reported spot")
    if b.accuracy_m > cfg.corridor_erosion_m:
        unjudged.append(f"its fix is good to {b.accuracy_m:.0f} m, coarser "
                        f"than the {cfg.corridor_erosion_m:.0f} m corridor "
                        f"erosion")
    if not has_forecast:
        unjudged.append("no forecast fire this cycle (nothing to judge it "
                        "against)")
    for inc in failed:
        try:
            d = geo.haversine_m(b.loc, inc.origin)
        except Exception:
            d = 0.0
        if d <= threat_radius_m:
            unjudged.append(f"fire {inc.incident_id}'s forecast failed this "
                            f"cycle ({d / 1000.0:.1f} km away)")

    # SPOT (SEMANTICS 10a)
    line, spot_inc, covered = _spot(b.loc, now_s, rasters, cfg,
                                    dispatch._offmap_table(offmap))
    spot_margin = (line - now_s) if math.isfinite(line) else math.inf
    row["spot"] = {"line_s": (line if math.isfinite(line) else None),
                   "margin_min": _mins(spot_margin),
                   "incident_id": spot_inc if math.isfinite(line) else None,
                   "covered": bool(covered)}
    if has_forecast and not covered and not math.isfinite(line):
        unjudged.append("no forecast covers its spot (and no fail-closed "
                        "bound reaches it)")
    if spot_margin < 0.0:
        reasons.append(f"its spot is {-spot_margin / 60.0:.1f} min past the "
                       f"line (eroded P10 of {spot_inc} less the "
                       f"{cfg.egress_buffer_min:g}-min egress buffer)")

    # HOME (SEMANTICS 2 / 7)
    homes = homes_of(b, clients or {}, cfg)
    home = None
    for cid, source in homes:
        ld, hinc = _home_deadline(cid, etas or {}, cfg)
        dist = None
        c = (clients or {}).get(cid)
        if c is not None:
            dist = round(geo.haversine_m(b.loc, c.loc), 1)
        cand = {"client_id": cid, "source": source, "distance_m": dist,
                "latest_depart_s": ld, "incident_id": hinc,
                "margin_min": (_mins(ld - now_s) if ld is not None
                               and math.isfinite(ld) else None)}
        if home is None or (ld is not None and (
                home["latest_depart_s"] is None
                or ld < home["latest_depart_s"])):
            home = cand
    row["homes"] = [h for h, _s in homes]
    row["home"] = home
    home_margin = math.inf
    if home is not None and home["latest_depart_s"] is not None:
        home_margin = home["latest_depart_s"] - now_s
        if home_margin < 0.0:
            reasons.append(f"home {home['client_id']}'s latest lawful "
                           f"departure passed {-home_margin / 60.0:.1f} min "
                           f"ago (P10 of {home['incident_id']} less the "
                           f"egress and ember buffers)")
    if (status is CrewStatus.DEPLOYING and home is not None
            and home["source"] == "report" and home["distance_m"] is not None
            and home["distance_m"] > (cfg.crew_work_radius_m
                                      + b.accuracy_m)):
        reasons.append(f"it reports working {home['client_id']} but is "
                       f"{home['distance_m'] / 1000.0:.2f} km from it")

    # WAY OUT (SEMANTICS 3 / 4 / 45c) — the planner's egress law, now
    table = dispatch.egress_table(b.loc, rasters, safe_zones, cfg, now_s,
                                  offmap=offmap)
    lawful = [(z, t) for z, t in table if now_s <= t]
    best = max(table, key=lambda r: (r[1], r[0])) if table else None
    way_margin = ((best[1] - now_s) if best is not None else -math.inf)
    row["way_out"] = {
        "lawful": bool(lawful),
        "zone_id": lawful[0][0] if lawful else None,
        "best_zone_id": best[0] if best is not None else None,
        "latest_depart_s": (best[1] if best is not None
                            and math.isfinite(best[1]) else None),
        "margin_min": (_mins(way_margin) if best is not None else None)}
    if not table:
        reasons.append("no safe zone is configured (fail closed)")
    elif not lawful:
        reasons.append(f"no leg to a safe zone is lawful now: the last one, "
                       f"to {best[0]}, closed {-way_margin / 60.0:.1f} min "
                       f"ago — leave by the least exposed way, {best[0]}")

    margins = [("spot", spot_margin, spot_inc), ("home", home_margin,
                                                 home["incident_id"]
                                                 if home else None),
               ("way_out", way_margin, None)]
    worst = min(margins, key=lambda m: m[1])
    row["margin_min"] = _mins(worst[1]) if math.isfinite(worst[1]) else None
    row["binding"] = worst[0] if math.isfinite(worst[1]) else None
    row["incident_id"] = worst[2] or spot_inc

    level = "clear"
    slack_s = cfg.slack_min * 60.0
    if worst[1] < slack_s:
        level = "at_risk"
        if worst[1] >= 0.0:
            reasons.append(f"its {worst[0].replace('_', ' ')} margin is "
                           f"{worst[1] / 60.0:.1f} min, under the "
                           f"{cfg.slack_min:g}-min slack floor")
    if unjudged:
        level = "unjudged"
    if spot_margin < 0.0 or home_margin < 0.0:
        level = "must_leave"
    if not lawful:
        level = "no_way_out"
    row["level"] = level
    row["unjudged"] = unjudged
    row["reasons"] = reasons
    return row


def judge(crews, now_s: float, cfg, *, rasters, safe_zones, offmap=None,
          etas=None, clients=None, failed=(), threat_radius_m=0.0,
          has_forecast=True) -> list:
    """Every watched crew's row (watched(crews, now_s)), crew id order. A
    crew whose judgement raised is listed UNJUDGED with the error."""
    rows = []
    for c in watched(crews, now_s):
        try:
            rows.append(judge_crew(
                c, now_s, cfg, rasters=rasters, safe_zones=safe_zones,
                offmap=offmap, etas=etas, clients=clients, failed=failed,
                threat_radius_m=threat_radius_m, has_forecast=has_forecast))
        except Exception as exc:     # fail closed: never listed as safe
            b = c.last_beacon
            rows.append({"crew_id": c.crew_id,
                         "name": getattr(c, "name", "") or c.crew_id,
                         "status": _status(b).value, "report_t_s": b.t_s,
                         "lat": b.loc.lat, "lon": b.loc.lon,
                         "level": "unjudged",
                         "unjudged": [f"the watch failed on it ({exc})"],
                         "reasons": [], "margin_min": None})
    return rows


def place(row: dict) -> str:
    """Where the crew is, for a message: its home, else its spot."""
    home = row.get("home") or {}
    if home.get("client_id"):
        return f"at {home['client_id']}"
    return f"at {row.get('lat', 0.0):.5f},{row.get('lon', 0.0):.5f}"


def describe(row: dict) -> str:
    """One line: the crew, its status and place, its level, every finding."""
    parts = list(row.get("reasons") or []) + [
        "unjudged: " + u for u in (row.get("unjudged") or [])]
    way = row.get("way_out") or {}
    if way.get("lawful"):
        parts.append(f"way out: {way['zone_id']} lawful "
                     f"({way['margin_min']:.1f} min left)"
                     if way.get("margin_min") is not None
                     else f"way out: {way['zone_id']} lawful")
    return (f"crew {row['crew_id']} ({row.get('status')} {place(row)}) "
            f"{_LEVEL_TEXT.get(row.get('level'), row.get('level'))}"
            + (": " + "; ".join(parts) if parts else ""))


def events_and_notes(rows: list, prev: dict, now_s: float):
    """(operator events, notes, new memory) for this cycle's rows given last
    cycle's {crew_id: level}: an event when a crew's level rises (a new
    crossing), a note for every crew above clear."""
    events, notes, memory = [], [], {}
    for row in rows:
        cid = row["crew_id"]
        level = row.get("level", "unjudged")
        memory[cid] = level
        if level != "clear":
            notes.append("crew watch (SAFETY SEMANTICS 45): " + describe(row))
        if level == "clear" or RANK[level] <= RANK.get(prev.get(cid,
                                                                "clear"), 0):
            continue
        way = row.get("way_out") or {}
        events.append(OperatorEvent(
            t_s=now_s, kind=EVENT_KIND[level], message=describe(row),
            incident_id=row.get("incident_id") or "",
            detail={"crew_id": cid, "status": row.get("status"),
                    "level": level, "lat": row.get("lat"),
                    "lon": row.get("lon"),
                    "client_id": (row.get("home") or {}).get("client_id"),
                    "margin_min": row.get("margin_min"),
                    "binding": row.get("binding"),
                    "way_out": way.get("zone_id"),
                    "best_zone_id": way.get("best_zone_id"),
                    "unjudged": list(row.get("unjudged") or [])}))
    return events, notes, memory


def rule_summary(cfg) -> dict:
    """The /api/state crew_watch rule block."""
    return {"statuses": sorted(s.value for s in FIELD_STATUSES),
            "silent_watch_h": SILENT_WATCH_S / 3600.0,
            "egress_buffer_min": cfg.egress_buffer_min,
            "ember_buffer_min": cfg.ember_buffer_min,
            "slack_min": cfg.slack_min,
            "route_margin_min": cfg.route_margin_min,
            "corridor_erosion_m": cfg.corridor_erosion_m,
            "crew_work_radius_m": cfg.crew_work_radius_m,
            "levels": list(LEVELS)}
