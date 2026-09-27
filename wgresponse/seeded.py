"""wgresponse.seeded — W1 seeded incidents: fires a user lights in the live world.

docs/LIVE_WORLD_PLAN.md W1 ticket 1.2 ("the simulator's ignite becomes 'seed
into the world'"); contracts SAFETY SEMANTICS 12 is the law this module
implements. A seed is a contracts.SeededIgnition; the watch daemon's
POST /api/test/seed creates one (TWO WORLDS, SAFETY SEMANTICS 16: the TEST
world's ThreatMonitor — the live one refuses seeds), the monitor holds them in
a SeededIncidentStore and unions them into every cycle's incident set, where
they run through the SAME code as a feed incident (domain, ensemble, ETAs,
ladder, dispatch).

    SeededIncidentStore   thread-safe add / retire / list (the HTTP threads
                          write, the cycle thread reads). MAX_ACTIVE cap;
                          retired seeds kept (bounded) for the audit list.
                          .rows() / .restore(rows) are the persistence field
                          map (the StateStore stores the rows verbatim).
    mint_seed_id          'seeded:<uuid5>' — a NAME-BASED uuid over (store
                          sequence, t_ignite_s, lat, lon), so a replayed
                          command script re-mints the same ids (the ensemble
                          is seeded on the incident id: contracts determinism).
    seeded_forecast       the per-incident weather override (SEMANTICS 12f)
                          through the weather seam, for that incident only.
    parse_seed_request    POST /api/test/seed body -> validated create()
                          kwargs (ValueError -> HTTP 400).
    SeedSafeNotifier      routes every seeded incident's notification to the
                          drill log — never a client channel (SEMANTICS 12g).
    DrillOnlyNotifier     TWO WORLDS (SEMANTICS 16e): the TEST world's
                          notifier — holds NO client channel; every send
                          (seeded or a live incident seen from the test
                          world) goes to the drill log and is ledgered.
    LiveWorldRefused      a seed offered to the LIVE world (SEMANTICS 16a;
                          HTTP 409 with the /api/test/seed pointer).

TWO WORLDS (2026-09-27, contracts SAFETY SEMANTICS 16): seeds are lit only
in the TEST world (wgresponse.worlds); the live world's monitor is locked
and refuses them.

Units per contracts: unix seconds, degrees, m/s, percent, Celsius. Stdlib
only; the simulator's intensity presets (simui.INTENSITY_WEATHER) are read
lazily so importing this module never imports the simulator.
"""
from __future__ import annotations

import dataclasses
import logging
import math
import threading
import uuid
from collections import deque
from typing import Optional

from .contracts import (
    SEED_INTENSITIES,
    SEEDED_ID_PREFIX,
    LatLon,
    SeededIgnition,
    SeedWeather,
    WeatherForecast,
    WeatherSample,
    is_seeded_id,
)

log = logging.getLogger(__name__)

#: Name-based uuid namespace for seed ids (a fixed constant: ids are
#: reproducible from the command script, never from os.urandom).
_SEED_NS = uuid.uuid5(uuid.NAMESPACE_URL,
                      "https://thewildfireguardian.com/wgresponse/seeded")

#: 1 mph in m/s — the simulator's constant (simui.MPH_TO_MPS), repeated here
#: only for human labels; the physics reads simui's own value.
_MPH = 0.44704

#: Retirement reasons (SEMANTICS 12e).
RETIRED_BY_OPERATOR = "operator"
RETIRED_AT_HORIZON = "horizon"


class SeedConflict(Exception):
    """A seed request that is well-formed but cannot apply (HTTP 409)."""


class SeedCapacityError(SeedConflict):
    """Too many active seeds (SeededIncidentStore.max_active)."""


class SeedAlreadyRetired(SeedConflict):
    """Retire on a seed that is already retired."""


#: The pointer every live-world refusal carries (SAFETY SEMANTICS 16a).
TEST_SEED_ROUTE = "/api/test/seed"
LIVE_REFUSAL = ("the LIVE world accepts no seeded fires (TWO WORLDS, contracts "
                "SAFETY SEMANTICS 16): drills and simulations run on the TEST "
                "map — POST " + TEST_SEED_ROUTE + " (the /world page: switch "
                "to TEST)")


