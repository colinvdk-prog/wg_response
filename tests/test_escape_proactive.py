"""SEM-10e PROACTIVE PRE-POCKET ESCAPE (contracts escape_proactive_lead_min;
moderate-wind census attribution 2026-08-02).

The measured defect (scratch_review/census_modviol_trace_current_paradise_ca
_8000_c8.json + sem10_idle_trace.py): the sweep's imminent flag fires at
eroded-P10 within egress_buffer + one tick (~45 min), but the lawful roads
out of the Paradise SW pocket burned over 2-3 ticks BEFORE that flag — four
idle crews then least-bad hopped into zone z1 and sat available/idle in the
flaming front for 11 audit ticks (45-55). SEM-10e lets an idle crew inside a
LONGER proactive horizon decamp while a durable clean exit still exists;
when none does, behavior is byte-identical to the flag being off.

Scripting convention: the single test crew stages round-robin at z1, so
scripting z1's P10 IS scripting the crew's cell (zone-cell match wins in the
exposure fake)."""
from __future__ import annotations

from wgresponse import simui
from wgresponse.contracts import ResponseConfig


UNREACHED = float("inf")


def _session(**cfg):
    return simui.SimSession("boulder_co", 2, 1, seed="pro-test",
                            cfg_overrides=cfg)


def _crew(s):
    return next(iter(s._crew_sims.values()))


def _zones(s):
    return sorted(s._loc.safe_zones, key=lambda z: z.zone_id)


def _script_exposure(s, zone_p10):
    """Monkeypatch _crew_exposure: each zone's cell gets
    (inf, zone_p10[zone_id]); every other point is safe (inf, inf). The crew
    sits at z1, so zone_p10[z1] is the crew's own cell."""
    zones = {(round(z.loc.lat, 6), round(z.loc.lon, 6)): z.zone_id
             for z in s._loc.safe_zones}

    def fake(pos):
        zid = zones.get((round(pos.lat, 6), round(pos.lon, 6)))
        if zid is not None:
            return UNREACHED, zone_p10.get(zid, UNREACHED)
        return UNREACHED, UNREACHED
    s._crew_exposure = fake


def _script_routes(s, table):
    """Monkeypatch _route_metrics keyed on rounded target latlon ->
    (burned, exposure, worst, total, dest_margin)."""
    def fake(pos, target, now, speed, lead_s):
        return table[(round(target.lat, 4), round(target.lon, 4))]
    s._route_metrics = fake


def _key(z):
    return (round(z.loc.lat, 4), round(z.loc.lon, 4))


# ------------------------------------------------------------- defaults


def test_engine_default_stays_off():
    assert ResponseConfig().escape_proactive_lead_min == 0.0


def test_sim_default_flips_on():
    s = _session()
    assert s._cfg.escape_proactive_lead_min == 120.0


def test_sim_default_explicit_override_wins():
    s = _session(escape_proactive_lead_min=0.0)
    assert s._cfg.escape_proactive_lead_min == 0.0


# ------------------------------------------------------------- picker


def test_picker_none_when_no_durable_zone():
    """Every zone inside the proactive horizon -> None (no treadmill hop).
    The zone-only picker: with contracts escape_road_network armed (the
    simulator default since 2026-09-26) a fully lawful, durable ROAD node
    is also a candidate when no zone rule yields a move — this scripted
    world makes every non-zone point safe forever, so that picker decamps by
    road (pinned in tests/test_sim_escape_roads.py)."""
    s = _session(escape_road_network=False)
    cs = _crew(s)
    now = s._now_s
    _script_exposure(s, {z.zone_id: now + 1800.0 for z in _zones(s)})
    assert s._proactive_escape_zone(cs.pos, now, cs.speed, 900.0,
                                    7200.0) is None


def test_picker_prefers_fully_lawful_forever_zone():
    """A forever (inf-P10) zone with a fully-lawful route wins outright."""
    s = _session()
    cs = _crew(s)
    now = s._now_s
    z1, z2, z3 = _zones(s)
    _script_exposure(s, {z1.zone_id: now + 600.0,       # crew cell, doomed
                         z2.zone_id: UNREACHED,         # forever
                         z3.zone_id: UNREACHED})        # forever, route burned
    _script_routes(s, {_key(z1): (False, 0.0, 0.0, 0.0, -100.0),
                       _key(z2): (False, 0.0, 10.0, 600.0, UNREACHED),
                       _key(z3): (True, 0.0, -900.0, 600.0, UNREACHED)})
    pick = s._proactive_escape_zone(cs.pos, now, cs.speed, 900.0, 7200.0)
    assert pick is not None and pick.zone_id == z2.zone_id


