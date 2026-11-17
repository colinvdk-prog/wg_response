"""tests/test_live_crew_laws.py — THE OCTOBER PLANNING LAWS IN THE WATCHER
(SAFETY SEMANTICS 45e; the crew-program review's lane C finding F5: 20a, 26,
32, 37 and 38 are dispatch knobs the simulator arms and `watch` never did).

`watch --crew-laws` arms, with the simulator's values, the ones that mean
something on the live planner's straight chords — 20a corridor_cell_exact,
26 dispatch_keep_frozen, 32 plan_egress_time_deadline, 38
egress_dest_durable_h = 8 h — and leaves out 37 (route_margin_egress_ramp:
it ramps the ROAD egress margin of 31 and reads only road legs; shown here
to change nothing on chords) and 35 (ember_eta_landings: the founder's
err-pessimistic rule).

On three stub worlds through the REAL monitor / planner (the seeded stack,
the off-domain stack under the fail-closed law, and a dense world: eight
homes downwind of one fire, three crews, three zones, one of them downwind)
every plan with the laws armed is re-verified stop by stop against the law
it was planned under — ingress corridor, the slack floor, a lawful way out
reached by the home's latest departure — and what changes is pinned."""
from __future__ import annotations

import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import offmap_stack as osk  # noqa: E402
import seeded_stack as ss  # noqa: E402
from wgresponse import cli, dispatch, geo, safety, simui  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.cli import _WatchDaemon  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    ClientProperty,
    LatLon,
    ResponseConfig,
    SafeZone,
    validate_config,
)
from wgresponse.notify import LogNotifier  # noqa: E402
from wgresponse.persist import StateStore  # noqa: E402
from wgresponse.threat import ThreatMonitor  # noqa: E402
from wgresponse.travel import travel_time_s  # noqa: E402

T0 = ss.T0
_EPS = 1e-3


def laws_cfg(cfg):
    """cfg with `watch --crew-laws` applied (the CLI's own helper)."""
    args = cli._build_parser().parse_args(cli._normalize_argv(
        ["watch", "--aoi", "-121.2,38.8,-120.8,39.2", "--crew-laws"]))
    out, notes = cli._crew_program_cfg(cfg, args)
    assert len(notes) == 1
    return out


# ---------------------------------------------------------------------------
# the dense world
# ---------------------------------------------------------------------------

DENSE_HOMES = [
    ("d1", 39.000, -120.975), ("d2", 39.010, -120.960),
    ("d3", 38.990, -120.955), ("d4", 39.020, -120.940),
    ("d5", 38.985, -120.930), ("d6", 39.005, -120.915),
    ("d7", 39.030, -120.905), ("d8", 38.975, -120.895),
]
DENSE_ZONES = [SafeZone("Z1", LatLon(39.18, -121.05), "north"),
               SafeZone("Z2", LatLon(38.85, -121.10), "south-west"),
               SafeZone("Z3", LatLon(39.00, -120.93), "downwind, among the "
                                                       "homes")]
DENSE_CREWS = [("G1", LatLon(39.10, -121.05)), ("G2", LatLon(39.12, -121.06)),
               ("G3", LatLon(38.92, -121.08))]


def dense_stack(state_dir, cfg, clock):
    roster = [ClientProperty(client_id=c, loc=LatLon(la, lo), name=c,
                             graphgel_enrolled=True)
              for c, la, lo in DENSE_HOMES]
    registry = ss.StubRegistry(roster)
    engine = AlertEngine(cfg, notifier=LogNotifier(),
                         clients_by_id={c.client_id: c for c in roster})
    crews = [ss.crew(crew_id=cid, loc=loc) for cid, loc in DENSE_CREWS]
    monitor = ThreatMonitor(
        feeds=[ss.ScriptedFeed([ss.feed_incident()])],
        weather_provider=ss.StubWeather(), terrain_provider=ss.StubTerrain(),
        registry=registry, alert_engine=engine, cfg=cfg,
        safe_zones=DENSE_ZONES, crew_tracker=ss.StubTracker(crews),
        dispatcher=dispatch, aoi=ss.AOI)
    daemon = _WatchDaemon(monitor=monitor, engine=engine, registry=registry,
                          tracker=None, store=StateStore(state_dir), cfg=cfg,
                          aoi=ss.AOI, period_s=ss.PERIOD_S,
                          safe_zones=DENSE_ZONES, clock=clock,
                          sleep=lambda s: None)
    return daemon, monitor, {c.client_id: c for c in roster}, crews


