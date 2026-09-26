"""tests/test_simui_policy.py — live safety-policy changes vs in-flight work.

Promoted from the 2026-07-09/10 verifier attacks (scratch_review/
attack_sim_slider.py B3 "frozen-stop rug-pull" — the BLOCKING finding — and
attack_sim_robust.py R3's session-level speed probe):

  * A stop that is IN PROGRESS when the safety slider tightens is NEVER
    silently grandfathered: set_buffer/set_slack re-check its projected
    departure against the NEW rule and WITHDRAW the crew when it would
    violate (deploy aborted unfinished, no ServicedStop, immediate egress,
    loud state()['withdrawn'] record + scoreboard count + note).
  * A kept in-flight stop is marked policy_superseded, audited under the
    NEW (tightened) rule, and re-checked every tick against the newest p10
    ('eta_drift') so forecast updates can never let it complete past the
    rule.
  * COMPLETED stops keep their adoption-time rule (history is not
    retroactively re-labeled) and every audit row now carries
    rule_buffer_min/rule_ember_min so the grandfather is visible, never
    silent.
  * SimSession.set_speed is bounded to the design-doc clock ceiling
    (0, 600x] — same law as the server /api/sim config path.

All on controlled synthetic checkerboard tiles (tile-agent-independent
geometry, tests/test_simui.py discipline); deterministic via contracts law.

RUG-PULL CONSTRUCTION (rebuilt 2026-07-10 for the honest-field engine): the
persisted/anchored arrival fields mean a from-scratch dispatch NEVER arms a
mid-deploy stop anywhere near the 120-min slider ceiling (measured p10 gaps
are 400-900+ min under the corridor law), so the old "arm a <=100-min gap,
then raise the buffer" script is structurally unconstructible. The demo now
uses live dynamics instead: (1) arm ANY mid-deploy stop via normal service;
(2) tighten the WORLD mid-deploy — one atomic ignite() plants a spot fire a
deterministic short distance upwind of the deploying client AND swings the
domain wind onto it (documented DOMAIN-SHARED weather semantics: every
fire's field re-anchors under the new script next tick), collapsing the
fresh p10 at that client to a KNOWN tight-but-safe gap (inside the 10-min
rule, outside 120); (3) set_buffer(120) must withdraw the in-flight stop on
the spot. _inject_spot_fire is the phase-2 helper; pinned numbers carry
their derivation runs inline.
"""
from __future__ import annotations

import json
import math

import pytest

from wgresponse import geo, spread
from wgresponse.contracts import LatLon
from wgresponse.simui import LOCATIONS, MAX_SPEED_MULT, SimSession
from wgresponse.terrain import SyntheticTerrain, save_ascii_tile

# The verifier's rug-pull scenario config: fast engine + tight starting rule
# (buffer 10 / slack 0) — the adoption-time rule the live tightening raises
# to 120 (a from-scratch stop is never near the wire on honest fields; the
# tight gap is created mid-deploy by _inject_spot_fire, module docstring).
FAST_TIGHT = {"ensemble_n": 3, "grid_cell_m": 500.0, "horizon_h": 24.0,
              "slack_min": 0.0, "egress_buffer_min": 10.0}
KEY = "boulder_co"


def _checkerboard_dir(tmp_path, key=KEY, size_km=20.0, cell_m=500.0,
                      hamlet_half=3):
    """The suite's porous-hamlet geometry (see tests/test_simui.py): a
    checkerboard of developed cells inside uniform grass lets the head fire
    run through the interface, so near-deadline service margins exist."""
    loc = LOCATIONS[key]
    bbox = geo.bbox_around([loc.center], pad_m=size_km * 500.0)
    tile = SyntheticTerrain(seed=f"simui-test|{key}", kind="grass").tile(
        bbox, cell_m)
    spec = tile.spec
    r0 = spec.nrows // 2 - hamlet_half
    c0 = spec.ncols // 2 - hamlet_half
    for r in range(r0, r0 + 2 * hamlet_half):
        for c in range(c0, c0 + 2 * hamlet_half):
            if (r + c) % 2 == 0:
                tile.fuel.set(r, c, 91.0)
    d = tmp_path / "tiles"
    d.mkdir(exist_ok=True)
    save_ascii_tile(tile, str(d / f"{key}.wgt"))
    return d


