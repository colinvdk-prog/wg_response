"""wgresponse.server — stdlib HTTP app: beacon ingest + ops-console state API.

Endpoints:
    POST /api/beacon   JSON body -> CrewBeacon -> beacon sink (auth required)
    GET  /api/state    full ops state JSON from the injected state_provider (auth)
    GET  /api/plan     latest dispatch plan JSON (auth)
    GET  /console      serves console/index.html (open: it is the key-prompt UI
                       and contains no data; every data endpoint stays authed)
    GET  /simulator    serves console/simulator.html (open, same rationale)
    GET  /healthz      liveness probe (always open). When the state_provider's
                       dict carries degradation keys (SAFETY SEMANTICS 6:
                       `degraded` bool, `t_s`/`now_s`/`last_cycle_t_s` unix s,
                       `operator_events` list) healthz reflects the COARSE
                       signal — degraded flag, last-cycle timestamp, operator-
                       event count — never event bodies (endpoint is open;
                       details stay behind the authed /api/state).

Interactive simulator API (SIM-3; docs/SIMULATOR_DESIGN.md; wgresponse.simui
runs the REAL engine — nothing here forks physics). All authed:
    POST /api/sim/new           {location, clients, crews, seed?, cfg?,
                                fire_zone?, crews_1h?, crews_3h?,
                                spotting?, grass_realism?}
                                -> {session_id, state}
                                (fire_zone 'standard'|'extended', contracts
                                FIRE ZONE block; unknown value -> 400; the
                                state echoes the zone that actually loaded.
                                crews_1h/crews_3h = out-of-region mutual-aid
                                reinforcement counts, contracts FOUNDER ROUND 5
                                SEMANTICS 9; default 0, each 0..12, negative/
                                non-int -> 400; `crews` stays the in-zone count.
                                reroll_clients preserves all three tiers.
                                spotting/grass_realism = realistic-fire physics
                                toggles (contracts SAFETY SEMANTICS 11 ember
                                spotting + cfg.grass_ros_floor_frac grass floor);
                                BOTH default TRUE on this demo path (a direct
                                simui.SimSession leaves both OFF); non-bool ->
                                400; mapped to cfg.spotting_enabled and
                                cfg.grass_ros_floor_frac (0.10 on / 0.0 off) and
                                echoed as state.spotting / state.grass_realism;
                                reset + reroll_clients preserve them)
    POST /api/sim/<id>/ignite   {lat, lon, intensity?, wind_dir_deg?}
    POST /api/sim/<id>/config   {egress_buffer_min?, slack_min?, speed?}
                                (validated; echoes what was applied)
    POST /api/sim/<id>/ctl      {action: run|pause|step|reset|reroll_clients|
                                goto}  (goto {tick} returns the recorded
                                tick-k snapshot for the UI scrub — LAW 1)
    GET  /api/sim/<id>/state    the live current state [?safety=1 adds the
                                eroded-P10 safety surface]; ?tick=k INSTEAD
                                returns SimSession.state_at(k) — the recorded
                                snapshot served live at tick k, VERBATIM
                                (contracts FOUNDER ROUND 3 LAW 1 TIME SCRUB).
                                404 (JSON error) when k is non-integer, evicted
                                (older than cfg.history_ticks), or still in the
                                future; no tick param -> the live state
                                (unchanged). A scrubbed frame is read-only: it
                                carries the tick-k picture verbatim (its own
                                history.current_tick == k) and never the live
                                server clock keys / safety surface.
Sessions live in an in-memory LRU dict (cap 4); a background ticker thread
per RUNNING session advances the engine (run = auto-tick at speed, step =
single tick). Malformed JSON -> 400; unknown session -> 404.

State providers wired to a live ThreatMonitor should include the CycleResult's
`degraded`, `operator_events`, and `t_s` in the state dict so /api/state and
the console degradation banner surface provider outages.

Auth (fail-closed): request header X-Internal-API-Key must equal env
WGRESP_API_KEY. When WGRESP_API_KEY is unset every authed endpoint returns 401
(fail closed) unless env WGRESP_DEV_OPEN=1. Env is read per-request so tests
and operators can rotate keys without restarting.

CORS: same-origin only — no CORS headers are ever emitted.

All times in payloads are unix seconds; positions are lat/lon degrees.
"""
from __future__ import annotations

import hmac
import json
import logging
import math
import os
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from wgresponse import geo, simui
from wgresponse.contracts import CrewBeacon, CrewStatus, LatLon
from wgresponse.sim import _trace_contour, jsonable

_LOG = logging.getLogger("wgresponse.server")

_CONSOLE_DIR = Path(__file__).resolve().parent.parent / "console"
_DEFAULT_CONSOLE = _CONSOLE_DIR / "index.html"
_DEFAULT_SIMULATOR = _CONSOLE_DIR / "simulator.html"
_MAX_BODY_BYTES = 1_000_000


