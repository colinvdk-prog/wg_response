# GROK independent assessment — statistics / operational realism / live-ops

Repo `C:\Users\colin\wg_response`. Review date 2026-08-31. Read-only on `wgresponse/` and `docs/`. Derivations: `scratch_review/rvgrok_stats_audit.py`, `rvgrok_gel_lead_ops.py`, `rvgrok_live_and_rsf.py` (+ `*_out.json`). Arithmetic below is recomputed from committed JSONs, not copied from the handoff table.

---

## 0. Table re-derivation (cell-weighted, n_new_burnable > 0)

Identity used everywhere: `cover = covered_possible / n`, `late = late_miss / n`, `early = early_miss / n`, median head-ratio = median of `head_m_p50 / obs_head_m` over pairs with `obs_head_m > 0` (same as `aug_hindcast_replay.py` / `hindcast_lib.pool_metrics`).

| Pool | Source | pairs / cells | cover | late | early | med head | vs handoff |
|---|---|---|---|---|---|---|---|
| Cal blend 0.5 | `cal_realism.json` variant `blend0.5` | 14 / 60,062 | 73.519% | 26.481% | 0 | 1.772× | 73.5 / 26.5 / 0 / 1.77× **match** |
| Cal + floor | `cal_realism.json` variant `grass_floor` | 14 / 60,062 | 91.485% | 8.515% | 0 | 2.776× | 91.5 / 8.5 / 0 / 2.78× **match** |
| July OOS blend 0.5 | `replay_b0.5.json` pairs with `t0 >= 2026-07-13` | 8 / 60,249 | 48.640% | 51.360% | 0 | 1.262× | 48.6 / 51.4 / 0 / 1.26× **match** |
| July + floor | `oos_realism.json` variant `grass_floor` | 8 / 60,249 | 64.770% | 35.230% | 0 | 3.388× | 64.8 / 35.2 / 0 **match** (head not claimed) |
| July floor excl. Porcupine | same, drop name contains porcupine | 7 / 26,361 | 99.970% | 0.030% | 0 | 3.388× | ≈100 / ≈0 **match** |
| **Aug as-deployed** | `hindcast_aug_data/aug_replay.json` variant `as-deployed` | **18 / 135,081** | **93.789%** | **6.211%** | **0** | **1.880×** | **93.8 / 6.2 / 0 / 1.88× match** |
| Aug calibration (no floor) | same, variant `calibration` | 18 / 135,081 | 77.189% | 22.811% | 0 | 1.098× | not in §4 table |

Aug per-fuel late (as-deployed, NB dropped): GR 3.667% (41,857) / GS 5.341% (58,432) / **SH 13.474% (26,955)** / TL 3.015% (3,018) / TU 0.228% (4,819). Handoff GR 3.7 / GS 5.3 / SH 13.5 / TL 3.0 / TU 0.2 — **match**.

Aug worst pair Bug-CA 2026-08-10 23:32: 13,594 cells, dt 9.486 h, obs head 10.174 km, p50 head 6.973 km, ratio **0.685×**, late **54.759%**. Handoff 10.2 km / 9.5 h / 0.69× / 54.8% — **match**.

July Porcupine blend 0.5: 33,888 cells (56.247% of pool), cover 17.180%, late 82.820%, never_reached 19,282, head ratio 0.076× (= 13.1× under). Floor: cover 37.4%, late 62.6%, ratio 0.46×.

**The published pooled numbers survived byte-level re-derivation.** The attacks below are about what those numbers *mean*.

---

## A. Findings that weaken claimed numbers (ranked)

### A1. “0% early-miss in every pool ever scored” is structurally guaranteed — not a calibration result

Scoring (`tools/hindcast_lib.py:119-126, 632-645`): engine is seeded at T0; arrivals are T0 + travel. `early_miss := p90 < T0`. `p10_before_window := p10 <= T0`. `covered_certain := p10 <= T0 AND p90 >= T1`.

The method note already says p10≤T0 is ~0 **by construction** (`docs/calibration/hindcast_first_pass.md:11`). Because p90 ≥ p10, `early_miss` is a stricter event than an already-impossible one.

Measured on every pool I scored: `early_miss = 0`, `p10_before_window = 0`, `covered_certain = 0`. Also: `covered_possible == p10_not_after_window` and `late + cover == n` exactly (sum_check residuals ≤ 2e-17). So **“coverage” ≡ “P10 arrived by T1” ≡ 1 − late_miss**. It is not an 80% P10–P90 interval calibration. A well-calibrated 80% band would show ~10% early and ~10% late; this design cannot produce early.