def _session(tmp_path, seed="m1", n_clients=16, n_crews=1,
             intensity="high", ign_dist_m=8_000.0):
    d = _checkerboard_dir(tmp_path)
    # roads_module=None: these are kinematics-pinned regression demos — they
    # must stay straight-leg even after wgresponse.roads + data/roads/ land
    # (road-following legs are covered by tests/test_simui.py's fake-roads
    # suite; the road departure guard is inert in straight-leg sessions).
    # intensity/ign_dist_m are parameterized so the rug-pull tests can pin
    # their own geometry (4.6 km) without disturbing the other demos (8 km).
    # These are kinematics-pinned withdrawal-SAFETY regression demos whose exact
    # scenario constants (victim id, arm tick, p10 gaps, served counts, deficit
    # minutes) were derived under the legacy USD dispatch objective. The founder
    # TRIAGE law flipped cfg.dispatch_objective's default to 'homes' (count-
    # first), which reorders the single crew's itinerary and drifts every pinned
    # constant. The withdrawal law under test is objective-independent, so these
    # demos are pinned to the objective they were derived under; re-deriving them
    # for 'homes' belongs with the simui re-plan work.
    s = SimSession(KEY, n_clients, n_crews, seed=seed,
                   cfg_overrides={**FAST_TIGHT, "dispatch_objective": "value"},
                   tile_dir=d, roads_module=None)
    loc = LOCATIONS[KEY]
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg,
                          float(ign_dist_m))
    s.ignite(ign.lat, ign.lon, intensity=intensity)
    return s


def _p10(state):
    return {c["client_id"]: c["eta_p10_s"] for c in state["clients"]}


def _arm_mid_deploy(s, max_ticks=60, min_tick=0, gap_max_min=math.inf,
                    gap_min_min=None):
    """Tick until a crew is MID-DEPLOY on a stop whose (current p10 -
    planned depart) gap fits the window; return (state, crew, stop, gap_min).
    The default window is wide open (honest-field engine: from-scratch gaps
    are 400-900+ min, so the rug-pull tests arm ANY stop and tighten the
    world afterwards via _inject_spot_fire); callers needing a band pass
    their own gap_min_min/gap_max_min. Fails LOUDLY if the scenario stops
    arming — this controlled geometry is a pinned regression demo, not a
    probe."""
    for _ in range(max_ticks):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, st["audit"]
        if st["tick"] < min_tick:
            continue
        p10 = _p10(st)
        for c in st["crews"]:
            if c["status"] != "deploying" or not c["itinerary"]:
                continue
            cur = c["itinerary"][0]
            v = p10.get(cur["client_id"])
            if v is None:
                continue
            gap_min = (v - cur["planned_depart_s"]) / 60.0
            if gap_min <= gap_max_min and (gap_min_min is None
                                           or gap_min >= gap_min_min):
                return st, c, cur, gap_min
    raise AssertionError(
        "rug-pull scenario failed to arm a mid-deploy stop — the pinned "
        "checkerboard demo geometry regressed (report this)")


#: Phase-2 spot-fire distance for the rug-pull demo (see _inject_spot_fire
#: and the derivation runs pinned on the tests below).
SPOT_FIRE_D_M = 1250.0


