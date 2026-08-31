# LUNA PASS-2 VERIFICATION — STATISTICS / OPERATIONAL REALISM

Review time: 2026-08-31. Verdicts apply to Grok's A1–A6 and C.

## Method and scope

I did not use `scratch_review/rvgrok_*_out.json`. I inspected each JSON schema and recomputed from the committed row-level fields. Python was unavailable exactly as warned: `py -3 --version` returned **“No installed Python found!” (exit 112)**. All offline arithmetic therefore used PowerShell `ConvertFrom-Json`; `gel_life_h` was hand-traced from source rather than imported. Reproducible work is in [rv2stats_derive.ps1](C:/Users/colin/wg_response/scratch_review/rv2stats_derive.ps1) and [rv2stats_derive_out.json](C:/Users/colin/wg_response/scratch_review/rv2stats_derive_out.json).

Windows Schannel also failed on the live hosts. I used Node's independent TLS stack for the same three authorized unauthenticated GETs, with redirects disabled and no retries; raw bodies and timestamps are in [rv2stats_live_node_out.json](C:/Users/colin/wg_response/scratch_review/rv2stats_live_node_out.json). No other endpoint was queried.

## Per-finding verdicts

### A1 — CONFIRMED

Source: [aug_replay.json](C:/Users/colin/wg_response/scratch_review/hindcast_aug_data/aug_replay.json), rows with `variant="as-deployed"` and `n_new_burnable>0` (18 pairs, 135,081 cells), using `covered_possible`, `p10_not_after_window`, `late_miss`, `early_miss`, `p10_before_window`, `covered_certain`, `head_m_p50`, `obs_head_m`, `modeled_new_area_ac_p50`, and `obs_new_area_ac`.

- Structural zero-early claim: **confirmed**. Sums are `early_miss=0`, `p10_before_window=0`, and `covered_certain=0`. There are zero row mismatches for `covered_possible == p10_not_after_window` and zero for `late_miss + covered_possible == n_new_burnable`; both identities also hold pooled exactly.
- The mechanism is source-level, not empirical calibration: the replay seeds at T0 in [hindcast_lib.py:568](C:/Users/colin/wg_response/tools/hindcast_lib.py:568), defines newly burned cells outside the engine-T0 mask at [hindcast_lib.py:580](C:/Users/colin/wg_response/tools/hindcast_lib.py:580), and scores `early_miss` as `p90<T0` at [hindcast_lib.py:629](C:/Users/colin/wg_response/tools/hindcast_lib.py:629). Thus “0% early” is a tautological consequence of this replay design and is not evidence of a calibrated two-sided interval.
- August mean head ratio: Grok **2.87×**; derived **2.866456×** from all 18 pair ratios. Confirmed.
- Tartar-ID 48 h: Grok **2.48× AREA**; derived `dt_h=48.085556`, observed `100,998.394 ac`, modeled `250,692.586 ac`, ratio **2.482144×**. Confirmed.
- Bug-CA +12.97 h: Grok **6.66× AREA**; derived `dt_h=12.966667`, observed `14,062.916 ac`, modeled `93,674.710 ac`, ratio **6.661116×**. Confirmed.

The raw arithmetic supports Grok's attack: `coverage_possible` is exactly a one-sided P10-by-T1 statistic here.

### A2 — CORRECTED

The four bin rows are correct; Grok's combined `>12 h` value and weighted prose are not exact. Source fields are `dt_h`, `n_new_burnable`, `covered_possible`, and `late_miss` in [aug_replay.json](C:/Users/colin/wg_response/scratch_review/hindcast_aug_data/aug_replay.json).

| `dt_h` bin | Pairs | Cells | Cell share | Derived cover / late | Grok |
|---|---:|---:|---:|---:|---:|
| `(6,12]` | 5 | 27,089 | 20.0539% | **69.7922 / 30.2078%** | 69.792 / 30.208% |
| `(12,24]` | 4 | 24,164 | 17.8885% | **99.2013 / 0.7987%** | 99.201 / 0.799% |
| `(24,48]` | 6 | 33,130 | 24.5260% | **99.9879 / 0.0121%** | 99.988 / 0.012% |
| `(48,96]` | 3 | 50,698 | 37.5316% | **99.9803 / 0.0197%** | 99.980 / 0.020% |

There is no scored pair with `dt_h<=6`. Combined `dt_h>12` is **107,785/107,992 = 99.808319% cover**, with 207 late cells—not `~99.99%`. The exact reconciliation is `0.2005389×69.7921666% + 0.7994611×99.8083191% = 93.7889118%`. Grok's prose used `~99.99%` and consequently wrote 93.94% before hand-waving it to 93.79%; that decomposition is wrong even though his four bin rows are right.