# ---------------------------------------------------------------------------
# the lawfulness check
# ---------------------------------------------------------------------------

def assert_plan_lawful(res, cfg, crews, clients, zones):
    """Every planned stop of res.plan, re-verified against the law it was
    planned under (the dispatcher's own rasters): the ingress chord lawful
    at its departure (SEMANTICS 3, cell-exact under 20a), the departure
    inside the slack floor (2 / 7), and a way out — some zone whose leg is
    lawful at the departure (3 / 4; 38's keep-until when armed) and, under
    32, reached by the home's latest departure. Returns the stop count."""
    plan = res.plan
    if plan is None:
        return 0
    rasters = dispatch.corridor_rasters(res.ensembles, cfg)
    off = dispatch._offmap_table(res.offmap)
    speed = safety._corridor_speed_mps(cfg)
    by_id = {c.crew_id: c for c in crews}
    n = 0
    for cid, cp in sorted(plan.plans.items()):
        prev_loc, prev_dep = by_id[cid].loc(), res.t_s
        for st in cp.stops:
            loc = clients[st.client_id].loc
            tstar = min((safety.leg_tstar(
                prev_loc, loc, speed, ras, cfg,
                offraster_ros_mps=off.get(iid, (None, None))[0],
                offraster_margin_min=off.get(iid, (None, None))[1])
                for iid, ras in rasters), default=float("inf"))
            assert prev_dep <= tstar + _EPS, (cid, st.client_id, "ingress")
            assert st.service_start_s >= st.arrive_s - _EPS
            assert st.depart_s <= (st.latest_depart_s
                                   - cfg.slack_min * 60.0 + _EPS), \
                (cid, st.client_id, "slack")
            table = dispatch.egress_table(loc, rasters, zones, cfg, res.t_s,
                                          offmap=res.offmap or None)
            zone = {z.zone_id: z for z in zones}
            ways = [zid for zid, t in table if st.depart_s <= t + _EPS and (
                not cfg.plan_egress_time_deadline
                or st.depart_s + travel_time_s(loc, zone[zid].loc, cfg)
                <= st.latest_depart_s + _EPS)]
            assert ways, (cid, st.client_id, "egress")
            prev_loc, prev_dep = loc, st.depart_s
            n += 1
    return n


def _served(plan):
    if plan is None:
        return []
    return sorted((cid, st.client_id) for cid, cp in plan.plans.items()
                  for st in cp.stops)


def run_world(world, tmp_path, cfg, cycles=3):
    """[(res, crews, clients, zones)] for `cycles` cycles of a world."""
    clock = ss.SettableClock(T0)
    if world == "seeded":
        daemon, *_ = ss.make_stack(tmp_path, cfg=cfg, clock=clock,
                                   test_world=False)
        crews, clients, zones = [ss.crew()], {c.client_id: c for c in
                                              ss.clients()}, [ss.SAFE_ZONE]
    elif world == "offmap":
        daemon, *_ = osk.make_stack(tmp_path, cfg, clock=clock)
        crews = [ss.crew(crew_id="G1", loc=osk.CREW_LOC)]
        clients = {c.client_id: c for c in osk.clients()}
        zones = [osk.Z1, osk.Z2]
    else:
        daemon, _m, clients, crews = dense_stack(tmp_path, cfg, clock)
        zones = DENSE_ZONES
    out = []
    for k in range(cycles):
        clock.t = T0 + 60.0 + k * ss.PERIOD_S
        out.append((daemon.run_cycle(), crews, clients, zones))
    return out


def _base(world):
    return osk.fail_closed_cfg() if world == "offmap" else ss.small_cfg()


# ---------------------------------------------------------------------------
# the flag
# ---------------------------------------------------------------------------

