"""The Willow replay under the per-fire off-domain horizon (SAFETY SEMANTICS
15, founder rule 2026-09-27). NOT product code — measurements 1 and 2 of
docs/LIVE_WORLD_PLAN.md "The off-domain horizon is per fire (2026-09-27)".

Replays the W1 drill's phase-5 seed (restored from its persisted row: same id
-> same ensemble; a HIGH, wind-135 fire 14 km upwind of mock-client-001 on
the Vail -> Aspen AOI) through the REAL ThreatMonitor + planner with the
production flags, the drill's cached LANDFIRE tiles, cached NWS forecasts and
its three beacon crews (Eagle, Gypsum, Avon), next to the live WFIGS Willow
fire as RECORDED at 2026-09-27 15:55Z (scratch_review/contained/feedsnap.py),
with the incident-points body augmented by the fire-behaviour fields of the
SAME record revision (scratch_review/offmap/behavior_snapshot.py: Willow's
ModifiedOnDateTime is unchanged since the recording, so its behaviour then
was "Minimal | Smoldering, Creeping, Smoldering").

Variants (every one with the production flags, incl. --fail-closed):
  seed only                              the job alone (served)
  A  legacy                              Willow's polygon from its own time
  B  + contained-fire flags              anchored at the confirming record
  C  + contained flags + per-fire        the rule as built (margin 60)
  D  + per-fire only (legacy seeding)    the rule without the anchor
  E  = C with Willow reported "Active | Running" (HYPOTHETICAL): rapid

Measurement 2 (veto reach): Willow's eroded P10 under B's ensemble (the
contained-fire flags on); the distance beyond its domain edge at which a
corridor sample passed one hour from now is refused:
  R = ((pass + margin - t_b) * v + 0.71 cell) / 0.99
with t_b the earliest reached boundary run (safety._bound_arrival_s's slack
math), for (a) today (1.7 m/s, the leg's margin: 56 min chord — every live
watcher leg — or 30 min road), (b) the rule as known-slow (Willow's own
speed, 60 min), (c) the rule as rapid (1.7 m/s, 60 min); plus the figure
docs/review/offmap_willow_veto_2026-09-27.png.

    py -3 scratch_review/offmap/willow_offmap.py [--no-figure]

Data (gitignored): out/contained/{snapshot,w1_terrain,nws_cache} copied from
the contained-fire study; out/offmap/behavior_points.body (behavior_snapshot
record). Results: scratch_review/offmap/willow_offmap.json.
"""
import dataclasses
import json
import math
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, WT)
sys.path.insert(0, os.path.join(WT, "scratch_review", "contained"))
sys.path.insert(0, HERE)

import behavior_snapshot as bsnap  # noqa: E402
import feedsnap  # noqa: E402
from ab_national import NEW, PROD, CachingHttp, burned_km2  # noqa: E402
from wgresponse import cli, dispatch, feeds, geo, offmap, safety, weather  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (Crew, CrewBeacon, CrewStatus, LatLon,  # noqa: E402
                                  ResponseConfig)
from wgresponse.terrain import LfpsTerrain  # noqa: E402
from wgresponse.threat import ThreatMonitor  # noqa: E402

PER_FIRE = dict(corridor_offraster_per_fire=True,
                corridor_offraster_margin_min=60.0)
OUT_JSON = os.path.join(HERE, "willow_offmap.json")
FIGURE = os.path.join(WT, "docs", "review", "offmap_willow_veto_2026-09-27.png")
KMH = 3.6

#: the W1 drill's phase-5 seed exactly as persisted (replay_69km.py)
ROW = {"created_by": "w1-drill",
       "incident_id": "seeded:ab3e53cc-0036-5b3c-be3c-2b21c4095313",
       "intensity": "high", "lat": 39.23823175250883,
       "lon": -107.12402668765158,
       "note": ("W1 drill phase 5: HIGH south-easterly fire 14 km upwind of "
                "mock-client-001"),
       "seq": 1, "t_ignite_s": 1790486750.5792997, "weather": None,
       "wind_dir_deg": 135.0}
CREWS = (("E1", LatLon(39.6605, -106.8285)), ("E2", LatLon(39.6465, -106.9425)),
         ("E3", LatLon(39.6365, -106.523)))