def _inject_spot_fire(s, st, stop, dist_m=SPOT_FIRE_D_M, step_m=250.0):
    """PHASE-2 WORLD TIGHTENER for the rug-pull demo: one atomic ignite()
    plants a second ignition dist_m dead-upwind of the DEPLOYING client and
    swings the domain wind straight from the stop's own fire onto that
    client (ignite's documented DOMAIN-SHARED weather semantics — the
    newest ignite's wind override rebuilds the one weather script every
    fire forecasts under, so BOTH fires' fields re-anchor on the next
    tick). Client-level dedupe keeps the one in-flight job; the threat
    monitor rolls up the worst-case ETA across the fires. Deterministic:
    derived purely from state + fixed constants. Non-burnable cells (the
    hamlet checkerboard) are skipped by stepping farther upwind — a
    rejected ignite() raises BEFORE mutating any session state (fail-loud
    ignition law), so probing is replay-safe. Returns the new incident id.
    """
    victim = stop["client_id"]
    cl = next(c for c in st["clients"] if c["client_id"] == victim)
    vloc = LatLon(cl["lat"], cl["lon"])
    f1 = next(f for f in st["fires"]
              if f["incident_id"] == stop["incident_id"])
    # Wind FROM the reciprocal of fire->client: both heads run at the client.
    w_new = (geo.bearing_deg(LatLon(f1["lat"], f1["lon"]), vloc)
             + 180.0) % 360.0
    d = float(dist_m)
    for _ in range(60):
        p = geo.destination(vloc, w_new, d)
        try:
            return s.ignite(p.lat, p.lon, intensity="high",
                            wind_dir_deg=w_new)
        except ValueError:               # non-burnable cell: step upwind
            d += step_m
    raise AssertionError(
        "no burnable cell upwind of the deploying client — the pinned "
        "checkerboard demo geometry regressed (report this)")


# ---------------------------------------------------------------------------
# THE BLOCKING FINDING: frozen mid-deploy stop vs a live tightening
# ---------------------------------------------------------------------------

def test_frozen_stop_withdrawn_when_buffer_tightens_midflight(tmp_path):
    """Raise the buffer 10 -> 120 while a crew is mid-deploy on a stop whose
    projected departure violates the new rule: the deploy must be WITHDRAWN
    on the spot (no gel completion, immediate egress, loud record) — never
    silently kept under the superseded rule.

    Live construction (module docstring; derivation run 2026-07-10, seed m1,
    16 clients / 1 crew / high fire 4.6 km upwind): normal service arms the
    first deploy at tick 2 (victim sim-c12, planned depart = deploy end =
    t0+2749.8 s, from-scratch gap 431.8 min); the phase-2 spot fire (1250 m
    dead-upwind of the victim, domain wind swung onto it at 255.2 deg)
    collapses the next tick's p10 gap to 43.3 min (rollup; the stop's own
    fire: 77.6 min) — inside the 10-min rule, outside 120 — so
    set_buffer(120) withdraws at tick 3 with deficit 42.4 min while
    completing under the old rule stays legal.
    """
    s = _session(tmp_path, ign_dist_m=4_600.0)
    # phase 1: arm ANY mid-deploy stop via normal service (wide-open window)
    st, crew, cur, gap_min = _arm_mid_deploy(s)
    victim = cur["client_id"]
    assert gap_min > 120.0, "from-scratch gap unexpectedly tight — re-pin"

    # phase 2: tighten the WORLD mid-deploy; verify on the NEXT tick the
    # fresh worst-case gap is tight-but-safe (10-min rule OK, 120 violated)
    _inject_spot_fire(s, st, cur)
    st2 = s.tick()
    assert st2["scoreboard"]["violations"] == 0, st2["audit"]
    crew_mid = next(c for c in st2["crews"]
                    if c["crew_id"] == crew["crew_id"])
    assert crew_mid["status"] == "deploying" and crew_mid["itinerary"] and \
        crew_mid["itinerary"][0]["client_id"] == victim, (
            "deploy ended before the rug-pull could arm — re-pin scenario")
    gap2_min = (_p10(st2)[victim] - cur["planned_depart_s"]) / 60.0
    assert 20.0 < gap2_min < 110.0, (
        f"phase-2 gap {gap2_min:.1f} min left the tight-but-safe window "
        f"(derivation: 43.3) — re-pin scenario")

    # phase 3: the tightening — the in-flight stop must withdraw NOW
    s.set_buffer(120.0)
    now = s.state()

    # 1. the frozen stop is gone from the crew, which is egressing NOW
    crew_after = next(c for c in now["crews"]
                      if c["crew_id"] == crew["crew_id"])
    assert not crew_after["itinerary"], "frozen stop silently kept"
    assert crew_after["status"] == "egressing"

    # 2. loud, machine-readable withdrawal record
    assert now["scoreboard"]["withdrawn"] == 1
    (w,) = now["withdrawn"]
    assert w["client_id"] == victim
    assert w["crew_id"] == crew["crew_id"]
    assert w["cause"] == "policy_change"
    assert w["rule_buffer_min"] == 120.0
    # deficit in (0, 110): violates the 120-min rule but would have been
    # legal under the adoption-time 10-min rule (derivation: 42.4 min) —
    # the withdrawal is genuinely the slider's doing, not a doomed stop.
    assert 0.0 < w["deficit_min"] < 110.0
    assert w["projected_depart_s"] > w["latest_depart_s"]
    assert any("SAFETY WITHDRAWAL" in n and victim in n for n in now["notes"])
    assert any(u["client_id"] == victim and u["reason"] == "withdrawn_unsafe"
               for u in now["unserved"])

    # 3. no ServicedStop ever appears for the victim; audit stays clean
    for _ in range(60 - now["tick"]):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, st["audit"]
        assert victim not in {sv["client_id"] for sv in st["serviced"]}, (
            "withdrawn stop completed anyway")
    json.dumps(st, sort_keys=True, allow_nan=False)


