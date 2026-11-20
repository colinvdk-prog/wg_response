"""wgresponse.decisions — OPERATOR CONFIRM AND DISMISS (contracts SAFETY
SEMANTICS 64; cfg.operator_confirm, watch --operator-confirm; needs 41's
hotspot_heat_only).

Founder decisions it implements:
  2026-11-01 (docs/review/hotspot_screening_2026-11-01.md): a fire only the
      satellite sees is the operator's; clients hear nothing until an agency
      reports it or an operator CONFIRMS it; crews only after a Confirm or an
      agency report. "A Confirm sticks. A wrong Confirm is undone only by a
      sustained quiet period or by a stand-down that two operators sign,
      never silently." "A Dismiss expires after 12 h and is undone by
      growth."
  2026-11-19: the quiet period is 24 h with no new detection; when an agency
      report takes a confirmed fire its homes' alerts CARRY OVER to the
      agency fire (no second alert, never a step down); the buttons are on
      the ops console.

CONFIRM (a heat cluster on the live map, by its current incident id): its
  detections become the confirmation's footprint and it gets a stable id,
  confirmed:<hex>. Every cycle (apply(), before the heat split) each
  satellite-only cluster with a detection within MATCH_M (the FIRMS
  clustering distance, 2 km) of the footprint — or carrying an id the
  confirmation has seen — is that fire: the clusters are merged into ONE
  incident under the confirmation's id with source id "operator", so the
  heat split keeps it (no longer satellite-only): forecast, the client
  ladder, dispatch. New detections join the footprint; the newest detection
  time is its last_seen_s.
  LAPSE: no detection for QUIET_S (24 h) -> the confirmation lapses (its
  pairs then take the absence law, SAFETY SEMANTICS 6).
  STAND-DOWN: two DIFFERENT operators sign; the first signature is
  recorded and changes nothing.
  AGENCY HANDOVER: a confirmed fire no cluster carries this cycle whose
  footprint an agency (reported) fire now covers — a detection or its
  origin within MATCH_M — is handed over: AlertEngine.carry_pairs moves
  every home's alert to the agency fire's id (the higher level stands when
  both exist; the sent-message ledger moves with it, so nothing is re-sent
  and no all-clear is said), and the agency fire is assessed as NEW this
  cycle (first, early alerts — never later than without the Confirm).
DISMISS: the cluster's footprint, n detections and max FRP are kept for
  DISMISS_S (12 h): its satellite-heat page is not raised again and the map
  says who dismissed it. GROWTH undoes it at once — a detection more than
  GROWTH_M (500 m) outside the footprint, or the detections or the FRP
  doubled (the protocol's escape rule) — with an operator event that pages
  (dismissed_heat_grew).
AUDIT: every decision and every lapse, stand-down signature, handover,
  expiry and growth is a row in oncall/decisions-audit-<date>.jsonl beside
  the state (mirrored to S3); the decisions persist (oncall/decisions.json)
  and are restored after a restart.

Stdlib only; thread-safe (the HTTP threads and the cycle thread)."""
from __future__ import annotations

import dataclasses
import json
import logging
import math
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .contracts import IncidentSource, LatLon, OperatorEvent

log = logging.getLogger(__name__)

#: a detection this close to a confirmed / dismissed footprint is that fire
MATCH_M = 2000.0
#: founder 2026-11-19: no detection for this long lapses a Confirm
QUIET_S = 24 * 3600.0
#: founder 2026-11-01: a Dismiss expires after 12 h
DISMISS_S = 12 * 3600.0
#: the protocol's escape distance: growth beyond the footprint
GROWTH_M = 500.0
#: footprint points kept per decision (the newest)
KEEP_POINTS = 400
_EARTH_R_M = 6_371_000.0


