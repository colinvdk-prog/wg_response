"""PASS-2 independent inventory of DEFAULT-OFF knobs that intercept
corridor / egress / audit / ladder paths.

A knob is DEFAULT-OFF if the ResponseConfig default is False, 0, 0.0, or
(for route_alternatives) 1 = legacy-fastest-only.

Writes scratch_review/rv2sci_knobs.json.
"""
from __future__ import annotations
import dataclasses
import json
import os
import sys

sys.path.insert(0, r"C:\Users\colin\wg_response")
from wgresponse.contracts import ResponseConfig

OUT = r"C:\Users\colin\wg_response\scratch_review\rv2sci_knobs.json"

# Hand-audited intercept table. `on_weakens_law` is the pass-2 question.
KNOBS = [
    dict(
        knob="corridor_erosion_euclidean", default=False,
        intercept="corridor (safety.py CorridorOracle / geo.erode_min disc vs Chebyshev square)",
        on_weakens_law=True,
        polarity="RELAXES. erode_min is a MIN filter: the honest disc is smaller than the legacy square's diagonal (1.53x stated radius). Contracts comment: 'the honest disc RELAXES the law'. Prior hunt #1.",
        paths=["corridor"],
    ),
    dict(
        knob="route_alternatives", default=1,
        intercept="egress/corridor routing (roads.route_alternatives, simui._pick_route, dispatch egress ordering)",
        on_weakens_law=True,
        polarity="CAN WEAKEN. >1 enables candidate routes; measured +3 caught crews (max-latest-depart chooses long drives across a moving front). Prior hunt #2. REJECTED as built.",
        paths=["corridor", "egress"],
    ),
    dict(
        knob="corridor_erosion_tiered", default=False,
        intercept="corridor (spread.tiered_eroded_p10; simui._eroded_p10 / _plan_eroded_rasters). Always euclidean discs. Radii FAST=550 / SH=200 / SLOW=150 vs uniform 500.",
        on_weakens_law=True,
        polarity="RELAXES spatial buffer for SH (200 vs 500) and TU/TL/SB (150 vs 500). Also forces disc erosion (same relaxation as corridor_erosion_euclidean) even when that flag is off. FAST 550 is slightly tighter for grass. CANDIDATE FOURTH.",
        paths=["corridor"],
    ),
    dict(
        knob="transit_cold_black_by_class", default=False,
        intercept="SEM-10 ROUTE SELECTION passability (simui._cold_black_pass_s). 10c audit never consults this.",
        on_weakens_law=True,
        polarity="RELAXES grass/GS windows (45 -> 5-20 min) so a recently-burned grass cell becomes a legal transit route sooner. Tightens timber (45 -> 60-90). Mixed; grass direction is a law-weakening. CANDIDATE FOURTH.",
        paths=["egress"],
    ),
    dict(
        knob="escape_hysteresis_margin_min", default=0.0,
        intercept="egress target adoption (simui._escape_switch_ok)",
        on_weakens_law=True,
        polarity="CAN WEAKEN. ON holds a fleeing crew on the adopted target unless the proposal improves routed destination margin by >N min. A strictly-better (but sub-threshold) target is refused. Can keep a crew on a worse ray. Measured 0.00pp homes.",
        paths=["egress"],
    ),
    dict(
        knob="escape_proactive_lead_min", default=0.0,
        intercept="idle-crew pre-pocket escape (simui SEM-10e; decamp before imminent flag)",
        on_weakens_law=False,
        polarity="TIGHTENS only: earlier escape, and only along a route clean at arrival. No-qualifying-exit => unchanged. Claude B7.",
        paths=["egress"],
    ),
    dict(
        knob="ember_buffer_min", default=0.0,
        intercept="P10 deadline (safety.latest_depart_s = p10 - (egress_buffer+ember_buffer)*60); also simui crew-exposure buffer",
        on_weakens_law=False,
        polarity="TIGHTENS: extra minutes on top of P10.",
        paths=["corridor", "egress", "audit"],
    ),
    dict(
        knob="containment_gate_enabled", default=False,
        intercept="alert ladder (alerts.py; SAFETY SEMANTICS 8). Caps NEW escalations at WATCH for highly-contained non-growing incidents.",
        on_weakens_law=True,
        polarity="WEAKENS the ladder: NONE/CLEARED escalate at most to NOTICE then WATCH; WATCH holds. Does not downgrade existing EXTREME. Never touches crew math. Default OFF pending founder approval. CANDIDATE FOURTH (ladder path).",
        paths=["ladder"],
    ),
    dict(
        knob="extreme_regime_enabled", default=False,
        intercept="alert ladder stage thresholds (P10 instead of P50 when cured-grass+wind detector fires)",
        on_weakens_law=False,
        polarity="TIGHTENS alerts (P10). Crew math already on P10, untouched.",
        paths=["ladder"],
    ),
    dict(
        knob="ember_threat_radius_m", default=0.0,
        intercept="ladder + dispatch threat ETAs for developed-cell homes (spread.eta_at adjacency fallback)",
        on_weakens_law=False,
        polarity="TIGHTENS/enables alerting of NB homes. Flag-OFF is the live gap (A13): majority of RSF developed cells never alert. ON cannot weaken a law.",
        paths=["ladder"],
    ),
    dict(
        knob="spotting_enabled", default=False,
        intercept="spread (Sardoy teleport edges feed P10)",
        on_weakens_law=False,
        polarity="TIGHTENS P10 (embers ahead of front). Conservative.",
        paths=["corridor", "egress", "ladder"],
    ),
    dict(
        knob="grass_ros_floor_frac", default=0.0,
        intercept="spread head ROS floor (spread._pack_params); live watcher arms via --grass-floor",
        on_weakens_law=False,
        polarity="Makes fire FASTER (coverage-conservative, area-inflating). Does not relax a safety law; it overrides extinction (A3 science defect).",
        paths=["corridor", "egress", "ladder"],
    ),
    dict(
        knob="wind_gust_blend", default=0.0,
        intercept="spread effective 10-m wind",
        on_weakens_law=False,
        polarity="Makes fire FASTER. Conservative.",
        paths=["corridor", "egress", "ladder"],
    ),
    dict(
        knob="dispatch_plan_on_roads", default=False,
        intercept="corridor T* + travel along road polylines (dispatch.py). Road legs then use route_margin_roads_min=30 vs chord route_margin_min=56.",
        on_weakens_law="mixed",
        polarity="Honesty of geometry (chord-vs-road was the overrun tail). NUMERICALLY the road-leg margin drops 56->30 min. Documented as removing double-counted travel error, not as a law relaxation. Score as mixed, not a silent carve-out.",
        paths=["corridor", "egress"],
    ),
    dict(
        knob="dispatch_hold_h", default=0.0,
        intercept="coating deferral (homes). Crew SEM-10 still governs idle/preposition.",
        on_weakens_law=False,
        polarity="Does not relax crew-safety. Hold releases if a probe cannot cover every coat-now home.",
        paths=["ladder"],  # weakly: defers protection of homes
    ),
    dict(
        knob="crew_shift_max_on_h", default=0.0,
        intercept="fatigue rest. Resting crews stay under SEM-10.",
        on_weakens_law=False,
        polarity="Cannot weaken a safety law; reduces dispatchable fleet.",
        paths=["egress"],
    ),
    dict(
        knob="gel_env_model_enabled", default=False,
        intercept="gel coating life (~6h vs 72h placeholder)",
        on_weakens_law=False,
        polarity="Shorter gel life => earlier re-coat demand. Tightens home protection, not crew law. Not corridor/egress/audit.",
        paths=["ladder"],
    ),
    dict(
        knob="audit idle-filter (NOT a knob)", default="hardcoded in simui.py:3629",
        intercept="10c audit (simui._crew_overruns): imminent branch requires _mode=='idle'",
        on_weakens_law=True,
        polarity="HARDCODED CARVE-OUT. Contract: any crew burned OR imminent. Implementation: burned OR (imminent AND idle). Non-idle imminent ticks never enter _crew_overruns. Prior hunt #3. No ResponseConfig flag.",
        paths=["audit"],
        is_knob=False,
    ),
]


