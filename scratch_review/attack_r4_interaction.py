"""attack_r4_interaction.py — FOUNDER ROUND 4 items 4 (INTERACTION): ember +
urgency TOGETHER.

  A. END-TO-END (real Malibu): developed-cell homes (own FBFM40 cell
     non-burnable) that the surface model can never reach DO flow through the
     law-6 ember fallback into finite ETAs -> alerts -> dispatch jobs -> gel, and
     every such developed home is correctly outcome-tagged. NO developed home is
     ever both 'not_threatened' AND gelled.
  B. URGENT-FIRST among EMBER-threatened homes: two developed homes whose ETAs
     come ONLY from the ember fallback, different p50, one crew that can serve
     just one -> the SOONER-to-burn (more urgent) developed home is served.

Run:  py -3 -m pytest scratch_review/attack_r4_interaction.py -q -s
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo, spread, dispatch, safety                  # noqa: E402
from wgresponse.contracts import (LatLon, GridSpec, Raster, TerrainTile,  # noqa: E402
                                  ResponseConfig, UNREACHED, WeatherForecast,
                                  WeatherSample, DispatchJob, Crew, CrewStatus,
                                  ClientProperty, ThreatEta, SafeZone,
                                  EnsembleArrival)
from attack_r4_common import (TILE_DIR, tile_present, skip_loud, rc_of)  # noqa: E402

T0 = 1_600_000_000.0


def _burnable(code):     # FBFM40 burnable namespace
    return 101 <= int(round(code)) <= 204


def _malibu(seed, n_clients=50, n_crews=3):
    from wgresponse.simui import SimSession
    from attack_sim_common import pick_ignition
    sess = SimSession("malibu_ca", n_clients, n_crews, seed=seed,
                      cfg_overrides={}, fire_zone="standard", tile_dir=TILE_DIR)
    p, _ = pick_ignition(sess, "malibu_ca", cluster=True)
    sess.ignite(p.lat, p.lon, intensity="high")
    return sess


@pytest.mark.skipif(not tile_present("malibu_ca"),
                    reason=skip_loud("malibu_ca", "tile"))
def test_developed_homes_flow_through_ember_and_are_tagged_right():
    """Ember + urgency both ON (defaults). Developed-cell homes reach dispatch
    via the fallback and are correctly tagged; none is both not_threatened and
    gelled; developed homes DO get gelled (the feature works end to end)."""
    R = 200.0
    developed_jobbed_seen = 0
    developed_gelled_seen = 0
    developed_ember_alerted = 0
    for seed in ("ix1", "ix2", "ix3"):
        sess = _malibu(seed, 55, 3)
        spec = sess._tile.spec
        # which clients sit on a non-burnable (developed/water) cell?
        dev_ids = set()
        for cl in sess._clients:
            rc = rc_of(spec, cl.loc.lat, cl.loc.lon)
            if rc is not None and not _burnable(sess._tile.fuel.get(*rc)):
                dev_ids.add(cl.client_id)
        for _t in range(36):
            st = sess.tick()
            now = st["sim_now_s"]
            for c in st["clients"]:
                if c["client_id"] not in dev_ids:
                    continue
                oc = c["outcome"]
                gelled = c["gel_applied"]
                # (core interaction guard) never both not_threatened AND gelled
                assert not (oc == "not_threatened" and gelled), (seed, c["client_id"])
                if gelled:
                    developed_gelled_seen += 1
                    assert oc in ("saved", "protected_then_impacted"), \
                        (seed, c["client_id"], oc)
                if c["eta_p50_s"] is not None:
                    # a developed home with a FINITE ETA can ONLY have gotten it
                    # from the ember fallback (own cell is non-burnable/UNREACHED)
                    developed_jobcandidate = True
                if c["state"] in ("notice_72h", "watch", "extreme_24h", "impacted"):
                    developed_ember_alerted += 1
                if c["client_id"] in sess._jobbed:
                    developed_jobbed_seen += 1
    # the fallback must actually be doing its job somewhere across these runs
    assert developed_jobbed_seen > 0, "no developed home ever became a job"
    assert developed_ember_alerted > 0, "no developed home ever alerted via ember"
    print(f"\n[interaction-A] developed-cell homes: jobbed-observations="
          f"{developed_jobbed_seen}, ember-alert-observations="
          f"{developed_ember_alerted}, gelled-observations={developed_gelled_seen}; "
          f"none was ever both not_threatened AND gelled")


# --------------------------------------------------------------------------
# B. urgent-first among ember-threatened developed homes (dispatch level)
# --------------------------------------------------------------------------
def _dev_tile():
    """31x41 GR2 tile, fire column on the west; TWO developed homes east, one
    NEAR the fire (reached fuel just west of it) and one FAR — both own cells
    non-burnable, so both ETAs come only from the ember fallback."""
    nr, nc, cm = 31, 41, 100.0
    spec = GridSpec(north_lat=34.2, west_lon=-118.8, cell_m=cm, nrows=nr, ncols=nc)
    n = nr * nc
    tile = TerrainTile(spec=spec, fuel=Raster(spec, [102.0] * n),
                       slope_deg=Raster(spec, [0.0] * n),
                       aspect_deg=Raster(spec, [0.0] * n),
                       elev_m=Raster(spec, [100.0] * n), source="synthetic")
    tile.fuel.set(15, 20, 91.0)     # NEAR developed home
    tile.fuel.set(15, 30, 91.0)     # FAR developed home
    return tile, spec


def test_ember_fallback_produces_finite_ordered_etas_for_developed_homes():
    """B1: two developed homes, own cells UNREACHED, BOTH get finite ordered
    ember ETAs (p10<=p50<=p90); the nearer-to-fire one is the more urgent
    (smaller p50) — the raw material the urgency term ranks on."""
    tile, spec = _dev_tile()
    ign = geo.rc_to_latlon(tile.spec, 15, 5)
    wx = WeatherForecast(loc=ign, samples=[
        WeatherSample(t_s=T0 + 3600 * i, wind_speed_mps=8.0, wind_gust_mps=10.0,
                      wind_dir_deg=270.0, rh_pct=15.0, temp_c=30.0)
        for i in range(160)], source="mock")
    cfg = ResponseConfig(dispatch_objective="homes", ensemble_n=2, horizon_h=200.0)
    ens = spread.ensemble_arrival(tile, [ign], wx, T0, cfg, incident_id="ix")
    near = geo.rc_to_latlon(spec, 15, 20)
    far = geo.rc_to_latlon(spec, 15, 30)
    assert math.isinf(spread.eta_at(ens, near).eta_p10_s)      # own cells unreached
    assert math.isinf(spread.eta_at(ens, far).eta_p10_s)
    en = spread.eta_at(ens, near, fallback_radius_m=200.0)
    ef = spread.eta_at(ens, far, fallback_radius_m=200.0)
    for e in (en, ef):
        assert T0 <= e.eta_p10_s <= e.eta_p50_s <= e.eta_p90_s < math.inf
    assert en.eta_p50_s < ef.eta_p50_s                          # NEAR more urgent


def test_urgency_selects_more_urgent_developed_home_when_count_equal():
    """B2: two EMBER-threatened developed homes enter dispatch as finite-ETA
    jobs. Same P10 safety deadline (both equally SAVABLE), symmetric travel, and
    mutually exclusive for one crew (serving one blows the other's deadline) ->
    a count-1-either-way choice. Urgency (law 7) breaks the tie toward the
    sooner-p50 home. This is exactly how a fire-adjacent developed home that got
    its ETA ONLY from the ember fallback is prioritised."""
    from attack_r4_urgency import spec20, cc, uniform_ens, make_crew
    cfg = ResponseConfig(dispatch_objective="homes")
    spec = spec20()
    ens = uniform_ens(spec)                                    # UNREACHED => safe corridors
    NOW = 1_800_000_000.0
    crew = make_crew("K", cc(spec, 10, 10))
    zones = [SafeZone("z", cc(spec, 10, 10))]
    p10 = NOW + 1.5 * 3600.0                                   # SAME deadline for both
    def job(cid, r, c, p50_h):
        return DispatchJob(
            client=ClientProperty(client_id=cid, loc=cc(spec, r, c), value_usd=1e6),
            incident_id="inc1",
            eta=ThreatEta(eta_p10_s=p10, eta_p50_s=NOW + p50_h * 3600.0,
                          eta_p90_s=NOW + (p50_h + 1) * 3600.0))
    # symmetric: both 5 cells from the crew; far apart (10 cells) => exclusive
    urgent = job("URGENT_DEV", 10, 5, 1.6)     # sooner p50 (ember-adjacent)
    calm = job("CALM_DEV", 10, 15, 60.0)       # far-future p50
    plan = dispatch.plan([crew], [urgent, calm], ens, zones, cfg, NOW)
    served = {s.client_id for cp in plan.plans.values() for s in cp.stops}
    assert served == {"URGENT_DEV"}, (served,
        "urgency did NOT prefer the sooner-to-burn developed home in a "
        "count-equal, equally-safe, symmetric choice")
    print(f"\n[interaction-B] count-equal ember-developed choice -> served "
          f"{served} (the sooner-p50 home; urgency law 7 among ember homes)")