def test_live_tightening_reduces_served_vs_control(tmp_path):
    """The slider visibly moves the headline number in the LIVE-change case:
    a control twin with the IDENTICAL seed + command script minus the
    set_buffer completes the in-flight stop and serves strictly more — the
    honest served-count demonstration (SIMULATOR_DESIGN.md 'Slider
    observability').

    Derivation run 2026-07-10 (same construction as
    test_frozen_stop_withdrawn_when_buffer_tightens_midflight): by tick 60
    the control run serves 9 with withdrawn=0; the tightened run withdraws
    the mid-deploy victim at tick 3 (deficit 42.4 min vs buffer 120) and
    serves 0 with withdrawn=1; violations 0 in both.

    (Re-pinned 2026-07-10 for FOUNDER ROUND 4 law 6: the simulator's 200 m
    ember-threat radius now gives fire-adjacent developed homes finite ETAs,
    so the control run gels 6 more of them — served 3 -> 9. LAW is unchanged:
    the tightened twin still withdraws its mid-deploy victim and serves
    strictly fewer; the withdrawn/served-0 pins hold.)"""
    def run(tighten):
        s = _session(tmp_path, ign_dist_m=4_600.0)
        st, crew, cur, _gap = _arm_mid_deploy(s)
        _inject_spot_fire(s, st, cur)          # identical world change
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, st["audit"]
        if tighten:
            s.set_buffer(120.0)                # the ONLY differing command
        while st["tick"] < 60:
            st = s.tick()
            assert st["scoreboard"]["violations"] == 0, st["audit"]
        return st

    st_a = run(tighten=False)
    st_b = run(tighten=True)

    assert st_a["scoreboard"]["served"] >= 1, "control run vacuous"
    assert st_a["scoreboard"]["withdrawn"] == 0
    assert st_b["scoreboard"]["withdrawn"] >= 1
    assert st_b["scoreboard"]["served"] < st_a["scoreboard"]["served"], (
        "live tightening should cost the withdrawn service: "
        f"control={st_a['scoreboard']}, pulled={st_b['scoreboard']}")
    # exact pins from the derivation run (loud re-pin markers, not law):
    # served 3 -> 9 re-pinned for FOUNDER ROUND 4 law 6 (the 200 m ember-threat
    # radius gels 6 more fire-adjacent developed homes); the law assertions
    # above are what actually matter and are unchanged.
    # Tightened twin served 0 -> 1 re-pinned 2026-09-26 for the progress-
    # preserving re-anchor (tests/test_sim_reanchor_progress.py): the spot-fire
    # injection swings the shared wind, so fire 1 re-anchors on that tick and
    # now keeps its front's progress (P10 0.1-0.4 h earlier at the homes it
    # threatens: c1 4.79 -> 4.68 h, c9 3.61 -> 3.51 h, c16 19.86 -> 19.44 h);
    # the tick-6 re-plan for the idle crew then adopts sim-c16 (was: no stop),
    # served lawfully under the 120-min rule (margin 15.3 h; violations 0 on
    # every tick). Same withdrawal, same victim; the control still serves 9.
    assert st_a["scoreboard"]["served"] == 9
    assert st_b["scoreboard"]["served"] == 1
    assert st_b["scoreboard"]["withdrawn"] == 1


