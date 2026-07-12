# Crews Caught In The Fire — Continuous Crew-Safety Audit Gap

**Status: CONFIRMED (top-priority safety bug). Diagnosis only — no product code changed.**
Founder repro: live Paradise session sim-9, extended zone, 5 crews, tick 177 —
crews g1 & g2 visibly INSIDE the fire, all 5 crews `available` / 0 stops,
`scoreboard.violations = 0`.

Reproduced in-process (no `:8878` server touched) via `SimSession`:
- `scratch_review/diag_caught_repro.py` — the founder's exact scenario (Paradise
  extended, 64 clients, 5 crews, extreme fire).
- `scratch_review/diag_caught_caseb.py` — low-churn sweep that isolates case (b).

> Line numbers below are **as read during this diagnosis**. `simui.py` is being
> edited concurrently (out-of-region crew tiers `n_crews_1h`/`n_crews_3h` were
> landing during this review), so cite the **function names + code patterns**;
> the numbers will drift.

---

## 1. Confirmed mechanism (with numbers)

The per-tick SAFETY AUDIT governs **deployments** — arrive → gel → depart-before-P10
plus the egress corridor — but **never re-validates a crew that has stopped
deploying**. A crew that returns to `AVAILABLE` (or is still `EN_ROUTE` / `EGRESSING`)
has its **current cell** compared to the advancing fire by nothing. Static safe
zones are never invalidated as the fire grows. So an idle crew is overrun and the
scoreboard stays green.

**The audit gap (`_run_audit`, `simui.py` ~L2267):**

```python
def _run_audit(self) -> None:
    ...
    for st in self._serviced:          # <-- iterates SERVICED STOPS only
        ...
        latest = eta.eta_p10_s - (buf_min + ember_min) * 60.0
        margin_h = ... (latest - st.depart_s) / 3600.0
        entry = SafetyAudit(..., violation=(finite and margin_h < 0.0))
    self._violations = violations       # scoreboard.violations = len(this)
```

`self._serviced` is a list of `ServicedStop` records (a completed gel job's
`depart_s`). The loop asks one question per record: *did the crew leave that job
before P10-minus-buffer?* It has **no term for a crew's live position**, no term
for an **idle** crew, and no term for a crew **whose resting cell the fire later
reaches**. `scoreboard.violations = len(self._violations)` (`simui.py` ~L2624,
`state()`), so once a crew goes idle it is invisible to the count forever.

**The only other continuous safety checks are all gated on `_mode == "deploy"`:**

| Check | Location | Guard |
|---|---|---|
| Road-egress departure guard | `_SimCrew._road_depart_check` ~L1013 | `self._mode != "deploy" ... return False` |
| In-flight re-check (policy/eta-drift/road) | `_recheck_deploy` ~L1776 | `cs._mode != "deploy" ... return False` |
| Per-tick 2b safety re-check | `tick()` ~L1972 | `if cs._mode != "deploy" or not cs.itinerary: continue` |

Every safety re-validation short-circuits the instant a crew is not mid-deploy.
There is **no branch anywhere** that samples `cs.pos` against a fire arrival
raster for an `idle` / `travel` / `egress` crew.

### Reproduction output (`diag_caught_repro.py`)

Config: `paradise_ca` extended, 64 clients, 5 crews, extreme fire ignited NE
(upwind of the canonical FROM-45° foehn) at (39.83, −121.52); default cfg
(900 s/tick, 30-min egress buffer, 120 h horizon, 90 m cells, roads on).

Earliest idle-crew-in-fire while `violations = 0`: **tick 27 (6.8 h elapsed)**

```
sim-g1: status=available mode=idle BURNED(in fire) nom_arr_h=-2.02 p10_arr_h=-4.97
        nearest_zone=paradise_ca-z3@13531m anchor=paradise_ca-z1 egress_zone='' itinerary=0
```

`nom_arr_h = -2.02` means the **nominal ensemble member (sim ground truth)**
reached g1's cell 2.0 h ago — g1 is standing in the burn. `violations = 0`.

**End-state — the founder's report signature (tick 320, 80 h):**