class LiveWorldRefused(SeedConflict):
    """A seed offered to the LIVE world (SAFETY SEMANTICS 16a) — HTTP 409.
    The live world's ThreatMonitor is locked (lock_live) and refuses every
    seed; drills belong to the TEST world."""

    def __init__(self, msg: str = LIVE_REFUSAL) -> None:
        super().__init__(msg)


def norm_dir_deg(v: float) -> float:
    """A compass bearing in [0, 360): x % 360 can round UP to exactly 360.0
    for a tiny negative x (-1e-15 % 360.0 == 360.0), which is north again."""
    d = float(v) % 360.0
    return 0.0 if d >= 360.0 else d


def mint_seed_id(seq: int, t_ignite_s: float, lat: float, lon: float) -> str:
    """'seeded:<uuid>' — uuid5 over the store sequence number and the
    ignition (time, point). Deterministic: the same command script against
    the same clock mints the same ids, so seeded ensembles (seeded on the
    incident id) replay byte-identically."""
    name = f"{int(seq)}|{float(t_ignite_s)!r}|{float(lat)!r}|{float(lon)!r}"
    return SEEDED_ID_PREFIX + str(uuid.uuid5(_SEED_NS, name))


@dataclasses.dataclass
class SeedEntry:
    """One seed in the store: the ignition as lit + its lifecycle."""
    ignition: SeededIgnition
    seq: int = 0
    retired_at_s: Optional[float] = None
    retired_reason: str = ""
    retired_by: str = ""

    @property
    def active(self) -> bool:
        return self.retired_at_s is None


# ---------------------------------------------------------------------------
# persistence field map (explicit — no pickle, no eval)
# ---------------------------------------------------------------------------

def encode_entry(e: SeedEntry) -> dict:
    """SeedEntry -> JSON-able row (the StateStore stores rows verbatim)."""
    ign = e.ignition
    w = ign.weather
    return {
        "incident_id": ign.incident_id,
        "lat": float(ign.loc.lat), "lon": float(ign.loc.lon),
        "t_ignite_s": float(ign.t_ignite_s),
        "intensity": ign.intensity,
        "weather": (None if w is None else {
            "wind_speed_mps": float(w.wind_speed_mps),
            "wind_gust_mps": float(w.wind_gust_mps),
            "rh_pct": float(w.rh_pct), "temp_c": float(w.temp_c)}),
        "wind_dir_deg": (None if ign.wind_dir_deg is None
                         else float(ign.wind_dir_deg)),
        "created_by": ign.created_by,
        "note": ign.note,
        "seq": int(e.seq),
        "retired_at_s": (None if e.retired_at_s is None
                         else float(e.retired_at_s)),
        "retired_reason": e.retired_reason,
        "retired_by": e.retired_by,
    }


def decode_entry(row: dict) -> SeedEntry:
    """Row -> SeedEntry; raises on any malformed field (the caller skips the
    row loudly — one bad row must not drop every other seed)."""
    w = row.get("weather")
    weather = None if w is None else SeedWeather(
        wind_speed_mps=float(w["wind_speed_mps"]),
        wind_gust_mps=float(w["wind_gust_mps"]),
        rh_pct=float(w["rh_pct"]), temp_c=float(w["temp_c"]))
    wd = row.get("wind_dir_deg")
    ign = SeededIgnition(
        incident_id=str(row["incident_id"]),
        loc=LatLon(float(row["lat"]), float(row["lon"])),
        t_ignite_s=float(row["t_ignite_s"]),
        intensity=row.get("intensity"),
        weather=weather,
        wind_dir_deg=None if wd is None else float(wd),
        created_by=str(row.get("created_by") or ""),
        note=str(row.get("note") or ""))
    problems = ign.problems()
    if problems:
        raise ValueError("; ".join(problems))
    ret = row.get("retired_at_s")
    return SeedEntry(ignition=ign, seq=int(row.get("seq") or 0),
                     retired_at_s=None if ret is None else float(ret),
                     retired_reason=str(row.get("retired_reason") or ""),
                     retired_by=str(row.get("retired_by") or ""))


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------