#: ONE-CLICK DEMO SCENARIOS (founder 2026-07-12: "a map and scenario for a
#: high risk area in Utah and in Colorado"). POST /api/sim/new {scenario: KEY}
#: fills location/clients/crews/reinforcements AND ignites the preset fire
#: (deterministic seed -> identical demo every run); the simulator UI accepts
#: /simulator?scenario=KEY and auto-runs. Explicit payload keys still win.
SCENARIOS = {
    # Park City, UT — dry SW-flow wind event: ignition in the oak/brush SW of
    # the Snyderville/Park City interface, running NE into the resort WUI.
    "park_city_windstorm": {
        "location": "park_city_ut", "clients": 40, "crews": 2, "crews_1h": 1,
        "intensity": "extreme", "ign_bearing": 225.0, "ign_dist_m": 7000.0,
    },
    # Boulder County, CO — Marshall-style downslope WEST wind grass fire:
    # ignition west of the Boulder–Louisville interface, running east through
    # cured grass into the suburbs.
    "boulder_chinook": {
        "location": "boulder_co", "clients": 40, "crews": 2, "crews_1h": 1,
        "intensity": "extreme", "ign_bearing": 270.0, "ign_dist_m": 6500.0,
    },
    # Rancho Santa Fe, CA — Witch Creek-style NE Santa Ana: ignition in the
    # grass-shrub NE of the Covenant, running SW into 100 protected estates
    # with the plan-of-record fleet (docs/rancho_santa_fe_plan.md: 10 crews,
    # clock-bound response; measured ~62% ember-belt even at extreme).
    "rancho_santa_fe_santa_ana": {
        "location": "rancho_santa_fe_ca", "clients": 100, "crews": 10,
        "crews_1h": 2, "intensity": "extreme", "ign_bearing": 60.0,
        "ign_dist_m": 7000.0,
    },
}


