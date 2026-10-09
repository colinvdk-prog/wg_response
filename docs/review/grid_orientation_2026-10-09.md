# Grid orientation: the fire could only run in 16 directions (2026-10-09)

Integrator finding, 2026-10-09 (`scratch_review/wind_area_inversion_probe.py`):
on a flat, uniform 24 x 24 km tile the area burned in 6 hours **fell** as the
wind rose (GR2 grass, wind between two grid directions: 2,110 acres at 20 mph,
1,302 at 25), the head ran 3.4-7.8 km where it should have run 4-28 km, and the
city-weather review saw the same on the real maps. This review measures why,
fixes it behind a new knob (`cfg.spread_any_angle`, contracts SAFETY SEMANTICS
21, default OFF, byte-identical when off), and measures the fix on uniform
tiles, on the committed real tiles, on the 13 one-click scenarios and on the
11-fire real hindcast.

Scripts and results: `scratch_review/grid_orientation_*.py` and `.json`
(section 8). Engine change: `wgresponse/spread.py` (+ thin wiring in
`wgresponse/simui.py`), tests `tests/test_spread_any_angle.py` (26).

## For the founder

**What was wrong.** The engine works out when fire reaches each 90-100 m square
of the map by stepping from square to square. From any square it can only step
in 16 fixed directions (the 8 neighbours plus the 8 "knight moves"). That is
fine when a fire is roughly round. But in strong wind the engine's fire is a
long, thin ellipse — from about 27 mph in grass (20 mph in brush) it is 8 times
longer than it is wide — and such a fire can only run at full speed within a
few degrees of the wind direction. When the wind blows between two of the 16 directions, which is most
of the time, the fire has to zig-zag along directions 11-15 degrees off the
wind, where its own speed is only a fifth to a quarter of its head speed. So:

* the head of the fire ran at **22-28 % of its own speed** at high wind (about
  half at 20 mph in grass);
* even with the wind exactly along a grid line, the fire was drawn about half
  as wide as it should be;
* the burned area was **16-45 % of the engine's own fire shape** at high wind,
  and **it shrank as the wind rose** (more wind made a thinner fire that the 16
  directions could follow even less).

On a flat uniform map we can check this exactly, because the engine's own
physics gives a perfect ellipse there; the engine's output matched the shape
the 16 directions can reach to within 1 %, so the cause is certain.

**How much it mattered.**

* **Live engine (crew safety).** For a home straight downwind of a new fire on
  flat uniform grass, the P10 arrival time the crew deadlines are built on was
  **hours late**: at 30 mph, 4.3 h instead of 1.0 h at 3 km and 8.8 h instead of
  2.0 h at 6 km (15 mph: 3.8 h instead of 2.6 h at 3 km). The ensemble does not
  rescue it: P10 is never later than the nominal run, and the nominal run is
  the late one. Late is the dangerous direction for every crew deadline. On the
  real maps the effect is smaller but the same way round: compared with a 5x
  finer solution of the same physics (25x the squares), the old engine's
  arrival times were a median 7-82 % late at 25-40 mph.
* **Demos.** Every high-wind demo fire was too slow and too thin on uniform
  ground; on the real maps it was 11-57 % too small at 25-40 mph.

**What the fix does.** Each burning square now also offers its neighbours the
straight line from where its fire run started (its "anchor"), at any angle,
not just the 16. The time along that straight line is added up square by
square, each square at its own fuel, slope and weather, so a straight run is
never faster than the engine's own physics allows — it only stops the 16
directions from slowing the fire. The old 16 steps are still all there
unchanged, so with steady weather no square ever burns later than before.

* On uniform ground the engine now burns exactly the squares its own ellipse
  covers, at every wind angle (overlap 0.98-1.00, head within 3 %), and **the
  area grows with the wind**. The crew-deadline P10 at the downwind home
  becomes the right time (1.0 h, 2.0 h above).
* On the real maps it moves the fire most of the way to the finer solution at
  high wind (median arrival error 82 % -> 30 % late on Boulder at 30 mph,
  22 % -> 5 % on Rancho Santa Fe) and cuts the error by half or more at
  15-20 mph.

**What it does not fix.** On the real maps **more wind still burns less**
between about 10 and 30 mph, and it is not the grid: the 25x finer solution of
the same physics shows it too (Boulder 4,544 acres at 10 mph, 1,691 at 30;
Eagle 1,689 -> 252). The reason is the physics: at high wind the engine's fire
is 8 times longer than wide, so when its head reaches roads, towns or slow
timber it stops, and in the engine's ellipse the sides of a fire spread
*slower* as the wind rises. Whether real fires are that thin at 30 mph is a
question for the ellipse model (its length-to-breadth law and cap), spotting
and wind variability — a separate review. One side effect to know: at low wind
on the Park City timber mosaic the fix makes the fire bigger than the finer
solution (679 acres vs 260 at 10 mph), because an older engine shortcut —
fire entering a slow timber square at the speed of the fast square it came
from — is now reached sooner. That shortcut is a known separate finding
("source-only edge rate"); fixing it naively made things worse (section 2.4).

**What it costs.** The fire field takes 2-3 times longer to compute: a demo's
first fire field (6 members) 1.3-4.7 s -> 3.1-11.2 s; a live-style domain
2.7-3.4x per member (Little Giant: 2.0 s -> 5.7 s) with 3x the peak memory
(150 MB on the largest). A very large uniform fire costs about 12x per burned
square, because each straight line is added up square by square; that case
does not occur on real maps (section 3.6).

**Recommendation.**

1. Arm `spread_any_angle` in the **simulator** now: the demos stop showing a
   fire that the grid slows, and every one-click scenario was re-run (section
   3.3).
2. For the **live watcher** (`watch --any-angle`, not armed), the fix removes a
   measured anti-safe bias in the crew-deadline P10 (hours late on uniform
   ground at high wind). On the 11 real 2026 fires of the hindcast it changes
   little (section 3.4): P10 coverage of newly burned ground 99.78 % ->
   99.82 %, the P50 footprint 3.55x -> 3.62x the burned area — the engine
   already over-forecasts those timber fires for reasons unrelated to the grid.
   Decide the live flip together with the ellipse review below, and check the
   cost (2.7-3.4x per member, 3x peak memory) against the watcher's cycle
   budget first.
3. Open a separate review of the high-wind fire shape (the length-to-breadth
   law and its cap of 8, wind-direction variability, spotting) — that, not the
   grid, is what still makes the real maps burn less in more wind. Keep the cap:
   without it even the engine's own ellipse loses area as the wind rises
   (section 1.4).

## 1. The defect, measured

### 1.1 Uniform tile: the engine draws the polygon inscribed in its own ellipse

`scratch_review/grid_orientation_probe.py wind`: flat tile sized to hold the
6-h fire, 100 m cells, one ignition, 15 % RH, 30 C, spotting OFF, nominal
`compute_arrival`. **Analytic** = the engine's own elliptical fire for the same
fuel and wind (head rate from `rothermel` + the floor exactly as `cell_params`
computes it, `L/B = rothermel.ellipse_lb(U_eff)`, ignition at the rear focus).
**Lattice polygon** = the convex hull of the ellipse's ray rate along each of
the 16 lattice bearings — what a 16-direction minimum-travel-time lattice can
reach in a uniform medium. The legacy engine matches the polygon to within 1 %
in every case:

**GR2, wind from 270** (on a lattice axis), 6 h

| wind mph | L/B | legacy acres | lattice polygon | fix acres | analytic acres | legacy head km | fix head km | analytic head km | IoU legacy → fix |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1.00 | 62 | 68 | 62 | 70 | 0.2 | 0.2 | 0.3 | 1.00 → 1.00 |
| 5 | 1.37 | 499 | 499 | 534 | 534 | 1.6 | 1.6 | 1.6 | 0.94 → 1.00 |
| 10 | 1.99 | 1,483 | 1,483 | 1,759 | 1,754 | 3.9 | 3.9 | 3.9 | 0.84 → 1.00 |
| 15 | 2.97 | 2,305 | 2,298 | 3,254 | 3,283 | 6.8 | 6.8 | 6.9 | 0.71 → 1.00 |
| 20 | 4.49 | 2,493 | 2,489 | 4,707 | 4,707 | 10.3 | 10.3 | 10.3 | 0.53 → 1.00 |
| 25 | 6.79 | 2,160 | 2,159 | 5,797 | 5,772 | 14.1 | 14.1 | 14.1 | 0.37 → 1.00 |
| 30 | 8.00 | 2,679 | 2,655 | 8,248 | 8,226 | 18.3 | 18.3 | 18.3 | 0.32 → 1.00 |
| 40 | 8.00 | 6,081 | 6,065 | 18,827 | 18,789 | 27.7 | 27.7 | 27.7 | 0.32 → 1.00 |


**GR2, wind from 281.25** (between lattice directions), 6 h

| wind mph | L/B | legacy acres | lattice polygon | fix acres | analytic acres | legacy head km | fix head km | analytic head km | IoU legacy → fix |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1.00 | 62 | 68 | 62 | 70 | 0.2 | 0.2 | 0.3 | 1.00 → 1.00 |
| 5 | 1.37 | 494 | 500 | 534 | 534 | 1.5 | 1.6 | 1.6 | 0.93 → 1.00 |
| 10 | 1.99 | 1,483 | 1,488 | 1,745 | 1,754 | 3.4 | 3.9 | 3.9 | 0.85 → 1.00 |
| 15 | 2.97 | 2,249 | 2,244 | 3,282 | 3,283 | 5.1 | 6.8 | 6.9 | 0.69 → 1.00 |
| 20 | 4.49 | 2,110 | 2,107 | 4,722 | 4,707 | 5.8 | 10.3 | 10.3 | 0.45 → 1.00 |
| 25 | 6.79 | 1,302 | 1,301 | 5,753 | 5,772 | 5.0 | 14.1 | 14.1 | 0.23 → 1.00 |
| 30 | 8.00 | 1,320 | 1,321 | 8,234 | 8,226 | 5.2 | 18.3 | 18.3 | 0.16 → 1.00 |
| 40 | 8.00 | 3,022 | 3,018 | 18,731 | 18,789 | 7.8 | 27.6 | 27.7 | 0.16 → 1.00 |


**SH5, wind from 270** (on a lattice axis), 6 h

| wind mph | L/B | legacy acres | lattice polygon | fix acres | analytic acres | legacy head km | fix head km | analytic head km | IoU legacy → fix |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1.00 | 121 | 125 | 121 | 128 | 0.4 | 0.4 | 0.4 | 1.00 → 1.00 |
| 5 | 1.64 | 2,866 | 2,864 | 3,188 | 3,195 | 4.6 | 4.6 | 4.7 | 0.90 → 1.00 |
| 10 | 2.99 | 4,898 | 4,902 | 7,038 | 7,035 | 10.1 | 10.1 | 10.1 | 0.70 → 1.00 |
| 15 | 5.59 | 4,085 | 4,066 | 9,210 | 9,183 | 16.1 | 16.1 | 16.1 | 0.44 → 1.00 |
| 20 | 8.00 | 4,013 | 4,012 | 12,459 | 12,430 | 22.5 | 22.5 | 22.5 | 0.32 → 1.00 |
| 25 | 8.00 | 6,776 | 6,763 | 20,969 | 20,951 | 29.2 | 29.2 | 29.3 | 0.32 → 1.00 |
| 30 | 8.00 | 10,383 | 10,376 | 32,074 | 32,144 | 36.2 | 36.2 | 36.3 | 0.32 → 1.00 |
| 40 | 8.00 | 20,433 | 20,428 | 63,163 | 63,286 | 50.8 | 50.8 | 50.9 | 0.32 → 1.00 |


**SH5, wind from 281.25** (between lattice directions), 6 h

| wind mph | L/B | legacy acres | lattice polygon | fix acres | analytic acres | legacy head km | fix head km | analytic head km | IoU legacy → fix |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1.00 | 121 | 125 | 121 | 128 | 0.4 | 0.4 | 0.4 | 1.00 → 1.00 |
| 5 | 1.64 | 2,869 | 2,872 | 3,207 | 3,195 | 4.2 | 4.6 | 4.7 | 0.89 → 1.00 |
| 10 | 2.99 | 4,781 | 4,779 | 7,047 | 7,035 | 7.5 | 10.1 | 10.1 | 0.68 → 1.00 |
| 15 | 5.59 | 2,965 | 2,953 | 9,175 | 9,183 | 7.3 | 16.1 | 16.1 | 0.32 → 1.00 |
| 20 | 8.00 | 2,004 | 1,997 | 12,417 | 12,430 | 6.4 | 22.5 | 22.5 | 0.16 → 1.00 |
| 25 | 8.00 | 3,371 | 3,366 | 20,915 | 20,951 | 8.3 | 29.2 | 29.3 | 0.16 → 1.00 |
| 30 | 8.00 | 5,162 | 5,164 | 31,970 | 32,144 | 10.3 | 36.2 | 36.3 | 0.16 → 0.99 |
| 40 | 8.00 | 10,176 | 10,166 | 62,271 | 63,286 | 14.5 | 50.8 | 50.9 | 0.16 → 0.98 |