The 0% early-miss record **survives as arithmetic and dies as evidence** that the ensemble is not too wide / the model is not too fast. Over-forecast is visible in other stats the headline suppresses: Aug as-deployed median head 1.88×, **mean head 2.87×**; Tartar-ID 48.1 h window (49,812 cells, 36.9% of Aug) head 1.18× but **area 250,693 / 100,998 = 2.48×**; Bug pair +12.97 h head 0.99× but **area 93,675 / 14,063 = 6.66×**.

### A2. August 93.8% is a long-window average. Sub-12 h windows are 69.8% / 30.2% late

As-deployed, cell-weighted by observation-window length (`rvgrok_stats_audit_out.json` `august_window_bins`):

| dt_h | pairs | cells | share of 135,081 | cover | late |
|---|---|---|---|---|---|
| (6,12] | 5 | 27,089 | 20.05% | **69.792%** | **30.208%** |
| (12,24] | 4 | 24,164 | 17.89% | 99.201% | 0.799% |
| (24,48] | 6 | 33,130 | 24.53% | 99.988% | 0.012% |
| (48,96] | 3 | 50,698 | 37.53% | 99.980% | 0.020% |

Decomposition: `0.2005×69.792 + 0.7995×~99.99 ≈ 14.00 + 79.94 = 93.94%` ≈ 93.79%. The headline is ~80% of the cells sitting in dt>12 h windows that a non-catastrophic forecast cannot miss, plus Tartar’s 48.1 h / 49,812-cell 100% cell (36.9% of the pool, HHI 0.187, largest-share 36.9%, top-3 60.0%).

Leave-one-out: drop Bug-CA 9.5 h pair (10.06% of cells, 54.76% late) → rest cover **99.22%** (Δ +5.43 pp). **5.43 of the 6.21 pp of August late-miss is one 9.5 h pair.** Operational lead times that matter for gel deploy (see A5) live in the 6–12 h bin, where late is **4.9× the headline 6.2%**.

No August pair has dt≤6 h. The scoring corpus has no first-burn-period windows.

### A3. Pair-weighted pooling does not change August; it *reverses* July

| Pool | cell-weighted cover / late | pair-mean cover / late | median pair cover |
|---|---|---|---|
| Aug as-deployed | 93.79 / 6.21 | 95.66 / 4.34 | **100.0** |
| July blend 0.5 | **48.64 / 51.36** | **83.11 / 16.89** | 95.81 |
| July + floor | **64.77 / 35.23** | **92.15 / 7.85** | **100.0** |
| July floor excl. Porcupine | 99.97 / 0.03 | 99.98 / 0.02 | 100.0 |

July HHI 0.352, largest share 56.25% (Porcupine). Pair-weighting answers “what fraction of *incidents* were fine” (7/8 ≈ 100% with floor). Cell-weighting answers “what fraction of *newly-burned ground* was covered.” The product fails if it misses the fire that runs 22.5 km in 12.9 h. Cell-weighting is the honest pooling for that; the handoff already shows the 7-window line. What the handoff does **not** say: **quoting 93.8% next to 64.8% as if they were the same estimand hides that August is a no-blow-up month, and pair-weighting would have reported July as 92%.**

July blend 0.5 `never_reached` = 32.007% of cells (19,282 of them on Porcupine). Floor cuts never_reached to 7.031% and lifts cover to 64.77% — the floor works by making grass *go far enough to intersect the window*, not by calibrating the band. July floor `p90_unreached` = 9.359% (5,639 cells): a slice of “coverage” is the band `[p10, +∞)`.

### A4. Selection + suppression: 93.8% is an upper bound on coverage for the fires that destroy homes

`aug_hindcast_discover.json`: 94 WFIGS candidates (already `min_acres=1000`, 8 states, since 2026-08-01), **61 kept / 33 dropped**. Kept: min 1,056 ac, p50 8,993, mean 41,646, max 355,121. Dropped: 18 with `n_rows≤1`, 10 more with `span_h=0`. Largest dropped: **BIG GRASS 575,163 ac, n_rows=1, span=0** (OR); Lee 123,223 ac; Aspen Acres 102,003 ac. The monthly skill (`~/.claude/scheduled-tasks/monthly-livefire-model-review/SKILL.md`) keeps `n_rows≥3` and `span≥6 h`.

