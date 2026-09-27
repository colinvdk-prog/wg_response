"""wgresponse.cli — argparse entrypoints.

Subcommands:
    simulate   --scenario --hours --out [--seed]      run a deterministic sim
    forecast   --lat --lon [--live]                   point threat ETA (mock default)
    plan       --in <sim_*_console.json>              re-plan dispatch, print table
    serve      --port [--demo]                        ops HTTP server (+console)
    pull-feeds --bbox W,S,E,N [--offline]             fused live incident table
    watch      --aoi "W,S,E,N"|zones.json [...]       the live operator daemon
    simui      [--port 8878] [--open] [--smoke]       the interactive simulator UI

`simui` (SIM-3, contracts cli seam; docs/SIMULATOR_DESIGN.md) serves
console/simulator.html at /simulator plus the /api/sim/* session API off
wgresponse.server — the REAL engine (ensemble spread, alert ladder, dispatch,
corridor/egress safety + the per-tick SAFETY AUDIT) behind an interactive
browser page. It is a LOCAL DEMONSTRATION INSTRUMENT: when neither
WGRESP_API_KEY nor WGRESP_DEV_OPEN is configured it sets WGRESP_DEV_OPEN=1
for itself (loudly; binding stays 127.0.0.1 by default) — a configured
operator key always wins. --smoke runs a self-test over the live HTTP API
(new session, ignite, 3 engine ticks, print the safety scoreboard) and exits
0 only when the SAFETY AUDIT reports 0 violations.

`watch` (Sprint 2 C1/C2, contracts cli seam) is the thing that actually RUNS
cycle() against the live world: it builds the live stack (WFIGS + FIRIS +
CAL FIRE feeds, + FIRMS when env FIRMS_MAP_KEY is set; NWS weather; LANDFIRE
LFPS terrain when the decode deps are importable, else SyntheticTerrain with
a LOUD warning; mock or Dynamo client registry by flag), threads the REQUIRED
--aoi bbox into every feed fetch AND the monitor, runs a cycle every --period
seconds (default cfg.poll_period_s), serves the ops console + /api/state +
/healthz off the live loop state, persists alert/notification/plan state via
wgresponse.persist.StateStore after EVERY cycle, restores it on startup
(restart re-fires nothing), and persists once more on Ctrl-C before exiting.
The AOI is either a bare "west,south,east,north" bbox or a zones JSON file:

    {"aoi": [w, s, e, n]}                                  — or —
    {"zones": [{"name": "...", "bbox": [w, s, e, n]}, ...],
     "safe_zones": [{"zone_id": "Z1", "lat": .., "lon": .., "name": "..."}]}

(multi-zone files watch the UNION bbox; safe_zones feed the dispatcher —
without any, dispatch fails closed and every job is unserved).

TWO WORLDS — A LIVE MAP AND A TEST MAP (2026-09-27; contracts SAFETY
SEMANTICS 16; wgresponse.worlds; founder: "we need a test map and a live map
so all simulations or drills do not impact real conditions"). The daemon runs
the LIVE world (real feeds, registry, crews, ladder, plan, notifier, state
file) and a fully isolated TEST world in one process. The live world accepts
no seeded fire; the TEST world sees the live incidents and forecasts
READ-ONLY (the live cycle's own snapshot — never recomputed), adds SEEDED
incidents (W1, contracts SAFETY SEMANTICS 12: a user lights a fire at a point
and the same engine path treats it exactly like a feed incident) and runs its
own ladder, its own virtual crews and its own plan; everything it emits goes
to the drill log; it persists under <state-dir>/test_world/ (and the S3
prefix's test_world/). Routes (authed like every write path —
X-Internal-API-Key unless WGRESP_DEV_OPEN=1):

    GET    /api/test/state         the TEST world's /api/state (+ world, banner,
                                   drill, seeds, live_crews, drill_log)
    POST   /api/test/seed          {lat, lon, intensity?, wind_dir_deg?,
                                   weather?, note?, created_by?} -> 201
                                   {incident_id: 'seeded:<uuid>', seed, drill};
                                   400 = bad body / point outside the AOI;
                                   409 = too many active seeds. Starts a drill
                                   (clones the live crews + ladder + plan)
    GET    /api/test/seed[/<id>]   every seed (active + recently retired) with
                                   its weather mode, last-cycle domain and pair
                                   states / one seed (404 unknown)
    DELETE /api/test/seed/<id>     retire (?by=name): absent from the next test
                                   cycle; its pairs take the absence law (404
                                   unknown, 409 already retired)
    POST   /api/test/crews/reset   re-clone the virtual crews from the live
                                   roster now
    POST   /api/test/crews/place   {crew_id, lat, lon, status?, name?} stage one
                                   virtual crew
    POST   /api/test/reset         wipe the test world back to IDLE
    GET    /api/world/summary?world=test   the TEST map's read model
    /api/seed                      GET = a read-only alias of the test list
                                   (one release); POST / DELETE = 409 (the
                                   live world refuses — use /api/test/seed)

Weather: live at the fire unless the seed overrides it (intensity = the
simulator's preset conditions, weather = explicit conditions, wind_dir_deg =
a fixed direction). A seed/retire persists the TEST namespace immediately and
re-runs the TEST world at once — it never wakes, delays or touches the live
loop. The live notifier keeps the seeded.SeedSafeNotifier wrapper as defense
in depth; the live /api/state never lists a seeded fire.
W6 THIN WORLD VIEWER (docs/LIVE_WORLD_PLAN.md §3 step 1 + "W6 thin viewer
(2026-09-26)"): the watch daemon also serves
    GET /world               console/world.html — one zoomable national map
                             over the daemon's state (open like /console: the
                             page is a key-prompt UI and carries no data)
    GET /api/world/summary   AUTHED, read-only: counts, the ranked list
                             "incidents threatening clients" (domain incidents
                             first by earliest client P10 arrival, then the
                             rest by acreage), per-incident domain facts, the
                             cycle's "why no domain?" note, display-simplified
                             perimeters and the P50 rings of every domain —
                             wgresponse.worldview, built once per cycle
                             (_WatchDaemon.world_summary); ?since=<t_s> equal
                             to the cached cycle time answers
                             {"unchanged": true} so a 15 s poll stays light.
With `--aoi data/aoi_conus.json` the AOI is the contiguous US (the feed
queries take the envelope; WFIGS pages at 2,000 features per layer) and
`--registry mock-presets` seeds --preset-homes (default 40) demo homes on
EVERY simulator preset with the simulator's own WUI placement
(clients.preset_client_registry), so live fires near our communities build
domains and everything else stays a marker.

Real modules are imported lazily inside each subcommand so `--help` and the
mock paths work before sibling modules land. All times printed are hours
relative to the relevant anchor (stated per column); config units follow
wgresponse.contracts.

Usage: py -3 -m wgresponse.cli <subcommand> ...
"""
from __future__ import annotations

import dataclasses
import argparse
import json
import logging
import math
import os
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

