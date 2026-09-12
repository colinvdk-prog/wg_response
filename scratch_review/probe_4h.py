"""THE 4-HOUR EXPERIMENT (founder 2026-09-11: "what would need to happen to
achieve higher rates of coating with 4h of notice?").

Per-home lead-time probe (scratch_review/probe_leadtime.py: detection = t0,
lead = ground-truth arrival hour at the home's own cell, member-0 truth field)
run as a LADDER of arms on the 5-map / 60-scenario suite, 40 homes, 8 crews:

  A_base      the simulator's optimized stack as the RSF plan measured it
              (deploy 15, slack 15, re-plan 15 min, base staging, scattered)
  B_honest    A + the LIVE watcher's physics (model-error band 0.71,
              extinction gate, phi-vector) — the deadlines crews really face
  C_overhead  B + deploy 10, slack floor 0, re-plan every 5 min
  D_staged    C + crews staged among the homes (all within ~30 min)
  E_cluster   D + homes served as 8 clusters (travel between homes ~minutes)
  F_perfect   E + PERFECT INFORMATION (P10 == truth: ensemble_n 1, no band)
              — the ceiling; NOT a deployable posture (its violations show
              why: a collapsed P10 has no margin for error)

Every arm keeps the safety law (30-min egress buffer, corridor checks,
fail-closed); violations are reported per arm and must stay 0 for any arm
to count. Output: scratch_review/probe_4h_<arm>.json (raw per-home rows) and
`--report` bins coated/reached by lead and writes
docs/calibration/leadtime_4h_experiment_2026-09-11.md.

Usage:
    py -3 scratch_review/probe_4h.py --arm C_overhead [--workers 4]
    py -3 scratch_review/probe_4h.py --report
"""
import argparse
import json
import math
import os
import pathlib
import sys
from concurrent.futures import ProcessPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONHASHSEED", "0")

from scratch_review.bench_dispatch import BASE, HORIZON_TICKS, scenarios  # noqa: E402
from scratch_review.opt_ember import cluster_locs  # noqa: E402
from wgresponse import geo, simui  # noqa: E402
from wgresponse.simui import LOCATIONS  # noqa: E402

N_CLIENTS = 40
N_CREWS = 8

COMMON = dict(BASE)
COMMON.update({"dispatch_plan_on_roads": True, "dispatch_hold_h": 24.0})
HONEST = {"model_error_sd_log": 0.71, "ros_floor_extinction_gate": True,
          "slope_phi_vector": True}
OVERHEAD = {"deploy_min": 10.0, "slack_min": 0.0, "replan_period_s": 300.0}
PERFECT = {"ensemble_n": 1, "model_error_sd_log": 0.0}

ARMS = {
    # arm: (cfg overrides, placement, staging)
    "A_base": ({**COMMON, "deploy_min": 15.0}, "scattered", "default"),
    "B_honest": ({**COMMON, "deploy_min": 15.0, **HONEST}, "scattered", "default"),
    "C_overhead": ({**COMMON, **HONEST, **OVERHEAD}, "scattered", "default"),
    "D_staged": ({**COMMON, **HONEST, **OVERHEAD}, "scattered", "near"),
    "E_cluster": ({**COMMON, **HONEST, **OVERHEAD}, "cluster8", "near"),
    "F_perfect": ({**COMMON, **HONEST, **OVERHEAD, **PERFECT}, "cluster8", "near"),
    # attribution arms (added after the first ladder: C fell below B)
    "C1_deploy10": ({**COMMON, **HONEST, "deploy_min": 10.0}, "scattered", "default"),
    "C2_slack0": ({**COMMON, **HONEST, "deploy_min": 15.0, "slack_min": 0.0}, "scattered", "default"),
    "G_perfect_scatter": ({**COMMON, **HONEST, "deploy_min": 15.0, **PERFECT}, "scattered", "default"),
}
BINS = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 8), (8, 12), (12, 16)]