class SeededIncidentStore:
    """Thread-safe seeded-incident store (SAFETY SEMANTICS 12).

    Entries keep creation order (the cycle unions active seeds in that
    order, after the fused feed incidents). At most ``max_active`` seeds are
    active at once (each one with clients in range builds a domain every
    cycle); retired seeds stay listed for audit, the oldest pruned beyond
    ``retired_keep``. Every method takes the lock; reads return copies."""

    MAX_ACTIVE = 12
    RETIRED_KEEP = 50

    def __init__(self, max_active: int = MAX_ACTIVE,
                 retired_keep: int = RETIRED_KEEP) -> None:
        self._lock = threading.RLock()
        self._entries: "dict[str, SeedEntry]" = {}
        self._seq = 0
        self._announce: list = []         # operator retirements to report
        self.max_active = int(max_active)
        self.retired_keep = int(retired_keep)

    # -- queries ------------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def has_active(self) -> bool:
        with self._lock:
            return any(e.active for e in self._entries.values())

    def active(self) -> "list[SeededIgnition]":
        """Active ignitions, creation order."""
        with self._lock:
            return [e.ignition for e in self._entries.values() if e.active]

    def entries(self) -> "list[SeedEntry]":
        """Every entry (active + retired), creation order, as copies."""
        with self._lock:
            return [dataclasses.replace(e) for e in self._entries.values()]

    def entry(self, incident_id: str) -> Optional[SeedEntry]:
        with self._lock:
            e = self._entries.get(incident_id)
            return None if e is None else dataclasses.replace(e)

    def ignition(self, incident_id: str) -> Optional[SeededIgnition]:
        """The ignition for an id, active OR retired (a seed retired while a
        cycle is running still spreads under its own weather that cycle)."""
        with self._lock:
            e = self._entries.get(incident_id)
            return None if e is None else e.ignition

    # -- mutations ----------------------------------------------------------

    def create(self, lat: float, lon: float, t_ignite_s: float,
               intensity: Optional[str] = None,
               weather: Optional[SeedWeather] = None,
               wind_dir_deg: Optional[float] = None,
               created_by: str = "", note: str = "") -> SeededIgnition:
        """Mint an id (mint_seed_id) and add the seed. ValueError on an
        invalid ignition; SeedCapacityError when max_active are active."""
        with self._lock:
            seq = self._seq + 1
            wd = None if wind_dir_deg is None else norm_dir_deg(wind_dir_deg)
            ign = SeededIgnition(
                incident_id=mint_seed_id(seq, t_ignite_s, lat, lon),
                loc=LatLon(float(lat), float(lon)),
                t_ignite_s=float(t_ignite_s), intensity=intensity,
                weather=weather, wind_dir_deg=wd,
                created_by=str(created_by or ""), note=str(note or ""))
            return self._add(ign, seq)

    def add(self, ign: SeededIgnition) -> SeededIgnition:
        """Add a pre-built ignition (tests, restore tooling). Same checks as
        create(): ValueError / SeedConflict (duplicate id) /
        SeedCapacityError."""
        with self._lock:
            return self._add(ign, self._seq + 1)

    def _add(self, ign: SeededIgnition, seq: int) -> SeededIgnition:
        problems = ign.problems()
        if problems:
            raise ValueError("; ".join(problems))
        if ign.incident_id in self._entries:
            raise SeedConflict(f"seed {ign.incident_id} already exists")
        n_active = sum(1 for e in self._entries.values() if e.active)
        if n_active >= self.max_active:
            raise SeedCapacityError(
                f"{n_active} seeded fires are already active (max "
                f"{self.max_active}); retire one first (DELETE "
                f"{TEST_SEED_ROUTE}/<id>)")
        self._entries[ign.incident_id] = SeedEntry(ignition=ign, seq=int(seq))
        self._seq = max(self._seq, int(seq))
        return ign

    def retire(self, incident_id: str, now_s: float,
               reason: str = RETIRED_BY_OPERATOR, by: str = "",
               announce: bool = True) -> SeedEntry:
        """Retire an active seed at now_s (SEMANTICS 12e). KeyError when
        unknown; SeedAlreadyRetired when already retired. announce=True
        queues it for the next cycle's note (take_announcements)."""
        with self._lock:
            e = self._entries.get(incident_id)
            if e is None:
                raise KeyError(incident_id)
            if not e.active:
                raise SeedAlreadyRetired(
                    f"seed {incident_id} was already retired at "
                    f"{e.retired_at_s:.0f} ({e.retired_reason})")
            e.retired_at_s = float(now_s)
            e.retired_reason = str(reason)
            e.retired_by = str(by or "")
            out = dataclasses.replace(e)
            if announce:
                self._announce.append(out)
            self._prune_retired()
            return out

    def take_announcements(self) -> "list[SeedEntry]":
        """Drain the retirements queued since the last call (cycle notes)."""
        with self._lock:
            out, self._announce = self._announce, []
            return out

    def _prune_retired(self) -> None:
        retired = [e for e in self._entries.values() if not e.active]
        extra = len(retired) - self.retired_keep
        if extra <= 0:
            return
        retired.sort(key=lambda e: (e.retired_at_s, e.seq))
        for e in retired[:extra]:
            del self._entries[e.ignition.incident_id]

    # -- persistence ----------------------------------------------------------

    def rows(self) -> "list[dict]":
        """Every entry as a persistence row (creation order)."""
        with self._lock:
            return [encode_entry(e) for e in self._entries.values()]

    def restore(self, rows) -> int:
        """Replace the store with persisted rows; returns the number restored.
        Malformed rows are SKIPPED LOUDLY (never raised): losing one seed to
        corruption must not drop the others, and a bad file must never kill
        the daemon at startup. The cap is not applied to restored seeds (they
        were admitted under it)."""
        entries: "dict[str, SeedEntry]" = {}
        if not isinstance(rows, list):
            if rows is not None:
                log.error("seeded: persisted seeds are %s, not a list — "
                          "starting with NO seeds", type(rows).__name__)
            rows = []
        for i, row in enumerate(rows):
            try:
                e = decode_entry(row)
            except Exception as exc:
                log.error("seeded: skipping malformed persisted seed #%d "
                          "(%s)", i, exc)
                continue
            if e.ignition.incident_id in entries:
                log.error("seeded: duplicate persisted seed %s skipped",
                          e.ignition.incident_id)
                continue
            entries[e.ignition.incident_id] = e
        with self._lock:
            self._entries = entries
            self._seq = max([self._seq] + [e.seq for e in entries.values()])
            self._announce = []
        return len(entries)


