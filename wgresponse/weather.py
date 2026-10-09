"""wgresponse.weather — weather providers (contracts.WeatherProvider).

    NwsWeather        api.weather.gov points -> forecastGridData (model-grade RLE
                      series, ~191 h horizon) + red_flag_active() via alerts/active
    OpenMeteoWeather  keyless fallback (LICENSING: free tier is NON-COMMERCIAL —
                      see class docstring)
    MockWeather       deterministic synthetic hourly series with scripted shifts
                      (+ optional DailyCycle / WindEvent time shape: the
                      simulator's CITY WEATHER, 2026-10-09)

Units at the seam (contracts law): wind m/s (10-m sustained + gust), direction
compass degrees the wind blows FROM (0 = N, CW), RH percent 0-100, temperature
Celsius, times unix seconds. NWS serves wind in km/h — converted here.

The NWS gridpoint response run-length-encodes every variable:
``values[] = {"validTime": "2026-07-06T02:00:00+00:00/PT3H", "value": 18.52}``
where the ISO-8601 duration says how long the value holds. ``_parse_duration_s``
+ ``_expand_rle_hourly`` below ARE that decoder (a PT3H segment becomes 3
hourly samples). Endpoint shapes per docs/research/weather_terrain_fuel.md
(live-verified 2026-07-06).

Network goes through injectable ``http_get(url, headers) -> (status, bytes)``;
NWS REQUIRES a User-Agent header (taken from cfg.user_agent). Stdlib only.
Live adapters raise RuntimeError on failure so callers can fall back
(NWS -> OpenMeteo); they never invent weather.

FORECAST CACHE (contracts SAFETY SEMANTICS 17, 2026-09-27; only with
cfg.weather_grid_km > 0 — local weather across the spread domain asks up to
cfg.weather_points_max points per domain every cycle): NwsWeather then keeps
a PROCESS-LEVEL NwsForecastCache (module singleton, injectable for tests):
the points -> forecastGridData URL mapping, which is static (a lat/lon
always resolves to the same 2.5 km gridpoint), and each gridpoint's DECODED
hourly series by URL for cfg.weather_cache_ttl_s. Lattice nodes are fixed
global coordinates and several nodes (and overlapping domains) resolve to
the same gridpoint, so most points cost no request after the first cycle.
A refresh that fails raises exactly like an uncached fetch — an expired
series is never served. Knob off: no cache, the legacy calls unchanged.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from .contracts import (LatLon, ResponseConfig, WeatherForecast, WeatherSample,
                        seeded_rng)

log = logging.getLogger(__name__)

HttpGet = Callable[[str, dict], "tuple[int, bytes]"]


def _default_http_get(url: str, headers: Optional[dict] = None,
                      timeout_s: float = 30.0) -> "tuple[int, bytes]":
    """Stdlib urllib GET -> (HTTP status, body bytes)."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return resp.getcode(), resp.read()


# ---------------------------------------------------------------------------
# ISO-8601 helpers (the NWS RLE decoder)
# ---------------------------------------------------------------------------

