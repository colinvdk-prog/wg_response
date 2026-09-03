"""A/B attribution rerun for the moderate-preset safety counters recorded in
scratch_review/census_wind_moderate_c4.json (stats.viol=4, ov_ticks=14) and
census_wind_moderate_c8.json (viol=9, ov_ticks=13).

Those two files ran wgresponse.simui.SimSession under the SIMULATOR's
current defaults, which -- whenever a cfg_overrides key is simply absent --
silently force ON the "calibrated corridor stack" (founder flip 2026-07-30,
commit d7f490f; see wgresponse/simui.py _init_state, ~lines 1490-1508):
    corridor_erosion_tiered       omitted -> True  (fuel-tiered erosion,
                                                     radii 550/200/150 m)
    escape_hysteresis_margin_min  omitted -> 20.0  (SIM_ESCAPE_HYSTERESIS_MIN)
    transit_cold_black_by_class   omitted -> True
That stack's validation runs (byte-identity / 0 viol-ovt derivations) only
ever exercised the "high"/"extreme" INTENSITY_WEATHER presets -- never
"moderate" (18 mph) before census_wind_moderate_c4/c8.json. This script
reruns the identical moderate scenario list with those three knobs
EXPLICITLY pinned to their pre-flip LEGACY values (corridor_erosion_tiered=
False, escape_hysteresis_margin_min=0.0, transit_cold_black_by_class=False
-- the same trio tests/test_simui_policy.py calls "CORRIDOR pinned to
LEGACY") to see whether the nonzero counters survive with the flip reverted.

Does NOT edit opt_ember.py / bench_dispatch.py / census_windregime.py. All
scenario-grid + scoring machinery (scenarios, WIND_MPH, run_one, pool_stats,
BASE) is imported from scratch_review.census_windregime, which itself
imports run_one/pool_stats from scratch_review.opt_ember (identical
physics/scoring code, zero duplication risk) and defines scenarios()/
WIND_MPH/BASE itself -- so this script duplicates none of the grid or cfg
logic, it only layers cfg overrides on top of that exact recipe.

Presets (cfg overrides layered on census_windregime's own cfg recipe):
  legacy        corridor_erosion_tiered=False, escape_hysteresis_margin_min=
                0.0, transit_cold_black_by_class=False  (full pre-flip revert)
  corridor_only corridor_erosion_tiered=False ONLY (hysteresis + cold-black
                left at the simulator's auto-defaults) -- isolates the
                fuel-tiered-erosion piece from the other two knobs
  none          {} (no overrides -- reproduces the as-recorded CURRENT
                simulator defaults; only useful with --trace-seed)

Modes:
  A/B pool mode (default): reruns the full moderate scenario list at each
  --crews value under --preset, writes scratch_review/census_modviol_
  <preset-tag>_c<crews>.json (same tag/intensity/wind_mph/params/stats/rows
  shape as census_windregime's own output -- params gets one added
  'cfg_overrides' key for traceability) and prints one opt_ember-format
  summary line per crew count.

  --trace-seed SEED mode: skips the pool entirely and reruns ONE scenario
  in-process (single SimSession, no ProcessPoolExecutor) so every tick's
  raw crew_overruns list can be captured -- run_one() only returns the
  ov_ticks COUNT and discards this detail. Writes scratch_review/
  census_modviol_trace_<preset-tag>_<map>_<dist>_c<crews>.json with one
  entry per overrun event (tick, crew_id, reason, status, mode) plus a
  self-check max_viol/ov_ticks recompute (should match the recorded row
  when --preset none is used with the same seed/crews as a recorded file).

CLI:
  py -3 scratch_review/census_modviol_ab.py [--preset legacy|corridor_only|none]
      [--crews 4 8] [--intensity moderate] [--clients 40] [--deploy 10]
      [--egress 30] [--ember-radius 200] [--hold 24] [--workers 10]
      [--trace-seed SEED]

Run (PYTHONHASHSEED=0, matches census_windregime.py's / rsf_grid.py's own
documented convention -- this script does NOT set it itself, since
PYTHONHASHSEED only takes effect at interpreter startup):
  PYTHONHASHSEED=0 py -3 scratch_review/census_modviol_ab.py --preset legacy
  PYTHONHASHSEED=0 py -3 scratch_review/census_modviol_ab.py --preset corridor_only
"""
import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch_review.census_windregime import (  # noqa: E402
    scenarios, WIND_MPH, run_one, pool_stats, BASE)

