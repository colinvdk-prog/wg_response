# Caught-in-fire determination & transit-hazard model spec (2026-07-30)

**Founder question:** our "crew caught in fire" events — are they true
entrapments, or the common real-world case of *evacuating with fire at the
side of the road*: worrying but survivable? "The P10 fire doesn't initially
have the full flame density; routes can be evaluated on fuel, road width
etc."

**Determination: the founder's frame is physically correct, quantifiable
with machinery the engine already contains, and bounded by honest limits.**
Physics derivation by the Opus review (artifacts
`scratch_review/hazard_*.py` + captured outputs); code-truth dissection of
recorded events in `scratch_review/caught_dissect.json`.

## The two physical facts

1. **"Cell burned" is not one thing.** Flaming-zone depth `D_f = ROS x
   residence_time` spans 0.1–1.2 m in timber litter (0.1–1.3% of a 90 m
   cell) to 11–49 m in grass (12–55% of the cell). The arrival raster marks
   both identically. A crew "in a burned cell" on a Paradise residential
   road (54% TL roadside, flame length 0.67 m) is metres of pavement from
   sub-threshold flux; the same flag beside Malibu chaparral (SH5, flame
   length 9.6 m) is inside the 1%-fatality dose envelope.
2. **The transit credit is real but small: 0.55x, not 4x.** Standing
   separation (7 kW/m², Butler & Cohen) = 8.34 x flame length; transiting
   one 90 m cell at crew speed (9.47 m/s) at the 12.5 kW/m² piloted-ignition
   cap = 4.60 x L. You may drive where you may not stand — by a factor of
   two, not ten. A 60 s stall converts transit to standing exactly, which
   anchors the non-negotiable floor: **never below 1.5 x flame length of
   clear setback** (a stalled vehicle at 1.48 x L takes the 1% fatality
   dose in under 10 s).

## Key derived tables (full numbers in scratch_review/hazard_*_out.txt)

**Per-class cold-black windows** (replace the flat 45 min, which is
numerically the Anderson residence time of a 1000-hour log — "assume
everything is downed timber"): GR 5–10 min, GS 10–20, SH 20–30, TU 45
(snag floor), TL 60, SB 90. Grass/shrub open up 2–9x; **timber gets
stricter** — the honest direction on both sides.

**Passability at the pavement edge (high intensity):** TL = PASSABLE at
every road class; GR/GS/TU/SB = IMPASSABLE at the edge but grass flips
PASSABLE with one 90 m cell of separation; SH5-class chaparral needs
~112 m. Roads themselves measured 46–93% non-burnable at the sample cell —
the model must sample fuel at a radius, and the ±45 m raster quantization
is the single largest error term (bigger than any physics uncertainty).

## The spec (all default-OFF; two independently shippable stages)

**Gate A (unchanged):** the eroded-P10 corridor law and
`LegOracle.latest_safe_depart_s` — monotone, owns "will the front overtake
me." **Gate B (new, non-monotone — cannot be a T\*):** per-sample thermal
verdict: nearest burnable fuel within `transit_hazard_scan_m` (270 m) →
lifecycle state (PRE-FRONT / FLAMING / RESIDUAL / COLD by per-class
residence + cold-black) → flame length & emissivity from the engine's OWN
`RosResult` (flame_length_m is computed today and discarded) → view-factor
flux + Hymes thermal dose at the pass speed → PASSABLE (≤5 kW/m², ≤92 TDU)
/ CAUTION (≤12.5, ≤290) / IMPASSABLE. Insertion: `LegOracle` per-sample
loop → `simui._route_metrics` / escape rays (route-selection policy ONLY —
the 10c audit and SEM-10 sweep are never relaxed).

Stage 1 = `transit_cold_black_by_class` alone (small, clean sign). Stage 2
= full `transit_hazard_enabled` model (14 knobs incl. flux/dose thresholds,
1.5xL floor non-tunable below 1.5, flame cap before quoting absolute
lengths — engine flame lengths are deliberately wind-uncapped).

## Validation (before any flip)

1. Byte-identity with knobs off vs opt_ember_ch_base.json, exactly.
2. Per-tick overrun ledger (crew, position, roadside fuel, q, dose, band) —
   the raw dicts are printed today but never persisted; build the tracer.
3. Classify every recorded overrun: TRUE ENTRAPMENT / RECLASSIFIED-CAUTION
   / RECLASSIFIED-PASSABLE. **Acceptance: zero true entrapments on the full
   suite; the clean rad550 runs must stay clean.**
4. Falsifiable prediction stated in advance: Paradise relief comes entirely
   from the flux term (TL roadside), Malibu relief entirely from the
   cold-black term (SH). If the split lands the other way, the model is
   wrong.

## Honest limits (cannot be claimed away)

Smoke/visibility — historically the actual killer on evacuation roads — is
not modeled; a PASSABLE verdict is thermal only. Spotting can cut a road
ahead of the vehicle (separate model). Cab shielding is NOT credited.
Civilian traffic jams are priced as time, not stop-probability. Snags keep
TU/TL/SB floors at 45/60/90 min. Never route INTO the head-fire sector
regardless of table verdict. Arrival times use floored (conservative-fast)
ROS while flame lengths use unfloored Rothermel — consistent for hazard,
stated here so it is not "discovered."