GS2 and TU5: appendix A. With the wind on a lattice axis (270) the head is
exact (the head direction IS a lattice direction) but the width is missed; with
the wind between directions (281.25) both are missed. The legacy area falls
with wind from 15-20 to 25 mph in GR2, GS2 and TU5 and from 10 to 20 mph in
SH5 (the probe's finding); the analytic ellipse's area grows monotonically.

### 1.2 Where the head speed is lost

`directional_ros_mps` gives each cell an ellipse with the ignition at its rear
focus: `R(theta) = R_head (1 - e) / (1 - e cos theta)`, `e = sqrt(lb^2-1)/lb`.
Its rate falls to half at 13.0 degrees off the head at L/B 4.5 and at **7.2
degrees** at L/B 8 (where the ellipse is also widest). The 16 lattice
directions are 18.4-26.6 degrees apart. GR2, wind from 281.25 (head toward
101.25):

| wind | L/B | lattice bearing 90 (11.25 deg off) | lattice bearing 116.57 (15.32 deg off) | head the lattice can reach |
|---|---|---|---|---|
| 20 mph | 4.49 | 57 % of the head rate | 42 % | 48 % along the axis (measured head 56 %) |
| 40 mph | 8.00 | 29 % | 18 % | 23 % along the axis (measured head 28 %) |

The fastest lattice path toward the head mixes the two neighbouring directions,
so the head reaches only the chord between those two polygon corners. The
hypothesis in the brief is right in mechanism; the loss is ~half at L/B 4.5 and
~three quarters at the L/B cap of 8.

### 1.3 The width, and why area falls with wind

With the wind on an axis the polygon's corners next to the head are at 26.6
degrees, where the L/B-8 ellipse's rate is 7 % of the head's; the ellipse is
widest at 7.2 degrees, between them, so the polygon misses the whole bulge
(half-width 0.5 km vs 1.15 km, GR2 30 mph 6 h). Polygon area ~ R_head^2 / L/B^2
while the ellipse's ~ R_head^2 / L/B: as L/B grows exponentially with wind
(Anderson 1983) the polygon's area falls between ~19 and ~27 mph in grass, then
grows again once L/B is capped at 8. The **back** of the fire (`R_head (1-e) /
(1+e)`) really does shrink with wind in the engine's physics; the lattice
reproduces it (it is not part of the defect).

### 1.4 The length-to-breadth cap is not the cause

`grid_orientation_probe.py lbcap` (GR2 / SH5, wind between directions, 2 h),
with the cap of 8 and with `ellipse_lb` uncapped:

| L/B rule | fuel | wind mph | L/B | legacy acres | fix acres | analytic acres |
|---|---|---|---|---|---|---|
| cap 8 | GR2 | 20 | 4.5 | 237 | 524 | 523 |
| cap 8 | GR2 | 25 | 6.8 | 146 | 647 | 641 |
| cap 8 | GR2 | 30 | 8.0 | 146 | 907 | 914 |
| cap 8 | GR2 | 35 | 8.0 | 235 | 1,428 | 1,422 |
| cap 8 | GR2 | 40 | 8.0 | 339 | 2,083 | 2,088 |
| cap 8 | SH5 | 20 | 8.0 | 225 | 1,376 | 1,381 |
| cap 8 | SH5 | 25 | 8.0 | 378 | 2,325 | 2,328 |
| cap 8 | SH5 | 30 | 8.0 | 578 | 3,568 | 3,572 |
| cap 8 | SH5 | 35 | 8.0 | 835 | 5,115 | 5,133 |
| cap 8 | SH5 | 40 | 8.0 | 1,134 | 7,035 | 7,032 |
| no cap | GR2 | 20 | 4.5 | 237 | 524 | 523 |
| no cap | GR2 | 25 | 6.8 | 146 | 647 | 641 |
| no cap | GR2 | 30 | 10.3 | 72 | 717 | 709 |
| no cap | GR2 | 35 | 15.5 | 30 | 736 | 729 |
| no cap | GR2 | 40 | 23.4 | 15 | 717 | 710 |
| no cap | SH5 | 20 | 10.4 | 99 | 1,053 | 1,054 |
| no cap | SH5 | 25 | 19.4 | 25 | 966 | 952 |
| no cap | SH5 | 30 | 36.0 | 7 | 781 | 787 |
| no cap | SH5 | 35 | 66.6 | 2 | 2 | 612 |
| no cap | SH5 | 40 | 122.9 | 2 | 2 | 454 |

Uncapped, the lattice collapses completely (SH5 40 mph: 2 acres in 2 h) and
**the analytic ellipse itself loses area as the wind rises** (SH5 1,054 -> 454
acres from 20 to 40 mph): area ~ R_head^2 / L/B and L/B grows faster than the
head rate. The cap keeps the engine's own physics monotone; keep it. (The fix
follows the uncapped ellipse to L/B 36; at L/B 60+ even its first lattice step
off a point ignition is too slow and the fire never starts — irrelevant with
the cap.)

### 1.5 The live quantities: P10 at a home straight downwind

`scratch_review/grid_orientation_p10.py`: the default ensemble (6 members,
wind/moisture/direction perturbations, the live 96-h horizon, so member
direction offsets up to +-24 deg), flat GR2, homes 3/6/10 km straight downwind.
Hours after ignition:

| wind | home | engine's own arrival | legacy P10 | legacy P50 | fix P10 | fix P50 |
|---|---|---|---|---|---|---|
| 15 mph from 270 (on axis) | 6 km | 5.23 | 5.23 | 7.19 | 5.23 | 7.19 |
| 15 mph from 281.25 | 3 km | 2.58 | 3.78 | 4.65 | 2.58 | 3.54 |
| 15 mph from 281.25 | 6 km | 5.25 | 7.68 | 9.45 | 5.25 | 7.21 |
| 20 mph from 281.25 | 3 km | 1.73 | 3.58 | 4.76 | 1.73 | 3.10 |
| 20 mph from 281.25 | 6 km | 3.51 | 7.27 | 9.67 | 3.51 | 6.31 |
| 30 mph from 270 (on axis) | 6 km | 1.96 | 1.96 | 7.78 | 1.96 | 7.78 |
| 30 mph from 281.25 | 3 km | 0.97 | **4.32** | 7.38 | 0.97 | 3.86 |
| 30 mph from 281.25 | 6 km | 1.97 | **8.75** | 14.96 | 1.97 | 7.82 |
| 30 mph from 281.25 | 10 km | 3.28 | **14.54** | 24.86 | 3.28 | 13.00 |
| 30 mph from 290 | 6 km | 1.97 | 6.76 | 12.84 | 1.97 | 7.80 |

P10 is nominal-bounded (`ensemble_arrival`), and the perturbed members' rotated
winds aim their thin heads off the home, so P10 = the nominal = the late
lattice arrival. Every crew deadline (`latest_depart = P10 - egress buffer`)
inherited hours of lateness on uniform ground. With the fix P10 equals the
engine's own arrival to the minute. (The P50 stays later than the nominal at
high wind in both: half the members' heads miss the home — ensemble physics,
not the grid.)

### 1.6 A bigger neighbourhood cannot fix it

`scratch_review/grid_orientation_stencils.py` (the same polygon construction
with every primitive lattice offset up to radius N; wind swept over a 45-degree
octant in half degrees): the worst wind angle at L/B 8.

| neighbourhood | directions | widest gap | worst head / analytic | worst area / analytic |
|---|---|---|---|---|
| N=2 (today) | 16 | 26.6 deg | 0.23 | 0.16 |
| N=3 | 32 | 18.4 deg | 0.39 | 0.33 |
| N=4 | 48 | 14.0 deg | 0.51 | 0.49 |
| N=5 | 80 | 11.3 deg | 0.63 | 0.61 |
| N=8 | 176 | 7.1 deg | 0.81 | 0.82 |

