"""Pass-time footprint seeding (SAFETY SEMANTICS 65), measured ON ITS OWN
before it is armed (founder 2026-11-01 build order, step 1: "Measure it on
its own first, because it changes forecasts").

Real LANDFIRE terrain (the bundled preset tiles), a constant west wind of
6 m/s (RH 20 %, 30 C; the report-C experiment weather), detections of a
given age at random burnable spots, homes 1 / 3 / 8 km downwind (east).
Each case runs the real ThreatMonitor + AlertEngine one cycle with the knob
OFF and ON — nothing else differs — and records each home's p10 / p50 and
alert state. Output: results.jsonl + summary.json beside this script.

usage: python measure.py [--sites a,b] [--spots N] [--ages 0,1,3,6,12]"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from tests.test_threat import (StubFeed, StubRegistry,  # noqa: E402
                               StubWeather, make_monitor)
from tests.test_hotspots import _cluster  # noqa: E402
from wgresponse import geo, terrain  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import ClientProperty, ResponseConfig  # noqa: E402

NOW = 1_795_000_000.0
DISTS_M = (1000.0, 3000.0, 8000.0)


class _Down:
    def tile(self, bbox, cell_m):
        raise RuntimeError("offline: bundled tiles only")


def _cfg(on: bool) -> ResponseConfig:
    # the production physics knobs the live watcher arms (gust blend, grass
    # floor, model error, any-angle, extreme regime); ensemble 6, 72 h
    return dataclasses.replace(
        ResponseConfig(), horizon_h=72.0, ensemble_n=6,
        wind_gust_blend=0.5, grass_ros_floor_frac=0.10,
        model_error_sd_log=0.71, spread_any_angle=True,
        extreme_regime_enabled=True, hotspot_pass_time_seeding=on)


def _spots(tile, n, rng):
    """n random burnable spots in the tile's inner third (room for 8 km)."""
    spec = tile.spec
    out = []
    tries = 0
    while len(out) < n and tries < 5000:
        tries += 1
        r = rng.randrange(spec.nrows // 3, 2 * spec.nrows // 3)
        c = rng.randrange(spec.ncols // 3, 2 * spec.ncols // 3 - 1)
        code = tile.fuel.data[r * spec.ncols + c]
        if code is None or not (100 <= code < 200 or 140 <= code < 190
                                or 160 <= code <= 205):
            continue
        p = geo.rc_to_latlon(spec, r, c)
        out.append((p, code))
    return out


def run_case(site_tile, p, age_h, on):
    homes = [ClientProperty(f"h{int(d / 1000)}",
                            geo.destination(p, 90.0, d), name=f"{d:g} m")
             for d in DISTS_M]
    cl = _cluster([p, geo.destination(p, 0.0, 120.0)],
                  t0=NOW - age_h * 3600.0)
    cfg = _cfg(on)
    eng = AlertEngine(cfg)
    mon = make_monitor(feeds=[StubFeed([cl])],
                       weather=StubWeather(wind_mps=6.0),
                       terrain=site_tile, registry=StubRegistry(homes),
                       alert_engine=eng, cfg=cfg)
    t0 = time.monotonic()
    res = mon.cycle(NOW)
    out = {}
    states = {k[0]: r.state.value for k, r in eng.states().items()}
    for a in res.assessments:
        out[a.client_id] = {"p10_h": (a.eta.eta_p10_s - NOW) / 3600.0
                            if math.isfinite(a.eta.eta_p10_s) else None,
                            "p50_h": (a.eta.eta_p50_s - NOW) / 3600.0
                            if math.isfinite(a.eta.eta_p50_s) else None,
                            "state": states.get(a.client_id, "none")}
    return out, time.monotonic() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default="paradise_ca,malibu_ca,boulder_co,"
                                        "park_city_ut")
    ap.add_argument("--spots", type=int, default=6)
    ap.add_argument("--ages", default="0,1,3,6,12")
    a = ap.parse_args()
    ages = [float(x) for x in a.ages.split(",")]
    tiles_dir = REPO / "data" / "tiles"
    prov = terrain.BundledFallbackTerrain(_Down(), tiles_dir)
    rows = []
    log = open(HERE / "results.jsonl", "w", encoding="utf-8")
    rng = random.Random(20261119)
    for site in a.sites.split(","):
        path = tiles_dir / f"{site}_ext.wgt"
        if not path.exists():
            path = tiles_dir / f"{site}.wgt"
        tile = terrain.load_tile_file(path) if hasattr(
            terrain, "load_tile_file") else None
        if tile is None:
            tile = terrain.load_ascii_tile(str(path))
        spots = _spots(tile, a.spots, rng)
        for k, (p, code) in enumerate(spots):
            for age in ages:
                both = {}
                for on in (False, True):
                    both[on], dt = run_case(prov, p, age, on)
                for h in sorted(set(both[False]) | set(both[True])):
                    row = {"site": site, "spot": k, "fuel": code,
                           "lat": round(p.lat, 5), "lon": round(p.lon, 5),
                           "age_h": age, "home": h,
                           "off": both[False].get(h), "on": both[True].get(h)}
                    rows.append(row)
                    log.write(json.dumps(row) + "\n")
                log.flush()
                print(f"{site} spot {k} age {age:g} h done ({dt:.1f} s)",
                      flush=True)
    log.close()
    json.dump(summarize(rows), open(HERE / "summary.json", "w",
                                    encoding="utf-8"), indent=1)
    print(json.dumps(summarize(rows), indent=1))


def summarize(rows):
    order = {"none": 0, "cleared": 0, "notice_72h": 1, "watch": 2,
             "extreme_24h": 3, "impacted": 4}
    out = {}
    for age in sorted({r["age_h"] for r in rows}):
        for home in sorted({r["home"] for r in rows}):
            rs = [r for r in rows if r["age_h"] == age and r["home"] == home]
            sh, later, up, ext_on, ext_off, zero_on, zero_off = \
                [], 0, 0, 0, 0, 0, 0
            for r in rs:
                off, on = r["off"] or {}, r["on"] or {}
                if off.get("p50_h") is not None and on.get("p50_h") is not None:
                    sh.append(off["p50_h"] - on["p50_h"])
                if (off.get("p10_h") is not None and on.get("p10_h") is not None
                        and on["p10_h"] > off["p10_h"] + 1e-6):
                    later += 1
                so = order.get(off.get("state", "none"), 0)
                sn = order.get(on.get("state", "none"), 0)
                up += sn > so
                ext_on += sn >= 3
                ext_off += so >= 3
                zero_on += (on.get("p50_h") is not None
                            and on["p50_h"] <= 0.0)
                zero_off += (off.get("p50_h") is not None
                             and off["p50_h"] <= 0.0)
            sh.sort()
            out[f"age {age:g} h / {home}"] = {
                "cases": len(rs),
                "p50_earlier_h_median": (round(sh[len(sh) // 2], 2)
                                         if sh else None),
                "p50_earlier_h_max": round(max(sh), 2) if sh else None,
                "p10_later_cases": later,
                "state_raised": up,
                "extreme_or_more_off": ext_off, "extreme_or_more_on": ext_on,
                "arrival_already_off": zero_off,
                "arrival_already_on": zero_on}
    return out


if __name__ == "__main__":
    main()