class AugReplayHttp(feedsnap.ReplayHttp):
    """The recorded payloads; the incident-points body carries the behaviour
    fields of the same record revisions (behavior_snapshot)."""

    def __init__(self):
        super().__init__()
        self.aug_counts = None

    def __call__(self, url, headers=None):
        status, body = super().__call__(url, headers)
        if "WFIGS_Incident_Locations_Current" in url:
            body, self.aug_counts = bsnap.behavior_augment(body)
        return status, body


class Feed:
    def __init__(self, incs):
        self.incs = incs
        self.last_fetch_ok = True

    def fetch_active(self, bbox=None):
        return list(self.incs)


class Tracker:
    def __init__(self, t):
        self._c = [Crew(crew_id=c, name=c, status=CrewStatus.AVAILABLE,
                        last_beacon=CrewBeacon(crew_id=c, loc=loc, t_s=t,
                                               status=CrewStatus.AVAILABLE),
                        home_base=loc) for c, loc in CREWS]

    def crews(self):
        return list(self._c)


def dist_to_runs_m(p, runs):
    """Planar distance (m) from p to the nearest boundary-run segment."""
    kx = safety._M_PER_DEG_LAT * math.cos(math.radians(p.lat))
    ky = safety._M_PER_DEG_LAT
    best = math.inf
    for _t, la0, lo0, la1, lo1 in runs:
        x0, y0 = (lo0 - p.lon) * kx, (la0 - p.lat) * ky
        x1, y1 = (lo1 - p.lon) * kx, (la1 - p.lat) * ky
        dx, dy = x1 - x0, y1 - y0
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 <= 0 else min(1.0, max(0.0, -(x0 * dx + y0 * dy) / l2))
        best = min(best, math.hypot(x0 + t * dx, y0 + t * dy))
    return best


def reach_km(t_b, pass_s, margin_min, v, cell_m):
    """Veto reach beyond the edge run first reached at t_b (module doc)."""
    r = ((pass_s + margin_min * 60.0 - t_b) * v + 0.71 * cell_m) \
        / safety._FLAT_EARTH_SLACK
    return max(0.0, r) / 1000.0


