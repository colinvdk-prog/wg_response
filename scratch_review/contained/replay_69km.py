"""Does the 69-km Willow refusal disappear? (2026-09-27; NOT product code)

Replays the W1 drill's phase-5 seed (restored from its persisted row: same
id -> same ensemble; a HIGH, wind-135 fire 14 km upwind of mock-client-001
on the Vail -> Aspen AOI) through the REAL ThreatMonitor + planner with the
production flags, the drill's cached LANDFIRE tiles and its three beacon
crews (Eagle, Gypsum, Avon), next to the live WFIGS Willow fire as RECORDED
on 2026-09-27 (out/contained/snapshot): seed alone, seed + Willow (legacy),
seed + Willow (--drop-contained --confirmed-perimeters). Also prints
Willow's corridor veto reach and the fail-closed T* of the Gypsum crew's
chord leg to client-001 against Willow's eroded P10 alone."""
import dataclasses
import json
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, WT)
sys.path.insert(0, HERE)

import feedsnap  # noqa: E402
from ab_national import NEW, PROD, CachingHttp, veto_reach, burned_km2  # noqa: E402
from wgresponse import cli, dispatch, feeds, geo, safety, weather  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (Crew, CrewBeacon, CrewStatus, LatLon,  # noqa: E402
                                  ResponseConfig)
from wgresponse.terrain import LfpsTerrain  # noqa: E402
from wgresponse.threat import ThreatMonitor  # noqa: E402

#: the W1 drill's phase-5 seed exactly as it was persisted (StateStore row;
#: docs/LIVE_WORLD_PLAN.md "W1 seeded incidents", phases 3-5) — the same id
#: re-mints the same ensemble. Its LANDFIRE tiles (and Willow's on this AOI)
#: are fetched once into out/contained/w1_terrain (LFPS, ~7 min each cold).
row = {"created_by": "w1-drill",
       "incident_id": "seeded:ab3e53cc-0036-5b3c-be3c-2b21c4095313",
       "intensity": "high", "lat": 39.23823175250883,
       "lon": -107.12402668765158,
       "note": ("W1 drill phase 5: HIGH south-easterly fire 14 km upwind of "
                "mock-client-001"),
       "seq": 1, "t_ignite_s": 1790486750.5792997, "weather": None,
       "wind_dir_deg": 135.0}
aoi, zones, _ = cli._parse_aoi(os.path.join(WT, "data", "aoi_vail_aspen.json"))
reg, _ = cli._watch_registry(SimpleNamespace(registry="mock"), aoi)
roster = {c.client_id: c for c in reg.protected_clients(aoi)}
now = float(feedsnap.ReplayHttp().manifest["recorded_at_s"])
willow = [i for i in feeds.WfigsFeed(http_get=feedsnap.ReplayHttp())
          .fetch_active(aoi) if i.name == "Willow"]
assert len(willow) == 1, willow
w = willow[0]
print("seed", row["incident_id"], "at", row["lat"], row["lon"])
print("Willow record age h", round((now - w.updated_at_s) / 3600, 1),
      "polygon age h", round((now - w.meta["perimeter_at_s"]) / 3600, 1),
      "reported", w.meta.get("reported_acres"), "polygon", w.meta.get("perimeter_acres"))
c001 = roster["mock-client-001"]
print("client-001", c001.loc, "km from Willow origin",
      round(geo.haversine_m(c001.loc, w.origin) / 1000, 1))


class Feed:
    def __init__(self, incs):
        self.incs = incs
        self.last_fetch_ok = True

    def fetch_active(self, bbox=None):
        return list(self.incs)


class Tracker:
    def __init__(self, t):
        self._c = [Crew(crew_id=c, name=c, status=CrewStatus.AVAILABLE,
                        last_beacon=CrewBeacon(crew_id=c, loc=l, t_s=t,
                                               status=CrewStatus.AVAILABLE),
                        home_base=l)
                   for c, l in (("E1", LatLon(39.6605, -106.8285)),
                                ("E2", LatLon(39.6465, -106.9425)),
                                ("E3", LatLon(39.6365, -106.523)))]

    def crews(self):
        return list(self._c)


base = dataclasses.replace(ResponseConfig(), **PROD)
wx = CachingHttp(os.path.join(feedsnap.DATA, "nws_cache"))
out = {"now_s": now, "variants": {}}