def run_one(job):
    sc, cfg_over, placement, staging = job
    loc = LOCATIONS[sc["map"]]
    wd = (loc.hazard_wind_dir_deg + sc["wind_off"]) % 360.0
    locs = None
    if placement != "scattered":
        k = {"cluster1": 1, "cluster4": 4, "cluster8": 8}[placement]
        locs = cluster_locs(sc["map"], N_CLIENTS, k, sc["seed"],
                            bounds_poly=getattr(loc, "bounds_poly", ()))
    crew_starts = None
    if staging == "near":
        probe = simui.SimSession(sc["map"], N_CLIENTS, N_CREWS, seed=sc["seed"],
                                 cfg_overrides=dict(cfg_over),
                                 fire_zone="standard", client_locs=locs)
        homes = [c.loc for c in probe._clients]
        crew_starts = [homes[(i * len(homes)) // N_CREWS] for i in range(N_CREWS)]
    s = simui.SimSession(sc["map"], N_CLIENTS, N_CREWS, seed=sc["seed"],
                         cfg_overrides=dict(cfg_over), fire_zone="standard",
                         client_locs=locs, crew_locs=crew_starts)
    for shrink in (1.0, 0.85, 0.7, 0.55):
        p = geo.destination(loc.center, wd, sc["dist"] * shrink)
        try:
            s.ignite(p.lat, p.lon, intensity=sc["inten"], wind_dir_deg=wd)
            break
        except ValueError:
            continue
    else:
        return dict(seed=sc["seed"], error="off_tile")
    t0 = s._now_s
    st = None
    viol_max = 0
    ovr_max = 0
    for _ in range(HORIZON_TICKS):
        st = s.tick()
        viol_max = max(viol_max, st["scoreboard"].get("violations", 0))
        ovr_max = max(ovr_max, len(st.get("crew_overruns", [])))
    by_id = {cl.client_id: cl for cl in s._clients}
    truths = [f.ens.members[0] for f in s._fires.values() if f.ens is not None]
    homes = []
    for row in st["clients"]:
        home = by_id[row["client_id"]].loc
        arr = None
        for truth in truths:
            rc = geo.latlon_to_rc(truth.spec, home)
            if rc is None:
                continue
            t = truth.data[rc[0] * truth.spec.ncols + rc[1]]
            if math.isfinite(t):
                h = (t - t0) / 3600.0
                if arr is None or h < arr:
                    arr = h
        homes.append(dict(arr_h=None if arr is None else round(arr, 3),
                          gelled=bool(row.get("gel_applied")),
                          outcome=row["outcome"]))
    return dict(seed=sc["seed"], map=sc["map"], inten=sc["inten"],
                viol=viol_max, overruns=ovr_max,
                served=st["scoreboard"].get("served"), homes=homes)


def run_arm(arm, workers):
    cfg, placement, staging = ARMS[arm]
    jobs = [(sc, cfg, placement, staging) for sc in scenarios()]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(run_one, jobs))
    out = ROOT / "scratch_review" / f"probe_4h_{arm}.json"
    json.dump(dict(arm=arm, cfg=cfg, placement=placement, staging=staging,
                   clients=N_CLIENTS, crews=N_CREWS, rows=rows),
              open(out, "w"), indent=1)
    print(f"{arm}: {len(jobs)} scenarios -> {out}")


def summarize(arm):
    d = json.load(open(ROOT / "scratch_review" / f"probe_4h_{arm}.json"))
    rows = [r for r in d["rows"] if not r.get("error")]
    homes = [h for r in rows for h in r["homes"] if h["arr_h"] is not None]
    out = {"arm": arm, "scenarios": len(rows), "reached": len(homes),
           "coated_reached": sum(1 for h in homes if h["gelled"]),
           "viol_scenarios": sum(1 for r in rows if r["viol"]),
           "viol_max": max((r["viol"] for r in rows), default=0),
           "overrun_scenarios": sum(1 for r in rows if r.get("overruns")),
           "bins": {}}
    for lo, hi in BINS:
        sel = [h for h in homes if lo <= h["arr_h"] < hi]
        out["bins"][f"{lo}-{hi}h"] = (sum(1 for h in sel if h["gelled"]), len(sel))
    sel = [h for h in homes if 3.0 <= h["arr_h"] < 5.0]
    out["four_h"] = (sum(1 for h in sel if h["gelled"]), len(sel))
    return out


def report():
    arms = [a for a in ARMS if (ROOT / "scratch_review" / f"probe_4h_{a}.json").exists()]
    sums = [summarize(a) for a in arms]
    lines = ["# The 4-hour experiment — 2026-09-11", "",
             "Coated-in-time fraction of homes the fire actually reached, by lead",
             "(detection → ground-truth arrival at the home's cell), across the 5-map",
             f"/ 60-scenario suite, {N_CLIENTS} homes, {N_CREWS} crews (scratch_review/probe_4h.py).",
             "Arms are cumulative; F is the perfect-information ceiling, not a posture.",
             "", "| arm | reached | coated | **3–5 h** | 2–3 h | 3–4 h | 4–5 h | 5–6 h | 6–8 h | 8–12 h | 12–16 h | viol scen / max | overrun scen |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in sums:
        b = s["bins"]

        def f(k):
            c, n = b[k]
            return f"{c}/{n} = {c / n:.0%}" if n else "—"

        c4, n4 = s["four_h"]
        lines.append(f"| {s['arm']} | {s['reached']} | {s['coated_reached']} ({s['coated_reached'] / max(1, s['reached']):.1%}) "
                     f"| **{c4}/{n4} = {c4 / n4:.0%}** | {f('2-3h')} | {f('3-4h')} | {f('4-5h')} | {f('5-6h')} | {f('6-8h')} | {f('8-12h')} | {f('12-16h')} "
                     f"| {s['viol_scenarios']} / {s['viol_max']} | {s['overrun_scenarios']} |" if n4 else
                     f"| {s['arm']} | {s['reached']} | {s['coated_reached']} | — | ... |")
    text = "\n".join(lines) + "\n"
    dest = ROOT / "docs" / "calibration" / "leadtime_4h_experiment_2026-09-11.md"
    dest.write_text(text, encoding="utf-8")
    print(text)
    print("wrote", dest)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=sorted(ARMS))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        report()
    elif a.arm:
        run_arm(a.arm, a.workers)
    else:
        ap.error("--arm or --report")
