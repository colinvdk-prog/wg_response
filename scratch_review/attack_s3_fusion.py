"""ADVERSARIAL VERIFIER (Session 3) — attack #3: fusion normalization (C3b).

Claims under attack (verifier brief #3):
  3a. Two DIFFERENT fires with the same name 40 km apart must NOT fuse
      (name join requires origin distance < 5 km).
  3b. The SAME fire whose centroid drifts (perimeter growth moves the
      representative point ~3 km between cycles) MUST keep ONE incident_id
      across cycles.
  3c. Id stability across source phase-in/out (firis-only -> +wfigs ->
      wfigs-only) and across per-cycle freshness shuffles.

FINDINGS / OBSERVATIONS documented here:
  OBS-2 (design-inherent, safety-net verified) The fused id is only stable
      across cycles when the members carry a stable key (IRWIN id, or a
      stable native id of the SAME most-preferred source). Two flips are
      reachable: (a) the one-time IRWIN upgrade when the first IRWIN-bearing
      member phases in (contract-mandated: "IRWIN id when known"), and
      (b) flip-flop when a MORE-preferred source without an IRWIN id phases
      in and out (rare live: WFIGS records essentially always carry IRWIN).
      Each flip re-alerts under the new id (re-notify-ward) and the alert
      layer suppresses the old pair's client-facing all-clear while the new
      pair is active — verified end-to-end below (no false all-clear, no
      lost alert).
  OBS-3 (minor) The 5 km name-join radius is measured origin-to-origin and
      groups are built greedily in input order, so (a) chains of same-named
      records can transitively bridge well beyond 5 km, and (b) grouping of
      a >5 km chain is input-order dependent. Realistic impact is low (needs
      3+ same-named no-IRWIN records in a line); documented with repros.

Run from repo root:
    py -3 -m pytest scratch_review/attack_s3_fusion.py -v
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scratch_review.attack_s3_common import (  # noqa: E402
    H, T0, ORIGIN, km_east, incident,
)
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    AlertState, IncidentSource, ResponseConfig, ThreatAssessment, ThreatEta,
)
from wgresponse.feeds import _norm_name, fuse_incidents  # noqa: E402
from wgresponse.notify import LogNotifier  # noqa: E402


def named(iid, name, source, km=0.0, irwin=None, updated=T0 - 600.0):
    return incident(iid=iid, name=name, source=source,
                    origin=km_east(ORIGIN, km), irwin_id=irwin,
                    updated_at_s=updated)


# ---------------------------------------------------------------------------
# 3a. same name, 40 km apart: two DIFFERENT fires
# ---------------------------------------------------------------------------

def test_same_name_40km_apart_must_not_fuse():
    a = named("wfigs:100", "Volcano Fire", IncidentSource.WFIGS, km=0.0)
    b = named("firis:200", "VOLCANO", IncidentSource.FIRIS, km=40.0)
    fused = fuse_incidents([[a], [b]])
    assert len(fused) == 2
    assert {i.incident_id for i in fused} == {"wfigs:100", "firis:200"}
    # and each keeps its own data (no cross-contamination)
    by_id = {i.incident_id: i for i in fused}
    assert by_id["wfigs:100"].origin == a.origin
    assert by_id["firis:200"].origin == b.origin


def test_name_join_distance_boundary_4p9_fuses_5p1_does_not():
    a = named("wfigs:100", "Volcano", IncidentSource.WFIGS, km=0.0)
    near = named("firis:200", "VOLCANO Fire", IncidentSource.FIRIS, km=4.9)
    far = named("firis:201", "VOLCANO Fire", IncidentSource.FIRIS, km=5.1)
    assert len(fuse_incidents([[a], [near]])) == 1
    assert len(fuse_incidents([[a], [far]])) == 2


def test_same_irwin_fuses_even_40km_apart_irwin_is_authoritative():
    a = named("wfigs:100", "Volcano", IncidentSource.WFIGS, km=0.0,
              irwin="{IRW-XYZ}")
    b = named("firis:200", "Totally Different Name", IncidentSource.FIRIS,
              km=40.0, irwin="irw-xyz")
    fused = fuse_incidents([[a], [b]])
    assert len(fused) == 1
    assert fused[0].incident_id == "irw-xyz"


def test_empty_and_suffix_only_names_never_name_join():
    assert _norm_name("Fire") == ""
    assert _norm_name("  ") == ""
    a = named("wfigs:1", "", IncidentSource.WFIGS, km=0.0)
    b = named("firis:2", "Fire", IncidentSource.FIRIS, km=0.5)
    c = named("calfire:3", "Incident", IncidentSource.CALFIRE, km=0.7)
    assert len(fuse_incidents([[a], [b], [c]])) == 3


# ---------------------------------------------------------------------------
# 3b. drifting centroid keeps one id
# ---------------------------------------------------------------------------

def _fuse_cycle(members):
    return {i.incident_id: i for i in fuse_incidents([[m] for m in members])}


def test_drifting_centroid_same_sources_keeps_one_id_across_cycles():
    """Perimeter growth moves both sources' representative points ~3 km per
    cycle (and they never sit at the same spot as each other). With stable
    per-source native ids the fused id must not move."""
    ids = []
    for k in range(4):                      # 4 cycles, centroid walks east
        w = named("wfigs:100", "Volcano", IncidentSource.WFIGS,
                  km=3.0 * k, updated=T0 + k * H)
        f = named("firis:200", "VOLCANO Fire", IncidentSource.FIRIS,
                  km=3.0 * k + 1.0, updated=T0 + k * H + 600.0)
        fused = _fuse_cycle([w, f])
        assert len(fused) == 1
        ids.append(next(iter(fused)))
    assert ids == ["wfigs:100"] * 4         # ONE id, all four cycles


def test_drifting_centroid_with_irwin_keeps_irwin_id():
    ids = []
    for k in range(4):
        w = named("irw-abc", "Volcano", IncidentSource.WFIGS, km=3.0 * k,
                  irwin="IRW-ABC", updated=T0 + k * H)
        f = named("firis:200", "Volcano", IncidentSource.FIRIS,
                  km=3.0 * k + 1.0, irwin="{irw-abc}", updated=T0 + k * H)
        fused = _fuse_cycle([w, f])
        assert len(fused) == 1
        ids.append(next(iter(fused)))
    assert ids == ["irw-abc"] * 4


def test_freshness_shuffle_never_flips_the_fused_id():
    """The Session-1 live bug class: which member is freshest alternates
    every cycle. The canonical id must not follow freshness."""
    for wfigs_fresher in (True, False, True, False):
        w = named("wfigs:100", "Volcano", IncidentSource.WFIGS, km=0.0,
                  updated=T0 - (600.0 if wfigs_fresher else 7200.0))
        f = named("firis:200", "Volcano", IncidentSource.FIRIS, km=1.0,
                  updated=T0 - (7200.0 if wfigs_fresher else 600.0))
        fused = _fuse_cycle([w, f])
        assert list(fused) == ["wfigs:100"]


def test_two_different_irwins_name_joined_id_still_stable_under_shuffle():
    """Two sources minted DIFFERENT IRWIN ids for one name-joined fire: the
    fused id must be the stable min, not the freshest member's irwin."""
    for wfigs_fresher in (True, False):
        w = named("irw-bbb", "Volcano", IncidentSource.WFIGS, km=0.0,
                  irwin="IRW-BBB",
                  updated=T0 - (600.0 if wfigs_fresher else 7200.0))
        f = named("irw-aaa", "Volcano", IncidentSource.FIRIS, km=1.0,
                  irwin="IRW-AAA",
                  updated=T0 - (7200.0 if wfigs_fresher else 600.0))
        fused = _fuse_cycle([w, f])
        assert list(fused) == ["irw-aaa"]   # lexicographic min, both cycles