# ---------------------------------------------------------------------------
# weather (SEMANTICS 12f)
# ---------------------------------------------------------------------------

def preset_weather(intensity: str) -> SeedWeather:
    """The simulator's preset conditions (simui.INTENSITY_WEATHER: sustained
    mph -> m/s, gust = sustained x simui.GUST_FACTOR, RH, temperature)."""
    from wgresponse import simui      # lazy: the simulator owns the presets
    spec = simui.INTENSITY_WEATHER[intensity]
    w = spec["wind_mph"] * simui.MPH_TO_MPS
    return SeedWeather(wind_speed_mps=w, wind_gust_mps=w * simui.GUST_FACTOR,
                       rh_pct=float(spec["rh_pct"]),
                       temp_c=float(spec["temp_c"]))


def seed_conditions(ign: SeededIgnition) -> Optional[SeedWeather]:
    """The override conditions (explicit weather, else the preset), or None
    for live conditions."""
    if ign.weather is not None:
        return ign.weather
    if ign.intensity is not None:
        return preset_weather(ign.intensity)
    return None


def weather_label(ign: SeededIgnition) -> str:
    """One human line naming the weather that drives this seed."""
    cond = seed_conditions(ign)
    if cond is None:
        parts = ["live conditions (the watcher's weather provider at the "
                 "fire — NWS gridpoint in production)"]
    else:
        src = (f"preset '{ign.intensity}'" if ign.intensity is not None
               else "explicit")
        parts = [f"{src} conditions {cond.wind_speed_mps:.1f} m/s "
                 f"({cond.wind_speed_mps / _MPH:.0f} mph) sustained, gust "
                 f"{cond.wind_gust_mps:.1f} m/s, RH {cond.rh_pct:g}%, "
                 f"{cond.temp_c:g} C in every hour"]
    if ign.wind_dir_deg is None:
        parts.append("wind direction live (hourly)")
    else:
        parts.append(f"wind FROM {ign.wind_dir_deg:g} deg in every hour")
    if cond is not None and ign.wind_dir_deg is not None:
        parts.append("fully synthetic (the simulator's MockWeather script, "
                     "no network)")
    return f"{ign.weather_mode()}: " + "; ".join(parts)


