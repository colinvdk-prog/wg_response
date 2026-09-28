"""The before/after registry: every DOCUMENTED hindcast calibration number
the stacked-rings fix can move, once, with where it is cited.

Values come from before_after.json (before_after.py), the re-run review
scripts' own JSON outputs in the mirrors (build_mirrors.py +
run_review_scripts.py), and a few statistics recomputed here with the
review scripts' formulas (named per entry). Citations are from the doc
inventory of 2026-09-27 (doc:line at commit 52d8b63).

Writes registry.json and registry.md next to this script.
"""
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
from tools import hindcast_lib as H  # noqa: E402
import before_after as BAm  # noqa: E402

MIRROR = os.path.join(HERE, "results", "mirror")
STATES = ("documented", "before", "after")
FIELDS = set(H.PairMetrics.__dataclass_fields__)


def jl(*parts):
    with open(os.path.join(*parts), "r", encoding="utf-8") as f:
        return json.load(f)


BA = jl(HERE, "before_after.json")


def mfile(state, rel):
    return jl(MIRROR, state, *rel.split("/"))


def aug_rows(state, variant):
    return [r for r in mfile(state, "scratch_review/hindcast_aug_data/aug_replay.json")["rows"]
            if r["variant"] == variant and r["n_new_burnable"] > 0]


def pm(rows):
    return H.pool_metrics([H.PairMetrics(**{k: v for k, v in r.items() if k in FIELDS}) for r in rows])


def july_pool(state):
    """oos_pool.py's strict July pool (t0 >= 07-13, growth, weather span)."""
    d = mfile(state, "scratch_review/hindcast_oos_data/replay_b0.5.json")
    return BAm.oos_keep(d["pairs"], BAm.ROOT if state == "after" else BAm.BEFORE_ROOT)[0]


def pct(a, b):
    return 100.0 * a / b if b else float("nan")


# ---------------------------------------------------------------------------
# statistics recomputed with the review scripts' formulas
# ---------------------------------------------------------------------------

def dispersion(rows):
    """rvclaude_dispersion.py: sd of log(head_P50/obs), band half-width
    |median log(head_P10/head_P50)|, pairs with obs head inside
    [head_P10, head_P50], median head_P10/head_P50."""
    rs = [r for r in rows if r.get("obs_head_m")]
    ens = [r["head_m_p10"] / r["head_m_p50"] for r in rs if r["head_m_p50"] > 0]
    err = [r["head_m_p50"] / r["obs_head_m"] for r in rs]
    lerr = [math.log(x) for x in err if x > 0]
    lens = [math.log(x) for x in ens if x > 0]
    half = abs(statistics.median(lens)) if lens else 0.0
    sd = statistics.pstdev(lerr) if lerr else float("nan")
    inside = sum(1 for r in rs if min(r["head_m_p10"], r["head_m_p50"]) <= r["obs_head_m"]
                 <= max(r["head_m_p10"], r["head_m_p50"]))
    return {"sd_log_err": sd, "under_dispersed_x": sd / half if half else float("inf"),
            "inside": f"{inside}/{len(rs)}", "ens_median": statistics.median(ens) if ens else None}


def pit_pool(rows):
    """rv2sci_a7_disp.pool_pit: lo + hi extreme-bin mass of the pooled PIT."""
    tot = None
    for r in rows:
        h = r.get("pit_hist") or []
        tot = list(h) if tot is None else [a + b for a, b in zip(tot, h)]
    n = sum(tot) if tot else 0
    return 100.0 * (tot[0] + tot[-1]) / n if n else float("nan")