def _confirmed_at(t_s):
    """HYPOTHETICAL (containment-scaling options, report only): the same
    recorded Willow with its confirming report moved to t_s — the anchor
    code path, no engine change."""
    w2 = dataclasses.replace(w, meta=dict(w.meta, reported_at_s=t_s))
    return [w2]


free_h = (now - w.meta["reported_at_s"]) / 3600.0
variants = [("seed only", [], base),
            ("seed + Willow (legacy)", willow, base),
            ("seed + Willow (both knobs)", willow,
             dataclasses.replace(base, **NEW))]
if "--options" in sys.argv:
    variants += [
        ("HYPOTHETICAL option c: free-run scaled by (1 - 80%)",
         _confirmed_at(now - 0.2 * free_h * 3600.0),
         dataclasses.replace(base, **NEW)),
        ("HYPOTHETICAL option d: contained polygon is the fire at now",
         _confirmed_at(now), dataclasses.replace(base, **NEW))]
for label, incs, cfg in variants:
    mon = ThreatMonitor(
        feeds=[Feed(incs)], weather_provider=weather.NwsWeather(http_get=wx, cfg=cfg),
        terrain_provider=LfpsTerrain(
            cache_dir=os.path.join(feedsnap.DATA, "w1_terrain"),
            email=os.environ.get("LFPS_EMAIL") or "ops@thewildfireguardian.com"),
        registry=reg, alert_engine=AlertEngine(cfg), cfg=cfg,
        safe_zones=zones, crew_tracker=Tracker(now), dispatcher=dispatch, aoi=aoi)
    mon.restore_seeds([dict(row, retired_at_s=None, retired_reason="",
                            retired_by="")])
    res = mon.cycle(now)
    plan = res.plan
    stops = [(cid, st.client_id, st.incident_id[:18],
              round((st.arrive_s - now) / 3600, 2), round(st.margin_h, 2))
             for cid, cp in (plan.plans.items() if plan else []) for st in cp.stops]
    uns = [(u.client_id, u.incident_id[:18], u.reason)
           for u in (plan.unserved if plan else [])]
    v = {"stops": stops, "unserved": uns,
         "ensembles": sorted(k[:18] for k in res.ensembles),
         "terrain_s": {k[:18]: round(t.get("terrain_s", 0), 2) for k, t in
                       (res.timings.get("incidents") or {}).items()},
         "degraded": res.degraded,
         "failures": [n for n in res.notes if "skipped (" in n or "failed" in n]}
    ens = res.ensembles.get(w.incident_id)
    if ens is not None:
        tb_h, reach = veto_reach(ens, cfg, now)
        eroded = safety._erode(ens.p10, cfg.corridor_erosion_m, shape="chebyshev")
        e2 = LatLon(39.6465, -106.9425)
        speed = safety._corridor_speed_mps(cfg)
        leg = safety.LegOracle(e2, c001.loc, speed, eroded, cfg)
        # distance from client-001 to Willow's domain box
        sp = ens.spec
        v["willow"] = {
            "burned_p50_km2_at_now": round(burned_km2(ens, now), 1),
            "boundary_reached_h_rel_now": None if tb_h is None else round(tb_h, 1),
            "veto_reach_km": round(reach, 1),
            "c001_in_domain": geo.latlon_to_rc(sp, c001.loc) is not None,
            "leg_E2_to_c001_Tstar_h_rel_now": (
                None if leg.latest_safe_depart_s in (float("inf"), float("-inf"))
                else round((leg.latest_safe_depart_s - now) / 3600, 2)),
            "leg_samples_constrained": len(leg.samples),
            "clients_p10_p50_h": sorted(
                (a.client_id[-3:],) + tuple(round(x, 1) if x != float("inf")
                                            else None
                                            for x in a.eta.hours_out(now)[:2])
                for a in res.assessments if a.incident_id == w.incident_id),
            "notes": [n for n in res.notes if n.startswith("incident " + w.incident_id)],
        }
    out["variants"][label] = v
    print(f"\n[{label}] ensembles={v['ensembles']} degraded={res.degraded}")
    print("  stops:", stops)
    print("  unserved:", uns)
    if "willow" in v:
        print("  willow:", {k: val for k, val in v["willow"].items() if k != "notes"})
        for n in v["willow"]["notes"]:
            print("   NOTE", n[:300])
    for n in v["failures"]:
        print("  FAIL", n[:300])
with open(os.path.join(feedsnap.DATA, "replay_69km" + ("_options" if "--options" in sys.argv else "") + ".json"), "w", encoding="utf-8") as f:
    json.dump(out, f, indent=1, default=str)