def test_kept_stop_flagged_and_audited_under_new_rule(tmp_path):
    """A tightening the in-flight stop still fits KEEPS it — but marked
    policy_superseded and audited under the NEW rule (rule echoed on the
    audit row), so nothing about the keep is silent."""
    s = _session(tmp_path)
    st, crew, cur, gap_min = _arm_mid_deploy(s, gap_max_min=math.inf,
                                             gap_min_min=60.0)
    victim = cur["client_id"]
    new_buf = gap_min - 40.0            # projected departure still fits
    assert new_buf > FAST_TIGHT["egress_buffer_min"]
    s.set_buffer(new_buf)

    now = s.state()
    crew_after = next(c for c in now["crews"]
                      if c["crew_id"] == crew["crew_id"])
    assert crew_after["itinerary"], "keepable stop was withdrawn"
    assert crew_after["itinerary"][0]["client_id"] == victim
    assert crew_after["itinerary"][0]["policy_superseded"] is True
    assert now["scoreboard"]["withdrawn"] == 0

    for _ in range(30):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, st["audit"]
        done = [a for a in st["audit"] if a["client_id"] == victim]
        if done:
            break
    assert done, "kept stop never completed (fire receded?) — re-pin scenario"
    row = done[0]
    assert row["violation"] is False
    assert row["rule_buffer_min"] == pytest.approx(new_buf), (
        "kept in-flight stop must be audited under the NEW tightened rule")
    # the superseded mark dies with the deploy
    for c in st["crews"]:
        for it in c["itinerary"]:
            assert it["client_id"] != victim


def test_eta_drift_withdraws_superseded_stop(tmp_path):
    """A kept (policy-superseded) stop is re-checked EVERY tick: swing the
    wind straight at the client so the next forecast jumps p10 closer — the
    crew must withdraw (cause 'eta_drift') instead of finishing into a
    violation of the tightened rule."""
    s = _session(tmp_path)
    st, crew, cur, gap_min = _arm_mid_deploy(s, min_tick=8,
                                             gap_max_min=math.inf,
                                             gap_min_min=200.0)
    victim = cur["client_id"]
    s.set_buffer(gap_min - 1.0)          # keep with 1 minute of headroom
    now = s.state()
    crew_after = next(c for c in now["crews"]
                      if c["crew_id"] == crew["crew_id"])
    assert crew_after["itinerary"] and \
        crew_after["itinerary"][0]["policy_superseded"] is True
    # turn the wind to blow FROM the reciprocal bearing -> straight at the
    # client (public command; no revoke, no recheck until the next tick)
    cl = next(c for c in now["clients"] if c["client_id"] == victim)
    brg = geo.bearing_deg(s._fires["sim-f1"].origin,
                          LatLon(cl["lat"], cl["lon"]))
    s.set_wind((brg + 180.0) % 360.0)

    withdrawn = None
    for _ in range(6):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, st["audit"]
        hits = [w for w in st["withdrawn"] if w["client_id"] == victim]
        if hits:
            withdrawn = hits[0]
            break
        if any(sv["client_id"] == victim for sv in st["serviced"]):
            pytest.fail("superseded stop completed although the drifted "
                        "forecast should have withdrawn it")
    assert withdrawn is not None, "drift re-check never fired"
    assert withdrawn["cause"] == "eta_drift"
    assert withdrawn["deficit_min"] > 0.0
    assert victim not in {sv["client_id"] for sv in st["serviced"]}