That filter is: large, repeatedly mapped, long-lived incidents. Those are the fires with crews on them. Suppression is unmodeled (`calibration_review_2026-07-31.md` §1.8). Suppression shrinks observed growth → reduces late-miss → **inflates coverage**. The existence proof is in-sample: Porcupine (the uncontained grass blow-up) is 37.4% cover with the *deployed* floor; every well-fought August window except Bug is ~100%. August has no Porcupine-class event (flag gate head-ratio<0.3 or late>60%: Bug is 0.69× / 54.8%, under threshold).

WFIGS Daily-perimeter discovery also cannot see fires that are gone in one mapping interval. Fast WUI ignitions — the GraphGel use case — are structurally under-sampled. Quantified floor on optimism: treat Porcupine+floor (37.4%) as the blow-up-regime coverage and August (93.8%) as the fought-normal regime. The product-relevant mixture is not 93.8%. There is no estimated P(blow-up | WUI dispatch) in the repo; n=1 true positive for extreme-regime (`calibration_review` §1.7).

ERA5-gust blend transferred live onto NWS forecast gusts: still untested (same review §1.2). Direction unknown.

### A5. Gel 72 h vs ~6 h (or 1.0–1.6 h in fire weather) — the number that most changes the product story

Probe `scratch_review/probe_leadtime_lead_c8d10.json` (params crews=8, deploy=10; `probe_leadtime.py` sets `dispatch_hold_h=24`, default `gel_window_h=72` flat). 2,400 homes, 1,082 truth-reached, 1,056 gelled, **632 gelled+reached**, 424 gelled+never-arrived.

Among gelled+reached, `arr_h` (truth hours from detection): min 1.536, p10 9.9, **p50 33.0**, p90 69.5, mean 36.2, max 90.7.

| threshold | n of 632 | % of gelled+reached |
|---|---|---|
| arr_h > 6 h | **598** | **94.62%** |
| arr_h > 24 h | **394** | **62.34%** |
| arr_h > 72 h | 47 | 7.44% |

Shipped policy coats at the 24 h hold line when lead > 24 h, else ASAP. Remaining gel age at truth arrival ≈ `min(arr_h, 24)`. A 6 h window with **no policy change** is dead on every gelled+reached home with arr_h>6 h = **94.6%**. The 62.3% with arr_h>24 h were *designed* to be coated ~24 h before arrival (18 h past a 6 h expiry). Probe does not persist `gelled_at_s`, so 62.3% is the **lower bound** on “coated >6 h before arrival under shipped hold”; 94.6% is the **upper bound** if coats land at first opportunity.

`wgresponse.safety.gel_life_h`: 6.0 h at 25 °C / 50% RH / 0 m/s; **1.64 h** at 35/20/5; **1.47 h** at 33/12/8; **1.0 h floor** at 38/8/15. If the env model is even directionally right for this product, 72 h is not 12× too long — it is 45–72× too long *in the weather the fire is in*. `gel_env_model_enabled=False` live and in sim. `gel_effectiveness=0.85` is a placeholder and does not enter the shipped `homes` objective (`dispatch.py:288-291`; RSF plan says this correctly). Handoff “survivability-vs-wind” is **coating rate**, not P(save).

If the 6 h (or ~1.5 h) number is true and policy is rewritten to coat inside the last 6 h: that is a different product (day-of, inside the 30 min egress / deploy squeeze). The lead-time curve already says survival among reached is **0% at <1.5 h** (0/15), 13.3% at [1.5,2), 27.3% at [3,4), 42.3% at [4,6). Moving all work into that window collides with the fire clock that already bounds the fleet (`calibration_review` §4.5: saturation at ~10 crews, unsavable floor 145–147/321).

**Rank vs other assumptions, product-story impact:** (1) gel window 72 vs 6/1.5 h — changes *when* you deploy, inventory, crew count, and whether 74–78% coating is even physically possible; (2) suppression-off + WFIGS selection — changes whether 93.8% is the operational coverage; (3) `ember_threat_radius_m=0` live vs 200 sim — 63% of RSF-like WUI homes never enter the ladder; (4) live `deploy_min=30` vs sim 10/15 — measured +4.3 pp (15→10) / +8.8 pp (30→10) on 1,710 threatened homes (`calibration_review` §4.2); (5) smoke unmodeled — all transit verdicts thermal-only, unquantified, historically the real killer; (6) extreme-regime n=1 — false-arm is benign (more pessimistic). Circuity 1.7 + `route_margin_min=56` is already the calibrated pair; not the story-breaker the 2026-07-31 review thought.

### A6. Simulator claims are coating rates on ember_radius=200 / hold=24 / deploy=10. Live engine is 0 / 0 / 30