Leave-one-out is confirmed for the unit Grok named: dropping only the Bug 9.486 h pair gives **120,541/121,487 = 99.221316%**, Grok **99.22%**. Pair-cell concentration is also confirmed: HHI **0.186530**, largest pair **36.8757%**, top three **60.0299%**.

### A3 — CORRECTED

The requested numerical reversal is exact, but Grok mislabels pair-weighting as incident-weighting. Source: [oos_realism.json](C:/Users/colin/wg_response/scratch_review/hindcast_oos_data/oos_realism.json), rows with `variant="grass_floor"`, using `incident`, `n_new_burnable`, `covered_possible`, and `late_miss`.

- Cell-weighted: Grok **64.77 / 35.23%**; derived **39,023/60,249 = 64.769540% cover**, **21,226/60,249 = 35.230460% late**.
- Pair mean over eight windows: Grok **92.15 / 7.85%**; derived **92.153849 / 7.846151%**. Median pair coverage is **100%**.
- Pair-cell HHI: Grok **0.352**, largest share **56.25%**; derived **0.351567**, largest **56.246577%** (Porcupine Ridge, 33,888 cells), top-three share **77.407094%**. Confirmed.
- Correction: the eight rows contain only **four distinct `incident` IDs**, not eight incidents. Equal-incident aggregation is **84.335662% cover / 15.664338% late**, not 92.15/7.85. Grok's sentence that pair-weighting answers “what fraction of incidents were fine (7/8)” is false; it answers what fraction of observation windows were fine and weights Kaiser Canyon three times, Brewer and Stookey twice each, and Porcupine once.

### A4 — CONFIRMED

Source: [aug_hindcast_discover.json](C:/Users/colin/wg_response/scratch_review/aug_hindcast_discover.json), fields `all`, `kept`, and per-candidate `name`, `state`, `n_rows`, `max_acres`, `span_h`, `kept`.

- Candidates: Grok **94**; derived **94**.
- Kept: Grok **61**; derived **61** both from `kept.Count` and `all | kept=true`; dropped **33**.
- The rule encoded by the artifact is exactly `n_rows>=3 AND span_h>=6`: zero classification mismatches.
- `BIG GRASS` (US-OR): Grok **575,163 ac, `n_rows=1`, dropped**; derived `max_acres=575,163.452385`, `n_rows=1`, `span_h=0`, `kept=false`. Confirmed.

The selection-bias inventory is real. The causal gloss is not proven by this file: `max_acres` is cumulative size at one snapshot, so the artifact proves that a huge incident was unscorable and excluded; it does not establish how fast BIG GRASS grew.

### A5 — CORRECTED (minor percentile correction; all counts and gel-life values confirmed)

Source: [probe_leadtime_lead_c8d10.json](C:/Users/colin/wg_response/scratch_review/probe_leadtime_lead_c8d10.json), flattening `rows[].homes[]` and using `gelled` and `arr_h`.

- Total homes **2,400**; truth-reached (`arr_h != null`) **1,082**; gelled **1,056**; gelled and truth-reached **632**; gelled and never truth-reached **424**. Grok's inventory is exact.
- Among the 632 gelled+reached homes: `arr_h>6` is **598 = 94.620253%**; `>24` is **394 = 62.341772%**; `>72` is **47 = 7.436709%**. Grok's 94.62%, 62.34%, and 7.44% are exact rounded values.
- Type-7 percentiles are min **1.536 h**, p10 **9.9005 h**, p50 **32.975 h**, p90 **69.5968 h**, mean **36.177714 h**, max **90.662 h**. Grok's min/9.9/33.0/mean 36.2/max 90.7 are right; his p90 **69.5** should be **69.6** under the standard interpolated convention. The lower order statistic is 69.505 h, which explains his figure.

Python could not import the function, so I hand-traced the exact formula at [safety.py:205](C:/Users/colin/wg_response/wgresponse/safety.py:205): `max(1, 6*exp(-0.08*(T-25))*(RH/50)^0.3/(1+0.05*wind))`.

| Inputs `(°C, RH%, m/s)` | Raw formula | Returned | Grok |
|---|---:|---:|---:|
| `(25,50,0)` | 6.000000 h | **6.000000 h** | 6.0 h |
| `(35,20,5)` | 1.638414 h | **1.638414 h** | 1.64 h |
| `(33,12,8)` | 1.472781 h | **1.472781 h** | 1.47 h |
| `(38,8,15)` | 0.699331 h | **1.000000 h floor** | 1.0 h |

These are arrival-from-detection statistics, not measured coat-age-at-arrival statistics: the JSON has no `gelled_at_s`. Grok correctly calls 62.3%/94.6% policy bounds; the file cannot directly prove which gelled homes had expired.