The L/B-8 ellipse's useful cone is ~+-7 degrees; even 176 directions (11x the
edges, 8-cell jumps over fuel breaks) leave the head 19 % slow. Correcting the
16 edges' rates cannot work either: to reach the true head through two corners
15 degrees off it, both corners must run at ~the head rate, which makes the
fire a fan 2.4x the ellipse's area.

## 2. The fix: `cfg.spread_any_angle` (SAFETY SEMANTICS 21)

### 2.1 What it does

Theta*-style any-angle paths (Nash et al. 2007) over the existing lattice:

* **Anchors.** Every labelled cell remembers the cell its straight run started
  from: a seed (ignition, perimeter-mask cell, kept cell, ember landing), or
  the cell whose lattice edge labelled it. When a cell burns, each neighbour
  gets the legacy edge candidate (unchanged) and — when the edge leads away
  from the burning cell's anchor — the anchor's straight run to that
  neighbour. The earlier wins; the neighbour inherits the winner's anchor (a
  tie keeps the longer run).
* **Every run is a real path.** `_segment_arrival` traces the straight line
  from the anchor's centre to the neighbour's centre cell by cell
  (Amanatides-Woo) and adds each crossed cell's stretch at that cell's OWN
  ellipse (its head rate, L/B and bearing; the edges' 1e-4 m/s floor), under
  the weather of the moment the fire enters the cell (a cell entered before a
  re-anchor's start time prices with that kept_wx history span). A
  non-burnable cell,
  or one that cannot carry fire then, ends the run; through an exact cell
  corner it passes when either side cell is burnable (the lattice's diagonal
  gate rule). So no run arrives earlier than a real path through the engine's
  own per-cell physics.
* **Unchanged:** every lattice edge (source-only rate, gate rule, frozen or
  piecewise weather, kept-edge history), spotting, perimeter seeding, the
  ellipse. Every legacy path is still offered at the same cost, so under
  constant weather no cell burns later than without the knob (exactly, in
  floats; without the SEMANTICS 19 size law, whose ember reach depends on the
  fire's size at ignition).
* **Laws kept.** Label-setting (a run never lands before the cell offering it
  burned — it is clamped to that time); deterministic (no draw); the ROS memo
  exact (memo = `WGRESP_NO_ROS_MEMO=1` reference with the knob on, weather
  zones and piecewise edges included); RE-ANCHOR HISTORY continuous:
  `compute_arrival` / `ensemble_arrival(anchor_log={})` record every labelled
  cell's anchor and `kept_anchor=` resumes each kept cell's run, so a no-op
  re-anchor is the continuous run cell for cell and a mid-run wind shift the
  continuous piecewise run (tests); the simulator threads them
  (`_SimFire._anchors`, a few lines in `simui.tick`; nothing is passed with
  the knob off).
* **Bounds.** A cell more than 256 cells from its anchor offers lattice edges
  only (the run restarts there) — `_ANY_ANGLE_MAX_CELLS`, measured below; a
  run stops tracing as soon as it can no longer beat the neighbour's label; the
  per-cell memory is O(cells).

### 2.2 How it was chosen (each measured; scripts in section 8)

| variant | uniform tiles (vs analytic) | heterogeneous tiles (vs continuum reference) | verdict |
|---|---|---|---|
| bigger neighbourhood (32-176 dirs) | worst head 0.39-0.81 at L/B 8 | — | not enough at any affordable size |
| same-ellipse anchors (run priced at the anchor's ellipse, chain only through identical cells) | exact off-axis; on-axis area 0.95-0.97 | identical to legacy: real tiles never have two neighbouring cells with the same ellipse (3-25 % share a fuel/slope/aspect key) | fixes the probe, inert on real maps |
| same + tolerance on rate / L/B / bearing | — | erratic: median error +24 % to -18 % depending on the tolerance | rejected |
| **runs priced cell by cell** (this fix) | exact at every angle | noise tile 30 mph: median error +88 % -> +3 %, area 0.41 -> 1.00; fuel mosaic +91 % -> +2 % | **adopted** |
| ... offered only through cells the line crosses ("witness") | on-axis area 0.96-0.97 (near-anchor runs offered late) | as below | dropped |
| ... with a 64- / 128-cell run cap | area down to 0.84 / 0.98 at L/B 8 | no run reached 64 cells | cap 256 |
| ... offered over the 8 rook/diagonal edges only | identical | Boulder 30 mph median error +30 % -> +38 % | not adopted (25-40 % cheaper) |
| + lattice edges also priced cell by cell | — | far too slow (Boulder 30 mph: 34 acres against the reference's 1,691): centre-to-centre lines blocked by corners the continuum slips past | rejected |
| + lattice edges half source / half destination | — | fixes Park City low wind, makes Eagle 30 mph worse than legacy (114 vs 148 ac; reference 252) | not bundled |

### 2.3 The continuum reference for real terrain

There is no closed form on a heterogeneous tile, so the reference is the
continuum limit of the engine's own per-cell physics: the same tile split k x k
(every cell into k x k sub-cells with its fuel, slope and aspect), solved with
the fix on, read at each cell's centre sub-cell
(`scratch_review/grid_orientation_hetero.py`). Inside each original cell the
fire then runs straight at its exact time and it can bend at sub-cell
resolution where cells meet. On a uniform tile the reference equals the analytic
field exactly; on a noisy-slope tile k=9 and k=11 agree within ~1 % (median
error +0.9 %, area 1.000), and on Eagle k=5 and k=9 within 1-2 %.

### 2.4 Why it can over-burn at low wind in timber mosaics

The legacy lattice prices a whole edge at its source cell's rate: a fast
grass-shrub cell's knight move into (or over) a slow timber-litter cell runs at
the fast rate. On the Park City mosaic (TL3 / TU1 / TU5 litter and understory
with GS1 / GS2 grass-shrub) that already made legacy burn 362 acres where the
continuum burns 260 at 10 mph; the fix reaches the fast cells sooner and their
knight moves then reach many more slow cells (679 acres; median arrival 15 %
early). The cells it reaches early are timber cells entered by legacy edges
from grass-shrub anchors 2.2 cells away (`out/orient/dbg_pc.py`). Pricing the
lattice edges honestly instead (cell by cell, or half and half) is a separate
physics change — it fixed Park City but made the high-wind cases slower than
legacy — so it is left to the "source-only edge rate" finding.

## 3. Results with the fix

### 3.1 Uniform tiles

GR2 / SH5 tables in 1.1 (GS2 / TU5 in appendix A): the fix burns the cells the
analytic ellipse covers — **IoU 0.98-1.00 in every row** (0.983 on the largest
fire, SH5 40 mph between directions, where the 256-cell run cap shows: 62,271
vs 63,286 acres); heads within 0.4 % in GR2 / GS2 / SH5 and 2.5 % in TU5. The
acres columns compare a cell count with the continuous ellipse, so a fire only
a few cells wide (TU5's 50-500 acres) differs there by its discretization while
its IoU is 1.00. Wind swept through a full lattice gap and on to the diagonal
(`grid_orientation_probe.py dir`, 270 -> 315 in 1.25-degree steps, 2 h):

| fuel, wind | L/B | legacy head / analytic | legacy area / analytic | fix head / analytic | fix area / analytic | fix IoU (min) |
|---|---|---|---|---|---|---|
| GR2, 20 mph | 4.49 | 0.47–0.99 | 0.45–0.68 | 0.972–0.997 | 0.959–1.025 | 1.000 |
| GR2, 40 mph | 8.00 | 0.22–1.00 | 0.16–0.45 | 0.991–1.000 | 0.984–1.008 | 1.000 |
| SH5, 20 mph | 8.00 | 0.23–1.00 | 0.16–0.45 | 0.987–1.000 | 0.989–1.011 | 1.000 |
| SH5, 40 mph | 8.00 | 0.23–1.00 | 0.16–0.44 | 0.995–1.000 | 0.995–1.003 | 1.000 |

The brief's own probe (`scratch_review/wind_area_inversion_probe.py`: 24 x 24
km tile, centre ignition, 6 h — the head reaches the tile edge at 12 km from
about 20 mph, so the high-wind areas are clipped by the map, not the fire):

| fuel, wind from | 10 mph legacy → fix | 20 mph | 25 mph | 40 mph |
|---|---|---|---|---|
| GR2, 270 | 1,483 → 1,759 ac | 2,493 → 4,707 | 2,108 → 5,266 | 4,053 → 7,902 |
| GR2, 281.25 | 1,483 → 1,745 | 2,110 → 4,722 | 1,302 → 5,318 | 3,022 → 8,090 |
| SH5, 270 | 4,898 → 7,038 | 3,086 → 6,823 | 4,324 → 8,194 | 8,053 → 11,656 |
| SH5, 281.25 | 4,781 → 7,047 | 2,004 → 6,958 | 3,371 → 8,407 | 9,506 → 12,009 |

(SH5's 20 mph dip with the fix is the tile edge: its 6-h head is 22 km; on a
tile that holds the fire, table 1.1, SH5 grows 7,047 -> 12,417 acres from 10 to
20 mph.)

The tolerance, stated: **at every wind angle the head is within 3 % and the
area within 4 % of the engine's own ellipse (within 1.6 % at L/B 8)**; the 2-h
GR2 20 mph fire is small enough (3 km) for single cells to show. The legacy
head ranges over 0.22-1.00 of it across the same sweep. Area grows
monotonically with wind (`test_area_grows_with_wind`; tables 1.1 and A).

### 3.2 Real tiles against the continuum reference

`scratch_review/grid_orientation_real.py`: each scenario's tile, ignition and
wind direction, constant weather (13 % RH, 30 C), the simulator's physics
(grass floor 0.10, grass-only floor) with spotting OFF, nominal member; the
reference at k=5. Median arrival error = median over cells the reference burns
in 6 h of (arrival - reference) / (reference - ignition):

| tile (wind from) | wind mph | legacy acres 1/3/6 h | fix acres 1/3/6 h | continuum reference acres 1/3/6 h | median arrival error vs reference, legacy → fix | IoU at 6 h, legacy → fix |
|---|---|---|---|---|---|---|
| boulder_chinook | 10 | 68/1,043/4,822 | 70/1,161/5,024 | 66/1,157/4,544 | +4% → +1% | 0.90 → 0.89 |
| boulder_chinook | 15 | 46/1,027/3,719 | 48/1,333/3,879 | 78/1,487/3,685 | +13% → +7% | 0.90 → 0.90 |
| boulder_chinook | 20 | 20/410/2,580 | 20/647/2,736 | 28/1,081/2,770 | +27% → +14% | 0.87 → 0.91 |
| boulder_chinook | 25 | 22/154/1,367 | 24/260/1,729 | 26/835/2,038 | +67% → +32% | 0.66 → 0.83 |
| boulder_chinook | 30 | 22/194/1,137 | 22/418/1,503 | 34/831/1,691 | +82% → +30% | 0.65 → 0.86 |
| boulder_chinook | 40 | 24/424/1,663 | 24/853/1,978 | 110/1,075/2,098 | +71% → +25% | 0.77 → 0.92 |
| rancho_santa_fe_santa_ana | 10 | 120/925/2,010 | 128/957/2,132 | 130/893/2,014 | +2% → -1% | 0.83 → 0.83 |
| rancho_santa_fe_santa_ana | 15 | 130/871/1,641 | 144/927/1,773 | 164/843/1,729 | +6% → +0% | 0.86 → 0.87 |
| rancho_santa_fe_santa_ana | 20 | 114/578/1,069 | 120/649/1,103 | 132/655/1,231 | +11% → +4% | 0.76 → 0.76 |
| rancho_santa_fe_santa_ana | 25 | 80/394/757 | 108/414/793 | 104/430/847 | +15% → +5% | 0.87 → 0.90 |
| rancho_santa_fe_santa_ana | 30 | 80/306/566 | 116/360/691 | 112/370/695 | +22% → +5% | 0.81 → 0.93 |
| rancho_santa_fe_santa_ana | 40 | 122/392/727 | 174/420/773 | 178/436/875 | +26% → +6% | 0.82 → 0.86 |
| eagle_gypsum_westerly | 10 | 62/546/1,889 | 64/604/1,968 | 58/532/1,689 | +4% → +0% | 0.84 → 0.84 |
| eagle_gypsum_westerly | 15 | 44/406/1,505 | 46/446/1,545 | 46/448/1,411 | +6% → +2% | 0.90 → 0.90 |
| eagle_gypsum_westerly | 20 | 22/144/985 | 24/166/1,045 | 20/188/957 | +8% → +3% | 0.87 → 0.88 |
| eagle_gypsum_westerly | 25 | 12/58/384 | 12/60/474 | 14/58/480 | +12% → +6% | 0.71 → 0.83 |
| eagle_gypsum_westerly | 30 | 12/40/148 | 12/46/194 | 12/44/252 | +7% → +4% | 0.55 → 0.72 |
| eagle_gypsum_westerly | 40 | 16/58/342 | 16/60/426 | 16/60/504 | +13% → +9% | 0.65 → 0.80 |
| park_city_windstorm | 10 | 2/46/362 | 2/74/679 | 6/42/260 | +4% → -15% | 0.63 → 0.38 |
| park_city_windstorm | 15 | 4/28/120 | 4/42/360 | 4/30/204 | +16% → -1% | 0.40 → 0.56 |
| park_city_windstorm | 20 | 4/12/50 | 4/24/82 | 4/24/76 | +15% → +4% | 0.62 → 0.80 |
| park_city_windstorm | 25 | 4/10/32 | 4/10/38 | 4/16/52 | +18% → +18% | 0.62 → 0.67 |
| park_city_windstorm | 30 | 4/8/22 | 4/10/34 | 4/12/46 | +13% → +13% | 0.48 → 0.67 |
| park_city_windstorm | 40 | 4/14/36 | 4/14/58 | 4/22/84 | +26% → +13% | 0.43 → 0.69 |

* At 20-40 mph the fix removes 30-77 % of the legacy lateness on every tile
  (Park City 25-30 mph excepted, unchanged) and raises the overlap.
* The **reference** loses area from 10 to 30 mph on all four tiles: the
  inversion on the real maps is the physics (section 5), not the grid.
* Park City 10-15 mph: over-burn (section 2.4). Boulder's remaining gap at 3 h
  is corner-slipping through its suburbs' non-burnable cells, which a grid
  that passes through cell centres cannot do (`out/orient/dbg_boulder.py`).

### 3.3 The 13 one-click scenarios

`scratch_review/grid_orientation_scenarios.py` (exactly the page's POST
/api/sim/new {scenario}: spotting and floor on, city weather, grass-only floor;
15-min ticks for 6 h; acres = the page's fire area; first home = the nominal
truth reaching a demo home; solve = the ensemble's wall time, 6 members):

| scenario | intensity | acres 1 h legacy → fix | acres 3 h | acres 6 h | first home reached (h) | ensemble solve s (6 members) |
|---|---|---|---|---|---|---|
| aspen_southwesterly | high | 2 → 2 | 2 → 2 | 2 → 2 | none in 6 h → none in 6 h | 1.6 → 4.9 |
| basalt_carbondale_southwesterly | high | 4 → 4 | 4 → 4 | 1205 → 1419 | none in 6 h → none in 6 h | 4.7 → 11.2 |
| beaver_creek_westerly | high | 2 → 2 | 4 → 6 | 32 → 46 | none in 6 h → none in 6 h | 3.4 → 8.3 |
| boulder_chinook | extreme | 154 → 298 | 943 → 1159 | 1895 → 2094 | none in 6 h → none in 6 h | 2.8 → 4.9 |
| eagle_gypsum_westerly | high | 16 → 16 | 440 → 484 | 1015 → 1085 | 3 → 3 | 4.6 → 11.1 |
| eagle_vail_corridor_westerly | high | 99 → 99 | 217 → 237 | 425 → 455 | none in 6 h → none in 6 h | 1.3 → 3.3 |
| glenwood_springs_westerly | high | 8 → 8 | 274 → 290 | 1049 → 1211 | none in 6 h → none in 6 h | 3.9 → 9.3 |
| malibu_santa_ana | extreme | 1365 → 1419 | 3467 → 3583 | 5640 → 5995 | 4.25 → 4 | 3.6 → 6.7 |
| paradise_ne_wind | extreme | 82 → 82 | 408 → 420 | 821 → 889 | 2.75 → 2.75 | 4.7 → 9.7 |
| park_city_windstorm | extreme | 4 → 4 | 12 → 48 | 1517 → 2052 | none in 6 h → none in 6 h | 4.6 → 8.9 |
| rancho_santa_fe_santa_ana | extreme | 330 → 362 | 1225 → 1319 | 3014 → 3122 | 2.25 → 2.25 | 2.0 → 3.5 |
| santa_rosa_diablo | extreme | 56 → 58 | 454 → 486 | 1533 → 1901 | 4 → 3.5 | 3.8 → 8.1 |
| vail_westerly | high | 2 → 2 | 10 → 18 | 70 → 104 | none in 6 h → none in 6 h | 1.4 → 3.1 |

* Every scenario burns at least as much: 6-h area +4 % (Rancho Santa Fe) to
  +49 % (Vail, a small fire), +35 % Park City, +24 % Santa Rosa; Boulder's
  first hour nearly doubles (154 -> 298 acres). Malibu reaches a home 15 min
  sooner, Santa Rosa 30 min; the five that reached a home still do, and the
  eight that reached none in 6 h still reach none (Aspen's fire still sits in
  timber litter: 2 acres).
* The first fire field costs 1.8-3.1x (1.5-6.5 s more). The whole 6-h session
  (24 ticks with dispatch) moved by -4 to +32 s, dominated by dispatch
  planning and machine load, not the field.

### 3.4 Real-fire hindcast (11 fires, 2026)

`scratch_review/grid_orientation_hindcast.py`: the physics stage of the
2026-09-27 study (`docs/review/belief_vs_physics_2026-09-27.md`) — the
production posture (`CFG_PROD`), seeded with each daily perimeter at its own
time under the archive weather, scored at the next perimeter — re-run with the
knob on, against the stored legacy scores (verified to reproduce bit for bit on
the current engine before the run):

| steps | phase | P50 area / burned area (median), legacy → fix | P10 adequacy (share of newly burned cells inside P10), legacy → fix | P50 hit rate, legacy → fix | P50 false-alarm area / fire area (median), legacy → fix | steps whose P50 reached the domain edge, legacy → fix |
|---|---|---|---|---|---|---|
| 255 | all | 3.55x → 3.62x | 99.78 % → 99.82 % | 98.85 % → 98.91 % | 2.53x → 2.61x | 201 → 207 |
| 70 | growing | 5.56x → 5.82x | 99.68 % → 99.78 % | 98.09 % → 98.18 % | 4.54x → 4.83x | 40 → 44 |
| 50 | moderate | 4.14x → 4.33x | 99.90 % → 99.90 % | 99.73 % → 99.75 % | 3.11x → 3.31x | 44 → 44 |
| 135 | stalled | 2.97x → 3.04x | 99.83 % → 99.83 % | 99.26 % → 99.32 % | 1.95x → 2.02x | 117 → 119 |

Per fire (all scored steps):

| fire | steps | P50 area / burned (median), legacy → fix | P10 adequacy, legacy → fix |
|---|---|---|---|
| aspenacres | 21 | 3.32x → 3.38x | 99.99 % → 99.99 % |
| bobcatlakes | 18 | 6.61x → 6.69x | 100.00 % → 100.00 % |
| border2 | 8 | 2.25x → 2.38x | 98.74 % → 98.74 % |
| grasshopper | 28 | 2.86x → 2.87x | 99.96 % → 99.96 % |
| hagen | 36 | 3.89x → 3.95x | 99.84 % → 99.87 % |
| littlegiant | 51 | 2.63x → 2.68x | 99.34 % → 99.49 % |
| plaskett | 8 | 3.00x → 3.01x | 100.00 % → 100.00 % |
| sandcreek | 16 | 5.86x → 6.01x | 100.00 % → 100.00 % |
| sinlahekin | 27 | 2.60x → 2.61x | 99.96 % → 99.96 % |
| timber | 23 | 3.70x → 3.70x | 100.00 % → 100.00 % |
| willow | 19 | 6.08x → 6.31x | 100.00 % → 100.00 % |

Steps where the fix's P10 adequacy is LOWER than legacy's: 3 of 255. Ensemble solve time per step (7 members, run with 3 workers in parallel): median 11.5 s → 26.7 s; total 46 → 105 CPU-minutes (the legacy timings come from the 2026-09-27 run, 10 workers).

* On these 11 mostly-timber fires, with crews on them, the knob changes the
  physics forecast only a little: the P50 footprint grows 2-5 % (pooled 3.55x
  -> 3.62x the burned area; growing days 5.56x -> 5.82x), P10 coverage of the
  newly burned ground rises 99.78 % -> 99.82 % (growing days 99.68 % ->
  99.78 %), and 6 more steps reach the replay domain's edge. The grid is not
  what makes physics over-run these fires (3.5x): the engine models no
  suppression, and they are slow-fuel fires at moderate wind.
* P10 coverage fell on 3 of 255 steps (Little Giant, stalled days), each by 1-2
  newly burned cells of about 2,000 (-0.05 to -0.11 points). Under hourly
  weather an earlier arrival at a cell can leave it in a slower hour (the
  frozen-departure law's non-FIFO caveat, section 4); no run arrives late.

### 3.5 Re-anchors in the simulator, real tiles

`scratch_review/grid_orientation_reanchor.py` runs the city-weather review's
probe (`scratch_review/city_weather_reanchor_probe.run`: real tile, page
physics with spotting, a no-op `set_wind` at T = 2 h 15 min, the nominal burned
mask compared with the uninterrupted run) with the knob off and on:

| scenario, weather | knob | +0.5 / +1 / +3 h | +6 h: area ratio, IoU, mean shift |
|---|---|---|---|
| Park City, generic (constant) | off | identical | 1.000, 1.000, 0 min |
| Park City, generic (constant) | on | identical | 1.000, 1.000, 0 min |
| Park City, city (hourly) | off | identical | 1.005, 0.927, -2.1 min |
| Park City, city (hourly) | on | identical | 0.980, 0.977, +1.4 min |
| Eagle, generic (constant) | off / on | identical | 1.000, 1.000, 0 min (both) |
| Eagle, city (hourly) | off | identical | 0.992, 0.992, +1.1 min |
| Eagle, city (hourly) | on | identical | 0.985, 0.985, +0.7 min |

With the knob the simulator's anchors keep a no-op re-anchor exact under
constant weather, spotting and embers aloft included. Under the hourly city
weather both arms keep the city-weather review's known residual (spread's hour
buckets restart at the anchor), of the same size.

### 3.6 Cost

Wall time per spread solve, legacy → `spread_any_angle` (Windows desktop, CPython 3.14, single process; the scenario timings were taken while other jobs ran, so their ratios matter more than their seconds; the hindcast-domain members were timed back to back, best of two):

| what | legacy | any-angle | ratio |
|---|---|---|---|
| real tiles, nominal member, constant weather, 6.5-h horizon (`grid_orientation_real.py`) | 0.01–0.03 s | 0.01–0.09 s | 1–3× |
| one-click scenarios: first field, 6-member ensemble, city weather, 96-h horizon (table 3.3) | 1.3–4.7 s | 3.1–11.2 s | 1.8–3.1× |
| live-style hindcast domains: one member, perimeter-seeded, 96-h horizon, archive weather, production posture (Willow 184×166, Timber 228×258, Hagen 321×396, Little Giant 604×498 cells) | 0.24 / 0.50 / 1.61 / 1.95 s | 0.63 / 1.68 / 4.93 / 5.72 s | 2.7–3.4× |
| very large uniform fire (SH5 40 mph, 6 h, 63,000 acres, flat uniform tile) | 0.13 s (for a 10,000-acre legacy fire) | 6.2 s | ~12× per burned cell |
| peak memory, Little Giant domain (one member) | 49 MB | 150 MB | 3× |

Where the time goes (profile of one Willow member): about two thirds is tracing runs (`_segment_arrival`), most of it pricing crossed cells at the hour the run reaches them — hours the lattice alone never evaluates for those cells, so the ROS memo grows (that is also the memory). Tried and dropped: offering runs over the 8 rook/diagonal edges only (25–40 % faster on the hindcast domains, but Boulder 30 mph's median error went +30 % → +38 %); keying the slowness memo on the ROS key (slower, no memory saving). Kept: a run stops tracing as soon as it can no longer beat the neighbour's label (about 40 % faster on a hindcast step, identical results), and per-cell O(n) memos. Members run one after another, so an ensemble's peak memory is one member's.

## 4. Safety direction

* Arrivals only move **earlier** (constant weather: exactly never later; the
  hindcast and the scenarios measure the time-varying case). Earlier is the
  safe direction for every P10 deadline, corridor and the EXTREME ladder rung.
* The downside is warning more: wider footprints, earlier P50 — more alerts and
  more conservative dispatch; on the real hindcast fires the engine already
  over-forecasts (3.5x the area), and the fix adds to that.
* The fix never moves a P10 later than legacy under steady weather; under a
  changing forecast an earlier arrival at a cell can, as with any frozen-hour
  edge, meet a slower hour (the pre-existing non-FIFO caveat of
  time-dependent MTT). Measured on the hindcast: 3 of 255 steps lost 1-2 newly
  burned cells (of about 2,000) from P10, while pooled P10 coverage rose
  99.78 % -> 99.82 %.

## 5. What it does not fix

* **The real-map inversion.** The continuum reference shows it (4 of 4 tiles).
  At L/B 8 a fire is a needle; when its head meets non-burnable ground or slow
  timber, growth comes from the flanks, and the elliptical model's flank rate
  `R_head (1 - e)` falls as the wind rises. Real fires broaden through wind
  variability and spotting. The lever is the ellipse model (L/B law and cap),
  wind-direction variability, spotting — not the grid.
* The lattice edges' **source-only rate** (Park City low wind, 2.4).
* **Corners.** A grid that passes through cell centres cannot slip through a
  diagonal gap at a cell corner; the continuum can (Boulder at 3 h).
* **Near-anchor cells.** A run is never offered before the cell offering it
  burned, so the first 2-4 cells from a point ignition (or a spot fire) can
  burn a few minutes late; at L/B 60+ (only without the cap) this stops the
  fire.

## 6. Recommendation

See "For the founder". In short: arm in the simulator; decide the live flip
with the hindcast numbers above and together with a review of the high-wind
fire shape; do not lift the L/B cap.

## 7. Tests

`tests/test_spread_any_angle.py` (26): knob OFF byte-identical to the pinned
legacy fingerprints with the new arguments passed; uniform tile = analytic
elliptical arrival to 0.1 % at six wind angles (and legacy shown far off);
head and area within 2 % across a lattice gap; area grows with wind; never later
than legacy under constant weather on a mosaic; full and gapped barrier
columns; a two-cell diagonal wall; memo = reference (constant / veering
weather, perimeter, offset, piecewise on and off, weather zones);
deterministic; ensemble ordering and nominal-only anchor log; the run cap;
no-op re-anchor = continuous run with kept_anchor (and drifts without it);
wind-shift re-anchor = continuous piecewise run; foreign anchors ignored; the
simulator's same-wind re-anchor = continuous run; knob off records no anchors.

## 8. Reproduction

| script | what |
|---|---|
| `scratch_review/wind_area_inversion_probe.py` | the integrator's original probe |
| `scratch_review/grid_orientation_common.py` | uniform tiles, analytic ellipse, lattice polygon |
| `scratch_review/grid_orientation_probe.py wind / dir / lbcap` | tables 1.1, 1.4, 3.1 (`grid_orientation_probe_*.json`) |
| `scratch_review/grid_orientation_p10.py` | table 1.5 (`grid_orientation_p10_h96.json`) |
| `scratch_review/grid_orientation_stencils.py` | table 1.6 (analytic, no solve) |
| `scratch_review/grid_orientation_hetero.py noise / mosaic 9` | the continuum reference; synthetic noise / mosaic tiles (table 2.2) |
| `scratch_review/grid_orientation_real.py SCEN 5` | table 3.2 (`grid_orientation_real_*.json`) |
| `scratch_review/grid_orientation_scenarios.py OUT.json ['{"spread_any_angle": true}']` | table 3.3 (`grid_orientation_scenarios_*.json`) |
| `scratch_review/grid_orientation_hindcast.py verify / run / collect` | table 3.4 (`grid_orientation_hindcast.json`; inputs copied read-only from the 2026-09-27 study's cache into `out/bvp_data/`, its stored legacy scores read in place — env `BVP_LEGACY_RUNS`) |
| `scratch_review/grid_orientation_reanchor.py` | table 3.5 (`grid_orientation_reanchor.json`) |

## Appendix A. GS2 and TU5 (uniform tiles, 6 h)

**GS2, wind from 270** (on a lattice axis), 6 h

| wind mph | L/B | legacy acres | lattice polygon | fix acres | analytic acres | legacy head km | fix head km | analytic head km | IoU legacy → fix |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1.00 | 32 | 35 | 32 | 36 | 0.2 | 0.2 | 0.2 | 1.00 → 1.00 |
| 5 | 1.41 | 225 | 223 | 240 | 240 | 1.1 | 1.1 | 1.1 | 0.94 → 1.00 |
| 10 | 2.12 | 640 | 624 | 754 | 757 | 2.7 | 2.7 | 2.7 | 0.85 → 1.00 |
| 15 | 3.30 | 917 | 903 | 1,391 | 1,374 | 4.7 | 4.7 | 4.7 | 0.66 → 1.00 |
| 20 | 5.17 | 912 | 903 | 1,895 | 1,911 | 7.0 | 7.0 | 7.1 | 0.48 → 1.00 |
| 25 | 8.00 | 741 | 743 | 2,313 | 2,301 | 9.7 | 9.7 | 9.7 | 0.32 → 1.00 |
| 30 | 8.00 | 1,250 | 1,251 | 3,825 | 3,876 | 12.5 | 12.5 | 12.6 | 0.33 → 1.00 |
| 40 | 8.00 | 2,869 | 2,861 | 8,804 | 8,864 | 19.0 | 19.0 | 19.0 | 0.33 → 1.00 |


**GS2, wind from 281.25** (between lattice directions), 6 h

| wind mph | L/B | legacy acres | lattice polygon | fix acres | analytic acres | legacy head km | fix head km | analytic head km | IoU legacy → fix |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1.00 | 32 | 35 | 32 | 36 | 0.2 | 0.2 | 0.2 | 1.00 → 1.00 |
| 5 | 1.41 | 217 | 223 | 237 | 240 | 1.0 | 1.1 | 1.1 | 0.92 → 1.00 |
| 10 | 2.12 | 625 | 626 | 754 | 757 | 2.3 | 2.7 | 2.7 | 0.83 → 1.00 |
| 15 | 3.30 | 865 | 863 | 1,369 | 1,374 | 3.3 | 4.7 | 4.7 | 0.63 → 1.00 |
| 20 | 5.17 | 704 | 698 | 1,908 | 1,911 | 3.4 | 7.0 | 7.1 | 0.37 → 1.00 |
| 25 | 8.00 | 376 | 370 | 2,296 | 2,301 | 2.7 | 9.7 | 9.7 | 0.16 → 1.00 |
| 30 | 8.00 | 623 | 623 | 3,870 | 3,876 | 3.5 | 12.6 | 12.6 | 0.16 → 1.00 |
| 40 | 8.00 | 1,423 | 1,424 | 8,866 | 8,864 | 5.4 | 19.0 | 19.0 | 0.16 → 1.00 |


**TU5, wind from 270** (on a lattice axis), 6 h

| wind mph | L/B | legacy acres | lattice polygon | fix acres | analytic acres | legacy head km | fix head km | analytic head km | IoU legacy → fix |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1.00 | 22 | 19 | 22 | 20 | 0.1 | 0.1 | 0.2 | 1.00 → 1.00 |
| 5 | 1.37 | 57 | 52 | 57 | 56 | 0.5 | 0.5 | 0.5 | 1.00 → 1.00 |
| 10 | 1.99 | 94 | 94 | 109 | 111 | 0.9 | 0.9 | 1.0 | 0.86 → 1.00 |
| 15 | 2.97 | 109 | 109 | 158 | 156 | 1.5 | 1.5 | 1.5 | 0.69 → 1.00 |
| 20 | 4.49 | 106 | 98 | 200 | 184 | 2.0 | 2.0 | 2.0 | 0.53 → 1.00 |
| 25 | 6.79 | 82 | 73 | 180 | 196 | 2.6 | 2.6 | 2.6 | 0.45 → 1.00 |
| 30 | 8.00 | 79 | 80 | 213 | 248 | 3.1 | 3.1 | 3.2 | 0.37 → 1.00 |
| 40 | 8.00 | 171 | 153 | 467 | 474 | 4.4 | 4.4 | 4.4 | 0.37 → 1.00 |


**TU5, wind from 281.25** (between lattice directions), 6 h

| wind mph | L/B | legacy acres | lattice polygon | fix acres | analytic acres | legacy head km | fix head km | analytic head km | IoU legacy → fix |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1.00 | 22 | 19 | 22 | 20 | 0.1 | 0.1 | 0.2 | 1.00 → 1.00 |
| 5 | 1.37 | 49 | 53 | 54 | 56 | 0.5 | 0.5 | 0.5 | 0.91 → 1.00 |
| 10 | 1.99 | 91 | 94 | 104 | 111 | 0.8 | 0.9 | 1.0 | 0.88 → 1.00 |
| 15 | 2.97 | 109 | 107 | 161 | 156 | 1.1 | 1.4 | 1.5 | 0.68 → 1.00 |
| 20 | 4.49 | 79 | 83 | 175 | 184 | 1.1 | 1.9 | 2.0 | 0.45 → 1.00 |
| 25 | 6.79 | 52 | 44 | 195 | 196 | 0.9 | 2.5 | 2.6 | 0.27 → 1.00 |
| 30 | 8.00 | 49 | 40 | 250 | 248 | 0.9 | 3.2 | 3.2 | 0.20 → 1.00 |
| 40 | 8.00 | 84 | 76 | 472 | 474 | 1.2 | 4.3 | 4.4 | 0.18 → 1.00 |