def parse_beacon(payload: object) -> CrewBeacon:
    """Validate a decoded JSON payload into a CrewBeacon.

    Required: crew_id (non-empty str), lat/lon (degrees, in range), t_s (unix
    seconds, number). Optional: status (CrewStatus value string), accuracy_m,
    speed_mps (non-negative numbers). Raises ValueError on any problem."""
    if not isinstance(payload, dict):
        raise ValueError("beacon body must be a JSON object")
    crew_id = payload.get("crew_id")
    if not isinstance(crew_id, str) or not crew_id.strip():
        raise ValueError("crew_id must be a non-empty string")
    try:
        lat = float(payload["lat"])
        lon = float(payload["lon"])
        t_s = float(payload["t_s"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("lat, lon, t_s are required numbers")
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError("lat/lon out of range")
    status_raw = payload.get("status", CrewStatus.AVAILABLE.value)
    try:
        status = CrewStatus(status_raw)
    except ValueError:
        raise ValueError(f"unknown crew status {status_raw!r}")
    try:
        accuracy_m = float(payload.get("accuracy_m", 30.0))
        speed_mps = float(payload.get("speed_mps", 0.0))
    except (TypeError, ValueError):
        raise ValueError("accuracy_m/speed_mps must be numbers")
    if accuracy_m < 0.0 or speed_mps < 0.0:
        raise ValueError("accuracy_m/speed_mps must be >= 0")
    return CrewBeacon(crew_id=crew_id.strip(), loc=LatLon(lat, lon), t_s=t_s,
                      status=status, accuracy_m=accuracy_m, speed_mps=speed_mps)


def _parse_tick(raw) -> Optional[int]:
    """Coerce a LAW 1 scrub tick (query string or JSON value) to an int, or
    None when it is NOT an integer tick (bool, NaN/inf, a fractional float, or
    a non-numeric string). Integer-valued forms — the query string '3', the
    JSON number 3, an integral float 3.0 — are accepted (a JSON client always
    sends whole ticks). The server maps None to 404 per the replay contract
    ('404 on non-integer tick'); SimSession.state_at then maps an out-of-range
    integer (evicted/future) to 404 too."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f != int(f):
        return None
    return int(f)


# ---------------------------------------------------------------------------
# Interactive simulator sessions (SIM-3; docs/SIMULATOR_DESIGN.md)
# ---------------------------------------------------------------------------

class _SimEntry:
    """One live simulator session + its ticker-thread controls.

    `lock` guards every SimSession mutation (the ticker holds it across each
    tick(), so HTTP threads never observe a half-tick). `_stop` is the CURRENT
    run's stop event — replaced with a fresh Event on every `run` so a stale
    ticker thread can recognise it has been superseded and exit."""

    def __init__(self, session_id: str, session, base_seed: str, args: dict):
        self.session_id = session_id
        self.session = session
        self.base_seed = base_seed
        self.args = args                  # location/clients/crews/cfg (re-rolls)
        self.rerolls = 0
        self.speed_x = SimSessionManager.DEFAULT_SPEED_X
        self.lock = threading.RLock()
        self._stop = threading.Event()
        self._stop.set()                  # sessions are born paused
        self._thread: Optional[threading.Thread] = None
        self._safety_cache = None         # ((tick, n_fires), rings)

    def running(self) -> bool:
        return (self._thread is not None and self._thread.is_alive()
                and not self._stop.is_set())


class SimSessionManager:
    """In-memory registry behind /api/sim/* (SIM-3).

    Holds at most `max_sessions` wgresponse.simui.SimSessions with LRU
    eviction (any touch — state poll or command — refreshes recency; evicted
    sessions get their ticker stopped and simply vanish: next request 404s).

    Clock model: `speed_x` is the wall-clock acceleration factor (design doc
    slider 1x-600x). A running session's ticker thread performs ONE engine
    tick — one full replan cycle, the real cadence — every
    max(min_tick_real_s, replan_period_s / speed_x) real seconds, so at 600x
    a 900 s cycle plays every 1.5 s and 1x is honest real time. The
    SimSession's own step multiplier stays 1.0 on this path (no coarse
    multi-cycle jumps that would skip re-plans).

    Reproducibility: everything that mutates the engine goes through
    SimSession commands, which append to its cmd_log — any server-driven run
    is replayable from the seed + cmd_log shown in state(). Default seeds are
    the deterministic session ids; re-rolls append '#r<k>'.

    Every public method returns (http_status, json_payload); ValueErrors from
    the session surface as 400s with the engine's own message (e.g. the
    non-burnable-cell ignite rejection the UI shows as a click hint).
    """

    MAX_SESSIONS = 4
    DEFAULT_SPEED_X = 300.0            # one 900 s cycle every 3 s real
    MAX_SPEED_X = 600.0                # design doc clock ceiling
    MIN_TICK_REAL_S = 0.25             # ticker floor (protects tiny periods)

    _CONFIG_KEYS = ("egress_buffer_min", "slack_min", "speed", "deploy_min")
    _ISO_LEADS_H = (6.0, 12.0, 24.0, 48.0, 72.0)   # simui._isochrones leads

    def __init__(self, tile_dir=None, synth_size_km=None):
        self._lock = threading.Lock()
        self._sessions: "OrderedDict[str, _SimEntry]" = OrderedDict()
        self._n = 0
        self._tile_dir = tile_dir                 # test seam (simui.load_tile)
        self._synth_size_km = synth_size_km       # test seam
        # Session cap is env-configurable so a hosted deployment can hold more
        # concurrent visitors than the local default of 4 (the App Runner demo
        # sets WGRESP_SIM_MAX_SESSIONS). Unset => 4, so tests are unchanged.
        try:
            self.max_sessions = max(1, int(os.environ.get(
                "WGRESP_SIM_MAX_SESSIONS", self.MAX_SESSIONS)))
        except (TypeError, ValueError):
            self.max_sessions = self.MAX_SESSIONS
        self.min_tick_real_s = self.MIN_TICK_REAL_S

    # -- registry ----------------------------------------------------------

    def _entry(self, sid: str) -> Optional[_SimEntry]:
        with self._lock:
            entry = self._sessions.get(sid)
            if entry is not None:
                self._sessions.move_to_end(sid)
            return entry

    def stop_all(self) -> None:
        """Signal every ticker to stop (server shutdown / test teardown)."""
        with self._lock:
            entries = list(self._sessions.values())
        for entry in entries:
            entry._stop.set()

    # -- endpoints ----------------------------------------------------------

    def new_session(self, payload):
        if not isinstance(payload, dict):
            return 400, {"error": "body must be a JSON object"}
        scenario = payload.get("scenario")
        if scenario is not None:
            if not isinstance(scenario, str) or scenario not in SCENARIOS:
                return 400, {"error": "scenario must be one of "
                                      f"{sorted(SCENARIOS)}"}
            preset = SCENARIOS[scenario]
            payload = dict(payload)
            for k in ("location", "clients", "crews", "crews_1h"):
                payload.setdefault(k, preset[k])
            payload.setdefault("seed", f"scenario-{scenario}")
        loc = payload.get("location")
        if not isinstance(loc, str) or loc not in simui.LOCATIONS:
            return 400, {"error": "location must be one of "
                                  f"{sorted(simui.LOCATIONS)}"}
        try:
            n_clients = int(payload["clients"])
            n_crews = int(payload["crews"])
        except (KeyError, TypeError, ValueError):
            return 400, {"error": "clients and crews are required integers"}
        # REGIONAL / MUTUAL-AID CREWS (contracts FOUNDER ROUND 5 SEMANTICS 9):
        # optional out-of-region reinforcement counts, default 0. Canonical
        # keys are crews_1h / crews_3h (matching the in-zone `crews`); the
        # python-style n_crews_1h / n_crews_3h are accepted as aliases. Each is
        # a non-negative int capped at 12; unknown/negative -> 400. `crews`
        # stays the IN-ZONE count.
        try:
            n_crews_1h = int(payload.get("crews_1h",
                                         payload.get("n_crews_1h", 0)))
            n_crews_3h = int(payload.get("crews_3h",
                                         payload.get("n_crews_3h", 0)))
        except (TypeError, ValueError):
            return 400, {"error": "crews_1h and crews_3h must be integers"}
        if not (0 <= n_crews_1h <= 12):
            return 400, {"error": "crews_1h must be in 0..12"}
        if not (0 <= n_crews_3h <= 12):
            return 400, {"error": "crews_3h must be in 0..12"}
        cfg = payload.get("cfg") or {}
        if not isinstance(cfg, dict):
            return 400, {"error": "cfg must be a JSON object of "
                                  "ResponseConfig overrides"}
        # FIRE ZONE (contracts wgresponse.simui): optional, default standard;
        # unknown values are a loud 400. The session's state echoes the zone
        # that ACTUALLY loaded (missing _ext tile -> loud note + standard).
        fire_zone = payload.get("fire_zone", "standard")
        if not isinstance(fire_zone, str) or fire_zone not in simui.FIRE_ZONES:
            return 400, {"error": "fire_zone must be one of "
                                  f"{list(simui.FIRE_ZONES)}"}
        # PHYSICS TOGGLES (contracts SAFETY SEMANTICS 11 ember spotting +
        # cfg.grass_ros_floor_frac grass head-ROS floor). Two optional bools,
        # BOTH default True: the founder's browser (this server path) gets
        # realistic fire — grass floor + spotting — by default, with a
        # per-session toggle. `spotting` -> cfg.spotting_enabled; `grass_realism`
        # -> cfg.grass_ros_floor_frac (0.10 head-ROS floor when on, 0.0 off).
        # Non-bool -> 400. Mapped into cfg_overrides (the ONE SimSession
        # construction path) and baked into the entry args so reset/reroll
        # preserve them; a DIRECT SimSession(...) leaves both at the
        # ResponseConfig product defaults (OFF), so the engine test suite is
        # byte-identical — physics turns ON only here. state echoes
        # state.spotting / state.grass_realism (simui.SimSession.state).
        spotting = payload.get("spotting", True)
        grass_realism = payload.get("grass_realism", True)
        # COAT-EARLY (founder dispatch loop 2026-07-11): coat as early as feasible
        # instead of the gel-freshness late-shift — sweep-proven ~+1% more homes
        # protected, 0 safety violations, freshness-at-arrival neutral. Opt-in
        # (default off) so a param-less POST stays byte-identical; the UI sends it
        # ON by default and lets the founder toggle to compare.
        coat_early = payload.get("coat_early", False)
        # PLAN-ON-ROADS (founder build 2026-07-12): the planner sees the roads
        # crews drive (travel + corridor T* on the route polyline). ON by
        # default in the simulator (it is reality — the chord-vs-road mismatch
        # caused ~73% of relocations); optional bool to compare legacy chords.
        plan_on_roads = payload.get("plan_on_roads", True)
        # FOUNDER ROUND 5 COATING HOLD: fire > 24h out -> alert + preposition
        # instead of coating, gated on the every-cycle full-coverage probe.
        # ON by default in the simulator; optional bool to compare.
        hold_24h = payload.get("hold_24h", True)
        if (not isinstance(spotting, bool) or not isinstance(grass_realism, bool)
                or not isinstance(coat_early, bool)
                or not isinstance(plan_on_roads, bool)
                or not isinstance(hold_24h, bool)):
            return 400, {"error": "spotting, grass_realism, coat_early, "
                                  "plan_on_roads, hold_24h must be booleans"}
        cfg = dict(cfg)
        cfg["spotting_enabled"] = spotting
        cfg["grass_ros_floor_frac"] = 0.10 if grass_realism else 0.0
        cfg["dispatch_late_shift"] = not coat_early
        cfg["dispatch_plan_on_roads"] = plan_on_roads
        cfg["dispatch_hold_h"] = 24.0 if hold_24h else 0.0
        # FOUNDER ROUND 6: coat time per home (minutes). Default 15 = the
        # adopted 3-person crew model (one member preps the next site); the
        # UI slider (POST /config deploy_min) changes it live mid-run.
        try:
            deploy_min = float(payload.get("deploy_min", 15.0))
        except (TypeError, ValueError):
            return 400, {"error": "deploy_min must be a number"}
        if not (1.0 <= deploy_min <= 120.0):
            return 400, {"error": "deploy_min must be in [1, 120] minutes"}
        cfg["deploy_min"] = deploy_min
        # FOUNDER ROUND 6 sequence knob (measured: fire_first/urgency cost
        # 0-12 homes vs the count-max order across ratios — default stays auto)
        sequence = payload.get("sequence", "auto")
        if sequence not in ("auto", "fire_first", "urgency"):
            return 400, {"error": "sequence must be auto|fire_first|urgency"}
        cfg["dispatch_sequence"] = sequence
        # CREW SHIFTS (ROUND 6b): 12h-on / 8h-rest fatigue cycles for multi-
        # day events (A/B staggered). Default OFF (short demo scenarios).
        crew_shifts = payload.get("crew_shifts", False)
        if not isinstance(crew_shifts, bool):
            return 400, {"error": "crew_shifts must be a boolean"}
        if crew_shifts:
            cfg["crew_shift_max_on_h"] = 12.0
            cfg["crew_shift_rest_h"] = 8.0
        with self._lock:
            self._n += 1
            sid = f"sim-{self._n}"
        seed = str(payload.get("seed") or sid)   # deterministic default
        kwargs = {}
        if self._tile_dir is not None:
            kwargs["tile_dir"] = self._tile_dir
        if self._synth_size_km is not None:
            kwargs["synth_size_km"] = self._synth_size_km
        try:
            session = simui.SimSession(loc, n_clients, n_crews, seed=seed,
                                       cfg_overrides=cfg,
                                       fire_zone=fire_zone,
                                       n_crews_1h=n_crews_1h,
                                       n_crews_3h=n_crews_3h, **kwargs)
        except (TypeError, ValueError) as e:
            return 400, {"error": str(e)}
        if scenario is not None:
            # preset ignition: search bearing jitter + range shrink until a
            # burnable cell takes (the surface model cannot ignite developed/
            # water cells); wind blows FROM the ignition bearing so the fire
            # runs at the community. Deterministic given the fixed seed.
            preset = SCENARIOS[scenario]
            center = simui.LOCATIONS[loc].center
            lit = False
            for db in (0, 10, -10, 20, -20, 30, -30):
                for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
                    p = geo.destination(center, preset["ign_bearing"] + db,
                                        preset["ign_dist_m"] * shrink)
                    try:
                        session.ignite(p.lat, p.lon,
                                       intensity=preset["intensity"],
                                       wind_dir_deg=preset["ign_bearing"])
                        lit = True
                        break
                    except ValueError:
                        continue
                if lit:
                    break
            if not lit:
                return 500, {"error": f"scenario {scenario!r} could not "
                                      "find a burnable ignition cell"}
        entry = _SimEntry(sid, session, seed,
                          {"location": loc, "clients": n_clients,
                           "crews": n_crews, "cfg": dict(cfg),
                           "fire_zone": fire_zone,
                           "crews_1h": n_crews_1h, "crews_3h": n_crews_3h})
        evicted = []
        with self._lock:
            self._sessions[sid] = entry
            self._sessions.move_to_end(sid)
            while len(self._sessions) > self.max_sessions:
                _, old = self._sessions.popitem(last=False)   # LRU out
                evicted.append(old)
        for old in evicted:
            old._stop.set()
        return 200, {"session_id": sid, "state": self._state_payload(entry)}

    def state(self, sid: str, safety: bool = False):
        entry = self._entry(sid)
        if entry is None:
            return 404, {"error": f"unknown session {sid!r}"}
        return 200, self._state_payload(entry, safety=safety)

    def state_at(self, sid: str, tick: int):
        """LAW 1 TIME SCRUB server half: the RECORDED tick-k snapshot verbatim
        (SimSession.state_at) or a 404. Unknown session -> 404; a tick that is
        evicted (older than cfg.history_ticks), still in the future, or from a
        history-disabled session -> 404 (the session returns None). Read under
        entry.lock so a concurrent ticker's ring append is never observed
        mid-write; the returned snapshot is the ring's own deep copy, already
        independent of live state (read-only). Touch refreshes LRU recency like
        every other endpoint (via _entry)."""
        entry = self._entry(sid)
        if entry is None:
            return 404, {"error": f"unknown session {sid!r}"}
        with entry.lock:
            snap = entry.session.state_at(tick)
        if snap is None:
            return 404, {"error": f"no recorded state for tick {tick} "
                                  f"(evicted, in the future, or history "
                                  f"disabled)"}
        return 200, snap

    def ignite(self, sid: str, payload):
        entry = self._entry(sid)
        if entry is None:
            return 404, {"error": f"unknown session {sid!r}"}
        if not isinstance(payload, dict):
            return 400, {"error": "body must be a JSON object"}
        try:
            lat = float(payload["lat"])
            lon = float(payload["lon"])
        except (KeyError, TypeError, ValueError):
            return 400, {"error": "lat and lon are required numbers"}
        if not (math.isfinite(lat) and math.isfinite(lon)):
            return 400, {"error": "lat/lon must be finite"}
        wind = payload.get("wind_dir_deg")
        if wind is not None:
            try:
                wind = float(wind)
            except (TypeError, ValueError):
                return 400, {"error": "wind_dir_deg must be a number"}
            if not math.isfinite(wind):
                return 400, {"error": "wind_dir_deg must be finite"}
        intensity = payload.get("intensity", "moderate")
        with entry.lock:
            try:
                fid = entry.session.ignite(lat, lon, intensity=intensity,
                                           wind_dir_deg=wind)
            except (TypeError, ValueError) as e:
                return 400, {"error": str(e)}
        return 200, {"ok": True, "incident_id": fid,
                     "state": self._state_payload(entry)}

    def config(self, sid: str, payload):
        """Validate-then-apply config knobs; echo what was applied.

        Values are pre-validated (finite, in range) BEFORE any is applied so
        a bad request never half-applies; buffer/slack go through the
        SimSession seam (set_buffer/set_slack: validate_config + revoke of
        unstarted stops -> re-plan next tick), speed is server clock glue."""
        entry = self._entry(sid)
        if entry is None:
            return 404, {"error": f"unknown session {sid!r}"}
        if not isinstance(payload, dict):
            return 400, {"error": "body must be a JSON object"}
        unknown = sorted(set(payload) - set(self._CONFIG_KEYS))
        if unknown:
            return 400, {"error": f"unknown config keys {unknown}; allowed: "
                                  f"{list(self._CONFIG_KEYS)}"}
        if not payload:
            return 400, {"error": "provide at least one of "
                                  f"{list(self._CONFIG_KEYS)}"}
        vals = {}
        for key in self._CONFIG_KEYS:
            if key not in payload:
                continue
            try:
                v = float(payload[key])
            except (TypeError, ValueError):
                return 400, {"error": f"{key} must be a number"}
            if not math.isfinite(v):
                return 400, {"error": f"{key} must be finite"}
            vals[key] = v
        if vals.get("egress_buffer_min", 0.0) < 0.0:
            return 400, {"error": "egress_buffer_min must be >= 0"}
        if vals.get("slack_min", 0.0) < 0.0:
            return 400, {"error": "slack_min must be >= 0"}
        if "deploy_min" in vals and not (1.0 <= vals["deploy_min"] <= 120.0):
            return 400, {"error": "deploy_min must be in [1, 120] minutes"}
        if "speed" in vals and not (0.0 < vals["speed"] <= self.MAX_SPEED_X):
            return 400, {"error": f"speed must be in (0, {self.MAX_SPEED_X:g}]"}
        with entry.lock:
            try:
                if "egress_buffer_min" in vals:
                    entry.session.set_buffer(vals["egress_buffer_min"])
                if "slack_min" in vals:
                    entry.session.set_slack(vals["slack_min"])
                if "deploy_min" in vals:
                    entry.session.set_deploy_min(vals["deploy_min"])
            except ValueError as e:    # validate_config residuals (defensive)
                return 400, {"error": str(e)}
            if "speed" in vals:
                entry.speed_x = vals["speed"]
        return 200, {"ok": True, "applied": vals,
                     "state": self._state_payload(entry)}

    def ctl(self, sid: str, payload):
        entry = self._entry(sid)
        if entry is None:
            return 404, {"error": f"unknown session {sid!r}"}
        if not isinstance(payload, dict):
            return 400, {"error": "body must be a JSON object"}
        action = payload.get("action")
        if action == "run":
            self._start_ticker(entry)
        elif action == "pause":
            entry._stop.set()
        elif action == "step":
            with entry.lock:
                entry.session.tick()
        elif action == "reset":
            # A fresh scenario starts paused, exactly like a new session.
            entry._stop.set()
            with entry.lock:
                entry.session.reset()
                entry._safety_cache = None
        elif action == "reroll_clients":
            entry._stop.set()
            entry.rerolls += 1
            new_seed = f"{entry.base_seed}#r{entry.rerolls}"
            a = entry.args
            with entry.lock:
                entry.session.configure(
                    a["location"], a["clients"], a["crews"], new_seed,
                    a["cfg"], fire_zone=a.get("fire_zone", "standard"),
                    n_crews_1h=a.get("crews_1h", 0),
                    n_crews_3h=a.get("crews_3h", 0))
                entry._safety_cache = None
            return 200, {"ok": True, "action": action, "seed": new_seed,
                         "state": self._state_payload(entry)}
        elif action == "goto":
            # LAW 1 TIME SCRUB (the UI slider): return the RECORDED tick-k
            # snapshot verbatim. Read-only — never mutates the session or its
            # clock (a scrubbed view is frozen; the UI's LIVE button re-polls
            # the un-ticked GET /state). Missing tick -> 400 (malformed
            # request); a present-but-non-integer tick -> 404, matching the
            # GET ?tick contract; evicted/future -> 404.
            if "tick" not in payload:
                return 400, {"error": "goto requires an integer 'tick'"}
            tick = _parse_tick(payload["tick"])
            if tick is None:
                return 404, {"error": f"non-integer tick {payload['tick']!r}"}
            with entry.lock:
                snap = entry.session.state_at(tick)
            if snap is None:
                return 404, {"error": f"no recorded state for tick {tick} "
                                      f"(evicted or in the future)"}
            return 200, {"ok": True, "action": "goto", "tick": tick,
                         "state": snap}
        else:
            return 400, {"error": "action must be one of "
                                  "run|pause|step|reset|reroll_clients|goto"}
        return 200, {"ok": True, "action": action,
                     "state": self._state_payload(entry)}

    # -- ticker --------------------------------------------------------------

    def _tick_interval_s(self, entry: _SimEntry) -> float:
        period = float(entry.session._cfg.replan_period_s)  # read-only peek
        return max(self.min_tick_real_s, period / entry.speed_x)

    def _start_ticker(self, entry: _SimEntry) -> None:
        with entry.lock:
            if entry.running():
                return
            stop = threading.Event()
            entry._stop = stop
            thread = threading.Thread(
                target=self._tick_loop, args=(entry, stop), daemon=True,
                name=f"simui-ticker-{entry.session_id}")
            entry._thread = thread
            thread.start()

    def _tick_loop(self, entry: _SimEntry, stop: threading.Event) -> None:
        while not stop.wait(self._tick_interval_s(entry)):
            with entry.lock:
                if stop.is_set() or entry._stop is not stop:
                    return             # paused / superseded while waiting
                try:
                    entry.session.tick()
                except Exception:
                    _LOG.exception("sim ticker %s: tick failed; pausing",
                                   entry.session_id)
                    stop.set()
                    return

    # -- state assembly --------------------------------------------------------

    def _state_payload(self, entry: _SimEntry, safety: bool = False) -> dict:
        with entry.lock:
            state = entry.session.state()
            if safety:
                state["safety_surface"] = self._safety_surface(entry, state)
        state["session_id"] = entry.session_id
        state["running"] = entry.running()
        state["speed_x"] = entry.speed_x
        state["tick_interval_s"] = self._tick_interval_s(entry)
        return state

    def _safety_surface(self, entry: _SimEntry, state: dict) -> list:
        """Eroded-P10 isochrone rings — the REAL safety surface.

        geo.erode_min over each fire's live p10 raster is the exact transform
        wgresponse.safety applies for corridor checks (SAFETY SEMANTICS 3);
        rings are traced at the same leads as the p50 isochrones. Read-only
        peek at the session's current ensembles (called under entry.lock, so
        never mid-tick); cached per (tick, fire-count) because erode_min is
        O(cells x window) pure Python."""
        key = (state["tick"], len(state["fires"]))
        cached = entry._safety_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        session = entry.session
        rings = []
        for fid in list(session._fire_order):
            fire = session._fires.get(fid)
            if fire is None or fire.ens is None:
                continue
            # One corridor field everywhere: reuse the session's memoized
            # eroded accessor (respects the euclidean AND fuel-tiered flags)
            # so the overlay shows exactly what the law enforces.
            eroded = session._eroded_p10(fire)
            if eroded is None:
                continue
            for h in self._ISO_LEADS_H:
                ring = _trace_contour(eroded, fire.ens.t0_s + h * 3600.0,
                                      fire.origin)
                if ring:
                    rings.append({"incident_id": fid, "h": h, "points": ring})
        entry._safety_cache = (key, rings)
        return rings


class _App:
    """Request-independent wiring shared by all handler threads."""

    def __init__(self, state_provider: Optional[Callable] = None,
                 beacon_sink=None, plan_provider: Optional[Callable] = None,
                 console_path=None, simulator_path=None, sim_manager=None):
        self.state_provider = state_provider or (lambda: {})
        self._beacon_sink = beacon_sink
        self.plan_provider = plan_provider or self._default_plan
        self.console_path = Path(console_path) if console_path else _DEFAULT_CONSOLE
        self.simulator_path = (Path(simulator_path) if simulator_path
                               else _DEFAULT_SIMULATOR)
        self.sim_manager = sim_manager or SimSessionManager()

    def _default_plan(self):
        state = self.state_provider() or {}
        if isinstance(state, dict):
            return state.get("plan")
        return None

    def ingest_beacon(self, beacon: CrewBeacon) -> None:
        sink = self._beacon_sink
        if sink is None:
            return
        ingest = getattr(sink, "ingest", None)
        if callable(ingest):
            ingest(beacon)
        elif callable(sink):
            sink(beacon)


class _Handler(BaseHTTPRequestHandler):
    server_version = "wgresponse/0.1"
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):  # route to logging, not stderr
        _LOG.debug("%s %s", self.address_string(), fmt % args)

    @property
    def app(self) -> _App:
        return self.server.app  # type: ignore[attr-defined]

    def _send_json(self, code: int, obj) -> None:
        body = json.dumps(jsonable(obj)).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self) -> bool:
        if os.environ.get("WGRESP_DEV_OPEN") == "1":
            return True
        expected = os.environ.get("WGRESP_API_KEY")
        if not expected:
            return False  # fail closed
        provided = self.headers.get("X-Internal-API-Key", "")
        return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))

    def _reject_unauthorized(self) -> bool:
        if self._auth_ok():
            return False
        self._send_json(401, {"error": "unauthorized"})
        return True

    def _health_payload(self) -> dict:
        """Liveness + coarse degradation signal (SAFETY SEMANTICS 6).

        Always {"ok": True} at minimum (a broken state_provider must not turn
        the liveness probe into a 500). When the state dict carries the
        degradation/watch keys, expose the degraded flag, the last-cycle
        timestamp + age, the cycle count, and the operator-event COUNT —
        never event bodies (this endpoint is unauthenticated; details live on
        the authed /api/state). The watch daemon (Sprint 2 C1) provides
        last_cycle_t_s / last_cycle_age_s / cycles_run in its state dict."""
        info: dict = {"ok": True}
        try:
            state = self.app.state_provider() or {}
        except Exception:
            _LOG.exception("state_provider failed during healthz")
            return info
        if not isinstance(state, dict):
            return info
        if "degraded" in state:
            info["degraded"] = bool(state.get("degraded"))
        t = state.get("last_cycle_t_s", state.get("now_s", state.get("t_s")))
        if isinstance(t, (int, float)):
            info["last_cycle_t_s"] = float(t)
        age = state.get("last_cycle_age_s")
        if isinstance(age, (int, float)):
            info["last_cycle_age_s"] = float(age)
        cycles = state.get("cycles_run")
        if isinstance(cycles, (int, float)) and not isinstance(cycles, bool):
            info["cycles_run"] = int(cycles)
        events = state.get("operator_events")
        if isinstance(events, (list, tuple)):
            info["operator_event_count"] = len(events)
        return info

    # -- routes -----------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/healthz":
            self._send_json(200, self._health_payload())
            return
        if path == "/console":
            self._serve_console()
            return
        if path == "/simulator":
            self._serve_simulator()
            return
        if path.startswith("/api/sim"):
            if self._reject_unauthorized():
                return
            self._sim_get(path)
            return
        if path == "/api/state":
            if self._reject_unauthorized():
                return
            try:
                self._send_json(200, self.app.state_provider() or {})
            except Exception as e:  # state provider is injected; never 500-loop
                _LOG.exception("state_provider failed")
                self._send_json(500, {"error": f"state_provider: {e}"})
            return
        if path == "/api/plan":
            if self._reject_unauthorized():
                return
            try:
                self._send_json(200, {"plan": self.app.plan_provider()})
            except Exception as e:
                _LOG.exception("plan_provider failed")
                self._send_json(500, {"error": f"plan_provider: {e}"})
            return
        if path == "/api/beacon":
            self._send_json(405, {"error": "use POST"})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/api/beacon":
            self._post_beacon()
            return
        if path.startswith("/api/sim"):
            self._sim_post(path)
            return
        self._send_json(404, {"error": "not found"})

    def _post_beacon(self):
        if self._reject_unauthorized():
            return
        payload, ok = self._read_json_body()
        if not ok:
            return
        try:
            beacon = parse_beacon(payload)
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        try:
            self.app.ingest_beacon(beacon)
        except Exception as e:
            _LOG.exception("beacon sink failed")
            self._send_json(500, {"error": f"beacon sink: {e}"})
            return
        self._send_json(200, {"ok": True, "crew_id": beacon.crew_id})

    def _read_json_body(self):
        """Read + decode the request's JSON body.

        Returns (payload, True) on success; on failure SENDS the error
        response (400 empty/malformed, 413 oversize — the codes/messages the
        beacon endpoint has always used) and returns (None, False)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._send_json(400, {"error": "empty body"})
            return None, False
        if length > _MAX_BODY_BYTES:
            self._send_json(413, {"error": "body too large"})
            return None, False
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8")), True
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            self._send_json(400, {"error": f"malformed JSON: {e}"})
            return None, False

    # -- interactive simulator API (SIM-3) ---------------------------------

    def _sim_get(self, path: str):
        parts = path.split("/")            # ['', 'api', 'sim', <id>, <verb>]
        if len(parts) == 5 and parts[4] == "state":
            query = parse_qs(urlparse(self.path).query)
            # LAW 1 TIME SCRUB: ?tick=k returns the recorded tick-k snapshot
            # (state_at) instead of the live state; safety is a live-only
            # augmentation, so tick takes precedence when both appear.
            if "tick" in query:
                tick = _parse_tick(query["tick"][0])
                if tick is None:
                    self._send_json(404, {"error": "non-integer tick "
                                                    f"{query['tick'][0]!r}"})
                    return
                try:
                    code, payload = self.app.sim_manager.state_at(parts[3],
                                                                  tick)
                except Exception as e:
                    _LOG.exception("sim state_at failed")
                    code, payload = 500, {"error": f"sim api: {e}"}
                self._send_json(code, payload)
                return
            safety = query.get("safety", ["0"])[0] not in ("", "0", "false")
            try:
                code, payload = self.app.sim_manager.state(parts[3],
                                                           safety=safety)
            except Exception as e:
                _LOG.exception("sim state failed")
                code, payload = 500, {"error": f"sim api: {e}"}
            self._send_json(code, payload)
            return
        if (path == "/api/sim/new"
                or (len(parts) == 5 and parts[4] in ("ignite", "config", "ctl"))):
            self._send_json(405, {"error": "use POST"})
            return
        self._send_json(404, {"error": "not found"})

    def _sim_post(self, path: str):
        if self._reject_unauthorized():
            return
        payload, ok = self._read_json_body()
        if not ok:
            return
        mgr = self.app.sim_manager
        parts = path.split("/")
        try:
            if path == "/api/sim/new":
                code, out = mgr.new_session(payload)
            elif len(parts) == 5 and parts[4] == "ignite":
                code, out = mgr.ignite(parts[3], payload)
            elif len(parts) == 5 and parts[4] == "config":
                code, out = mgr.config(parts[3], payload)
            elif len(parts) == 5 and parts[4] == "ctl":
                code, out = mgr.ctl(parts[3], payload)
            elif len(parts) == 5 and parts[4] == "state":
                code, out = 405, {"error": "use GET"}
            else:
                code, out = 404, {"error": "not found"}
        except Exception as e:      # a sim bug must never wedge the server
            _LOG.exception("sim api failed")
            code, out = 500, {"error": f"sim api: {e}"}
        self._send_json(code, out)

    # -- static pages -------------------------------------------------------

    def _serve_console(self):
        self._serve_html(self.app.console_path, "console not installed")

    def _serve_simulator(self):
        self._serve_html(self.app.simulator_path, "simulator not installed")

    def _serve_html(self, path: Path, missing_msg: str):
        try:
            body = path.read_bytes()
        except OSError:
            self._send_json(404, {"error": missing_msg})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def serve(port: int = 8877, state_provider: Optional[Callable] = None,
          beacon_sink=None, plan_provider: Optional[Callable] = None,
          host: str = "127.0.0.1", console_path=None, simulator_path=None,
          sim_tile_dir=None, sim_synth_size_km=None) -> ThreadingHTTPServer:
    """Build (without starting) the threaded HTTP server on `host:port`
    (port 0 = ephemeral). Caller runs .serve_forever() — tests do so on a
    background thread and .shutdown() when done.

    state_provider: () -> JSON-able ops state (incidents, clients+alert
    states, crews, latest plan, etas). beacon_sink: CrewTracker-like (has
    .ingest(CrewBeacon)) or a plain callable(CrewBeacon).

    sim_tile_dir / sim_synth_size_km are TEST SEAMS for the /api/sim/*
    simulator sessions (point tile lookup at a controlled dir; shrink the
    synthetic-fallback tile) — production uses the committed data/tiles/."""
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.daemon_threads = True
    httpd.app = _App(state_provider=state_provider, beacon_sink=beacon_sink,  # type: ignore[attr-defined]
                     plan_provider=plan_provider, console_path=console_path,
                     simulator_path=simulator_path,
                     sim_manager=SimSessionManager(
                         tile_dir=sim_tile_dir,
                         synth_size_km=sim_synth_size_km))
    return httpd