Wind-vs-belt (`census_axis_sensitivity.json` `wind_speed_curve` + `wind_mph`; belt = e1000):

| mph | c4 | c8 | viol c4/c8 |
|---|---|---|---|
| 8 | 189/215 = 87.91% | 191/215 = **88.84%** | 0 / 0 |
| 18 | 173/217 = 79.72% | 182/217 = **83.87%** | **4 / 9** (ovt 14 / 13, min margin 0.30 h) |
| 28 (high) | 94/155 = 60.6% | 107/155 = **69.0%** | (flip suite) |
| 40 (extreme) | 70/166 = 42.2% | 74/166 = **44.6%** | (flip suite) |

Handoff 8≈88, 18≈80–84, 28≈61–69, 40≈42–45 — **arithmetic survived**. Two weakenings: (i) these are **gelled-among-e1000**, not survivability (`gel_effectiveness` unused); (ii) 18 mph is the unscreened corridor regime with **4–9 crew-safety violations**. More crews made it worse (9>4). The “0 violations” corridor story is high/extreme-only.

Lead-time among truth-reached, `probe_leadtime_lead_c8d10.json`:

| bucket | n | gelled | % |
|---|---|---|---|
| [0, 1.5) | 15 | 0 | **0.0** |
| [3, 5) (~4 h) | 49 | 20 | **40.8** (handoff ~33 @4 h: [3,4)=27.3, [4,6)=42.3) |
| [10, 14) | 87 | 51 | **58.6** (handoff 50–63) |

Direction survived; the 33% @4 h is a bit low vs this file (use 27–42 depending on bin). 0% <1.5 h is exact.

RSF 100-home city-limits (`rsf_city_c{8,10,12}_scatter.json` stats.e1000, 18 seeds × 100 homes, ember 200, hold 24, deploy 10): **c8 1013/1365 = 74.21%, c10 1048/1365 = 76.78%, c12 1065/1365 = 78.02%**. Handoff 74–78% — **exact**. The `rsf_grid_*` (not city-limits) is 69.5–70.1% and must not be mixed in. Cluster placement on the same tile (`rsf_city_c10_cluster.json`) collapses threatened volume (1,374/1,800 `not_threatened`) — dispersion is load-bearing.

Live `ResponseConfig` (read from code, 2026-08-31): `ember_threat_radius_m=0.0`, `deploy_min=30.0`, `dispatch_hold_h=0.0`. Watch CLI (`cli.py:872-904`) arms gust-blend / grass-floor / extreme-regime only — **does not arm ember radius**. At RSF, 56–69/100 placed clients sit on NB cells, mean **63.2** (`docs/rancho_santa_fe_plan.md:594`). Live ladder never sees them. Simulator 74–78% is not a live number.

Perfect-info ceiling: `opt_ember_pi_c8d10.json` e1000 **233/321 = 72.59%, viol=7, ovt=45**. Matches the +22–26 pp band vs c8d10 base 161/321 = 50.16% (Δ +22.43 pp). Survived. Naive P10-collapse still kills crews.

---

## B. Claims that survived

1. Every §4 pooled hindcast percentage I was asked to re-derive **reproduces exactly** from the cited JSONs (cell-weighted).
2. Aug SH-worst late 13.5%; Bug-CA 10.17 km / 9.49 h / 0.685× / 54.76% late.
3. July two-regime split: 7 windows 26,361 cells ≈100% with floor; Porcupine 33,888 cells is the pool.
4. Early-miss is 0 in every scored pool (true; uninformative — A1).
5. Wind coating curve 88 / 80–84 / 61–69 / 42–45 and RSF city-limits 74–78% e1000 (sim config).
6. Lead-time 0% <1.5 h; 50–63% at 10–14 h (58.6% here).
7. Grass floor is the load-bearing live physics knob: Aug no-floor 77.2% → as-deployed 93.8%; July 48.6% → 64.8% (all Porcupine residual). Watcher startup_notes confirm `--gust-blend 0.5 --grass-floor 0.10 --extreme-regime` is actually armed.
8. SEM-10 / P10-egress as *laws* — not audited in this lane beyond noting the 18 mph violation census. PI collapse → viol=7 is consistent with “band is load-bearing.”
9. Stale-FIRIS guard is live: 14 incidents excluded at 168 h (`/api/state` notes). Fail-closed polarity on that path is real.

---

## C. Live-ops (queried 2026-08-31 ~22:43–22:46 UTC)