def verify_defaults():
    cfg = ResponseConfig()
    checks = []
    mapping = {
        "corridor_erosion_euclidean": False,
        "route_alternatives": 1,
        "corridor_erosion_tiered": False,
        "transit_cold_black_by_class": False,
        "escape_hysteresis_margin_min": 0.0,
        "escape_proactive_lead_min": 0.0,
        "ember_buffer_min": 0.0,
        "containment_gate_enabled": False,
        "extreme_regime_enabled": False,
        "ember_threat_radius_m": 0.0,
        "spotting_enabled": False,
        "grass_ros_floor_frac": 0.0,
        "wind_gust_blend": 0.0,
        "dispatch_plan_on_roads": False,
        "dispatch_hold_h": 0.0,
        "crew_shift_max_on_h": 0.0,
        "gel_env_model_enabled": False,
    }
    for k, expect in mapping.items():
        got = getattr(cfg, k)
        checks.append(dict(knob=k, expected_default=expect, actual=got,
                           ok=(got == expect)))
    return checks


def sim_auto_on():
    """Knobs the simulator silently forces ON when the override key is absent."""
    return [
        dict(knob="corridor_erosion_tiered", sim_value=True,
             site="simui.py ~1533"),
        dict(knob="escape_hysteresis_margin_min", sim_value=20.0,
             site="simui.py ~1535 SIM_ESCAPE_HYSTERESIS_MIN"),
        dict(knob="transit_cold_black_by_class", sim_value=True,
             site="simui.py ~1541"),
        dict(knob="escape_proactive_lead_min", sim_value="SIM_ESCAPE_PROACTIVE_LEAD_MIN (120)",
             site="simui.py ~1549"),
        dict(knob="ember_threat_radius_m", sim_value="~200",
             site="simui.py ~1521"),
        dict(knob="spotting_enabled", sim_value=True,
             site="sim server / simui session defaults"),
        dict(knob="grass_ros_floor_frac", sim_value=0.10,
             site="sim grass_realism toggle"),
        dict(knob="dispatch_plan_on_roads", sim_value=True,
             site="sim server per session"),
        dict(knob="dispatch_hold_h", sim_value=24.0,
             site="sim server"),
    ]