PRESETS = {
    "legacy": {
        "corridor_erosion_tiered": False,
        "escape_hysteresis_margin_min": 0.0,
        "transit_cold_black_by_class": False,
        # SEM-10e (2026-08-02): pinned OFF so 'legacy' keeps meaning the
        # full pre-flip revert after the proactive-escape sim default landed.
        "escape_proactive_lead_min": 0.0,
    },
    "corridor_only": {
        "corridor_erosion_tiered": False,
    },
    "none": {},
}
TAG_NAME = {"legacy": "legacy", "corridor_only": "corridoronly", "none": "current"}


def _base_cfg(deploy, egress, ember_radius, hold):
    cfg = dict(BASE)
    cfg.update({"deploy_min": deploy,
                "dispatch_plan_on_roads": True,
                "dispatch_hold_h": hold,
                "egress_buffer_min": egress,
                "ember_threat_radius_m": ember_radius})
    return cfg


def run_crews(preset, n_crews, intensity, clients, deploy, egress,
              ember_radius, hold, workers):
    cfg = _base_cfg(deploy, egress, ember_radius, hold)
    cfg.update(PRESETS[preset])

    scen = scenarios(intensity)
    jobs = [(sc, cfg, clients, n_crews, "scattered", "default")
            for sc in scen]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(run_one, jobs))
    st = pool_stats(rows)
    tag = f"census_modviol_{TAG_NAME[preset]}_c{n_crews}"
    payload = dict(tag=tag, intensity=intensity, wind_mph=WIND_MPH[intensity],
                   params=dict(crews=n_crews, deploy=deploy, egress=egress,
                               ember_radius=ember_radius, hold=hold,
                               clients=clients, placement="scattered",
                               staging="default",
                               cfg_overrides=PRESETS[preset]),
                   stats=st, rows=rows)
    out = f"scratch_review/{tag}.json"
    json.dump(payload, open(out, "w"), indent=1)
    f = st["front"]
    e1 = st["e1000"]
    e3 = st["e300"]
    e5 = st["e500"]
    print(f"{tag:<28} n={len(scen)} front={f[0]}/{f[1]} {f[2]:5.1f}%  "
          f"e300={e3[2]:5.1f}%  e500={e5[2]:5.1f}%  "
          f"e1000={e1[0]}/{e1[1]} {e1[2]:5.1f}%  viol={st['viol']} "
          f"ovt={st['ov_ticks']} minmarg={st['min_margin_h']} "
          f"coated={st['coated_total']}")
    print("wrote", out)
    return payload