Watcher `https://y372zmpfuj.us-west-2.awsapprunner.com/healthz` → 200 `{"ok": true, "degraded": false, "last_cycle_t_s": 1788214195.4267356, "cycles_run": 351, "operator_event_count": 0}`. `last_cycle_age_s` **1990 s → 2155 s** across repeats; `last_cycle_t_s` **frozen** at 2026-08-31 22:09:55Z. HTTP process is alive; **watch loop is stalled ~36 min** against `poll_period_s=300`. Healthz does not map stall → `degraded`. LIVE_OPS exit gate “zero missed cycles > 15 min” is currently failing, and the probe cannot see it as a 503.

`/api/state` is **unauthenticated 200**, 51 kB (Dockerfile `ENV WGRESP_DEV_OPEN=1` is inherited by the watch service; `cli.py:956-961` only *labels* DEV-OPEN, does not refuse). Payload: 12 `mock-client-*`, **0 crews**, 4 **UNVERIFIED** safe zones, 26 incidents, 19 ladder events (NOTICE/WATCH/EXTREME/IMPACTED/cleared on mocks), `restored: false`, terrain cache **`/tmp/wgstate/terrain_cache`**, FIRMS off (no `FIRMS_MAP_KEY`). Uptime since `started_at_s=1787712568.94`: **139.34 h**. Expected cycles at 300 s: 1,672. Actual 351 = **21.0%**. Mean interval 1,429 s (4.8× the 5 min target). Last plan `solver_s=160.8` s — solver alone is 2.7 of the 5 min budget; LFPS on top explains the 24 min mean. Two WATCH clients unserved `reason=capacity` with an empty crew roster.

Ephemeral state: `restored:false` + `/tmp/wgstate` confirms the handoff’s ~37 Aug restarts / `/tmp` blocker. I did not have CloudWatch; I cannot independently count 37. I can confirm the mechanism is still in production.

Simulator `https://m897yqgsmt.us-west-2.awsapprunner.com/healthz` → 200 `{"ok": true}` (no cycle fields; simui has no watch loop). `/simulator` 200 HTML.

Nightly hindcast job (`LIVE_OPS_PLAN` 1.4): **not built**. Monthly procedure exists as a human skill (`monthly-livefire-model-review/SKILL.md`), not an App Runner cron. Open-Meteo free tier remains non-commercial — a production nightly job would not be lawful on the current weather path.

---

## D. Top-3 next actions

1. **Gel supplier data, then re-score every coating table under a 6 h (and env-scaled) window with hold rewritten.** Until then, do not quote 74–78% / wind curves as survivability, and do not run 24 h hold in any client-facing posture. Persist `gelled_at_s` on the next probe so coat-to-arrival is measured, not bounded.
2. **Durable watch state + stall SLO.** Move `StateStore` off `/tmp` (EFS/Dynamo). Make `/healthz` 503 when `last_cycle_age_s > 2×poll_period_s` (today: ok/degraded lie). Kill `WGRESP_DEV_OPEN` on the watch service. Fix the cycle budget (solver 161 s + LFPS vs 300 s period — currently 4.8× slow and presently *stuck*).
3. **Score the next hindcast on dt≤12 h cells as the primary metric; keep cell-weighting; accumulate blow-ups until extreme-regime has n≥5.** Publish P50-in-window and median/mean head next to “coverage.” Arm `ember_threat_radius_m` (300 m at RSF; 200 m elsewhere) on the live watcher before any real client — 63% NB-cell invisibility is a detection hole, not a conservatism.

Honorable: replace `time.monotonic` solver deadline (noise ±1 belt / ±6 coated); don’t collapse the ensemble.

---

## E. Fitness-for-purpose

**Shadow now: conditional yes, currently red.** The engine is running live SoCal AOI, flags match the as-deployed physics, FIRIS-stale guard works, mock ladder has exercised NOTICE→WATCH→EXTREME→IMPACTED→cleared. But: loop stalled 36 min with healthz green, state in `/tmp` and `restored:false`, `/api/state` world-readable, 0 crews, mock clients, unverified zones, FIRMS off, cycle cadence 5 min in name only.

**Clients after blockers:** gel window measured (or env-model armed with a re-bench), durable state + stall alarm + DEV-OPEN off, ember radius on, real registry + GPS crews + verified zones, cycle p95 ≤ 15 min, and a dt≤12 h hindcast scorecard that is not 30% late. The 74–78% / 93.8% numbers are not client-facing until (1) and the short-window score exist.

Shadow-vs-client: **shadow is the correct current mode. It is not a healthy shadow today.**