def main():
    checks = verify_defaults()
    assert all(c["ok"] for c in checks), checks
    weaken = [k for k in KNOBS if k.get("on_weakens_law") is True]
    fourth = [k for k in weaken if k["knob"] not in (
        "corridor_erosion_euclidean", "route_alternatives",
        "audit idle-filter (NOT a knob)")]
    out = dict(
        default_checks=checks,
        inventory=KNOBS,
        weaken_on_ON=weaken,
        prior_hunt=["corridor_erosion_euclidean", "route_alternatives",
                    "audit idle-filter (hardcoded)"],
        additional_weakeners_the_fourth_and_beyond=fourth,
        sim_auto_enables=sim_auto_on(),
        fail_open_not_a_knob=dict(
            site="safety.py CorridorOracle: samples with v is None or inf are skipped; T* stays +inf if nothing constrains",
            polarity="FAIL OPEN on missing/short raster (Claude A14). Not a knob.",
        ),
    )
    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1)
    print("wrote", OUT)
    print(f"defaults verified: {sum(c['ok'] for c in checks)}/{len(checks)}")
    print("WEAKEN-ON-ON:")
    for k in weaken:
        print(f"  - {k['knob']}: {k['paths']} | {k['polarity'][:80]}")
    print("FOURTH+ candidates:")
    for k in fourth:
        print(f"  - {k['knob']}")


if __name__ == "__main__":
    main()