#: DURABLE STATE (docs/DURABLE_STATE.md): s3://bucket/prefix that mirrors the
#: watcher's alert state and LANDFIRE cache across container restarts.
_STATE_S3_ENV = "WGRESP_STATE_S3"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wgresponse",
                                description="Wildfire Guardian response engine CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("simulate", help="run a deterministic scenario simulation")
    ps.add_argument("--scenario", default="canyon_wind_event",
                    help="scenario name (see wgresponse.sim.scenario_names)")
    ps.add_argument("--hours", type=float, default=24.0,
                    help="simulated hours to run (default 24)")
    ps.add_argument("--out", default="out", help="output directory (default ./out)")
    ps.add_argument("--seed", default="default", help="layout jitter seed")
    ps.set_defaults(fn=_cmd_simulate)

    pf = sub.add_parser("forecast", help="threat ETA forecast for a point")
    pf.add_argument("--lat", type=float, required=True, help="latitude degrees")
    pf.add_argument("--lon", type=float, required=True, help="longitude degrees")
    pf.add_argument("--live", action="store_true",
                    help="use live feeds/terrain/weather (default: mocks)")
    pf.set_defaults(fn=_cmd_forecast)

    pp = sub.add_parser("plan", help="re-plan dispatch from a sim console JSON")
    pp.add_argument("--in", dest="infile", required=True,
                    help="path to out/sim_<name>_console.json")
    pp.set_defaults(fn=_cmd_plan)

    pv = sub.add_parser("serve", help="run the ops HTTP server + console")
    pv.add_argument("--port", type=int, default=8877)
    pv.add_argument("--host", default="127.0.0.1")
    pv.add_argument("--demo", action="store_true",
                    help="preload a sim result and replay crew movement")
    pv.add_argument("--scenario", default="canyon_wind_event",
                    help="scenario backing --demo")
    pv.add_argument("--hours", type=float, default=24.0, help="demo sim hours")
    pv.add_argument("--seed", default="default")
    pv.add_argument("--out", default="out", help="where demo sim JSON lives/goes")
    pv.add_argument("--demo-cycle-s", type=float, default=2.0,
                    help="real seconds per replayed sim cycle (default 2)")
    pv.set_defaults(fn=_cmd_serve)

    pl = sub.add_parser("pull-feeds", help="fetch + fuse live fire feeds")
    pl.add_argument("--bbox", required=True, help="west,south,east,north degrees")
    pl.add_argument("--offline", action="store_true",
                    help="skip all network access (prints 'no incidents')")
    pl.set_defaults(fn=_cmd_pull_feeds)

    pw = sub.add_parser(
        "watch",
        help="the live operator daemon: poll feeds over an AOI, alert, "
             "dispatch, persist, serve the console")
    pw.add_argument("--aoi", required=True,
                    help='REQUIRED area of interest: "west,south,east,north" '
                         "degrees, or a zones JSON file (see module help)")
    pw.add_argument("--period", type=float, default=None,
                    help="seconds between cycles (default: config "
                         "poll_period_s = 300)")
    pw.add_argument("--state-dir", default="state",
                    help="persistence directory (alert/notification/plan "
                         "state survives restart; default ./state)")
    pw.add_argument("--port", type=int, default=8877)
    pw.add_argument("--host", default="127.0.0.1")
    tgroup = pw.add_mutually_exclusive_group()
    tgroup.add_argument("--live-terrain", action="store_true",
                        help="REQUIRE live LANDFIRE terrain (error out if the "
                             "decode deps are missing)")
    tgroup.add_argument("--synthetic-terrain", action="store_true",
                        help="force SyntheticTerrain (rehearsal mode; LOUD "
                             "warning — not real fuels)")
    pw.add_argument("--registry", choices=("mock", "mock-presets", "dynamo"),
                    default="mock",
                    help="client registry: deterministic mock (default: 12 "
                         "homes around the AOI centre), mock-presets (demo "
                         "homes on EVERY simulator preset via the "
                         "simulator's own WUI placement — the national "
                         "demo roster, W6 thin viewer) or the live "
                         "read-only DynamoDB roster")
    pw.add_argument("--preset-homes", type=int, default=40,
                    help="demo homes per simulator preset for --registry "
                         "mock-presets (default 40: 13 presets -> 520 "
                         "homes); ignored by the other registries")
    pw.add_argument("--gust-blend", type=float, default=0.0,
                    help="cfg.wind_gust_blend: effective wind = sustained + "
                         "blend x (gust - sustained); hindcast-calibrated "
                         "0.5, default 0 (sustained only)")
    pw.add_argument("--grass-floor", type=float, default=0.0,
                    help="cfg.grass_ros_floor_frac: grass-class head-ROS "
                         "floor = frac x U10 (Cruz-Alexander); hindcast-"
                         "validated 0.10 (cal 91.5%% / OOS-normal ~100%% "
                         "coverage, 0%% early); default 0 (off)")
    pw.add_argument("--extreme-regime", action="store_true",
                    help="arm the EXTREME SPREAD REGIME posture: cured-grass "
                         "domains (GR+GS >= 60%% burnable) with wind evaluate "
                         "the alert ladder on the pessimistic (p10) arrival "
                         "+ banner (docs/calibration/hindcast_oos_2026-07-28)")
    # FIX PROGRAM (docs/TRI_MODEL_REVIEW_2026-08-31.md): the review's engine
    # remedies, each a default-OFF contracts knob the operator arms here.
    pw.add_argument("--ember-radius", type=float, default=0.0,
                    help="cfg.ember_threat_radius_m: adjacency/ember threat "
                         "radius (m) for homes on developed non-burnable "
                         "cells — 0 = off (the live detection hole the "
                         "review measured at 63%% of RSF-class WUI homes); "
                         "recommended 200 (300 for RSF-class WUI)")
    pw.add_argument("--fail-closed", action="store_true",
                    help="arm corridor_fail_closed + corridor_unreached_bound: "
                         "off-raster / unreached corridor samples are "
                         "BOUNDED through reached boundary cells, never "
                         "skipped; a leg with no forecast raster is unsafe "
                         "(fix-program item 1)")
    pw.add_argument("--floor-gate", action="store_true",
                    help="cfg.ros_floor_extinction_gate: the ROS floor only "
                         "raises a positive Rothermel ROS — fuel at/above its "
                         "moisture of extinction stays out (item 2)")
    pw.add_argument("--grass-only", action="store_true",
                    help="cfg.ros_floor_grass_only: floor GR/GS only, never "
                         "TU/TL timber (item 2)")
    pw.add_argument("--model-error", type=float, default=0.0,
                    help="cfg.model_error_sd_log: ensemble model-error term, "
                         "sd(log head ROS) per perturbed member — "
                         "hindcast-PIT-calibrated (item 3); 0 = off")
    pw.add_argument("--edge-piecewise", action="store_true",
                    help="cfg.edge_weather_piecewise: hour-crossing edge "
                         "traversals integrate each hour's rate (review "
                         "held item; 3.7-7x spread cost — see the fix "
                         "program 2026-09-11 before arming live)")
    pw.add_argument("--phi-vector", action="store_true",
                    help="cfg.slope_phi_vector: phi-factor vector slope/wind "
                         "combination (corrects the opposed-slope bias, "
                         "cost-free; fix program 2026-09-11)")
    pw.add_argument("--solver-evals", type=int, default=0,
                    help="cfg.solver_budget_evals: deterministic dispatch "
                         "budget as a consult COUNT (0 = wall clock); the "
                         "simulator runs 20000 (fix program 2026-09-11)")
    pw.add_argument("--regime-floor", type=float, default=0.0,
                    help="cfg.extreme_regime_floor_frac: when the extreme-"
                         "regime detector trips for an incident, spread it "
                         "with THIS floor fraction on every floored class "
                         "(the blow-up posture) while other incidents keep "
                         "the calibrated band; needs --extreme-regime "
                         "(calibration matrix 2026-09-03 verdict)")
    pw.add_argument("--p50-bound", action="store_true",
                    help="cfg.ensemble_p50_nominal_bound: the ladder median "
                         "never trails the nominal run (item 3, N4)")
    # SAFETY SEMANTICS 13 (founder ruling 2026-09-27): default-OFF knobs.
    pw.add_argument("--drop-contained", action="store_true",
                    help="cfg.drop_contained_incidents: a fire the feed "
                         "reports contained (a source at >= 100%% or a "
                         "containment / control / out timestamp; no source "
                         "below 100%%) and not growing leaves the map — no "
                         "domain, no ETAs; its pairs take the absence law; "
                         "the count is reported (notes, /api/state, /world)")
    pw.add_argument("--confirmed-perimeters", action="store_true",
                    help="cfg.perimeter_confirmed_anchor: a polygon whose "
                         "fresher record reports the same size (within "
                         "cfg.perimeter_confirm_tolerance_frac) is seeded at "
                         "that report instead of being spread forward from "
                         "its own age; a growing fire (record larger) keeps "
                         "the legacy seeding")
    # SAFETY SEMANTICS 15 (founder rule 2026-09-27): default-OFF knob.
    pw.add_argument("--offmap-per-fire", action="store_true",
                    help="cfg.corridor_offraster_per_fire + "
                         "corridor_offraster_margin_min=60: the fail-closed "
                         "off-domain bound extrapolates each fire at its own "
                         "forecast front speed where it crosses its domain "
                         "edge (P90, floored at 0.28 m/s, capped at 1.7 m/s) "
                         "when a fresh WFIGS behaviour report says it is not "
                         "rapid — rapid / unknown fires keep 1.7 m/s — and a "
                         "bounded sample needs a 60-min crossing margin; "
                         "inert without --fail-closed")
    # FIRE BELIEF (contracts SAFETY SEMANTICS 14; docs/FIRE_BELIEF.md)
    pw.add_argument("--fire-belief", action="store_true",
                    help="cfg.fire_belief_track: learn a per-fire spread-rate "
                         "belief from the feeds' growth observations and "
                         "publish it (status, /api/world/summary, "
                         "<state-dir>/fire_beliefs.json); it changes no "
                         "arrival, alert or dispatch")
    pw.add_argument("--fire-belief-apply", action="store_true",
                    help="cfg.fire_belief_apply (implies --fire-belief): the "
                         "learned belief drives the ensemble's head-ROS "
                         "multiplier in same-weather-pattern hours (the "
                         "footprint, the ladder's P50 and P90 move: alert "
                         "timing changes); P10 stays guarded, never later "
                         "than physics (cfg.fire_belief_apply_p10 lifts the "
                         "guard: config only, measured unsafe for contained "
                         "fires, docs/FIRE_BELIEF.md)")
    pw.add_argument("--dry-notify", action="store_true",
                    help="force log-only notifications (no SES even when "
                         "WGRESP_NOTIFY_SES=1)")
    pw.add_argument("--max-cycles", type=int, default=0,
                    help="stop after N cycles (0 = run until Ctrl-C; for "
                         "drills/tests)")
    pw.set_defaults(fn=_cmd_watch)

    pu = sub.add_parser(
        "simui",
        help="the interactive simulator UI: real engine, five real WUI "
             "locations, the safety-threshold slider "
             "(docs/SIMULATOR_DESIGN.md)")
    pu.add_argument("--port", type=int, default=None,
                    help="HTTP port (default 8878; --smoke defaults to an "
                         "ephemeral port unless one is given)")
    pu.add_argument("--host", default="127.0.0.1")
    pu.add_argument("--open", action="store_true", dest="open_browser",
                    help="open the simulator page in the default browser")
    pu.add_argument("--smoke", action="store_true",
                    help="self-test over the live HTTP API: new session, "
                         "ignite, 3 engine ticks, print the safety "
                         "scoreboard; exit 0 iff 0 safety violations")
    pu.set_defaults(fn=_cmd_simui)
    return p


# ---------------------------------------------------------------------------
# simulate
# ---------------------------------------------------------------------------

