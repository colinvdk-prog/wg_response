# PASS-2 VERIFICATION — code-correctness aspect (verifier: Claude/Opus; primary: Luna)

Repro scripts rv2code_*.py (14); integrator fidelity-gated byte-identical
vs spread.compute_arrival before every reported number.

1. Off-raster fail-open: CONFIRMED CRITICAL, failure MODE CORRECTED —
   "entirely outside" unreachable (0/720; domain anchors on client); the
   reachable defect is PARTIAL coverage: 4.0% FALSE-CLEAR of 720 realistic
   geometries, worst 7.85 h past honest latest-safe-departure, egress legs
   24-84% unsampled; truncation MANUFACTURES UNREACHED (2,024/2,024 cells
   behind a 2-cell NB wall) which safety.py:353 reads as safe and the
   ladder reads as "no fire ever". Composition of two defects.
2. Frozen edge weather: CONFIRMED HIGH, late polarity — 46.3 min constant
   offset (step case) propagated downstream; ramps +1-14%; TL edges span
   up to 1,496 min so the 1-h freeze is structural on flank/backing edges.
3. Source-only edge ROS: CONFIRMED deviation, RESEVERITIZED HIGH ->
   LOW/MEDIUM — bounded boundary term (does not accumulate; algebra +
   empirics); head-axis +11..+39 min late. AND THE SPEC IS WRONG:
   mean(R_A,R_B) errs -81..-104 min (harmonic mean is correct). Fix doc.
4. One-sided coverage: CONFIRMED HIGH, three proofs + MUTATION CONTROL:
   an 80 m/s (10x wind) model scores 98.95%/1.05%/0.00% — BETTER than the
   deployed 93.8%. Coverage is maximizable by absurdity; early_miss branch
   is dead code. late_miss + head-ratios survive as the real metrics.
5. Slope/wind in speed-space: CONFIRMED MEDIUM — 30% slope, 5 m/s:
   aligned +21.6..+31.0% fast; OPPOSED -11.1..-33.5% slow (anti-safe;
   downslope Santa Ana geometry); worst -63.6%; bearing off ~10 deg;
   EXACTLY ZERO on flat terrain (flat hindcast corpus unaffected).
6. Ensemble: CONFIRMED MEDIUM and worse — the *0.5 is a HARD CEILING:
   wind band converges to ~+/-12.2% and can NEVER reach the documented
   +/-25% at any member count; realized shipped bands +/-10.0% wind /
   +/-8.0% moisture / +/-24 deg direction.
7. validate_config: CONFIRMED MEDIUM — 22/22 unsound configs accepted;
   corridor_erosion_m=-500 silently DISABLES erosion; validate_config is
   NEVER CALLED on the CLI/watcher path (live flags are bare floats).
8. 1e-4 ROS floor: CONFIRMED LOW at 90 m; LIVE one knob away — at
   grid_cell_m=30 (blessed by validate_config) zero-ROS fuel propagates
   fabricated 83.3 h arrivals.
9. Raster negative-index wrap: CONFIRMED LOW (latent; current callers
   clamp/delegate).
10. sorted[n//2] median: CONFIRMED — published 1.77x calibration head is
    really 1.61x (+10.0%); 2.78x and 1.88x carry the same even-n bias.

NEW N4 (MEDIUM, product-critical): p50 at a head-axis client = 4.18x the
engine's own nominal arrival at every range 1-16 km (direction perturbation
rotates the head off fixed points; median member arrives via flank).
Ladder p50 rungs ~4x inflated; "~X h (median)" client copy materially
wrong; the p10<=12h rung is the load-bearing near-term catch.
NEW N5 (info): +/-45 m quantization worth up to 101 min of p10 off-axis
(<=1.3 min on-axis). REFUTED (verifier's own hypothesis): uniform-terrain
domain truncation does NOT bias p10 (0.00 min over 3,095 aligned cells);
the +69 min first-pass signal was cell misalignment. Luna's "no-client
incident -> no raster" hole: real in code, unreachable in practice (60 km
threat radius).

VERDICT FOR USES: shadow — keep running (errors late-biased, not
silencing) with the record restated; client alerting — p50 rungs/copy
must be fixed first; dispatch — HOLD until off-raster fails closed and
the spread domain covers safe zones + crew bases + route geometry.
