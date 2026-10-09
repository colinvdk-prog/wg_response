"""tools/city_weather_climatology.py — the station climatology behind the
simulator's CITY WEATHER (founder 2026-10-09: "change the weather conditions
for each city specific"; docs/review/city_weather_2026-10-09.md).

Downloads hourly METARs (2-minute sustained wind, 5-second gust, temperature,
relative humidity; routine AND special reports — IEM files many sites'
hourly reports as specials) for the airport stations nearest the thirteen
simulator communities from the Iowa Environmental Mesonet ASOS/AWOS archive
(keyless, https://mesonet.agron.iastate.edu/request/download.phtml), 2005-2024,
keeps the report nearest each top of the hour (QC: impossible RH /
temperature, unbacked sustained winds over 70 mph and isolated spikes are
dropped), then derives what "low", "moderate", "high" and "extreme" fire
weather mean at each community:

  1. Every station-hour gets the Fosberg Fire Weather Index (FFWI; Fosberg
     1978; not capped at 100): wind speed (mph) and the Simard equilibrium
     moisture content of dead fine fuel from temperature and RH — the same
     EMC formula the engine prices dead fuel with
     (wgresponse.fuels.dead_fuel_moisture_frac). A centered 3-hour mean
     smooths the two-minute wind sample's noise.
  2. Each fire-season day (the community's season months, local standard
     time, >= 18 valid hours) is scored by its highest smoothed FFWI. Its
     PEAK PERIOD is the contiguous hours around that peak whose smoothed FFWI
     stays >= 70 % of it (followed across midnight, so a two-day Santa Ana
     counts whole); the day's afternoon conditions are the MEAN wind, gust,
     RH and temperature over the peak period (they occur together —
     percentiles of each variable separately would build a day that never
     happened); its EASING is the hours after the period until the smoothed
     FFWI falls to half the peak; its night is the pre-dawn trough (mean
     wind 02-06, highest RH and lowest temperature 00-08).
  3. The days are ranked by score:
         low       the 40th-60th percentile day (a typical fire-season day)
         moderate  the 75th-90th percentile (a dry, breezy day)
         high      the 93rd-98th percentile (about one day in twenty)
         extreme   the top 1 % of days
     and each class is the MEDIAN of its days' afternoon, night, peak-period
     length (the event's hold) and easing.
  4. RECORD ENVELOPE AND ANCHORS: the station's record day, and the historic
     fire days read straight off the archive (`anchors`). Where a community's
     own fire day, measured inside the community by a fire-weather station,
     was harsher than its airport's top 1 %, extreme IS that day (OVERRIDES).
  5. ELEVATION: the homes' median ground elevation on the committed tile
     (HOME_ELEV_M; `elevations`) against the station's. Temperature and dew
     point move with the lapse rates the Eagle (1,993 m) - Aspen (2,382 m)
     pair measured on the same hours (`lapse`), RH is recomputed (Magnus);
     wind is not adjusted.

Usage (from the repo root):

    py -3 tools/city_weather_climatology.py fetch       # cache the station hours
    py -3 tools/city_weather_climatology.py analyze     # classes -> JSON + table
    py -3 tools/city_weather_climatology.py anchors     # historic fire days
    py -3 tools/city_weather_climatology.py lapse       # the lapse rates
    py -3 tools/city_weather_climatology.py elevations  # the homes' elevations

Cache: out/city_weather_cache/ (gitignored). The outputs
docs/review/city_weather/climatology.json and anchors.json are committed;
simui.CITY_WEATHER transcribes climatology.json and tests/test_city_weather.py
holds the two together. Stdlib only; no RNG (medians and percentiles of a
static archive).
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import os
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

CACHE_DIR = os.path.join(_REPO_ROOT, "out", "city_weather_cache")
OUT_JSON = os.path.join(_REPO_ROOT, "docs", "review", "city_weather",
                        "climatology.json")
IEM_URL = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
           "station={sid}&data=tmpf&data=dwpf&data=relh&data=sknt&data=gust"
           "&data=drct&data=p01i&year1={y1}&month1={m1}&day1={d1}"
           "&year2={y2}&month2={m2}&day2={d2}&tz=Etc/UTC&format=onlycomma"
           "&latlon=no&elev=no&missing=M&trace=T&direct=no&report_type=3"
           "&report_type=4")
USER_AGENT = "wgresponse-city-weather/1.0 (ops@thewildfireguardian.com)"

YEARS = (2005, 2024)          # 20 fire seasons

#: Stations (IEM ids, elevation m per the IEM network metadata). Heber is one
#: site under two ids (36U until 2017-10-11, HCR from 2017-10-19).
STATIONS = {
    "CMA": {"name": "Camarillo (AWOS)", "elev_m": 23.0, "utc_off_h": -8},
    "VNY": {"name": "Van Nuys", "elev_m": 244.0, "utc_off_h": -8},
    "SMO": {"name": "Santa Monica Muni", "elev_m": 53.0, "utc_off_h": -8},
    "CIC": {"name": "Chico Muni", "elev_m": 73.0, "utc_off_h": -8},
    "OVE": {"name": "Oroville", "elev_m": 58.0, "utc_off_h": -8},
    "STS": {"name": "Santa Rosa (Sonoma Co.)", "elev_m": 31.0, "utc_off_h": -8},
    "CRQ": {"name": "Carlsbad / Palomar", "elev_m": 100.0, "utc_off_h": -8},
    "RNM": {"name": "Ramona", "elev_m": 425.0, "utc_off_h": -8},
    "BJC": {"name": "Broomfield / Rocky Mtn Metro", "elev_m": 1724.0,
            "utc_off_h": -7},
    "BDU": {"name": "Boulder Muni", "elev_m": 1612.0, "utc_off_h": -7},
    "36U": {"name": "Heber (old id)", "elev_m": 1718.0, "utc_off_h": -7},
    "HCR": {"name": "Heber Valley", "elev_m": 1724.0, "utc_off_h": -7},
    "SLC": {"name": "Salt Lake City Intl", "elev_m": 1288.0, "utc_off_h": -7},
    "EGE": {"name": "Eagle County Regional", "elev_m": 1993.0, "utc_off_h": -7},
    "ASE": {"name": "Aspen / Sardy Field", "elev_m": 2382.0, "utc_off_h": -7},
    "RIL": {"name": "Rifle / Garfield County", "elev_m": 1691.0,
            "utc_off_h": -7},
}


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def _cache_path(sid: str, year: int) -> str:
    return os.path.join(CACHE_DIR, f"{sid}_{year}.csv")


def _get(url: str, timeout_s: float = 300.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return resp.read()


def fetch(sids, years=YEARS, force=False, pause_s=1.0) -> None:
    """Cache one CSV per (station, year) — routine AND special METARs, UTC
    (IEM files many sites' hourly reports as specials: Broomfield July 2015
    has 43 routine rows a week but 269 with specials). load_station keeps
    the report nearest each top of the hour."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    for sid in sids:
        for y in range(years[0], years[1] + 1):
            path = _cache_path(sid, y)
            if os.path.exists(path) and not force:
                continue
            url = IEM_URL.format(sid=sid, y1=y, m1=1, d1=1, y2=y + 1, m2=1,
                                 d2=1)
            for attempt in range(4):
                try:
                    body = _get(url)
                    break
                except Exception as e:          # transient: back off, retry
                    print(f"  {sid} {y}: {e} (attempt {attempt + 1})",
                          flush=True)
                    time.sleep(10.0 * (attempt + 1))
            else:
                print(f"FAILED {sid} {y}", flush=True)
                continue
            with open(path, "wb") as fh:
                fh.write(body)
            n = body.count(b"\n") - 1
            print(f"{sid} {y}: {n} rows", flush=True)
            time.sleep(pause_s)