def _hav(a: LatLon, b: LatLon) -> float:
    la1, la2 = math.radians(a.lat), math.radians(b.lat)
    dla = la2 - la1
    dlo = math.radians(b.lon - a.lon)
    h = (math.sin(dla / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2)
    return 2 * _EARTH_R_M * math.asin(min(1.0, math.sqrt(h)))


def _iso(t_s: float) -> str:
    return datetime.fromtimestamp(float(t_s), tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _pts(inc) -> list:
    return list(getattr(inc, "hotspots", None) or []) or [inc.origin]


def _newest(inc) -> float:
    meta = getattr(inc, "meta", None) or {}
    ts = [t for t in (meta.get("hotspot_t_s") or [])
          if isinstance(t, (int, float)) and math.isfinite(t)]
    return max(ts) if ts else float(inc.updated_at_s)


def _near(pts: list, foot: list, within: float) -> bool:
    for p in pts:
        for q in foot:
            if abs(p.lat - q.lat) * 111_000.0 > within:
                continue
            if _hav(p, q) <= within:
                return True
    return False


def satellite_only(inc) -> bool:
    """41's predicate (a FIRMS incident no reported feed contributed to)."""
    return (inc.source is IncidentSource.FIRMS
            and not str(inc.incident_id).startswith("seeded:")
            and set((inc.source_ids or {}).keys()) <= {"firms"})


class OperatorDecisions:
    """Confirm / Dismiss / stand-down, matched to the live map every cycle
    (module docstring). state_dir: where decisions.json and the audit live
    (None = memory only); mirror: a persist.S3Mirror (None = local)."""

    def __init__(self, state_dir: Optional[str] = None, mirror=None,
                 clock: Callable[[], float] = time.time):
        self._dir = Path(state_dir) / "oncall" if state_dir else None
        self._mirror = mirror
        self._clock = clock
        self._lock = threading.RLock()
        self._confirmed: dict = {}      # confirm_id -> record
        self._dismissed: dict = {}      # dismiss_id -> record
        self.handover_new: set = set()  # agency ids handed over this cycle

    # -- persistence ---------------------------------------------------------

    @staticmethod
    def _ser(rec: dict) -> dict:
        out = dict(rec)
        out["points"] = [[p.lat, p.lon] for p in rec["points"]]
        return out

    @staticmethod
    def _de(rec: dict) -> dict:
        out = dict(rec)
        out["points"] = [LatLon(float(a), float(b)) for a, b in rec["points"]]
        return out

    def restore(self) -> int:
        body = None
        try:
            if self._dir is not None and (self._dir / "decisions.json").exists():
                body = (self._dir / "decisions.json").read_bytes()
            elif self._mirror is not None:
                body = self._mirror.download(
                    self._mirror.key_for("oncall/decisions.json"))
        except Exception as exc:                # noqa: BLE001
            log.error("operator decisions: not restored (%s) — starting "
                      "with none", exc)
            return 0
        if not body:
            return 0
        try:
            obj = json.loads(body.decode("utf-8"))
        except Exception:                       # noqa: BLE001
            log.error("operator decisions: decisions.json does not parse — "
                      "starting with none")
            return 0
        with self._lock:
            for r in obj.get("confirmed") or []:
                self._confirmed[r["confirm_id"]] = self._de(r)
            for r in obj.get("dismissed") or []:
                self._dismissed[r["dismiss_id"]] = self._de(r)
        return len(self._confirmed) + len(self._dismissed)

    def _persist(self) -> None:
        if self._dir is None:
            return
        with self._lock:
            obj = {"confirmed": [self._ser(r) for r in self._confirmed.values()],
                   "dismissed": [self._ser(r) for r in self._dismissed.values()]}
        body = json.dumps(obj, sort_keys=True).encode("utf-8")
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._dir / "decisions.json.tmp"
            tmp.write_bytes(body)
            os.replace(tmp, self._dir / "decisions.json")
        except OSError as exc:
            log.error("operator decisions: decisions.json not written (%s)",
                      exc)
            return
        if self._mirror is not None:
            self._mirror.upload(self._mirror.key_for("oncall/decisions.json"),
                                body)

    def _audit(self, rec: dict, what: str, now_s: float, **extra) -> None:
        rid = rec.get("confirm_id") or rec.get("dismiss_id")
        rec.setdefault("history", []).append(
            {"t_s": now_s, "what": what, **extra})
        if self._dir is None:
            return
        row = {"t_s": now_s, "t": _iso(now_s), "id": rid,
               "cluster": rec.get("cluster_id"), "what": what, **extra}
        name = f"decisions-audit-{_iso(now_s)[:10]}.jsonl"
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with open(self._dir / name, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
            if self._mirror is not None:
                self._mirror.upload(self._mirror.key_for(f"oncall/{name}"),
                                    (self._dir / name).read_bytes())
        except OSError as exc:
            log.error("operator decisions: audit row not written (%s)", exc)

    # -- the operator's actions ----------------------------------------------

    def _active_confirm_for(self, inc) -> Optional[dict]:
        for r in self._confirmed.values():
            if r["status"] in ("active", "stand_down_pending") and (
                    inc.incident_id in r["cluster_ids"]
                    or _near(_pts(inc), r["points"], MATCH_M)):
                return r
        return None

    def confirm(self, inc, by: str) -> "tuple[int, dict]":
        """Confirm a satellite-only cluster (the live map's incident)."""
        if not satellite_only(inc):
            return 409, {"error": "only satellite-only heat can be confirmed "
                                  "(a reported fire is already confirmed)"}
        now = float(self._clock())
        with self._lock:
            hit = self._active_confirm_for(inc)
            if hit is not None:
                return 200, self.view(hit)
            for d in self._dismissed.values():
                if d["status"] == "active" and (
                        inc.incident_id == d["cluster_id"]
                        or _near(_pts(inc), d["points"], MATCH_M)):
                    d["status"] = "superseded"
                    self._audit(d, "superseded by a Confirm", now, by=by)
            meta = inc.meta or {}
            rec = {"confirm_id": "confirmed:" + secrets.token_hex(5),
                   "cluster_id": inc.incident_id,
                   "cluster_ids": [inc.incident_id],
                   "name": f"Confirmed satellite fire near "
                           f"{inc.origin.lat:.3f},{inc.origin.lon:.3f}",
                   "points": _pts(inc)[-KEEP_POINTS:],
                   "n_detections": int(meta.get("n_detections")
                                       or len(_pts(inc))),
                   "confirmed_by": by, "confirmed_s": now,
                   "last_seen_s": _newest(inc), "status": "active",
                   "stand_down": [], "handed_to": None, "history": []}
            self._confirmed[rec["confirm_id"]] = rec
            self._audit(rec, "confirmed", now, by=by,
                        n_detections=rec["n_detections"])
            self._persist()
            return 200, self.view(rec)

    def dismiss(self, inc, by: str) -> "tuple[int, dict]":
        if not satellite_only(inc):
            return 409, {"error": "only satellite-only heat can be dismissed"}
        now = float(self._clock())
        with self._lock:
            if self._active_confirm_for(inc) is not None:
                return 409, {"error": "this heat is a confirmed fire — a "
                                      "confirmed fire is stood down by two "
                                      "operators, never dismissed"}
            meta = inc.meta or {}
            rec = {"dismiss_id": "dismissed:" + secrets.token_hex(5),
                   "cluster_id": inc.incident_id,
                   "points": _pts(inc)[-KEEP_POINTS:],
                   "n_detections": int(meta.get("n_detections")
                                       or len(_pts(inc))),
                   "max_frp": meta.get("max_frp"),
                   "dismissed_by": by, "dismissed_s": now,
                   "expires_s": now + DISMISS_S, "status": "active",
                   "history": []}
            self._dismissed[rec["dismiss_id"]] = rec
            self._audit(rec, "dismissed", now, by=by,
                        n_detections=rec["n_detections"],
                        expires=_iso(rec["expires_s"]))
            self._persist()
            return 200, self.view(rec)

    def stand_down(self, confirm_id: str, by: str) -> "tuple[int, dict]":
        """A stand-down needs two DIFFERENT operators (founder 2026-11-01)."""
        now = float(self._clock())
        who = str(by or "").strip()
        if not who:
            return 400, {"error": "a stand-down signature needs a name"}
        with self._lock:
            rec = self._confirmed.get(confirm_id)
            if rec is None:
                return 404, {"error": "no such confirmed fire"}
            if rec["status"] not in ("active", "stand_down_pending"):
                return 409, {"error": f"this confirmation is {rec['status']}"}
            signers = [s["by"].strip().lower() for s in rec["stand_down"]]
            if who.lower() in signers:
                return 409, {"error": "a second, different operator must "
                                      "sign the stand-down"}
            rec["stand_down"].append({"by": who, "t_s": now})
            if len(rec["stand_down"]) >= 2:
                rec["status"] = "stood_down"
                self._audit(rec, "stood down (two signatures)", now, by=who)
            else:
                rec["status"] = "stand_down_pending"
                self._audit(rec, "stand-down signed (one of two)", now,
                            by=who)
            self._persist()
            return 200, self.view(rec)

    # -- the cycle -----------------------------------------------------------

    def apply(self, active: list, now_s: float, notes: list,
              operator_events: list, carry=None) -> list:
        """Before the heat split: confirmed clusters become one incident
        each under their confirmation's id; lapses, handovers (carry(old,
        new) moves the alerts), dismissal expiry and growth. Returns the
        new active list. Never raises (a failure is a note; the cycle runs
        on the list it had)."""
        try:
            return self._apply(active, now_s, notes, operator_events, carry)
        except Exception as exc:                # noqa: BLE001
            log.exception("operator decisions: apply failed")
            notes.append(f"operator decisions not applied this cycle "
                         f"({exc}) — SAFETY SEMANTICS 64")
            return active

    def _apply(self, active, now_s, notes, operator_events, carry):
        self.handover_new = set()
        changed = False
        out: list = []
        merged: dict = {}               # confirm_id -> [incidents]
        with self._lock:
            live = [r for r in self._confirmed.values()
                    if r["status"] in ("active", "stand_down_pending")]
            for inc in active:
                hit = None
                if satellite_only(inc):
                    for r in live:
                        if (inc.incident_id in r["cluster_ids"]
                                or _near(_pts(inc), r["points"], MATCH_M)):
                            hit = r
                            break
                if hit is None:
                    out.append(inc)
                else:
                    merged.setdefault(hit["confirm_id"], []).append(inc)
            for r in live:
                cid = r["confirm_id"]
                incs = merged.get(cid)
                if incs:
                    pts, newest = [], r["last_seen_s"]
                    for inc in incs:
                        pts += _pts(inc)
                        newest = max(newest, _newest(inc))
                        if inc.incident_id not in r["cluster_ids"]:
                            r["cluster_ids"].append(inc.incident_id)
                            changed = True
                    if newest > r["last_seen_s"]:
                        r["last_seen_s"] = newest
                        changed = True
                    known = {(round(p.lat, 5), round(p.lon, 5))
                             for p in r["points"]}
                    add = [p for p in pts
                           if (round(p.lat, 5), round(p.lon, 5)) not in known]
                    if add:
                        r["points"] = (r["points"] + add)[-KEEP_POINTS:]
                        changed = True
                    out.append(self._as_fire(r, incs, pts))
                    continue
                # no cluster carries it: an agency fire took it?
                taker = next((a for a in active
                              if not satellite_only(a)
                              and not str(a.incident_id).startswith(
                                  ("seeded:", "confirmed:"))
                              and _near(_pts(a), r["points"], MATCH_M)),
                             None)
                if taker is not None:
                    moved = []
                    if carry is not None:
                        moved = carry(cid, taker.incident_id, now_s)
                    r["status"] = "handed_over"
                    r["handed_to"] = taker.incident_id
                    self.handover_new.add(taker.incident_id)
                    self._audit(r, "handed over to an agency fire", now_s,
                                to=taker.incident_id,
                                pairs=[list(m) for m in moved])
                    operator_events.append(OperatorEvent(
                        t_s=now_s, kind="confirmed_fire_handed_over",
                        message=(f"confirmed satellite fire {cid} is now "
                                 f"agency fire {taker.name or taker.incident_id}"
                                 f" — {len(moved)} home alert(s) carried "
                                 f"over, nothing re-sent (SAFETY SEMANTICS "
                                 f"64)"),
                        incident_id=taker.incident_id,
                        detail={"confirm_id": cid,
                                "to": taker.incident_id,
                                "carried": len(moved)}))
                    notes.append(f"confirmed fire {cid} handed over to "
                                 f"{taker.incident_id} ({len(moved)} alert "
                                 f"pair(s) carried)")
                    changed = True
                elif now_s - r["last_seen_s"] > QUIET_S:
                    r["status"] = "lapsed"
                    self._audit(r, "lapsed: no detection for 24 h", now_s)
                    operator_events.append(OperatorEvent(
                        t_s=now_s, kind="confirmed_fire_lapsed",
                        message=(f"confirmed satellite fire {cid} lapsed: no "
                                 f"satellite detection for 24 h (SAFETY "
                                 f"SEMANTICS 64) — its alerts now follow the "
                                 f"absence rule"),
                        incident_id=cid, detail={"confirm_id": cid}))
                    changed = True
                else:
                    notes.append(f"confirmed fire {cid}: not seen this "
                                 f"cycle (last detection "
                                 f"{(now_s - r['last_seen_s']) / 3600.0:.1f}"
                                 f" h ago; lapses at 24 h)")
            for d in self._dismissed.values():
                if d["status"] != "active":
                    continue
                if now_s >= d["expires_s"]:
                    d["status"] = "expired"
                    self._audit(d, "dismissal expired (12 h)", now_s)
                    changed = True
                    continue
                for inc in out:
                    if not satellite_only(inc):
                        continue
                    pts = _pts(inc)
                    if not (inc.incident_id == d["cluster_id"]
                            or _near(pts, d["points"], MATCH_M)):
                        continue
                    why = self._growth(d, inc, pts)
                    if why:
                        d["status"] = "undone_by_growth"
                        self._audit(d, "undone by growth", now_s, why=why)
                        operator_events.append(OperatorEvent(
                            t_s=now_s, kind="dismissed_heat_grew",
                            message=(f"DISMISSED heat {inc.incident_id} grew "
                                     f"({why}) — the dismissal is undone "
                                     f"(SAFETY SEMANTICS 64)"),
                            incident_id=inc.incident_id,
                            detail={"dismiss_id": d["dismiss_id"],
                                    "why": why}))
                        changed = True
                        break
        if changed:
            self._persist()
        return out

    @staticmethod
    def _growth(d: dict, inc, pts: list) -> Optional[str]:
        far = [p for p in pts if not _near([p], d["points"], GROWTH_M)]
        if far:
            return f"{len(far)} detection(s) more than 500 m outside"
        meta = inc.meta or {}
        n = int(meta.get("n_detections") or len(pts))
        if d["n_detections"] and n >= 2 * d["n_detections"]:
            return f"detections {d['n_detections']} -> {n}"
        frp, frp0 = meta.get("max_frp"), d.get("max_frp")
        if (isinstance(frp, (int, float)) and isinstance(frp0, (int, float))
                and frp0 > 0 and frp >= 2 * frp0):
            return f"max FRP {frp0:g} -> {frp:g} MW"
        return None

    @staticmethod
    def _as_fire(r: dict, incs: list, pts: list):
        """One incident for the confirmed fire: the newest cluster, its
        detections the union, its id the confirmation's, its source ids
        carrying "operator" (so 41's split keeps it)."""
        base = max(incs, key=lambda i: float(i.updated_at_s))
        meta = dict(base.meta or {})
        ts: list = []
        for inc in incs:
            ts += list((inc.meta or {}).get("hotspot_t_s") or [])
        if ts:
            meta["hotspot_t_s"] = ts
        meta["n_detections"] = len(pts)
        meta["operator_confirmed"] = {
            "confirm_id": r["confirm_id"], "by": r["confirmed_by"],
            "at_s": r["confirmed_s"], "clusters": [i.incident_id
                                                   for i in incs]}
        sids = dict(base.source_ids or {})
        sids["operator"] = r["confirm_id"]
        return dataclasses.replace(
            base, incident_id=r["confirm_id"], name=r["name"],
            hotspots=list(pts),
            updated_at_s=max(float(i.updated_at_s) for i in incs),
            source_ids=sids, meta=meta)

    # -- views -----------------------------------------------------------------

    def dismissed_for(self, incident_id: str, pts=None) -> Optional[dict]:
        """The active dismissal covering this heat (paging asks)."""
        with self._lock:
            for d in self._dismissed.values():
                if d["status"] == "active" and (
                        incident_id == d["cluster_id"]
                        or (pts and _near(pts, d["points"], MATCH_M))):
                    return self.view(d)
        return None

    def confirmed_for(self, incident_id: str) -> Optional[dict]:
        with self._lock:
            for r in self._confirmed.values():
                if (r["status"] in ("active", "stand_down_pending")
                        and (incident_id == r["confirm_id"]
                             or incident_id in r["cluster_ids"])):
                    return self.view(r)
        return None

    @staticmethod
    def view(rec: dict) -> dict:
        out = {k: v for k, v in rec.items() if k != "points"}
        out["n_points"] = len(rec.get("points") or [])
        return json.loads(json.dumps(out))

    def status(self) -> dict:
        with self._lock:
            conf = [self.view(r) for r in self._confirmed.values()]
            dis = [self.view(d) for d in self._dismissed.values()]
        conf.sort(key=lambda r: -r["confirmed_s"])
        dis.sort(key=lambda r: -r["dismissed_s"])
        live = ("active", "stand_down_pending")
        return {"rule": {"quiet_h": QUIET_S / 3600.0,
                         "dismiss_h": DISMISS_S / 3600.0,
                         "match_m": MATCH_M, "growth_m": GROWTH_M,
                         "stand_down_signatures": 2},
                "confirmed": [r for r in conf if r["status"] in live]
                + [r for r in conf if r["status"] not in live][:20],
                "dismissed": [d for d in dis if d["status"] == "active"]
                + [d for d in dis if d["status"] != "active"][:20]}