def _cmd_simulate(args) -> int:
    from wgresponse import sim
    try:
        scenario = sim.build_scenario(args.scenario, args.seed)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    report = sim.run(scenario, args.hours, out_dir=args.out)
    print(f"scenario        {report.scenario} (seed={report.seed})")
    print(f"cycles          {report.cycles_run} x {report.cycle_period_s:.0f}s "
          f"({args.hours:g} h simulated)")
    print(f"alert events    {len(report.events)}")
    print(f"serviced stops  {len(report.serviced)}")
    print(f"unserved        {len(report.unserved)}")
    mm = "n/a" if report.min_margin_h is None else f"{report.min_margin_h:.2f} h"
    print(f"safety          {len(report.violations)} violations, min margin {mm}")
    print(f"report          {report.report_path}")
    print(f"console         {report.console_path}")
    if report.violations:
        print("SAFETY VIOLATIONS PRESENT — do not ship this scenario", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# forecast
# ---------------------------------------------------------------------------

def _cmd_forecast(args) -> int:
    from wgresponse.contracts import LatLon
    loc = LatLon(args.lat, args.lon)
    if args.live:
        try:
            import wgresponse.feeds    # noqa: F401
            import wgresponse.weather  # noqa: F401
            import wgresponse.terrain  # noqa: F401
            import wgresponse.spread   # noqa: F401
        except Exception as e:
            print(f"error: live forecast needs feeds/weather/terrain/spread "
                  f"modules + network ({e}); rerun without --live for mocks",
                  file=sys.stderr)
            return 1
        print("live forecast wiring lands with the threat orchestrator; "
              "using mock ignition for now", file=sys.stderr)
    from wgresponse import sim
    eta, t0 = sim.quick_forecast(loc)
    p10, p50, p90 = eta.hours_out(t0)
    fmt = lambda h: "not reached in horizon" if math.isinf(h) else f"{h:.1f} h"
    print(f"threat forecast for ({args.lat:.4f}, {args.lon:.4f}) "
          f"[mock ignition 5 km west, wind-event weather]")
    print(f"  p10 (pessimistic) arrival: {fmt(p10)}")
    print(f"  p50 (median)      arrival: {fmt(p50)}")
    print(f"  p90 (optimistic)  arrival: {fmt(p90)}")
    return 0


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

def _cmd_plan(args) -> int:
    from wgresponse import sim
    path = Path(args.infile)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read {path}: {e}", file=sys.stderr)
        return 2
    if not isinstance(data, dict) or "clients" not in data:
        print(f"error: {path} is not a sim console JSON "
              f"(expected out/sim_<name>_console.json)", file=sys.stderr)
        return 2
    now = float(data.get("now_s", data.get("t0_s", 0.0)))
    plan = sim.replan_console(data, now_s=now)
    rows = []
    for crew_id in sorted(plan.plans):
        for st in plan.plans[crew_id].stops:
            rows.append((crew_id, st.client_id,
                         (st.arrive_s - now) / 3600.0,
                         (st.depart_s - now) / 3600.0,
                         st.margin_h, st.gel_fresh_frac))
    print(f"re-plan @ now_s={now:.0f}  (times in hours from now; corridor "
          f"checks skipped — console JSON carries no arrival grids)")
    print(f"{'crew':<6} {'client':<8} {'arrive_h':>9} {'depart_h':>9} "
          f"{'margin_h':>9} {'gel_fresh':>9}")
    for r in rows:
        print(f"{r[0]:<6} {r[1]:<8} {r[2]:>9.2f} {r[3]:>9.2f} {r[4]:>9.2f} {r[5]:>9.2f}")
    if not rows:
        print("(no feasible stops)")
    if plan.unserved:
        print("unserved:")
        for uj in plan.unserved:
            print(f"  {uj.client_id:<8} {uj.reason}")
    return 0


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

def _cmd_serve(args) -> int:
    from wgresponse import server as server_mod
    state_provider = None
    if args.demo:
        state_provider = _demo_state_provider(args)
        if state_provider is None:
            return 1
    httpd = server_mod.serve(port=args.port, host=args.host,
                             state_provider=state_provider)
    if os.environ.get("WGRESP_DEV_OPEN") == "1":
        auth = "DEV-OPEN (no auth!)"
    elif os.environ.get("WGRESP_API_KEY"):
        auth = "X-Internal-API-Key header"
    else:
        auth = "FAIL-CLOSED — set WGRESP_API_KEY (healthz/console only)"
    host, port = httpd.server_address[:2]
    print(f"wgresponse server on http://{host}:{port}/console  [auth: {auth}]"
          f"{'  [demo replay]' if args.demo else ''}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def _demo_state_provider(args):
    """Closure replaying a sim console JSON: crew tracks advance one sim cycle
    per --demo-cycle-s real seconds; events appear as sim time passes."""
    from wgresponse import sim
    path = Path(args.out) / f"sim_{args.scenario}_console.json"
    if not path.exists():
        try:
            scenario = sim.build_scenario(args.scenario, args.seed)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return None
        print(f"demo: running {args.scenario} sim ({args.hours:g} h)...")
        report = sim.run(scenario, args.hours, out_dir=args.out)
        path = Path(report.console_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    t0 = float(data.get("t0_s", 0.0))
    period = float(data.get("cycle_period_s", 900.0))
    max_len = max((len(c.get("track") or []) for c in data.get("crews", [])), default=1)
    start = time.monotonic()
    cycle_s = max(args.demo_cycle_s, 0.1)

    def state():
        k = min(int((time.monotonic() - start) / cycle_s), max(max_len - 1, 0))
        now = t0 + k * period
        crews = []
        for c in data.get("crews", []):
            track = c.get("track") or []
            shown = track[:k + 1] if track else []
            last = shown[-1] if shown else None
            crews.append({**c, "track": shown,
                          "lat": last["lat"] if last else None,
                          "lon": last["lon"] if last else None,
                          "status": last["status"] if last else c.get("status")})
        return {**data, "crews": crews, "now_s": now, "demo": True,
                "events": [e for e in data.get("events", [])
                           if (e.get("t_s") or 0) <= now]}
    return state


# ---------------------------------------------------------------------------
# pull-feeds
# ---------------------------------------------------------------------------

def _parse_bbox(text: str):
    from wgresponse.contracts import BBox
    parts = [float(x) for x in text.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be west,south,east,north")
    return BBox(west=parts[0], south=parts[1], east=parts[2], north=parts[3])


def _live_feed_stack(map_key):
    """Build the live feed adapters: WFIGS + FIRIS + CAL FIRE always, FIRMS
    only when a MAP_KEY is provided (Sprint 2 C3d/C1: a keyless FirmsFeed is
    BLIND by design — [] with last_fetch_ok=False — and would mark EVERY watch
    cycle degraded, so the CLI skips it gracefully with a note instead).

    Returns (feeds, notes): the adapter list and human-readable notes (the
    FIRMS skip line, when applicable)."""
    from wgresponse import feeds
    stack = [feeds.WfigsFeed(), feeds.FirisFeed(), feeds.CalFireFeed()]
    notes = []
    if map_key:
        stack.append(feeds.FirmsFeed(map_key=map_key))
    else:
        notes.append(
            "FirmsFeed skipped — no FIRMS_MAP_KEY in the environment "
            f"(hotspot layer off; free key in ~5 min: {feeds.FirmsFeed.SIGNUP_URL})")
    return stack, notes


#: W6 CONUS check (2026-09-26): an AOI spanning more than this many square
#: degrees is "national" for the FIRMS note below (CONUS = 1,534 deg²; the
#: SoCal shadow AOI 1.8, the Vail -> Aspen corridor 0.94).
_FIRMS_NATIONAL_DEG2 = 100.0


def _firms_scale_notes(feeds_stack, aoi) -> list:
    """LOUD startup notes for FIRMS over a national AOI (W6 CONUS check).

    Checked against the live FIRMS area-API docs on 2026-09-26: the area
    endpoint takes any west,south,east,north box up to the whole world
    ("Maximum boundary: -180, -90, 180, 90"; a larger request may count as
    several of the MAP_KEY's 5000 transactions / 10 min), so a CONUS AOI is
    ONE legal request per cycle — no tiling, no skip. What does scale is
    downstream: every 2 km hotspot cluster (greedy, O(detections x
    clusters)) becomes an incident assessed each cycle, and a FIRMS cluster
    never name-joins a WFIGS fire (synthetic names, no IRWIN id), so a fire
    near clients can build several domains. Said once, at startup; [] when
    FIRMS is off or the AOI is regional."""
    from wgresponse import feeds as feeds_mod
    if not any(isinstance(f, feeds_mod.FirmsFeed) for f in feeds_stack):
        return []
    area = (aoi.east - aoi.west) * (aoi.north - aoi.south)
    if area <= _FIRMS_NATIONAL_DEG2:
        return []
    return [f"FIRMS over a NATIONAL AOI ({area:,.0f} deg²): one area request "
            f"per cycle (the FIRMS area API accepts any bbox up to the world "
            f"— docs checked 2026-09-26; a large request may count as several "
            f"of the key's 5000 transactions / 10 min). Every 2 km hotspot "
            f"cluster becomes an incident assessed each cycle (agricultural "
            f"and industrial heat included) and FIRMS clusters never "
            f"name-join a WFIGS fire — expect hundreds of FIRMS incidents and "
            f"more than one domain per fire near clients"]


def _cmd_pull_feeds(args) -> int:
    try:
        bbox = _parse_bbox(args.bbox)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.offline:
        print("no incidents (offline mode — network access disabled)")
        return 0
    try:
        from wgresponse import feeds  # noqa: F401 (availability probe)
    except Exception as e:
        print(f"no incidents (feeds module unavailable: {e})")
        return 0
    stack, skip_notes = _live_feed_stack(os.environ.get("FIRMS_MAP_KEY"))
    for note in skip_notes:
        print(f"  [skip] {note}")
    batches = []
    for feed in stack:
        cls_name = type(feed).__name__
        try:
            batches.append(feed.fetch_active(bbox))
        except Exception as e:
            print(f"  [{cls_name}] fetch failed: {e}", file=sys.stderr)
    fuse = getattr(feeds, "fuse_incidents", None)
    try:
        fused = fuse(batches) if (fuse and batches) else [i for b in batches for i in b]
    except Exception as e:
        print(f"no incidents (fusion failed: {e})")
        return 0
    if not fused:
        print("no incidents in bbox")
        return 0
    print(f"{'incident_id':<24} {'name':<24} {'source':<9} {'lat':>9} "
          f"{'lon':>10} {'acres':>9} {'contain%':>8}")
    for inc in fused:
        acres = "-" if inc.acreage is None else f"{inc.acreage:.0f}"
        cont = "-" if inc.containment_pct is None else f"{inc.containment_pct:.0f}"
        src = inc.source.value if hasattr(inc.source, "value") else str(inc.source)
        print(f"{inc.incident_id:<24.24} {inc.name:<24.24} {src:<9} "
              f"{inc.origin.lat:>9.4f} {inc.origin.lon:>10.4f} {acres:>9} {cont:>8}")
    return 0


# ---------------------------------------------------------------------------
# watch — the live operator daemon (Sprint 2 C1/C2, contracts cli seam)
# ---------------------------------------------------------------------------

def _validate_watch_bbox(bbox) -> None:
    """Reject inside-out / out-of-range AOI boxes with a clear message."""
    if not (bbox.west < bbox.east and bbox.south < bbox.north):
        raise ValueError("aoi bbox must satisfy west < east and south < north")
    if not (-180.0 <= bbox.west <= 180.0 and -180.0 <= bbox.east <= 180.0
            and -90.0 <= bbox.south <= 90.0 and -90.0 <= bbox.north <= 90.0):
        raise ValueError("aoi bbox degrees out of range (lat ±90, lon ±180)")


def _parse_aoi(text: str):
    """Parse --aoi into (BBox, [SafeZone], description).

    Accepts a bare "west,south,east,north" degrees string, or a path to a
    zones JSON file shaped either {"aoi": [w,s,e,n], "safe_zones": [...]} or
    {"zones": [{"name", "bbox": [w,s,e,n]}, ...], "safe_zones": [...]}
    (multi-zone files watch the UNION bbox — every feed fetch is bounded by
    it). safe_zones entries need zone_id/lat/lon (+ optional name) and feed
    the dispatcher's egress choice. Raises ValueError on anything malformed
    (json.JSONDecodeError is a ValueError)."""
    from wgresponse.contracts import BBox, LatLon, SafeZone
    s = str(text).strip()
    if "," in s and not s.lower().endswith(".json"):
        parts = [p.strip() for p in s.split(",")]
        if len(parts) != 4:
            raise ValueError("aoi bbox must be west,south,east,north")
        try:
            w, so, e, n = (float(p) for p in parts)
        except ValueError:
            raise ValueError(f"aoi bbox has non-numeric parts: {s!r}")
        bbox = BBox(west=w, south=so, east=e, north=n)
        _validate_watch_bbox(bbox)
        return bbox, [], f"bbox {s}"

    path = Path(s)
    if not path.is_file():
        raise ValueError(
            f"--aoi is neither a 'west,south,east,north' bbox nor an "
            f"existing zones JSON file: {text!r}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"zones file {path} must contain a JSON object")

    boxes = []
    zone_names = []
    if isinstance(data.get("aoi"), (list, tuple)) and len(data["aoi"]) == 4:
        w, so, e, n = (float(v) for v in data["aoi"])
        boxes.append(BBox(west=w, south=so, east=e, north=n))
        zone_names.append("aoi")
    for z in data.get("zones") or []:
        bb = z.get("bbox") if isinstance(z, dict) else None
        if not (isinstance(bb, (list, tuple)) and len(bb) == 4):
            raise ValueError(f"zones file {path}: every zone needs a "
                             f"4-element bbox [w,s,e,n]")
        w, so, e, n = (float(v) for v in bb)
        boxes.append(BBox(west=w, south=so, east=e, north=n))
        zone_names.append(str(z.get("name") or f"zone{len(zone_names)}"))
    if not boxes:
        raise ValueError(f"zones file {path} defines no aoi/zones bbox")
    for b in boxes:
        _validate_watch_bbox(b)
    union = BBox(west=min(b.west for b in boxes),
                 south=min(b.south for b in boxes),
                 east=max(b.east for b in boxes),
                 north=max(b.north for b in boxes))

    safe_zones = []
    for i, sz in enumerate(data.get("safe_zones") or []):
        try:
            safe_zones.append(SafeZone(
                zone_id=str(sz["zone_id"]),
                loc=LatLon(float(sz["lat"]), float(sz["lon"])),
                name=str(sz.get("name", ""))))
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"zones file {path}: safe_zones[{i}] needs "
                             f"zone_id, lat, lon")
    desc = (f"zones file {path.name} ({', '.join(zone_names)}; union bbox "
            f"{union.west:g},{union.south:g},{union.east:g},{union.north:g}; "
            f"{len(safe_zones)} safe zone(s))")
    return union, safe_zones, desc


# LFPS job-submission email (Session-3 exit-gate soak finding, 2026-07-09):
# the live LFPS API rejects submits without an Email parameter
# ({"parameter":"Email","message":"Required"} — HTTP 400), so a watch daemon
# that omitted it went per-incident DEGRADED on every fresh tile. Overridable
# via env LFPS_EMAIL; the default is the ops address the hindcast harness
# already submits with (tools/hindcast_harness.py).
_LFPS_EMAIL_ENV = "LFPS_EMAIL"
_LFPS_EMAIL_DEFAULT = "ops@thewildfireguardian.com"


def _watch_terrain(args, state_dir):
    """Terrain provider per the C1 rule: LfpsTerrain when the live decode
    deps import, else SyntheticTerrain with a LOUD note. --live-terrain makes
    missing deps fatal (returns (None, notes)); --synthetic-terrain forces
    the synthetic path. Returns (provider_or_None, notes)."""
    from wgresponse import terrain as terrain_mod
    cache_dir = str(Path(state_dir) / "terrain_cache")
    if args.synthetic_terrain:
        return terrain_mod.SyntheticTerrain("watch"), [
            "SYNTHETIC TERRAIN FORCED (--synthetic-terrain): fuels/slope are "
            "generated, NOT LANDFIRE — rehearsal-grade forecasts only"]
    try:
        terrain_mod.probe_live_decode_deps()
    except RuntimeError as exc:
        if args.live_terrain:
            return None, [f"--live-terrain requested but the live decode "
                          f"deps are missing: {exc}"]
        return terrain_mod.SyntheticTerrain("watch"), [
            f"LIVE TERRAIN UNAVAILABLE ({exc}) — falling back to "
            "SyntheticTerrain: fuels/slope are generated, NOT LANDFIRE — "
            "rehearsal-grade forecasts until the deps are installed"]
    email = os.environ.get(_LFPS_EMAIL_ENV) or _LFPS_EMAIL_DEFAULT
    # DURABLE STATE (fix program 2026-09-11, docs/DURABLE_STATE.md): the
    # same S3 prefix that mirrors the alert state also mirrors the LANDFIRE
    # tile + zip cache, so a fresh container (App Runner /tmp is wiped on
    # every restart) refetches nothing it has already fetched.
    s3_uri = os.environ.get(_STATE_S3_ENV) or None
    provider = terrain_mod.LfpsTerrain(cache_dir=cache_dir, email=email,
                                       s3_uri=s3_uri)
    return provider, [
        f"terrain: live LANDFIRE via LFPS (zip+tile cache: {cache_dir}"
        + (f", mirrored to {s3_uri}/terrain/" if s3_uri else "")
        + f"; job email {email} — override with env {_LFPS_EMAIL_ENV})"]


def _watch_registry(args, aoi, notes=None):
    """Client registry by flag: the live read-only DynamoDB roster, a
    deterministic mock seeded ACROSS THE AOI (so drills see clients), or
    (mock-presets, W6 thin viewer) --preset-homes demo homes on every
    simulator preset placed by the simulator's own WUI placement — its
    placement record lands in `notes` (the daemon's startup notes)."""
    if args.registry == "dynamo":
        from wgresponse.clients import DynamoClientRegistry
        return DynamoClientRegistry(), "DynamoClientRegistry (read-only scan)"
    if args.registry == "mock-presets":
        from wgresponse import simui
        from wgresponse.clients import preset_client_registry
        n = int(getattr(args, "preset_homes", 40))
        if n < 1:
            raise ValueError("--preset-homes must be >= 1")
        place_notes: list = []
        registry = preset_client_registry(n, notes=place_notes)
        roster = registry.protected_clients()
        in_aoi = sum(1 for c in roster if aoi.contains(c.loc))
        if notes is not None:
            notes.extend(place_notes)
            if in_aoi < len(roster):
                notes.append(f"registry mock-presets: {len(roster) - in_aoi} "
                             f"of {len(roster)} demo homes lie OUTSIDE the "
                             f"AOI (their presets are not in it)")
        return (registry,
                f"MockClientRegistry over the simulator presets ({n} demo "
                f"homes x {len(simui.LOCATIONS)} presets = {len(roster)} "
                f"homes, simulator WUI placement; {in_aoi} inside the AOI)")
    from wgresponse import geo
    from wgresponse.clients import MockClientRegistry
    ctr = aoi.center()
    half_h_km = (aoi.north - aoi.south) * geo.M_PER_DEG_LAT / 2000.0
    half_w_km = (aoi.east - aoi.west) * geo.m_per_deg_lon(ctr.lat) / 2000.0
    spread_km = max(1.0, min(half_h_km, half_w_km) * 0.9)
    return (MockClientRegistry("watch", center=ctr, spread_km=spread_km),
            f"MockClientRegistry (12 synthetic clients within "
            f"{spread_km:.0f} km of AOI center)")


def _watch_notifier(args, state_dir):
    """Notifier per C1: --dry-notify forces LogNotifier; otherwise SES when
    env WGRESP_NOTIFY_SES=1 (consent + sandbox gates apply inside
    SesNotifier), else LogNotifier. Returns (notifier, description)."""
    from wgresponse.notify import LogNotifier, SesNotifier
    if args.dry_notify:
        return LogNotifier(), "LogNotifier (--dry-notify)"
    if os.environ.get(SesNotifier.ENV_FLAG) == "1":
        return (SesNotifier(sender=os.environ.get("SES_SENDER", ""),
                            ledger_path=str(Path(state_dir)
                                            / "ses_send_ledger.jsonl")),
                "SesNotifier (WGRESP_NOTIFY_SES=1; consent gate fail-closed)")
    return LogNotifier(), "LogNotifier (set WGRESP_NOTIFY_SES=1 for SES)"


def _fire_belief_cfg(cfg, args):
    """cfg with the --fire-belief / --fire-belief-apply flags applied (SAFETY
    SEMANTICS 14; apply implies track). No flag = cfg unchanged."""
    if getattr(args, "fire_belief", False) or \
            getattr(args, "fire_belief_apply", False):
        cfg = dataclasses.replace(
            cfg, fire_belief_track=True,
            fire_belief_apply=bool(getattr(args, "fire_belief_apply", False)))
    return cfg


def _fire_belief_file(cfg, state_dir, s3_uri=None):
    """The watch daemon's fire-belief file: <state-dir>/fire_beliefs.json,
    S3-mirrored under the state's prefix (object fire_beliefs.json) — or
    None with cfg.fire_belief_track off (nothing written or read)."""
    if not getattr(cfg, "fire_belief_track", False):
        return None
    from wgresponse.fire_belief import BeliefFile
    return BeliefFile(state_dir, s3_uri=s3_uri)


def _fire_belief_notes(args) -> list:
    """Startup notes for --fire-belief / --fire-belief-apply (SEMANTICS 14)."""
    if getattr(args, "fire_belief_apply", False):
        return ["fire belief APPLIED (alert-timing change): each fire's "
                "learned spread rate drives its footprint, P50 and P90 in "
                "same-weather-pattern hours; physics returns for new weather, "
                "surprises and rapid fires; P10 GUARDED = never later than "
                "physics (docs/FIRE_BELIEF.md)"]
    if getattr(args, "fire_belief", False):
        return ["fire belief TRACKING: per-fire spread-rate belief learned "
                "and published (status, world summary, fire_beliefs.json); "
                "physics, alerts and dispatch unchanged"]
    return []


class _WatchDaemon:
    """The live watch loop: cycle -> persist -> serve, with injectable clock.

    Owns the glue between ThreatMonitor.cycle(), persist.StateStore, and the
    HTTP state surface (/api/state, /healthz, console). Everything the server
    threads read comes from state()/plan() snapshots built per call; the
    cycle loop runs on the caller's thread (run()). ``clock``/``sleep`` are
    injectable so tests never touch the wall clock (contracts determinism
    discipline); the live CLI passes time.time/time.sleep.
    """

    EVENTS_KEEP = 200      # AlertEvents retained for the console feed
    OP_LOG_KEEP = 100      # OperatorEvents retained across cycles
    STALE_PERIODS = 3.0    # cycle older than this many poll periods => stale

    def __init__(self, monitor, engine, registry, tracker, store, cfg, aoi,
                 period_s, aoi_desc="", safe_zones=(), clock=time.time,
                 sleep=time.sleep, startup_notes=(), test_world=None,
                 belief_file=None):
        # TWO WORLDS (contracts SAFETY SEMANTICS 16a): this daemon IS the
        # LIVE world — its monitor refuses every seed from here on (drills
        # run in `test_world`, a wgresponse.worlds.TestWorld, or nowhere).
        lock_live = getattr(monitor, "lock_live", None)
        if callable(lock_live):
            lock_live()
        # set while a live cycle runs (the test world yields to it, 16g)
        self._live_busy = threading.Event()
        self._test_world = test_world
        self.migration_note = None
        # FIRE BELIEF (SAFETY SEMANTICS 14): its OWN file beside the state
        # (fire_belief.BeliefFile); None (the default, and every TEST world)
        # = nothing written or read.
        self._belief_file = belief_file
        self._monitor = monitor
        self._engine = engine
        self._registry = registry
        self._tracker = tracker
        self._store = store
        self._cfg = cfg
        self._aoi = aoi
        self._aoi_desc = aoi_desc or "bbox"
        self._safe_zones = list(safe_zones)
        self._period_s = float(period_s)
        self._clock = clock
        # W1 SEEDED INCIDENTS: the live loop's default sleep is WAKEABLE —
        # a seed lit or retired through the API wakes it so the next cycle
        # runs now, not after the period. An injected sleep (tests, drills)
        # is used verbatim.
        self._wake = threading.Event()
        self._sleep = self._wakeable_sleep if sleep is time.sleep else sleep
        # persist_now runs on the cycle thread AND (seed API) on HTTP
        # threads: one writer at a time, never two saves interleaving.
        self._persist_lock = threading.Lock()
        self._startup_notes = list(startup_notes)
        self.cycles_run = 0
        self.started_at_s = None       # stamped at the first cycle
        self.last_result = None        # latest contracts.CycleResult
        self.restored = False
        self._events = []              # accumulated AlertEvents (bounded)
        self._op_log = []              # accumulated OperatorEvents (bounded)
        if test_world is not None:
            test_world.attach_live(self)

    @property
    def test_world(self):
        """The TEST world (wgresponse.worlds.TestWorld) or None."""
        return self._test_world

    # ------------------------------------------------------------ lifecycle

    def restore(self) -> bool:
        """Load persisted state (contracts persist seam); True when restored.

        Alert records + notified ledger + containment-gate growth clocks go
        back into the AlertEngine, the incumbent plan back into the monitor —
        so the next evaluate() re-fires NOTHING that was already successfully
        sent for an unchanged world and dispatch keeps its commitments (an
        applied-but-never-sent active alert IS re-fired by the engine on the
        next cycle — Session-3 verifier F4, re-notify-ward). Absent/corrupt
        state is a loud note inside StateStore.load(); the daemon simply
        starts fresh.

        TWO WORLDS (SAFETY SEMANTICS 16a/f): the TEST world restores its
        drill from its own namespace first; then the LIVE file loads, and any
        drill artifact a pre-two-worlds build persisted into it (seed rows,
        seeded alert pairs / ledgered drill sends / growth clocks, seeded
        plan stops) is partitioned OUT (worlds.split_legacy_seeds) before
        the live engine sees it and handed to the test world — loudly (log +
        startup note), never run live, and independently of whether the
        live ladder restore succeeds."""
        tw = self._test_world
        if tw is not None:
            try:
                tw.restore()
            except Exception:
                log.exception("watch: TEST world restore failed; it starts "
                              "IDLE (the live world is unaffected)")
        self._restore_fire_beliefs()
        try:
            loaded = self._store.load()
        except Exception:
            # Defense-in-depth (Session-3 verifier F1): StateStore.load()
            # contractually NEVER raises, but a corrupt state file must not
            # be able to kill the daemon at startup even if that guarantee
            # ever regresses — a supervised deployment would crash-loop with
            # ZERO alerting until an operator deletes the file.
            log.exception("watch: state load raised unexpectedly; STARTING "
                          "FRESH (re-alerts, never silences)")
            return False
        if not loaded:
            return False
        from wgresponse import worlds
        loaded, legacy = worlds.split_legacy_seeds(loaded)
        try:
            ok = self._restore_live(loaded)
        finally:
            if legacy is not None:
                self._migrate_legacy(legacy)
        return ok

    def _migrate_legacy(self, legacy) -> None:
        """SAFETY SEMANTICS 16a: pre-two-worlds drill artifacts go to the
        TEST world (a loud note either way; never the live world)."""
        note = legacy.note()
        log.warning("watch: %s", note)
        self.migration_note = note
        self._startup_notes.append(note)
        tw = self._test_world
        if tw is None:
            log.error("watch: no TEST world is wired — the migrated drill "
                      "artifacts are DROPPED from the live world (the live "
                      "file keeps them only until the next live persist)")
            return
        try:
            tw.adopt_legacy(legacy)
        except Exception:
            log.exception("watch: the TEST world could not adopt the "
                          "migrated seeds (the live world never runs them)")

    def _restore_live(self, loaded) -> bool:
        """The live world's own restore (ladder, ledger, growth clocks,
        incumbent plan) from a live-only state dict."""
        try:
            self._engine.restore(loaded["alert_records"],
                                 notified_ledger=loaded["notified_ledger"],
                                 incident_growth=loaded["incident_growth"])
        except Exception:
            log.exception("watch: alert-engine restore failed; starting "
                          "with fresh alert state (re-alerts, never silences)")
            return False
        plan = loaded.get("incumbent_plan")
        if plan is not None and hasattr(self._monitor, "restore_incumbent"):
            self._monitor.restore_incumbent(plan)
        prev_aoi = (loaded.get("meta") or {}).get("aoi")
        if prev_aoi and prev_aoi != self._bbox_dict():
            log.warning(
                "watch: restored state was persisted for a DIFFERENT AOI "
                "(%s vs now %s); alert records are pair-scoped and restored "
                "anyway", prev_aoi, self._bbox_dict())
        self.restored = True
        log.info("watch: restored %d alert pair(s), %d ledgered send(s) "
                 "from %s", len(loaded["alert_records"]),
                 len(loaded["notified_ledger"]), self._store.path)
        return True

    def run_cycle(self):
        """One monitor cycle at clock() now + persist. Returns the CycleResult.

        TWO WORLDS (SAFETY SEMANTICS 16g): the live busy flag is up while
        the cycle and its persist run (the test world yields to it); AFTER
        the persist the cycle's snapshot is handed to the TEST world —
        a flag + an event in production, never a wait, and a test-world
        failure is logged, never raised into this loop."""
        now = float(self._clock())
        if self.started_at_s is None:
            self.started_at_s = now
        t_wall = time.monotonic()
        self._live_busy.set()
        try:
            res = self._monitor.cycle(now)
            # CADENCE TELEMETRY (tri-model review 2026-08-31, live-ops
            # finding): the loop was measured at ~21% of its target cadence
            # (24-min cycles against a 5-min period, 33-47 min stale) while
            # healthz said degraded=false — a slow loop and a stalled one
            # were indistinguishable. state()/healthz now carry the last
            # cycle's wall duration and a staleness flag.
            self.last_cycle_duration_s = time.monotonic() - t_wall
            self.cycles_run += 1
            self.last_result = res
            self._events = (self._events
                            + list(res.events))[-self.EVENTS_KEEP:]
            self._op_log = (self._op_log
                            + list(res.operator_events))[-self.OP_LOG_KEEP:]
            self.persist_now()
        finally:
            self._live_busy.clear()
        self._publish_to_test_world()
        return res

    def _publish_to_test_world(self) -> None:
        tw = self._test_world
        if tw is None:
            return
        try:
            tw.on_live_cycle(self.latest_snapshot())
        except Exception:
            log.exception("watch: the TEST world failed on this live cycle — "
                          "the live world is untouched (SAFETY SEMANTICS 16g)")

    def run(self, max_cycles: int = 0) -> int:
        """Cycle every period_s until Ctrl-C (or max_cycles > 0 is reached).

        KeyboardInterrupt is caught for a CLEAN stop: a final persist runs
        and the method returns normally (the CLI layer persists once more in
        its finally — idempotent). Returns the number of cycles run by this
        call."""
        ran = 0
        try:
            while True:
                t0 = float(self._clock())
                self.run_cycle()
                ran += 1
                if max_cycles and ran >= max_cycles:
                    return ran
                elapsed = float(self._clock()) - t0
                self._sleep(max(0.0, self._period_s - elapsed))
        except KeyboardInterrupt:
            log.info("watch: interrupt after %d cycle(s); final persist + "
                     "clean shutdown", ran)
            self.persist_now(final=True)
            return ran

    def wake(self) -> None:
        """Cut the current (or next) inter-cycle sleep short. A no-op for an
        injected sleep. Since TWO WORLDS (SAFETY SEMANTICS 16a) no test
        action calls it: a drill never moves the live cadence."""
        self._wake.set()

    def _wakeable_sleep(self, seconds: float) -> None:
        """The live loop's sleep: `seconds`, or until wake(). Waits in <= 1 s
        slices so a Ctrl-C still lands promptly (an Event wait is not
        reliably interruptible on Windows)."""
        end = time.monotonic() + max(0.0, float(seconds))
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0.0 or self._wake.wait(min(remaining, 1.0)):
                break
        self._wake.clear()

    def persist_now(self, final: bool = False) -> bool:
        """Persist durable state via StateStore; disk failure is a LOUD log +
        False, never fatal (a full disk must not kill the alert loop).
        Serialized (the loop and the shutdown path). Since TWO WORLDS
        (SAFETY SEMANTICS 16f) no test action ever calls it — the test world
        persists into its own namespace."""
        with self._persist_lock:
            ok = self._persist_locked(final)
            self._save_fire_beliefs()
            return ok

    # ------------------------------------------ fire belief (SEMANTICS 14)

    def _restore_fire_beliefs(self) -> None:
        """Local fire_beliefs.json first, then the S3 mirror; independent of
        the alert-state restore (never raises: a failure = the prior)."""
        store = getattr(self._monitor, "fire_beliefs", None)
        if self._belief_file is not None and store is not None:
            self._belief_file.load_into(store)

    def _save_fire_beliefs(self) -> None:
        store = getattr(self._monitor, "fire_beliefs", None)
        if self._belief_file is None or store is None:
            return
        self._belief_file.save(store, now_s=(self.last_result.t_s
                                             if self.last_result else None))

    def _persist_locked(self, final: bool = False) -> bool:
        """persist_now's body, under the persist lock. The LIVE world never
        writes seeded_incidents (it holds no seed — SEMANTICS 16a): the file
        is the pre-W1 shape, byte for byte."""
        try:
            states = self._engine.states()
            ledger_fn = getattr(self._engine, "notified_ledger", None)
            growth_fn = getattr(self._engine, "incident_growth", None)
            meta = {
                "aoi": self._bbox_dict(),
                "aoi_desc": self._aoi_desc,
                "started_at_s": self.started_at_s,
                "cycles_run": self.cycles_run,
                "last_cycle_t_s": (self.last_result.t_s
                                   if self.last_result else None),
                "final": bool(final),
            }
            self._store.save(states,
                             ledger_fn() if callable(ledger_fn) else {},
                             getattr(self._monitor, "incumbent", None),
                             meta,
                             incident_growth=(growth_fn()
                                              if callable(growth_fn) else None))
            return True
        except Exception:
            log.exception("watch: persist FAILED — state not saved this "
                          "cycle (alerting continues)")
            return False

    # ------------------------------------------------------- state surface

    def _bbox_dict(self) -> dict:
        b = self._aoi
        return {"west": b.west, "south": b.south,
                "east": b.east, "north": b.north}

    def plan(self):
        """Latest DispatchPlan (or None) — the /api/plan provider."""
        return self.last_result.plan if self.last_result else None

    def state(self) -> dict:
        """The /api/state payload (server jsonable-serializes dataclasses).

        Carries the degradation keys the console banner + /healthz read
        (degraded, operator_events, last_cycle_t_s, last_cycle_age_s,
        cycles_run — SAFETY SEMANTICS 6 surfacing), the console layers
        (incidents/clients/crews/safe_zones/events/bbox), and the watch
        metadata. operator_events are the LAST cycle's only (recovery must
        clear the banner); operator_log keeps a bounded recent history."""
        now = float(self._clock())
        out = {
            "live": True,
            "aoi": self._bbox_dict(),
            "bbox": self._bbox_dict(),          # console fitBounds key
            "aoi_desc": self._aoi_desc,
            "poll_period_s": self._period_s,
            "cycles_run": self.cycles_run,
            "started_at_s": self.started_at_s,
            "restored": self.restored,
            "state_mirror": (self._store.mirror_status()
                             if hasattr(self._store, "mirror_status") else None),
            "startup_notes": list(self._startup_notes),
            "safe_zones": [{"zone_id": z.zone_id, "lat": z.loc.lat,
                            "lon": z.loc.lon, "name": z.name}
                           for z in self._safe_zones],
            "crews": self._crew_rows(),
            "events": list(self._events),
            "operator_log": list(self._op_log),
        }
        # (TWO WORLDS, SAFETY SEMANTICS 16a: the live world holds no seed, so
        # the W1 `seeds` key never appears here — the TEST world's seeds are
        # on /api/test/state.)
        res = self.last_result
        if res is None:
            out.update({"degraded": False, "operator_events": [],
                        "notes": ["no cycle has completed yet"],
                        "incidents": [], "clients": [], "plan": None})
            return out
        age_s = max(0.0, now - res.t_s)
        stale_after_s = self.STALE_PERIODS * self._period_s
        out.update({
            "now_s": res.t_s,
            "last_cycle_t_s": res.t_s,
            "last_cycle_age_s": age_s,
            "last_cycle_duration_s": getattr(self, "last_cycle_duration_s",
                                             None),
            # STALENESS SLO (review live-ops finding): a cycle older than
            # STALE_PERIODS poll periods is stale whatever `degraded` says —
            # the console banner and /healthz surface it; the daemon is not
            # restarted on it (state lives on local disk; a restart storm on
            # a merely slow loop would silence alerting entirely).
            "cycle_stale": age_s > stale_after_s,
            "stale_after_s": stale_after_s,
            "last_cycle_phases": dict(getattr(res, "timings", {}) or {}),
            "degraded": bool(res.degraded),
            "operator_events": list(res.operator_events),
            "notes": list(res.notes),
            "incidents": [self._incident_row(i) for i in res.incidents],
            "clients": self._client_rows(res),
            "plan": res.plan,
        })
        if getattr(self._cfg, "drop_contained_incidents", False):
            # SAFETY SEMANTICS 13c: nothing leaves the map silently —
            # additive keys, present only with the knob armed.
            gone = list(getattr(res, "removed_incidents", None) or [])
            out["removed_contained"] = len(gone)
            out["removed_incidents"] = [self._removed_row(r) for r in gone]
        if getattr(self._cfg, "corridor_offraster_per_fire", False):
            # SAFETY SEMANTICS 15d: the rule's parameters + each incident's
            # off-domain horizon (null = no domain this cycle) — additive
            # keys, present only with the knob armed.
            from wgresponse import offmap as offmap_mod
            table = getattr(res, "offmap", None) or {}
            out["offmap_rule"] = offmap_mod.rule_summary(self._cfg)
            for row in out["incidents"]:
                h = table.get(row["incident_id"])
                row["offmap"] = (offmap_mod.horizon_row(h)
                                 if h is not None else None)
        if getattr(self._cfg, "fire_belief_track", False):
            # FIRE BELIEF (SEMANTICS 14): additive, only while tracking
            from wgresponse.fire_belief import status_row
            out["fire_status"] = {
                iid: status_row(st) for iid, st in
                sorted((getattr(res, "fire_status", None) or {}).items())}
        return out

    @classmethod
    def _removed_row(cls, rem) -> dict:
        """One contained fire removed this cycle (SAFETY SEMANTICS 13c): its
        incident row + the feed declarations that removed it."""
        row = cls._incident_row(rem.incident)
        row.update({"reason": rem.reason, "signals": list(rem.signals),
                    "removed_since_s": rem.removed_since_s})
        return row

    @staticmethod
    def _incident_row(inc) -> dict:
        src = getattr(inc.source, "value", None) or str(inc.source)
        row = {"incident_id": inc.incident_id, "name": inc.name,
               "source": src, "lat": inc.origin.lat, "lon": inc.origin.lon,
               "acreage": inc.acreage,
               "containment_pct": inc.containment_pct,
               "updated_at_s": inc.updated_at_s}
        if src == "seeded":
            # W1 (SEMANTICS 12f): when it was lit, by whom, which weather
            row["seed"] = dict((inc.meta or {}).get("seed") or {})
        return row

    # ------------------- TWO WORLDS (SAFETY SEMANTICS 16): the route seams
    # wgresponse.server routes /api/seed to app.seed_api and /api/test/* to
    # app.test_api — both this daemon; every method returns (http_status,
    # json_payload) like SimSessionManager.

    _NO_TEST_WORLD = {"error": "no TEST world is wired to this daemon (the "
                               "test map is served by `wgresponse.cli "
                               "watch`)"}
    _ALIAS_NOTE = ("read-only alias of GET /api/test/seed for one release "
                   "(TWO WORLDS, contracts SAFETY SEMANTICS 16): seeds live "
                   "in the TEST world; the live world holds none")

    def _live_refusal(self):
        from wgresponse import seeded as seeded_mod
        return 409, {"error": seeded_mod.LIVE_REFUSAL, "world": "live",
                     "use": seeded_mod.TEST_SEED_ROUTE}

    def seed_list(self):
        """GET /api/seed — the TEST world's list, read-only alias."""
        tw = self._test_world
        if tw is None:
            return 404, dict(self._NO_TEST_WORLD)
        code, out = tw.seed_list()
        return code, dict(out, alias_of="/api/test/seed",
                          deprecated=self._ALIAS_NOTE)

    def seed_get(self, incident_id: str):
        """GET /api/seed/<id> — alias of GET /api/test/seed/<id>."""
        tw = self._test_world
        if tw is None:
            return 404, dict(self._NO_TEST_WORLD)
        code, out = tw.seed_get(incident_id)
        return code, dict(out, alias_of="/api/test/seed/<id>",
                          deprecated=self._ALIAS_NOTE)

    def seed_create(self, payload):
        """POST /api/seed: the LIVE world refuses every seed (409 + the
        pointer to /api/test/seed; SAFETY SEMANTICS 16a)."""
        return self._live_refusal()

    def seed_retire(self, incident_id: str, by: str = "api"):
        """DELETE /api/seed/<id>: refused like POST (the live world holds no
        seed to retire — /api/test/seed/<id>)."""
        return self._live_refusal()

    def _tw_call(self, name: str, *args, **kw):
        tw = self._test_world
        if tw is None:
            return 404, dict(self._NO_TEST_WORLD)
        return getattr(tw, name)(*args, **kw)

    def test_state(self):
        """GET /api/test/state."""
        tw = self._test_world
        if tw is None:
            return 404, dict(self._NO_TEST_WORLD)
        return 200, tw.state()

    def test_seed_list(self):
        return self._tw_call("seed_list")

    def test_seed_get(self, incident_id: str):
        return self._tw_call("seed_get", incident_id)

    def test_seed_create(self, payload):
        return self._tw_call("seed_create", payload)

    def test_seed_retire(self, incident_id: str, by: str = "api"):
        return self._tw_call("seed_retire", incident_id, by=by)

    def test_crews_reset(self, payload=None):
        return self._tw_call("crews_reset", payload)

    def test_crews_place(self, payload):
        return self._tw_call("crews_place", payload)

    def test_reset(self, payload=None):
        return self._tw_call("reset", payload)

    def test_world_summary(self, since=None):
        """GET /api/world/summary?world=test."""
        tw = self._test_world
        if tw is None:
            return 404, dict(self._NO_TEST_WORLD)
        return 200, tw.world_summary(since=since)

    # ------------ TWO WORLDS: the read-only LIVE view the test world uses
    # (worlds.TestWorld.attach_live). Every method READS — never the live
    # clock, never the live registry, never a write, a wake or a wait.

    def latest_snapshot(self):
        """The last completed live cycle's WorldSnapshot (or None)."""
        return getattr(self._monitor, "last_snapshot", None)

    def is_busy(self) -> bool:
        """True while a live cycle (and its persist) runs."""
        return self._live_busy.is_set()

    def live_result(self):
        return self.last_result

    def live_engine_states(self) -> dict:
        try:
            return dict(self._engine.states())
        except Exception:
            return {}

    def live_crew_rows(self) -> list:
        return self._crew_rows()

    def live_meta(self) -> dict:
        return {"aoi": self._bbox_dict(), "aoi_desc": self._aoi_desc,
                "poll_period_s": self._period_s,
                "safe_zones": [{"zone_id": z.zone_id, "lat": z.loc.lat,
                                "lon": z.loc.lon, "name": z.name}
                               for z in self._safe_zones]}

    def clone_fire_beliefs(self):
        """A non-persistent deep copy of the live per-fire spread beliefs
        (SAFETY SEMANTICS 14/16), or None when the live monitor tracks none.
        The test world calls it while the live loop is idle; the copy can
        learn inside a drill but never reaches the live store or its file."""
        store = getattr(self._monitor, "fire_beliefs", None)
        return store.copy() if store is not None else None

    def clone_crews(self, now_s: float) -> list:
        """The live roster as virtual test crews (SEMANTICS 16c/d): the crew
        tracker the live DISPATCHER reads (the monitor's; `watch` hands the
        same CrewTracker to both), else the daemon's."""
        from wgresponse import worlds
        tracker = getattr(self._monitor, "_crew_tracker", None)
        if tracker is None:
            tracker = self._tracker
        return worlds.clone_live_crews(tracker, now_s)

    def clone_ladder(self, now_s: float) -> dict:
        """Deep copies of the live ladder, ledger, growth clocks, incumbent
        plan and regime postures (SEMANTICS 16c) — the test world calls it
        while the live loop is idle."""
        import copy
        states = self.live_engine_states()
        ledger_fn = getattr(self._engine, "notified_ledger", None)
        growth_fn = getattr(self._engine, "incident_growth", None)
        return {
            "records": {k: copy.deepcopy(r) for k, r in states.items()},
            "ledger": dict(ledger_fn()) if callable(ledger_fn) else {},
            "growth": (copy.deepcopy(growth_fn()) if callable(growth_fn)
                       else {}),
            "incumbent": copy.deepcopy(getattr(self._monitor, "incumbent",
                                               None)),
            "extreme_prev": dict(getattr(self._monitor, "_extreme_prev",
                                         None) or {}),
        }

    def _crew_rows(self) -> list:
        if self._tracker is None:
            return []
        try:
            crews = self._tracker.crews()
        except Exception:
            log.exception("watch: crew tracker unavailable for /api/state")
            return []
        rows = []
        for c in crews:
            loc = c.loc()
            rows.append({
                "crew_id": c.crew_id, "name": c.name,
                "status": getattr(c.status, "value", None) or str(c.status),
                "lat": loc.lat if loc else None,
                "lon": loc.lon if loc else None,
                "t_s": c.last_beacon.t_s if c.last_beacon else None})
        return rows

    # Worst-state ranking for the per-client console dot (a client with an
    # EXTREME pair shows EXTREME even if another incident just cleared).
    _STATE_RANK = {"none": 0, "cleared": 1, "notice_72h": 2, "watch": 3,
                   "extreme_24h": 4, "impacted": 5}

    def _client_rows(self, res) -> list:
        try:
            roster = {c.client_id: c
                      for c in self._registry.protected_clients(self._aoi)}
        except Exception:
            log.exception("watch: registry unavailable for /api/state")
            roster = {}
        try:
            states = self._engine.states()
        except Exception:
            states = {}
        worst: dict = {}
        for (cid, _iid), rec in states.items():
            sv = getattr(rec.state, "value", None) or str(rec.state)
            cur = worst.get(cid)
            if cur is None or (self._STATE_RANK.get(sv, 0)
                               > self._STATE_RANK.get(cur, 0)):
                worst[cid] = sv
        best_eta: dict = {}
        for a in res.assessments:
            cur = best_eta.get(a.client_id)
            if cur is None or a.eta.eta_p50_s < cur[0]:
                best_eta[a.client_id] = (a.eta.eta_p50_s, a.eta.eta_p10_s)
        rows = []
        for cid in sorted(set(roster) | set(worst)):
            c = roster.get(cid)
            eta = best_eta.get(cid)
            rows.append({
                "client_id": cid,
                "name": c.name if c else "",
                "lat": c.loc.lat if c else None,
                "lon": c.loc.lon if c else None,
                "state": worst.get(cid, "none"),
                "eta_p50_s": eta[0] if eta else None,
                "eta_p10_s": eta[1] if eta else None})
        return rows

    # ------------------------------------------- world viewer (W6 thin)

    _world_cache = None                 # ((t_s, cycles_run), summary dict)
    _world_lock = threading.Lock()      # one summary build at a time

    def world_summary(self, since=None) -> dict:
        """GET /api/world/summary provider (W6 thin viewer; the payload is
        wgresponse.worldview.build_summary over the last CycleResult and
        this daemon's state()). Built ONCE per completed cycle and cached —
        the page polls every 15 s, the P50 ring tracing runs once. `since`
        (the query's ?since=, unix s) equal to the cached cycle time
        answers {"unchanged": true, "t_s": ...} instead of the payload.
        Before the first cycle: an empty summary that says so. Read-only:
        never touches the engine, the monitor or the registry."""
        from wgresponse import worldview
        res = self.last_result
        if res is None:
            out = worldview.summary_from_state(self.state())
            out["note"] = "no cycle has completed yet"
            return out
        key = (float(res.t_s), self.cycles_run)
        with self._world_lock:
            cached = self._world_cache
            if cached is None or cached[0] != key:
                summary = worldview.build_summary(self.state(), res)
                self._world_cache = cached = (key, summary)
        summary = cached[1]
        try:
            since_f = float(since) if since not in (None, "") else None
        except (TypeError, ValueError):
            since_f = None
        if since_f is not None and abs(since_f - summary["t_s"]) < 1e-6:
            return {"unchanged": True, "t_s": summary["t_s"],
                    "cycles_run": summary.get("cycles_run")}
        return summary


def _cmd_watch(args) -> int:
    from wgresponse import crews as crews_mod
    from wgresponse import dispatch as dispatch_mod
    from wgresponse import server as server_mod
    from wgresponse.alerts import AlertEngine
    from wgresponse.contracts import ResponseConfig, validate_config
    from wgresponse.persist import StateStore
    from wgresponse.threat import ThreatMonitor
    from wgresponse.weather import NwsWeather

    try:
        aoi, safe_zones, aoi_desc = _parse_aoi(args.aoi)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    cfg = ResponseConfig()
    if getattr(args, "gust_blend", 0.0):
        # WIND GUST BLEND (hindcast-calibrated; contracts knob): apply the
        # operator-chosen blend to every spread forecast this daemon runs.
        cfg = dataclasses.replace(cfg, wind_gust_blend=float(args.gust_blend))
        startup_note_gust = (f"wind gust blend ACTIVE: effective wind = "
                             f"sustained + {float(args.gust_blend):g} x "
                             f"(gust - sustained) [hindcast-calibrated]")
    else:
        startup_note_gust = None
    startup_note_floor = None
    if getattr(args, "grass_floor", 0.0):
        # GRASS ROS FLOOR (hindcast-validated; contracts knob): Rothermel
        # structurally under-predicts cured-grass spread — the floor lifts
        # grass-class head ROS to frac*U10 (GS/TU/TL at reduced fractions).
        cfg = dataclasses.replace(
            cfg, grass_ros_floor_frac=float(args.grass_floor))
        startup_note_floor = (
            f"fuel-class ROS floor ACTIVE: head ROS >= "
            f"{float(args.grass_floor):g} x U10 (GR; GS/TU/TL at reduced "
            f"ratios) [record restated by the 2026-08-31 tri-model review: "
            f"buys P10 adequacy through area inflation; see --floor-gate]")
    startup_note_regime = None
    if getattr(args, "extreme_regime", False):
        # EXTREME SPREAD REGIME (contracts knob block): cured-grass domains
        # with wind evaluate the alert ladder on the pessimistic arrival.
        cfg = dataclasses.replace(cfg, extreme_regime_enabled=True)
        startup_note_regime = (
            f"extreme-regime posture ARMED: burnable domain >= "
            f"{cfg.extreme_regime_grass_frac:g} grass-class (GR+GS) + "
            f"effective wind >= {cfg.extreme_regime_wind_mps:g} m/s within "
            f"{cfg.extreme_regime_lookahead_h:g} h -> ladder evaluates on "
            f"pessimistic (p10) arrival + message banner")
    # FIX PROGRAM knobs (docs/TRI_MODEL_REVIEW_2026-08-31.md): each is a
    # default-OFF contracts knob; the startup note is the operator's record
    # of what this daemon runs.
    startup_notes_fix: list = []
    if getattr(args, "ember_radius", 0.0):
        cfg = dataclasses.replace(
            cfg, ember_threat_radius_m=float(args.ember_radius))
        startup_notes_fix.append(
            f"ember/adjacency threat radius ACTIVE: a home on a developed "
            f"non-burnable cell inherits the earliest burnable arrival within "
            f"{float(args.ember_radius):g} m (FOUNDER ROUND 4 law 6; review "
            f"item 6)")
    if getattr(args, "fail_closed", False):
        cfg = dataclasses.replace(cfg, corridor_fail_closed=True,
                                  corridor_unreached_bound=True)
        startup_notes_fix.append(
            "corridor law FAIL-CLOSED: off-raster and unreached corridor "
            "samples are bounded through reached boundary cells at "
            f"{cfg.corridor_offraster_ros_mps:g} m/s; a leg with no forecast "
            "raster is unsafe (review item 1)")
    if getattr(args, "floor_gate", False):
        cfg = dataclasses.replace(cfg, ros_floor_extinction_gate=True)
        startup_notes_fix.append(
            "ROS floor EXTINCTION-GATED: fuel at/above its moisture of "
            "extinction stays out; Byram intensity follows the floored ROS "
            "(review item 2)")
    if getattr(args, "grass_only", False):
        cfg = dataclasses.replace(cfg, ros_floor_grass_only=True)
        startup_notes_fix.append(
            "ROS floor restricted to GR/GS grass classes (TU/TL timber "
            "unfloored; review item 2)")
    if getattr(args, "model_error", 0.0):
        cfg = dataclasses.replace(
            cfg, model_error_sd_log=float(args.model_error))
        startup_notes_fix.append(
            f"ensemble MODEL-ERROR term ACTIVE: sd(log) "
            f"{float(args.model_error):g} on every perturbed member's head "
            f"ROS — p10/p90 are calibrated percentiles, not the sample "
            f"minimum/maximum (review item 3)")
    if getattr(args, "edge_piecewise", False):
        cfg = dataclasses.replace(cfg, edge_weather_piecewise=True)
        startup_notes_fix.append(
            "edge-weather PIECEWISE integration ACTIVE: hour-crossing "
            "traversals follow each hour's rate (3.7-7x spread cost)")
    if getattr(args, "phi_vector", False):
        cfg = dataclasses.replace(cfg, slope_phi_vector=True)
        startup_notes_fix.append(
            "phi-vector slope/wind combination ACTIVE: max-spread magnitude, "
            "bearing and effective wind from the Rothermel phi-factor vector "
            "sum (corrects the opposed-slope slow bias)")
    if getattr(args, "solver_evals", 0):
        cfg = dataclasses.replace(cfg, solver_budget_evals=int(args.solver_evals))
        startup_notes_fix.append(
            f"dispatch solver budget DETERMINISTIC: {int(args.solver_evals)} "
            f"consults (10x wall-clock runaway guard) — identical inputs "
            f"replay identical plans regardless of load")
    if getattr(args, "regime_floor", 0.0):
        if not getattr(args, "extreme_regime", False):
            print("error: --regime-floor needs --extreme-regime (the posture "
                  "that decides which incidents get the blow-up floor)",
                  file=sys.stderr)
            return 2
        cfg = dataclasses.replace(
            cfg, extreme_regime_floor_frac=float(args.regime_floor))
        startup_notes_fix.append(
            f"regime-conditional floor ACTIVE: incidents in the extreme "
            f"spread regime spread with floor {float(args.regime_floor):g} "
            f"x U10 on every floored class; others keep the calibrated band "
            f"(matrix verdict 2026-09-03)")
    if getattr(args, "p50_bound", False):
        cfg = dataclasses.replace(cfg, ensemble_p50_nominal_bound=True)
        startup_notes_fix.append(
            "ladder MEDIAN BOUND ACTIVE: p50 never trails the nominal run "
            "(review N4)")
    if getattr(args, "drop_contained", False):
        cfg = dataclasses.replace(cfg, drop_contained_incidents=True)
        startup_notes_fix.append(
            "CONTAINED FIRES LEAVE THE MAP: a fire the feed reports contained "
            "(>= 100% or a containment / control / out timestamp, no source "
            "below 100%) and not growing is removed before assessment; its "
            "pairs take the absence law; it returns when a source reports < "
            f"100% or it grows (then for {cfg.containment_no_growth_h:g} h "
            "without growth) — founder ruling 2026-09-27, SAFETY SEMANTICS 13")
    if getattr(args, "confirmed_perimeters", False):
        cfg = dataclasses.replace(cfg, perimeter_confirmed_anchor=True)
        startup_notes_fix.append(
            "CONFIRMED PERIMETERS: a polygon whose fresher record reports the "
            f"same size (within {cfg.perimeter_confirm_tolerance_frac:.0%}) "
            "is seeded at that report, not spread forward from its own age; "
            "a growing fire (record larger) keeps the legacy seeding "
            "(SAFETY SEMANTICS 13e/f)")
    if getattr(args, "offmap_per_fire", False):
        cfg = dataclasses.replace(cfg, corridor_offraster_per_fire=True,
                                  corridor_offraster_margin_min=60.0)
        startup_notes_fix.append(
            "OFF-DOMAIN HORIZON PER FIRE: a fire whose fresh WFIGS behaviour "
            f"report (<= {cfg.fire_behavior_max_age_h:g} h) says it is not "
            "rapid extrapolates beyond its domain at its own P90 front speed "
            f"(floor {cfg.corridor_offraster_ros_floor_mps:g} m/s, ceiling "
            f"{cfg.corridor_offraster_ros_mps:g} m/s); rapid or unknown fires "
            f"keep {cfg.corridor_offraster_ros_mps:g} m/s; a bounded corridor "
            f"sample needs a {cfg.corridor_offraster_margin_min:g}-min "
            "crossing margin — founder rule 2026-09-27, SAFETY SEMANTICS 15"
            + ("" if getattr(args, "fail_closed", False) else
               " [INERT: the rule changes the fail-closed bound, and "
               "--fail-closed is not armed]"))
    startup_notes_fix += _fire_belief_notes(args)
    cfg = _fire_belief_cfg(cfg, args)       # SEMANTICS 14 (apply => track)
    problems = validate_config(cfg)
    if problems:
        # The review found validate_config never ran on this path and
        # accepted every unsound value it was handed; a daemon must not
        # start on a config that disables a safety law.
        for p in problems:
            print(f"error: config: {p}", file=sys.stderr)
        return 2

    period_s = (float(args.period) if args.period and args.period > 0
                else cfg.poll_period_s)
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    startup_notes: list = []
    if startup_note_gust:
        startup_notes.append(startup_note_gust)
    if startup_note_floor:
        startup_notes.append(startup_note_floor)
    if startup_note_regime:
        startup_notes.append(startup_note_regime)
    startup_notes += startup_notes_fix
    feeds_stack, feed_notes = _live_feed_stack(os.environ.get("FIRMS_MAP_KEY"))
    startup_notes += feed_notes
    terrain, terrain_notes = _watch_terrain(args, state_dir)
    startup_notes += terrain_notes
    if terrain is None:
        for note in terrain_notes:
            print(f"error: {note}", file=sys.stderr)
        return 2
    try:
        registry, registry_desc = _watch_registry(args, aoi,
                                                  notes=startup_notes)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    # W6 CONUS check: FIRMS at national scale is said out loud, once.
    startup_notes += _firms_scale_notes(feeds_stack, aoi)
    notifier, notifier_desc = _watch_notifier(args, state_dir)
    # W1 SEEDED INCIDENTS (SAFETY SEMANTICS 12g): a seeded (drill) fire's
    # notifications go to the drill log, never to a client channel. Since
    # TWO WORLDS (16) the live world holds no seed at all; the wrapper stays
    # as defense in depth, and the TEST world's notifier holds no channel.
    from wgresponse.seeded import SeedSafeNotifier
    notifier = SeedSafeNotifier(notifier)
    notifier_desc += (" · seeded fires -> drill log only · the TEST map's "
                      "alerts -> drill log only")
    if not safe_zones:
        startup_notes.append(
            "no safe zones configured — dispatch fails closed (every job "
            "unserved); provide safe_zones via a zones JSON --aoi file")

    clients_by_id: dict = {}
    try:
        clients_by_id = {c.client_id: c
                         for c in registry.protected_clients(aoi)}
    except Exception as e:
        startup_notes.append(f"registry preload failed ({e}); client names "
                             f"resolve as ids until it recovers")
    engine = AlertEngine(cfg, notifier=notifier, clients_by_id=clients_by_id)
    tracker = crews_mod.CrewTracker(cfg)
    monitor = ThreatMonitor(
        feeds=feeds_stack, weather_provider=NwsWeather(cfg=cfg),
        terrain_provider=terrain, registry=registry, alert_engine=engine,
        cfg=cfg, safe_zones=safe_zones, crew_tracker=tracker,
        dispatcher=dispatch_mod, aoi=aoi)
    # DURABLE STATE (fix program 2026-09-11): env WGRESP_STATE_S3 mirrors
    # the alert ladder / notified ledger / incumbent plan to S3 and restores
    # them into a fresh container before the first cycle. A configured but
    # unusable mirror (no boto3, no credentials, no bucket) must fail LOUD at
    # startup, never silently run un-mirrored.
    s3_uri = os.environ.get(_STATE_S3_ENV) or None
    store = StateStore(state_dir, s3_uri=s3_uri)
    if s3_uri:
        try:
            store.probe_mirror()
        except RuntimeError as exc:
            print(f"error: state mirror {s3_uri} unusable: {exc}",
                  file=sys.stderr)
            return 2
        startup_notes.append(
            f"durable state: alert state + terrain cache mirrored to "
            f"{s3_uri} (restart restores from the mirror; local disk stays "
            f"the authority)")
    else:
        startup_notes.append(
            "durable state OFF: state lives on local disk only (set "
            "WGRESP_STATE_S3=s3://bucket/prefix to mirror it — App Runner "
            "wipes /tmp on every restart)")
    # TWO WORLDS (SAFETY SEMANTICS 16): the TEST world — drills and
    # simulations, isolated from everything above.
    test_world = _watch_test_world(
        args, cfg=cfg, aoi=aoi, aoi_desc=aoi_desc, safe_zones=safe_zones,
        registry=registry, terrain=terrain, clients_by_id=clients_by_id,
        state_dir=state_dir, s3_uri=s3_uri, period_s=period_s,
        dispatcher=dispatch_mod)
    belief_file = _fire_belief_file(cfg, state_dir, s3_uri)   # SEMANTICS 14
    daemon = _WatchDaemon(monitor=monitor, engine=engine, registry=registry,
                          tracker=tracker, store=store, cfg=cfg, aoi=aoi,
                          period_s=period_s, aoi_desc=aoi_desc,
                          safe_zones=safe_zones, startup_notes=startup_notes,
                          test_world=test_world,
                          belief_file=belief_file)
    restored = daemon.restore()

    httpd = server_mod.serve(port=args.port, host=args.host,
                             state_provider=daemon.state, beacon_sink=tracker,
                             plan_provider=daemon.plan)
    if os.environ.get("WGRESP_DEV_OPEN") == "1":
        auth = "DEV-OPEN (no auth!)"
    elif os.environ.get("WGRESP_API_KEY"):
        auth = "X-Internal-API-Key header"
    else:
        auth = "FAIL-CLOSED — set WGRESP_API_KEY (healthz/console only)"
    host, port = httpd.server_address[:2]
    # TWO WORLDS: /api/seed (the legacy route: GET alias, POST/DELETE 409)
    # and /api/test/* route to this daemon (server.py).
    httpd.app.seed_api = daemon
    httpd.app.test_api = daemon

    print("wgresponse watch — live operator daemon")
    print(f"  AOI       : {aoi_desc}")
    print(f"  feeds     : {', '.join(type(f).__name__ for f in feeds_stack)}")
    print(f"  terrain   : {type(terrain).__name__}")
    print(f"  registry  : {registry_desc}")
    print(f"  notifier  : {notifier_desc}")
    print(f"  period    : {period_s:g} s per cycle")
    print(f"  state     : {store.path} "
          f"({'RESTORED — restart re-fires nothing' if restored else 'fresh'})")
    print(f"  console   : http://{host}:{port}/console  [auth: {auth}]  "
          f"(LIVE only)")
    # W6 THIN WORLD VIEWER: the national map over this daemon's state; its
    # /api/world/summary provider is attached here (additive — serve() and
    # every other route are untouched).
    httpd.app.world_provider = daemon.world_summary
    print(f"  world     : http://{host}:{port}/world  (the LIVE map; "
          f"/api/world/summary) — switch LIVE | TEST in the header")
    print(f"  test map  : http://{host}:{port}/world?world=test  (drills "
          f"and simulations; drill {test_world.drill_state}, "
          f"{len(test_world.monitor.seeded.active())} active seed(s); state "
          f"{test_world.store.path})")
    print(f"  seeding   : POST http://{host}:{port}/api/test/seed, GET "
          f"/api/test/seed[/<id>], DELETE /api/test/seed/<id> — the TEST "
          f"world only; /api/seed POST/DELETE answer 409 (SAFETY SEMANTICS "
          f"16)")
    for note in startup_notes:
        print(f"  NOTE      : {note}")
    if daemon.migration_note:
        print(f"  NOTE      : {daemon.migration_note}")
    print("  Ctrl-C stops cleanly (final persist).")

    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    test_world.start()
    try:
        daemon.run(max_cycles=args.max_cycles)
    finally:
        # Ctrl-C lands here too (run() catches it and returns): one last
        # persist so the on-disk state matches the final in-memory state.
        daemon.persist_now(final=True)
        test_world.stop()
        test_world.persist_now(final=True)
        httpd.shutdown()
        httpd.server_close()
        server_thread.join(timeout=5)
        print(f"watch stopped after {daemon.cycles_run} cycle(s); state "
              f"persisted to {store.path} (test map: "
              f"{test_world.store.path})")
    return 0


def _watch_test_world(args, cfg, aoi, aoi_desc, safe_zones, registry,
                      terrain, clients_by_id, state_dir, s3_uri, period_s,
                      dispatcher):
    """TWO WORLDS (contracts SAFETY SEMANTICS 16b/f): the TEST world for
    `watch`. Its OWN weather provider instance; its OWN registry when the
    live one caches (the Dynamo TTL roster — a test read must never refresh
    the live cache), else the stateless mock roster shared read-only; a
    terrain overlay that reads the live tile cache read-only and fetches /
    caches everything else under <state-dir>/test_world/; its own
    StateStore there (+ s3://<prefix>/test_world/ when mirrored)."""
    from wgresponse import worlds
    from wgresponse.persist import StateStore
    from wgresponse.weather import NwsWeather
    test_dir = Path(state_dir) / worlds.TEST_NAMESPACE
    test_s3 = worlds.child_s3_uri(s3_uri, worlds.TEST_NAMESPACE)
    store = StateStore(test_dir, s3_uri=test_s3)
    if getattr(args, "registry", "mock") == "dynamo":
        test_registry, _desc = _watch_registry(args, aoi)
    else:
        test_registry = registry
    email = os.environ.get(_LFPS_EMAIL_ENV) or _LFPS_EMAIL_DEFAULT
    test_terrain = worlds.test_terrain_for(terrain, test_dir, email=email,
                                           s3_uri=test_s3)
    return worlds.TestWorld(
        cfg=cfg, aoi=aoi, safe_zones=safe_zones,
        weather_provider=NwsWeather(cfg=cfg), terrain_provider=test_terrain,
        registry=test_registry, dispatcher=dispatcher, store=store,
        clients_by_id=clients_by_id, aoi_desc=aoi_desc, period_s=period_s)


# ---------------------------------------------------------------------------
# simui — the interactive simulator UI (SIM-3, contracts cli seam)
# ---------------------------------------------------------------------------

_SIMUI_DEFAULT_PORT = 8878
_SMOKE_LOCATION = "boulder_co"


def _simui_auth_setup() -> bool:
    """Dev-open-by-default posture for the LOCAL demo instrument.

    The simulator API keeps the server's fail-closed auth; but `simui` is a
    founder-facing localhost demo (SIMULATOR_DESIGN.md product shape), so
    when NO auth is configured at all it enables WGRESP_DEV_OPEN=1 for this
    process and says so loudly. A configured WGRESP_API_KEY (or an explicit
    WGRESP_DEV_OPEN) always wins. Returns True iff this call set the flag
    (callers that must not leak env — --smoke — restore it)."""
    if os.environ.get("WGRESP_API_KEY") or os.environ.get("WGRESP_DEV_OPEN") == "1":
        return False
    os.environ["WGRESP_DEV_OPEN"] = "1"
    return True


def _console_line(line: str) -> str:
    """`line` as the current stdout can encode it: unencodable characters
    (the 'Eagle → Vail corridor, CO' arrow on a Windows cp1252 pipe) become
    replacement characters instead of a UnicodeEncodeError that would kill
    the simui startup; an encodable line is returned unchanged."""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        line.encode(enc)
    except UnicodeEncodeError:
        return line.encode(enc, errors="replace").decode(enc)
    except LookupError:
        pass
    return line


def _simui_print_locations(simui_mod) -> None:
    """The simulator locations + on-disk tile availability (standard tile +
    the FIRE ZONE extended wide-area tile, contracts FIRE ZONE). A preset
    with no extended product BY DESIGN (SimLocation.extended_zone False —
    the whole-valley eagle_vail_co) says so instead of a refetch hint."""
    print("locations (docs/SIMULATOR_DESIGN.md):")
    for key in sorted(simui_mod.LOCATIONS):
        loc = simui_mod.LOCATIONS[key]
        tile_path = simui_mod.tile_file(key)
        avail = (f"tile cached ({loc.tile_path})" if tile_path.exists()
                 else "tile MISSING -> synthetic fallback + amber banner")
        if not getattr(loc, "extended_zone", True):
            ext = "standard zone only (no extended tile by design)"
        else:
            ext = ("extended zone cached"
                   if simui_mod.tile_file(key, fire_zone="extended").exists()
                   else "extended zone MISSING -> standard fallback "
                        "(tools/fetch_sim_tiles.py --extended)")
        print(_console_line(f"  {key:<14} {loc.name:<34} {avail} · {ext}"))


def _cmd_simui(args) -> int:
    from wgresponse import server as server_mod
    from wgresponse import simui as simui_mod

    port = args.port if args.port is not None else (0 if args.smoke
                                                    else _SIMUI_DEFAULT_PORT)
    dev_open_added = _simui_auth_setup()
    httpd = None
    try:
        try:
            httpd = server_mod.serve(port=port, host=args.host)
        except OSError as e:
            if port != 0 and args.port is None:
                # Default port taken (stale server, second instance) — a demo
                # launch must not dead-end: fall back to an ephemeral port.
                print(f"note: port {port} is in use ({e}) — picking a free "
                      f"port instead", file=sys.stderr)
                try:
                    httpd = server_mod.serve(port=0, host=args.host)
                except OSError as e2:
                    print(f"error: cannot bind {args.host} ({e2})",
                          file=sys.stderr)
                    return 2
            else:
                print(f"error: cannot bind {args.host}:{port} ({e})",
                      file=sys.stderr)
                return 2
        host, bound_port = httpd.server_address[:2]
        url = f"http://{host}:{bound_port}/simulator"
        if dev_open_added:
            auth = ("DEV-OPEN by default (local demo; set WGRESP_API_KEY "
                    "to require a key)")
        elif os.environ.get("WGRESP_DEV_OPEN") == "1":
            auth = "DEV-OPEN (no auth!)"
        else:
            auth = "X-Internal-API-Key header"
        print(f"wgresponse simulator on {url}  [auth: {auth}]")
        _simui_print_locations(simui_mod)
        if args.smoke:
            server_thread = threading.Thread(target=httpd.serve_forever,
                                             daemon=True)
            server_thread.start()
            try:
                return _simui_smoke(host, bound_port)
            finally:
                httpd.shutdown()
                server_thread.join(timeout=5)
        print("  controls: safety slider (egress buffer), intensity+wind, "
              "clients/crews, run/pause/step/speed")
        print("  Ctrl-C stops the server.")
        if args.open_browser:
            import webbrowser
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    finally:
        if httpd is not None:
            httpd.server_close()
        if dev_open_added and args.smoke:
            # --smoke must not leak auth posture into the caller's process
            # (tests run in-process); the serving path keeps it for its own
            # lifetime, which IS the process lifetime.
            os.environ.pop("WGRESP_DEV_OPEN", None)


def _simui_smoke(host: str, port: int) -> int:
    """SIM-3 self-test over the live HTTP API.

    new session (real tile when cached, synthetic fallback otherwise) ->
    deterministic burnable-candidate ignite scan (real tiles have urban/water
    cells; a non-burnable click is a loud 400 by design) -> 3 engine ticks
    (each = one full replan cycle + SAFETY AUDIT) -> print the scoreboard.
    Exit 0 iff every call succeeded and violations == 0."""
    import http.client

    from wgresponse import geo as geo_mod
    from wgresponse import simui as simui_mod

    key = os.environ.get("WGRESP_API_KEY", "")

    def call(method, path, body=None):
        conn = http.client.HTTPConnection(host, port, timeout=300)
        try:
            headers = {"Content-Type": "application/json"}
            if key:
                headers["X-Internal-API-Key"] = key
            conn.request(method, path,
                         body=None if body is None else json.dumps(body),
                         headers=headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    status, out = call("POST", "/api/sim/new", {
        "location": _SMOKE_LOCATION, "clients": 8, "crews": 2, "seed": "smoke",
        # Config-only speedups (same overrides the real-tile integration test
        # uses): fewer ensemble members, shorter horizon. NEVER forked physics.
        "cfg": {"ensemble_n": 2, "horizon_h": 24.0},
    })
    if status != 200:
        print(f"smoke FAIL: /api/sim/new -> {status} {out}", file=sys.stderr)
        return 1
    sid = out["session_id"]
    state = out["state"]
    print(f"smoke: session {sid} seed={state['seed']} location={state['location']} "
          f"synthetic_terrain={state['terrain']['synthetic']}")
    for note in state.get("notes", []):
        print(f"smoke: NOTE {note}")

    center = simui_mod.LOCATIONS[_SMOKE_LOCATION].center
    candidates = [center]
    for dist_m in (1_000.0, 2_000.0, 3_000.0, 5_000.0):
        for bearing in (0.0, 90.0, 180.0, 270.0):
            candidates.append(geo_mod.destination(center, bearing, dist_m))
    fire_id = None
    for p in candidates:
        status, out = call("POST", f"/api/sim/{sid}/ignite",
                           {"lat": p.lat, "lon": p.lon, "intensity": "high"})
        if status == 200:
            fire_id = out["incident_id"]
            break
    print(f"smoke: fire "
          f"{fire_id or 'NONE (no burnable candidate; ticking fire-free)'}")

    for _ in range(3):
        status, out = call("POST", f"/api/sim/{sid}/ctl", {"action": "step"})
        if status != 200:
            print(f"smoke FAIL: step -> {status} {out}", file=sys.stderr)
            return 1
    state = out["state"]
    sb = state["scoreboard"]
    mm = sb["min_margin_h"]
    print(f"smoke scoreboard after {state['tick']} ticks:")
    print(f"  served         {sb['served']}")
    print(f"  unserved       {sb['unserved_by_reason'] or '{}'}")
    print(f"  min margin     {'n/a' if mm is None else f'{mm:.2f} h'}")
    print(f"  violations     {sb['violations']}")
    print(f"  buffer/slack   {sb['buffer_min']:g}/{sb['slack_min']:g} min")
    if sb["violations"] != 0:
        print("smoke FAIL: SAFETY VIOLATIONS PRESENT", file=sys.stderr)
        return 1
    print("smoke OK (violations=0)")
    return 0


# ---------------------------------------------------------------------------

_NEGATIVE_VALUE_OPTS = ("--bbox", "--aoi")


def _normalize_argv(argv) -> list:
    """Join `--bbox <value>` / `--aoi <value>` into the `=` form.

    Bbox/AOI values usually start with a negative west longitude
    ("-120,34,..."); argparse < 3.14 classifies such a token as an option
    (its negative-number heuristic rejects commas) and errors with "expected
    one argument". The `=` form is unambiguous on every supported Python.
    """
    out = list(argv)
    i = 0
    while i < len(out) - 1:
        if out[i] in _NEGATIVE_VALUE_OPTS and out[i + 1].startswith("-"):
            out[i] = f"{out[i]}={out[i + 1]}"
            del out[i + 1]
        i += 1
    return out


def main(argv=None) -> int:
    """CLI entrypoint; returns a process exit code (0 = success)."""
    if argv is None:
        argv = sys.argv[1:]
    args = _build_parser().parse_args(_normalize_argv(argv))
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
