"""bench_cycle — full ThreatMonitor.cycle wall-clock breakdown.

Drives wgresponse.threat.ThreatMonitor with the canyon_wind_event simulation
scenario providers (sim._Scenario* mocks — deterministic, no network) and the
REAL sibling modules everywhere else: spread.ensemble_arrival,
alerts.AlertEngine, crews.CrewTracker, dispatch.plan.

Phase attribution is via runtime timing shims (module attributes / provider
proxies restored afterwards — product files untouched):
    feeds | terrain | weather | spread(ensemble) | alerts | dispatch | other

Also reports a scaled cycle: the same scenario weather/clients/crews but the
spread domain re-gridded to cfg.grid_cell_m=90 (operational default) instead
of the scenario's 250 m cells, which is what a real incident window costs.

Usage:  py -3 bench/bench_cycle.py [--cycles 4] [--skip-90m]
"""
from __future__ import annotations

import argparse
import time

from _benchlib import env_banner, fmt_s, table

from wgresponse import alerts as alerts_mod
from wgresponse import crews as crews_mod
from wgresponse import dispatch as dispatch_mod
from wgresponse import sim, spread
from wgresponse.contracts import CrewBeacon, CrewStatus, ResponseConfig
from wgresponse.threat import ThreatMonitor


class _Acc:
    """Per-phase elapsed-time accumulator."""

    def __init__(self):
        self.t = {}

    def add(self, key, dt):
        self.t[key] = self.t.get(key, 0.0) + dt

    def reset(self):
        self.t = {}


def _timed(acc, key, fn):
    def wrapper(*a, **k):
        t0 = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            acc.add(key, time.perf_counter() - t0)
    return wrapper


class _TimedProvider:
    """Times one named method of a provider object."""

    def __init__(self, inner, acc, key, method):
        self._inner = inner
        setattr(self, method, _timed(acc, key, getattr(inner, method)))

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _TimedDispatcher:
    def __init__(self, acc):
        self._acc = acc

    def plan(self, *a, **k):
        t0 = time.perf_counter()
        try:
            return dispatch_mod.plan(*a, **k)
        finally:
            self._acc.add("dispatch", time.perf_counter() - t0)


class _Regrid250to90:
    """Terrain proxy: serve the scenario tile re-gridded at the REQUESTED bbox
    and cell size (nearest-neighbor from the 250 m scenario tile) so the
    operational 90 m grid cost can be measured with identical geography."""

    def __init__(self, inner):
        self._inner = inner

    def tile(self, bbox, cell_m):
        from wgresponse.contracts import Raster, TerrainTile
        from wgresponse.terrain import _grid_for_bbox
        src = self._inner.tile(bbox, cell_m)          # scenario-spec tile
        spec = _grid_for_bbox(bbox, cell_m)
        from wgresponse import geo
        bands = []
        for band in (src.fuel, src.slope_deg, src.aspect_deg, src.elev_m):
            out = Raster(spec)
            for r in range(spec.nrows):
                for c in range(spec.ncols):
                    p = geo.rc_to_latlon(spec, r, c)
                    v = geo.raster_sample(band, p)
                    if v is None:                     # outside src: nearest edge
                        rr = min(max(int((src.spec.north_lat - p.lat) * 111320.0
                                         / src.spec.cell_m), 0), src.spec.nrows - 1)
                        cc = 0 if p.lon < src.spec.west_lon else src.spec.ncols - 1
                        v = band.get(rr, cc)
                    out.set(r, c, v)
            bands.append(out)
        return TerrainTile(spec=spec, fuel=bands[0], slope_deg=bands[1],
                           aspect_deg=bands[2], elev_m=bands[3],
                           source=src.source + ":regrid")