def main():
    http = AugReplayHttp()
    now = float(http.manifest["recorded_at_s"])
    aoi, zones, _ = cli._parse_aoi(os.path.join(WT, "data",
                                                "aoi_vail_aspen.json"))
    reg, _ = cli._watch_registry(SimpleNamespace(registry="mock"), aoi)
    roster = {c.client_id: c for c in reg.protected_clients(aoi)}
    c001 = roster["mock-client-001"]
    (w,) = [i for i in feeds.WfigsFeed(http_get=http).fetch_active(aoi)
            if i.name == "Willow"]
    status = offmap.status_for(w, now, ResponseConfig())
    print("augmented points body:", http.aug_counts)
    print("Willow behaviour:", w.meta.get("fire_behavior"),
          w.meta.get("fire_behavior_descriptors"), "record age h",
          round((now - w.meta["fire_behavior_at_s"]) / 3600, 1), "->", status)
    w_rapid = dataclasses.replace(w, meta=dict(
        w.meta, fire_behavior="Active", fire_behavior_descriptors=["Running"]))
    base = dataclasses.replace(ResponseConfig(), **PROD)
    variants = [
        ("seed only", [], base),
        ("A legacy", [w], base),
        ("B contained-fire flags", [w], dataclasses.replace(base, **NEW)),
        ("C contained-fire flags + per-fire", [w],
         dataclasses.replace(base, **NEW, **PER_FIRE)),
        ("D per-fire only (legacy seeding)", [w],
         dataclasses.replace(base, **PER_FIRE)),
        ("E = C, Willow reported 'Active | Running' (HYPOTHETICAL)", [w_rapid],
         dataclasses.replace(base, **NEW, **PER_FIRE)),
    ]
    wx = CachingHttp(os.path.join(feedsnap.DATA, "nws_cache"))
    out = {"now_s": now, "snapshot_utc": http.manifest["recorded_utc"],
           "behavior_augment": http.aug_counts,
           "willow_status": list(status[:2]) + [list(status[2])],
           "client_001": [c001.loc.lat, c001.loc.lon],
           "client_001_km_from_willow": round(
               geo.haversine_m(c001.loc, w.origin) / 1000.0, 1),
           "variants": {}}
    keep = {}
    for label, incs, cfg in variants:
        mon = ThreatMonitor(
            feeds=[Feed(incs)],
            weather_provider=weather.NwsWeather(http_get=wx, cfg=cfg),
            terrain_provider=LfpsTerrain(
                cache_dir=os.path.join(feedsnap.DATA, "w1_terrain"),
                email=os.environ.get("LFPS_EMAIL")
                or "ops@thewildfireguardian.com"),
            registry=reg, alert_engine=AlertEngine(cfg), cfg=cfg,
            safe_zones=zones, crew_tracker=Tracker(now), dispatcher=dispatch,
            aoi=aoi)
        mon.restore_seeds([dict(ROW, retired_at_s=None, retired_reason="",
                                retired_by="")])
        res = mon.cycle(now)
        plan = res.plan
        v = {"stops": [[cid, st.client_id, st.incident_id[:18],
                        round((st.arrive_s - now) / 3600, 2),
                        round(st.margin_h, 2)]
                       for cid, cp in (plan.plans.items() if plan else [])
                       for st in cp.stops],
             "unserved": [[u.client_id, u.incident_id[:18], u.reason]
                          for u in (plan.unserved if plan else [])],
             "degraded": res.degraded,
             "failures": [n for n in res.notes
                          if "skipped (" in n or "failed" in n]}
        ens = res.ensembles.get(w.incident_id)
        if ens is not None:
            er = safety._erode(ens.p10, cfg.corridor_erosion_m,
                               shape="chebyshev")
            runs = safety._boundary_runs(er)
            t_b = min(r[0] for r in runs) if runs else None
            h = res.offmap.get(w.incident_id)
            p90, mx, n = offmap.front_speed_stats(ens.p10)
            speed = safety._corridor_speed_mps(cfg)
            e2 = dict(CREWS)["E2"]
            leg_flat = safety.LegOracle(e2, c001.loc, speed, er, cfg)
            leg_pf = (safety.LegOracle(e2, c001.loc, speed, er, cfg,
                                       offraster_ros_mps=h.ros_mps,
                                       offraster_margin_min=h.margin_min)
                      if h is not None else leg_flat)
            pts = safety._sample_polyline(e2, c001.loc, cfg.corridor_sample_m)
            leg_d = min(dist_to_runs_m(p, runs) for p in pts) if runs else None

            def rel(t):
                return (None if t is None or not math.isfinite(t)
                        else round((t - now) / 3600.0, 2))
            fs = offmap.front_speeds_mps(ens.p10)
            q50 = offmap.percentile_nearest_rank(fs, 0.5)
            q95 = offmap.percentile_nearest_rank(fs, 0.95)
            v["willow"] = {
                "burned_p50_km2_at_now": round(burned_km2(ens, now), 1),
                "domain": [ens.spec.nrows, ens.spec.ncols, ens.spec.cell_m],
                "edge_reached_h_rel_now": rel(t_b),
                "front_p50_kmh": None if q50 is None else round(q50 * KMH, 2),
                "front_p95_kmh": None if q95 is None else round(q95 * KMH, 2),
                "front_p90_kmh": None if p90 is None else round(p90 * KMH, 2),
                "front_max_kmh": None if mx is None else round(mx * KMH, 2),
                "front_cells": n,
                "horizon": (None if h is None else {
                    "kind": h.kind, "ros_kmh": round(h.ros_mps * KMH, 2),
                    "reason": h.reason, "margin_min": h.margin_min,
                    "status": list(h.status_reasons)}),
                "leg_E2_c001_min_km_beyond_edge": (None if leg_d is None else
                                                   round(leg_d / 1000.0, 1)),
                "leg_E2_c001_Tstar_h_rel_now_flat": rel(
                    leg_flat.latest_safe_depart_s),
                "leg_E2_c001_Tstar_h_rel_now_variant": rel(
                    leg_pf.latest_safe_depart_s),
                "notes": [nn for nn in res.notes
                          if nn.startswith("incident " + w.incident_id)],
            }
            keep[label] = (ens, er, runs, t_b, h, cfg)
        out["variants"][label] = v
        print(f"\n[{label}]")
        print("  stops:", v["stops"])
        print("  unserved:", v["unserved"])
        if "willow" in v:
            print("  willow:", {k: val for k, val in v["willow"].items()
                                if k != "notes"})
        for nn in v["failures"]:
            print("  FAIL", nn[:300])

    # ---- measurement 2: the veto reach on B's ensemble (contained flags on)
    ens, er, runs, t_b, _h, cfg_b = keep["B contained-fire flags"]
    h_c = keep["C contained-fire flags + per-fire"][4]
    cell = ens.spec.cell_m
    p90, mx, n = offmap.front_speed_stats(ens.p10)
    pf = h_c.ros_mps
    pass1 = now + 3600.0
    rows = [
        ("(a) today: 1.7 m/s, the leg's margin", 1.7, 56.0, 30.0),
        (f"(b) per-fire, known slow: {pf * KMH:.2f} km/h ({h_c.kind}), "
         f"margin 60", pf, 60.0, 60.0),
        ("(b') the same with the MAX front statistic "
         f"({min(mx, 1.7) * KMH:.2f} km/h)", min(mx, 1.7), 60.0, 60.0),
        ("(c) per-fire, rapid: 1.7 m/s, margin 60", 1.7, 60.0, 60.0),
    ]
    table = []
    for name, vel, m_chord, m_road in rows:
        table.append({"variant": name, "speed_kmh": round(vel * KMH, 2),
                      "chord_margin_min": m_chord, "road_margin_min": m_road,
                      "reach_km_chord": round(reach_km(t_b, pass1, m_chord,
                                                       vel, cell), 1),
                      "reach_km_road": round(reach_km(t_b, pass1, m_road,
                                                      vel, cell), 1)})
    # is a sample AT each crew / the job, passed +1 h, refused? (off-raster)
    points = dict(CREWS)
    points["client-001"] = c001.loc
    refused = {}
    for name, loc in points.items():
        inside = safety._raster_value_at(er, loc) is not None
        row = {"inside_domain": inside}
        for key, vel, marg in (("a", 1.7, 56.0), ("b", pf, 60.0),
                               ("c", 1.7, 60.0)):
            if inside:
                row[key] = None
                continue
            b = safety._bound_arrival_s(loc, runs, vel, cell)
            row[key] = {"bound_h_rel_now": round((b - now) / 3600.0, 2),
                        "refused": (b - pass1) < marg * 60.0}
        refused[name] = row
    print("refused at +1 h:", refused)
    out["veto_reach_pass_plus_1h"] = {
        "points_refused": refused,
        "ensemble": "B contained-fire flags (the anchored Willow)",
        "edge_first_reached_h_rel_now": round((t_b - now) / 3600.0, 2),
        "cell_m": cell, "front_p90_kmh": round(p90 * KMH, 2),
        "front_max_kmh": round(mx * KMH, 2), "front_cells": n,
        "rows": table}
    print("\nveto reach, pass +1 h (edge first reached "
          f"{(t_b - now) / 3600.0:+.1f} h):")
    for r in table:
        print("  ", r)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, default=str)
    if "--no-figure" not in sys.argv:
        figure(ens, er, runs, t_b, pf, now, c001, zones, out)