# ---------------------------------------------------------------------------
# 3c. source phase-in/out
# ---------------------------------------------------------------------------

def test_phase_in_out_with_shared_irwin_is_fully_stable():
    """firis-only -> +wfigs -> wfigs-only, every member carrying the IRWIN
    id (the realistic live shape): ONE id across all three phases."""
    f = named("irw-abc", "Volcano", IncidentSource.FIRIS, km=0.0,
              irwin="IRW-ABC")
    w = named("irw-abc", "Volcano", IncidentSource.WFIGS, km=1.0,
              irwin="{IRW-ABC}")
    ids = [list(_fuse_cycle(members)) for members in ([f], [f, w], [w])]
    assert ids == [["irw-abc"], ["irw-abc"], ["irw-abc"]]


def test_OBS2_phase_in_of_first_irwin_bearing_member_flips_id_once():
    """OBS-2a: firis phase-1 has NO irwin -> id 'firis:200'. When wfigs
    phases in WITH an irwin, the fused id upgrades to the irwin (contract:
    'IRWIN id when known') — a one-time client-visible rename. Alert-layer
    safety net verified in test_id_flip_re_alerts_and_suppresses_all_clear."""
    f = named("firis:200", "Volcano", IncidentSource.FIRIS, km=0.0)
    w = named("irw-abc", "Volcano", IncidentSource.WFIGS, km=1.0,
              irwin="IRW-ABC")
    ids = [list(_fuse_cycle(members)) for members in ([f], [f, w], [w])]
    assert ids == [["firis:200"], ["irw-abc"], ["irw-abc"]]  # exactly one flip


def test_OBS2_no_irwin_phase_in_out_flip_flops_id():
    """OBS-2b: with NO irwin anywhere, a more-preferred source phasing in
    and back out flip-flops the id (firis:200 -> wfigs:100 -> firis:200).
    Rare live (WFIGS ~always carries IRWIN); each flip is re-notify-ward."""
    f = named("firis:200", "Volcano", IncidentSource.FIRIS, km=0.0)
    w = named("wfigs:100", "Volcano", IncidentSource.WFIGS, km=1.0)
    ids = [list(_fuse_cycle(members)) for members in ([f], [f, w], [f])]
    assert ids == [["firis:200"], ["wfigs:100"], ["firis:200"]]


