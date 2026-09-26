"""scratch_review/corridor_grid.py — THE VAIL -> ASPEN RESPONSE PROGRAM grid
(founder 2026-09-23: "build the response program from Vail to Aspen").

Six corridor community presets (simui.LOCATIONS: vail_co, beaver_creek_co,
eagle_co, glenwood_springs_co, basalt_carbondale_co, aspen_co) on their real
LANDFIRE tiles + OSM roads, the Rancho Santa Fe 18-scenario matrix per
community (intensity moderate/high/extreme x ignition 6/8 km upwind x wind
offset 0/+30/-30 off the hazard axis), 40 homes, under the LIVE watcher
physics (the 4-hour experiment's "honest" arm: model-error band 0.71,
extinction gate, phi-vector, spotting, grass floor 0.10, plan-on-roads,
24 h coating hold, deploy 15) — the deadlines crews really face, not the old
narrow band. Sim defaults (calibrated corridor stack, stage-1 cold-black,
ember radius 200 m, solver evals 20,000) are NOT overridden.

Arms = the corridor's basing question (where the crews sleep):
  local2      2 in-zone crews staged at the community's own safe zones
  local4      4 in-zone crews
  mixed2p2    2 in-zone + 2 corridor crews driving in from the Eagle base
              (SEMANTICS 9 reinforcements: arrival = measured OSM drive time
              under fire traffic + MOBILIZE_MIN, from corridor_drive_times.json)
  base1p3     1 in-zone + 3 from Eagle ("one local crew, fleet at the base";
              the session requires >= 1 in-zone crew)
  alerted2    ALERTED regime: extended tile, fire detected ~15 km out on the
              hazard bearing (offsets 0/+30/-30, high intensity), 2 in-zone
              crews, 60 h — the WATCH / hold / preposition doctrine
Every arm keeps the safety law; violations / overrun ticks are reported and
must stay 0 for an arm to count.

Per home the rows carry gelled / front-reached / nearest-burn distance
(opt_ember's belt machinery, imported unmodified) AND the detection-to-
arrival lead (probe_leadtime's truth-member-0 arrival), so one run yields
the ember-belt coating rate, coated / threatened, and the lead-time bands
(3-5 h = "four hours of notice").

Usage (one process per arm x map, incremental; PYTHONHASHSEED=0):
  py -3 scratch_review/corridor_grid.py --arm local2 [--maps vail_co ...] [--workers 12]
  py -3 scratch_review/corridor_grid.py --report
Writes scratch_review/corridor/<arm>__<map>.json and, with --report,
docs/calibration/vail_aspen_grid_2026-09-23.md.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import pathlib
import sys
from concurrent.futures import ProcessPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONHASHSEED", "0")

from scratch_review.bench_dispatch import BASE, HORIZON_TICKS  # noqa: E402
from scratch_review.opt_ember import _burn_dist_m, pool_stats  # noqa: E402
from wgresponse import geo, roads, simui  # noqa: E402
from wgresponse.contracts import seeded_rng  # noqa: E402
from wgresponse.simui import LOCATIONS, load_tile, wui_cells  # noqa: E402

CORRIDOR = ("vail_co", "beaver_creek_co", "eagle_co", "glenwood_springs_co",
            "basalt_carbondale_co", "aspen_co")
N_CLIENTS = 40
INTENS = ("moderate", "high", "extreme")
DISTS = (6000, 8000)
WIND_OFFS = (0, 30, -30)
ALERTED_DIST_M = 15000.0
ALERTED_TICKS = 240              # 60 h at 15 min
MOBILIZE_MIN = 15.0              # [ASSUMED] base crews roll 15 min after the first ignition
COMMON = dict(BASE)
COMMON.update({"dispatch_plan_on_roads": True, "dispatch_hold_h": 24.0,
               "deploy_min": 15.0})
HONEST = {"model_error_sd_log": 0.71, "ros_floor_extinction_gate": True,
          "slope_phi_vector": True}
#: A/B hooks (escape roads 2026-09-26, scratch_review/escape_roads_ab.py):
#: CORRIDOR_EXTRA_CFG (a JSON object of ResponseConfig overrides merged onto
#: CFG) and CORRIDOR_OUT_DIR (write the arm files somewhere else), read from
#: the environment so the spawned workers see them too. Unset = the grid of
#: record, unchanged.
_EXTRA_CFG = json.loads(os.environ.get("CORRIDOR_EXTRA_CFG") or "{}")
CFG = {**COMMON, **HONEST, **_EXTRA_CFG}
DRIVE_JSON = ROOT / "scratch_review" / "corridor_drive_times.json"
OUT_DIR = (pathlib.Path(os.environ["CORRIDOR_OUT_DIR"])
           if os.environ.get("CORRIDOR_OUT_DIR")
           else ROOT / "scratch_review" / "corridor")
BINS = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 8), (8, 12), (12, 16)]

ARMS = {
    # arm: (in-zone crews, corridor crews, fire_zone, ticks, corridor base)
    "local2": (2, 0, "standard", HORIZON_TICKS, None),
    "local4": (4, 0, "standard", HORIZON_TICKS, None),
    "mixed2p2": (2, 2, "standard", HORIZON_TICKS, "eagle"),
    "base1p3": (1, 3, "standard", HORIZON_TICKS, "eagle"),
    "alerted2": (2, 0, "extended", ALERTED_TICKS, None),
    # the corridor fleet in the ALERTED regime: 1 local + 3 from Eagle — with
    # hours of lead the 23-122 min drive should not matter; this arm measures
    # whether that is true community by community
    "alerted1p3": (1, 3, "extended", ALERTED_TICKS, "eagle"),
    # ROARING FORK BASE sensitivity (program §6.2): the corridor crews come
    # from Basalt / El Jebel instead of Eagle (Basalt -> Glenwood 34 min,
    # -> Aspen 40 min under fire traffic; its own community at Eagle's
    # in-town 8 min) — run on the three Roaring Fork-side communities.
    "mixed2p2_rf": (2, 2, "standard", HORIZON_TICKS, "basalt"),
    "base1p3_rf": (1, 3, "standard", HORIZON_TICKS, "basalt"),
    "alerted1p3_rf": (1, 3, "extended", ALERTED_TICKS, "basalt"),
}
DESC = {
    "local2": "2 in-zone crews at the community's safe zones",
    "local4": "4 in-zone crews",
    "mixed2p2": "2 in-zone + 2 corridor crews from the Eagle base (measured drive + 15 min)",
    "base1p3": "1 in-zone + 3 corridor crews from the Eagle base",
    "alerted2": "ALERTED: fire detected ~15 km out (extended tile, 60 h), 2 in-zone crews",
    "alerted1p3": "ALERTED: 1 in-zone + 3 corridor crews from the Eagle base",
    "mixed2p2_rf": "2 in-zone + 2 corridor crews from a Basalt / El Jebel base",
    "base1p3_rf": "1 in-zone + 3 corridor crews from a Basalt / El Jebel base",
    "alerted1p3_rf": "ALERTED: 1 in-zone + 3 corridor crews from a Basalt / El Jebel base",
}
ROARING_FORK = ("glenwood_springs_co", "basalt_carbondale_co", "aspen_co")


#: PLACEMENT SENSITIVITY (arms suffixed `_road`): the resort tiles carry
#: LANDFIRE 'developed' cells on the ski mountains (lift stations, on-mountain
#: restaurants) inside the WUI band the simulator seeds clients into — at
#: Vail 25% of the grid's placed homes are > 2 km from any road
#: (scratch_review/corridor_roadless.py). A real client has a driveway, so the
#: `_road` variant draws the same 40 homes from WUI cells within ROAD_HOME_M
#: of a road node (same seeded draw law, own namespace) and hands them to the
#: session through the client_locs seam. No engine change.
ROAD_HOME_M = 300.0
_ROAD_POOL: dict = {}


def road_homes(key: str, seed: str, n: int) -> list:
    pool = _ROAD_POOL.get(key)
    if pool is None:
        tile, _syn = load_tile(key, cell_m=90.0)
        g = roads.load_road_graph(key)
        pool = [p for p in (geo.rc_to_latlon(tile.spec, r, c) for r, c in wui_cells(tile))
                if g is not None and g.nearest_node(p, ROAD_HOME_M) is not None]
        _ROAD_POOL[key] = pool
    rng = seeded_rng("simui-clients-road", str(seed))
    return rng.sample(pool, n)


def base_arm(arm: str) -> str:
    return arm[:-5] if arm.endswith("_road") else arm


def crew_delay_min(map_key: str, base: str) -> float:
    """Minutes after the first ignition a corridor crew from `base` reaches
    this community: measured OSM drive time under fire traffic
    (scratch_review/corridor_roads.py; the all-season graph for Eagle, the
    community-to-community matrix for a Basalt base; a base's own community
    uses Eagle's in-town 8 min) + MOBILIZE_MIN."""
    d = json.load(open(DRIVE_JSON, encoding="utf-8"))
    if base == "eagle":
        mins = d["from_eagle_base_all_season"][map_key]["fire_min"]
    elif base == "basalt":
        if map_key == "basalt_carbondale_co":
            mins = d["from_eagle_base"]["eagle_co"]["fire_min"]      # in-town analog
        else:
            mins = d["center_to_center"][f"basalt_carbondale_co->{map_key}"]["fire_min"]
    else:
        raise ValueError(base)
    return round(mins + MOBILIZE_MIN, 1)


def eagle_delay_min(map_key: str) -> float:
    return crew_delay_min(map_key, "eagle")


def scenarios(map_key: str, arm: str) -> list:
    out = []
    if ARMS[base_arm(arm)][2] == "extended":
        for wind_off in WIND_OFFS:
            out.append(dict(map=map_key, wind_off=wind_off, dist=ALERTED_DIST_M,
                            inten="high",
                            seed=f"corr-alert|{map_key}|{wind_off:+d}|high"))
        return out
    for inten in INTENS:
        for dist in DISTS:
            for wind_off in WIND_OFFS:
                out.append(dict(map=map_key, wind_off=wind_off, dist=dist, inten=inten,
                                seed=f"corr|{map_key}|{wind_off:+d}|{dist}|{inten}"))
    return out


def _ignite(s, loc, wd, dist_m, inten, extended):
    """Standard tile: shrink toward center until a burnable cell takes.
    Extended: bench_alerted's bearing +-30 / shrink scan (far tiles include
    water/developed cells the surface model cannot ignite)."""
    bearings = (0, 10, -10, 20, -20, 30, -30) if extended else (0,)
    for db in bearings:
        for shrink in (1.0, 0.85, 0.7, 0.55):
            p = geo.destination(loc.center, wd + db, dist_m * shrink)
            try:
                s.ignite(p.lat, p.lon, intensity=inten, wind_dir_deg=wd)
                return True
            except ValueError:
                continue
    return False


def run_one(job):
    sc, arm = job
    n_local, n_eagle, fire_zone, ticks, base = ARMS[base_arm(arm)]
    loc = LOCATIONS[sc["map"]]
    wd = (loc.hazard_wind_dir_deg + sc["wind_off"]) % 360.0
    cfg = dict(CFG)
    delay = None
    if n_eagle:
        delay = crew_delay_min(sc["map"], base)
        cfg["crew_reinforce_1h_min"] = delay
    if fire_zone == "extended" and not simui.tile_file(sc["map"], fire_zone="extended").exists():
        return dict(seed=sc["seed"], map=sc["map"], arm=arm, error="no_ext_tile")
    locs = road_homes(sc["map"], sc["seed"], N_CLIENTS) if arm.endswith("_road") else None
    # place_near_road_m=0.0: the whole-band arms stay the WHOLE WUI band now
    # that the six corridor presets are road-placed by default (roads
    # inspection 2026-09-25), so the reactive (standard-tile) grid of record
    # reproduces byte for byte. The ALERTED arms (extended tile) now route on
    # data/roads/<key>_ext.wgr when it is cached — legs beyond the 18 km core
    # are road routes instead of straight chords — so they can differ from
    # the 2026-09-23 record, which routed on the standard graph.
    s = simui.SimSession(sc["map"], N_CLIENTS, n_local, seed=sc["seed"],
                         cfg_overrides=cfg, fire_zone=fire_zone, n_crews_1h=n_eagle,
                         client_locs=locs, place_near_road_m=0.0)
    if not _ignite(s, loc, wd, sc["dist"], sc["inten"], fire_zone == "extended"):
        return dict(seed=sc["seed"], map=sc["map"], arm=arm, error="off_tile")
    t0 = s._now_s
    st = None
    max_viol = 0
    ov_ticks = 0
    first_alert_lead_h = None
    for _ in range(ticks):
        st = s.tick()
        v = st["scoreboard"].get("violations", 0)
        max_viol = max(max_viol, v)
        if st.get("crew_overruns"):
            ov_ticks += 1
        if first_alert_lead_h is None and st.get("alert_feed"):
            etas = [c.get("eta_p10_s") for c in st["clients"]
                    if isinstance(c.get("eta_p10_s"), (int, float))
                    and math.isfinite(c.get("eta_p10_s"))]
            if etas:
                first_alert_lead_h = round((min(etas) - st["sim_now_s"]) / 3600.0, 2)
    now_s = s._now_s
    by_id = {cl.client_id: cl for cl in s._clients}
    truths = []
    for f in s._fires.values():
        ens = f.ens
        if ens is None:
            continue
        truths.append(ens.members[0] if getattr(ens, "members", None) else ens.p50)
    homes = []
    for row in st["clients"]:
        home = by_id[row["client_id"]].loc
        p50 = row.get("eta_p50_s")
        front = (row.get("state") == "impacted"
                 or (isinstance(p50, (int, float)) and p50 <= now_s))
        d = _burn_dist_m(s, home, now_s)
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
        homes.append(dict(gelled=bool(row.get("gel_applied")), front=bool(front),
                          dist=None if d is None else round(d, 1),
                          arr_h=None if arr is None else round(arr, 3),
                          outcome=row["outcome"]))
    sb = st["scoreboard"]
    return dict(seed=sc["seed"], map=sc["map"], arm=arm, inten=sc["inten"],
                wind_off=sc["wind_off"], dist=sc["dist"],
                viol=max_viol, ov_ticks=ov_ticks,
                min_margin_h=sb.get("min_margin_h"),
                served=sb.get("served"), withdrawn=sb.get("withdrawn"),
                eagle_delay_min=delay, first_alert_lead_h=first_alert_lead_h,
                homes=homes)


def run_arm(arm: str, maps, workers: int):
    OUT_DIR.mkdir(exist_ok=True)
    for m in maps:
        jobs = [(sc, arm) for sc in scenarios(m, arm)]
        with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as ex:
            rows = list(ex.map(run_one, jobs))
        out = OUT_DIR / f"{arm}__{m}.json"
        json.dump(dict(arm=arm, map=m, cfg=CFG, crews=ARMS[base_arm(arm)][:2],
                       base=ARMS[base_arm(arm)][4],
                       placement=("road" if arm.endswith("_road") else "wui"),
                       clients=N_CLIENTS, mobilize_min=MOBILIZE_MIN, rows=rows),
                  open(out, "w"), indent=1)
        st = summarize(rows)
        e1 = st["e1000"]
        c4, n4 = st["four_h"]
        print(f"{arm:<9} {m:<22} n={len(rows)} err={st['errors']} "
              f"e1000={e1[0]}/{e1[1]} {e1[2]:5.1f}%  thr={st['threatened'][0]}/{st['threatened'][1]} "
              f"4h={c4}/{n4}  viol={st['viol']} ovt={st['ov_ticks']} "
              f"minmarg={st['min_margin_h']} delay={st['delay']}", flush=True)


def summarize(rows) -> dict:
    ok = [r for r in rows if not r.get("error")]
    homes = [h for r in ok for h in r["homes"]]
    st = pool_stats(ok) if ok else {"front": (0, 0, 0.0), "e300": (0, 0, 0.0),
                                    "e500": (0, 0, 0.0), "e1000": (0, 0, 0.0),
                                    "viol": 0, "ov_ticks": 0, "min_margin_h": None,
                                    "coated_total": 0, "homes_total": 0}
    thr = [h for h in homes if h["outcome"] != "not_threatened"]
    st["threatened"] = (sum(1 for h in thr if h["gelled"]), len(thr))
    reached = [h for h in homes if h["arr_h"] is not None]
    st["reached"] = (sum(1 for h in reached if h["gelled"]), len(reached))
    st["bins"] = {}
    for lo, hi in BINS:
        sel = [h for h in reached if lo <= h["arr_h"] < hi]
        st["bins"][f"{lo}-{hi}h"] = (sum(1 for h in sel if h["gelled"]), len(sel))
    sel = [h for h in reached if 3.0 <= h["arr_h"] < 5.0]
    st["four_h"] = (sum(1 for h in sel if h["gelled"]), len(sel))
    sel = [h for h in reached if 6.0 <= h["arr_h"] < 8.0]
    st["six_eight_h"] = (sum(1 for h in sel if h["gelled"]), len(sel))
    st["n_scen"] = len(ok)
    st["errors"] = [r["seed"] + ":" + r["error"] for r in rows if r.get("error")]
    leads = [r["first_alert_lead_h"] for r in ok if r.get("first_alert_lead_h") is not None]
    st["first_alert_lead_h"] = (round(min(leads), 1), round(max(leads), 1)) if leads else None
    delays = {r["eagle_delay_min"] for r in ok if r.get("eagle_delay_min") is not None}
    st["delay"] = sorted(delays) if delays else None
    return st


def _pct(pair):
    c, n = pair[0], pair[1]
    return f"{c}/{n} = {100.0 * c / n:.1f}%" if n else "—"


def report():
    files = sorted(glob.glob(str(OUT_DIR / "*__*.json")))   # <arm>__<map>.json only
    by = {}
    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        by.setdefault(d["arm"], {})[d["map"]] = d["rows"]
    lines = ["# Vail -> Aspen response program — the measured grid (2026-09-23)", "",
             "Six corridor community presets on real LANDFIRE tiles + OSM roads, 40 homes",
             "each, 18 scenarios per community (moderate/high/extreme x 6/8 km x wind",
             "offset 0/+30/-30), LIVE watcher physics (model-error band 0.71, extinction",
             "gate, phi-vector, spotting, grass floor 0.10, plan-on-roads, 24 h hold, deploy",
             "15). Corridor crews from the Eagle base arrive after the measured all-season",
             f"OSM drive time under fire traffic + {MOBILIZE_MIN:.0f} min mobilization.",
             "Belt = coated among homes within 1 km of actual burn or front-reached;",
             "threatened = coated among homes the forecast gave a live arrival; 3-5 h =",
             "coated among homes the fire reached 3-5 h after detection (the '4 hours of",
             "notice' band). Violations / overrun ticks must be 0. scratch_review/corridor_grid.py.",
             ""]
    reactive = [a for a in ("local2", "local4", "mixed2p2", "base1p3",
                            "local2_road", "local4_road", "mixed2p2_road", "base1p3_road",
                            "mixed2p2_rf", "base1p3_rf", "mixed2p2_rf_road", "base1p3_rf_road")
                if a in by]
    lines += ["## Reactive arms — pooled over the six communities", "",
              "| arm | scenarios | belt e1000 | front-reached | coated / threatened | **3-5 h** | 6-8 h | viol / ovt | min margin h |",
              "|---|---|---|---|---|---|---|---|---|"]
    for arm in reactive:
        rows = [r for m in by[arm] for r in by[arm][m]]
        st = summarize(rows)
        desc = DESC.get(arm) or (DESC[base_arm(arm)] + " — homes within 300 m of a road")
        lines.append(f"| {arm} — {desc} | {st['n_scen']} | {_pct(st['e1000'])} | {_pct(st['front'])} "
                     f"| {_pct(st['threatened'])} | **{_pct(st['four_h'])}** | {_pct(st['six_eight_h'])} "
                     f"| {st['viol']} / {st['ov_ticks']} | {st['min_margin_h']} |")
    lines += ["", "## Per community (belt e1000 · coated/threatened · 3-5 h band)", "",
              "| community | Eagle delay min | " + " | ".join(reactive) + " |",
              "|---|---|" + "---|" * len(reactive)]
    for m in CORRIDOR:
        cells = []
        delay = "—"
        for arm in reactive:
            rows = by[arm].get(m)
            if not rows:
                cells.append("not run")
                continue
            st = summarize(rows)
            if st["delay"]:
                delay = ", ".join(f"{d:.0f}" for d in st["delay"])
            flag = "" if (st["viol"] == 0 and st["ov_ticks"] == 0) else f" ⚠ viol {st['viol']}/ovt {st['ov_ticks']}"
            cells.append(f"{st['e1000'][2]:.1f}% · {st['threatened'][0]}/{st['threatened'][1]} · "
                         f"{_pct(st['four_h'])}{flag}")
        lines.append(f"| {LOCATIONS[m].name} | {delay} | " + " | ".join(cells) + " |")
    for arm in ("alerted2", "alerted1p3", "alerted1p3_rf",
                "alerted2_road", "alerted1p3_road", "alerted1p3_rf_road"):
        if arm not in by:
            continue
        desc = DESC.get(arm) or (DESC[base_arm(arm)] + " — homes within 300 m of a road")
        lines += ["", f"## Alerted regime — {desc} (fire detected ~15 km out, 60 h)", "",
                  "| community | runs | first-alert lead h (min-max) | coated / threatened | belt e1000 | viol / ovt | errors |",
                  "|---|---|---|---|---|---|---|"]
        for m in CORRIDOR:
            rows = by[arm].get(m)
            if not rows:
                continue
            st = summarize(rows)
            lines.append(f"| {LOCATIONS[m].name} | {st['n_scen']} | {st['first_alert_lead_h']} "
                         f"| {_pct(st['threatened'])} | {_pct(st['e1000'])} | {st['viol']} / {st['ov_ticks']} "
                         f"| {len(st['errors'])} |")
        rows = [r for m in by[arm] for r in by[arm][m]]
        st = summarize(rows)
        lines.append(f"| **pooled** | {st['n_scen']} | {st['first_alert_lead_h']} | **{_pct(st['threatened'])}** "
                     f"| {_pct(st['e1000'])} | {st['viol']} / {st['ov_ticks']} | {len(st['errors'])} |")
    lines += ["", "## Lead-time bands, pooled (coated / reached)", "",
              "| arm | " + " | ".join(f"{lo}-{hi} h" for lo, hi in BINS) + " |",
              "|---|" + "---|" * len(BINS)]
    for arm in reactive + [a for a in ("alerted2", "alerted1p3", "alerted1p3_rf", "alerted2_road",
                                       "alerted1p3_road", "alerted1p3_rf_road") if a in by]:
        rows = [r for m in by[arm] for r in by[arm][m]]
        st = summarize(rows)
        lines.append(f"| {arm} | " + " | ".join(_pct(st["bins"][f"{lo}-{hi}h"]) for lo, hi in BINS) + " |")
    text = "\n".join(lines) + "\n"
    dest = ROOT / "docs" / "calibration" / "vail_aspen_grid_2026-09-23.md"
    dest.write_text(text, encoding="utf-8", newline="\n")
    print(text)
    print("wrote", dest)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", choices=sorted(ARMS) + sorted(a + "_road" for a in ARMS))
    ap.add_argument("--maps", nargs="*", default=list(CORRIDOR))
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        report()
    elif a.arm:
        run_arm(a.arm, a.maps, a.workers)
    else:
        ap.error("--arm or --report")