def seeded_forecast(ign: SeededIgnition, live_provider, loc: LatLon,
                    horizon_h: float, now_s: float) -> WeatherForecast:
    """The fire-weather forecast for ONE seeded incident (SEMANTICS 12f).

    live (no override)  -> live_provider.forecast(loc, horizon_h), untouched
                           (the same call every feed incident gets).
    conditions + dir    -> fully synthetic, anchored at now_s: a preset runs
                           the simulator's exact MockWeather script
                           (simui._rebuild_weather's construction); explicit
                           weather the same constant sample every hour.
    one part overridden -> the live forecast with the conditions (sustained,
                           gust, RH, temperature) or the direction replaced in
                           every hourly sample; the live sample times stay.
    Every other incident keeps calling the live provider directly."""
    cond = seed_conditions(ign)
    wdir = ign.wind_dir_deg
    if cond is None and wdir is None:
        return live_provider.forecast(loc, horizon_h)
    tag = "seeded-" + ign.weather_mode()
    if cond is not None and wdir is not None:
        if ign.intensity is not None:
            from wgresponse import simui
            from wgresponse.weather import MockWeather
            spec = simui.INTENSITY_WEATHER[ign.intensity]
            wxf = MockWeather(
                seed=f"seeded|{ign.incident_id}|wx",
                base_wind_mps=spec["wind_mph"] * simui.MPH_TO_MPS,
                dir_deg=wdir, rh_pct=spec["rh_pct"], temp_c=spec["temp_c"],
                gust_factor=simui.GUST_FACTOR,
                t0_s=float(now_s)).forecast(loc, horizon_h)
        else:
            n = max(1, int(horizon_h) + 1)
            wxf = WeatherForecast(loc=loc, samples=[
                WeatherSample(t_s=float(now_s) + h * 3600.0,
                              wind_speed_mps=cond.wind_speed_mps,
                              wind_gust_mps=cond.wind_gust_mps,
                              wind_dir_deg=float(wdir) % 360.0,
                              rh_pct=cond.rh_pct, temp_c=cond.temp_c)
                for h in range(n)])
        wxf.source = tag
        return wxf
    base = live_provider.forecast(loc, horizon_h)
    samples = []
    for s in base.samples:
        kw = {}
        if cond is not None:
            kw.update(wind_speed_mps=cond.wind_speed_mps,
                      wind_gust_mps=cond.wind_gust_mps,
                      rh_pct=cond.rh_pct, temp_c=cond.temp_c)
        if wdir is not None:
            kw["wind_dir_deg"] = float(wdir) % 360.0
        samples.append(dataclasses.replace(s, **kw))
    return WeatherForecast(loc=base.loc, samples=samples,
                           source=f"{base.source}+{tag}")


# ---------------------------------------------------------------------------
# the POST /api/test/seed body (the W1 POST /api/seed body, unchanged)
# ---------------------------------------------------------------------------

SEED_REQUEST_KEYS = ("lat", "lon", "intensity", "wind_dir_deg", "weather",
                     "note", "created_by")
_WEATHER_KEYS = ("wind_speed_mps", "wind_gust_mps", "rh_pct", "temp_c")


