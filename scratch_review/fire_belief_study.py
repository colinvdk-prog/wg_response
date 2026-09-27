"""Synthetic truth study for the per-fire spread-rate belief (contracts SAFETY
SEMANTICS 14; docs/FIRE_BELIEF.md "Measured results").

A HIDDEN truth fire burns on a real cached LANDFIRE tile (data/tiles/
paradise_ca_ext.wgt: 48 km, 240 m cells, grass / grass-shrub / timber mix)
under the SAME spread engine with a hidden whole-fire multiplier m_true(t)
(spread.compute_arrival hour_ros_scale). The live watcher's REAL code path
watches it — ThreatMonitor (+ AlertEngine) over stub providers serving:
  * feed: one incident whose PERIMETER is re-traced from the truth burn every
    12 h (the polygon's own time) and whose REPORTED acreage is the truth
    area with Student-t(4) 3 % noise every 12 h (6 h after each perimeter);
    the record is re-modified every cycle; discovered at ignition;
  * weather: a PERFECT forecast of the truth weather from the cycle hour;
  * terrain: the tile; registry: 24 clients on rings 3-15 km around the fire.
The truth integrates every edge PIECEWISE across hours (cfg.edge_weather_
piecewise), so a hidden rate change acts on the front at once; the watched
forecasts run the live default (frozen departure-hour edges).
The fire first runs at m = 1 (it ESTABLISHES) under diurnal 6 +- 2.5 m/s SW
winds; the watcher first sees it when establishment ends and the hidden
multiplier takes over. Cycles run HOURLY for 84 h (the live watcher's 5-min
cadence logs every weather hour). Arms, each its own monitor + belief:
OFF = cfg.fire_belief_track only (learns; the forecast is the legacy
engine's), ON = + cfg.fire_belief_apply (the belief drives nominal / P50 /
P90; P10 GUARDED = min(belief, physics)) and ON+P10 = + cfg.fire_belief_
apply_p10 (the belief moves P10 too: the full per-member rule). Everything
else is the live watcher's posture: ensemble_n 6, model error 0.71, p50
median bound.

Two suites:
  GROWING  establish 24 h (~1,700 ac), then m_true in {0.5, 1.0, 2.0} under
           the same diurnal weather. Every one of these fires is RAPID by
           the default thresholds, so the belief may speed a fire up but
           never slows it (members = the larger of prior and posterior).
  AGED     establish 72 h (~17,000 ac), then CALM weather (3 +- 1 m/s, RH
           26-54 %) with m_true in {0.01, 0.03, 0.1}: a contained fire. At
           ~17,000 ac even 0.05x physics grows ~1,000 ac/day (the rapid
           line); 0.03x and 0.01x are under both rapid thresholds, 0.1x is
           over the absolute one.
  aged-wind-event  m 0.03, then at +120 h a FORECAST 11 m/s north-east wind
           event (RH 9 %) for 18 h that also breaks containment (m 1.0 from
           then) — arm ON+P10-no-novelty (the new-pattern rule disabled)
           shows what that rule protects.
  aged-flare       m 0.03, then 1.0 at +122 h with NO weather change: the
           belief is blind to it until a report shows the growth.

Per cycle it scores the forecast against the truth on the ensemble grid:
  area24   |A_nom(now+24h) - A_truth(now+24h)| / A_truth(now+24h) with A_nom
           from the NOMINAL member (the engine's best estimate)
  growth24 (A_nom(now+24h) - A_truth(now)) / (A_truth(now+24h) - A_truth(now))
  miss24   P10 SAFETY: among cells the truth burns in (now, now+24h], the
           fraction where the truth arrives BEFORE the forecast P10 (the
           P10 was too late — the crew-safety failure); miss48 likewise
           over (now, now+48h]; client misses = clients the truth reaches
           in (now, now+48h] before their forecast P10 (summed over cycles)
  lead24   median (P10 - truth) hours over the miss24 cells (negative =
           the P10 came early: margin)
  p50late  fraction of the same cells the truth reaches before the P50 (the
           ladder's median): ~50 % = calibrated, ~0 % = over-warning

Run from the worktree root (a background job, ~15 min):
    py -3 scratch_review/fire_belief_study.py [case ...]
Writes scratch_review/fire_belief_study.json and prints markdown tables.
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from wgresponse import geo, simui, spread, terrain  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    ClientProperty,
    FireIncident,
    IncidentSource,
    LatLon,
    ResponseConfig,
    WeatherForecast,
    WeatherSample,
    seeded_rng,
)
from wgresponse.fire_belief import ACRE_M2  # noqa: E402
from wgresponse.threat import ThreatMonitor  # noqa: E402

H = 3600.0
T_IGN = 1_780_002_000.0 - (1_780_002_000.0 % H)      # an hour boundary
TILE_PATH = os.path.join(ROOT, "data", "tiles", "paradise_ca_ext.wgt")
IGN_RC = (120, 80)             # GS2 grass-shrub, gentle slope
IID = "study-fire"
STUDY_LEN_H = 84.0             # hourly cycles after establishment
PERIM_EVERY_H = 12.0
REPORT_OFFSET_H = 6.0
REPORT_NOISE = 0.03
TRUTH_H = 230.0                # truth horizon (covers every forecast)


def case_spec(case: str) -> dict:
    """est (h), calm (weather after est), m(h) after est, event (start, len)."""
    if case.startswith("grow-m"):
        m = float(case[len("grow-m"):])
        return {"est": 24.0, "calm": False, "m": (lambda h: m), "event": None}
    if case.startswith("aged-m"):
        m = float(case[len("aged-m"):])
        return {"est": 72.0, "calm": True, "m": (lambda h: m), "event": None}
    if case == "aged-wind-event":
        return {"est": 72.0, "calm": True,
                "m": (lambda h: 0.03 if h < 120 else 1.0),
                "event": (120, 18)}
    if case == "aged-flare":
        return {"est": 72.0, "calm": True,
                "m": (lambda h: 0.03 if h < 122 else 1.0), "event": None,
                "flare_h": 122.0}
    raise ValueError(case)


# ---------------------------------------------------------------------------
# truth weather + multiplier
# ---------------------------------------------------------------------------

def base_sample(h: int, calm: bool = False) -> tuple:
    """(wind10 m/s, gust, dir FROM, rh %, temp C) at truth hour h: a diurnal
    cycle peaking mid-afternoon (hour of day counted from T_IGN)."""
    hod = h % 24
    day = math.sin(2.0 * math.pi * (hod - 9.0) / 24.0)      # +1 at 15:00
    ws = (3.0 if calm else 6.0) + (1.0 if calm else 2.5) * day
    rh = (40.0 if calm else 28.0) - 14.0 * day
    tc = (22.0 if calm else 25.0) + 8.0 * day
    wdir = 235.0 + 15.0 * day
    return ws, ws * 1.4, wdir, rh, tc


def truth_weather(case: str):
    spec = case_spec(case)

    def at(h: int) -> tuple:
        if h >= spec["est"]:
            ev = spec["event"]
            if ev and ev[0] <= h < ev[0] + ev[1]:
                return 11.0, 15.0, 45.0, 9.0, 30.0
            return base_sample(h, calm=spec["calm"])
        return base_sample(h)
    return at


def m_true_fn(case: str):
    spec = case_spec(case)
    return lambda h: 1.0 if h < spec["est"] else spec["m"](h)


def weather_forecast(case: str, loc, t0_s: float, hours: int) -> WeatherForecast:
    at = truth_weather(case)
    h0 = int(round((t0_s - T_IGN) / H))
    samples = []
    for i in range(hours):
        ws, gs, wd, rh, tc = at(h0 + i)
        samples.append(WeatherSample(t_s=t0_s + i * H, wind_speed_mps=ws,
                                     wind_gust_mps=gs, wind_dir_deg=wd,
                                     rh_pct=rh, temp_c=tc))
    return WeatherForecast(loc=loc, samples=samples, source="study")


def build_truth(tile, case: str, cfg) -> list:
    ign = geo.rc_to_latlon(tile.spec, *IGN_RC)
    wx = weather_forecast(case, ign, T_IGN, int(TRUTH_H) + 2)
    mf = m_true_fn(case)
    scale = [mf(h) for h in range(int(TRUTH_H) + 1)]
    # the TRUTH integrates every edge piecewise across hours, so a hidden
    # rate change acts on the front at once (the engine's default frozen
    # departure-hour edge law would carry an edge in transit at its old
    # multiplier for the whole traversal: at 0.03x that delays a flare-up
    # by hours and carries a day of "momentum" after a drop)
    tcfg = dataclasses.replace(cfg, horizon_h=TRUTH_H,
                               edge_weather_piecewise=True)
    return spread.compute_arrival(tile, [ign], wx, T_IGN, tcfg,
                                  hour_ros_scale=scale).data


# ---------------------------------------------------------------------------
# the stub world
# ---------------------------------------------------------------------------

class StudyFeed:
    def __init__(self, tile, truth: list, case: str):
        self.tile = tile
        self.now = T_IGN
        self.last_fetch_ok = True
        est = case_spec(case)["est"]
        rng = seeded_rng("fire-belief-study", case)
        self.perims = []          # (t, rings)
        self.reports = []         # (t, acres)
        cell_ac = tile.spec.cell_m ** 2 / ACRE_M2
        j = 0
        while est + j * PERIM_EVERY_H <= TRUTH_H - PERIM_EVERY_H:
            t = T_IGN + (est + j * PERIM_EVERY_H) * H
            rings = simui._threshold_rings(tile.spec, truth, t)
            rings = [[LatLon(p[0], p[1]) for p in r] for r in rings
                     if len(r) >= 4]
            if rings:
                self.perims.append((t, rings))
            tr = t + REPORT_OFFSET_H * H
            n = sum(1 for v in truth if v <= tr)
            # Student-t(4) relative noise (a normal over a chi, seeded)
            z = rng.gauss(0.0, 1.0) / math.sqrt(
                sum(rng.gauss(0.0, 1.0) ** 2 for _ in range(4)) / 4.0)
            self.reports.append(
                (tr, max(1.0, n * cell_ac * (1.0 + REPORT_NOISE * z))))
            j += 1

    def fetch_active(self, bbox=None):
        per = [p for p in self.perims if p[0] <= self.now]
        rep = [r for r in self.reports if r[0] <= self.now]
        meta = {}
        perimeter = None
        if per:
            meta["perimeter_at_s"] = per[-1][0]
            perimeter = per[-1][1]
        acres = rep[-1][1] if rep else None
        if rep:
            meta["reported_at_s"] = rep[-1][0]
            meta["reported_acres"] = acres
        origin = geo.rc_to_latlon(self.tile.spec, *IGN_RC)
        return [FireIncident(
            incident_id=IID, name="Study Fire", source=IncidentSource.MOCK,
            origin=origin, updated_at_s=self.now - 600.0,
            discovered_at_s=T_IGN, acreage=acres, containment_pct=None,
            active=True, perimeter=perimeter, meta=meta,
            source_ids={"mock": IID})]


class StudyWeather:
    def __init__(self, case: str):
        self.case = case
        self.now = T_IGN

    def forecast(self, loc, horizon_h):
        t0 = math.floor(self.now / H) * H
        return weather_forecast(self.case, loc, t0, int(horizon_h) + 3)


class StudyTerrain:
    def __init__(self, tile):
        self._tile = tile

    def tile(self, bbox, cell_m):
        return self._tile


class StudyRegistry:
    def __init__(self, clients):
        self._c = clients

    def protected_clients(self, bbox=None):
        return list(self._c) if bbox is None else [c for c in self._c
                                                    if bbox.contains(c.loc)]


def study_clients(tile) -> list:
    ign = geo.rc_to_latlon(tile.spec, *IGN_RC)
    out = []
    for dist in (3000.0, 6000.0, 10000.0, 15000.0):
        for brg in (0.0, 45.0, 90.0, 135.0, 225.0, 315.0):
            p = geo.destination(ign, brg, dist)
            if geo.latlon_to_rc(tile.spec, p) is not None:
                out.append(ClientProperty(client_id=f"c{int(dist)}-{int(brg)}",
                                          loc=p, name=""))
    return out


def study_cfg(**kw) -> ResponseConfig:
    base = dict(horizon_h=48.0, ensemble_n=6, model_error_sd_log=0.71,
                ensemble_p50_nominal_bound=True, grid_cell_m=240.0,
                grid_max_cells=400_000, fire_belief_track=True)
    base.update(kw)
    return ResponseConfig(**base)


ARMS = {
    "OFF": {},
    "ON": {"fire_belief_apply": True},
    "ON+P10": {"fire_belief_apply": True, "fire_belief_apply_p10": True},
    "ON+P10-no-novelty": {"fire_belief_apply": True,
                          "fire_belief_apply_p10": True,
                          "fire_belief_novel_severity_ratio": 1e9,
                          "fire_belief_novel_shift_deg": 180.0},
}


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def score(ens, truth, now: float, clients, tile) -> dict:
    p10 = ens.p10.data
    p50 = ens.p50.data
    nom = ens.members[0].data
    out = {}
    for lead in (24.0, 48.0):
        end = now + lead * H
        idx = [i for i, t in enumerate(truth) if now < t <= end]
        miss = [i for i in idx if truth[i] < p10[i] - 1e-6]
        out[f"n{int(lead)}"] = len(idx)
        out[f"miss{int(lead)}"] = len(miss)
        out[f"p50late{int(lead)}"] = sum(1 for i in idx
                                         if truth[i] < p50[i] - 1e-6)
        leads = [(p10[i] - truth[i]) / H for i in idx if math.isfinite(p10[i])]
        out[f"lead{int(lead)}"] = statistics.median(leads) if leads else None
    a_t = sum(1 for t in truth if t <= now + 24.0 * H)
    a_n = sum(1 for t in nom if t <= now + 24.0 * H)
    a_now = sum(1 for t in truth if t <= now)
    out["area24"] = abs(a_n - a_t) / a_t if a_t else None
    g_t = a_t - a_now
    out["growth24"] = ((a_n - a_now) / g_t) if g_t > 0 else None
    out["acres_now"] = a_now * tile.spec.cell_m ** 2 / ACRE_M2
    out["truth_ac_24h"] = g_t * tile.spec.cell_m ** 2 / ACRE_M2
    cl_n = cl_miss = 0
    for c in clients:
        rc = geo.latlon_to_rc(tile.spec, c.loc)
        if rc is None:
            continue
        i = rc[0] * tile.spec.ncols + rc[1]
        if now < truth[i] <= now + 48.0 * H:
            cl_n += 1
            if truth[i] < p10[i] - 1e-6:
                cl_miss += 1
    out["cl_n"] = cl_n
    out["cl_miss"] = cl_miss
    return out


def run_arm(case: str, arm: str, tile, truth, clients) -> list:
    cfg = study_cfg(**ARMS[arm])
    feed = StudyFeed(tile, truth, case)
    wx = StudyWeather(case)
    mon = ThreatMonitor(feeds=[feed], weather_provider=wx,
                        terrain_provider=StudyTerrain(tile),
                        registry=StudyRegistry(clients),
                        alert_engine=AlertEngine(cfg), cfg=cfg, safe_zones=[])
    est = case_spec(case)["est"]
    mf = m_true_fn(case)
    rows = []
    h = est
    while h <= est + STUDY_LEN_H + 1e-9:
        now = T_IGN + h * H + 60.0
        feed.now = now
        wx.now = now
        res = mon.cycle(now)
        ens = res.ensembles.get(IID)
        st = res.fire_status.get(IID)
        row = {"h": h, "state": st.state if st else None,
               "mult_p50": st.mult_p50 if st else None,
               "mult_p10": st.mult_p10 if st else None,
               "mult_p90": st.mult_p90 if st else None,
               "n_obs": st.n_obs if st else 0,
               "rapid": st.rapid if st else None,
               "novel_h": (None if not st or st.novel_from_s is None
                           else round((st.novel_from_s - now) / H, 1)),
               "m_true": mf(int(h)),
               "reset_note": [n for n in res.notes if "RESET" in n]}
        if ens is not None:
            row.update(score(ens, truth, now, clients, tile))
        rows.append(row)
        h += 1.0
    return rows


def summarize(rows: list, h_from: float = -1e9, h_to: float = 1e9) -> dict:
    sel = [r for r in rows if h_from <= r["h"] <= h_to and "miss24" in r]

    def med(key):
        v = [r[key] for r in sel if r.get(key) is not None]
        return statistics.median(v) if v else None

    def rate(mk, nk):
        n = sum(r[nk] for r in sel)
        return (sum(r[mk] for r in sel) / n) if n else None

    return {
        "cycles": len(sel),
        "area24_med": med("area24"),
        "growth24_med": med("growth24"),
        "miss24": rate("miss24", "n24"),
        "miss48": rate("miss48", "n48"),
        "p50late24": rate("p50late24", "n24"),
        "lead24_med": med("lead24"),
        "cl_miss": sum(r.get("cl_miss", 0) for r in sel),
        "cl_n": sum(r.get("cl_n", 0) for r in sel),
        "rapid_frac": (sum(1 for r in sel if r.get("rapid")) / len(sel)
                       if sel else None),
        "applied_frac": None,
        "mult_p50_last": next((r["mult_p50"] for r in reversed(sel)
                               if r.get("mult_p50") is not None), None),
        "truth_ac_24h_med": med("truth_ac_24h"),
    }


CASES = ["grow-m0.5", "grow-m1.0", "grow-m2.0", "aged-m0.01", "aged-m0.03",
         "aged-m0.1", "aged-wind-event", "aged-flare"]


def main(argv) -> int:
    cases = argv or CASES
    tile = terrain.load_ascii_tile(TILE_PATH)
    clients = study_clients(tile)
    out_path = os.path.join(ROOT, "scratch_review", "fire_belief_study.json")
    try:
        with open(out_path, encoding="utf-8") as fh:
            results = json.load(fh)
    except (OSError, ValueError):
        results = {}
    for case in cases:
        t0 = time.time()
        truth = build_truth(tile, case, study_cfg())
        arms = ["OFF", "ON", "ON+P10"] + (["ON+P10-no-novelty"]
                                          if case == "aged-wind-event" else [])
        results[case] = {}
        for arm in arms:
            rows = run_arm(case, arm, tile, truth, clients)
            results[case][arm] = {"rows": rows}
            print(f"{case:>16} {arm:>14}: {len(rows)} cycles "
                  f"({time.time() - t0:.0f} s)", flush=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=1, default=str)
    report(results)
    return 0


def _f(v, fmt="{:.2f}"):
    return "-" if v is None else fmt.format(v)


def phase_rows(case: str, arms: dict) -> list:
    """(label, h_from, h_to) phases scored separately for the event cases."""
    spec = case_spec(case)
    est = spec["est"]
    if spec.get("event"):
        s, n = spec["event"]
        return [("before (learned)", est + 24.0, s - 1.0),
                ("event", s, s + n - 1.0), ("after", s + n, est + STUDY_LEN_H)]
    if spec.get("flare_h"):
        f = spec["flare_h"]
        return [("before (learned)", est + 24.0, f - 1.0),
                ("after the flare-up", f, est + STUDY_LEN_H)]
    return [("learned (after the first 24 h)", est + 24.0, est + STUDY_LEN_H)]


def report(results: dict) -> None:
    print("\n| case | phase | arm | cycles | posterior P50 (last) | rapid "
          "cycles | truth growth ac/24 h (median) | median 24-h area error "
          "| median 24-h growth ratio | P10 misses 24 h | P10 misses 48 h "
          "| median P10 lead 24 h (h) | client P10 misses "
          "| P50 late 24 h (calibrated ~50%) |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for case, arms in results.items():
        for label, h0, h1 in phase_rows(case, arms):
            for arm, d in arms.items():
                s = summarize(d["rows"], h0, h1)
                print(f"| {case} | {label} | {arm} | {s['cycles']} "
                      f"| {_f(s['mult_p50_last'], '{:.3f}')} "
                      f"| {_f(s['rapid_frac'], '{:.0%}')} "
                      f"| {_f(s['truth_ac_24h_med'], '{:,.0f}')} "
                      f"| {_f(s['area24_med'], '{:.0%}')} "
                      f"| {_f(s['growth24_med'])} "
                      f"| {_f(s['miss24'], '{:.2%}')} "
                      f"| {_f(s['miss48'], '{:.2%}')} "
                      f"| {_f(s['lead24_med'], '{:.1f}')} "
                      f"| {s['cl_miss']}/{s['cl_n']} "
                      f"| {_f(s['p50late24'], '{:.0%}')} |")
    # the flare-up exposure: hours from the flare to the first reset cycle
    for case, arms in results.items():
        f = case_spec(case).get("flare_h")
        if not f:
            continue
        for arm, d in arms.items():
            reset_h = next((r["h"] for r in d["rows"] if r["h"] >= f
                            and r["state"] == "reset"), None)
            print(f"\nflare-up exposure ({case}, {arm}): flare at +{f:.0f} h, "
                  f"first surprise reset at "
                  f"{'-' if reset_h is None else '+%.0f h' % reset_h} "
                  f"(exposed {'-' if reset_h is None else '%.0f h' % (reset_h - f)})")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