```
violations=0   crews available&idle=5/5   of which in/near fire=5
  sim-g1: available stops=0 egress_zone='' anchor=z1  12.6km from z3 -> imminent (p10 -27.7h)
  sim-g2: available stops=0 egress_zone='' anchor=z2  14.7km from z3 -> BURNED   (nom -0.2h)
  sim-g3: available stops=0 egress_zone='' anchor=z3  14.5km from z3 -> imminent (p10 -25.9h)
  sim-g4: available stops=0 egress_zone='' anchor=z1  13.7km from z3 -> imminent (p10 -27.6h)
  sim-g5: available stops=0 egress_zone='' anchor=z2  11.7km from z3 -> imminent (p10 -26.8h)
```

**Persistence / invariant check:** across the whole run, **284 ticks had ≥1
exposed crew and the MAX `violations` seen across all of them was 0.** g1 & g2
(the two crews the founder named) are the deepest in the burn.

> Exposure metric definitions, computed from the **same engine samplers the audit
> uses**: *BURNED* = nominal member arrival `geo.raster_sample(ens.members[0],
> pos) <= now` (sim ground truth); *IMMINENT* = `spread.eta_at(ens, pos,
> fallback_radius_m=cfg.ember_threat_radius_m).eta_p10_s < now + egress_buffer`.
> The repro samples the **raw** p10; the true "eroded-P10" the corridor law uses
> (`geo.erode_min(p10, cfg.corridor_erosion_m)`) is strictly earlier, so these
> counts are a **conservative lower bound** on exposure.

---

## 2. (a)/(b)/(c)/(d) breakdown — answer: **(d) all three**

### (a) Idle crew overrun at its resting spot — DOMINANT
`diag_caught_repro.py`, final tick: **5 of 5** crews. Each finished its
itinerary and went `idle` with **`egress_zone_id = ''` (no egress zone)**, so it
came to rest **at its last gel-job site inside the central community** (11–15 km
from any safe zone), which the front then overran. Mechanism: after the last
deploy completes, `advance()` sets `_mode="idle"`; if `egress_loc is None`
(cleared earlier by a dispatch drop / `revoke_unstarted`) the `idle` branch hits
`else: self._leg = None; return` (`advance` ~L1107–1116) and the crew sits
**wherever it stopped**, unmonitored, until the fire arrives.

### (b) Crew idle AT a safe zone that is itself in the fire path — CONFIRMED
`diag_caught_caseb.py` (low-churn: 5–12 clients so crews finish early and egress
cleanly to a zone, then idle there). **All 5 swept configs produced a case (b)
instance, `violations = 0` in every one:**

| config | crew | idles AT zone | is its anchor? | tick (elapsed) | exposure |
|---|---|---|---|---|---|
| 8cl/4cr | g3 | z1 (0.0 m) | no (egress terminus) | 146 (36.5 h) | IMMINENT p10 +0.42 h |
| 10cl/5cr | g4 | z1 (0.0 m) | **yes (anchor)** | 127 (31.8 h) | IMMINENT p10 +0.33 h |
| 6cl/3cr | g1 | z2 (0.0 m) | no | 129 (32.2 h) | IMMINENT p10 +0.31 h |
| 12cl/6cr | g4 | z1 (0.0 m) | **yes (anchor)** | **176 (44.0 h)** | IMMINENT p10 +0.41 h |
| 5cl/4cr | g2 | z1 (0.0 m) | no | 125 (31.2 h) | IMMINENT p10 +0.28 h |

The crew is parked **exactly on the zone (0.0 m)** and the eroded/pessimistic
front arrives **inside the 30-min egress buffer** — it should already be fleeing,
nothing moves it, and the scoreboard reads 0. Note the 12cl/6cr instance lands at
**tick 176 ≈ the founder's tick 177**, at the crew's own **anchor** zone.

### (c) A safe zone the fire overran, still used as an egress/anchor target — CONFIRMED
`diag_caught_repro.py`, final tick — the static `_loc.safe_zones` are never
checked against the fire:

