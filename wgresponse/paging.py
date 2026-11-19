"""wgresponse.paging — THE OPERATOR PAGE AND THE ON-CALL ROTA (contracts SAFETY
SEMANTICS 63; cfg.operator_paging, watch --operator-paging [--rota PATH]).

Founder decisions it implements:
  2026-11-01 (docs/review/hotspot_screening_2026-11-01.md, decision 4): an
      operator is on call 24/7 in fire season and acknowledges within 15
      minutes; a second person is paged if there is no answer (the 8 km
      tripwire follows from those 15 minutes).
  2026-11-19: the channel stays DRY until the SMS provider (OPEN_QUESTIONS
      O-1) is chosen — a page is shown on the ops console and in the log and
      leaves nothing; it pages for satellite heat within 8 km of a home
      (SAFETY SEMANTICS 41's satellite_heat event), a new agency fire near
      homes (the sentinel's new_fire_near_homes, 60) and a client stepping UP
      to EXTREME_24H or IMPACTED; the operator acknowledges by a one-time
      signed link or the console's button; the rota is a JSON file in the
      watcher's private S3 state prefix (oncall/rota.json), reloaded while
      the watcher runs.

THE ROTA (Rota.parse; a template ships as data/oncall/rota.template.json —
never real contact details in the repo):
    {"version": 1,
     "season": [{"start": "2026-05-01", "end": "2026-11-30"}],   # [] = all year
     "people": {"op-a": {"name": "...", "email": "...", "sms": "..."}},
     "shifts": [{"start": "2026-11-19T00:00:00Z",
                 "end": "2026-11-26T00:00:00Z",
                 "primary": "op-a", "secondary": "op-b"}]}
  On call at t = the covering shift that started last. Contact details are
  read only by a channel; they never reach a log, /api/state or the audit.

THE PAGE (Pager): one page per warning, keyed by what it is about —
  satellite_heat:<incident>, new_fire:<incident>, alert:<incident>:<state>
  (the homes of one fire stepping up together are ONE page that lists them).
  A trigger whose key has an open page is added to it (more homes, a newer
  message) without paging again; one whose key was acknowledged within
  PAGE_REPEAT_QUIET_S is noted on that page, not paged again.
  t0: the page goes to the on-call PRIMARY.
  t0 + ack_min (15): unacknowledged -> the SECONDARY is paged, the primary
      again (escalation 1).
  every ack_min after: both again, until repeat_max sends to each tier;
      then the page reads UNANSWERED and stays open on the console.
  Nobody on call (no rota, no covering shift) -> the page is still raised on
  the console and its audit says so (never silently dropped).
  Acknowledged (link or console) -> no further sends; who, when and how are
  recorded. Every step is an audit row (oncall/audit-<date>.jsonl beside the
  state, mirrored to S3) and open pages survive a restart (oncall/pages.json).

THE LINK: page_ack_token(page, person, expires, key) — HMAC-SHA256 over the
page id, the person id and the expiry (key: env WGRESP_PAGE_KEY, else a
random per-process key, so links die with the process). GET /page/ack?t=
shows the page and a button; only the POST acknowledges (a link preview or
mail scanner that fetches the link acknowledges nothing).

DRY CHANNEL: send() records "would page <person id> (<tier>)" in the log and
the page's targets and returns True; it never reads a contact detail. A real
channel (SES / SNS / Twilio, O-1) implements the same send(person, page,
link) and is the only code that reads them.

Stdlib only; thread-safe (the cycle thread, the sentinel thread, the HTTP
threads and the escalation ticker share one lock)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

#: the founder's acknowledgement window (minutes; SAFETY SEMANTICS 63)
DEFAULT_ACK_MIN = 15.0
#: an acknowledged page's key is not paged again for this long (s)
PAGE_REPEAT_QUIET_S = 6 * 3600.0
#: closed pages kept for the console / state file
KEEP_CLOSED = 50
#: how often the rota source is re-read (s)
ROTA_RELOAD_S = 60.0
#: ack links stay valid this long (s)
LINK_TTL_S = 24 * 3600.0
#: the trigger names (cfg.paging_triggers)
TRIGGERS = ("satellite_heat", "new_fire_near_homes", "alert_step_up")
_STEP_UP_STATES = ("extreme_24h", "impacted")


def _iso(t_s: Optional[float]) -> Optional[str]:
    if t_s is None:
        return None
    return datetime.fromtimestamp(float(t_s), tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _parse_time(s, end_of_day: bool = False) -> float:
    """ISO date or date-time (Z or offset) -> unix s. A bare date is its
    00:00 UTC (end_of_day: the next day's 00:00 UTC)."""
    s = str(s).strip()
    if len(s) == 10:
        d = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return d.timestamp() + (86400.0 if end_of_day else 0.0)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


# ---------------------------------------------------------------------------
# the rota
# ---------------------------------------------------------------------------

class Rota:
    """A parsed rota (module docstring). Rota.parse(obj) -> (Rota, problems);
    a rota with problems still answers on_call() from what parsed."""

    def __init__(self, people: dict, shifts: list, season: list):
        self.people = people            # id -> {"name": ..., contact...}
        self.shifts = shifts            # [(start_s, end_s, primary, secondary)]
        self.season = season            # [(start_s, end_s)]; [] = all year

    @classmethod
    def parse(cls, obj) -> "tuple[Rota, list]":
        problems: list = []
        if not isinstance(obj, dict):
            return cls({}, [], []), ["rota is not a JSON object"]
        people: dict = {}
        raw_people = obj.get("people") or {}
        if not isinstance(raw_people, dict):
            problems.append("people must be an object of id -> person")
            raw_people = {}
        for pid, p in raw_people.items():
            if not isinstance(p, dict):
                problems.append(f"person {pid} is not an object")
                continue
            people[str(pid)] = dict(p)
        shifts: list = []
        for k, sh in enumerate(obj.get("shifts") or []):
            try:
                a = _parse_time(sh["start"])
                b = _parse_time(sh["end"])
            except Exception as exc:            # noqa: BLE001
                problems.append(f"shift {k}: bad start/end ({exc})")
                continue
            if b <= a:
                problems.append(f"shift {k}: end is not after start")
                continue
            pri = sh.get("primary")
            sec = sh.get("secondary")
            for role, pid in (("primary", pri), ("secondary", sec)):
                if pid is not None and str(pid) not in people:
                    problems.append(f"shift {k}: {role} {pid} is not in people")
            if pri is None:
                problems.append(f"shift {k}: no primary")
            shifts.append((a, b, None if pri is None else str(pri),
                           None if sec is None else str(sec)))
        season: list = []
        for k, ss in enumerate(obj.get("season") or []):
            try:
                season.append((_parse_time(ss["start"]),
                               _parse_time(ss["end"], end_of_day=True)))
            except Exception as exc:            # noqa: BLE001
                problems.append(f"season {k}: bad start/end ({exc})")
        return cls(people, shifts, season), problems

    def on_call(self, t_s: float) -> "tuple[Optional[str], Optional[str]]":
        """(primary, secondary) person ids on call at t_s (the covering shift
        that started last), (None, None) when no shift covers it."""
        best = None
        for a, b, pri, sec in self.shifts:
            if a <= t_s < b and (best is None or a > best[0]):
                best = (a, pri, sec)
        if best is None:
            return None, None
        pri = best[1] if best[1] in self.people else None
        sec = best[2] if best[2] in self.people else None
        return pri, sec

    def in_season(self, t_s: float) -> bool:
        return (not self.season
                or any(a <= t_s < b for a, b in self.season))

    def name(self, pid: Optional[str]) -> Optional[str]:
        if pid is None:
            return None
        p = self.people.get(pid) or {}
        return str(p.get("name") or pid)


class RotaSource:
    """Where the rota comes from: a local file (--rota PATH) or the S3 state
    mirror's oncall/rota.json; re-read at most every ROTA_RELOAD_S. A read
    that fails or does not parse keeps the last good rota (and says so)."""

    S3_NAME = "oncall/rota.json"

    def __init__(self, path: Optional[str] = None, mirror=None,
                 clock: Callable[[], float] = time.time,
                 reload_s: float = ROTA_RELOAD_S):
        self._path = path
        self._mirror = mirror
        self._clock = clock
        self._reload_s = float(reload_s)
        self._rota: Optional[Rota] = None
        self._digest: Optional[str] = None
        self._loaded_s: Optional[float] = None
        self._checked_s: Optional[float] = None
        self._error: Optional[str] = None
        self._problems: list = []

    @property
    def where(self) -> str:
        if self._path:
            return str(self._path)
        if self._mirror is not None:
            return f"s3://{self._mirror.bucket}/{self._mirror.key_for(self.S3_NAME)}"
        return "(none)"

    def _read(self) -> Optional[bytes]:
        if self._path:
            p = Path(self._path)
            return p.read_bytes() if p.exists() else None
        if self._mirror is not None:
            return self._mirror.download(self._mirror.key_for(self.S3_NAME))
        return None

    def rota(self, force: bool = False) -> Optional[Rota]:
        now = float(self._clock())
        if (not force and self._checked_s is not None
                and now - self._checked_s < self._reload_s):
            return self._rota
        self._checked_s = now
        try:
            body = self._read()
        except Exception as exc:                # noqa: BLE001
            self._error = f"read failed ({type(exc).__name__}: {str(exc)[:120]})"
            log.error("on-call rota: %s from %s — keeping the last good rota",
                      self._error, self.where)
            return self._rota
        if body is None:
            self._error = "no rota file"
            return self._rota
        digest = hashlib.sha256(body).hexdigest()
        if digest == self._digest:
            self._error = None
            return self._rota
        try:
            obj = json.loads(body.decode("utf-8"))
        except Exception as exc:                # noqa: BLE001
            self._error = f"not JSON ({type(exc).__name__})"
            log.error("on-call rota at %s is not JSON — keeping the last "
                      "good rota", self.where)
            return self._rota
        rota, problems = Rota.parse(obj)
        self._rota, self._digest = rota, digest
        self._loaded_s, self._error, self._problems = now, None, problems
        log.warning("on-call rota loaded from %s: %d people, %d shifts%s",
                    self.where, len(rota.people), len(rota.shifts),
                    f" — {len(problems)} problem(s)" if problems else "")
        return rota

    def status(self, now_s: float) -> dict:
        rota = self._rota
        pri, sec = rota.on_call(now_s) if rota else (None, None)
        return {"source": self.where, "loaded_s": self._loaded_s,
                "error": self._error, "problems": list(self._problems),
                "people": len(rota.people) if rota else 0,
                "shifts": len(rota.shifts) if rota else 0,
                "in_season": rota.in_season(now_s) if rota else None,
                "on_call": {"primary": pri,
                            "primary_name": rota.name(pri) if rota else None,
                            "secondary": sec,
                            "secondary_name": rota.name(sec) if rota else None}}


# ---------------------------------------------------------------------------
# the link
# ---------------------------------------------------------------------------

def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def page_ack_token(page_id: str, person_id: Optional[str], expires_s: float,
                   key: bytes) -> str:
    body = json.dumps({"p": page_id, "u": person_id,
                       "e": int(expires_s)}, separators=(",", ":")).encode()
    sig = hmac.new(key, body, hashlib.sha256).hexdigest()[:32]
    return f"{_b64(body)}.{sig}"


def check_ack_token(token: str, key: bytes, now_s: float) -> dict:
    """{"p": page id, "u": person id, "e": expiry}; raises ValueError when
    the token is malformed, forged or expired."""
    try:
        b64, sig = str(token).split(".", 1)
        body = _unb64(b64)
    except Exception as exc:                    # noqa: BLE001
        raise ValueError("malformed link") from exc
    want = hmac.new(key, body, hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(want, sig):
        raise ValueError("this link is not valid")
    obj = json.loads(body.decode("utf-8"))
    if float(obj.get("e", 0)) < now_s:
        raise ValueError("this link has expired")
    return obj


# ---------------------------------------------------------------------------
# channels
# ---------------------------------------------------------------------------

class DryChannel:
    """The 2026-11-19 channel: nothing leaves the system. The page and its
    targets are on the console; the log names the person id and tier only
    (never a contact detail or a link)."""

    name = "dry"

    def send(self, person_id: Optional[str], person: dict, page: dict,
             tier: str, link: Optional[str]) -> "tuple[bool, str]":
        log.warning("PAGE (dry channel — nothing sent): page %s to %s (%s): "
                    "%s", page["page_id"], person_id or "nobody on call",
                    tier, page["title"])
        return True, "dry: shown on the console"


# ---------------------------------------------------------------------------
# the pager
# ---------------------------------------------------------------------------

class Pager:
    """Pages, escalation, acknowledgement and the audit (module docstring).
    rota_source: a RotaSource (or None: nobody on call); channel: DryChannel
    or a real one; state_dir: where pages.json and the audit live (None =
    memory only); mirror: a persist.S3Mirror (None = local only)."""

    def __init__(self, cfg, rota_source: Optional[RotaSource] = None,
                 channel=None, state_dir: Optional[str] = None, mirror=None,
                 clock: Callable[[], float] = time.time,
                 key: Optional[bytes] = None,
                 base_url: Optional[str] = None):
        self._ack_s = float(getattr(cfg, "paging_ack_min", DEFAULT_ACK_MIN)
                            ) * 60.0
        self._repeat_max = int(getattr(cfg, "paging_repeat_max", 8))
        trig = str(getattr(cfg, "paging_triggers", ",".join(TRIGGERS)))
        self._triggers = {t.strip() for t in trig.split(",") if t.strip()}
        self._rota = rota_source
        self._channel = channel or DryChannel()
        self._dir = Path(state_dir) / "oncall" if state_dir else None
        self._mirror = mirror
        self._clock = clock
        env_key = os.environ.get("WGRESP_PAGE_KEY", "")
        self._key = key or (env_key.encode("utf-8") if env_key
                            else secrets.token_bytes(32))
        self.key_from_env = bool(env_key) and key is None
        self._base = (base_url if base_url is not None
                      else os.environ.get("WGRESP_PUBLIC_URL", "")).rstrip("/")
        self._lock = threading.RLock()
        self._pages: dict = {}          # page_id -> page dict
        self._order: list = []          # page ids, oldest first
        self._ticker: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # -- persistence ---------------------------------------------------------

    def restore(self) -> int:
        """Open pages (and the recent closed ones) back from pages.json —
        the local file, else the mirror. Returns how many came back."""
        body = None
        try:
            if self._dir is not None and (self._dir / "pages.json").exists():
                body = (self._dir / "pages.json").read_bytes()
            elif self._mirror is not None:
                body = self._mirror.download(
                    self._mirror.key_for("oncall/pages.json"))
        except Exception as exc:                # noqa: BLE001
            log.error("paging: could not restore pages (%s) — starting with "
                      "none", exc)
            return 0
        if not body:
            return 0
        try:
            rows = json.loads(body.decode("utf-8")).get("pages") or []
        except Exception:                       # noqa: BLE001
            log.error("paging: pages.json does not parse — starting with "
                      "none")
            return 0
        with self._lock:
            for p in rows:
                if isinstance(p, dict) and p.get("page_id"):
                    self._pages[p["page_id"]] = p
                    self._order.append(p["page_id"])
        return len(rows)

    def _persist(self) -> None:
        if self._dir is None:
            return
        with self._lock:
            keep = [self._pages[i] for i in self._order]
        body = json.dumps({"pages": keep}, sort_keys=True).encode("utf-8")
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._dir / "pages.json.tmp"
            tmp.write_bytes(body)
            os.replace(tmp, self._dir / "pages.json")
        except OSError as exc:
            log.error("paging: pages.json not written (%s)", exc)
            return
        if self._mirror is not None:
            self._mirror.upload(self._mirror.key_for("oncall/pages.json"),
                                body)

    def _audit(self, page: dict, what: str, now_s: float, **extra) -> None:
        row = {"t_s": now_s, "t": _iso(now_s), "page_id": page["page_id"],
               "key": page["key"], "what": what}
        row.update(extra)
        page.setdefault("history", []).append(
            {"t_s": now_s, "what": what,
             **{k: v for k, v in extra.items() if k != "title"}})
        if self._dir is None:
            return
        name = f"audit-{_iso(now_s)[:10]}.jsonl"
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with open(self._dir / name, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
            if self._mirror is not None:
                self._mirror.upload(self._mirror.key_for(f"oncall/{name}"),
                                    (self._dir / name).read_bytes())
        except OSError as exc:
            log.error("paging: audit row not written (%s)", exc)

    # -- triggers ------------------------------------------------------------

    def observe_operator_event(self, ev) -> Optional[str]:
        """satellite_heat (41) and new_fire_near_homes (60): a page (or an
        addition to the open one). Returns the page id, else None."""
        kind = getattr(ev, "kind", None)
        if kind == "satellite_heat" and "satellite_heat" in self._triggers:
            d = getattr(ev, "detail", None) or {}
            km = d.get("nearest_m")
            title = ("POTENTIAL FIRE — satellite heat"
                     + (f" {float(km) / 1000.0:.1f} km" if km is not None
                        else "")
                     + f" from {d.get('nearest_client') or 'a home'}"
                     + " (operator only; clients hear nothing until an agency"
                       " report or your Confirm)")
            return self._raise(f"satellite_heat:{ev.incident_id}", kind,
                               ev.incident_id, title, ev.message,
                               homes=[d.get("nearest_client_id")]
                               if d.get("nearest_client_id") else [])
        if (kind == "new_fire_near_homes"
                and "new_fire_near_homes" in self._triggers):
            d = getattr(ev, "detail", None) or {}
            title = (f"NEW AGENCY FIRE '{d.get('name') or ev.incident_id}' "
                     f"{d.get('km', '?')} km from home {d.get('client', '?')}"
                     f" ({d.get('source', '?')})")
            return self._raise(f"new_fire:{ev.incident_id}", kind,
                               ev.incident_id, title, ev.message,
                               homes=[d["client"]] if d.get("client") else [])
        return None

    def observe_cycle(self, res, names: Optional[dict] = None) -> list:
        """A live cycle's result: its satellite_heat events and every alert
        step UP to EXTREME_24H or IMPACTED (one page per fire and state,
        listing its homes). Returns the page ids raised or added to."""
        out: list = []
        for ev in getattr(res, "operator_events", None) or ():
            pid = self.observe_operator_event(ev)
            if pid:
                out.append(pid)
        if "alert_step_up" not in self._triggers:
            return out
        groups: dict = {}
        for e in getattr(res, "events", None) or ():
            st = getattr(e.state, "value", e.state)
            prev = getattr(e.prev, "value", e.prev)
            if st not in _STEP_UP_STATES:
                continue
            if _STEP_UP_STATES.index(st) <= (
                    _STEP_UP_STATES.index(prev) if prev in _STEP_UP_STATES
                    else -1):
                continue                        # not a step up
            groups.setdefault((e.incident_id, st), []).append(e)
        for (iid, st), evs in sorted(groups.items()):
            p10 = min((e.eta_p10_h for e in evs
                       if isinstance(e.eta_p10_h, (int, float))),
                      default=None)
            fire = (names or {}).get(iid) or iid
            homes = sorted({e.client_id for e in evs})
            title = (f"{st.replace('_', ' ').upper()} — {len(homes)} home(s)"
                     f" from {fire}"
                     + (f"; earliest pessimistic arrival {p10:.1f} h"
                        if p10 is not None else ""))
            pid = self._raise(f"alert:{iid}:{st}", "alert_step_up", iid,
                              title, evs[0].message or title, homes=homes)
            if pid:
                out.append(pid)
        return out

    def _raise(self, key: str, kind: str, incident_id, title: str,
               message: str, homes: list) -> Optional[str]:
        now = float(self._clock())
        with self._lock:
            for pid in reversed(self._order):
                p = self._pages[pid]
                if p["key"] != key:
                    continue
                if p["status"] in ("open", "unanswered"):
                    added = sorted(set(homes) - set(p["homes"]))
                    p["homes"] = sorted(set(p["homes"]) | set(homes))
                    p["message"] = message
                    if added:
                        self._audit(p, "added homes", now, homes=added)
                        self._persist()
                    return pid
                if (p["status"] == "acked" and p.get("acked_s") is not None
                        and now - p["acked_s"] < PAGE_REPEAT_QUIET_S):
                    self._audit(p, "repeat after acknowledgement (not paged "
                                   "again)", now, homes=sorted(homes))
                    self._persist()
                    return None
                break
            page = {"page_id": secrets.token_hex(6), "key": key,
                    "kind": kind, "incident_id": incident_id,
                    "title": title, "message": message,
                    "homes": sorted(set(h for h in homes if h)),
                    "created_s": now, "status": "open", "acked_s": None,
                    "acked_by": None, "acked_via": None, "sends": 0,
                    "escalation": 0, "targets": [], "history": []}
            self._pages[page["page_id"]] = page
            self._order.append(page["page_id"])
            self._audit(page, "raised", now, kind=kind, title=title)
            self._send_round(page, now, tiers=("primary",))
            page["next_due_s"] = now + self._ack_s
            self._trim()
            self._persist()
            return page["page_id"]

    # -- sending and escalation ---------------------------------------------

    def _send_round(self, page: dict, now: float, tiers: tuple) -> None:
        rota = self._rota.rota() if self._rota is not None else None
        pri, sec = rota.on_call(now) if rota else (None, None)
        who = {"primary": pri, "secondary": sec}
        sent_any = False
        for tier in tiers:
            pid = who.get(tier)
            if pid is None:
                continue
            person = (rota.people.get(pid) or {}) if rota else {}
            link = self.ack_link(page["page_id"], pid, now)
            try:
                ok, info = self._channel.send(pid, person, page, tier, link)
            except Exception as exc:            # noqa: BLE001
                ok, info = False, f"{type(exc).__name__}: {str(exc)[:120]}"
            page["targets"].append({"t_s": now, "person": pid,
                                    "name": rota.name(pid) if rota else pid,
                                    "tier": tier, "ok": bool(ok),
                                    "channel": getattr(self._channel, "name",
                                                       "?"),
                                    "info": info})
            self._audit(page, "sent" if ok else "send failed", now,
                        person=pid, tier=tier, info=info)
            sent_any = sent_any or bool(ok)
        page["sends"] += 1
        if not sent_any:
            self._audit(page, "nobody on call — on the console only", now,
                        tiers=list(tiers))

    def tick(self) -> int:
        """Escalate every open page past its due time. Returns how many
        rounds were sent. Called by the ticker (and the tests)."""
        now = float(self._clock())
        n = 0
        changed = False
        with self._lock:
            for pid in list(self._order):
                p = self._pages[pid]
                due = p.get("next_due_s")
                if p["status"] != "open" or due is None or now < due:
                    continue
                if p["sends"] >= self._repeat_max:
                    p["status"] = "unanswered"
                    p["next_due_s"] = None
                    self._audit(p, "UNANSWERED — no acknowledgement after "
                                   f"{p['sends']} rounds; stays on the "
                                   "console", now)
                    changed = True
                    continue
                p["escalation"] += 1
                self._audit(p, f"no acknowledgement in "
                               f"{self._ack_s / 60.0:g} min — escalation "
                               f"{p['escalation']}", now)
                self._send_round(p, now, tiers=("primary", "secondary"))
                p["next_due_s"] = now + self._ack_s
                n += 1
            if n or changed:
                self._persist()
        return n

    def start(self, every_s: float = 15.0) -> None:
        """The escalation ticker (a daemon thread): tick() every every_s."""
        if self._ticker is not None:
            return

        def run():
            while not self._stop.wait(every_s):
                try:
                    self.tick()
                except Exception:               # noqa: BLE001
                    log.exception("paging: escalation tick failed")
        self._ticker = threading.Thread(target=run, daemon=True,
                                        name="operator-paging")
        self._ticker.start()

    def stop(self) -> None:
        self._stop.set()

    # -- acknowledgement -----------------------------------------------------

    def ack_link(self, page_id: str, person_id: Optional[str],
                 now_s: float) -> str:
        tok = page_ack_token(page_id, person_id, now_s + LINK_TTL_S,
                             self._key)
        return f"{self._base}/page/ack?t={tok}"

    def check_link(self, token: str) -> dict:
        """The page a link names (a copy) with "link_person"; raises
        ValueError for a bad, forged or expired link or an unknown page."""
        now = float(self._clock())
        obj = check_ack_token(token, self._key, now)
        with self._lock:
            page = self._pages.get(obj.get("p"))
            if page is None:
                raise ValueError("this page is no longer known")
            out = json.loads(json.dumps(page))
        out["link_person"] = obj.get("u")
        rota = self._rota.rota() if self._rota is not None else None
        out["link_person_name"] = (rota.name(obj.get("u")) if rota
                                   else obj.get("u"))
        return out

    def ack(self, page_id: str, by: str, via: str) -> dict:
        """Acknowledge a page (the link's POST or the console). Idempotent:
        a second acknowledgement is recorded, the first one stands.
        Raises KeyError for an unknown page."""
        now = float(self._clock())
        with self._lock:
            page = self._pages[page_id]
            if page["status"] == "acked":
                self._audit(page, "acknowledged again", now, by=by, via=via)
                self._persist()
                return dict(page)
            page["status"] = "acked"
            page["acked_s"] = now
            page["acked_by"] = by
            page["acked_via"] = via
            page["next_due_s"] = None
            self._audit(page, "acknowledged", now, by=by, via=via,
                        after_s=round(now - page["created_s"], 1))
            self._persist()
            return dict(page)

    def ack_by_link(self, token: str) -> dict:
        obj = check_ack_token(token, self._key, float(self._clock()))
        rota = self._rota.rota() if self._rota is not None else None
        by = (rota.name(obj.get("u")) if rota else None) or obj.get("u") \
            or "link"
        return self.ack(obj["p"], by=f"{by} ({obj.get('u')})", via="link")

    # -- views ---------------------------------------------------------------

    def _trim(self) -> None:
        closed = [i for i in self._order
                  if self._pages[i]["status"] == "acked"]
        for pid in closed[:-KEEP_CLOSED] if len(closed) > KEEP_CLOSED else []:
            self._order.remove(pid)
            del self._pages[pid]

    def status(self) -> dict:
        """The /api/state "paging" block: the rota's status (who is on call,
        never a contact detail), the channel, and the pages (open first)."""
        now = float(self._clock())
        with self._lock:
            pages = [json.loads(json.dumps(self._pages[i]))
                     for i in reversed(self._order)]
        for p in pages:
            for t in p.get("targets", []):
                t.pop("link", None)
        open_ = [p for p in pages if p["status"] in ("open", "unanswered")]
        rest = [p for p in pages if p["status"] not in ("open", "unanswered")]
        return {"channel": getattr(self._channel, "name", "?"),
                "ack_min": self._ack_s / 60.0,
                "repeat_max": self._repeat_max,
                "triggers": sorted(self._triggers),
                "links_survive_restart": self.key_from_env,
                "rota": (self._rota.status(now) if self._rota is not None
                         else {"source": "(none)", "on_call": {}}),
                "open": len(open_),
                "pages": open_ + rest[:20]}

    def open_count(self) -> int:
        with self._lock:
            return sum(1 for i in self._order
                       if self._pages[i]["status"] in ("open", "unanswered"))
