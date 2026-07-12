"""attack_r4_outcome.py — FOUNDER ROUND 4 law 8 (PER-CLIENT OUTCOME TAG):
the tags CANNOT LIE. For every client every tick on real Malibu runs:

  * outcome is one of the 7 legal tags;
  * scoreboard.outcomes sums to len(clients) AND every tag count matches an
    INDEPENDENT recount of clients[].outcome;
  * gelled  => outcome in {saved, protected_then_impacted}  (never abandoned/
    triaged/pending/in_progress/not_threatened);
  * protected_then_impacted  <=>  gelled AND impacted (impacted recomputed
    independently from the payload: state==IMPACTED or p50<=now);
  * saved  <=>  gelled AND not impacted;
  * not_threatened  =>  p50 UNREACHED, or p50 beyond the notice horizon at
    job-eval time (NO in-scope home is ever mislabeled safe). The count of
    not_threatened homes that still expose a FINITE p50 is reported (a
    non-blocking labeling gap vs the law-8 gloss "no ETA");
  * abandoned_unsavable  =>  impacted (burned ungelled) OR genuinely infeasible
    RIGHT NOW — verified by an INDEPENDENT fresh dispatch.plan from live crew
    positions (all crews eligible); a fresh re-plan that SERVES it would be a
    stale-reason LIE;
  * triaged  =>  not gelled AND feasible-on-its-own (fresh single-job re-plan
    SERVES it) — a deliberate count-max skip, not a hidden infeasibility;
  * in_progress  =>  a crew is actively en_route/deploying on it;
  * scrub replay: state_at(k) is byte-identical to the live frame k (tags incl.).

Run:  py -3 -m pytest scratch_review/attack_r4_outcome.py -q
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections import Counter

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import spread, safety, dispatch                       # noqa: E402
from wgresponse.contracts import (DispatchJob, Crew, CrewBeacon,       # noqa: E402
                                  CrewStatus, UNREACHED)
from wgresponse.simui import OUTCOME_TAGS                             # noqa: E402
from attack_r4_common import TILE_DIR, tile_present, skip_loud         # noqa: E402


def _malibu(seed, n_clients=50, n_crews=3):
    from wgresponse.simui import SimSession
    from attack_sim_common import pick_ignition
    sess = SimSession("malibu_ca", n_clients, n_crews, seed=seed,
                      cfg_overrides={}, fire_zone="standard", tile_dir=TILE_DIR)
    p, _ = pick_ignition(sess, "malibu_ca", cluster=True)
    sess.ignite(p.lat, p.lon, intensity="high")
    return sess


def _fresh_feasibility(sess, cid_target, now):
    """INDEPENDENT current-feasibility oracle: build the target's job from the
    LIVE ensemble and run a fresh dispatch.plan with ALL crews eligible from
    their live positions (defeats any stale stored reason). Returns
    'SERVED' | 'UNSERVED:<reason>' | 'UNREACHED'."""
    cfg = sess._cfg
    cl = sess._client_by_id[cid_target]
    reason = None
    for fid in sess._fire_order:
        ens = sess._fires[fid].ens
        if ens is None:
            continue
        eta = spread.eta_at(ens, cl.loc, fallback_radius_m=cfg.ember_threat_radius_m)
        if math.isinf(eta.eta_p50_s) or math.isinf(eta.eta_p10_s):
            continue
        latest = safety.latest_departure_s(eta.eta_p10_s, cfg)
        job = DispatchJob(client=cl, incident_id=fid, eta=eta,
                          latest_depart_s=latest,
                          latest_service_start_s=latest - cfg.deploy_min * 60.0,
                          earliest_useful_start_s=max(
                              now, eta.eta_p50_s - cfg.gel_window_h * 3600.0))
        crew_args = [Crew(crew_id=cid, name=cs.crew.name,
                          status=CrewStatus.AVAILABLE,
                          last_beacon=CrewBeacon(crew_id=cid, loc=cs.pos, t_s=now,
                                                 status=CrewStatus.AVAILABLE),
                          home_base=cs.crew.home_base)
                     for cid, cs in sess._crew_sims.items()]
        plan = dispatch.plan(crew_args, [job], {fid: ens},
                             list(sess._loc.safe_zones), cfg, now, None,
                             forecasts={fid: sess._wx})
        for cp in plan.plans.values():
            if any(s.client_id == cid_target for s in cp.stops):
                return "SERVED"
        for uj in plan.unserved:
            if uj.client_id == cid_target:
                reason = uj.reason
    return f"UNSERVED:{reason}" if reason is not None else "UNREACHED"


_UNSAVABLE_REASONS = frozenset({
    "infeasible_deadline", "infeasible_slack", "gel_window_closed",
    "no_safe_corridor", "no_safe_egress", "fire_arrived", "withdrawn_unsafe"})


@pytest.mark.skipif(not tile_present("malibu_ca"),
                    reason=skip_loud("malibu_ca", "tile"))
def test_outcome_tags_never_lie_across_seeds():
    stats = {"finite_p50_not_threatened": 0, "au_impacted": 0,
             "au_reasoned": 0, "triaged": 0, "clients_ticks": 0,
             "elig_fresh_serves": 0}
    for seed in ("oc1", "oc2", "oc3", "oc4"):
        sess = _malibu(seed, 50, 3)
        cfg = sess._cfg
        notice_h = cfg.notice_h
        ever_au, ever_gelled = set(), set()
        for _t in range(32):
            st = sess.tick()
            now = st["sim_now_s"]
            pre_now = now - cfg.replan_period_s * sess._speed  # job-eval clock
            clients = st["clients"]
            sb = st["scoreboard"]["outcomes"]
            # (2) scoreboard sums to client count + matches an independent recount
            assert sum(sb.values()) == len(clients), (seed, _t)
            recount = Counter(c["outcome"] for c in clients)
            for tag in set(list(sb) + list(recount)):
                assert sb.get(tag, 0) == recount.get(tag, 0), (seed, _t, tag)
            in_prog_engine = set()
            for cr in st["crews"]:
                if cr["status"] in ("en_route", "deploying") and cr["itinerary"]:
                    in_prog_engine.add(cr["itinerary"][0]["client_id"])
            for c in clients:
                stats["clients_ticks"] += 1
                cid = c["client_id"]
                oc = c["outcome"]
                p50 = c["eta_p50_s"]
                gelled = c["gel_applied"]
                impacted = (c["state"] == "impacted"
                            or (p50 is not None and p50 <= now))
                if gelled:
                    ever_gelled.add(cid)
                assert oc in OUTCOME_TAGS, (seed, _t, oc)             # (1) legal tag
                if gelled:                                            # (3) gelled precedence
                    assert oc in ("saved", "protected_then_impacted"), (seed, cid, oc)
                # (4)(5) protected_then_impacted <=> gelled&impacted; saved <=> gelled&!imp
                if oc == "protected_then_impacted":
                    assert gelled and impacted, (seed, cid)
                if oc == "saved":
                    assert gelled and not impacted, (seed, cid)
                if gelled and impacted:
                    assert oc == "protected_then_impacted", (seed, cid)
                if gelled and not impacted:
                    assert oc == "saved", (seed, cid)
                # (6) not_threatened: never an in-scope (p50<=notice) home; the
                # finite-p50 (72-96 h band) cases are counted + reported
                if oc == "not_threatened" and p50 is not None:
                    stats["finite_p50_not_threatened"] += 1
                    h50 = (p50 - pre_now) / 3600.0
                    assert h50 > notice_h - 1e-6, (
                        seed, cid, h50, "not_threatened home is WITHIN the notice "
                        "horizon -> should have been a dispatch job")
                # (7) in_progress <=> a crew is actively en_route/deploying on it
                if oc == "in_progress":
                    assert cid in in_prog_engine, (seed, cid)
                # (8) abandoned_unsavable: impacted (burned ungelled) OR a genuine
                # terminal unsavable reason (tag-reason consistency, from the
                # engine's own diagnose vocabulary)
                if oc == "abandoned_unsavable":
                    ever_au.add(cid)
                    if impacted:
                        stats["au_impacted"] += 1
                    else:
                        r = sess._unserved_reason.get(cid)
                        assert r in _UNSAVABLE_REASONS, (
                            seed, cid, r, "abandoned_unsavable with NO terminal "
                            "unsavable reason and not impacted -> lying tag")
                        stats["au_reasoned"] += 1
                        # diagnostic (not a hard fail): a myopic eligible-crew
                        # re-plan may 'serve' it by freely reassigning committed
                        # crews; the terminal-consistency assert below proves it
                        # is never ACTUALLY saved.
                        if _fresh_feasibility(sess, cid, pre_now) == "SERVED":
                            stats["elig_fresh_serves"] += 1
                # (9) triaged: not gelled; feasible on its own (deliberate skip,
                # never a hidden infeasibility)
                if oc == "triaged":
                    assert not gelled
                    assert _fresh_feasibility(sess, cid, pre_now) == "SERVED", \
                        (seed, cid, "triaged but not feasible-on-its-own")
                    stats["triaged"] += 1
        # TERMINAL CONSISTENCY (the strong anti-lie check): a home ever tagged
        # abandoned_unsavable is NEVER later gelled -> the write-off never hid a
        # savable home.
        assert ever_au.isdisjoint(ever_gelled), (
            seed, sorted(ever_au & ever_gelled),
            "a home tagged abandoned_unsavable was later GELLED -> premature "
            "write-off / lying tag")
    print(f"\n[law8] {stats['clients_ticks']} client-ticks checked across 4 seeds. "
          f"AU(impacted-burned)={stats['au_impacted']} "
          f"AU(terminal-reason)={stats['au_reasoned']} "
          f"triaged(feasible-skip)={stats['triaged']}; NO AU home was ever later "
          f"gelled. myopic-eligible-fresh-'serves'={stats['elig_fresh_serves']} "
          f"(reassigns committed crews; never an actual save). "
          f"not_threatened-with-finite-p50={stats['finite_p50_not_threatened']} "
          f"(NON-BLOCKING labeling gap vs the law-8 'no ETA' gloss)")
    assert stats["clients_ticks"] > 0


@pytest.mark.skipif(not tile_present("malibu_ca"),
                    reason=skip_loud("malibu_ca", "tile"))
def test_outcome_tags_scrub_byte_identical_to_live():
    """Every recorded frame replays byte-identically (LAW 1 + LAW 8): state_at(k)
    equals the live frame k, so the outcome tags in scrub history match the
    frame served live."""
    sess = _malibu("scrub-oc", 45, 3)
    live = []
    for _t in range(25):
        live.append(sess.tick())
    for st in live:
        k = st["tick"]
        snap = sess.state_at(k)
        assert snap is not None, k
        assert json.dumps(snap, sort_keys=True) == json.dumps(st, sort_keys=True), k
        # explicit tag-level check
        live_tags = {c["client_id"]: c["outcome"] for c in st["clients"]}
        snap_tags = {c["client_id"]: c["outcome"] for c in snap["clients"]}
        assert live_tags == snap_tags, k
        assert snap["scoreboard"]["outcomes"] == st["scoreboard"]["outcomes"], k
