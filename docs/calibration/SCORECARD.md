# Hindcast scorecard — metric reference

One page defining every metric `tools/hindcast_lib.py` reports (per-pair on
`PairMetrics`, pooled in `pool_metrics()`), which polarity of each is
DANGEROUS for an alerting product, and why three of the original metrics
were retired. Written for fix-program item 5 of the tri-model review
(`docs/TRI_MODEL_REVIEW_2026-08-31.md`), which found the published scorecard
one-sided by construction. This document is descriptive of the metrics only
— it changes no engine behavior and recommends no config change.

**Context you need once:** the harness seeds the shipped engine with an
observed T0 perimeter and scores the modeled arrival-time quantile field
(p10/p50/p90/nominal) against the next observed perimeter T1. Because the
engine can only be seeded at T0, **no modeled arrival can ever be earlier
than T0** — p90 (and p10, and every member) is always >= T0 for any cell the
ensemble reaches. That one structural fact is the root cause of every
retirement below: it makes an entire family of "looks two-sided" statistics
collapse onto one-sided ones.

## Honest metrics (present these as calibration evidence)

| Key | Definition | Dangerous direction |
|---|---|---|
| `p10_adequacy_frac` (per-pair: `p10_adequacy()`) | P(p10 <= T1) — the pessimistic quantile arrived by the end of the observation window. Honest rename of the old `covered_possible` (identical value; see Retired below). | **LOW is dangerous.** A low p10_adequacy means p10 is *not* a safe pessimistic bound — real fire outran even the pessimistic member, so a p10-gated alert would have been late. |
| `late_miss_frac` (per-pair: `frac(late_miss)`) | p10 > T1: the band is entirely after the window — the engine is DEFINITIVELY late for that cell (`1 - p10_adequacy_frac`, restricted to reached cells; see `never_reached_frac`). | **HIGH is dangerous.** This is the single most important number in the scorecard: it is the fraction of newly-burned ground where even the pessimistic member didn't catch up with reality. |
| `never_reached_frac` | p10 = inf — no member reached the cell at all within the horizon; the worst case of `late_miss`, counted inside it. | **HIGH is dangerous** — same polarity as late_miss, more severe (not just late, absent). |
| `p50_in_window_frac` | T0 < p50 <= T1 — the median member's arrival actually falls inside the observed window (the closest thing to "the forecast was about right"). | Informational; not a safety threshold by itself. Low values do not by themselves indicate danger (see area/head ratios for direction). |
| `head_ratio_p50` / `_p10` / `_p90` / `_nominal`, `median_head_ratio_*` | Modeled head-run length (farthest newly-burned boundary cell from the T0 boundary) at each quantile, divided by the *observed* head-run length. > 1 = engine ran farther than reality (over-forecast / fail-safe for an alerting product); < 1 = engine ran short (under-forecast). | **< 1 (especially << 1) is dangerous** for p10/p50 — it means the "pessimistic" and "typical" model runs are both slower than the real fire. > 1 is the safe/fail-safe direction (over-warning, not silence). |
| `area_ratio_p50` / `_p10`, `median_area_ratio_*` | Modeled new-burn area (cells with arrival <= T1, minus the T0 seed) at p50/p10, divided by observed new-burn area. Squares any ROS error (area ~ ROS^2), so expect ratios to move more than head ratios for the same underlying bias. | Same polarity as head ratios: **< 1 is dangerous**, > 1 is the fail-safe direction. |
| `pit_hist`, `pit_summary` (`lo_bin_frac`, `hi_bin_frac`, `extreme_bins_frac`, `chi2_flatness`, `chi2_df`) | Probability-Integral-Transform histogram: for each newly-burned cell, the rank of the observation-window midpoint among the ensemble members' arrival times (bin 0 = every member arrived after the midpoint i.e. observed burn was before every member = engine late; top bin = observed burn was after every member = engine early/over-run). A well-calibrated ensemble has a FLAT PIT histogram. `chi2_flatness` is the Pearson chi-square statistic against a uniform histogram with `chi2_df = n_bins-1` degrees of freedom (0 = perfectly flat; larger = more peaked/under-dispersed). This field was always computed by `score_pair` but never surfaced in the report before this fix. | **High mass in bin 0 is dangerous** (engine late — the same signal as late_miss, at cell-level rank resolution). High mass in the top bin is the fail-safe direction (engine early/over-running, i.e. suppression the model doesn't know about). Mass piled at BOTH extremes (high `extreme_bins_frac`, high `chi2_flatness`) means the ensemble is under-dispersed in both directions at once — a band-width fix alone cannot repair that; it needs separate late- and early-side causes addressed. |
| `null_new_covered`, `null_adequacy_frac`, `skill` | Physics-free control: an isotropic (Euclidean nearest-first) dilation of the engine's T0 interior, grown to the SAME cell count as the engine's own p10 new-area budget (`modeled_new_area_ac_p10`) — "just grow a circle to the area the engine claimed." `null_adequacy_frac` is that circle's own p10-style adequacy (fraction of truly-newly-burned burnable cells it happens to cover); `skill = p10_adequacy_frac / null_adequacy_frac` at that equal area. Ported from the tri-model review's independent verification, `scratch_review/rv2sci_a2_null.py`. | **skill <= 1x is dangerous** — it means the physics-based forecast is no better (or worse) than a physics-free circle at the SAME area cost, i.e. the model's directional/fuel/wind information isn't earning its area-budget. `skill` is `None` (not 0) when the null model covered zero cells — a genuine "undefined," not "zero skill." |
| `modeled_new_area_ac_p10` / `_p50`, `head_m_p10` / `_p50` / `_p90` / `_nominal` | Raw modeled quantities feeding the ratios above (area in acres, head run in meters). Not a fraction, so has no "dangerous direction" of its own — read via the ratio it feeds. | — |
| `window_length_bins` (`<=6h`, `6-12h`, `12-24h`, `>24h`: `pairs`, `cells`, `p10_adequacy`, `late_miss`) | The pooled p10_adequacy/late_miss statistics, split by the observation window length `dt_h`. | **A low `<=6h`/`6-12h` bin is the dangerous case to watch, even if the long-window bins and the pooled-all-windows headline both look good.** The corpus scored to date has zero `dt_h<=6h` pairs and one 9.5h pair carrying most of the short-window late-miss mass (see the August 93.8%-headline correction in `docs/TRI_MODEL_REVIEW_2026-08-31.md`'s corrected-record table) — **score `dt<=12h` as the primary bin**, never the pooled-all-windows average, because windows longer than ~12h are dominated by pairs a non-catastrophic forecast almost cannot miss (the observation window itself provides most of the slack), which mechanically inflates the pooled number without saying anything about early-hour behavior. |
| `n_incidents`, `equal_incident_mean_p10_adequacy`, `loo_incident_p10_adequacy_min` / `_max`, `largest_incident_share_of_cells` / `_of_late_miss` | Incident-level concentration diagnostics. `equal_incident_mean` groups pairs by `incident` and averages each incident's OWN cell-weighted p10_adequacy with EQUAL weight per incident (not per cell) — this is different from, and usually more conservative than, the cell-weighted pooled number when one incident's pairs dominate the cell count. Leave-one-incident-out gives the [min, max] the pooled p10_adequacy_frac would have been had any single incident been excluded entirely (`None` when there is only one incident — undefined, not a `/0`). The `largest_incident_share_*` fields report what fraction of the pool's cells / late-miss cells come from the single biggest-contributing incident. | **A large gap between the cell-weighted headline and the equal-incident mean, a wide leave-one-out range, or a high `largest_incident_share_of_late_miss` are all dangerous SIGNS ABOUT THE HEADLINE NUMBER ITSELF** — not a separate safety statistic, but a warning that the pooled percentage may be one fire's behavior wearing a corpus-wide number. Cell count is not inferential sample size: an 18-pair, 135k-cell pool can be 3-4 effective incidents (see the tri-model review's HHI findings). |

## Retired metrics (kept for JSON compatibility only — never calibration evidence)

These three fields remain on `PairMetrics` and in `pool_metrics()`'s
returned dict — with a `deprecated_metrics` note attached to the pooled
dict — purely so old readers/dashboards loading this JSON do not break.
`render_report()` no longer presents them in the main narrative; they
appear only in a clearly labelled "Deprecated one-sided metrics" appendix.

| Key | Why it looked meaningful | Why it is retired |
|---|---|---|
| `early_miss_frac` (`p90 < T0`) | Read naturally as "the model was too fast / too pessimistic on the early side" — the mirror image of `late_miss`, together implying a two-sided [P10,P90] interval check. | **Structurally impossible.** Because the engine seeds at T0, p90 >= T0 ALWAYS holds for any reached cell (no modeled arrival — pessimistic or otherwise — can precede its own seed). `early_miss` is therefore 0 in every pool ever scored, including under a mutated 10x-wind config that is obviously too fast (tri-model review, finding A1). "0% early-miss" is a tautology of the replay design, not evidence the ensemble is well-centered or too wide. It can never move, so it can never be evidence of anything. |
| `covered_certain_frac` (`p10<=T0 AND p90>=T1`) | Read as "the [P10,P90] band fully contains the observation window" — the strict, high-confidence calibration check. | Requires `p10 <= T0`, which needs an arrival strictly before (or exactly at) the T0 seed — i.e. it needs the SAME structurally-impossible event as `early_miss`'s complement. Measured ~0 in every pool ever scored for the identical reason. |
| `covered_possible_frac` (`p10<=T1 AND p90>=T0`) | Read as "the band intersects the observation window" — the looser, still-two-sided calibration check. | The `p90>=T0` half is ALWAYS true (same structural fact as above), so this collapses EXACTLY onto `p10<=T1` — i.e. it IS `p10_adequacy_frac` (now `p10_not_after_window_frac` per-pair), just not labelled as the one-sided statistic it actually is. Verified as an exact identity (zero row mismatches) across every pool scored to date by two independent model families. Use `p10_adequacy_frac` and call it what it is. |

`p10_before_window_frac` (per-pair: `p10_before_window`, "p10 <= T0")
survives in the schema as the "sufficient bound" companion to
`p10_not_after_window` ("necessary bound"), but is likewise ~0 by
construction for the same reason and carries no information — treat it the
same as the three retired fields above even though it was not renamed.

## Why this happened (one paragraph)

A perimeter pair only brackets a newly-burned cell's true burn time inside
`(T0, T1]` — that is genuine interval censoring, and the CERTAIN/POSSIBLE
bound-pair framing was the right instinct for handling it honestly. The
mistake was pairing that framing with a replay design where the engine is
seeded exactly at T0: seeding removes half of the theoretical state space
(no arrival before T0 is even representable), so the "certain" and "early"
sides of the bound pair are not merely rare, they are unreachable. A
calibration check that cannot ever fail on one side is not a two-sided
check. The fix is not a different scoring formula — the arithmetic behind
every one of these numbers reproduced exactly under independent
re-derivation (tri-model review, section 0) — it is reporting the
statistics that CAN move (`late_miss`, head/area ratios, PIT shape, the
null-model control, window-length and incident breakdowns) as the
evidence, and keeping the ones that cannot move out of the evidence base.

## Running a scorecard

```
py -3 tools/hindcast_harness.py replay --data-dir tools/hindcast_data ^
    --grass-floor 0.10 --floor-gate --model-error 0.71 ^
    --out grass_floor_variant.json --label grass_floor_variant
py -3 tools/hindcast_harness.py report --data-dir tools/hindcast_data ^
    --results grass_floor_variant.json --report docs/calibration/grass_floor_variant.md
```

The default invocation (`replay` / `report` with no flags) is unchanged —
it reads/writes `replay_results.json` and `docs/calibration/hindcast_first_pass.md`
exactly as before fix-program item 5, with additional keys (`schema:
"hindcast-replay/2"`, `label`, `config`, and the new `PairMetrics` fields)
that old readers ignore.
