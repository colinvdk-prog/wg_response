"""PASS-2 independent re-derivation of A7: ensemble band vs observed error,
plus PIT extreme-bin masses, plus a small synthetic ensemble instrumentation.

Writes scratch_review/rv2sci_a7.json.
"""
from __future__ import annotations
import json
import math
import os
import statistics
import sys

sys.path.insert(0, r"C:\Users\colin\wg_response")
from wgresponse.contracts import ResponseConfig, GridSpec, TerrainTile
from wgresponse.spread import ensemble_arrival, _quantile, _stratified_member_draws

AUG = r"C:\Users\colin\wg_response\scratch_review\hindcast_aug_data\aug_replay.json"
OUT = r"C:\Users\colin\wg_response\scratch_review\rv2sci_a7.json"


def _pstdev(xs):
    return statistics.pstdev(xs) if len(xs) > 1 else 0.0


def band_vs_error(rows, variant):
    rs = [r for r in rows
          if r.get("variant") == variant
          and r.get("n_new_burnable", 0) > 0
          and r.get("obs_head_m")]
    ens, err = [], []
    inside = 0
    pairs = []
    for r in rs:
        p10, p50, obs = r["head_m_p10"], r["head_m_p50"], r["obs_head_m"]
        if p50 <= 0:
            continue
        e = p10 / p50
        er = p50 / obs
        ens.append(e)
        err.append(er)
        lo, hi = min(p10, p50), max(p10, p50)
        inn = lo <= obs <= hi
        if inn:
            inside += 1
        pairs.append(dict(
            name=r["name"], dt_h=r["dt_h"], obs=obs, p10=p10, p50=p50,
            ens_ratio=e, err_ratio=er, inside=inn,
        ))
    lerr = [math.log(x) for x in err]
    lens = [math.log(x) for x in ens]
    half = abs(statistics.median(lens)) if lens else 0.0
    need = _pstdev(lerr)
    return dict(
        n=len(rs),
        ens_median=statistics.median(ens) if ens else None,
        ens_min=min(ens) if ens else None,
        ens_max=max(ens) if ens else None,
        err_median=statistics.median(err) if err else None,
        err_min=min(err) if err else None,
        err_max=max(err) if err else None,
        sd_log_err=need,
        sd_log_ens=_pstdev(lens),
        band_halfwidth_log=half,
        underdispersion_x=(need / half) if half > 0 else None,
        inside_p10_p50=inside,
        inside_n=len(rs),
        pairs=pairs,
    )


def pool_pit(rows, variant):
    rs = [r for r in rows
          if r.get("variant") == variant and r.get("n_new_burnable", 0) > 0]
    n_bins = max(len(r.get("pit_hist") or []) for r in rs) if rs else 0
    pit = [0] * n_bins
    tot_cells = 0
    tot_pit = 0
    for r in rs:
        tot_cells += r["n_new_burnable"]
        h = r.get("pit_hist") or []
        for k, v in enumerate(h):
            pit[k] += v
            tot_pit += v
    n = tot_pit
    lo = pit[0] / n if n else None
    hi = pit[-1] / n if n else None
    return dict(
        n_pairs=len(rs), n_cells_burnable=tot_cells, pit_sum=n,
        pit_hist=pit, extreme_lo=lo, extreme_hi=hi,
        extreme_both=(lo + hi) if (lo is not None and hi is not None) else None,
    )


def perturb_math():
    cfg = ResponseConfig()
    # wind_scale = 1 + u_w * wind_perturb_frac * 0.5, u in [-1,1]
    w_max = cfg.wind_perturb_frac * 0.5
    m_max = cfg.moisture_perturb_frac * 0.5
    dir_lead = min(1.0, (cfg.horizon_h * 0.5) / 48.0)
    d_max = cfg.dir_perturb_deg * dir_lead
    # Hazen p10 for n=6
    n = cfg.ensemble_n
    pos = 0.10 * n - 0.5  # 0.10
    draws = _stratified_member_draws("probe-id", n - 1)
    w_scales = [max(0.05, 1.0 + u[0] * cfg.wind_perturb_frac * 0.5) for u in draws]
    m_scales = [max(0.05, 1.0 + u[1] * cfg.moisture_perturb_frac * 0.5) for u in draws]
    d_offs = [u[2] * cfg.dir_perturb_deg * dir_lead for u in draws]
    # synthetic 6-sample Hazen
    sample = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
    q10 = _quantile(sample, 0.10)
    q50 = _quantile(sample, 0.50)
    return dict(
        wind_perturb_frac_cfg=cfg.wind_perturb_frac,
        moisture_perturb_frac_cfg=cfg.moisture_perturb_frac,
        dir_perturb_deg_cfg=cfg.dir_perturb_deg,
        ensemble_n=n,
        realized_wind_half_frac=w_max,          # ±12.5%
        realized_moist_half_frac=m_max,         # ±10%
        realized_dir_max_deg=d_max,             # ±30 at 96h
        docs_claim_wind_pct=25.0,
        hazen_p10_pos=pos,
        hazen_p10_near_sample_min=abs(pos) < 0.2,
        example_q10=q10, example_q50=q50,
        example_p10_is_almost_min=abs(q10 - sample[0]) < 0.2,
        probe_draws_w_scales=w_scales,
        probe_draws_m_scales=m_scales,
        probe_draws_d_offs=d_offs,
        w_scale_span=(min(w_scales), max(w_scales)),
        m_scale_span=(min(m_scales), max(m_scales)),
        d_off_span=(min(d_offs), max(d_offs)),
    )