# ---------------------------------------------------------------------------
# load + derive
# ---------------------------------------------------------------------------

KT_TO_MPH = 1.150779
MPS_PER_MPH = 0.44704


def _f(v):
    if v in (None, "", "M", "T"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def emc_pct(temp_c: float, rh: float) -> float:
    """Simard (1968) equilibrium moisture content of dead fine fuel, percent
    (T in F inside) — wgresponse.fuels.dead_fuel_moisture_frac without its
    1.03 NFDRS multiplier, as FFWI defines it."""
    t_f = temp_c * 9.0 / 5.0 + 32.0
    h = min(max(rh, 0.0), 100.0)
    if h < 10.0:
        return 0.03229 + 0.281073 * h - 0.000578 * h * t_f
    if h < 50.0:
        return 2.22749 + 0.160107 * h - 0.01478 * t_f
    return 21.0606 + 0.005565 * h * h - 0.00035 * h * t_f - 0.483199 * h


def ffwi(wind_mph: float, temp_c: float, rh: float) -> float:
    """Fosberg Fire Weather Index (Fosberg 1978; Goodrick 2002). NOT capped
    at the conventional 100: the days are RANKED by it, and at a windy site
    (Broomfield) the cap ties the whole top percent."""
    m = min(max(emc_pct(temp_c, rh), 0.0), 30.0) / 30.0
    eta = 1.0 - 2.0 * m + 1.5 * m * m - 0.5 * m * m * m
    return eta * math.sqrt(1.0 + wind_mph * wind_mph) / 0.3002


def es_hpa(t_c: float) -> float:
    """Saturation vapour pressure over water (Magnus, Alduchov-Eskridge)."""
    return 6.1094 * math.exp(17.625 * t_c / (t_c + 243.04))


def dewpoint_c(t_c: float, rh: float) -> float:
    g = math.log(max(rh, 0.1) / 100.0) + 17.625 * t_c / (t_c + 243.04)
    return 243.04 * g / (17.625 - g)


def rh_from(t_c: float, td_c: float) -> float:
    return min(100.0, 100.0 * es_hpa(td_c) / es_hpa(t_c))


def load_station(sids, years=YEARS) -> dict:
    """{utc hour (aware datetime): obs dict} for one site (several ids merge;
    a later id wins a shared hour). Each report is assigned to its nearest
    whole hour (:53 -> the next hour) and the report nearest that hour is
    kept (ties: the later), so routine reports win over specials."""
    hours = {}
    dist = {}
    for sid in sids:
        for y in range(years[0], years[1] + 1):
            path = _cache_path(sid, y)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    try:
                        t = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M")
                    except (KeyError, ValueError):
                        continue
                    t = t.replace(tzinfo=timezone.utc)
                    hr = (t + timedelta(minutes=30)).replace(minute=0)
                    tf, rh = _f(row.get("tmpf")), _f(row.get("relh"))
                    kt = _f(row.get("sknt"))
                    if tf is None or rh is None or kt is None:
                        continue
                    dd = abs((t - hr).total_seconds())
                    if hr in dist and dist[hr] < dd:
                        continue
                    dist[hr] = dd
                    g = _f(row.get("gust"))
                    tc = (tf - 32.0) * 5.0 / 9.0
                    w = kt * KT_TO_MPH
                    gm = None if g is None else g * KT_TO_MPH
                    if (not (1.0 <= rh <= 100.0) or not (-40.0 <= tc <= 50.0)
                            or (w > 70.0 and (gm is None or gm < w))):
                        # QC: impossible RH / temperature, or a sustained
                        # wind over 70 mph no gust backs (sensor spikes:
                        # Broomfield 147 kt 2012-01-10, Eagle 109 kt
                        # 2013-06-17) -> the hour is missing
                        hours.pop(hr, None)
                        continue
                    hours[hr] = {
                        "t": hr, "temp_c": tc, "rh": rh, "wind_mph": w,
                        "gust_mph": gm, "dir": _f(row.get("drct")),
                        "ffwi": ffwi(w, tc, rh)}
    # QC: an isolated wind spike (> 40 mph and > 2.5 x the larger neighbour
    # + 10 mph) is a sensor glitch, not weather
    one = timedelta(hours=1)
    bad = []
    for t, ob in hours.items():
        w = ob["wind_mph"]
        if w <= 40.0:
            continue
        nb = [hours[u]["wind_mph"] for u in (t - one, t + one) if u in hours]
        if nb and w > 2.5 * max(nb) + 10.0:
            bad.append(t)
    for t in bad:
        del hours[t]
    return hours


def _pct(sorted_vals, p):
    """Percentile p in [0, 100] by linear interpolation (sorted input)."""
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _med(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


#: Day classes by percentile of the day's fire-weather score (fire season).
CLASSES = {"low": (40.0, 60.0), "moderate": (75.0, 90.0),
           "high": (93.0, 98.0), "extreme": (99.0, 100.0)}
NEAR_PEAK = 0.70   # the day's peak period: smoothed FFWI >= 70 % of its peak
HALF_PEAK = 0.50   # the easing ends when the smoothed FFWI is half the peak
MIN_HOURS_PER_DAY = 18

#: Lapse rates MEASURED between Eagle (1,993 m) and Aspen (2,382 m) on the
#: same Jun-Sep hours 2005-2024 (`py -3 tools/city_weather_climatology.py
#: lapse`): afternoon (13-16 LST) temperature 7.1 C/km, dew point 2.8 C/km;
#: pre-dawn (03-06 LST) temperature 2.3 C/km, dew point 2.9 C/km (valley
#: inversions). Used to move a station's numbers to the homes' elevation.
LAPSE = {"day": (7.1, 2.8), "night": (2.3, 2.9)}

#: The thirteen simulator communities: station(s) (IEM ids; the first is
#: the climatology, the rest cross-checks), local standard time offset,
#: fire-season months, the short name the page shows, and why that station.
COMMUNITIES = {
    "malibu_ca": {
        "short": "Malibu", "stations": ["CMA"], "cross": ["SMO", "VNY"],
        "utc_off_h": -8, "months": (9, 10, 11, 12), "season": "Sep-Dec",
        "why": "Camarillo sits where the Santa Ana flow leaves the "
               "Santa Clara / Conejo gaps west of the Santa Monica "
               "Mountains; Santa Monica (coast) and Van Nuys (valley) are "
               "the cross-checks"},
    "paradise_ca": {
        "short": "Paradise", "stations": ["CIC"], "cross": ["OVE"],
        "utc_off_h": -8, "months": (6, 7, 8, 9, 10, 11),
        "season": "Jun-Nov",
        "why": "Chico Municipal, 22 km W on the valley floor below the "
               "Paradise ridge (Oroville cross-check)"},
    "santa_rosa_ca": {
        "short": "Santa Rosa", "stations": ["STS"], "cross": [],
        "utc_off_h": -8, "months": (6, 7, 8, 9, 10, 11),
        "season": "Jun-Nov",
        "why": "Sonoma County Airport, 14 km W of the Mark West / "
               "Fountaingrove interface"},
    "rancho_santa_fe_ca": {
        "short": "Rancho Santa Fe", "stations": ["CRQ"], "cross": ["RNM"],
        "utc_off_h": -8, "months": (9, 10, 11, 12), "season": "Sep-Dec",
        "why": "Carlsbad / Palomar, 14 km NW on the same coastal mesa; "
               "Ramona (inland, upwind in a Santa Ana) is the cross-check"},
    "boulder_co": {
        "short": "Boulder", "stations": ["BDU"], "cross": ["BJC"],
        "utc_off_h": -7, "months": tuple(range(1, 13)),
        "season": "all year",
        "why": "Boulder Municipal, 11 km NNW, under the foothills' "
               "downslope winds (Broomfield, 6 km SE on its mesa, stayed "
               "near 15 mph through the Marshall Fire); the grass fire "
               "season is year-round (Marshall burned in December)"},
    "park_city_ut": {
        "short": "Park City", "stations": ["36U", "HCR"], "cross": ["SLC"],
        "utc_off_h": -7, "months": (6, 7, 8, 9, 10),
        "season": "Jun-Oct",
        "why": "Heber Valley, 28 km SSE - the nearest mountain-valley "
               "airport (Park City has none); Salt Lake City, below "
               "Parleys Canyon, is the cross-check"},
    "vail_co": {
        "short": "Vail", "stations": ["EGE"], "cross": ["ASE"],
        "utc_off_h": -7, "months": (6, 7, 8, 9), "season": "Jun-Sep",
        "why": "Eagle County Regional, the Eagle Valley's airport, moved up "
               "to Vail's homes; Aspen (same elevation band) cross-checks"},
    "beaver_creek_co": {
        "short": "Beaver Creek", "stations": ["EGE"], "cross": ["ASE"],
        "utc_off_h": -7, "months": (6, 7, 8, 9), "season": "Jun-Sep",
        "why": "Eagle County Regional, 33 km W down the same valley"},
    "eagle_co": {
        "short": "Eagle", "stations": ["EGE"], "cross": ["RIL"],
        "utc_off_h": -7, "months": (6, 7, 8, 9), "season": "Jun-Sep",
        "why": "Eagle County Regional, in the community"},
    "glenwood_springs_co": {
        "short": "Glenwood Springs", "stations": ["RIL"], "cross": ["EGE"],
        "utc_off_h": -7, "months": (6, 7, 8, 9), "season": "Jun-Sep",
        "why": "Rifle / Garfield County, 34 km W down the Colorado River "
               "at nearly the town's elevation (Glenwood has no weather "
               "station in the archive); Eagle cross-checks"},
    "basalt_carbondale_co": {
        "short": "Basalt", "stations": ["ASE"], "cross": ["EGE", "RIL"],
        "utc_off_h": -7, "months": (6, 7, 8, 9), "season": "Jun-Sep",
        "why": "Aspen / Sardy Field, 28 km up the same Roaring Fork "
               "Valley, moved down 380 m to the homes"},
    "aspen_co": {
        "short": "Aspen", "stations": ["ASE"], "cross": ["EGE"],
        "utc_off_h": -7, "months": (6, 7, 8, 9), "season": "Jun-Sep",
        "why": "Aspen / Sardy Field, in the community"},
    "eagle_vail_co": {
        "short": "Eagle-Vail corridor", "stations": ["EGE"],
        "cross": ["ASE"], "utc_off_h": -7, "months": (6, 7, 8, 9),
        "season": "Jun-Sep",
        "why": "Eagle County Regional, at the corridor's west end, moved up "
               "to the corridor homes' median elevation"},
}

#: The homes' median ground elevation per community (m): 200 seeded homes on
#: the committed tile under the simulator's placement law (WUI band, city
#: limits, 300 m road rule on the corridor presets) - `elevations` command.
HOME_ELEV_M = {
    "malibu_ca": 246.0, "paradise_ca": 572.0, "santa_rosa_ca": 138.0,
    "boulder_co": 1637.0, "rancho_santa_fe_ca": 62.0, "park_city_ut": 2042.0,
    "vail_co": 2496.0, "beaver_creek_co": 2312.0, "eagle_co": 2010.0,
    "glenwood_springs_co": 1814.0, "basalt_carbondale_co": 1998.0,
    "aspen_co": 2428.0, "eagle_vail_co": 2256.0,
}


#: THE RECORD ENVELOPE: where the community's own worst historic fire day,
#: measured INSIDE the community by a fire-weather station, was harsher than
#: its airport's top-1 % days, "extreme" IS that fire day — the airport sits
#: outside the downslope wind's surface footprint. Only Santa Rosa: the
#: Sonoma County Airport is out on the Santa Rosa Plain, and its top-1 % day
#: (19 mph, 18 % RH) is far milder than the Tubbs night on the slopes.
#: (Paradise's Camp Fire at Jarbo Gap — 25-30 mph, RH 10-23 % — and the
#: Marshall Fire at Boulder Municipal itself sit within or at the edge of
#: their communities' extreme rows; no override.) RAWS winds are 20-ft
#: ten-minute averages, kept as published.
OVERRIDES = {
    "santa_rosa_ca": {"extreme": {
        "wind_mph": 26.0, "gust_mph": 61.1, "rh_pct": 7.0, "temp_c": 32.8,
        "hold_h": 4, "ease_h": 12,
        "source": "Tubbs Fire night, Santa Rosa RAWS 2017-10-09 11:00 UTC: "
                  "wind 11.6 m/s, gust 27.3 m/s, RH 7 %, 32.8 C, FFWI 78 "
                  "(Nauslar et al. 2018, Fire 1(1):18, Table 3); hold and "
                  "easing as measured at Sonoma County Airport that day "
                  "(anchors)"}},
}


def smooth_ffwi(hours: dict) -> dict:
    """{utc hour: centered 3-hour mean FFWI} (an hour with a missing
    neighbour averages what it has). The hourly index rides a two-minute
    wind sample, so single hours are noisy; peaks and event lengths are read
    off this."""
    one = timedelta(hours=1)
    out = {}
    for t, ob in hours.items():
        vals = [ob["ffwi"]]
        for u in (t - one, t + one):
            n = hours.get(u)
            if n is not None:
                vals.append(n["ffwi"])
        out[t] = sum(vals) / len(vals)
    return out


def peak_period(sm: dict, t_peak: datetime) -> tuple:
    """(hours of the period, ease hours) of one day's peak: the contiguous
    hours around t_peak whose smoothed FFWI stays >= NEAR_PEAK x the peak
    (followed across midnight; one missing hour bridged, a longer gap ends
    it), then the hours until the smoothed FFWI first falls to HALF_PEAK x
    the peak (capped 48)."""
    one = timedelta(hours=1)
    thr = NEAR_PEAK * sm[t_peak]

    def walk(step):
        got, t, miss = [], t_peak, 0
        while True:
            t = t + step
            v = sm.get(t)
            if v is None:
                miss += 1
                if miss > 1:
                    return got, t - step * miss
                continue
            if v < thr:
                return got, t
            got.append(t)
            miss = 0

    back, _ = walk(-one)
    fwd, t_end = walk(one)
    period = sorted(back + [t_peak] + fwd)
    half = HALF_PEAK * sm[t_peak]
    ease, t = 0, t_end
    while ease < 48:
        v = sm.get(t)
        if v is not None and v <= half:
            break
        ease += 1
        t = t + one
    return period, ease


def daily_table(hours: dict, utc_off_h: int, months) -> list:
    """One row per local-standard-time fire-season day with >= 18 valid
    hours: its score (the highest smoothed FFWI of the day), its peak period
    (peak_period) with the period's MEAN wind / gust / RH / temperature (the
    afternoon the simulator holds), the easing, and the pre-dawn trough."""
    sm = smooth_ffwi(hours)
    by_day = {}
    off = timedelta(hours=utc_off_h)
    for t in hours:
        lt = t + off
        if lt.month not in months:
            continue
        by_day.setdefault(lt.date(), []).append((lt.hour, t))
    days = []
    for d in sorted(by_day):
        obs = by_day[d]
        if len(obs) < MIN_HOURS_PER_DAY:
            continue
        night = [hours[t] for h, t in obs if 2 <= h <= 6]
        dawn = [hours[t] for h, t in obs if 0 <= h <= 8]
        if not night or not dawn:
            continue
        lh_peak, t_peak = max(obs, key=lambda x: (sm[x[1]], -x[0]))
        period, ease = peak_period(sm, t_peak)
        obp = [hours[t] for t in period if t in hours]
        gusts = [o["gust_mph"] for o in obp if o["gust_mph"] is not None]
        days.append({
            "date": d.isoformat(), "peak_hour_lst": lh_peak,
            "score": sm[t_peak], "hold_h": len(period), "ease_h": ease,
            "wind_mph": statistics.mean(o["wind_mph"] for o in obp),
            "gust_mph": statistics.mean(gusts) if gusts else None,
            "rh": statistics.mean(o["rh"] for o in obp),
            "temp_c": statistics.mean(o["temp_c"] for o in obp),
            "max_wind_mph": max(o["wind_mph"] for o in obp),
            "min_rh": min(o["rh"] for o in obp),
            "max_temp_c": max(o["temp_c"] for o in obp),
            "night_wind_mph": statistics.mean(o["wind_mph"] for o in night),
            "dawn_rh_max": max(o["rh"] for o in dawn),
            "dawn_temp_min": min(o["temp_c"] for o in dawn),
        })
    return days


def class_stats(hours: dict, days: list) -> dict:
    """The four day classes of one site + its record envelope."""
    sc = sorted(d["score"] for d in days)
    out = {"n_days": len(days),
           "first_day": days[0]["date"] if days else None,
           "last_day": days[-1]["date"] if days else None}
    # windy-hour gust factor (sustained >= 15 mph, gust reported)
    gf = sorted(ob["gust_mph"] / ob["wind_mph"] for ob in hours.values()
                if ob["wind_mph"] >= 15.0 and ob["gust_mph"])
    gust_factor = _pct(gf, 50) if gf else 1.4
    out["gust_factor_windy"] = round(gust_factor, 2)
    for name, (lo_p, hi_p) in CLASSES.items():
        lo, hi = _pct(sc, lo_p), _pct(sc, hi_p)
        sel = [d for d in days if lo <= d["score"] <= hi]
        gusts = [d["gust_mph"] for d in sel if d["gust_mph"] is not None]
        wind = _med([d["wind_mph"] for d in sel])
        gust = (_med(gusts) if len(gusts) >= max(3, len(sel) // 2)
                else wind * gust_factor)
        out[name] = {
            "pct": [lo_p, hi_p], "score_range": [round(lo, 1), round(hi, 1)],
            "n": len(sel),
            "score": round(_med([d["score"] for d in sel]), 1),
            "wind_mph": round(wind, 1), "gust_mph": round(max(gust, wind), 1),
            "gust_reported_frac": round(len(gusts) / max(1, len(sel)), 2),
            "rh": round(_med([d["rh"] for d in sel]), 1),
            "temp_c": round(_med([d["temp_c"] for d in sel]), 1),
            "peak_hour_lst": _med([d["peak_hour_lst"] for d in sel]),
            "hold_h": _med([d["hold_h"] for d in sel]),
            "hold_h_iqr": [_pct(sorted(d["hold_h"] for d in sel), 25),
                           _pct(sorted(d["hold_h"] for d in sel), 75)],
            "ease_h": _med([d["ease_h"] for d in sel]),
            "night_wind_mph": round(_med([d["night_wind_mph"]
                                          for d in sel]), 1),
            "night_rh": round(_med([d["dawn_rh_max"] for d in sel]), 1),
            "night_temp_c": round(_med([d["dawn_temp_min"] for d in sel]), 1),
            "months": _month_hist(sel),
        }
    top = max(days, key=lambda d: d["score"])
    out["record_day"] = {k: (round(top[k], 1) if isinstance(top[k], float)
                             else top[k])
                         for k in ("date", "score", "wind_mph", "gust_mph",
                                   "max_wind_mph", "rh", "min_rh", "temp_c",
                                   "hold_h", "peak_hour_lst")}
    out["p99_9_score"] = round(_pct(sc, 99.9), 1)
    return out


def _month_hist(sel) -> dict:
    h = {}
    for d in sel:
        m = int(d["date"][5:7])
        h[m] = h.get(m, 0) + 1
    return {str(k): h[k] for k in sorted(h)}


def to_elevation(temp_c: float, rh: float, dz_m: float, when: str) -> tuple:
    """(temperature, RH) moved dz_m metres up (negative: down) with the
    measured LAPSE[when] rates for temperature and dew point."""
    lt, ld = LAPSE[when]
    td = dewpoint_c(temp_c, rh)
    t2 = temp_c - lt * dz_m / 1000.0
    td2 = td - ld * dz_m / 1000.0
    return t2, rh_from(t2, min(td2, t2))


def community_profile(key: str) -> dict:
    """The analysed profile of one community (the JSON entry)."""
    c = COMMUNITIES[key]
    sids = c["stations"]
    st0 = STATIONS[sids[0]]
    hours = load_station(sids)
    days = daily_table(hours, c["utc_off_h"], c["months"])
    stats = class_stats(hours, days)
    dz = HOME_ELEV_M[key] - st0["elev_m"]
    prof = {"key": key, "short": c["short"], "stations": sids,
            "station_name": st0["name"], "station_elev_m": st0["elev_m"],
            "home_elev_m": HOME_ELEV_M[key], "dz_m": round(dz),
            "utc_off_h": c["utc_off_h"], "season": c["season"],
            "why": c["why"], "station_stats": stats, "days": {}}
    for name in CLASSES:
        r = stats[name]
        t_d, rh_d = to_elevation(r["temp_c"], r["rh"], dz, "day")
        t_n, rh_n = to_elevation(r["night_temp_c"], r["night_rh"], dz,
                                 "night")
        prof["days"][name] = {
            "wind_mph": r["wind_mph"], "gust_mph": r["gust_mph"],
            "rh_pct": round(rh_d, 1), "temp_c": round(t_d, 1),
            "night_wind_mph": r["night_wind_mph"],
            "night_rh_pct": round(rh_n, 1), "night_temp_c": round(t_n, 1),
            "hold_h": r["hold_h"], "ease_h": r["ease_h"],
            "peak_hour_lst": r["peak_hour_lst"]}
        ov = OVERRIDES.get(key, {}).get(name)
        if ov is not None:
            prof["days"][name].update(
                {k: ov[k] for k in ("wind_mph", "gust_mph", "rh_pct",
                                    "temp_c", "hold_h", "ease_h")})
            prof["days"][name]["override"] = ov["source"]
    # the daily cycle's afternoon peak: the moderate days' median peak hour
    prof["peak_hour_lst"] = float(stats["moderate"]["peak_hour_lst"])
    cross = {}
    for sid in c.get("cross", []):
        h2 = load_station([sid])
        s2 = class_stats(h2, daily_table(h2, c["utc_off_h"], c["months"]))
        cross[sid] = {k: {kk: s2[k][kk] for kk in
                          ("wind_mph", "gust_mph", "rh", "temp_c", "hold_h",
                           "ease_h", "n")}
                      for k in CLASSES}
        cross[sid]["record_day"] = s2["record_day"]
    prof["cross_checks"] = cross
    return prof


def analyze_main(only=None) -> int:
    keys = only or list(COMMUNITIES)
    out = {}
    if os.path.exists(OUT_JSON) and only:
        with open(OUT_JSON, encoding="utf-8") as fh:
            out = json.load(fh).get("communities", {})
    for key in keys:
        p = community_profile(key)
        out[key] = p
        print(f"== {key} ({p['station_name']}, {p['station_elev_m']:.0f} m "
              f"-> homes {p['home_elev_m']:.0f} m, {p['season']}, "
              f"{p['station_stats']['n_days']} days)")
        for name, d in p["days"].items():
            r = p["station_stats"][name]
            print(f"  {name:8s} {d['wind_mph']:5.1f} mph g{d['gust_mph']:5.1f}"
                  f" {d['rh_pct']:5.1f}% {d['temp_c']:5.1f}C | night "
                  f"{d['night_wind_mph']:4.1f} mph {d['night_rh_pct']:5.1f}% "
                  f"{d['night_temp_c']:5.1f}C | peak {d['peak_hour_lst']} "
                  f"hold {d['hold_h']} ease {d['ease_h']} "
                  f"(IQR {r['hold_h_iqr']}) | n={r['n']} months {r['months']}",
                  flush=True)
        print(f"  record {p['station_stats']['record_day']}")
        for sid, cr in p["cross_checks"].items():
            print(f"  cross {sid}: " + " | ".join(
                f"{k} {cr[k]['wind_mph']} mph {cr[k]['rh']}% "
                f"{cr[k]['temp_c']}C h{cr[k]['hold_h']}" for k in CLASSES))
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump({"generated_by": "tools/city_weather_climatology.py",
                   "source": "Iowa Environmental Mesonet ASOS/AWOS archive "
                             "(routine + special METARs)",
                   "years": list(YEARS), "classes": CLASSES,
                   "near_peak": NEAR_PEAK, "half_peak": HALF_PEAK,
                   "lapse_c_per_km": LAPSE, "communities": out},
                  fh, indent=1, sort_keys=False)
    print(f"wrote {OUT_JSON}")
    return 0


def lapse_main() -> int:
    """Measure LAPSE from the Eagle - Aspen pair (same hours, Jun-Sep)."""
    lo, hi = load_station(["EGE"]), load_station(["ASE"])
    dz = (STATIONS["ASE"]["elev_m"] - STATIONS["EGE"]["elev_m"]) / 1000.0
    for label, hrs in (("day", range(13, 17)), ("night", range(3, 7))):
        d_t, d_td = [], []
        for t, a in lo.items():
            lt = t + timedelta(hours=-7)
            if lt.month not in (6, 7, 8, 9) or lt.hour not in hrs:
                continue
            b = hi.get(t)
            if not b:
                continue
            d_t.append(a["temp_c"] - b["temp_c"])
            d_td.append(dewpoint_c(a["temp_c"], a["rh"])
                        - dewpoint_c(b["temp_c"], b["rh"]))
        print(f"{label}: n={len(d_t)} temperature "
              f"{statistics.median(d_t) / dz:.1f} C/km, dew point "
              f"{statistics.median(d_td) / dz:.1f} C/km")
    return 0


def elevations_main() -> int:
    """HOME_ELEV_M: median elevation of 200 seeded simulator homes."""
    from wgresponse import geo, simui
    for key in simui.LOCATIONS:
        s = simui.SimSession(key, 200, 1, seed="elev")
        t = s._tile
        nc = t.spec.ncols
        el = []
        for c in s._clients:
            rc = geo.latlon_to_rc(t.spec, c.loc)
            el.append(t.elev_m.data[rc[0] * nc + rc[1]])
        print(f"{key:22s} {statistics.median(el):7.0f} m")
    return 0


#: Historic fire days (local standard dates) read straight off the archive.
ANCHORS = [
    ("malibu_ca", "Woolsey Fire", "CMA", "2018-11-08", "2018-11-10"),
    ("malibu_ca", "Woolsey Fire", "VNY", "2018-11-08", "2018-11-10"),
    ("malibu_ca", "Woolsey Fire", "SMO", "2018-11-08", "2018-11-10"),
    ("paradise_ca", "Camp Fire", "CIC", "2018-11-07", "2018-11-09"),
    ("paradise_ca", "Camp Fire", "OVE", "2018-11-07", "2018-11-09"),
    ("santa_rosa_ca", "Tubbs Fire", "STS", "2017-10-08", "2017-10-10"),
    ("rancho_santa_fe_ca", "Witch Creek Fire", "CRQ", "2007-10-21",
     "2007-10-23"),
    ("rancho_santa_fe_ca", "Witch Creek Fire", "RNM", "2007-10-21",
     "2007-10-23"),
    ("boulder_co", "Marshall Fire", "BDU", "2021-12-30", "2021-12-31"),
    ("boulder_co", "Marshall Fire", "BJC", "2021-12-30", "2021-12-31"),
    ("park_city_ut", "Parleys Canyon Fire", "HCR", "2021-08-14",
     "2021-08-15"),
    ("park_city_ut", "Parleys Canyon Fire", "SLC", "2021-08-14",
     "2021-08-15"),
    ("basalt_carbondale_co", "Lake Christine Fire", "ASE", "2018-07-03",
     "2018-07-05"),
    ("glenwood_springs_co", "Grizzly Creek Fire", "RIL", "2020-08-10",
     "2020-08-11"),
    ("glenwood_springs_co", "Grizzly Creek Fire", "EGE", "2020-08-10",
     "2020-08-11"),
    ("eagle_co", "Sylvan Fire", "EGE", "2021-06-20", "2021-06-21"),
]


def anchors_main(only=None) -> int:
    """The worst smoothed peak of each historic fire day at its station,
    and where it falls among that community's fire-season days."""
    rows = []
    cache = {}
    for key, fire, sid, d1, d2 in ANCHORS:
        if only and key not in only:
            continue
        c = COMMUNITIES[key]
        sids = ["36U", "HCR"] if sid == "HCR" else [sid]
        if sid not in cache:
            cache[sid] = load_station(sids)
        hours = cache[sid]
        days = daily_table(hours, c["utc_off_h"], tuple(range(1, 13)))
        pick = [d for d in days if d1 <= d["date"] <= d2]
        if not pick:
            print(f"{fire} {sid}: no data")
            continue
        top = max(pick, key=lambda d: d["score"])
        season = daily_table(hours, c["utc_off_h"], c["months"])
        sc = sorted(d["score"] for d in season)
        pct = 100.0 * bisect.bisect_left(sc, top["score"]) / max(1, len(sc))
        row = {"community": key, "fire": fire, "station": sid,
               "date": top["date"], "score": round(top["score"], 1),
               "season_percentile": round(pct, 1),
               "wind_mph": round(top["wind_mph"], 1),
               "gust_mph": (round(top["gust_mph"], 1)
                            if top["gust_mph"] else None),
               "max_wind_mph": round(top["max_wind_mph"], 1),
               "rh": round(top["rh"], 1), "min_rh": round(top["min_rh"], 1),
               "temp_c": round(top["temp_c"], 1), "hold_h": top["hold_h"],
               "ease_h": top["ease_h"], "peak_hour_lst": top["peak_hour_lst"]}
        rows.append(row)
        print(f"{fire:22s} {sid} {row['date']} pct "
              f"{row['season_percentile']:5.1f} | {row['wind_mph']:5.1f} mph "
              f"(max {row['max_wind_mph']}) g{row['gust_mph']} "
              f"{row['rh']:5.1f}% (min {row['min_rh']}) "
              f"{row['temp_c']:5.1f}C | hold {row['hold_h']} ease "
              f"{row['ease_h']} peak {row['peak_hour_lst']} LST", flush=True)
    path = os.path.join(os.path.dirname(OUT_JSON), "anchors.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)
    print(f"wrote {path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("fetch", "analyze", "anchors", "lapse",
                                    "elevations"))
    ap.add_argument("--only", action="append", default=None,
                    help="station id (fetch) or community key (analyze)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "fetch":
        fetch(args.only or sorted(STATIONS), force=args.force)
        return 0
    if args.cmd == "analyze":
        return analyze_main(args.only)
    if args.cmd == "lapse":
        return lapse_main()
    if args.cmd == "elevations":
        return elevations_main()
    return anchors_main(args.only)


if __name__ == "__main__":
    sys.exit(main())