def build_monitor(sc, cfg, acc, regrid_90m: bool):
    feed = _TimedProvider(sim._ScenarioFireFeed(sc), acc, "feeds", "fetch_active")
    terrain = sim._ScenarioTerrain(sc)
    if regrid_90m:
        terrain = _Regrid250to90(terrain)
    terrain = _TimedProvider(terrain, acc, "terrain", "tile")
    weather = _TimedProvider(sim._ScenarioWeather(sc), acc, "weather", "forecast")
    registry = sim._ScenarioClients(sc)
    engine = alerts_mod.AlertEngine(cfg)
    engine.evaluate = _timed(acc, "alerts", engine.evaluate)
    tracker = crews_mod.CrewTracker(cfg)
    monitor = ThreatMonitor([feed], weather, terrain, registry, engine, cfg,
                            sc.safe_zones, crew_tracker=tracker,
                            dispatcher=_TimedDispatcher(acc))
    return monitor, tracker


def run_cycles(name, sc, cfg, n_cycles, regrid_90m):
    acc = _Acc()
    monitor, tracker = build_monitor(sc, cfg, acc, regrid_90m)
    orig_ens = spread.ensemble_arrival
    spread.ensemble_arrival = _timed(acc, "spread", orig_ens)
    rows = []
    try:
        for k in range(n_cycles):
            now = sc.t0_s + k * cfg.replan_period_s
            for cr in sc.crews:       # fresh GPS beacons: crews stay eligible
                tracker.ingest(CrewBeacon(crew_id=cr.crew_id, loc=cr.home_base,
                                          t_s=now, status=CrewStatus.AVAILABLE))
            acc.reset()
            t0 = time.perf_counter()
            cyc = monitor.cycle(now)
            total = time.perf_counter() - t0
            ph = dict(acc.t)
            other = total - sum(ph.values())
            plan_stops = (sum(len(p.stops) for p in cyc.plan.plans.values())
                          if cyc.plan else 0)
            rows.append((k, total, ph, other, len(cyc.assessments),
                         len(cyc.events), plan_stops, len(cyc.notes)))
    finally:
        spread.ensemble_arrival = orig_ens

    print(f"\n== {name} ==")
    t = table(["cycle", "total_s", "spread_s", "dispatch_s", "terrain_s",
               "alerts_s", "other_s", "assess", "events", "stops"],
              [6, 8, 9, 10, 9, 9, 8, 7, 7, 6])
    t.print_header()
    for (k, total, ph, other, n_ass, n_ev, n_stops, _n) in rows:
        t.print_row([k, fmt_s(total), fmt_s(ph.get("spread", 0.0)),
                     fmt_s(ph.get("dispatch", 0.0)), fmt_s(ph.get("terrain", 0.0)),
                     fmt_s(ph.get("alerts", 0.0)), fmt_s(other),
                     n_ass, n_ev, n_stops])
    tot = sum(r[1] for r in rows)
    sp = sum(r[2].get("spread", 0.0) for r in rows)
    dp = sum(r[2].get("dispatch", 0.0) for r in rows)
    print(f"totals: {tot:.1f} s over {len(rows)} cycles  "
          f"(spread {sp / tot:.0%}, dispatch {dp / tot:.0%}, "
          f"other {(tot - sp - dp) / tot:.0%}); "
          f"budget/cycle = replan_period_s = {cfg.replan_period_s:.0f} s")
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cycles", type=int, default=4)
    ap.add_argument("--skip-90m", action="store_true")
    args = ap.parse_args()

    print("bench_cycle |", env_banner())
    sc = sim.build_scenario("canyon_wind_event", "default")
    cfg = ResponseConfig()
    print(f"scenario grid {sc.spec.nrows}x{sc.spec.ncols} @ {sc.spec.cell_m:.0f} m "
          f"({sc.spec.nrows * sc.spec.ncols:,} cells), 12 clients, 3 crews, "
          f"ensemble_n={cfg.ensemble_n}, replan={cfg.replan_period_s:.0f} s")

    run_cycles(f"A. canyon_wind_event as-built ({sc.spec.cell_m:.0f} m cells)",
               sc, cfg, args.cycles, regrid_90m=False)
    if not args.skip_90m:
        run_cycles("B. same incident on the operational 90 m grid "
                   "(cfg.grid_cell_m default)", sc, cfg,
                   max(2, args.cycles // 2), regrid_90m=True)


if __name__ == "__main__":
    main()