### A6 — CONFIRMED

Wind-curve arithmetic was recomputed from raw `rows[].homes[]`, defining the committed e1000 belt as `front==true OR (dist!=null AND dist<=1000)`, then counting `gelled`.

| Wind preset | c4 derived | c8 derived | Grok |
|---|---:|---:|---:|
| low, 8 mph | 189/215 = **87.9070%** | 191/215 = **88.8372%** | 87.91 / 88.84% |
| moderate, 18 mph | 173/217 = **79.7235%** | 182/217 = **83.8710%** | 79.72 / 83.87% |
| high, 28 mph | 94/155 = **60.6452%** | 107/155 = **69.0323%** | 60.6 / 69.0% |
| extreme, 40 mph | 70/166 = **42.1687%** | 74/166 = **44.5783%** | 42.2 / 44.6% |

The moderate rows also independently reproduce Grok's safety counts: c4 `viol=4`, `ov_ticks=14`; c8 `viol=9`, `ov_ticks=13`. Raw files are `census_wind_{low,moderate}_c{4,8}.json` and `opt_ember_flip_c{4,8}.json` under `scratch_review/`.

RSF city-limits raw-row e1000 re-derivation is also exact: [c8](C:/Users/colin/wg_response/scratch_review/rsf_city_c8_scatter.json) **1,013/1,365 = 74.212454%**; [c10](C:/Users/colin/wg_response/scratch_review/rsf_city_c10_scatter.json) **1,048/1,365 = 76.776557%**; [c12](C:/Users/colin/wg_response/scratch_review/rsf_city_c12_scatter.json) **1,065/1,365 = 78.021978%**. Each file has 18×100 homes and params `deploy=10`, `ember_radius=200`, `hold=24`.

Source spot-checks:

- [simui.py:232](C:/Users/colin/wg_response/wgresponse/simui.py:232) defines low/moderate/high/extreme at exactly **8/18/28/40 mph** and [simui.py:1975](C:/Users/colin/wg_response/wgresponse/simui.py:1975) feeds those values into weather. All four benchmark labels match production simulator source.
- [simui.py:451](C:/Users/colin/wg_response/wgresponse/simui.py:451) tags every gelled home as `saved` or `protected_then_impacted`; no stochastic 0.85 survival draw is applied. Grok is right that these tables measure coating rate, not P(save).
- [contracts.py:847](C:/Users/colin/wg_response/wgresponse/contracts.py:847), [contracts.py:1029](C:/Users/colin/wg_response/wgresponse/contracts.py:1029), and [contracts.py:1065](C:/Users/colin/wg_response/wgresponse/contracts.py:1065) give live `ResponseConfig` defaults `deploy_min=30.0`, `dispatch_hold_h=0.0`, `wind_gust_blend=0.0`, `ember_threat_radius_m=0.0`, `grass_ros_floor_frac=0.0`, and `extreme_regime_enabled=false`.
- The watch path constructs a fresh `ResponseConfig()` and makes exactly three possible config replacements: `--gust-blend` → `wind_gust_blend` at [cli.py:873](C:/Users/colin/wg_response/wgresponse/cli.py:873); `--grass-floor` → `grass_ros_floor_frac` at [cli.py:883](C:/Users/colin/wg_response/wgresponse/cli.py:883); `--extreme-regime` → `extreme_regime_enabled=true` at [cli.py:894](C:/Users/colin/wg_response/wgresponse/cli.py:894). There are no later config mutations in `_cmd_watch`. `--period` selects a local loop `period_s`; registry/terrain/state/notify flags select operational components. They do not change `deploy_min`, `dispatch_hold_h`, `ember_threat_radius_m`, `gel_window_h`, or `gel_env_model_enabled`, which remain at defaults.

Grok's live-config gap claim is exact.

### C — CORRECTED (time-sensitive values updated; core failure and unauthenticated state confirmed)

Exactly the three requested endpoints were successfully fetched at 2026-08-31 23:21:46–47Z; see [raw live output](C:/Users/colin/wg_response/scratch_review/rv2stats_live_node_out.json).