def _parse_iso8601_s(s: str) -> float:
    """ISO-8601 timestamp -> unix seconds. 'Z' accepted; naive treated as UTC."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


_DURATION_RE = re.compile(
    r"^P(?:(?P<weeks>\d+(?:\.\d+)?)W)?(?:(?P<days>\d+(?:\.\d+)?)D)?"
    r"(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$")


def _parse_duration_s(s: str) -> float:
    """ISO-8601 duration ('PT3H', 'P1DT2H', 'PT30M', ...) -> seconds."""
    m = _DURATION_RE.match(s.strip())
    if not m or not any(m.groupdict().values()):
        raise ValueError(f"unparseable ISO-8601 duration: {s!r}")
    g = {k: float(v) if v else 0.0 for k, v in m.groupdict().items()}
    return (g["weeks"] * 604800.0 + g["days"] * 86400.0 + g["hours"] * 3600.0
            + g["minutes"] * 60.0 + g["seconds"])


def _split_valid_time(vt: str) -> "tuple[float, float]":
    """NWS validTime 'start/ISO-duration' -> (start unix s, duration s).
    Missing duration defaults to one hour."""
    if "/" in vt:
        start, dur = vt.split("/", 1)
        return _parse_iso8601_s(start), _parse_duration_s(dur)
    return _parse_iso8601_s(vt), 3600.0


def _snap_hour(t_s: float) -> int:
    """Snap a unix-seconds time to the nearest whole hour (int seconds)."""
    return int(round(t_s / 3600.0)) * 3600


def _expand_rle_hourly(values: list, convert: Callable[[float], float]) -> dict:
    """Expand NWS RLE segments into {hour_unix_s(int): converted value}.
    A segment spanning N hours contributes N hourly entries (min 1); later
    segments overwrite overlapping hours (they are authoritative at their
    own start)."""
    out: dict = {}
    for seg in values or []:
        v = seg.get("value")
        if v is None:
            continue
        try:
            t0, dur = _split_valid_time(seg["validTime"])
        except (KeyError, ValueError):
            continue
        # A genuinely zero-duration segment (PT0H) spans no time and must emit
        # zero hours; the max(1, ...) floor only applies to positive sub-hour
        # durations (e.g. PT30M), which still occupy their starting hour.
        if dur <= 0.0:
            continue
        hours = max(1, int(dur // 3600.0))
        base = _snap_hour(t0)
        cv = convert(float(v))
        for k in range(hours):
            out[base + k * 3600] = cv
    return out


# ---------------------------------------------------------------------------
# Unit conversion (per-variable 'uom' on the NWS response)
# ---------------------------------------------------------------------------

def _speed_to_mps(uom: str) -> Callable[[float], float]:
    """Converter for a wmoUnit speed uom -> m/s. NWS grid default is km/h."""
    u = (uom or "").lower()
    if "m_s" in u:
        return lambda v: v
    if u.endswith(":kn") or "knot" in u:
        return lambda v: v * 0.514444
    return lambda v: v / 3.6          # wmoUnit:km_h-1 (NWS default)


def _temp_to_c(uom: str) -> Callable[[float], float]:
    """Converter for a wmoUnit temperature uom -> Celsius."""
    if "degf" in (uom or "").lower():
        return lambda v: (v - 32.0) * 5.0 / 9.0
    return lambda v: v


_IDENT = lambda v: v


def _decode_grid_series(grid: dict) -> tuple:
    """(speed, gust, dir, rh, temp) hourly dicts {hour unix s: value} of one
    NWS gridpoint ``properties`` payload (RLE-expanded, units converted per
    each variable's uom). Raises RuntimeError when there is no wind speed."""

    def series(key: str, conv_for_uom) -> dict:
        var = grid.get(key) or {}
        return _expand_rle_hourly(var.get("values") or [],
                                  conv_for_uom(var.get("uom") or ""))

    speed = series("windSpeed", _speed_to_mps)
    gust = series("windGust", _speed_to_mps)
    wdir = series("windDirection", lambda u: _IDENT)
    rh = series("relativeHumidity", lambda u: _IDENT)
    temp = series("temperature", _temp_to_c)
    if not speed:
        raise RuntimeError("NWS gridpoint response had no windSpeed data")
    return speed, gust, wdir, rh, temp


class NwsForecastCache:
    """PROCESS-LEVEL NWS forecast cache (contracts SAFETY SEMANTICS 17; used
    only by an NwsWeather whose cfg arms weather_grid_km).

    points : points URL (lat/lon at 1e-4 deg) -> forecastGridData URL —
             static, kept (bounded at max_points, oldest evicted first).
    grids  : forecastGridData URL -> (fetched at, decoded hourly series) —
             served while clock() - fetched < the caller's ttl_s (bounded
             at max_grids; the decoded series, ~0.1 MB, not the ~0.3 MB
             payload). A hit is shared by every point and domain that
             resolves to the same gridpoint.
    Thread-safe (one lock); stats() counts hits and misses."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 max_points: int = 4096, max_grids: int = 512):
        self._clock = clock
        self._max_points = int(max_points)
        self._max_grids = int(max_grids)
        self._lock = threading.Lock()
        self._points: dict = {}
        self._grids: dict = {}
        self._stats = {"points_hits": 0, "points_misses": 0,
                       "grid_hits": 0, "grid_misses": 0}

    def points_get(self, points_url: str) -> Optional[str]:
        with self._lock:
            url = self._points.get(points_url)
            self._stats["points_hits" if url else "points_misses"] += 1
            return url

    def points_put(self, points_url: str, grid_url) -> None:
        if not isinstance(grid_url, str) or not grid_url:
            return
        with self._lock:
            self._points[points_url] = grid_url
            while len(self._points) > self._max_points:
                self._points.pop(next(iter(self._points)))

    def points_drop(self, points_url: str) -> None:
        """Forget one mapping (its gridpoint fetch failed: the next call
        re-resolves the point instead of trusting the kept URL)."""
        with self._lock:
            self._points.pop(points_url, None)

    def grid_get(self, grid_url: str, ttl_s: float):
        with self._lock:
            ent = self._grids.get(grid_url)
            if ent is not None and self._clock() - ent[0] < ttl_s:
                self._stats["grid_hits"] += 1
                return ent[1]
            self._stats["grid_misses"] += 1
            return None

    def grid_put(self, grid_url: str, series: tuple) -> None:
        with self._lock:
            self._grids.pop(grid_url, None)
            self._grids[grid_url] = (self._clock(), series)
            while len(self._grids) > self._max_grids:
                self._grids.pop(next(iter(self._grids)))

    def clear(self) -> None:
        with self._lock:
            self._points.clear()
            self._grids.clear()
            for k in self._stats:
                self._stats[k] = 0

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats, points=len(self._points),
                        grids=len(self._grids))


#: the process-level cache every armed NwsWeather shares (SEMANTICS 17)
_PROCESS_CACHE = NwsForecastCache()


def nws_forecast_cache() -> NwsForecastCache:
    """The process-level NWS forecast cache (SEMANTICS 17)."""
    return _PROCESS_CACHE


class NwsWeather:
    """NOAA/NWS api.weather.gov provider (primary; keyless, US coverage).

    forecast(): points/{lat},{lon} -> properties.forecastGridData ->
    RLE-decoded windSpeed/windGust (km/h -> m/s), windDirection (deg FROM),
    relativeHumidity (%), temperature (C) merged into hourly WeatherSamples
    (unix s). red_flag_active(): alerts/active?point= scan for Red Flag
    Warning / Extreme Fire Danger events. A User-Agent header (cfg.user_agent)
    is sent on every call — NWS requires it. Raises RuntimeError on transport/
    shape failure; callers fall back to OpenMeteoWeather.

    SAFETY SEMANTICS 17: with cfg.weather_grid_km > 0, forecast() goes
    through the process-level NwsForecastCache (module docstring; `cache`
    injects another one, e.g. for tests) with ttl cfg.weather_cache_ttl_s;
    red_flag_active() is never cached. Knob off: no cache — exactly the
    legacy requests."""

    POINTS_URL = "https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
    ALERTS_URL = "https://api.weather.gov/alerts/active?point={lat:.4f},{lon:.4f}"
    _RED_FLAG_EVENTS = ("red flag warning", "extreme fire danger")

    def __init__(self, http_get: Optional[HttpGet] = None,
                 cfg: Optional[ResponseConfig] = None,
                 cache: Optional[NwsForecastCache] = None):
        self._http_get = http_get or _default_http_get
        self._cfg = cfg or ResponseConfig()
        self._cache = None
        self._cache_ttl_s = 0.0
        if float(getattr(self._cfg, "weather_grid_km", 0.0) or 0.0) > 0.0:
            # SAFETY SEMANTICS 17: armed — the process cache (or the given one)
            self._cache = cache if cache is not None else _PROCESS_CACHE
            self._cache_ttl_s = float(self._cfg.weather_cache_ttl_s)

    def _get_json(self, url: str) -> dict:
        headers = {"User-Agent": self._cfg.user_agent,
                   "Accept": "application/geo+json"}
        status, body = self._http_get(url, headers)
        if status != 200:
            raise RuntimeError(f"NWS HTTP {status} for {url}")
        try:
            return json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise RuntimeError(f"NWS returned unparseable JSON from {url}") from e

    @property
    def cache(self) -> Optional[NwsForecastCache]:
        """The forecast cache in use (None = knob off, legacy requests)."""
        return self._cache

    def _grid_series(self, loc: LatLon) -> tuple:
        """The decoded hourly series of the gridpoint covering loc: the
        points lookup then the gridpoint fetch — through the cache when
        armed (SAFETY SEMANTICS 17), else exactly the legacy two requests."""
        points_url = self.POINTS_URL.format(lat=loc.lat, lon=loc.lon)
        cache = self._cache
        grid_url = cache.points_get(points_url) if cache is not None else None
        kept = grid_url is not None
        if grid_url is None:
            pts = self._get_json(points_url)
            try:
                grid_url = pts["properties"]["forecastGridData"]
            except (KeyError, TypeError) as e:
                raise RuntimeError(
                    "NWS points response missing forecastGridData") from e
            if cache is not None:
                cache.points_put(points_url, grid_url)
        if cache is not None:
            hit = cache.grid_get(grid_url, self._cache_ttl_s)
            if hit is not None:
                return hit
        try:
            grid = self._get_json(grid_url).get("properties") or {}
            series = _decode_grid_series(grid)
        except RuntimeError:
            if kept:
                # a kept mapping whose gridpoint now fails (an NWS grid
                # change, say) is re-resolved next time, never trusted again
                cache.points_drop(points_url)
            raise
        if cache is not None:
            cache.grid_put(grid_url, series)
        return series

    def forecast(self, loc: LatLon, horizon_h: float) -> WeatherForecast:
        """Hourly forecast at loc out to horizon_h hours (clipped to NWS's ~191 h
        grid horizon). Returns WeatherForecast with ascending hourly samples."""
        speed, gust, wdir, rh, temp = self._grid_series(loc)

        t0 = min(speed)
        t_end = min(max(speed), t0 + int(horizon_h * 3600.0))
        samples = []
        last: dict = {}
        t = t0
        while t <= t_end:
            for key, d in (("speed", speed), ("gust", gust), ("dir", wdir),
                           ("rh", rh), ("temp", temp)):
                v = d.get(t)
                if v is not None:
                    last[key] = v
            wind = last.get("speed")
            if wind is not None:
                g = last.get("gust")
                g = wind if g is None else max(g, wind)
                samples.append(WeatherSample(
                    t_s=float(t),
                    wind_speed_mps=wind,
                    wind_gust_mps=g,
                    wind_dir_deg=float(last.get("dir", 0.0)) % 360.0,
                    rh_pct=float(last.get("rh", 40.0)),
                    temp_c=float(last.get("temp", 20.0)),
                ))
            t += 3600
        if not samples:
            raise RuntimeError("NWS gridpoint decode produced no samples")
        return WeatherForecast(loc=loc, samples=samples, source="nws")

    def red_flag_active(self, loc: LatLon) -> bool:
        """True if an active Red Flag Warning / Extreme Fire Danger alert covers
        loc (alerts/active?point=lat,lon). Raises RuntimeError on transport
        failure — a fetch error must not read as 'no red flag'."""
        payload = self._get_json(self.ALERTS_URL.format(lat=loc.lat, lon=loc.lon))
        for feat in payload.get("features") or []:
            event = str((feat.get("properties") or {}).get("event") or "").lower()
            if any(name in event for name in self._RED_FLAG_EVENTS):
                return True
        return False


class OpenMeteoWeather:
    """Open-Meteo fallback provider (keyless, global).

    LICENSING: the free api.open-meteo.com endpoint is NON-COMMERCIAL ONLY
    (10k calls/day). WG Response is commercial software — production use
    requires the paid tier (hostname customer-api.open-meteo.com + &apikey=)
    or self-hosting (AGPLv3 server, CC-BY-4.0 data). Pass base_url/apikey once
    licensed; the default free endpoint is for development only.

    Requests wind in m/s directly (&wind_speed_unit=ms) and UTC timestamps;
    returns hourly WeatherSamples (unix s). Raises RuntimeError on failure."""

    DEFAULT_BASE_URL = "https://api.open-meteo.com/v1/forecast"
    _HOURLY = ("temperature_2m,relative_humidity_2m,wind_speed_10m,"
               "wind_direction_10m,wind_gusts_10m")

    def __init__(self, http_get: Optional[HttpGet] = None,
                 base_url: str = DEFAULT_BASE_URL, apikey: Optional[str] = None):
        self._http_get = http_get or _default_http_get
        self._base_url = base_url
        self._apikey = apikey

    def forecast(self, loc: LatLon, horizon_h: float) -> WeatherForecast:
        """Hourly forecast at loc out to horizon_h hours (max 16 days)."""
        days = max(1, min(16, math.ceil(horizon_h / 24.0)))
        url = (f"{self._base_url}?latitude={loc.lat:.4f}&longitude={loc.lon:.4f}"
               f"&hourly={self._HOURLY}&wind_speed_unit=ms&timezone=UTC"
               f"&forecast_days={days}")
        if self._apikey:
            url += f"&apikey={self._apikey}"
        status, body = self._http_get(url, {"Accept": "application/json"})
        if status != 200:
            raise RuntimeError(f"Open-Meteo HTTP {status}")
        try:
            hourly = json.loads(body.decode("utf-8"))["hourly"]
            times = hourly["time"]
            temp = hourly["temperature_2m"]
            rh = hourly["relative_humidity_2m"]
            wind = hourly["wind_speed_10m"]
            wdir = hourly["wind_direction_10m"]
            gust = hourly["wind_gusts_10m"]
        except (ValueError, KeyError, TypeError) as e:
            raise RuntimeError("Open-Meteo response missing hourly arrays") from e
        # Guard against ragged arrays (a truncated/partial response, or a variable
        # unavailable for later hours): bound the loop to the shortest array so an
        # out-of-range index can never escape as a bare IndexError. The class
        # contract is to raise RuntimeError on failure (caller falls back); a raw
        # IndexError would crash a fallback that only catches RuntimeError.
        n = min(len(times), len(temp), len(rh), len(wind), len(wdir), len(gust))
        samples = []
        limit_s = None
        for i in range(n):
            ts = times[i]
            if None in (wind[i], temp[i], rh[i], wdir[i]):
                continue
            t_s = _parse_iso8601_s(ts)      # naive == UTC (timezone=UTC requested)
            if limit_s is None:
                limit_s = t_s + horizon_h * 3600.0
            if t_s > limit_s:
                break
            w = float(wind[i])
            g = float(gust[i]) if gust[i] is not None else w
            samples.append(WeatherSample(
                t_s=t_s, wind_speed_mps=w, wind_gust_mps=max(g, w),
                wind_dir_deg=float(wdir[i]) % 360.0, rh_pct=float(rh[i]),
                temp_c=float(temp[i])))
        if not samples:
            raise RuntimeError("Open-Meteo decode produced no samples")
        return WeatherForecast(loc=loc, samples=samples, source="open-meteo")


@dataclass(frozen=True)
class DailyCycle:
    """A repeating 24-hour weather cycle for MockWeather(diurnal=...)
    (simulator CITY WEATHER, founder 2026-10-09: "change the weather
    conditions for each city specific"; docs/review/city_weather_2026-10-09.md).

    The cycle runs on LOCAL STANDARD TIME = UTC + utc_offset_h (no daylight
    saving — the sim clock is UTC unix seconds, so the cycle is a pure
    function of absolute time, never of the wall clock). Every variable
    follows ONE smooth curve between its pre-dawn value at trough_hour and
    its afternoon value at peak_hour: a half cosine up over the
    (peak_hour - trough_hour) hours of the morning, a half cosine down over
    the rest of the day. So the afternoon brings the day's strongest wind,
    lowest RH and highest temperature and the night the overnight recovery
    (light wind, humid, cool). Units: m/s, percent, Celsius; gust_factor =
    gust / sustained."""
    utc_offset_h: float
    wind_day_mps: float          # afternoon sustained wind (at peak_hour)
    wind_night_mps: float        # pre-dawn sustained wind (at trough_hour)
    rh_day_pct: float            # afternoon minimum RH
    rh_night_pct: float          # pre-dawn maximum RH (overnight recovery)
    temp_day_c: float            # afternoon maximum temperature
    temp_night_c: float          # pre-dawn minimum temperature
    gust_factor: float = 1.4
    peak_hour: float = 15.0      # local standard hour of the afternoon peak
    trough_hour: float = 6.0     # local standard hour of the pre-dawn trough

    def __post_init__(self):
        if not 0.0 <= self.trough_hour < self.peak_hour < 24.0:
            raise ValueError("DailyCycle needs 0 <= trough_hour < "
                             "peak_hour < 24 (local standard hours)")

    def local_hour(self, t_s: float) -> float:
        """Local standard hour of day in [0, 24) at unix second t_s."""
        return ((t_s / 3600.0 + self.utc_offset_h) % 24.0 + 24.0) % 24.0

    def phase(self, t_s: float) -> float:
        """0.0 at the pre-dawn trough, 1.0 at the afternoon peak."""
        h = self.local_hour(t_s)
        up = self.peak_hour - self.trough_hour
        if self.trough_hour <= h <= self.peak_hour:
            return 0.5 - 0.5 * math.cos(math.pi * (h - self.trough_hour) / up)
        down = 24.0 - up
        d = (h - self.peak_hour) % 24.0
        return 0.5 + 0.5 * math.cos(math.pi * d / down)

    def at(self, t_s: float) -> "tuple[float, float, float]":
        """(sustained wind m/s, RH %, temperature C) at unix second t_s."""
        p = self.phase(t_s)
        return (self.wind_night_mps + (self.wind_day_mps
                                       - self.wind_night_mps) * p,
                self.rh_night_pct + (self.rh_day_pct
                                     - self.rh_night_pct) * p,
                self.temp_night_c + (self.temp_day_c
                                     - self.temp_night_c) * p)


@dataclass(frozen=True)
class WindEvent:
    """A FINITE wind event for MockWeather(event=...) (simulator CITY
    WEATHER; see DailyCycle). From start_s (unix s — the simulator: the
    ignition that chose the intensity) the conditions HOLD at
    (wind_mps, rh_pct, temp_c, gust_factor) for peak_h hours, then ease
    LINEARLY back to the underlying weather (the daily cycle, or the
    constant base) over ease_h hours; before start_s and after the easing
    the underlying weather alone. Deterministic, a pure function of time."""
    start_s: float
    peak_h: float
    ease_h: float
    wind_mps: float
    rh_pct: float
    temp_c: float
    gust_factor: float = 1.4

    def __post_init__(self):
        if not (self.peak_h >= 0.0 and self.ease_h >= 0.0):
            raise ValueError("WindEvent peak_h and ease_h must be >= 0")

    def weight(self, t_s: float) -> float:
        """1.0 while the event holds, falling linearly to 0.0 over the
        easing, 0.0 before the start and after the easing."""
        dt_h = (t_s - self.start_s) / 3600.0
        if dt_h < 0.0:
            return 0.0
        if dt_h < self.peak_h:
            return 1.0
        if self.ease_h > 0.0 and dt_h < self.peak_h + self.ease_h:
            return 1.0 - (dt_h - self.peak_h) / self.ease_h
        return 0.0

    def end_s(self) -> float:
        """Unix second the easing is complete (the event is over)."""
        return self.start_s + (self.peak_h + self.ease_h) * 3600.0


class MockWeather:
    """Deterministic synthetic weather (contracts.seeded_rng; no wall-clock).

    Constant base conditions plus optional scripted shifts:
    ``shifts=[(hour, wind_mps, dir_deg, rh_pct), ...]`` — from the given hour
    onward the base wind/direction/RH step to those values (temp unchanged).
    Optional jitter_frac (default 0 = exact) adds seeded per-hour wobble.
    Same (seed, loc, args) always yields the identical series, and the first N
    hours are identical regardless of horizon_h. Units: m/s, deg FROM, %, C,
    unix seconds; samples start at t0_s, hourly.

    TIME SHAPE (simulator CITY WEATHER, 2026-10-09; both default None =>
    exactly the legacy constant series above, byte for byte):
      diurnal  a DailyCycle — the base wind / RH / temperature (and gust
               factor) follow the community's daily cycle on local standard
               time instead of holding constant;
      event    a WindEvent — a finite wind event laid over the base (the
               daily cycle, or the constant base without one): held, then
               eased back (WindEvent.weight blends every variable,
               gust factor included).
    With a daily cycle the shifts steer the DIRECTION only (their wind / RH
    entries are ignored: the cycle and the event own speed and humidity).
    Every hour's value is a pure function of its absolute time, so a series
    built from any t0_s agrees hour for hour with one built from another,
    and the seeded jitter draws stay two per hour as before."""

    def __init__(self, seed: object, base_wind_mps: float = 6.0,
                 dir_deg: float = 45.0, rh_pct: float = 25.0,
                 temp_c: float = 30.0, gust_factor: float = 1.4,
                 shifts: Optional[list] = None, jitter_frac: float = 0.0,
                 t0_s: float = 1_780_000_000.0, red_flag: bool = False,
                 diurnal: Optional[DailyCycle] = None,
                 event: Optional[WindEvent] = None):
        self._seed = seed
        self._base = (base_wind_mps, dir_deg, rh_pct, temp_c)
        self._gust_factor = gust_factor
        self._shifts = sorted(shifts or [], key=lambda s: s[0])
        self._jitter_frac = jitter_frac
        self._t0_s = t0_s
        self._red_flag = red_flag
        self._diurnal = diurnal
        self._event = event

    def forecast(self, loc: LatLon, horizon_h: float) -> WeatherForecast:
        """Deterministic hourly series at loc covering [t0_s, t0_s + horizon_h]."""
        if self._diurnal is not None or self._event is not None:
            return self._forecast_shaped(loc, horizon_h)
        rng = seeded_rng(self._seed, "mockwx", round(loc.lat, 4), round(loc.lon, 4))
        wind, wdir, rh, temp = self._base
        n = max(1, int(horizon_h) + 1)
        samples = []
        si = 0
        for h in range(n):
            while si < len(self._shifts) and self._shifts[si][0] <= h:
                _, wind, wdir, rh = self._shifts[si]
                si += 1
            jw = 1.0 + rng.uniform(-self._jitter_frac, self._jitter_frac)
            jr = rng.uniform(-self._jitter_frac, self._jitter_frac) * 10.0
            w = max(0.0, wind * jw)
            samples.append(WeatherSample(
                t_s=self._t0_s + h * 3600.0,
                wind_speed_mps=w,
                wind_gust_mps=w * self._gust_factor,
                wind_dir_deg=float(wdir) % 360.0,
                rh_pct=min(100.0, max(1.0, rh + jr)),
                temp_c=float(temp),
            ))
        return WeatherForecast(loc=loc, samples=samples, source="mock")

    def _forecast_shaped(self, loc: LatLon, horizon_h: float) -> WeatherForecast:
        """The TIME SHAPE path (class docstring): base = the daily cycle (or
        the constant base), the event blended over it, then the same seeded
        jitter law as the legacy path."""
        rng = seeded_rng(self._seed, "mockwx", round(loc.lat, 4), round(loc.lon, 4))
        cyc = self._diurnal
        ev = self._event
        wind0, wdir, rh0, temp0 = self._base
        n = max(1, int(horizon_h) + 1)
        samples = []
        si = 0
        for h in range(n):
            t_s = self._t0_s + h * 3600.0
            while si < len(self._shifts) and self._shifts[si][0] <= h:
                if cyc is None:
                    _, wind0, wdir, rh0 = self._shifts[si]
                else:
                    wdir = self._shifts[si][2]
                si += 1
            if cyc is not None:
                wind, rh, temp = cyc.at(t_s)
                gf = cyc.gust_factor
            else:
                wind, rh, temp, gf = wind0, rh0, temp0, self._gust_factor
            if ev is not None:
                k = ev.weight(t_s)
                if k > 0.0:
                    wind += (ev.wind_mps - wind) * k
                    rh += (ev.rh_pct - rh) * k
                    temp += (ev.temp_c - temp) * k
                    gf += (ev.gust_factor - gf) * k
            jw = 1.0 + rng.uniform(-self._jitter_frac, self._jitter_frac)
            jr = rng.uniform(-self._jitter_frac, self._jitter_frac) * 10.0
            w = max(0.0, wind * jw)
            samples.append(WeatherSample(
                t_s=t_s,
                wind_speed_mps=w,
                wind_gust_mps=w * max(1.0, gf),
                wind_dir_deg=float(wdir) % 360.0,
                rh_pct=min(100.0, max(1.0, rh + jr)),
                temp_c=float(temp),
            ))
        return WeatherForecast(loc=loc, samples=samples, source="mock")

    def red_flag_active(self, loc: LatLon) -> bool:
        """Scripted red-flag state (constructor arg; deterministic)."""
        return self._red_flag