def _number(payload: dict, key: str, where: str = "") -> float:
    v = payload.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{where}{key} must be a number")
    v = float(v)
    if not math.isfinite(v):
        raise ValueError(f"{where}{key} must be finite")
    return v


def parse_seed_request(payload, aoi=None) -> dict:
    """Validate a POST /api/test/seed body into SeededIncidentStore.create
    kwargs (minus t_ignite_s, which is the TEST world's clock). Raises
    ValueError with the reason (HTTP 400) on: a non-object body, unknown
    keys, missing /
    non-numeric / non-finite / out-of-range lat-lon, a point outside the
    watch AOI, an unknown intensity, a non-finite wind_dir_deg, a malformed
    or out-of-range explicit weather object, intensity AND weather together,
    over-long note / created_by."""
    if not isinstance(payload, dict):
        raise ValueError("body must be a JSON object")
    unknown = sorted(set(payload) - set(SEED_REQUEST_KEYS))
    if unknown:
        raise ValueError(f"unknown seed keys {unknown}; allowed: "
                         f"{list(SEED_REQUEST_KEYS)}")
    if "lat" not in payload or "lon" not in payload:
        raise ValueError("lat and lon are required numbers")
    lat = _number(payload, "lat")
    lon = _number(payload, "lon")
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError("lat/lon out of range (lat ±90, lon ±180)")
    if aoi is not None and not aoi.contains(LatLon(lat, lon)):
        raise ValueError(
            f"({lat:.5f}, {lon:.5f}) is outside the watch AOI "
            f"[{aoi.west:g}, {aoi.south:g}, {aoi.east:g}, {aoi.north:g}] — "
            f"the watcher seeds only where it watches")
    intensity = payload.get("intensity")
    if intensity is not None and intensity not in SEED_INTENSITIES:
        raise ValueError(f"intensity must be one of {list(SEED_INTENSITIES)} "
                         f"or omitted (live conditions)")
    wind = None
    if payload.get("wind_dir_deg") is not None:
        wind = norm_dir_deg(_number(payload, "wind_dir_deg"))
    weather = None
    raw_w = payload.get("weather")
    if raw_w is not None:
        if not isinstance(raw_w, dict):
            raise ValueError("weather must be an object {wind_speed_mps, "
                             "rh_pct, temp_c, wind_gust_mps?}")
        bad = sorted(set(raw_w) - set(_WEATHER_KEYS))
        if bad:
            raise ValueError(f"unknown weather keys {bad}; allowed: "
                             f"{list(_WEATHER_KEYS)}")
        for k in ("wind_speed_mps", "rh_pct", "temp_c"):
            if k not in raw_w:
                raise ValueError(f"weather.{k} is required")
        ws = _number(raw_w, "wind_speed_mps", "weather.")
        if raw_w.get("wind_gust_mps") is None:
            from wgresponse import simui    # the simulator's gust factor
            gust = ws * simui.GUST_FACTOR
        else:
            gust = _number(raw_w, "wind_gust_mps", "weather.")
        weather = SeedWeather(wind_speed_mps=ws, wind_gust_mps=gust,
                              rh_pct=_number(raw_w, "rh_pct", "weather."),
                              temp_c=_number(raw_w, "temp_c", "weather."))
        problems = weather.problems()
        if problems:
            raise ValueError("; ".join(problems))
        if intensity is not None:
            raise ValueError("intensity and weather are exclusive — each "
                             "sets the fire's conditions")
    note = payload.get("note", "")
    if note is None:
        note = ""
    if not isinstance(note, str) or len(note) > 280:
        raise ValueError("note must be a string of at most 280 characters")
    who = payload.get("created_by", "api")
    if who is None:
        who = "api"
    if not isinstance(who, str) or not who.strip() or len(who) > 64:
        raise ValueError("created_by must be a non-empty string of at most "
                         "64 characters")
    return {"lat": lat, "lon": lon, "intensity": intensity,
            "weather": weather, "wind_dir_deg": wind,
            "note": note.strip(), "created_by": who.strip()}