def test_picker_falls_back_to_clean_at_arrival_route():
    """No fully-lawful route (worst < 0 through the pessimistic band) but a
    not-burned route to a forever zone with lawful destination -> taken.
    This is exactly the paradise t13-27 regime: the z2 crossing was P10-
    exposed but pre-nominal for 15 ticks after the last fully-lawful tick."""
    s = _session()
    cs = _crew(s)
    now = s._now_s
    z1, z2, z3 = _zones(s)
    _script_exposure(s, {z1.zone_id: now + 600.0,
                         z2.zone_id: UNREACHED,
                         z3.zone_id: now + 600.0})
    _script_routes(s, {_key(z1): (False, 0.0, 0.0, 0.0, -100.0),
                       _key(z2): (False, 500.0, -900.0, 1800.0, UNREACHED),
                       _key(z3): (False, 0.0, 10.0, 60.0, UNREACHED)})
    pick = s._proactive_escape_zone(cs.pos, now, cs.speed, 900.0, 7200.0)
    assert pick is not None and pick.zone_id == z2.zone_id


def test_picker_fallback_rejects_nominal_crossing():
    """The clean-at-arrival fallback must NEVER route over a nominal-passed
    cell (however cold the black) — the 10c audit flags a crew standing on
    any front-passed cell, so a proactive move may not buy overrun ticks.
    The 6000 m sibling measured exactly this: 5 crews flagged on one tick
    mid-egress crossing 4-h-old burn the route law legally allows."""
    s = _session()
    cs = _crew(s)
    now = s._now_s
    z1, z2, z3 = _zones(s)
    zones = {(round(z.loc.lat, 6), round(z.loc.lon, 6)): z.zone_id
             for z in s._loc.safe_zones}

    def fake_expo(pos):
        zid = zones.get((round(pos.lat, 6), round(pos.lon, 6)))
        if zid == z2.zone_id:
            return UNREACHED, UNREACHED
        if zid in (z1.zone_id, z3.zone_id):
            return UNREACHED, now + 600.0
        # every intermediate sample: nominal passed HOURS ago (cold black)
        return now - 4 * 3600.0, now - 4 * 3600.0
    s._crew_exposure = fake_expo
    _script_routes(s, {_key(z1): (False, 0.0, 0.0, 0.0, -100.0),
                       _key(z2): (False, 0.0, -900.0, 1800.0, UNREACHED),
                       _key(z3): (False, 0.0, -900.0, 60.0, -100.0)})
    assert s._proactive_escape_zone(cs.pos, now, cs.speed, 900.0,
                                    7200.0) is None


# ------------------------------------------------------------- sweep


def _prime(s, zone_p10, routes):
    """Scripted world; the crew stages at z1 and starts idle."""
    cs = _crew(s)
    _script_exposure(s, zone_p10)
    _script_routes(s, routes)
    return cs


def test_sweep_decamps_idle_crew_inside_horizon():
    s = _session(escape_proactive_lead_min=120.0)
    z1, z2, z3 = _zones(s)
    # crew cell (z1) P10 in 2h: outside the imminent window (45 min), inside
    # the proactive horizon 30m buffer + 15m lead + 120m = 2.75h -> triggers
    cs = _prime(s, {z1.zone_id: s._now_s + 2 * 3600.0,
                    z2.zone_id: UNREACHED},
                {_key(z1): (False, 0.0, 0.0, 0.0, -100.0),
                 _key(z2): (False, 0.0, 10.0, 600.0, UNREACHED),
                 _key(z3): (True, 0.0, -900.0, 600.0, UNREACHED)})
    assert cs._mode == "idle"
    s._crew_safety_sweep(lead_s=900.0)
    assert cs._mode == "egress"
    assert cs.status.value == "egressing"
    assert cs.egress_zone_id == z2.zone_id
    cid = cs.crew.crew_id
    assert s._relocating.get(cid) == "proactive_escape"
    assert s._n_relocations == 1
    assert any("proactive escape" in n for n in s._notes)


def test_sweep_holds_when_no_durable_exit():
    """No durable zone -> the crew stays put (never a least-bad early hop);
    behavior identical to the knob being off until the imminent law fires.
    Zone-only picker (see test_picker_none_when_no_durable_zone): the road-
    network counterpart is pinned in tests/test_sim_escape_roads.py."""
    s = _session(escape_proactive_lead_min=120.0, escape_road_network=False)
    z1, z2, z3 = _zones(s)
    cs = _prime(s, {z1.zone_id: s._now_s + 2 * 3600.0,
                    z2.zone_id: s._now_s + 1800.0,
                    z3.zone_id: s._now_s + 1800.0},
                {_key(z1): (False, 0.0, 10.0, 60.0, UNREACHED),
                 _key(z2): (False, 0.0, 10.0, 60.0, UNREACHED),
                 _key(z3): (False, 0.0, 10.0, 60.0, UNREACHED)})
    s._crew_safety_sweep(lead_s=900.0)
    assert cs._mode == "idle"
    assert s._n_relocations == 0