def synth_ensemble():
    """Tiny synthetic grass tile: measure internal head-band vs a truth member.

    Truth = unperturbed compute (member 0). Internal band = p10 vs p50 head
    distance from ignition after a fixed horizon. Cheap; not a hindcast.
    """
    from wgresponse.contracts import Raster, WeatherForecast
    from wgresponse import geo
    from wgresponse.weather import MockWeather
    nrows, ncols, cell = 61, 61, 90.0
    spec = GridSpec(north_lat=39.5, west_lon=-120.5, cell_m=cell,
                    nrows=nrows, ncols=ncols)
    n = nrows * ncols
    tile = TerrainTile(
        spec=spec,
        fuel=Raster(spec, [102.0] * n),
        slope_deg=Raster(spec, [0.0] * n),
        aspect_deg=Raster(spec, [-1.0] * n),
        elev_m=Raster(spec, [500.0] * n),
        source="rv2sci-a7",
    )
    t0 = 1_780_000_000.0
    ign_ll = geo.rc_to_latlon(spec, nrows // 2, ncols // 2)
    wx = MockWeather("rv2sci-a7", base_wind_mps=8.0, dir_deg=225.0,
                     temp_c=30.0, rh_pct=15.0, t0_s=t0).forecast(ign_ll, 24.0)
    cfg = ResponseConfig()
    r, c = nrows // 2, ncols // 2
    ign = [ign_ll]
    ens = ensemble_arrival(tile, ign, wx, t0, cfg, incident_id="rv2sci-a7")
    t_cut = t0 + 3.0 * 3600.0  # 3h
    ign_i = r * ncols + c

    def head_m(arr):
        burned = [i for i, t in enumerate(arr.data)
                  if t <= t_cut and not math.isinf(t)]
        if not burned:
            return 0.0
        # max chebyshev-ish Euclidean in cell units from ignition
        best = 0.0
        ir, ic = divmod(ign_i, ncols)
        for i in burned:
            rr, cc = divmod(i, ncols)
            d = math.hypot(rr - ir, cc - ic) * cell
            if d > best:
                best = d
        return best

    hp10 = head_m(ens.p10)
    hp50 = head_m(ens.p50)
    hnom = head_m(ens.members[0])
    member_heads = [head_m(m) for m in ens.members]
    ratio = (hp10 / hp50) if hp50 > 0 else None
    return dict(
        t_cut_h=3.0, n_members=len(ens.members),
        head_p10_m=hp10, head_p50_m=hp50, head_nominal_m=hnom,
        member_heads_m=member_heads,
        p10_over_p50=ratio,
        member_head_cv=(_pstdev(member_heads) / statistics.mean(member_heads)
                        if member_heads and statistics.mean(member_heads) else None),
    )


def main():
    data = json.load(open(AUG, encoding="utf-8"))
    rows = data["rows"]
    out = {
        "as_deployed": band_vs_error(rows, "as-deployed"),
        "calibration": band_vs_error(rows, "calibration"),
        "pit_as_deployed": pool_pit(rows, "as-deployed"),
        "pit_calibration": pool_pit(rows, "calibration"),
        "perturb_math": perturb_math(),
    }
    try:
        out["synth_ensemble"] = synth_ensemble()
    except Exception as e:
        out["synth_ensemble"] = dict(error=f"{type(e).__name__}: {e}")
    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1)
    print("wrote", OUT)
    a = out["as_deployed"]
    print(f"as-deployed n={a['n']}")
    print(f"  head_P10/head_P50 median={a['ens_median']:.3f}  "
          f"range {a['ens_min']:.3f}-{a['ens_max']:.3f}")
    print(f"  sd(log head-ratio P50/obs)={a['sd_log_err']:.3f}")
    print(f"  band half-width={a['band_halfwidth_log']:.3f}  "
          f"underdisp={a['underdispersion_x']:.1f}x")
    print(f"  observed inside [P10,P50]: {a['inside_p10_p50']}/{a['inside_n']}")
    p = out["pit_as_deployed"]
    print(f"  PIT {p['pit_hist']}  lo={100*p['extreme_lo']:.1f}%  "
          f"hi={100*p['extreme_hi']:.1f}%  both={100*p['extreme_both']:.1f}%")
    pm = out["perturb_math"]
    print(f"  realized wind ±{100*pm['realized_wind_half_frac']:.1f}% "
          f"(cfg {100*pm['wind_perturb_frac_cfg']:.0f}% * 0.5)")
    print(f"  synth={out['synth_ensemble']}")


if __name__ == "__main__":
    main()