def trace_scenario(seed, preset, n_crews, intensity, clients, deploy, egress,
                    ember_radius, hold):
    """In-process (no pool) single-scenario rerun that captures every
    crew_overruns entry per tick. Deliberately does NOT call opt_ember.
    run_one -- run_one only returns the ov_ticks COUNT and discards the
    per-event crew_id/reason/status/mode this trace needs -- so the
    SimSession/ignite/tick sequence is inlined here, copied verbatim from
    run_one's own recipe (same LOCATIONS lookup, same shrink-retry ladder,
    same HORIZON_TICKS loop) so the physics path is identical, just with an
    extra capture step added."""
    from wgresponse import geo, simui
    from wgresponse.simui import LOCATIONS
    from scratch_review.bench_dispatch import HORIZON_TICKS

    scen = [s for s in scenarios(intensity) if s["seed"] == seed]
    if not scen:
        raise SystemExit(f"no {intensity!r} scenario with seed {seed!r}")
    sc = scen[0]
    cfg = _base_cfg(deploy, egress, ember_radius, hold)
    cfg.update(PRESETS[preset])

    loc = LOCATIONS[sc["map"]]
    wd = (loc.hazard_wind_dir_deg + sc["wind_off"]) % 360.0
    s = simui.SimSession(sc["map"], clients, n_crews, seed=sc["seed"],
                         cfg_overrides=dict(cfg), fire_zone="standard")
    for shrink in (1.0, 0.85, 0.7, 0.55):
        p = geo.destination(loc.center, wd, sc["dist"] * shrink)
        try:
            s.ignite(p.lat, p.lon, intensity=sc["inten"], wind_dir_deg=wd)
            break
        except ValueError:
            continue
    else:
        raise SystemExit("off_tile on every shrink factor")

    events = []
    max_viol = 0
    ov_ticks = 0
    for tick in range(HORIZON_TICKS):
        st = s.tick()
        v = st["scoreboard"].get("violations", 0)
        max_viol = max(max_viol, v)
        overruns = st.get("crew_overruns") or []
        if overruns:
            ov_ticks += 1
        for o in overruns:
            events.append(dict(tick=tick, crew_id=o["crew_id"],
                               reason=o["reason"], status=o["status"],
                               mode=o["mode"],
                               nominal_arrival_s=o.get("nominal_arrival_s"),
                               eroded_p10_s=o.get("eroded_p10_s")))
    return dict(seed=seed, map=sc["map"], dist=sc["dist"], inten=sc["inten"],
                preset=preset, cfg_overrides=PRESETS[preset], crews=n_crews,
                n_ticks=HORIZON_TICKS, recheck_max_viol=max_viol,
                recheck_ov_ticks=ov_ticks, min_margin_h=st["scoreboard"].get(
                    "min_margin_h"), overrun_events=events)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="legacy", choices=tuple(PRESETS))
    ap.add_argument("--crews", type=int, nargs="+", default=[4, 8])
    ap.add_argument("--intensity", default="moderate",
                     choices=("low", "moderate"))
    ap.add_argument("--clients", type=int, default=40)
    ap.add_argument("--deploy", type=float, default=10.0)
    ap.add_argument("--egress", type=float, default=30.0)
    ap.add_argument("--ember-radius", type=float, default=200.0)
    ap.add_argument("--hold", type=float, default=24.0)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--trace-seed", default=None,
                     help="skip the A/B pool run; rerun ONE scenario "
                          "in-process and dump every crew_overruns event. "
                          "Uses --crews[0] as the single crew count.")
    args = ap.parse_args()
    if os.environ.get("PYTHONHASHSEED") != "0":
        print("WARNING: PYTHONHASHSEED != '0' -- results may not match the "
              "recorded census_wind_moderate_c4/c8.json determinism "
              "convention (see census_windregime.py / rsf_grid.py headers)",
              file=sys.stderr)

    if args.trace_seed:
        n_crews = args.crews[0]
        result = trace_scenario(args.trace_seed, args.preset, n_crews,
                                 args.intensity, args.clients, args.deploy,
                                 args.egress, args.ember_radius, args.hold)
        safe = f"{result['map']}_{result['dist']:.0f}"
        tag = f"census_modviol_trace_{TAG_NAME[args.preset]}_{safe}_c{n_crews}"
        out = f"scratch_review/{tag}.json"
        json.dump(result, open(out, "w"), indent=1)
        print(f"{tag}: recheck viol={result['recheck_max_viol']} "
              f"ov_ticks={result['recheck_ov_ticks']} "
              f"min_margin_h={result['min_margin_h']} "
              f"events={len(result['overrun_events'])}")
        for e in result["overrun_events"]:
            print(" ", e)
        print("wrote", out)
        return

    for n_crews in args.crews:
        run_crews(args.preset, n_crews, args.intensity, args.clients,
                   args.deploy, args.egress, args.ember_radius, args.hold,
                   args.workers)


if __name__ == "__main__":
    main()