# ---------------------------------------------------------------------------
# notifications (SEMANTICS 12g)
# ---------------------------------------------------------------------------

DRILL_LOG_KEEP = 200


class DrillLog:
    """Log-only notifier: every send is logged loudly as a DRILL and reported
    delivered (so the ledger records it and a restart re-fires nothing).
    Keeps the last DRILL_LOG_KEEP sends for inspection: ``sent`` holds this
    process's (event, client) pairs, ``rows()`` the JSON-able lines (the
    TEST world persists and serves them — SAFETY SEMANTICS 16e/f).
    ``label`` names the world in the log line."""

    def __init__(self, label: str = "", keep: int = DRILL_LOG_KEEP) -> None:
        self.sent: deque = deque(maxlen=int(keep))
        self._rows: deque = deque(maxlen=int(keep))
        self._label = str(label or "")
        self._lock = threading.Lock()

    def send(self, event, client) -> bool:
        row = {"t_s": float(event.t_s), "client_id": event.client_id,
               "incident_id": event.incident_id,
               "prev": getattr(event.prev, "value", str(event.prev)),
               "state": getattr(event.state, "value", str(event.state)),
               "message": event.message}
        with self._lock:
            self.sent.append((event, client))
            self._rows.append(row)
        if self._label:
            log.warning("DRILL (%s, incident %s) — NOT sent to any client "
                        "channel: %s->%s client=%s: %s", self._label,
                        event.incident_id, row["prev"], row["state"],
                        event.client_id, event.message)
        else:
            log.warning("DRILL (seeded incident %s) — NOT sent to any client "
                        "channel: %s->%s client=%s: %s", event.incident_id,
                        row["prev"], row["state"], event.client_id,
                        event.message)
        return True

    def rows(self) -> "list[dict]":
        """The drill lines, oldest first (JSON-able copies)."""
        with self._lock:
            return [dict(r) for r in self._rows]

    def restore(self, rows) -> int:
        """Refill the display lines from persisted rows (malformed skipped
        loudly; ``sent`` is this process's and starts empty)."""
        good = []
        for i, r in enumerate(rows or []):
            try:
                good.append({"t_s": float(r["t_s"]),
                             "client_id": str(r["client_id"]),
                             "incident_id": str(r["incident_id"]),
                             "prev": str(r["prev"]), "state": str(r["state"]),
                             "message": str(r.get("message") or "")})
            except Exception as exc:
                log.error("drill log: skipping malformed persisted line #%d "
                          "(%s)", i, exc)
        with self._lock:
            self._rows.clear()
            self._rows.extend(good)
        return len(good)


_DrillLog = DrillLog            # the W1 name (kept for callers and tests)


class SeedSafeNotifier:
    """contracts.Notifier wrapper: a notification of a SEEDED incident goes to
    the drill log, never to ``real`` (SES / SMS / whatever the daemon runs);
    every other notification is ``real``'s, unchanged. The ladder, its
    AlertEvents and the notified ledger are untouched — only the channel.
    Since TWO WORLDS (SAFETY SEMANTICS 16) the live world never carries a
    seed; the watch daemon keeps this wrapper on the live notifier as
    defense in depth."""

    def __init__(self, real, drill=None) -> None:
        self._real = real
        self.drill = drill if drill is not None else DrillLog()

    def send(self, event, client) -> bool:
        if is_seeded_id(getattr(event, "incident_id", None)):
            return bool(self.drill.send(event, client))
        return self._real.send(event, client)


class DrillOnlyNotifier:
    """The TEST world's notifier (TWO WORLDS, SAFETY SEMANTICS 16e): it holds
    NO client channel at all — there is nothing real to send to — so every
    notification, a seeded fire's or a LIVE incident's as seen from the test
    world, goes to the drill log and reports delivered (ledgered: a restart
    re-fires nothing). Structural, not a filter: no code path here can reach
    SES, SMS or any other client channel."""

    def __init__(self, drill=None) -> None:
        self.drill = drill if drill is not None else DrillLog(label="TEST MAP")

    def send(self, event, client) -> bool:
        return bool(self.drill.send(event, client))