def figure(ens, er, runs, t_b, pf, now, c001, zones, out):
    """Left: the unsafe region (pass +1 h, chord legs) beyond Willow's domain
    under (a) / (b) / (c); right: veto reach vs pass time."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    ink, ink2, grid = "#0b0b0b", "#52514e", "#e4e3df"
    surf = "#fcfcfb"
    col = {"a": "#2a78d6", "b": "#eb6834", "c": "#1baf7a"}
    spec = ens.spec
    lat0, lon0 = 39.234, -106.44          # Willow (WFIGS origin)
    # ---- the bound on a grid (vectorised safety._bound_arrival_s)
    la = np.linspace(37.95, 40.55, 261)
    lo = np.linspace(-108.15, -104.65, 351)
    LO, LA = np.meshgrid(lo, la)
    kx = safety._M_PER_DEG_LAT * np.cos(np.radians(LA))
    ky = safety._M_PER_DEG_LAT
    slack = 0.71 * spec.cell_m

    def bound(v):
        best = np.full(LA.shape, np.inf)
        for tmin, la0, lo0, la1, lo1 in runs:
            x0 = (lo0 - LO) * kx
            y0 = (la0 - LA) * ky
            dx = (lo1 - LO) * kx - x0
            dy = (la1 - LA) * ky - y0
            l2 = dx * dx + dy * dy
            with np.errstate(invalid="ignore", divide="ignore"):
                t = np.where(l2 > 0, -(x0 * dx + y0 * dy) / l2, 0.0)
            t = np.clip(t, 0.0, 1.0)
            d = np.hypot(x0 + t * dx, y0 + t * dy) * safety._FLAT_EARTH_SLACK - slack
            best = np.minimum(best, tmin + np.maximum(d, 0.0) / v)
        return best

    south = spec.north_lat - spec.nrows * spec.cell_m / geo.M_PER_DEG_LAT
    east = spec.west_lon + spec.ncols * spec.cell_m / geo.m_per_deg_lon(
        spec.north_lat)
    pass1 = now + 3600.0
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(13.5, 6.9),
                                  gridspec_kw={"width_ratios": [1.35, 1]})
    fig.patch.set_facecolor(surf)
    for a in (ax, ax2):
        a.set_facecolor(surf)
        for s in a.spines.values():
            s.set_color(grid)
        a.tick_params(colors=ink2, labelsize=8)
    kxm = math.cos(math.radians(lat0))
    ax.set_aspect(1.0 / kxm)
    variants = (("a", 1.7, 56.0, "(a) today: 6.1 km/h, 56-min chord margin"),
                ("b", pf, 60.0, f"(b) per-fire, known slow: {pf * KMH:.1f} km/h,"
                                " 60-min margin"),
                ("c", 1.7, 60.0, "(c) per-fire, rapid: 6.1 km/h, 60-min margin"))
    styles = {"a": "solid", "b": "solid", "c": (0, (4, 2))}
    for key, v, m, lab in variants:
        unsafe = (bound(v) - pass1 < m * 60.0)
        ax.contour(LO, LA, unsafe.astype(float), levels=[0.5],
                   colors=[col[key]], linewidths=2.0,
                   linestyles=[styles[key]])
        ax.plot([], [], color=col[key], lw=2, ls=styles[key], label=lab)
    # the domain (inside it the forecast's own arrivals rule, not the bound)
    # and the modeled burn at now
    ax.add_patch(plt.Rectangle((spec.west_lon, south), east - spec.west_lon,
                               spec.north_lat - south, fc=surf, alpha=0.85,
                               ec=ink2, lw=1.0, ls=(0, (2, 2)), zorder=3))
    ax.text(spec.west_lon + 0.02, south - 0.02,
            "Willow's forecast domain (dashed box):\nits own modeled arrivals",
            fontsize=7, color=ink2, zorder=4, va="top")
    burned = np.array(ens.p50.data).reshape(spec.nrows, spec.ncols) <= now
    rr, cc = np.nonzero(burned)
    if len(rr):
        blat = spec.north_lat - (rr + 0.5) * spec.cell_m / geo.M_PER_DEG_LAT
        blon = spec.west_lon + (cc + 0.5) * spec.cell_m / geo.m_per_deg_lon(
            lat0)
        ax.scatter(blon[::3], blat[::3], s=0.6, color="#8f8d86", lw=0,
                   label="modeled burn at now (P50)", zorder=5)
    # the job, the crews, the leg
    for cid, loc in CREWS:
        ax.plot(loc.lon, loc.lat, marker="s", ms=6, color=ink, mec=surf,
                mew=1.5, zorder=6)
        ax.annotate(f"{cid}", (loc.lon, loc.lat), xytext=(-4, 6),
                    textcoords="offset points", fontsize=8, color=ink,
                    zorder=6)
    e2 = dict(CREWS)["E2"]
    ax.plot([e2.lon, c001.loc.lon], [e2.lat, c001.loc.lat], color=ink, lw=1.0,
            ls=(0, (1, 2)), zorder=6)
    ax.plot(c001.loc.lon, c001.loc.lat, marker="o", ms=8, color=ink,
            mec=surf, mew=2, zorder=6)
    ax.annotate("the seeded job (client-001),\n69 km from Willow;\n"
                "crew E2's leg dotted",
                (c001.loc.lon, c001.loc.lat), xytext=(-8, -6),
                textcoords="offset points", fontsize=8, color=ink, zorder=6,
                ha="right", va="top")
    ax.plot(lon0, lat0, marker="^", ms=8, color=ink, mec=surf, mew=1.5,
            zorder=6)
    ax.annotate("Willow", (lon0, lat0), xytext=(10, -3),
                textcoords="offset points", fontsize=8, color=ink, zorder=6)
    ax.set_xlim(lo[0], lo[-1])
    ax.set_ylim(la[0], la[-1])
    ax.grid(color=grid, lw=0.6)
    ax.set_xlabel("longitude", color=ink2, fontsize=8)
    ax.set_ylabel("latitude", color=ink2, fontsize=8)
    ax.set_title("Where a corridor sample passed 1 h from now is refused\n"
                 "(inside each line: Willow's off-domain bound closes the road)",
                 fontsize=10, color=ink, loc="left")
    leg = ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.09), ncol=2,
                    fontsize=7.5, frameon=False)
    for t in leg.get_texts():
        t.set_color(ink)
    # ---- right: reach vs pass time
    hrs = np.linspace(0, 24, 97)
    cell = spec.cell_m
    ends = {}
    for key, v, m, lab in variants:
        r = [reach_km(t_b, now + hh * 3600.0, m, v, cell) for hh in hrs]
        ax2.plot(hrs, r, color=col[key], lw=2, ls=styles[key])
        ends[key] = r[-1]
    # (a) and (c) differ by 4 min of margin: one shared end label
    ax2.annotate("(a) today and (c) rapid", (hrs[-1], ends["a"]),
                 xytext=(4, 0), textcoords="offset points", fontsize=8,
                 color=ink, va="center")
    ax2.annotate("(b) known slow", (hrs[-1], ends["b"]), xytext=(4, 0),
                 textcoords="offset points", fontsize=8, color=ink,
                 va="center")
    tstar = out["variants"]["C contained-fire flags + per-fire"]["willow"][
        "leg_E2_c001_Tstar_h_rel_now_variant"]
    if tstar is not None and tstar > 0:
        ax2.axvline(tstar, color=ink2, lw=1.0, ls=(0, (2, 2)))
        ax2.annotate(f"under (b) crew E2 can leave for client-001\nuntil "
                     f"+{tstar:.1f} h (under (a): closed since "
                     f"{out['variants']['B contained-fire flags']['willow']['leg_E2_c001_Tstar_h_rel_now_flat']:+.1f} h)",
                     (tstar, 5), xytext=(5, 0), textcoords="offset points",
                     fontsize=8, color=ink2)
    ax2.set_xlim(0, 30.5)
    ax2.set_ylim(0, None)
    ax2.grid(color=grid, lw=0.6)
    ax2.set_xlabel("hours from now the crew passes", color=ink2, fontsize=8)
    ax2.set_ylabel("veto reach beyond the edge the front crossed first (km)",
                   color=ink2, fontsize=8)
    ax2.set_title("The reach grows with the pass time at the extrapolation "
                  "speed\n(front first crossed the edge "
                  f"{-(t_b - now) / 3600.0:.1f} h ago; chord legs)",
                  fontsize=10, color=ink, loc="left")
    fig.text(0.01, 0.01,
             "Recorded WFIGS 2026-09-27 15:55Z, production flags + "
             "--drop-contained --confirmed-perimeters; SAFETY SEMANTICS 15 "
             "(scratch_review/offmap/willow_offmap.py).", fontsize=7,
             color=ink2)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    os.makedirs(os.path.dirname(FIGURE), exist_ok=True)
    fig.savefig(FIGURE, dpi=130, facecolor=surf)
    print("figure:", FIGURE)


if __name__ == "__main__":
    main()