def test_sweep_outside_horizon_no_action():
    s = _session(escape_proactive_lead_min=120.0)
    z1, z2, z3 = _zones(s)
    # crew cell P10 in 4h > 2.75h horizon -> no trigger
    cs = _prime(s, {z1.zone_id: s._now_s + 4 * 3600.0,
                    z2.zone_id: UNREACHED},
                {_key(z1): (False, 0.0, 0.0, 0.0, -100.0),
                 _key(z2): (False, 0.0, 10.0, 600.0, UNREACHED),
                 _key(z3): (True, 0.0, -900.0, 600.0, UNREACHED)})
    s._crew_safety_sweep(lead_s=900.0)
    assert cs._mode == "idle"
    assert s._n_relocations == 0


def test_knob_off_is_inert():
    """escape_proactive_lead_min=0 -> the branch never runs even with a
    perfect durable exit and a threatened cell outside the imminent window
    (byte-identity: the only new code path is behind pro_s > 0)."""
    s = _session(escape_proactive_lead_min=0.0)
    z1, z2, z3 = _zones(s)
    cs = _prime(s, {z1.zone_id: s._now_s + 2 * 3600.0,
                    z2.zone_id: UNREACHED},
                {_key(z1): (False, 0.0, 0.0, 0.0, -100.0),
                 _key(z2): (False, 0.0, 10.0, 600.0, UNREACHED),
                 _key(z3): (True, 0.0, -900.0, 600.0, UNREACHED)})
    n_notes = len(s._notes)
    s._crew_safety_sweep(lead_s=900.0)
    assert cs._mode == "idle"
    assert s._n_relocations == 0
    assert len(s._notes) == n_notes


def test_sweep_never_touches_nonidle_modes():
    """A deploying/travelling/egressing crew is owned by the existing laws —
    SEM-10e is idle-only (the founder bug class: idle crews in the path)."""
    s = _session(escape_proactive_lead_min=120.0)
    z1, z2, z3 = _zones(s)
    cs = _prime(s, {z1.zone_id: s._now_s + 2 * 3600.0,
                    z2.zone_id: UNREACHED},
                {_key(z1): (False, 0.0, 0.0, 0.0, -100.0),
                 _key(z2): (False, 0.0, 10.0, 600.0, UNREACHED),
                 _key(z3): (True, 0.0, -900.0, 600.0, UNREACHED)})
    cs._mode = "travel"
    cs.itinerary = [{"client_id": "c1", "incident_id": "i1",
                     "loc": z3.loc}]
    s._crew_safety_sweep(lead_s=900.0)
    assert cs._mode == "travel"
    assert s._relocating.get(cs.crew.crew_id) != "proactive_escape"


def test_proactive_crew_telemetry_reason():
    """state() surfaces relocating=True + relocate_reason=proactive_escape
    while the crew flees (the console renders the chip from these)."""
    s = _session(escape_proactive_lead_min=120.0)
    z1, z2, z3 = _zones(s)
    _prime(s, {z1.zone_id: s._now_s + 2 * 3600.0,
               z2.zone_id: UNREACHED},
           {_key(z1): (False, 0.0, 0.0, 0.0, -100.0),
            _key(z2): (False, 0.0, 10.0, 600.0, UNREACHED),
            _key(z3): (True, 0.0, -900.0, 600.0, UNREACHED)})
    s._crew_safety_sweep(lead_s=900.0)
    st = s.state()
    row = next(c for c in st["crews"] if c["relocating"])
    assert row["relocate_reason"] == "proactive_escape"
    assert row["status"] == "egressing"


def test_already_at_durable_zone_no_churn():
    """A crew already sitting on a forever zone is outside the horizon by
    construction (its cell P10 is inf) — no trigger, no phantom leg."""
    s = _session(escape_proactive_lead_min=120.0)
    z1, z2, z3 = _zones(s)
    cs = _crew(s)
    cs.pos = z2.loc
    _script_exposure(s, {z2.zone_id: UNREACHED})
    s._crew_safety_sweep(lead_s=900.0)
    assert cs._mode == "idle"
    assert s._n_relocations == 0