```
paradise_ca-z1: IMMINENT  p10_arr_h=-1.33  used_as=['anchor']         <-- fire front already past
paradise_ca-z2: IMMINENT  p10_arr_h=-0.23  used_as=['anchor']         <-- fire front already past
paradise_ca-z3: safe      p10_arr_h=+30.7  used_as=['anchor']
```

z1 (anchor for g1, g4) and z2 (anchor for g2, g5) have **negative eroded-P10
arrival** (the pessimistic front swept over them hours ago) yet remain live crew
anchors. A crew directed to egress/stage there would be routed **into** the fire.

**Egress/anchor selection does no fire check.** Zones are chosen purely by
proximity: `_adopt` picks `min(self._loc.safe_zones, key=distance-to-last-stop)`
(`simui.py` ~L2203); `egress_choice` (`safety.py` L369) corridor-checks the
**path at departure time** but there is no "is this zone still safe **now**"
gate, and it is never re-run for an idle/parked crew.

**Verdict: (d) all of these.** One root defect (no continuous per-crew safety
monitor + static safe zones) with three surface manifestations; (a) dominates the
high-load case, (b) and (c) dominate the low-load / long-run case.

---

## 3. Exact unmonitored crew states + the `_run_audit` gap

Crew mode → `CrewStatus` and whether the current cell is checked against fire:

| mode | status | current-cell vs fire checked? | where |
|---|---|---|---|
| `deploy` | `DEPLOYING` | **YES** (only state) — but only the *departure deadline at the stop's client cell* | `_road_depart_check` L1013, `_recheck_deploy` L1776, tick 2b L1972 |
| `idle` | `AVAILABLE` | **NO** | — (cases a, b) |
| `travel` | `EN_ROUTE` | **NO** | — (repro: g2 en_route, IMMINENT p10 −1.33 h, violations 0) |
| `egress` | `EGRESSING` | **NO** (corridor validated once at departure, never re-checked; target zone never re-validated) | — (case c) |

**The gap, precisely:** `_run_audit` (`simui.py` ~L2267) iterates
`self._serviced` (completed-stop departure records) and **nothing else**.
`scoreboard.violations` is `len(self._violations)` populated solely by that loop
(`state()` ~L2624). Therefore the scoreboard **can report 0 while any number of
crews stand in the burn**, because a crew's live position is never an input to
the audit in any state except `deploy`, and even there only as a *departure*
deadline, not an *overrun* of its current cell.

---

## 4. Fix spec (to build after the concurrent `simui.py` edit lands)

All three parts belong in `wgresponse/simui.py`; they **extend** coverage from
stop-departures to continuous crew safety and **do not touch** the P10 / corridor
/ slack math. Effort assumes reusing existing withdrawal/egress machinery.

### 4.1 CONTINUOUS CREW-SAFETY MONITOR — new `_crew_safety_sweep()`, call every tick
- Add `SimSession._crew_safety_sweep(now)`, invoked in `tick()` **after** crew
  movement (`advance`) and **before** `_run_audit` (so this tick's audit reflects
  any relocation), for **every** crew in `self._crew_sims` regardless of mode
  (this automatically covers the concurrently-added out-of-region `n_crews_1h/3h`
  crews — they are ordinary `_crew_sims` entries).
- For each crew, sample the **eroded-P10** raster at `cs.pos`. Reuse the existing
  per-tick per-fire transform `geo.erode_min(fire.ens.p10, cfg.corridor_erosion_m)`
  (already computed/cached for the corridor path — see `server.py` ~L495 and
  `sim.py` L787; cache once per tick keyed by fire, not per crew). Take the min
  arrival across fires; also fold in the nominal-member ground-truth
  (`ens.members[0]`) so an already-burned cell always trips.