def test_completed_history_not_retro_relabeled(tmp_path):
    """Stops fully COMPLETED before the slider moves keep violation=False
    under their adoption-time rule — and that rule is VISIBLE on the audit
    row (rule_buffer_min), never a silent mismatch with the scoreboard."""
    s = _session(tmp_path)
    st = None
    for _ in range(30):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        if st["scoreboard"]["served"] >= 1:
            break
    assert st["scoreboard"]["served"] >= 1, "nothing serviced — vacuous"
    pre = {a["client_id"] for a in st["audit"]}

    s.set_buffer(120.0)
    for _ in range(4):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, (
            f"retroactive re-label after a buffer change: {st['audit']}")
        assert st["scoreboard"]["buffer_min"] == 120.0
        for a in st["audit"]:
            if a["client_id"] in pre:
                assert a["violation"] is False
                assert a["rule_buffer_min"] == 10.0     # adoption-time rule
                assert a["rule_ember_min"] == 0.0


def test_withdrawal_script_replays_byte_identically(tmp_path):
    """Determinism law holds through the new withdraw path: the same (seed,
    command script) — including the phase-2 spot-fire injection and the
    mid-flight tightening that withdraws — replays to identical state
    snapshots. Script pinned from the derivation run of
    test_frozen_stop_withdrawn_when_buffer_tightens_midflight: the demo
    arms its first deploy at tick 2; injection, one re-anchor tick, then
    set_buffer(120) withdraws (deficit 42.4 min)."""
    import hashlib

    def h(st):
        return hashlib.sha256(json.dumps(
            st, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def run():
        s = _session(tmp_path, ign_dist_m=4_600.0)
        hashes = []
        st = None
        for _ in range(2):               # the demo arms at tick 2 (pinned)
            st = s.tick()
            hashes.append(h(st))
        crew = st["crews"][0]
        assert crew["status"] == "deploying" and crew["itinerary"], (
            "pinned arm tick drifted — re-derive this script")
        _inject_spot_fire(s, st, crew["itinerary"][0])
        hashes.append(h(s.tick()))       # fields re-anchor under new script
        s.set_buffer(120.0)              # the rug-pull command
        hashes.append(h(s.state()))
        for _ in range(5):
            hashes.append(h(s.tick()))
        return hashes, s.state()["scoreboard"]["withdrawn"]

    (ha, wa), (hb, wb) = run(), run()
    assert ha == hb
    assert wa == wb == 1, "script must actually exercise the withdraw path"


# ---------------------------------------------------------------------------
# WITHDRAWAL law under the REAL 'homes' default (FOUNDER ROUND 3 #4 flipped
# cfg.dispatch_objective's default to 'homes'; the value-mode demos above are
# pinned to the objective they were derived under). The safety-withdrawal law
# is objective-independent — this proves it still fires in homes mode via the
# same second-ignition p10-collapse construction, numbers RE-DERIVED.
# ---------------------------------------------------------------------------

def test_homes_mode_withdrawal_still_fires(tmp_path):
    """Raise the buffer mid-deploy under the count-first 'homes' objective (the
    real default, not the value-mode pin the other demos use): the in-flight
    stop must WITHDRAW exactly as in value mode — no silent grandfather.

    Same construction as test_frozen_stop_withdrawn_when_buffer_tightens_
    midflight (module docstring): normal homes-mode service arms a mid-deploy
    stop; the phase-2 spot fire swings the domain wind onto the deploying
    client so its OWN incident's p10 collapses; the tightening then violates
    the new rule. Homes reorders the single crew's itinerary vs value mode, so
    the victim / arm tick / gap all differ (derivation run 2026-07-10, seed m1:
    homes arms sim-c1 at tick 2, own-incident p10 gap ~153 min after the spot
    fire — vs value mode's sim-c12 @ 77.6 min). The buffer is SELF-CALIBRATED a
    fixed margin above the measured collapsed gap, so the withdrawal fires with
    a known deficit regardless of which home homes-mode happens to deploy."""
    d = _checkerboard_dir(tmp_path)
    # homes is the DEFAULT — do NOT override dispatch_objective (the value
    # demos' _session pins it to 'value'); everything else matches FAST_TIGHT.
    # CORRIDOR pinned to LEGACY (calibrated-stack sim-default flip
    # 2026-07-30): this test's arm/rug-pull choreography was derived under
    # the uniform corridor field; its subject is the WITHDRAWAL LAW, not
    # corridor defaults, so it pins the old field rather than re-deriving.
    s = SimSession(KEY, 16, 1, seed="m1",
                   cfg_overrides=dict(FAST_TIGHT,
                                      corridor_erosion_tiered=False,
                                      escape_hysteresis_margin_min=0.0,
                                      transit_cold_black_by_class=False),
                   tile_dir=d, roads_module=None)
    assert s._cfg.dispatch_objective == "homes", "test must run the real default"
    loc = LOCATIONS[KEY]
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 4_600.0)
    s.ignite(ign.lat, ign.lon, intensity="high")

    # phase 1: arm ANY mid-deploy stop via normal homes-mode service
    st, crew, cur, _gap = _arm_mid_deploy(s)
    victim = cur["client_id"]

    # phase 2: collapse the victim's OWN-incident p10 with the spot fire, then
    # confirm it is still the in-flight stop on the next (re-anchor) tick
    _inject_spot_fire(s, st, cur)
    st2 = s.tick()
    # no serviced stop departed past its rule; the 10c backstop (counting
    # IMMINENT exposure in every mode since 2026-09-03, review A8) may
    # already report the still-deploying victim crew inside the collapsed
    # p10 — that is the rug-pull this test engineers, one tick early
    assert all(a["violation"] is False for a in st2["audit"]), st2["audit"]
    assert st2["scoreboard"]["violations"] == len(st2["crew_overruns"])
    crew_mid = next(c for c in st2["crews"] if c["crew_id"] == crew["crew_id"])
    assert (crew_mid["status"] == "deploying" and crew_mid["itinerary"]
            and crew_mid["itinerary"][0]["client_id"] == victim), (
        "deploy ended before the rug-pull could arm — re-pin scenario")

    # measure the stop's OWN-incident p10 gap (the exact quantity
    # _recheck_deploy keys off — NOT the cross-fire rollup) against the crew's
    # projected departure (eta_s == the deploy end == _deploy_end_s), then set
    # the buffer a fixed margin above it: the new rule is violated by ~margin.
    proj_depart_s = crew_mid["eta_s"]
    fire = s._fires[cur["incident_id"]]
    own_p10_s = spread.eta_at(fire.ens, s._client_by_id[victim].loc).eta_p10_s
    own_gap_min = (own_p10_s - proj_depart_s) / 60.0
    assert math.isfinite(own_gap_min) and own_gap_min > 0.0, (
        f"victim off a live finite deadline (gap {own_gap_min}) — re-pin")
    margin_min = 30.0
    s.set_buffer(own_gap_min + margin_min)          # THE tightening

    now = s.state()
    # 1. the frozen stop is gone; the crew is egressing NOW (not grandfathered)
    crew_after = next(c for c in now["crews"] if c["crew_id"] == crew["crew_id"])
    assert not crew_after["itinerary"], "frozen stop silently kept in homes mode"
    assert crew_after["status"] == "egressing"

    # 2. loud, machine-readable withdrawal — SAME vocabulary as value mode
    assert now["scoreboard"]["withdrawn"] == 1
    (w,) = now["withdrawn"]
    assert w["client_id"] == victim and w["crew_id"] == crew["crew_id"]
    assert w["cause"] == "policy_change"
    assert w["projected_depart_s"] > w["latest_depart_s"]
    # self-calibrated: deficit ~ margin (+ ember 0), robust to planned-vs-actual
    # departure jitter and independent of the (re-derived) collapsed gap
    assert w["deficit_min"] == pytest.approx(margin_min, abs=2.0), w
    assert any("SAFETY WITHDRAWAL" in n and victim in n for n in now["notes"])
    assert any(u["client_id"] == victim and u["reason"] == "withdrawn_unsafe"
               for u in now["unserved"])

    # 3. no ServicedStop ever appears for the victim; audit stays clean
    for _ in range(60 - now["tick"]):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, st["audit"]
        assert victim not in {sv["client_id"] for sv in st["serviced"]}, (
            "withdrawn stop completed anyway under homes mode")
    json.dumps(st, sort_keys=True, allow_nan=False)


# ---------------------------------------------------------------------------
# set_speed ceiling (design-doc clock 1x-600x, enforced at the seam)
# ---------------------------------------------------------------------------

def test_set_speed_bounded_to_design_ceiling(tmp_path):
    s = _session(tmp_path, n_clients=4)
    period = s._cfg.replan_period_s
    t0 = s.state()["sim_now_s"]
    s.set_speed(MAX_SPEED_MULT)          # the ceiling itself is legal
    st = s.tick()
    assert st["sim_now_s"] == t0 + MAX_SPEED_MULT * period
    assert st["speed"] == 600.0
    for bad in (600.0001, 1e9, 0.0, -3.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            s.set_speed(bad)
    # rejected commands never reach the replay journal
    speeds = [c["mult"] for c in s.state()["cmd_log"] if c["cmd"] == "set_speed"]
    assert speeds == [600.0]


def test_speed_600x_coarse_jumps_audit_still_clean(tmp_path):
    """Promoted from attack_sim_robust R3 (session path, synthetic tile):
    even 600x coarse multi-cycle jumps run the per-tick SAFETY AUDIT and
    stay at 0 violations.

    SPLIT 2026-09-26 (founder): with the progress-preserving re-anchor the
    fire no longer freezes at each 150 h rebase, and big jumps are sub-
    stepped (simui.SIM_MAX_SUBSTEP_S). Every original assertion holds, as
    strictly as before, over the first TWO jumps (300 h) — before the fire
    has burned out this small tile. From ~400 h the whole tile, safe zone
    included, is burned and the strict audit counts crews standing on
    days-old black; that burned-out stretch is pinned on its own terms in
    tests/test_sim_coarse_substep.py::test_burned_out_map_crews_only_on_old_black."""
    s = _session(tmp_path, n_clients=8, n_crews=2)
    st = s.tick()                        # one fine tick: adopt plans
    assert st["scoreboard"]["violations"] == 0
    s.set_speed(600.0)
    for _ in range(2):
        st = s.tick()
        # The subject here is the serviced-stop DEPARTURE audit under coarse
        # jumps: no stop may depart past its rule. Since 2026-09-03 the 10c
        # crew-overrun backstop counts IMMINENT exposure in every crew mode
        # (tri-model review A8), and at 150 h per tick it catches an
        # EGRESSING crew standing in a cell whose eroded-P10 passed days ago
        # while the nominal front never arrives — a real exposure row this
        # test used to hide, not a departure violation. The scoreboard folds
        # the two; assert each on its own terms.
        for a in st["audit"]:
            assert a["violation"] is False, a
        assert st["scoreboard"]["violations"] == len(st["crew_overruns"]), \
            (st["audit"], st["crew_overruns"])
        for o in st["crew_overruns"]:
            assert o["reason"] == "imminent" and o["mode"] != "idle", o
    assert st["sim_now_s"] - st["t0_s"] >= 2 * 600.0 * 900.0