- [Watcher healthz](https://y372zmpfuj.us-west-2.awsapprunner.com/healthz): HTTP 200, `ok=true`, `degraded=false`, `cycles_run=352`, `last_cycle_t_s=1788215666.9265664` (22:34:26.926Z), health payload `last_cycle_age_s=2840.6819` (**47.34 min**). `poll_period_s=300` comes from state, so staleness is **9.47 cadence periods** while health remains green.
- [Simulator healthz](https://m897yqgsmt.us-west-2.awsapprunner.com/healthz): HTTP 200, exactly `{"ok":true}`. It exposes no cycle, cadence, or staleness field.
- [Watcher state](https://y372zmpfuj.us-west-2.awsapprunner.com/api/state): an unauthenticated GET with no cookie or authorization header returned **HTTP 200** and **51,118 bytes** of state. Auth posture confirmed: state is world-readable.
- State fields at receipt: `cycles_run=352`, `poll_period_s=300`, `last_cycle_age_s=2840.9231`, `degraded=false`, `started_at_s=1787712568.9372928`, `restored=false`. Derived uptime **140.5384 h**, expected cadence count **1686.46**, actual/expected **20.872%**, mean elapsed time per completed cycle **1437.32 s = 4.791×** the target. Last plan `solver_s=160.8034`.

Compared with Grok's 22:43–22:46Z check: cycles advanced **351→352** and the last-cycle timestamp advanced from 22:09:55Z to 22:34:26Z, but by this check the loop was again staler—**47.34 min**, versus his 33–36 min. His exact snapshot is obsolete; his conclusion is confirmed and stronger now: the loop is missing the 15-minute gate while `degraded=false`.

## Claude–Grok adjudication

- **Structural early-zero:** tie. Both are correct. The file has 38 raw variant rows, but 36 scored nonzero-cell rows (18 per variant), explaining Claude's “36” and Grok's 18 as-deployed pairs.
- **Bug leave-one-out:** **Grok wins for the operation requested here.** Drop the single 9.486 h pair → **99.221316%**. Claude's **99.317139%** is also arithmetically correct, but it drops all four Bug incident pairs. Those are different estimands, not competing computations.
- **Tartar share:** both are correct on different units. Grok's single 48.086 h pair is **36.8757%** of cells; Claude's whole three-pair Tartar incident is **65,296/135,081 = 48.3384%**. Dropping the whole incident gives Claude's **88.006018%** coverage.
- **August median head ratio:** **Claude wins.** The committed pooled field/Grok table reports upper-middle `sorted[n//2] = 1.880425×`; with 18 ratios, the mathematical median is the average of the two middle values, **1.836820×**. Grok's A1 mean **2.866456×** remains correct.
- **August area inflation:** Claude's independently claimed pooled **6.061332×**, median-pair **8.904864×**, and max **120.5×** all reproduce from `modeled_new_area_ac_p50 / obs_new_area_ac`. They complement rather than contradict Grok's named-pair 2.48× and 6.66× values.
- **Pair mean as incident mean:** neither report supplied the correct July equal-incident figure. Grok's semantic statement is refuted: eight pairs represent four incidents; the incident mean is **84.335662 / 15.664338%**.

## New findings missed by both reviewers

1. **July's “8 pairs” are only 4 incidents.** Equal-incident coverage is **84.34%**, between cell weighting (64.77%) and pair weighting (92.15%). Calling 92.15% an incident success rate materially overstates the incident-level result.
2. **HHI implies tiny effective sample sizes.** `1/HHI` is **2.844 windows** for July despite 8 rows and **5.361 windows** for August despite 18 rows. After aggregating by incident, effective N falls to **2.414** for July and **3.244** for August. Cell count is not inferential sample size.
3. **The 6–12 h failure is itself one-pair concentrated.** Bug 9.486 h contributes **7,444/8,183 = 90.969%** of all late cells in that bin. Remove it and the other four short windows are **12,756/13,495 = 94.5239% cover / 5.4761% late**, not 69.8/30.2. The corpus supports a two-regime claim, not a stable short-window rate.
4. **Grok's discovery-drop inventory leaves five drops unexplained.** Beyond 18 with `n_rows<=1` and 10 additional zero-span rows, the remaining five are MITCHELL SPRINGS (2 rows, 9.294 h), Burnt Creek (2 rows, 8427.483 h), Big Buffalo (3 rows, 0.0000436 h), Slough Grass (2 rows, 29.546 h), and S-1 VYA (2 rows, 1 h). Burnt Creek's 351-day span and Big Buffalo's sub-second span are data-quality red flags, not ordinary cadence misses.

## Five-line aspect summary

1. Early-miss 0% is structurally forced; August “coverage” is exactly P10-by-T1 and one-sided.
2. August is 69.79% covered in 6–12 h windows and 99.808% after 12 h; the 93.79% headline is window-length dominated.
3. July is 64.77% cell-weighted, 92.15% pair-mean, but only 84.34% equal-incident across four incidents.
4. Gel sensitivity counts and 1.0–6.0 h source formula are real; the benchmark stores no coat time and live leaves ember/deploy/hold at 0 m/30 min/0 h.
5. Live watcher state is unauthenticated and the loop is 47.3 min stale at a 5 min target while `degraded=false`; simulator health exposes no cadence telemetry.
