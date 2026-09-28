"""Belief-vs-physics real-fire study (2026-09-27) -- STEP 2: the replay.

For every kept daily capture k of a fire (bvp_fetch.py), strictly causal:

  physics  the DEPLOYED engine ensemble (spread.ensemble_arrival; production
           flags, see CFG_PROD) seeded with perimeter k at its own time t_k
           (rings subsampled to <= 200 vertices, perimeter_at_s = t_k = now:
           the harness's replay_pair seeding law) under the Open-Meteo
           ARCHIVE weather at capture k's fire centroid. Scored at t_{k+1}
           against perimeter k+1. Also keeps the NOMINAL member's growth
           curve (fire_belief.growth_curve, the exact after_spread function).
           Parallel over steps (physics does not depend on the belief).
  belief   the per-fire FireBeliefStore (wgresponse.fire_belief) driven as
           the live watcher drives it: observe() the capture (perimeter
           series only: meta perimeter_at_s = the capture time, meta
           perimeter_acres = poly_GISAcres, exactly what feeds.py sets for
           WFIGS), the weather log filled hour by hour from the archive at
           the then-current fire centroid, before_spread() (weather log +
           new-pattern test + the apply callable), then the physics nominal
           curve pinned to the capture (after_spread()'s rule), then
           statuses(). Sequential and cheap. The posterior at step k uses
           ONLY captures <= k.
  apply    where before_spread() returned a member_hour_scale callable, the
           belief-APPLIED ensemble (same call + member_hour_scale). Two arms
           are scored: GUARDED (the default build: P10 = min(belief P10,
           physics P10), fire_belief.applied_ensemble) and UNGUARDED
           (cfg.fire_belief_apply_p10: the belief's own P10). The guard is
           applied on the t_{k+1} footprints (cell burned by t_{k+1} under
           min(a, b) <=> under a or under b -- exact for every score here;
           `check-guard` re-runs fire_belief.applied_ensemble itself on the
           first applied step and the first SLOWED step of each fire and
           asserts it; output in check_guard.txt). Where the callable is None (state prior or
           reset, or the first forecast hour is already a new weather
           pattern) the applied forecast IS the physics forecast.

PERFECT-FORECAST HINDCAST: the weather every forecast sees is the observed
reanalysis for the hours it forecasts, so the comparison isolates MODEL
error (the engine's spread physics; the belief's correction of it) from
weather-FORECAST error, which the live system also carries.

Outputs (out/bvp_data/runs[_wide]/<key>/, gitignored): phys_<k>.json +
phys_<k>.npz (footprint masks), belief[_<variant>].json,
apply[_<variant>]_<k>.json. `collect` writes scratch_review/
belief_vs_physics/results[_wide]_<key>.json (the belief as built; the
what-if rule variants' per-fire files go to out/bvp_data/variant_results/).

Options: --variant wide (the 20 km-pad domain sensitivity, bvp_fetch
--variant wide tiles); --belief-variant nonovel|norapid|free (what-if rule
settings: the new-weather rule off / the rapid rule off / both; same
learning, same physics).

Usage (repo root):
    py -3 scratch_review/belief_vs_physics/bvp_run.py physics --fires willow [--workers 10]
    py -3 scratch_review/belief_vs_physics/bvp_run.py belief  --fires willow
    py -3 scratch_review/belief_vs_physics/bvp_run.py apply   --fires willow
    py -3 scratch_review/belief_vs_physics/bvp_run.py collect --fires willow
    py -3 scratch_review/belief_vs_physics/bvp_run.py all     --fires willow,grasshopper
    py -3 scratch_review/belief_vs_physics/bvp_run.py check-guard --fires willow
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
for _p in (_REPO, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bvp_fetch as F  # noqa: E402
from tools import hindcast_lib as H  # noqa: E402
from wgresponse import fire_belief as FB  # noqa: E402
from wgresponse import spread  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    FireIncident, IncidentSource, LatLon, ResponseConfig)
from wgresponse.fuels import is_burnable  # noqa: E402
from wgresponse.terrain import LfpsTerrain, load_ascii_tile  # noqa: E402

H_S = 3600.0
ACRE_M2 = 4046.8564224
MAX_SCORED_DT_H = 90.0            # the harness's dt <= horizon - 6 rule
MONOTONE_MIN = 0.85               # score_pair's T0-inside-T1 guard


def variant() -> str:
    """'' = the harness domain (4 km pad); 'wide' = the 20 km-pad
    sensitivity (env BVP_VARIANT, inherited by the worker processes)."""
    return "wide" if os.environ.get("BVP_VARIANT") == "wide" else ""


def runs_dir() -> str:
    return os.path.join(F.DATA_DIR, "runs_wide" if variant() else "runs")


def belief_variant() -> str:
    """'' = the belief AS BUILT (every fire_belief_* knob default).
    What-if variants (knob settings only; the LEARNING is identical, only
    how before_spread() uses it changes; env BVP_BELIEF):
      nonovel  the new-weather-pattern rule never fires
               (novel_severity_ratio 1e9, novel_min_wind_mps 1e9)
      norapid  the rapid rule never fires (rapid thresholds 1e12)
      free     both."""
    v = os.environ.get("BVP_BELIEF", "")
    return v if v in ("nonovel", "norapid", "free") else ""


def _bsuf() -> str:
    return f"_{belief_variant()}" if belief_variant() else ""


VARIANT_RESULTS_DIR = os.path.join(F.DATA_DIR, "variant_results")


def results_path(key: str) -> str:
    """The as-built belief's per-fire results live beside the scripts
    (committed); the what-if rule variants' per-fire files go to the
    gitignored data dir (their pooled summaries are committed)."""
    tag = "results_wide" if variant() else "results"
    if belief_variant():
        os.makedirs(VARIANT_RESULTS_DIR, exist_ok=True)
        return os.path.join(VARIANT_RESULTS_DIR,
                            f"{tag}{_bsuf()}_{key}.json")
    return os.path.join(_HERE, f"{tag}_{key}.json")


# The live watcher's production posture (task brief, 2026-09-27; cli watch
# flags --gust-blend 0.5 --grass-floor 0.10 --floor-gate --model-error 0.71
# --extreme-regime --p50-bound --phi-vector --solver-evals 20000; every
# other field the ResponseConfig default: ensemble_n 6, horizon 96 h,
# edge_weather_piecewise OFF, weather_grid_km 0).
CFG_PROD = dataclasses.replace(
    ResponseConfig(), wind_gust_blend=0.5, grass_ros_floor_frac=0.10,
    ros_floor_extinction_gate=True, model_error_sd_log=0.71,
    extreme_regime_enabled=True, ensemble_p50_nominal_bound=True,
    slope_phi_vector=True, solver_budget_evals=20000)
# The belief ON as built: track + apply, P10 guarded (apply_p10 OFF), every
# fire_belief_* tuning knob at its default.
CFG_BELIEF = dataclasses.replace(CFG_PROD, fire_belief_track=True,
                                 fire_belief_apply=True)
_NO_NOVEL = {"fire_belief_novel_severity_ratio": 1e9,
             "fire_belief_novel_min_wind_mps": 1e9}
_NO_RAPID = {"fire_belief_rapid_acres_per_day": 1e12,
             "fire_belief_rapid_frac_per_day": 1e12}


def cfg_belief():
    v = belief_variant()
    kw = {}
    if v in ("nonovel", "free"):
        kw.update(_NO_NOVEL)
    if v in ("norapid", "free"):
        kw.update(_NO_RAPID)
    return dataclasses.replace(CFG_BELIEF, **kw) if kw else CFG_BELIEF


# ---------------------------------------------------------------------------
# loading (cached per process)
# ---------------------------------------------------------------------------

_FIRE_CACHE: dict = {}


class Fire:
    def __init__(self, key: str):
        fires = F.load_fires()
        self.key = key
        self.irwin = fires[key]["irwin"]
        self.name = fires[key]["name"]
        d = F.read_json(os.path.join(F.DATA_DIR, "incidents", key + ".json"))
        self.snaps = d["snapshots"]
        self.rings = [[[LatLon(v[0], v[1]) for v in ring] for ring in s["rings"]]
                      for s in self.snaps]
        wd = F.read_json(os.path.join(F.DATA_DIR, "weather", key + ".json"))
        self.points = [tuple(p) for p in wd["points"]]
        self.forecasts = [H.parse_archive_weather(pl, LatLon(p[0], p[1]))
                          for p, pl in zip(self.points, wd["payloads"])]
        self.snap_pt = []
        for rings in self.rings:
            rp = tuple(F.round_pt(F.centroid(rings)))
            self.snap_pt.append(self.points.index(rp))
        self.wx_end_s = min(fc.samples[-1].t_s for fc in self.forecasts)
        self.bbox, self.cell_m = F.domain_of(self.snaps)
        self._tile = None
        rows = F.read_json(os.path.join(F.DATA_DIR, "rows", key + ".json"))["rows"]
        disc = [r.get("attr_FireDiscoveryDateTime") for r in rows
                if isinstance(r.get("attr_FireDiscoveryDateTime"), (int, float))]
        self.discovered_s = H.epoch_s(min(disc)) if disc else None

    @property
    def tile(self):
        if self._tile is None:
            lf = LfpsTerrain(cache_dir=os.path.join(F.DATA_DIR, "lfps"))
            cp = lf.cache_path(self.bbox, self.cell_m)
            if not (cp and os.path.exists(cp)):
                raise RuntimeError(f"{self.key}: no cached LFPS tile ({cp})")
            self._tile = load_ascii_tile(cp)
        return self._tile

    def has_tile(self) -> bool:
        lf = LfpsTerrain(cache_dir=os.path.join(F.DATA_DIR, "lfps"))
        cp = lf.cache_path(self.bbox, self.cell_m)
        return bool(cp and os.path.exists(cp))

    def wx(self, k: int):
        return self.forecasts[self.snap_pt[k]]

    def seed_rings(self, k: int) -> list:
        return [H.subsample_ring(r, H.MAX_RING_VERTS) for r in self.rings[k]]

    def n_steps(self) -> int:
        return len(self.snaps) - 1

    def step_info(self, k: int) -> dict:
        a, b = self.snaps[k], self.snaps[k + 1]
        dt_h = (b["t_s"] - a["t_s"]) / H_S
        g = (b["acres"] - a["acres"]) / a["acres"] / (dt_h / 24.0)
        why = []
        if dt_h > MAX_SCORED_DT_H:
            why.append(f"dt {dt_h:.0f} h > {MAX_SCORED_DT_H:.0f} h")
        if b["t_s"] > self.wx_end_s:
            why.append("archive weather ends before t_{k+1}")
        return {"k": k, "t0_s": a["t_s"], "t1_s": b["t_s"], "dt_h": dt_h,
                "gis0_ac": a["acres"], "gis1_ac": b["acres"],
                "growth_frac_per_day": g,
                "phase": phase_of(g), "scoreable": not why,
                "unscored_why": why,
                "pct_contained0": a.get("pct_contained"),
                "pct_contained1": b.get("pct_contained")}

    def incident(self, k: int) -> FireIncident:
        """The feed record a live watcher would hold at capture k (perimeter
        series only; feeds.py WFIGS meta keys)."""
        s = self.snaps[k]
        c = F.centroid(self.rings[k])
        return FireIncident(
            incident_id=self.irwin, name=self.name,
            source=IncidentSource.WFIGS, origin=c,
            updated_at_s=float(s["t_s"]), discovered_at_s=self.discovered_s,
            acreage=None, containment_pct=s.get("pct_contained"),
            perimeter=self.rings[k], irwin_id=self.irwin,
            meta={"perimeter_at_s": float(s["t_s"]),
                  "perimeter_acres": float(s["acres"])})


def get_fire(key: str) -> Fire:
    f = _FIRE_CACHE.get(key)
    if f is None:
        f = _FIRE_CACHE[key] = Fire(key)
    return f


def phase_of(g: float) -> str:
    """OBSERVED growth over the step (poly_GISAcres, fraction per 24 h)."""
    if g >= 0.10:
        return "growing"
    if g >= 0.02:
        return "moderate"
    return "stalled"


def run_dir(key: str) -> str:
    d = os.path.join(runs_dir(), key)
    os.makedirs(d, exist_ok=True)
    return d


def write_json(path: str, obj, compact: bool = False) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        if compact:
            json.dump(obj, f, sort_keys=True, separators=(",", ":"),
                      default=_jdefault)
        else:
            json.dump(obj, f, indent=1, sort_keys=True, default=_jdefault)
    os.replace(tmp, path)


def _jdefault(o):
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    raise TypeError(type(o))


# ---------------------------------------------------------------------------
# scoring (footprints at t_{k+1}; harness conventions, documented inline)
# ---------------------------------------------------------------------------

_MASK_CACHE: dict = {}


def obs_masks(fire: Fire, k1: int):
    """(t1_mask bool array, burnable bool array) on the fire's grid; the T1
    mask is hindcast_lib.polygon_interior_mask (cell centre inside, even-
    odd) of the stored (<= 400-vertex) rings, as score_pair uses."""
    key = (fire.key, k1)
    got = _MASK_CACHE.get(key)
    if got is not None:
        return got
    spec = fire.tile.spec
    n = spec.nrows * spec.ncols
    t1 = np.zeros(n, dtype=bool)
    idx = list(H.polygon_interior_mask(spec, fire.rings[k1]))
    if idx:
        t1[np.array(idx, dtype=np.int64)] = True
    burn = np.array([is_burnable(int(round(v))) for v in fire.tile.fuel.data],
                    dtype=bool)
    _MASK_CACHE.clear()
    _MASK_CACHE[key] = (t1, burn)
    return t1, burn


def footprints(ens, t1_s: float) -> dict:
    """{q: bool array of cells with arrival <= t1} for p10/p50/p90/nominal
    (UNREACHED is +inf, never <= t1)."""
    out = {}
    for q, r in (("p10", ens.p10.data), ("p50", ens.p50.data),
                 ("p90", ens.p90.data), ("nom", ens.members[0].data)):
        a = np.asarray(r, dtype=np.float64)
        out[q] = a <= t1_s
    return out


def seed_mask(ens, t0_s: float) -> np.ndarray:
    """score_pair's engine-T0 set: cells whose NOMINAL arrival == t0 exactly
    (the perimeter interior the engine pre-burned; identical in every member
    and every arm)."""
    return np.asarray(ens.members[0].data, dtype=np.float64) == t0_s


def _touches_edge(mask: np.ndarray, spec) -> bool:
    g = mask.reshape(spec.nrows, spec.ncols)
    return bool(g[0].any() or g[-1].any() or g[:, 0].any() or g[:, -1].any())


def score(fp: dict, t0m: np.ndarray, t1m: np.ndarray, burn: np.ndarray,
          spec) -> dict:
    """Metrics of one arm at t_{k+1}:
      area_p50_ac / area_nom_ac  forecast burned area by t1 (seed included)
      obs_area_ac                rasterized observed perimeter k+1 area
      log_area_p50               ln(area_p50 / obs_area)  (0 = exact)
      grow_<q>_ac                forecast NEW area by t1 (outside the seed)
      obs_new_ac                 observed newly burned area (T1 minus seed,
                                 every fuel; includes re-mapping noise)
      n_new_b                    newly burned BURNABLE cells (the scored set)
      hit_<q>                    share of n_new_b with arrival_q <= t1:
                                 hit_p10 = P10 ADEQUACY (the harness's
                                 p10_not_after_window: higher is safer);
                                 hit_p50 = P50 hit rate
      fa_<q>_ac                  forecast NEW area by t1 OUTSIDE perimeter
                                 k+1 (false-alarm area)
      contained_frac             share of the seed inside T1 (score_pair's
                                 monotonicity guard; < 0.85 -> cell metrics
                                 are None)
      edge_<q>                   the footprint reached the domain edge by t1
                                 (its area is then CAPPED by the harness
                                 domain: bbox of every perimeter + 4 km)"""
    cell_m = spec.cell_m
    cell_ac = cell_m * cell_m / ACRE_M2
    n_seed = int(t0m.sum())
    n_obs = int(t1m.sum())
    contained = float((t0m & t1m).sum()) / n_seed if n_seed else 0.0
    monotone = contained >= MONOTONE_MIN
    new = t1m & ~t0m
    new_b = new & burn
    n_new_b = int(new_b.sum())
    m = {"cell_m": cell_m, "n_seed": n_seed, "seed_ac": n_seed * cell_ac,
         "obs_area_ac": n_obs * cell_ac, "obs_new_ac": float(new.sum()) * cell_ac,
         "n_new_b": n_new_b, "contained_frac": contained,
         "monotone": monotone}
    for q, f in fp.items():
        n_fp = int((f | t0m).sum())
        m[f"area_{q}_ac"] = n_fp * cell_ac
        m[f"grow_{q}_ac"] = int((f & ~t0m).sum()) * cell_ac
        m[f"fa_{q}_ac"] = int((f & ~t0m & ~t1m).sum()) * cell_ac
        m[f"hit_{q}"] = (float((f & new_b).sum()) / n_new_b
                         if (monotone and n_new_b > 0) else None)
        m[f"n_hit_{q}"] = int((f & new_b).sum()) if monotone else None
        m[f"edge_{q}"] = _touches_edge(f, spec)
    m["log_area_p50"] = (math.log(m["area_p50_ac"] / m["obs_area_ac"])
                         if m["obs_area_ac"] > 0 and m["area_p50_ac"] > 0
                         else None)
    m["log_area_nom"] = (math.log(m["area_nom_ac"] / m["obs_area_ac"])
                         if m["obs_area_ac"] > 0 and m["area_nom_ac"] > 0
                         else None)
    return m


# ---------------------------------------------------------------------------
# stage 1: physics (one task per step)
# ---------------------------------------------------------------------------

def physics_task(key: str, k: int) -> dict:
    t_start = time.time()
    fire = get_fire(key)
    tile = fire.tile
    info = fire.step_info(k)
    t0, t1 = info["t0_s"], info["t1_s"]
    wx = fire.wx(k)
    ens = spread.ensemble_arrival(tile, [], wx, t0, CFG_PROD,
                                  incident_id=fire.irwin,
                                  perimeter=fire.seed_rings(k),
                                  perimeter_at_s=t0)
    curve = FB.growth_curve(ens, t0, CFG_PROD, False, None)
    from wgresponse.threat import _extreme_regime_now
    regime = bool(_extreme_regime_now(tile, wx, t0, CFG_PROD))
    out = {"key": key, "k": k, "info": info, "curve": curve,
           "extreme_regime": regime, "weather_point": fire.snap_pt[k]}
    if info["scoreable"]:
        t0m = seed_mask(ens, t0)
        t1m, burn = obs_masks(fire, k + 1)
        fp = footprints(ens, t1)
        out["phys"] = score(fp, t0m, t1m, burn, tile.spec)
        np.savez_compressed(os.path.join(run_dir(key), f"phys_{k}.npz"),
                            t0m=t0m, t1m=t1m, **fp)
    out["elapsed_s"] = time.time() - t_start
    write_json(os.path.join(run_dir(key), f"phys_{k}.json"), out)
    return {"key": key, "k": k, "s": out["elapsed_s"]}


# ---------------------------------------------------------------------------
# stage 2: the belief replay (sequential per fire)
# ---------------------------------------------------------------------------

def _pin_curve(bel, curve: dict, now_s: float) -> None:
    """FireBeliefStore.after_spread's pin rule, with the curve computed by
    the same fire_belief.growth_curve in the physics stage (pending nominal
    None: the PHYSICS nominal, m_nom = 1)."""
    for kind in FB.KINDS:
        series = bel.obs[kind]
        if series and series[-1].get("curve") is None and \
                now_s - series[-1]["seen"] <= FB.PIN_MAX_LAG_S:
            series[-1]["curve"] = curve


def run_belief(key: str) -> dict:
    fire = get_fire(key)
    cfg = cfg_belief()
    store = FB.FireBeliefStore(persistent=False)
    H_hours = FB.horizon_hours(cfg)
    rows = []
    n = len(fire.snaps)
    for k in range(n):
        t = float(fire.snaps[k]["t_s"])
        inc = fire.incident(k)
        bel = store.belief(fire.irwin)
        if bel is not None and k > 0:
            # the watcher cycles every few minutes between captures and logs
            # every weather hour from the forecast at the THEN-current fire
            # centroid (capture k-1's): fill those hours from the archive
            wx_prev = fire.wx(k - 1)
            h = math.floor(fire.snaps[k - 1]["t_s"] / H_S) * H_S + H_S
            while h < t:
                store._log_weather(bel, wx_prev, h, cfg)
                h += H_S
        events = store.observe([inc], t, cfg)
        bel = store.belief(fire.irwin)
        win = None
        if bel.windows and abs(bel.windows[-1]["tb"] - t) < 1e-6:
            w = bel.windows[-1]
            win = {k2: w.get(k2) for k2 in ("ta", "tb", "aa", "ab", "vobs",
                                            "sdv", "vnom", "cens", "mnom",
                                            "pred50", "pred90", "surprise")}
            if w.get("vnom") is not None:
                dt_h = (w["tb"] - w["ta"]) / H_S
                win["phys_nominal_growth_ac"] = FB._growth_acres(
                    FB._radius(w["aa"]), w["vnom"] / w["mnom"], dt_h, w["aa"])
            win["obs_growth_ac"] = (w["ab"] - w["aa"]) / ACRE_M2
        fn = store.before_spread(inc, fire.tile, fire.wx(k), t, cfg)
        pending = bel.pending_nominal
        bel.pending_nominal = None
        wins = store._evidence(bel, t, cfg)
        apply_params = None
        h_new = None
        if bel.novel_from_s is not None:
            h_new = int(round((bel.novel_from_s - t) / H_S))
        if fn is not None:
            post = store._posterior_from(wins, cfg)
            rapid_now = store._rapid(bel, t, cfg)[0]
            hn = h_new if h_new is not None else H_hours + 1
            apply_params = {"p": list(post.p), "cum": list(post.cum),
                            "h_new": hn, "rapid": rapid_now,
                            "nominal_mult": list(pending) if pending else None}
            # the reconstruction the apply stage uses must equal the live fn
            assert _rebuild_fn(apply_params)(0, 0.5, 1.0) == fn(0, 0.5, 1.0)
        if k < n - 1:
            ph = F.read_json(os.path.join(run_dir(key), f"phys_{k}.json"))
            _pin_curve(bel, ph["curve"], t)
        st = store.statuses([inc], t, cfg)[fire.irwin]
        rows.append({
            "k": k, "t_s": t, "gis_ac": fire.snaps[k]["acres"],
            "pct_contained": fire.snaps[k].get("pct_contained"),
            "events": events, "window": win,
            "applied": fn is not None, "apply": apply_params,
            "status": FB.status_row(st), "h_new": h_new,
            "novel_reason": bel.novel_reason,
            "n_wx_log": len(bel.wx_log)})
    out = {"key": key, "name": fire.name, "irwin": fire.irwin,
           "cfg": {k2: getattr(cfg, k2) for k2 in
                   ("fire_belief_half_life_h", "fire_belief_prior_sd_log",
                    "model_error_sd_log", "fire_belief_discrepancy_frac",
                    "fire_belief_noise_nu", "fire_belief_reset_h",
                    "fire_belief_novel_severity_ratio")},
           "rows": rows}
    write_json(os.path.join(run_dir(key), f"belief{_bsuf()}.json"), out)
    return out


def _rebuild_fn(params: dict):
    post = FB.Posterior.__new__(FB.Posterior)
    post.p = list(params["p"])
    post.cum = list(params["cum"])
    return FB.member_scale_fn(post, int(params["h_new"]),
                              FB.horizon_hours(cfg_belief()), cfg_belief(),
                              bool(params["rapid"]))


# ---------------------------------------------------------------------------
# stage 3: the belief-applied ensembles (one task per applied, scored step)
# ---------------------------------------------------------------------------

def apply_task(key: str, k: int, params: dict) -> dict:
    t_start = time.time()
    fire = get_fire(key)
    tile = fire.tile
    info = fire.step_info(k)
    t0, t1 = info["t0_s"], info["t1_s"]
    fn = _rebuild_fn(params)
    if params.get("nominal_mult") is not None:
        assert fn(0, 0.5, 1.0) == params["nominal_mult"]
    ens = spread.ensemble_arrival(tile, [], fire.wx(k), t0, cfg_belief(),
                                  incident_id=fire.irwin,
                                  perimeter=fire.seed_rings(k),
                                  perimeter_at_s=t0, member_hour_scale=fn)
    ph = np.load(os.path.join(run_dir(key), f"phys_{k}.npz"))
    t0m = seed_mask(ens, t0)
    assert np.array_equal(t0m, ph["t0m"]), "seed differs between arms"
    t1m, burn = obs_masks(fire, k + 1)
    fp_u = footprints(ens, t1)
    fp_g = dict(fp_u)
    fp_g["p10"] = fp_u["p10"] | ph["p10"]        # min(belief, physics) P10
    out = {"key": key, "k": k,
           "unguarded": score(fp_u, t0m, t1m, burn, tile.spec),
           "guarded": score(fp_g, t0m, t1m, burn, tile.spec),
           "nominal_mult_first_h": fn(0, 0.5, 1.0)[0],
           "elapsed_s": time.time() - t_start}
    np.savez_compressed(os.path.join(run_dir(key), f"apply{_bsuf()}_{k}.npz"), **fp_u)
    write_json(os.path.join(run_dir(key), f"apply{_bsuf()}_{k}.json"), out)
    return {"key": key, "k": k, "s": out["elapsed_s"]}


def check_guard(key: str, want_slowed: bool = False) -> None:
    """Re-run fire_belief.applied_ensemble (the watcher's own call) on the
    first applied scored step (with want_slowed: the first whose nominal
    multiplier is < 1, i.e. the belief really slowed it) and assert the
    footprint construction."""
    fire = get_fire(key)
    b = F.read_json(os.path.join(run_dir(key), f"belief{_bsuf()}.json"))
    for row in b["rows"][:-1]:
        k = row["k"]
        if not (row["applied"] and fire.step_info(k)["scoreable"]):
            continue
        nm = (row["apply"] or {}).get("nominal_mult") or [1.0]
        if want_slowed and not nm[0] < 1.0:
            continue
        fn = _rebuild_fn(row["apply"])
        t0, t1 = fire.step_info(k)["t0_s"], fire.step_info(k)["t1_s"]
        ens = FB.applied_ensemble(fire.tile, [], fire.wx(k), t0, cfg_belief(),
                                  fire.irwin, fire.seed_rings(k), t0, fn)
        fp = footprints(ens, t1)
        ap = np.load(os.path.join(run_dir(key), f"apply{_bsuf()}_{k}.npz"))
        ph = np.load(os.path.join(run_dir(key), f"phys_{k}.npz"))
        ok10 = np.array_equal(fp["p10"], ap["p10"] | ph["p10"])
        ok50 = np.array_equal(fp["p50"], ap["p50"])
        ok90 = np.array_equal(fp["p90"], ap["p90"])
        print(f"[check-guard] {key} step {k}: applied_ensemble P10 == belief|"
              f"physics footprint: {ok10}; P50 same: {ok50}; P90 same: {ok90}")
        assert ok10 and ok50 and ok90
        return
    print(f"[check-guard] {key}: no applied scored step")


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------

def collect(key: str) -> dict:
    fire = get_fire(key)
    b = F.read_json(os.path.join(run_dir(key), f"belief{_bsuf()}.json"))
    steps = []
    for k in range(fire.n_steps()):
        ph = F.read_json(os.path.join(run_dir(key), f"phys_{k}.json"))
        brow = b["rows"][k]
        nrow = b["rows"][k + 1]
        rec = {"k": k, **ph["info"], "extreme_regime": ph["extreme_regime"],
               "phys_elapsed_s": ph["elapsed_s"],
               "belief_at_k": {"state": brow["status"]["state"],
                               "mult_p10": brow["status"]["mult_p10"],
                               "mult_p50": brow["status"]["mult_p50"],
                               "mult_p90": brow["status"]["mult_p90"],
                               "n_obs": brow["status"]["n_obs"],
                               "rapid": brow["status"]["rapid"],
                               "known": brow["status"]["known"],
                               "novel_from_s": brow["status"]["novel_from_s"],
                               "h_new": brow["h_new"],
                               "novel_reason": brow["novel_reason"],
                               "reasons": brow["status"]["reasons"],
                               "applied": brow["applied"],
                               "nominal_mult_first_h": (
                                   brow["apply"]["nominal_mult"][0]
                                   if brow["applied"] and
                                   brow["apply"]["nominal_mult"] else None)},
               "window_k_to_k1": nrow["window"],
               "events_at_k1": nrow["events"]}
        if ph["info"]["scoreable"]:
            rec["phys"] = ph["phys"]
            if brow["applied"]:
                ap = F.read_json(os.path.join(run_dir(key), f"apply{_bsuf()}_{k}.json"))
                rec["bel_g"] = ap["guarded"]
                rec["bel_u"] = ap["unguarded"]
            else:
                rec["bel_g"] = ph["phys"]      # the prior: physics exactly
                rec["bel_u"] = ph["phys"]
        steps.append(rec)
    out = {"schema": "bvp-results/1", "key": key, "name": fire.name,
           "irwin": fire.irwin, "cell_m": fire.cell_m,
           "domain": [fire.bbox.west, fire.bbox.south, fire.bbox.east,
                      fire.bbox.north],
           "tile_shape": [fire.tile.spec.nrows, fire.tile.spec.ncols],
           "snapshots": [{"t_s": s["t_s"], "acres": s["acres"],
                          "pct_contained": s.get("pct_contained")}
                         for s in fire.snaps],
           "belief_rows": [{"k": r["k"], "t_s": r["t_s"],
                            "status": r["status"], "applied": r["applied"],
                            "h_new": r["h_new"], "events": r["events"],
                            "window": r["window"]} for r in b["rows"]],
           "steps": steps}
    path = results_path(key)
    write_json(path, out, compact=True)
    return out


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def _pool_run(tasks: list, fn, workers: int, label: str) -> None:
    if not tasks:
        return
    t0 = time.time()
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, *t): t for t in tasks}
        for fu in as_completed(futs):
            t = futs[fu]
            try:
                r = fu.result()
                done += 1
                print(f"[{label}] {r['key']} step {r['k']:2d} "
                      f"{r['s']:6.1f} s  ({done}/{len(tasks)}, "
                      f"{time.time() - t0:6.0f} s)", flush=True)
            except Exception as exc:
                print(f"[{label}] FAILED {t[:2]}: {exc!r}", flush=True)
                raise


def stage_physics(keys: list, workers: int, force: bool = False) -> None:
    tasks = []
    for key in keys:
        fire = get_fire(key)
        if not fire.has_tile():
            print(f"[physics] {key}: no LFPS tile yet -- skipped")
            continue
        for k in range(fire.n_steps()):
            p = os.path.join(run_dir(key), f"phys_{k}.json")
            if force or not os.path.exists(p):
                tasks.append((key, k))
    # longest fires first keeps the pool busy
    _pool_run(tasks, physics_task, workers, "physics")


def stage_apply(keys: list, workers: int, force: bool = False) -> None:
    tasks = []
    for key in keys:
        fire = get_fire(key)
        b = F.read_json(os.path.join(run_dir(key), f"belief{_bsuf()}.json"))
        for row in b["rows"][:-1]:
            k = row["k"]
            if not (row["applied"] and fire.step_info(k)["scoreable"]):
                continue
            p = os.path.join(run_dir(key), f"apply{_bsuf()}_{k}.json")
            if force or not os.path.exists(p):
                tasks.append((key, k, row["apply"]))
    _pool_run(tasks, apply_task, workers, "apply")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("physics", "belief", "apply",
                                        "collect", "all", "check-guard"))
    ap.add_argument("--fires", required=True)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--variant", choices=("", "wide"), default="")
    ap.add_argument("--belief-variant", choices=("", "nonovel", "norapid",
                                                 "free"), default="")
    args = ap.parse_args(argv)
    if args.variant:
        os.environ["BVP_VARIANT"] = args.variant
    if args.belief_variant:
        os.environ["BVP_BELIEF"] = args.belief_variant
    keys = [k.strip() for k in args.fires.split(",") if k.strip()]
    if args.command in ("physics", "all"):
        stage_physics(keys, args.workers, args.force)
    if args.command in ("belief", "all"):
        for key in keys:
            if not get_fire(key).has_tile():
                continue
            out = run_belief(key)
            n_app = sum(1 for r in out["rows"] if r["applied"])
            print(f"[belief] {key}: {len(out['rows'])} captures, belief "
                  f"applied at {n_app}", flush=True)
    if args.command in ("apply", "all"):
        stage_apply([k for k in keys if get_fire(k).has_tile()],
                    args.workers, args.force)
    if args.command in ("collect", "all"):
        for key in keys:
            if get_fire(key).has_tile():
                collect(key)
                print(f"[collect] wrote {results_path(key)}", flush=True)
    if args.command == "check-guard":
        for key in keys:
            check_guard(key)
            check_guard(key, want_slowed=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