def test_the_flag_arms_the_simulators_values_and_nothing_else():
    base = ResponseConfig()
    cfg = laws_cfg(base)
    changed = {f.name for f in dataclasses.fields(cfg)
               if getattr(cfg, f.name) != getattr(base, f.name)}
    assert changed == {"corridor_cell_exact", "dispatch_keep_frozen",
                       "plan_egress_time_deadline", "egress_dest_durable_h"}
    assert cfg.egress_dest_durable_h == simui.SIM_EGRESS_DEST_DURABLE_H == 8.0
    # 37 and 35 stay off; validate passes
    assert cfg.route_margin_egress_ramp == 0.0
    assert cfg.route_margin_egress_roads_min == 0.0
    assert cfg.ember_eta_landings is False
    assert validate_config(cfg) == []
    note = cli._crew_program_cfg(base, cli._build_parser().parse_args(
        cli._normalize_argv(["watch", "--aoi", "-121,38,-120,39",
                             "--crew-laws"])))[1][0]
    assert "route_margin_egress_ramp" in note and "road" in note
    assert "ember_eta_landings" in note and "SAFETY SEMANTICS 45e" in note
    assert {k for k, _w in cli.CREW_LAWS_ARMED} == changed


def test_37_changes_nothing_on_chords(tmp_path):
    """The ramp (with 31's road margin it needs) on the dense world: the
    live planner has no roads, so every plan is the flat-margin plan."""
    base = ss.small_cfg()
    ramp = dataclasses.replace(base, route_margin_egress_roads_min=45.0,
                               route_margin_egress_ramp=1.0 / 3.0)
    a = run_world("dense", tmp_path / "a", base, cycles=2)
    b = run_world("dense", tmp_path / "b", ramp, cycles=2)
    assert [ss._canon(r.plan) for r, *_ in a] == \
        [ss._canon(r.plan) for r, *_ in b]


# ---------------------------------------------------------------------------
# plans stay lawful; what changes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("world", ["seeded", "offmap", "dense"])
def test_plans_stay_lawful_with_the_laws_armed(world, tmp_path):
    on = laws_cfg(_base(world))
    stops = 0
    for res, crews, clients, zones in run_world(world, tmp_path / "on", on):
        stops += assert_plan_lawful(res, on, crews, clients, zones)
    if world != "offmap":
        assert stops > 0            # the law was exercised on a real plan


def test_the_seeded_and_offmap_worlds_plan_byte_for_byte_the_same(tmp_path):
    """Measured: on these two stacks the four laws change no plan (their
    stops leave hours before their deadlines; no chord clips a cell between
    samples; no second fire re-keys a committed stop)."""
    for world in ("seeded", "offmap"):
        base = _base(world)
        off = run_world(world, tmp_path / world / "off", base)
        on = run_world(world, tmp_path / world / "on", laws_cfg(base))
        assert [ss._canon(r.plan) for r, *_ in off] == \
            [ss._canon(r.plan) for r, *_ in on], world


def test_38_moves_the_way_out_off_a_dead_end_zone(tmp_path):
    """The dense world (8 m/s): Z3, downwind among the homes, is reached by
    its 10c line within 8 h. Without the laws G1's route ends there; with
    them (38) the planner sends it out to Z1 — the same homes served, every
    stop lawful, and the zone a crew waits in after its last stop is one
    the fire does not reach for 8 h."""
    base = ss.small_cfg()
    (off, *_), = run_world("dense", tmp_path / "off", base, cycles=1)
    (on, crews, clients, zones), = run_world("dense", tmp_path / "on",
                                             laws_cfg(base), cycles=1)
    assert _served(off.plan) == _served(on.plan)
    assert len(_served(on.plan)) == 7
    assert off.plan.plans["G1"].egress_zone_id == "Z3"
    assert on.plan.plans["G1"].egress_zone_id == "Z1"
    rasters = dispatch.corridor_rasters(on.ensembles, on_cfg := laws_cfg(
        base))
    z3_line = min(safety._raster_value_at(r, DENSE_ZONES[2].loc)
                  for _i, r in rasters) - on_cfg.egress_buffer_min * 60.0
    assert z3_line - on.t_s < on_cfg.egress_dest_durable_h * 3600.0
    assert assert_plan_lawful(on, on_cfg, crews, clients, zones) == 7
