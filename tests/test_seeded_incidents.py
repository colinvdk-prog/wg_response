"""W1 seeded incidents — the engine side (docs/LIVE_WORLD_PLAN.md ticket 1.2;
contracts SAFETY SEMANTICS 12).

Contracts validation; fusion never merges a seeded incident with a feed one;
the staleness guard keeps a seeded fire until it is retired; the union-then-
filter law (a far seed is listed but builds no domain); the per-incident
weather override touches that incident only; a seeded domain is deterministic
given the same tile and IS the feed point-incident law; persistence + restart
re-fires nothing; retirement walks exactly the absence path of a vanished
feed incident; the horizon auto-retire; the drill-safe notifier; and
byte-identity with no seeds — pinned against goldens computed on commit
09f94b5, BEFORE any seeded-incident code existed.

TWO WORLDS (2026-09-27, contracts SAFETY SEMANTICS 16): every seed now lives
in the TEST world (daemon.test_world, a wgresponse.worlds.TestWorld running
inline after each live cycle) — the live monitor refuses seeds. The tests
below seed the test world and read its result / engine / state; wherever a
seed meets a feed fire, the feed fire is the LIVE world's, seen read-only.

Deterministic; no network; stub stack from tests/seeded_stack.py.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
from wgresponse import feeds, seeded, simui, spread  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    SEED_INTENSITIES,
    SEEDED_ID_PREFIX,
    AlertEvent,
    AlertState,
    FireIncident,
    IncidentSource,
    LatLon,
    SeedWeather,
    is_seeded_id,
)
from wgresponse.notify import LogNotifier  # noqa: E402
from wgresponse.persist import StateStore  # noqa: E402
from wgresponse.weather import MockWeather  # noqa: E402

NEAR = "seeded:test-near"
FAR = "seeded:test-far"
EXPLICIT = SeedWeather(wind_speed_mps=5.0, wind_gust_mps=6.5, rh_pct=30.0,
                       temp_c=25.0)

#: PRE-W1 GOLDENS: tests/seeded_stack.baseline_run() digests computed on
#: commit 09f94b5 (the Live World Plan commit), before any seeded-incident
#: code existed — 3 cycles of a feed fire + 2 clients + 1 crew + 1 safe zone
#: through the REAL monitor / ladder / planner / StateStore / _WatchDaemon —
#: and recomputed on the `git archive 09f94b5` engine tree with these exact
#: digest functions (the state digest normalizes CRLF, so it is the same on
#: every OS). The W1 build must reproduce every byte with no seed lit.
_GOLDEN_CYCLES = [
    "ac17b00f24122fdc679d82b1113770e90cebf14bbf6cf6dacbe8ffe5e6280968",
    "55163466e7cbec27b678a36939eb8119aba291ceae8b1f391874d4a2dab242e0",
    "f828a6bb41344d6f1d72570ad9365d8efe9c817abe74a763dbfc68f338fea846",
]
_GOLDEN_API = "388b5a434ad3369c48087accc06846f61b111cb6ebf9555bc571d4b289394d6d"
_GOLDEN_STATE = "16b7b049904028d0c8270699aa247f1622725680cdc0e4ede12b9ce53fa69798"


# ---------------------------------------------------------------------------
# byte-identity with no seeds (the contracts law)
# ---------------------------------------------------------------------------

def test_no_seeds_is_byte_identical_to_the_pre_w1_goldens(tmp_path):
    cycles, api, state = ss.baseline_run(tmp_path / "state")
    assert cycles == _GOLDEN_CYCLES          # every CycleResult decision
    assert api == _GOLDEN_API                # the /api/state payload
    assert state == _GOLDEN_STATE            # the persisted state file bytes


def test_no_seeds_adds_no_state_key_and_no_file_key(tmp_path):
    daemon, monitor, *_rest = ss.make_stack(tmp_path / "state")
    store = _rest[3]
    daemon.run_cycle()
    assert "seeds" not in daemon.state()
    assert "seeded_incidents" not in json.loads(store.path.read_text("utf-8"))
    assert StateStore(tmp_path / "state").load()["seeded_incidents"] == []


# ---------------------------------------------------------------------------
# contracts
# ---------------------------------------------------------------------------

def test_seed_intensities_are_the_simulator_presets():
    assert set(SEED_INTENSITIES) == set(simui.INTENSITY_WEATHER)
    assert IncidentSource.SEEDED.value == "seeded"


@pytest.mark.parametrize("intensity, weather, wind, mode", [
    (None, None, None, "live"),
    (None, None, 240.0, "live+fixed-dir"),
    ("high", None, 240.0, "preset"),
    ("high", None, None, "preset+live-dir"),
    (None, EXPLICIT, 240.0, "explicit"),
    (None, EXPLICIT, None, "explicit+live-dir"),
])
def test_valid_ignitions_and_their_weather_modes(intensity, weather, wind,
                                                 mode):
    ign = ss.seed(intensity=intensity, weather=weather, wind_dir_deg=wind)
    assert ign.problems() == []
    assert ign.weather_mode() == mode


@pytest.mark.parametrize("kw, needle", [
    ({"iid": "inc-1"}, "seeded namespace"),
    ({"iid": "seeded:"}, "seeded namespace"),
    ({"iid": "seeded:a b"}, "no whitespace"),
    ({"iid": "seeded:a/b"}, "no whitespace"),
    ({"loc": LatLon(float("nan"), -121.0)}, "finite"),
    ({"loc": LatLon(95.0, -121.0)}, "out of range"),
    ({"t_ignite_s": float("inf")}, "t_ignite_s"),
    ({"t_ignite_s": 0.0}, "t_ignite_s"),
    ({"intensity": "apocalyptic"}, "intensity must be one of"),
    ({"wind_dir_deg": 360.0}, "wind_dir_deg"),
    ({"wind_dir_deg": float("nan")}, "wind_dir_deg"),
    ({"note": "x" * 281}, "note"),
    ({"created_by": "y" * 65}, "created_by"),
    ({"intensity": None,
      "weather": SeedWeather(70.0, 80.0, 10.0, 30.0)}, "wind_speed_mps"),
    ({"intensity": None,
      "weather": SeedWeather(10.0, 5.0, 10.0, 30.0)}, "wind_gust_mps"),
    ({"intensity": None,
      "weather": SeedWeather(10.0, 12.0, 0.0, 30.0)}, "rh_pct"),
    ({"intensity": None,
      "weather": SeedWeather(10.0, 12.0, 10.0, 99.0)}, "temp_c"),
    ({"intensity": "high", "weather": EXPLICIT}, "exclusive"),
])
def test_invalid_ignitions_are_named(kw, needle):
    problems = ss.seed(**kw).problems()
    assert any(needle in p for p in problems), problems


def test_to_incident_is_a_fresh_point_incident():
    ign = ss.seed(t_ignite_s=ss.T0 - 7200.0, note="drill one")
    inc = ign.to_incident(ss.T0)
    assert inc.source is IncidentSource.SEEDED
    assert inc.incident_id == ign.incident_id
    assert inc.origin == ign.loc
    assert inc.perimeter is None and inc.hotspots is None
    assert inc.ignition_points() == [ign.loc]       # the point (point law)
    assert inc.acreage == 0.0 and inc.containment_pct is None
    assert inc.irwin_id is None
    assert inc.source_ids == {"seeded": ign.incident_id}
    assert inc.updated_at_s == ss.T0                # fresh every cycle
    assert inc.discovered_at_s == ss.T0 - 7200.0    # when it was lit
    assert inc.active is True
    assert inc.name.startswith("SEEDED fire ") and "drill one" in inc.name
    meta = inc.meta["seed"]
    assert meta["weather_mode"] == "preset" and meta["intensity"] == "high"
    assert meta["t_ignite_s"] == ss.T0 - 7200.0
    json.dumps(meta)                                # JSON-able for the state


def test_minted_ids_are_namespaced_replayable_and_distinct():
    a = seeded.mint_seed_id(1, ss.T0, 39.0, -121.0)
    assert a.startswith(SEEDED_ID_PREFIX) and is_seeded_id(a)
    assert a == seeded.mint_seed_id(1, ss.T0, 39.0, -121.0)    # replayable
    assert a != seeded.mint_seed_id(2, ss.T0, 39.0, -121.0)
    store = seeded.SeededIncidentStore()
    i1 = store.create(39.0, -121.0, ss.T0)
    i2 = store.create(39.0, -121.0, ss.T0)          # same point, same second
    assert i1.incident_id != i2.incident_id
    assert i1.problems() == [] and i1.weather_mode() == "live"
    assert store.create(39.0, -121.0, ss.T0,
                        wind_dir_deg=-1e-15).wind_dir_deg == 0.0


def test_store_cap_duplicates_retire_and_bounded_history():
    store = seeded.SeededIncidentStore(max_active=2, retired_keep=1)
    a = store.create(39.0, -121.0, ss.T0)
    store.add(ss.seed())
    with pytest.raises(seeded.SeedCapacityError):
        store.create(39.01, -121.0, ss.T0 + 1)
    with pytest.raises(seeded.SeedConflict):
        store.add(ss.seed())                        # duplicate id
    with pytest.raises(ValueError):
        store.add(ss.seed(iid="not-seeded"))
    store.retire(a.incident_id, ss.T0 + 10, by="op")
    with pytest.raises(seeded.SeedAlreadyRetired):
        store.retire(a.incident_id, ss.T0 + 20)
    with pytest.raises(KeyError):
        store.retire("seeded:nope", ss.T0)
    b = store.create(39.02, -121.0, ss.T0 + 30)     # room again
    store.retire(b.incident_id, ss.T0 + 40)         # 2 retired > keep 1
    ids = [e.ignition.incident_id for e in store.entries()]
    assert a.incident_id not in ids                 # the oldest retired pruned
    assert b.incident_id in ids
    assert [i.incident_id for i in store.active()] == [NEAR]
    ann = store.take_announcements()
    assert [e.ignition.incident_id for e in ann] == [a.incident_id,
                                                     b.incident_id]
    assert store.take_announcements() == []         # drained once


def test_store_rows_round_trip_and_malformed_rows_are_skipped(caplog):
    store = seeded.SeededIncidentStore()
    live = store.create(39.0, -121.0, ss.T0, created_by="op", note="n")
    expl = store.create(39.02, -121.0, ss.T0, weather=EXPLICIT,
                        wind_dir_deg=-90.0)
    store.retire(live.incident_id, ss.T0 + 60, by="op")
    rows = store.rows()
    json.dumps(rows)                                # verbatim-persistable
    other = seeded.SeededIncidentStore()
    bad = [{"incident_id": "garbage"},
           {"incident_id": "seeded:x", "lat": "nan?", "lon": 1,
            "t_ignite_s": ss.T0}]
    with caplog.at_level(logging.ERROR, logger="wgresponse.seeded"):
        assert other.restore(rows + bad) == 2
    assert sum("malformed" in r.message for r in caplog.records) == 2
    assert other.rows() == rows
    assert [i.incident_id for i in other.active()] == [expl.incident_id]
    assert other.ignition(expl.incident_id).wind_dir_deg == 270.0
    assert other.entry(live.incident_id).retired_by == "op"
    nxt = other.create(39.0, -121.0, ss.T0)         # the sequence continues
    assert nxt.incident_id not in {r["incident_id"] for r in rows}
    with caplog.at_level(logging.ERROR, logger="wgresponse.seeded"):
        assert seeded.SeededIncidentStore().restore(7) == 0   # never raises


# ---------------------------------------------------------------------------
# fusion never merges; the guards treat a seed as fresh
# ---------------------------------------------------------------------------

def test_fuse_incidents_never_joins_a_seeded_incident():
    ign = ss.seed()
    twin = FireIncident(incident_id="irwin-twin", name=ign.display_name(),
                        source=IncidentSource.WFIGS, origin=ign.loc,
                        updated_at_s=ss.T0, irwin_id="irwin-twin")
    # the seeded record even carries the SAME irwin id and name
    evil = dataclasses.replace(ign.to_incident(ss.T0), irwin_id="irwin-twin")
    for batches in ([[twin], [evil]], [[evil], [twin]], [[evil, twin]]):
        fused = feeds.fuse_incidents(batches)
        assert sorted(i.incident_id for i in fused) == ["irwin-twin", NEAR]
        (s,) = [i for i in fused if i.incident_id == NEAR]
        (f,) = [i for i in fused if i.incident_id == "irwin-twin"]
        assert s is evil and f is twin             # untouched, unmerged


def test_monitor_keeps_a_seed_apart_from_a_same_named_feed_fire(tmp_path):
    # TWO WORLDS: the seed is the TEST world's; the twin feed fire is the
    # LIVE world's, seen read-only on the test map (never merged either way)
    ign = ss.seed()
    twin = ss.feed_incident(iid="irwin-twin", name=ign.display_name(),
                            origin=ign.loc, irwin_id="irwin-twin")
    daemon, monitor, *_ = ss.make_stack(tmp_path / "s", incidents=[twin])
    tw = daemon.test_world
    tw.add_seed(ign)
    live = daemon.run_cycle()
    res = tw.last_result
    assert [i.incident_id for i in live.incidents] == ["irwin-twin"]
    assert [i.incident_id for i in res.incidents] == ["irwin-twin", NEAR]
    assert {i.incident_id: i.source for i in res.incidents} == {
        "irwin-twin": IncidentSource.MOCK, NEAR: IncidentSource.SEEDED}
    assert set(res.ensembles) == {"irwin-twin", NEAR}
    assert res.ensembles["irwin-twin"] is live.ensembles["irwin-twin"]


def test_staleness_gate_keeps_a_seeded_fire_until_it_is_retired(tmp_path):
    cfg = ss.small_cfg(incident_stale_h=1.0)
    zombie = ss.feed_incident(updated_at_s=ss.T0 - 3 * ss.H)   # 3 h old
    daemon, monitor, *_ = ss.make_stack(tmp_path / "s", cfg=cfg,
                                        incidents=[zombie])
    tw = daemon.test_world
    tw.add_seed(ss.seed(t_ignite_s=ss.T0 - 3 * ss.H))           # lit 3 h ago
    live = daemon.run_cycle()
    res = tw.last_result
    assert live.incidents == []                                 # gated live
    assert [i.incident_id for i in res.incidents] == [NEAR]
    # the live gate's note, seen on the test map (the live set is gated once)
    (gate,) = [n for n in res.notes if "stale incident" in n]
    assert "inc-1" in gate and SEEDED_ID_PREFIX not in gate
    assert NEAR in res.ensembles


# ---------------------------------------------------------------------------
# the union-then-filter law
# ---------------------------------------------------------------------------

def test_far_seed_is_listed_but_builds_no_domain(tmp_path):
    daemon, monitor, *_ = ss.make_stack(tmp_path / "s", incidents=[])
    tw = daemon.test_world
    tw.add_seed(ss.seed(iid=FAR, loc=ss.FAR_SEED_LOC))
    daemon.run_cycle()
    res = tw.last_result
    assert [i.incident_id for i in res.incidents] == [FAR]
    assert res.ensembles == {} and res.assessments == [] and res.plan is None
    (note,) = [n for n in res.notes if FAR in n]
    assert "no protected clients within 60 km" in note
    assert "seeded fire follows the live law" in note
    state = tw.state()
    (row,) = state["incidents"]
    assert row["source"] == "seeded" and row["seed"]["weather_mode"] == "preset"
    assert "preset 'high'" in row["seed"]["weather_label"]
    assert row["world"] == "test" and row["read_only"] is False
    (srow,) = state["seeds"]
    assert srow["status"] == "active" and srow["in_last_cycle"] is True
    assert srow["domain"] is None and srow["pairs"] == {}
    assert "seeds" not in daemon.state()                 # never the live state
    assert daemon.state()["incidents"] == []


def test_near_seed_gets_a_domain_the_ladder_and_dispatch(tmp_path):
    disp = ss.RecordingDispatcher()
    daemon, monitor, feed, engine, *_ = ss.make_stack(
        tmp_path / "s", incidents=[], test_dispatcher=disp)
    tw = daemon.test_world
    tw.add_seed(ss.seed())
    live = daemon.run_cycle()
    res = tw.last_result
    assert NEAR in res.ensembles
    assert {(a.client_id, a.incident_id) for a in res.assessments} == {
        ("c1", NEAR), ("c2", NEAR)}
    assert tw.engine.states()[("c1", NEAR)].state is AlertState.EXTREME_24H
    assert any(e.incident_id == NEAR and e.client_id == "c1"
               for e in res.events)
    assert disp.calls and {j.incident_id for j in disp.calls[-1]["jobs"]} \
        == {NEAR}
    planned = {st.incident_id for cp in res.plan.plans.values()
               for st in cp.stops} | {u.incident_id for u in res.plan.unserved}
    assert NEAR in planned
    (srow,) = tw.state()["seeds"]
    assert srow["domain"]["cells"] == (res.ensembles[NEAR].spec.nrows
                                       * res.ensembles[NEAR].spec.ncols)
    assert srow["domain"]["clients"] == 2
    assert srow["pairs"]["extreme_24h"] >= 1
    # the LIVE world saw none of it
    assert live.ensembles == {} and live.plan is None
    assert engine.states() == {}


def test_a_seed_leaves_the_feed_fire_physics_untouched(tmp_path):
    def feed_view(with_seed):
        daemon, monitor, *_ = ss.make_stack(tmp_path / str(with_seed))
        if with_seed:
            daemon.test_world.add_seed(ss.seed())
        res = daemon.run_cycle()
        ens = res.ensembles["inc-1"]
        if with_seed:        # the test map SHARES the live raster, verbatim
            assert daemon.test_world.last_result.ensembles["inc-1"] is ens
        return ([ss.raster_fingerprint(m) for m in ens.members],
                [(a.client_id, a.eta) for a in res.assessments
                 if a.incident_id == "inc-1"])
    assert feed_view(True) == feed_view(False)


# ---------------------------------------------------------------------------
# weather: live by default, the override for that incident only
# ---------------------------------------------------------------------------

def test_preset_override_applies_to_the_seed_only(tmp_path):
    disp = ss.RecordingDispatcher()
    tw_weather = ss.StubWeather()
    daemon, monitor, feed, engine, notifier, store, weather, terrain = \
        ss.make_stack(tmp_path / "s", test_dispatcher=disp,
                      test_weather=tw_weather)
    tw = daemon.test_world
    tw.add_seed(ss.seed(wind_dir_deg=240.0))
    daemon.run_cycle()
    res = tw.last_result
    # the live provider was asked for the FEED fire only (by the LIVE
    # world), and the test world asked nothing: the fully synthetic seed
    # costs no provider call and the feed fire's forecast is SHARED
    assert [loc for loc, _ in weather.calls] == [ss.ORIGIN]
    assert tw_weather.calls == []
    fx = disp.calls[-1]["forecasts"]
    feed_wx, seed_wx = fx["inc-1"], fx[NEAR]
    assert feed_wx.source == "stub"
    assert {(s.wind_speed_mps, s.wind_dir_deg) for s in feed_wx.samples} \
        == {(8.0, 270.0)}
    spec = simui.INTENSITY_WEATHER["high"]
    w = spec["wind_mph"] * simui.MPH_TO_MPS
    assert seed_wx.source == "seeded-preset"
    assert seed_wx.samples[0].t_s == res.t_s        # anchored at the cycle
    assert {(s.wind_speed_mps, s.wind_gust_mps, s.wind_dir_deg, s.rh_pct,
             s.temp_c) for s in seed_wx.samples} == {
        (w, w * simui.GUST_FACTOR, 240.0, spec["rh_pct"], spec["temp_c"])}
    # ... the simulator's exact weather script (simui._rebuild_weather)
    sim = MockWeather(seed="simui|any|wx", base_wind_mps=w, dir_deg=240.0,
                      rh_pct=spec["rh_pct"], temp_c=spec["temp_c"],
                      gust_factor=simui.GUST_FACTOR,
                      t0_s=res.t_s).forecast(ss.SEED_LOC, 24.0)
    assert [dataclasses.astuple(s) for s in seed_wx.samples] == \
        [dataclasses.astuple(s) for s in sim.samples]
    (note,) = [n for n in res.notes
               if n.startswith(f"seeded incident {NEAR}: weather")]
    assert "preset: preset 'high'" in note and "FROM 240" in note
    assert "no network" in note


@pytest.mark.parametrize("intensity, weather, wind, mode", [
    ("high", None, None, "preset+live-dir"),
    (None, None, 240.0, "live+fixed-dir"),
    (None, EXPLICIT, None, "explicit+live-dir"),
    (None, EXPLICIT, 240.0, "explicit"),
    (None, None, None, "live"),
])
def test_every_weather_mode_through_the_seam(intensity, weather, wind, mode):
    live = ss.StubWeather()
    ign = ss.seed(intensity=intensity, weather=weather, wind_dir_deg=wind)
    assert ign.weather_mode() == mode
    now = ss.T0 + 1800.0
    wxf = seeded.seeded_forecast(ign, live, ign.loc, 24.0, now)
    base = ss.StubWeather().forecast(ign.loc, 24.0)
    cond = seeded.seed_conditions(ign)
    if mode == "live":
        assert live.calls == [(ign.loc, 24.0)]
        assert wxf.source == "stub" and wxf.samples == base.samples
        return
    if cond is not None and wind is not None:
        assert live.calls == []                     # no network
        assert wxf.samples[0].t_s == now
    else:
        assert live.calls == [(ign.loc, 24.0)]      # the live half is live
        assert [s.t_s for s in wxf.samples] == [s.t_s for s in base.samples]
        assert wxf.source == "stub+seeded-" + mode
    for s, b in zip(wxf.samples, base.samples):
        assert s.wind_dir_deg == (wind if wind is not None
                                  else b.wind_dir_deg)
        src = b if cond is None else cond
        assert (s.wind_speed_mps, s.wind_gust_mps, s.rh_pct, s.temp_c) == (
            src.wind_speed_mps, src.wind_gust_mps, src.rh_pct, src.temp_c)


def test_live_seed_asks_the_live_provider_at_the_ignition(tmp_path):
    # "live" weather = the live PROVIDER (NWS in production) — the test
    # world's OWN instance of it, never the live world's
    tw_weather = ss.StubWeather()
    daemon, monitor, feed, engine, notifier, store, weather, terrain = \
        ss.make_stack(tmp_path / "s", incidents=[], test_weather=tw_weather)
    tw = daemon.test_world
    tw.add_seed(ss.seed(intensity=None, wind_dir_deg=None))
    daemon.run_cycle()
    res = tw.last_result
    assert [loc for loc, _ in tw_weather.calls] == [ss.SEED_LOC]
    assert weather.calls == []                     # the live world's: untouched
    (note,) = [n for n in res.notes if ": weather live:" in n]
    assert "live conditions" in note and "direction live" in note


def test_non_burnable_ignition_is_said_out_loud(tmp_path):
    daemon, monitor, *_ = ss.make_stack(tmp_path / "s", incidents=[],
                                        terrain=ss.StubTerrain(fuel_code=91.0))
    daemon.test_world.add_seed(ss.seed())
    daemon.run_cycle()
    res = daemon.test_world.last_result
    assert any("NON-BURNABLE (FBFM40 91)" in n for n in res.notes)


# ---------------------------------------------------------------------------
# determinism: the same seed + tile -> the same domain; the point law
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("override", [
    {"intensity": "high", "weather": None},             # the preset script
    {"intensity": None, "weather": SeedWeather(9.0, 12.0, 14.0, 31.0)},
])
def test_seeded_domain_is_deterministic_and_is_the_point_incident_law(
        tmp_path, override):
    views = []
    for k in range(2):
        terrain = ss.StubTerrain()                  # the TEST world's own
        tw_weather = ss.StubWeather()
        daemon, monitor, feed, engine, notifier, store, weather, _t = \
            ss.make_stack(tmp_path / f"r{k}", incidents=[],
                          test_terrain=terrain, test_weather=tw_weather)
        daemon.test_world.add_seed(ss.seed(**override))
        daemon.run_cycle()
        res = daemon.test_world.last_result
        ens = res.ensembles[NEAR]
        views.append((ens.spec, terrain.calls[-1],
                      [ss.raster_fingerprint(m) for m in ens.members],
                      ss.raster_fingerprint(ens.p10),
                      ss.raster_fingerprint(ens.p50),
                      ss.raster_fingerprint(ens.p90)))
    assert views[0] == views[1]
    assert tw_weather.calls == []       # fully synthetic: never the provider
    assert weather.calls == [] and _t.calls == []     # nor the live world's
    # ...and it is EXACTLY a feed point-incident: the point, seeded at now
    tile = ss.StubTerrain().tile(*terrain.calls[-1])
    wxf = seeded.seeded_forecast(ss.seed(**override), ss.StubWeather(),
                                 ss.SEED_LOC, 24.0, res.t_s)
    want = spread.ensemble_arrival(tile, [ss.SEED_LOC], wxf, res.t_s,
                                   ss.small_cfg(), incident_id=NEAR)
    assert [m.data for m in ens.members] == [m.data for m in want.members]
    assert ens.p10.data == want.p10.data and ens.p50.data == want.p50.data


# ---------------------------------------------------------------------------
# persistence + restart re-fires nothing
# ---------------------------------------------------------------------------

def test_seeds_persist_and_restart_refires_nothing(tmp_path):
    # TWO WORLDS: seeds, their ladder and their drill ledger persist in the
    # TEST namespace (<state>/test_world); the live world never alerts
    state_dir = tmp_path / "state"
    clock = ss.SettableClock(ss.T0)
    notifier = seeded.SeedSafeNotifier(LogNotifier())
    daemon, monitor, feed, engine, _, store, *_ = ss.make_stack(
        state_dir, incidents=[], notifier=notifier, clock=clock)
    tw = daemon.test_world
    code, body = tw.seed_create({"lat": ss.SEED_LOC.lat,
                                 "lon": ss.SEED_LOC.lon,
                                 "intensity": "high",
                                 "wind_dir_deg": 225})
    assert code == 201
    iid = body["incident_id"]
    # persisted at once — before any cycle ran — in the TEST namespace only
    test_state_dir = state_dir / "test_world"
    rows = StateStore(test_state_dir).load()["seeded_incidents"]
    assert [r["incident_id"] for r in rows] == [iid]
    assert not store.path.exists()                  # no live write at all
    # a second seed, retired before the restart, must stay retired
    code, body2 = tw.seed_create({"lat": 39.1, "lon": -121.1})
    gone = body2["incident_id"]
    assert tw.seed_retire(gone)[0] == 200
    for k in range(2):
        clock.t = ss.T0 + 60.0 + k * ss.PERIOD_S
        daemon.run_cycle()
    assert len(tw.drill_log.sent) >= 1             # the seed alerted...
    assert notifier._real.sent == []               # ...never a client channel
    assert list(notifier.drill.sent) == []         # (nor the live drill log)
    assert any(k[1] == iid for k in tw.engine.notified_ledger())   # ledgered
    assert engine.states() == {}                   # the live ladder: empty
    assert "seeded_incidents" not in json.loads(store.path.read_text("utf-8"))
    states_before = {k: r.state for k, r in tw.engine.states().items()}
    ledger_before = tw.engine.notified_ledger()

    # process-equivalent restart: fresh everything, same disk
    clock2 = ss.SettableClock(ss.T0 + 60.0 + 2 * ss.PERIOD_S)
    notifier2 = seeded.SeedSafeNotifier(LogNotifier())
    daemon2, monitor2, _, engine2, *_ = ss.make_stack(
        state_dir, incidents=[], notifier=notifier2, clock=clock2)
    tw2 = daemon2.test_world
    assert daemon2.restore() is True
    assert tw2.drill_state == "active" and tw2.restored is True
    assert [i.incident_id for i in tw2.monitor.seeded.active()] == [iid]
    assert tw2.monitor.seeded.entry(gone).retired_at_s is not None
    daemon2.run_cycle()
    res = tw2.last_result
    assert [i.incident_id for i in res.incidents] == [iid]
    assert list(tw2.drill_log.sent) == []          # RESTART RE-FIRES NOTHING
    assert notifier2._real.sent == [] and list(notifier2.drill.sent) == []
    assert {k: r.state for k, r in tw2.engine.states().items()} \
        == states_before
    assert tw2.engine.notified_ledger() == ledger_before


# ---------------------------------------------------------------------------
# retirement = absence (SAFETY SEMANTICS 12e)
# ---------------------------------------------------------------------------

def _absence_run(tmp_path, seeded_fire: bool, blind_after: bool = False):
    """A fire due WEST of c1 (upwind of the stub west wind) under the SAME
    live stub weather — a live-weather seed (the TEST world) or a feed
    point-incident (the LIVE world) — that goes away at cycle 2 (retired at
    the monitor level, like the old test / dropped by the feed). Returns the
    c1 pair's state per cycle and its alert events (cycle, prev, new) in the
    fire's own world. A blind LIVE feed blinds the test world too (it sees
    the live feed health read-only)."""
    clock = ss.SettableClock(ss.T0)
    daemon, monitor, feed, engine, *_ = ss.make_stack(
        tmp_path, incidents=[], clock=clock,
        registry_clients=[ss.clients()[0]])
    tw = daemon.test_world
    if seeded_fire:
        ign = ss.seed(loc=ss.ORIGIN, intensity=None, wind_dir_deg=None)
        tw.add_seed(ign)
        iid = ign.incident_id
        world_engine = tw.engine
    else:
        iid = "inc-9"
        feed.incidents = [ss.feed_incident(iid=iid, origin=ss.ORIGIN)]
        world_engine = engine
    traj, events = [], []
    for k in range(6):
        clock.t = ss.T0 + k * ss.PERIOD_S
        if k == 2:
            if seeded_fire:
                tw.monitor.retire_seed(iid, clock.t, by="test")
            else:
                feed.incidents = []
            if blind_after:
                feed.ok = False
        live = daemon.run_cycle()
        res = tw.last_result if seeded_fire else live
        assert (iid in [i.incident_id for i in res.incidents]) == (k < 2)
        traj.append(world_engine.states()[("c1", iid)].state)
        events += [(k, e.prev, e.state) for e in res.events
                   if e.incident_id == iid]
    return traj, events


def test_retire_walks_the_same_absence_path_as_a_vanished_feed_incident(
        tmp_path):
    seed_traj, seed_ev = _absence_run(tmp_path / "seed", seeded_fire=True)
    feed_traj, feed_ev = _absence_run(tmp_path / "feed", seeded_fire=False)
    assert seed_traj == feed_traj
    assert seed_ev == feed_ev
    E, C = AlertState.EXTREME_24H, AlertState.CLEARED
    assert seed_traj == [E, E, E, C, C, C]     # clear_cycles=2 of absence
    assert (3, E, C) in seed_ev


def test_retired_seed_pairs_freeze_while_a_feed_is_blind(tmp_path):
    traj, events = _absence_run(tmp_path, seeded_fire=True, blind_after=True)
    assert traj == [AlertState.EXTREME_24H] * 6     # absence is not clearance
    assert not any(new is AlertState.CLEARED for _, _, new in events)


def test_operator_retirement_is_noted_once(tmp_path):
    clock = ss.SettableClock(ss.T0)
    daemon, monitor, *_ = ss.make_stack(tmp_path / "s", incidents=[],
                                        clock=clock)
    tw = daemon.test_world
    tw.add_seed(ss.seed())
    daemon.run_cycle()
    tw.monitor.retire_seed(NEAR, ss.T0 + 100.0, by="colin")
    clock.t = ss.T0 + 300.0
    daemon.run_cycle()
    res = tw.last_result
    (note,) = [n for n in res.notes if "retired by colin" in n]
    assert NEAR in note and "absence law" in note
    clock.t += 300.0
    daemon.run_cycle()
    assert not [n for n in tw.last_result.notes if "retired by" in n]


# ---------------------------------------------------------------------------
# the horizon auto-retire (SAFETY SEMANTICS 12e)
# ---------------------------------------------------------------------------

def test_seed_auto_retires_after_its_horizon_when_nothing_is_threatened(
        tmp_path):
    clock = ss.SettableClock(ss.T0)
    daemon, monitor, *_ = ss.make_stack(tmp_path / "s", incidents=[],
                                        clock=clock)
    tw = daemon.test_world
    tw.add_seed(ss.seed(iid=FAR, loc=ss.FAR_SEED_LOC))
    clock.t = ss.T0 + 23.9 * ss.H
    daemon.run_cycle()
    assert [i.incident_id for i in tw.monitor.seeded.active()] == [FAR]
    clock.t = ss.T0 + 24.0 * ss.H                   # cfg.horizon_h = 24
    daemon.run_cycle()
    res = tw.last_result
    assert FAR in [i.incident_id for i in res.incidents]   # carried this cycle
    (ev,) = [o for o in res.operator_events if o.kind == "seed_retired"]
    assert ev.incident_id == FAR and ev.detail == {"reason": "horizon"}
    assert res.degraded is False                    # not a provider failure
    assert any("auto-RETIRED" in n for n in res.notes)
    entry = tw.monitor.seeded.entry(FAR)
    assert entry.retired_reason == "horizon" and entry.retired_by == "watcher"
    clock.t += ss.PERIOD_S
    daemon.run_cycle()
    assert tw.last_result.incidents == []


def test_threatening_or_blind_seed_is_never_auto_retired(tmp_path):
    clock = ss.SettableClock(ss.T0 + 25 * ss.H)
    daemon, monitor, feed, engine, *_ = ss.make_stack(
        tmp_path / "a", incidents=[], clock=clock)
    tw = daemon.test_world
    tw.add_seed(ss.seed())                          # near: threatens c1
    daemon.run_cycle()
    res = tw.last_result
    assert tw.engine.states()[("c1", NEAR)].state is AlertState.EXTREME_24H
    assert not [o for o in res.operator_events if o.kind == "seed_retired"]
    assert tw.monitor.seeded.active()

    class DeadRegistry:
        def protected_clients(self, bbox=None):
            raise RuntimeError("dynamo throttled")
    daemon2, monitor2, *_ = ss.make_stack(tmp_path / "b", incidents=[],
                                          clock=clock)
    tw2 = daemon2.test_world
    tw2.monitor._registry = DeadRegistry()          # assessment fails: no evidence
    tw2.add_seed(ss.seed(iid=FAR, loc=ss.FAR_SEED_LOC))
    live2 = daemon2.run_cycle()
    res2 = tw2.last_result
    assert res2.degraded is True
    assert live2.degraded is False                  # the live world: healthy
    assert tw2.monitor.seeded.active()              # never retired blind


# ---------------------------------------------------------------------------
# a seeded fire never reaches a client channel (SAFETY SEMANTICS 12g)
# ---------------------------------------------------------------------------

def test_seed_safe_notifier_routes_only_seeded_events_to_the_drill_log():
    real = LogNotifier()
    n = seeded.SeedSafeNotifier(real)
    client = ss.clients()[0]
    ev_seed = AlertEvent(t_s=ss.T0, client_id="c1", incident_id=NEAR,
                         prev=AlertState.NONE, state=AlertState.EXTREME_24H,
                         eta_p50_h=3.0, eta_p10_h=1.0, message="m")
    ev_feed = dataclasses.replace(ev_seed, incident_id="inc-1")
    assert n.send(ev_seed, client) is True
    assert n.send(ev_feed, client) is True
    assert [e.incident_id for e, _ in real.sent] == ["inc-1"]
    assert [e.incident_id for e, _ in n.drill.sent] == [NEAR]

    class Failing:
        def send(self, event, client):
            return False
    n2 = seeded.SeedSafeNotifier(Failing())
    assert n2.send(ev_seed, client) is True         # a drill needs no channel
    assert n2.send(ev_feed, client) is False        # the real one is untouched