- **Unsafe** iff `eroded_p10_arrival < now + cfg.egress_buffer_min*60`
  (generalizes the stop-departure rule to the crew's own cell). When unsafe:
  - **deploying** → withdraw via the existing `abort_deploy()` / `_recheck_deploy`
    withdrawal law (loud `state()['withdrawn']` record), then relocate.
  - **idle / travel / egress** → **relocate now**: retarget to the nearest
    **currently-safe** zone (see 4.2) via `adopt`/`_begin_leg("egress", ...)`.
- Effort: **Medium** (~1 day). Reuses withdrawal + leg machinery; the new work is
  the per-crew sweep, the "relocate an idle crew" path, and the per-tick eroded
  raster cache.

### 4.2 DYNAMIC SAFE-ZONE VALIDITY — new `_zone_currently_safe(zone, now)`
- A zone is a valid egress/staging target only if its own cell's eroded-P10
  arrival `>= now + cfg.egress_buffer_min*60`.
- Apply the filter at **every** zone-selection site: `_adopt`'s
  `min(self._loc.safe_zones, ...)` (~L2203), `_SimCrew._refresh_egress_plan`
  target, and pass a **pre-filtered** zone list into `dispatch.plan` /
  `safety.egress_choice` (or gate inside — note `egress_choice` already
  corridor-checks, but add the "zone itself safe now" precondition so an overrun
  zone can never win).
- Idle crews **stage only at currently-safe zones and re-relocate as the front
  advances**. If **no** zone is currently safe → operator alarm: pull all crews
  to the **latest-arrival** (safest available) zone and emit a **loud** note.
- Effort: **Small–Medium** (~0.5 day). Pure helper + wiring at 3 call sites.

### 4.3 AUDIT COUNTS OVERRUN — extend `_run_audit` (the no-reship invariant)
- After the serviced-stop loop, iterate `self._crew_sims` and emit a violation
  for any crew whose **current cell** is overrun: nominal arrival `<= now`
  (definitely in fire) OR eroded-P10 `< now + egress_buffer` (imminent). Add a
  distinct row type (e.g. `kind="crew_overrun"` / `reason`) so it renders
  separately from stop-departure rows.
- This makes it **structurally impossible** for `scoreboard.violations` to read 0
  while any crew stands in the burn — the invariant that stops this bug from ever
  reshipping. It is the single most important line of the fix; it can land even
  before 4.1/4.2 as a pure detector (fail-loud) so the scoreboard tells the truth
  immediately.
- Effort: **Small** (~0.5 day incl. a regression test that asserts
  `violations > 0` whenever a crew cell is burned).

### 4.4 TELEMETRY / OUTCOME SIGNAL
- Emit a per-crew relocation signal the UI can show ("repositioning — zone
  threatened"): a `notes` entry + a crew-level flag in the `crews[]` `state()`
  block (e.g. `relocating: true`, `relocate_reason`), and a scoreboard counter
  (e.g. `repositioned`). Snapshot with `state()` so time-scrub replays it.
- Effort: **Small** (~0.25 day).

**Total: Medium, ~2–3 days with tests.** Recommend landing **4.3 first**
(fail-loud detector) so the scoreboard stops lying while 4.1/4.2 (the corrective
relocation) are built.

---

## 5. This EXTENDS, does not weaken, the existing law

- The P10 latest-depart rule, the ember/adjacency fallback, the corridor
  (`route_margin_min`) check, the slack floor, and the deploy withdrawal law are
  **unchanged**. 4.1 uses the **same** `eroded_p10 = erode_min(p10,
  corridor_erosion_m)` transform and the **same** `egress_buffer_min` threshold
  the corridor/departure law already uses — it merely applies them to **every
  crew's current cell every tick** instead of only to serviced-stop departures
  and in-flight deploys.
- 4.2 only ever **removes** an unsafe zone from the candidate set (fail-safe /
  fail-closed — mirrors `egress_choice`'s existing "no_safe_egress" fail-closed
  behavior); it never makes an unsafe zone eligible.
- 4.3 can only **raise** `violations`, never lower it — a strict superset of the
  current audit. With an unchanged config and no idle/overrun crew, the audit is
  byte-identical to today's.

Resolves strictly toward safety: withdraw / relocate / count, never silently keep.

---

## Artifacts (read-only, under `scratch_review/`)
- `scratch_review/diag_caught_repro.py` — founder-scenario reproduction + a/c breakdown + invariant/persistence check.
- `scratch_review/diag_caught_caseb.py` — low-churn sweep isolating case (b).
