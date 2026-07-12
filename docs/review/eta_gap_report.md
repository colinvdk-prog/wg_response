# No-ETA "home near the fire, never protected" — diagnosis

**Date:** 2026-07-10   **Scope:** DIAGNOSIS ONLY (read-only product code; scratch
scripts under `scratch_review/diag_eta_*`, in-process SimSession, server on :8878
untouched).

**Founder report:** Malibu simulator, 48 clients / 3 crews / central high-extreme
fire. Live session sim-7 tick 118 showed **15 of 48 clients with no projected ETA
(`eta_p10_s`/`eta_p50_s` = None, alert state `none`)** while the fire had impacted
13 homes and threatened 20 more around them. Example: `c34` ($630k) shows
`state=none` / no-ETA.

---

## Verdict

The no-ETA homes split into two fundamentally different populations:

* **Most are legitimately safe** ("too far" / upwind of a wind-driven fire that
  runs the other way) — *not* a model failure.
* **A structural block — 23-46 % of ALL placed clients — sit ON non-burnable
  (developed / urban FBFM40 91) cells.** The Rothermel *surface* spread model
  treats every non-burnable cell as a permanent fire break, so a home on such a
  cell **never receives an arrival time even when fire has burned up to 7 of its
  8 neighbours.** These homes are the founder's complaint. They are **not**
  genuinely safe — a developed parcel engulfed by fire is threatened by embers,
  radiant heat and structure-to-structure ignition, none of which the surface
  model represents.

This is a **known, documented caveat** (not a new bug): `simui.py` already emits a
loud note at configure time — *"N of 48 clients sit on developed (non-burnable)
cells: the surface-spread model cannot reach them, so they will not alert or
dispatch (ember/structure spread not modeled)."* The founder is seeing exactly
that note's consequence on the map.

There is **no barrier/hole artifact** (class c = 0 across every seed tested): the
engine's no-barrier guarantee holds — no *burnable* home is ever spuriously left
unreached.

---

## Mechanism (code trace)

1. `spread.compute_arrival` (spread.py:310-315) sets `fuel_of[i]=None` for every
   non-burnable cell (FBFM40 91/92/93/98/99, nodata). The Dijkstra relaxation
   (spread.py:474, 485) **skips `None`-fuel cells as both source and target**, so
   a non-burnable cell keeps `best = UNREACHED (inf)` forever unless directly
   seeded. This is intended (a surface fire cannot burn pavement/water).
2. `spread.eta_at` (spread.py:704) samples the client's **own containing cell**
   (`geo.raster_sample`, nearest-cell). A client on a non-burnable cell samples
   `inf` for all of p10/p50/p90 → `ThreatEta(inf,inf,inf)`.
3. `SimSession.tick` (simui.py:1730) does `eta = spread.eta_at(ens, cl.loc)` per
   client; the alert engine never elevates an all-`inf` pair, and dispatch skips
   it (simui.py:1775 `if math.isinf(eta.eta_p50_s)... continue`).
4. `SimSession.state` (simui.py:2192-2196) renders `inf → None` and the missing
   alert record as `AlertState.NONE` → the UI shows **state `none`, ETA blank.**

The root that *places* homes there: `wui_cells` (simui.py:544-573) defines the WUI
band as **developed cells (FBFM40 91/93) within 3 cells of burnable fuel**, UNION
burnable cells within 1 cell of a developed cell. `place_clients` samples that band
(simui.py:621), so the developed (non-burnable) side of the interface is a
first-class placement target — by design (WUI homes really do sit on developed
parcels), but the surface model can't threaten them.

---

## Reproduction

Deterministic, in-process (`scratch_review/diag_eta_gap.py`): real Malibu standard
tile (200×200 @ 90 m; **42.9 % of the tile is non-burnable** — 30 % ocean/water
code 98, 9 % urban code 91), 48 clients, 3 crews, `extreme` intensity ignited at
the nearest burnable cell to the community center (49 m from center), Santa-Ana
hazard wind FROM 50° (fire runs SW toward the ocean), 120 ticks (+30 h sim).

### Q1 — no-ETA breakdown (primary seed `eta-gap-diag-1`, tick 120)