def test_id_flip_re_alerts_and_suppresses_all_clear():
    """The alert-layer net under an id flip (OBS-2): the client must never
    read a false 'All clear' and must keep an active alert stream. Old pair
    walks to CLEARED through hysteresis but its client-facing send is
    SUPPRESSED while the new-id pair is active; the new pair alerts."""
    cfg = ResponseConfig()
    notifier = LogNotifier()
    engine = AlertEngine(cfg, notifier=notifier)

    def hot(iid, now_s):
        eta = ThreatEta(eta_p10_s=now_s + 6 * H, eta_p50_s=now_s + 10 * H,
                        eta_p90_s=now_s + 15 * H)
        return ThreatAssessment(client_id="c1", incident_id=iid, eta=eta,
                                computed_at_s=now_s)

    engine.evaluate([hot("firis:200", T0)], T0)
    assert engine.states()[("c1", "firis:200")].state \
        is AlertState.EXTREME_24H
    # id flips: assessments now arrive under the irwin id only.
    for k in range(1, 5):
        engine.evaluate([hot("irw-abc", T0 + k * H)], T0 + k * H,
                        feeds_healthy=True)
    assert engine.states()[("c1", "irw-abc")].state is AlertState.EXTREME_24H
    assert engine.states()[("c1", "firis:200")].state is AlertState.CLEARED
    sent_states = [(e.incident_id, e.state) for e, _ in notifier.sent]
    assert ("irw-abc", AlertState.EXTREME_24H) in sent_states  # re-alerted
    assert all(s is not AlertState.CLEARED for _, s in sent_states), \
        "false all-clear reached the client during an id flip"


# ---------------------------------------------------------------------------
# OBS-3: transitive name-join chaining + order dependence (probes)
# ---------------------------------------------------------------------------

def test_OBS3_name_chain_bridges_beyond_5km():
    """Three same-named no-irwin records at 0/4/8 km: the greedy grouper
    joins C via B (4 km) even though C is 8 km from A — the group spans
    beyond the 5 km radius. Documents the transitive bridge."""
    a = named("wfigs:1", "Volcano", IncidentSource.WFIGS, km=0.0)
    b = named("firis:2", "Volcano", IncidentSource.FIRIS, km=4.0)
    c = named("calfire:3", "Volcano", IncidentSource.CALFIRE, km=8.0)
    fused = fuse_incidents([[a], [b], [c]])
    assert len(fused) == 1                  # one bridged group


def test_OBS3_chain_grouping_is_input_order_dependent():
    """Same three records, two arrival orders -> different group counts
    (greedy first-match). Deterministic per ordering, but the id set an
    operator sees depends on feed order for >5 km chains."""
    a = named("wfigs:1", "Volcano", IncidentSource.WFIGS, km=0.0)
    b = named("firis:2", "Volcano", IncidentSource.FIRIS, km=4.0)
    c = named("calfire:3", "Volcano", IncidentSource.CALFIRE, km=8.0)
    n_abc = len(fuse_incidents([[a], [b], [c]]))
    n_acb = len(fuse_incidents([[a], [c], [b]]))
    assert n_abc == 1                       # b bridges c into a's group
    assert n_acb == 2                       # c grouped alone before b joins a
    # 40 km NEVER bridges without intermediates, regardless of order:
    far = named("firis:9", "Volcano", IncidentSource.FIRIS, km=40.0)
    assert len(fuse_incidents([[a], [far]])) == 2
    assert len(fuse_incidents([[far], [a]])) == 2


def test_OBS3_big_fire_origin_vs_centroid_split_beyond_5km():
    """A large fire's perimeter centroid can sit >5 km from a point-feed
    discovery origin. Without a shared IRWIN (CAL FIRE never has one) the
    two records split into two incidents -> duplicate alert streams for the
    same physical fire. With WFIGS present (point origin ~= CAL FIRE
    origin), chaining re-bridges the group — the realistic mitigation."""
    firis_centroid = named("firis:2", "Volcano", IncidentSource.FIRIS, km=6.0)
    calfire_origin = named("calfire:3", "Volcano", IncidentSource.CALFIRE,
                           km=0.0)
    assert len(fuse_incidents([[firis_centroid], [calfire_origin]])) == 2
    # WFIGS present at the discovery point: CAL FIRE joins wfigs (0.3 km) so
    # the client sees no calfire:* duplicate — but the FIRIS perimeter record
    # (centroid 6 km out, >5 km from both origins) STILL splits off: the
    # duplicate stream survives unless FIRIS shares an IRWIN id.
    wfigs_origin = named("wfigs:1", "Volcano", IncidentSource.WFIGS, km=0.3)
    for order in ([[wfigs_origin], [firis_centroid], [calfire_origin]],
                  [[wfigs_origin], [calfire_origin], [firis_centroid]]):
        ids = sorted(i.incident_id for i in fuse_incidents(order))
        assert ids == ["firis:2", "wfigs:1"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