def heads(rows):
    """published upper-middle vs true median of head_P50/obs (rvclaude_aug_deep)."""
    hs = sorted(r["head_m_p50"] / r["obs_head_m"] for r in rows if r.get("obs_head_m"))
    return (hs[len(hs) // 2], statistics.median(hs)) if hs else (float("nan"),) * 2


def late_share(rows, name_prefix):
    """rvclaude_aug_deep.py: incident's share of all late-miss cells."""
    tot = sum(r["late_miss"] for r in rows)
    own = sum(r["late_miss"] for r in rows if r["name"].startswith(name_prefix))
    return pct(own, tot)


def eff_n_incidents(rows):
    """1 / HHI of cell shares grouped BY INCIDENT (rv2stats_derive)."""
    by = {}
    for r in rows:
        by[r["name"]] = by.get(r["name"], 0) + r["n_new_burnable"]
    tot = sum(by.values())
    return 1.0 / sum((v / tot) ** 2 for v in by.values()) if tot else float("nan")


def equal_incident(rows):
    by = {}
    for r in rows:
        a = by.setdefault(r["name"], [0, 0])
        a[0] += r["p10_not_after_window"]
        a[1] += r["n_new_burnable"]
    return statistics.mean(100.0 * a / n for a, n in by.values()) if by else float("nan")


def area_pooled(rows):
    """rvclaude_final_table.py: sum modeled p50 new area / sum observed."""
    return sum(r["modeled_new_area_ac_p50"] for r in rows) / sum(r["obs_new_area_ac"] for r in rows)


def area_budget_p10(rows):
    """rv2sci_a2 eng_area_x: the engine's p10 new cells / new burnable cells."""
    cells = sum(r["modeled_new_area_ac_p10"] / (r["cell_m"] ** 2 / 4046.8564224) for r in rows)
    return cells / sum(r["n_new_burnable"] for r in rows)


def bin_6_12(rows):
    sub = [r for r in rows if 6 < r["dt_h"] <= 12]
    n = sum(r["n_new_burnable"] for r in sub)
    return len(sub), n, pct(sum(r["covered_possible"] for r in sub), n)


def worst_pair(rows):
    r = max(rows, key=lambda r: r["late_miss"] / r["n_new_burnable"])
    return (f"{r['name']} {r['obs_head_m'] / 1000:.1f} km / {r['dt_h']:.1f} h at "
            f"{r['head_m_p50'] / r['obs_head_m']:.2f}x ({pct(r['late_miss'], r['n_new_burnable']):.1f} % late)")


def fuel5(rows):
    agg = {}
    for r in rows:
        for c, rec in (r.get("by_fuel_class") or {}).items():
            a = agg.setdefault(c, [0, 0])
            a[0] += rec.get("late", 0)
            a[1] += rec.get("n", 0)
    return " / ".join(f"{c} {pct(*agg[c]):.1f}" for c in ("GR", "GS", "SH", "TL", "TU") if c in agg)


def f1(x, unit="%"):
    if isinstance(x, str):
        return x
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:.1f}{unit}" if unit == "%" else f"{x:.2f}{unit}"


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------

ENTRIES = []


def add(key, what, gen, vals, cited):
    ENTRIES.append({"key": key, "what": what, "generator": gen,
                    "documented": vals[0], "before": vals[1], "after": vals[2],
                    "cited": cited})


def by_state(fn):
    return tuple(fn(s) for s in STATES)


FIRST_PASS_DOC = {   # docs/calibration/hindcast_first_pass.md (07-10, blend 0) as printed
    "fp.size": "14 / 60,062", "fp.adequacy": "68.8%", "fp.late": "31.2%", "fp.p50in": "64.7%",
    "fp.head": "1.32x", "fp.wide": "69.8% (+1.0 pp)",
    "fp.fuel": "GR 8,241/52.6 · GS 12,913/46.8 · SH 34,337/20.1 · TL 237/54.0 · TU 4,334/30.0 · NB 3,315",
    "fp.pit": "[28706, 2050, 2145, 707, 1789, 2257, 22408]",
}


def build():
    fp = BA["pinned"]["first_pass"]
    add("fp.size", "Pinned corpus (Gifford/Dillon/Madre/Butler), default replay: scored pairs / new burnable cells",
        "hindcast_harness.py replay (blend 0)",
        by_state(lambda s: f"{fp[s]['pool']['pairs_scored']} / {fp[s]['pool']['cells']:,}"),
        "hindcast_first_pass.md:45; SPRINT2_PLAN.md:330,412; calibration_review_2026-07-31.md:98,143; "
        "MODEL_ASSESSMENT_HANDOFF.md:93-94 (14 / 60k); contracts.py:2310 (14-window)")
    add("fp.adequacy", "Pinned, blend 0: p10 adequacy (necessary bound, = coverage possible)",
        "hindcast_harness.py replay", by_state(lambda s: f1(fp[s]["pool"]["coverage"])),
        "hindcast_first_pass.md:46-47,92-93; SPRINT2_PLAN.md:413-414; ENGINE_REVIEW_2026-07-11.md:78; "
        "LIVE_OPS_PLAN.md:41; calibration_review:102,312,350; TRI_MODEL_REVIEW:49")
    add("fp.late", "Pinned, blend 0: late miss", "hindcast_harness.py replay",
        by_state(lambda s: f1(fp[s]["pool"]["late"])),
        "hindcast_first_pass.md:48,94; SPRINT2_PLAN.md:414; calibration_review:102")
    add("fp.p50in", "Pinned, blend 0: p50 inside window", "hindcast_harness.py replay",
        by_state(lambda s: f1(fp[s]["pool"]["p50_in"])), "hindcast_first_pass.md:48; SPRINT2_PLAN.md:414")
    add("fp.head", "Pinned, blend 0: median head ratio p50 (published upper-middle rule)",
        "hindcast_harness.py replay (07-10 report)",
        by_state(lambda s: f1(fp[s]["pool"]["head_upper_mid"], "x")),
        "hindcast_first_pass.md:50; calibration_review:102")
    add("fp.wide", "Pinned, blend 0: p10 adequacy with the widened band (wind 0.50 / dir 45) and its gain",
        "hindcast_harness.py replay (pairs_wide)",
        by_state(lambda s: f"{f1(fp[s]['wide']['coverage'])} (+{fp[s]['wide']['coverage'] - fp[s]['pool']['coverage']:.1f} pp)"),
        "hindcast_first_pass.md:52,93; calibration_review:312,957; SPRINT2_PLAN.md:416")
    add("fp.fuel", "Pinned, blend 0: late miss by fuel class (cells / late %)",
        "hindcast_harness.py replay",
        by_state(lambda s: " · ".join(f"{c} {v['cells']:,}/{v['late_pct']:.1f}" for c, v in fp[s]["fuel"].items() if c != "NB")
                 + f" · NB {fp[s]['fuel'].get('NB', {}).get('cells', 0):,}"),
        "hindcast_first_pass.md:62-69,95; calibration_review:180-189,204; SPRINT2_PLAN.md:420")
    add("fp.pit", "Pinned, blend 0: PIT histogram (rank 0 = engine late ... top = engine early)",
        "hindcast_harness.py replay", by_state(lambda s: str(fp[s]["pit"])),
        "hindcast_first_pass.md:51,95 (rank 0: 48 %, top: 37 %)")
    for b in ("0.0", "0.25", "0.5", "0.75", "1.0"):
        gs = BA["pinned"]["gust_sweep"][b]
        add(f"gs.{b}", f"Pinned gust sweep, blend {b}: coverage / late / median head (upper-middle)",
            "scratch_review/gust_sweep.py",
            by_state(lambda s: f"{gs[s]['coverage']:.1f} / {gs[s]['late']:.1f} / {gs[s]['head_upper_mid']:.2f}x"),
            f"calibration_review:{ {'0.0': 102, '0.25': 103, '0.5': 104, '0.75': 105, '1.0': 106}[b] }"
            + ("; hindcast_oos_2026-07-28.md:40 (2.03x)" if b == "1.0" else "")
            + ("; contracts.py:2310 (the blend-0.5 calibration)" if b == "0.5" else ""))
    for var, cite in (("blend0.5", "hindcast_oos:108; calibration_review:104,145,269; MODEL_ASSESSMENT_HANDOFF:93; TRI_MODEL_REVIEW:42,49"),
                      ("grass_floor", "hindcast_oos:109,127; calibration_review:146,157,269; MODEL_ASSESSMENT_HANDOFF:94"),
                      ("realism_pack", "hindcast_oos:110,131; calibration_review:147,167,171,269")):
        cr = BA["pinned"]["cal_realism"][var]
        add(f"cr.{var}", f"Calibration-set realism [{var}]: pairs / coverage / late / median head (upper-middle)",
            "scratch_review/oos_realism_cal.py",
            by_state(lambda s: f"{cr[s]['pairs']} / {cr[s]['coverage']:.1f} / {cr[s]['late']:.1f} / {cr[s]['head_upper_mid']:.2f}x"),
            cite)

    o = BA["oos"]
    add("oos.pool", "July OOS strict pool (t0 >= 07-13, growth, weather span), blend 0.5: windows / cells / fires",
        "scratch_review/oos_pool.py",
        by_state(lambda s: f"{o['blend']['0.5'][s]['pairs_scored']} / {o['blend']['0.5'][s]['cells']:,} / {len(o['blend']['0.5'][s]['incidents'])}"),
        "hindcast_oos:19; calibration_review:108,143; MODEL_ASSESSMENT_HANDOFF:95; TRI_MODEL_REVIEW:44")
    for b, ln in (("0.0", 31), ("0.25", 32), ("0.5", 33), ("0.75", 34), ("1.0", 35)):
        add(f"oos.b{b}", f"July OOS pool, blend {b}: coverage / late / median head (upper-middle)",
            "scratch_review/oos_pool.py",
            by_state(lambda s: f"{o['blend'][b][s]['coverage']:.1f} / {o['blend'][b][s]['late']:.1f} / {o['blend'][b][s]['head_upper_mid']:.2f}x"),
            f"hindcast_oos:{ln}; calibration_review:{ln + 82}"
            + ("; MODEL_ASSESSMENT_HANDOFF:95; calibration_review:145" if b == "0.5" else "")
            + ("; calibration_review:122 (+3.7 pp vs 0.5)" if b == "1.0" else ""))
    add("oos.normal", "July OOS, normal-regime subset (every pool window except Porcupine), blend 0.5: windows / cells / coverage",
        "scratch_review/oos_pool.py (per-pair)",
        by_state(lambda s: f"{o['regimes'][s]['normal']['windows']} / {o['regimes'][s]['normal']['cells']:,} / {o['regimes'][s]['normal']['coverage']:.1f}"),
        "hindcast_oos:44; calibration_review:149; MODEL_ASSESSMENT_HANDOFF:97")
    add("oos.porcupine", "Porcupine Ridge 07-17 +12.9 h, blend 0.5: cells / observed head / p50 head / head ratio / observed new area / late",
        "replay_b0.5.json via oos_pool.py",
        by_state(lambda s: "; ".join(
            f"{p['new_burnable']:,} cells / {p['obs_head_km']:.1f} km / {p['p50_head_km']:.1f} km / "
            f"{(p['head_p50'] or 0):.2f}x / {p['obs_new_area_ac']:,.0f} ac / {p['late']:.1f} % late"
            for p in o["regimes"][s]["porcupine"]) or "not in pool"),
        "hindcast_oos:57-59; calibration_review:214-217; MODEL_ASSESSMENT_HANDOFF:100-101; README.md:400; "
        "rancho_santa_fe_plan.md:114,875; vail_aspen_response_program.md:906; contracts.py:2347; FIX_PROGRAM_2026-09-11:114")
    add("oos.porcupine_share", "Porcupine's share of the July OOS pool's cells (blend 0.5)",
        "scratch_review/oos_pool.py", by_state(lambda s: f1(o["regimes"][s]["porcupine_share_of_cells"])),
        "hindcast_oos:57; calibration_review:151,216")
    add("oos.porcupine_wide", "Porcupine late miss under the widened band (wind 0.50 / dir 45), blend 0.5",
        "replay_b0.5.json pairs_wide", by_state(lambda s: ", ".join(f"{x:.1f} %" for x in o["wide_porcupine"][s]) or "—"),
        "hindcast_oos:61; calibration_review:216")
    add("oos.fuel", "July OOS pool, blend 0.5: late miss by fuel class (cells / late %)",
        "per-fuel pooling of replay_b0.5.json",
        by_state(lambda s: " · ".join(f"{c} {v['cells']:,}/{v['late_pct']:.1f}" for c, v in o["fuel_b05"][s].items() if c != "NB")),
        "hindcast_oos:66-72; calibration_review:191-199,204; rancho_santa_fe_plan.md:181,872; vail_aspen_response_program.md:263,861")
    for var, cite in (("shipped", "hindcast_oos:91,108; calibration_review:145"),
                      ("grass_floor", "hindcast_oos:92,127; calibration_review:146,167; MODEL_ASSESSMENT_HANDOFF:96; TRI_MODEL_REVIEW:40 (64.8 %)"),
                      ("realism_pack", "hindcast_oos:93; calibration_review:147,167")):
        rr = o["realism"][var]
        add(f"oos.realism.{var}", f"July OOS realism [{var}] (oos_realism.py GOOD set): pairs / coverage / late",
            "scratch_review/oos_realism.py",
            by_state(lambda s: f"{rr[s]['pairs']} / {rr[s]['coverage']:.1f} / {rr[s]['late']:.1f}"), cite)
    add("oos.realism.porcupine", "Porcupine late miss: shipped / + floor / + floor + spotting",
        "scratch_review/oos_realism.py",
        by_state(lambda s: " / ".join(
            f1(next((v['late'] for k, v in o['realism'][var][s]['per_pair'].items() if k.startswith('PorcupineRidge')), None))
            for var in ("shipped", "grass_floor", "realism_pack"))),
        "hindcast_oos:59,92-93; calibration_review:215")
    add("oos.realism.kaiser", "Kaiser Canyon's worst pool window: coverage at blend 0.5 -> + floor",
        "scratch_review/oos_pool.py + oos_realism.py",
        by_state(lambda s: _kaiser(o, s)), "hindcast_oos:95; calibration_review:151")
    add("oos.realism.normal", "July OOS normal-regime subset: coverage + floor (every window except Porcupine)",
        "scratch_review/oos_realism.py",
        by_state(lambda s: _normal_floor(o, s)), "hindcast_oos:96; calibration_review:150; MODEL_ASSESSMENT_HANDOFF:97")
    add("oos.excluded", "July OOS: windows excluded from the strict pool (blend 0.5) and why",
        "scratch_review/oos_pool.py", by_state(lambda s: f"{len(o['excluded'][s])}: " + "; ".join(o["excluded"][s])),
        "hindcast_oos:20-25 (Crosswhite 'no spatial growth', BISCAR 'perimeter did not grow', ELEPHANT dt)")
    add("oos.gate", "Incidents with >= 1 scored growth window, pinned + July OOS (the 10-incident evidence gate)",
        "harness replays (blend 0.5)", by_state(lambda s: _gate(s)),
        "hindcast_oos:79-82; calibration_review:154")
    add("oos.corpus42", "Pinned + July OOS replay windows / new cells / incidents ('42 windows / ~177k cells / 11 incidents')",
        "harness replays (pinned blend 0 + OOS blend 0.5)", by_state(lambda s: _corpus42(s)),
        "calibration_review:37-38,354-355,907")

    ex = BA["extreme"]
    add("sig.windows", "Extreme-regime signature: windows scored under the grass floor (cal + strict OOS)",
        "scratch_review/extreme_signature.py", by_state(lambda s: str(len(ex[s]))),
        "hindcast_oos:143; calibration_review:237,251,254; rancho_santa_fe_plan.md:172,206,975")
    add("sig.armed", "Windows the shipped regime condition arms (GR+GS >= 0.60 AND max eff wind >= 4.0 m/s): window (grass %, eff wind, floored late %)",
        "scratch_review/extreme_signature.py",
        by_state(lambda s: "; ".join(f"{r['window']} ({r['grass']:.1f}, {r['max_eff']:.2f}, {r['floored_late']:.1f})" for r in ex[s] if r["armed"])),
        "hindcast_oos:154-157; calibration_review:248-251; README.md:401; contracts.py:2350-2352")
    add("sig.over50", "Windows with floored late miss > 50 %",
        "scratch_review/extreme_signature.py",
        by_state(lambda s: "; ".join(f"{r['window']} ({r['floored_late']:.1f})" for r in ex[s] if r["floored_late"] > 50) or "none"),
        "calibration_review:251-255 ('exactly one: Porcupine', 'n = 1'); TRI_MODEL_REVIEW:124; FIX_PROGRAM_2026-09-03:214; "
        "MODEL_ASSESSMENT_HANDOFF:162; LIVE_WORLD_PLAN:1302; rancho_santa_fe_plan.md:206")
    add("sig.under60", "Max floored late miss among windows under 60 % grass-class ('~0 %')",
        "scratch_review/extreme_signature.py",
        by_state(lambda s: _under60(ex[s])), "hindcast_oos:149-150; calibration_review:245-246; README.md:401; contracts.py:2351")

    ag = BA["aug"]
    add("aug.table", "August review-era table (t0 >= 08-01): pairs / cells / fires",
        "scratch_review/aug_hindcast_replay.py",
        by_state(lambda s: f"{ag['as-deployed'][s]['pairs']} / {ag['as-deployed'][s]['cells']:,} / "
                           f"{len({r['name'] for r in ag['as-deployed'][s]['per_pair']})}"),
        "MODEL_ASSESSMENT_HANDOFF:98; TRI_MODEL_REVIEW:40 (7 fires); SCORECARD.md:34 (18 / 135k)")
    for var in ("calibration", "as-deployed"):
        add(f"aug.{var}", f"August review-era [{var}]: coverage / late / median head (upper-middle)",
            "scratch_review/aug_hindcast_replay.py",
            by_state(lambda s: f"{ag[var][s]['coverage']:.1f} / {ag[var][s]['late']:.1f} / {ag[var][s]['head_upper_mid']:.2f}x"),
            "MODEL_ASSESSMENT_HANDOFF:98; TRI_MODEL_REVIEW:40,42,45; SCORECARD.md:33" if var == "as-deployed"
            else "TRI_MODEL_REVIEW:45 (77.2 -> 93.8 = +16.6 pp)")
    add("aug.worst", "August review-era worst pair (as-deployed)", "scratch_review/aug_hindcast_replay.py",
        by_state(lambda s: worst_pair(aug_rows(s, "as-deployed"))), "MODEL_ASSESSMENT_HANDOFF:102-103")
    add("aug.fuel", "August review-era per-fuel late miss (as-deployed), %", "scratch_review/aug_hindcast_replay.py",
        by_state(lambda s: fuel5(aug_rows(s, "as-deployed"))), "MODEL_ASSESSMENT_HANDOFF:104-105")
    add("tm.skill", "Null-model area efficiency, Aug as-deployed: area a physics-free isotropic buffer needs to match "
        "the engine's p10 coverage / the engine's own p10 area ('1.85x')",
        "scratch_review/rv2sci_a2_null.py (run_a2.py)", by_state(lambda s: f1(_a2(s)["skill_x"], "x")),
        "TRI_MODEL_REVIEW:24-25,40")
    add("tm.area_budget", "p10 area budget (engine p10 new cells / new burnable cells), Aug as-deployed",
        "scratch_review/rv2sci_a2_null.py eng_area_x", by_state(lambda s: f1(_a2(s)["eng_area_x"], "x")),
        "TRI_MODEL_REVIEW:40 (5.8x)")
    add("tm.bin612", "Aug as-deployed, dt 6-12 h: pairs / cells / p10 adequacy",
        "rvgrok_stats_audit.py window_duration_bins",
        by_state(lambda s: "{} / {:,} / {:.1f}".format(*bin_6_12(aug_rows(s, "as-deployed")))),
        "TRI_MODEL_REVIEW:40 (69.8 %); SCORECARD.md:33 ('one 9.5 h pair')")
    add("tm.bug_share", "Bug-CA's share of all late-miss cells, Aug as-deployed",
        "rvclaude_aug_deep.py", by_state(lambda s: f1(late_share(aug_rows(s, "as-deployed"), "Bug"))),
        "TRI_MODEL_REVIEW:40 (91 %); SCORECARD.md:33")
    add("tm.effn_aug", "Effective incidents (1 / HHI of cell shares by incident), Aug as-deployed",
        "rv2stats_derive (HHI by incident)", by_state(lambda s: f1(eff_n_incidents(aug_rows(s, "as-deployed")), "")),
        "TRI_MODEL_REVIEW:40,44 (~3; 3.244); SCORECARD.md:34 (3-4)")
    add("tm.pit", "PIT extreme-bin mass: Aug as-deployed / Aug calibration / July OOS blend 0.5 (pool)",
        "rv2sci_a7_disp.pool_pit", by_state(lambda s: " / ".join(f1(x) for x in (
            pit_pool(aug_rows(s, "as-deployed")), pit_pool(aug_rows(s, "calibration")),
            BA["matrix"]["oos/F_nofloor"][s]["pit_extreme"]))),
        "TRI_MODEL_REVIEW:41 (76-95 %; 79.7 / 82.7 / 88.3; July = every scored July pair at blend 0.5)")
    add("tm.heads", "Median head ratio, published upper-middle -> true median: Aug as-deployed; pinned blend 0.5 (cal_realism)",
        "rvclaude_aug_deep.py / rv2code_r8_r10_followup.py",
        by_state(lambda s: "; ".join("{:.2f} -> {:.3f}".format(*heads(rs)) for rs in (
            aug_rows(s, "as-deployed"),
            [r for r in mfile(s, "scratch_review/hindcast_oos_data/cal_realism.json") if r["variant"] == "blend0.5"]))),
        "TRI_MODEL_REVIEW:42 (1.88 -> 1.837; 1.77 -> 1.61)")
    add("tm.dispersion", "Aug as-deployed: sd(log head_P50/obs) / under-dispersion factor / obs head inside [P10, P50] / median head_P10/head_P50",
        "rvclaude_dispersion.py",
        by_state(lambda s: (lambda d: f"{d['sd_log_err']:.3f} / {d['under_dispersed_x']:.1f}x / {d['inside']} / {d['ens_median']:.3f}")(dispersion(aug_rows(s, "as-deployed")))),
        "TRI_MODEL_REVIEW:43,45 (0.710 / 8x / 1/18 / 1.09), 114-115; README.md:378,423; FIX_PROGRAM_2026-09-03:58-59; "
        "FIRE_BELIEF.md:19-20,101,313; contracts.py:2210-2211; LIVE_WORLD_PLAN:1312 (the 0.71 prior)")
    add("tm.area_p50", "Pooled area ratio p50 (modeled / observed new area): Aug calibration -> as-deployed",
        "rvclaude_final_table.py",
        by_state(lambda s: f"{area_pooled(aug_rows(s, 'calibration')):.2f}x -> {area_pooled(aug_rows(s, 'as-deployed')):.2f}x"),
        "TRI_MODEL_REVIEW:45 (2.3x -> 6.1x)")
    add("tm.july_equal", "July OOS [+ floor]: windows / incidents / equal-incident coverage / effective incidents",
        "rv2stats_derive over oos_realism.json",
        by_state(lambda s: (lambda rs: f"{len(rs)} / {len({r['name'] for r in rs})} / {equal_incident(rs):.1f} / {eff_n_incidents(rs):.2f}")(
            [r for r in mfile(s, "scratch_review/hindcast_oos_data/oos_realism.json") if r["variant"] == "grass_floor"])),
        "TRI_MODEL_REVIEW:44 (8 windows = 4 incidents, 84.3 %; 2.414)")

    add("fx11.regime", "J_regime_I071: scored pairs the extreme-regime detector arms (August; July)",
        "calmatrix2_run.sh -> calmatrix/{aug,oos}_J_regime_I071.json (pair notes)",
        by_state(lambda s: "; ".join(_regime_count(s, c) for c in ("aug", "oos"))),
        "FIX_PROGRAM_2026-09-11:102,106-108 (Aug 13/27, July 2/18); MODEL_ASSESSMENT_HANDOFF:66")
    add("fx11.perpair", "July matrix corpus: every scored pair with > 6 % late under D071 or I071 (cells / late D071 / late I071)",
        "calmatrix/oos_{D_gate_me071,I_gategrass05_me071}.json",
        by_state(lambda s: _perpair(s)),
        "FIX_PROGRAM_2026-09-11:114-121 (Porcupine 33,888 / 53.1 / 62.8; AspenAcres 11,003 / 3.8 / 86.9; "
        "Willow 1,231 / 0 / 62.6; Cottonwood 7,886 / 0 / 34.3; others 0 / <= 6)")

    for corpus, name in (("aug", "August"), ("oos", "July OOS")):
        for label in ("F_nofloor", "A_deployed", "B_gate", "C_gate_grass", "D_gate_me035", "D_gate_me050",
                      "D_gate_me071", "E_gategrass_me050", "E_gategrass_me071", "G_gate_p50",
                      "H_nofloor_me050", "H_nofloor_me071", "I_gategrass05_me050", "I_gategrass05_me071",
                      "J_regime_I071", "L_regime_I_noband", "M_D071_piecewise", "N_D071_phi", "O_D071_both"):
            m = BA["matrix"][f"{corpus}/{label}"]
            add(f"mx.{corpus}.{label}",
                f"Matrix {name} {label}: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident",
                "calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py",
                by_state(lambda s: _mx(m[s])), _mx_cites(corpus, label))
    return ENTRIES


def _mx(v):
    b = "—" if v["bin_6_12"] is None else f"{v['bin_6_12']:.1f}"
    return (f"{v['pairs']}/{v['cells']:,}/{v['incidents']} · {v['p10_adequacy']:.1f} · {v['late_miss']:.1f} · "
            f"{v['area_p10']:.2f}x · {v['skill']:.2f} · {v['pit_extreme']:.1f} ({v['pit_early']:.0f}/{v['pit_late']:.0f}) · "
            f"{b} · {v['eq_incident_mean']:.1f}")


def _mx_cites(corpus, label):
    base = f"calibration_matrix_2026-09-03.md / -09-11.md ({corpus})"
    extra = {
        "A_deployed": "FIX_PROGRAM_2026-09-03:162,167-172,184,204-209; FIX_PROGRAM_2026-09-11:139-141",
        "B_gate": "FIX_PROGRAM_2026-09-03:167-169,185",
        "C_gate_grass": "FIX_PROGRAM_2026-09-03:170-171,186,195-196",
        "F_nofloor": "FIX_PROGRAM_2026-09-03:172-174",
        "D_gate_me050": "FIX_PROGRAM_2026-09-03:174-176,191",
        "D_gate_me071": "FIX_PROGRAM_2026-09-03:174-176,190,200-209; FIX_PROGRAM_2026-09-11:100,134,145",
        "E_gategrass_me050": "FIX_PROGRAM_2026-09-03:187,195-196",
        "H_nofloor_me071": "FIX_PROGRAM_2026-09-03:189",
        "I_gategrass05_me071": "FIX_PROGRAM_2026-09-03:188,195-196,211-216; FIX_PROGRAM_2026-09-11:101; contracts.py:2336-2338",
        "J_regime_I071": "FIX_PROGRAM_2026-09-11:102,106-108 (regime pairs Aug 13/27, July 2/18); MODEL_ASSESSMENT_HANDOFF:66",
        "L_regime_I_noband": "FIX_PROGRAM_2026-09-11:103",
        "M_D071_piecewise": "FIX_PROGRAM_2026-09-11:136,147-148",
        "N_D071_phi": "FIX_PROGRAM_2026-09-11:135,139-141; LIVE_WORLD_PLAN:830-831 (27 / 145,760 / 97.7 / 2.3, August)",
        "O_D071_both": "FIX_PROGRAM_2026-09-11:137",
    }.get(label, "")
    return base + ("; " + extra if extra else "")


def _regime_count(s, corpus):
    ps = [p for p in mfile(s, f"scratch_review/calmatrix/{corpus}_J_regime_I071.json")["pairs"]
          if p["n_new_burnable"] > 0]

    allp = mfile(s, f"scratch_review/calmatrix/{corpus}_J_regime_I071.json")["pairs"]
    armed = lambda q: any("extreme spread regime" in n for n in q.get("notes") or [])
    # the doc's count: armed among ALL replayed pairs over the SCORED pairs
    return (f"{corpus} {sum(armed(q) for q in allp)}/{len(ps)} "
            f"(armed among scored: {sum(armed(q) for q in ps)})")


def _perpair(s):
    d = {(p["incident"], p["t0_s"]): p for p in
         mfile(s, "scratch_review/calmatrix/oos_D_gate_me071.json")["pairs"] if p["n_new_burnable"] > 0}
    i = {(p["incident"], p["t0_s"]): p for p in
         mfile(s, "scratch_review/calmatrix/oos_I_gategrass05_me071.json")["pairs"] if p["n_new_burnable"] > 0}
    out = []
    for k, p in sorted(d.items(), key=lambda kv: -kv[1]["n_new_burnable"]):
        q = i.get(k)
        ld = pct(p["late_miss"], p["n_new_burnable"])
        li = pct(q["late_miss"], q["n_new_burnable"]) if q else float("nan")
        if ld > 6.0 or li > 6.0:
            out.append(f"{p['name']} {BAm.fdt(p['t0_s'])} +{p['dt_h']:.1f}h {p['n_new_burnable']:,} / {ld:.1f} / {li:.1f}")
    return "; ".join(out) or "none"


def _a2(s):
    if s == "documented":
        return jl(ROOT, "scratch_review", "rv2sci_a2.json")
    return jl(HERE, "results", "review_scripts", s, "rv2sci_a2.json")


def _kaiser(o, s):
    rows = {k: v for k, v in o["realism"]["shipped"][s]["per_pair"].items() if k.startswith("KaiserCanyon")}
    if not rows:
        return "—"
    k = min(rows, key=lambda k: rows[k]["coverage"])
    fl = o["realism"]["grass_floor"][s]["per_pair"].get(k, {})
    return f"{k}: {rows[k]['coverage']:.1f} -> {fl.get('coverage', float('nan')):.1f}"


def _normal_floor(o, s):
    rows = {k: v for k, v in o["realism"]["grass_floor"][s]["per_pair"].items() if not k.startswith("PorcupineRidge")}
    n = sum(v["cells"] for v in rows.values())
    cov = sum(v["cells"] * v["coverage"] / 100 for v in rows.values())
    return f"{len(rows)} windows / {n:,} cells / {pct(cov, n):.1f}"


def _gate(s):
    names = set()
    for rel, key in (("tools/hindcast_data/replay_results.json", "pairs"),
                     ("scratch_review/hindcast_oos_data/replay_b0.5.json", "pairs")):
        names |= {p["name"] for p in mfile(s, rel)[key] if p["n_new_burnable"] > 0}
    return f"{len(names)}: {', '.join(sorted(names))}"


def _corpus42(s):
    a = mfile(s, "tools/hindcast_data/replay_results.json")["pairs"]
    b = mfile(s, "scratch_review/hindcast_oos_data/replay_b0.5.json")["pairs"]
    scored = [p for p in a + b if p["n_new_burnable"] > 0]
    return (f"{len(a) + len(b)} replayed ({len(scored)} scored) / "
            f"{sum(p['n_new'] for p in a + b):,} new cells / "
            f"{len({p['name'] for p in scored})} incidents with a scored window")


def _under60(rows):
    xs = [r["floored_late"] for r in rows if r["grass"] < 60.0]
    return f"{max(xs):.1f} % (of {len(xs)} windows)" if xs else "—"


def main() -> int:
    entries = build()
    for e in entries:
        if e["key"] in FIRST_PASS_DOC:
            # the committed replay_results.json was regenerated at blend 0.5
            # after this report (tri-model A11), so the report's own figures
            # are the documented values; they equal today's pre-fix run
            e["documented"] = FIRST_PASS_DOC[e["key"]]
            e["documented_check"] = "equals before" if e["documented"] == e["before"] else "DIFFERS from before"
    with open(os.path.join(HERE, "registry.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(entries, f, indent=1)
    lines = ["| # | number | documented | before (today's engine, pre-fix corpus) | after (repaired corpus) | cited at |",
             "|---|---|---|---|---|---|"]
    for i, e in enumerate(entries, 1):
        lines.append(f"| {i} | {e['what']} <br>*{e['generator']}* | {e['documented']} | {e['before']} | "
                     f"{e['after']} | {e['cited']} |")
    with open(os.path.join(HERE, "registry.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"{len(entries)} entries -> registry.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