| class | meaning | n |
|---|---|---|
| **(a)** | own cell **NON-BURNABLE** (surface model can't reach it) | **11** |
| **(b)** | burnable, genuinely **outside the fire's 96 h projected reach** | 27 |
| **(c)** | burnable but **left unreached despite reached neighbours** (barrier/hole artifact) | **0** |
| | **total no-ETA** | **38** |

* **Class (a)** are all on code 91 (urban). Every one of them has `p10 = inf`
  regardless of how close the fire is.
* **Class (b) proven legitimate:** each is 8-11 km from the ignition, mostly to
  the **NE (upwind, bearings 11-50°)** or far crosswind, with **0 reached
  neighbours** — outside a SW-running Santa-Ana fire's footprint. Correctly safe.
* **Class (c) = 0** — the no-barrier guarantee (spread.py:95-103) holds; no
  burnable home is spuriously unreached.

### Worst visual case — the c34 analogue exists

`sim-c13` ($1.28M), cell (129,39), **FBFM40 91 (urban)**, **7 of 8 neighbours
burned, 3 of 4 rook directions burned, 17 burned cells within 2 rings** — and
**`state = none`, no ETA.** This is precisely the founder's "home near the fire,
never protected": fire on nearly every side, zero alert, zero dispatch, because
the home's own cell is a non-burnable break.

### Q2 — WUI placement census (fraction of 48 on NB cells)

Primary seed: **11 / 48 = 22.9 %** on non-burnable cells (all code 91).

**Seed sweep (robustness, 8 seeds, tick 120):**

| seed | on-NB /48 | no-ETA | (a) NB | (b) far | (c) hole | peak surrounded |
|---|---|---|---|---|---|---|
| eta-gap-diag-1 | 11 (23 %) | 38 | 11 | 27 | 0 | 1 |
| malibu-A | 17 (35 %) | 46 | 17 | 29 | 0 | 0 |
| malibu-B | 18 (38 %) | 41 | 18 | 23 | 0 | 3 |
| malibu-C | 16 (33 %) | 43 | 16 | 27 | 0 | 1 |
| malibu-D | 13 (27 %) | 38 | 13 | 25 | 0 | 3 |
| malibu-E | 22 (46 %) | 36 | 22 | 14 | 0 | 4 |
| malibu-F | 21 (44 %) | 41 | 21 | 20 | 0 | 3 |
| malibu-G | 17 (35 %) | 42 | 17 | 25 | 0 | 2 |

**23-46 % of every placed roster sits on a non-burnable cell, and class (a) always
equals the on-NB count exactly** (100 % of NB-cell homes are no-ETA homes). Class
(c) is 0 in every seed. The founder's live 15/48 (31 %) no-ETA sits squarely inside
the on-NB band; their session had the fire cover more of the cluster (33 assessed
vs my ~10), so their residual 15 is dominated by the NB-cell gap rather than the
far-upwind class-(b) homes my smaller footprint additionally surfaces.

> **Latent secondary finding:** `wui_cells` only classifies 91/93 as "developed,"
> but the overflow pool `_burnable_adjacent_cells` (simui.py:576, used when the
> band is smaller than n) can place a client on **any** cell with a burnable
> neighbour — including 92/98/99 (snow/water/barren). The configure-time `n_dev`
> note (simui.py:1254-1264) only counts 91/93, so it would **undercount** the true
> no-ETA set if overflow ever lands on other NB codes. It did not trigger at n=48
> here (band is large enough), but a denser roster could expose it.

### Fix validation (`scratch_review/diag_eta_fix_probe.py`)

Applying `geo.erode_min` (the existing morphological-min filter) to the fire's p10
raster and re-sampling the 11 NB homes:

| radius | NB homes gaining a finite threat ETA | notes |
|---|---|---|
| 90 m (1 cell) | 1 | sim-c13 → IMPACTED |
| 180 m (2 cells) | 3 | sim-c13 IMPACTED; sim-c46 +5.9 h; sim-c12 +6.4 h |
| 270 m (3 cells) | 3 | 2 IMPACTED |
| 450 m (5 cells) | 3 | 3 IMPACTED |

Exactly the desired behaviour: only the developed homes the fire has actually
reached the edge of inherit a threat ETA (and would alert/dispatch); the 8
genuinely-distant NB homes correctly stay UNREACHED. Polarity is strictly
**more homes threatened** (conservative — correct for an alerting product).

---

## Q3 — Fix options

**(i) Threat-by-proximity fallback (RECOMMENDED).** When a client's direct
nearest-cell ETA is UNREACHED, inherit the **earliest arrival among burnable cells
within R metres** (ember/adjacency threat). A developed home surrounded by fire
then gets a real threat ETA + alert.
*Pro:* keeps placement honest (WUI homes stay on their real developed parcels);
reuses `geo.erode_min`, which the codebase already applies to p10 for corridor
safety (server.py:512, sim.py:787, safety.py:160); validated above. *Safety
polarity:* more homes threatened = conservative. *Con:* R is a modelled proxy for
ember reach, not physics.

**(ii) Place only on burnable-adjacent cells, assign threat from nearest burnable
cell.** *Con:* changes deterministic placement (re-baselines fingerprints), and
hides the honest reality that WUI structures sit on developed land. Rejected.

**(iii) Explicit ember / structure-ignition band.** The physically honest fix
(model spotting/structure-to-structure onto NB parcels). *Con:* a real modelling
project (new spread pathway, calibration, tests) — Large. Right long-term, wrong
for an immediate simulator fix.

### Recommendation

**Adopt option (i), scoped to the simulator first. Effort: S.**

* **`wgresponse/spread.py::eta_at`** — add optional `fallback_radius_m: float =
  0.0`. When any quantile sample is UNREACHED and the radius > 0, sample an
  `geo.erode_min(raster, fallback_radius_m)` copy instead (memoise the eroded
  rasters on the `EnsembleArrival`, keyed by radius — erosion is O(n·k) and the
  ensemble is fixed per fire per tick). `0.0` default is byte-identical to today,
  preserving the 1047-test suite and the pinned spread fingerprints. Erosion
  preserves p10 ≤ p50 ≤ p90.
* **`wgresponse/simui.py::SimSession.tick`** (line 1730, and the per-client
  rollup at 1740-1744; for consistency the audit/replay samplers at 1504, 1568,
  2020) — pass a new `cfg.ember_threat_radius_m` (suggest default **180-270 m** =
  2-3 cells). Only NB/unreached clients change; burnable clients stay byte-equal.
* Update the configure-time `n_dev` note to say these homes now alert via the
  ember-proximity fallback, and extend its census to all NB codes (fixes the
  latent overflow-undercount).

The identical call site exists at **`wgresponse/threat.py:636`** for the *live*
product. Enabling it there is the same one-line change but is a bigger decision
(it changes real-world alert volume and needs an alert-ladder/config-default
review) — **Medium**. Recommend shipping the simulator fix first so the founder
sees surrounded homes light up, then productionising behind the same config knob.

---

## Artifacts

* `scratch_review/diag_eta_gap.py` — reproduction + a/b/c classification + seed
  sweep + NB-placement census.
* `scratch_review/diag_eta_fix_probe.py` — option (i) validation via
  `geo.erode_min`.

No product code was modified.
