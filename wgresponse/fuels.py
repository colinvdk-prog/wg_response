"""wgresponse.fuels — FBFM40 fuel model parameters + fuel moisture helpers.

FUEL_TABLE values are SI at the seam (contracts.FuelModel): loads kg/m^2,
SAV 1/m, depth m, moisture-of-extinction fraction, heat kJ/kg.

Provenance: Scott & Burgan 2005 (RMRS-GTR-153) FBFM40 parameter rows as pinned
in pyretechnics ``src/pyretechnics/fuel_models.py`` (EPL-2.0), fetched 2026-07-06
(initial 14-row subset) and re-fetched 2026-07-09 (Sprint 2 ticket A3: ALL 40
burnable rows) from
https://raw.githubusercontent.com/pyregence/pyretechnics/main/src/pyretechnics/fuel_models.py
(source-table imperial units: loads lb/ft^2, SAV ft^2/ft^3, depth ft, M_x percent).
Spot-checked against the RMRS-GTR-153 tons/acre values quoted in
docs/research/spread_model.md (GR2 1h 0.10 t/ac, herb 1.0 t/ac; SH5 3.6/2.1/2.9;
TL3 0.5/2.2/2.8; t/ac x 0.0459137 = lb/ft^2) and, for the 2026-07-09 rows,
GR3 0.10/0.40/1.50 t/ac -> 0.0046/0.0184/0.0689 lb/ft^2, GR7 1.0/5.4 ->
0.0459/0.2479, TL6 2.4/1.2/1.2 -> 0.1102/0.0551/0.0551.
"""
from __future__ import annotations

from wgresponse.contracts import FuelModel

# --- unit conversion factors (exact definitions) ---------------------------
# 1 lb = 0.45359237 kg (exact); 1 ft = 0.3048 m (exact) -> 1 ft^2 = 0.09290304 m^2
LB_FT2_TO_KGM2 = 0.45359237 / 0.09290304     # = 4.88242764... kg/m^2 per lb/ft^2
FT2FT3_TO_1M = 1.0 / 0.3048                  # SAV: 1/ft -> 1/m  (= 3.2808399)
FT_TO_M = 0.3048                             # depth ft -> m
BTU_LB_TO_KJKG = 2.326                       # 1 Btu/lb = 2.326 kJ/kg (exact by definition of IT Btu/cal)

_HEAT_BTU_LB = 8000.0                        # standard S&B heat content for FBFM40 models...
# ...with ONE exception: GR6 carries h = 9000 Btu/lb in RMRS-GTR-153 (pinned as
# h=9.0 in the pyretechnics compact table; every other model is 8.0).
_HEAT_OVERRIDE_BTU_LB = {106: 9000.0}

# Imperial source rows, verbatim from pyretechnics fuel_models.py (values verified
# against RMRS-GTR-153 as noted above). Tuple layout:
#   (name, w_1h, w_10h, w_100h, w_live_herb, w_live_woody,   [lb/ft^2]
#    sav_1h, sav_live_herb, sav_live_woody,                  [ft^2/ft^3]
#    depth_ft, mx_dead_pct)
# 10-h SAV is 109 ft^-1 and 100-h SAV is 30 ft^-1 for every model (S&B standard);
# those constants live in wgresponse.rothermel, not per-row here.
_IMPERIAL_ROWS = {
    # --- non-burnable NB1..NB9 (urban, snow/ice, agriculture, water, barren) ---
    91:  ("NB1 urban",              0.0,    0.0,    0.0,    0.0,    0.0,    0.0,    0.0,    0.0,    0.0, 0.0),
    92:  ("NB2 snow/ice",           0.0,    0.0,    0.0,    0.0,    0.0,    0.0,    0.0,    0.0,    0.0, 0.0),
    93:  ("NB3 agriculture",        0.0,    0.0,    0.0,    0.0,    0.0,    0.0,    0.0,    0.0,    0.0, 0.0),
    98:  ("NB8 water",              0.0,    0.0,    0.0,    0.0,    0.0,    0.0,    0.0,    0.0,    0.0, 0.0),
    99:  ("NB9 barren",             0.0,    0.0,    0.0,    0.0,    0.0,    0.0,    0.0,    0.0,    0.0, 0.0),
    # --- grass GR1-9 (dynamic) ---
    101: ("GR1 short sparse grass", 0.0046, 0.0,    0.0,    0.0138, 0.0,    2200.0, 2000.0, 0.0,    0.4, 15.0),
    102: ("GR2 low load grass",     0.0046, 0.0,    0.0,    0.0459, 0.0,    2000.0, 1800.0, 0.0,    1.0, 15.0),
    103: ("GR3 low load very coarse grass",0.0046,0.0184,0.0,0.0689,0.0,    1500.0, 1300.0, 0.0,    2.0, 30.0),
    104: ("GR4 moderate load grass",0.0115, 0.0,    0.0,    0.0872, 0.0,    2000.0, 1800.0, 0.0,    2.0, 15.0),
    105: ("GR5 low load humid grass",0.0184,0.0,    0.0,    0.1148, 0.0,    1800.0, 1600.0, 0.0,    1.5, 40.0),
    106: ("GR6 moderate load humid grass",0.0046,0.0,0.0,   0.1561, 0.0,    2200.0, 2000.0, 0.0,    1.5, 40.0),
    107: ("GR7 high load dry grass",0.0459, 0.0,    0.0,    0.2479, 0.0,    2000.0, 1800.0, 0.0,    3.0, 15.0),
    108: ("GR8 high load very coarse grass",0.0230,0.0459,0.0,0.3352,0.0,   1500.0, 1300.0, 0.0,    4.0, 30.0),
    109: ("GR9 very high load humid grass",0.0459,0.0459,0.0,0.4132,0.0,    1800.0, 1600.0, 0.0,    5.0, 40.0),
    # --- grass-shrub GS1-4 (dynamic) ---
    121: ("GS1 low grass-shrub",    0.0092, 0.0,    0.0,    0.0230, 0.0298, 2000.0, 1800.0, 1800.0, 0.9, 15.0),
    122: ("GS2 moderate grass-shrub",0.0230,0.0230, 0.0,    0.0275, 0.0459, 2000.0, 1800.0, 1800.0, 1.5, 15.0),
    123: ("GS3 moderate humid grass-shrub",0.0138,0.0115,0.0,0.0666,0.0574, 1800.0, 1600.0, 1600.0, 1.8, 40.0),
    124: ("GS4 high humid grass-shrub",0.0872,0.0138,0.0046,0.1561, 0.3260, 1800.0, 1600.0, 1600.0, 2.1, 40.0),
    # --- shrub SH1-9 ---
    141: ("SH1 low load dry shrub", 0.0115, 0.0115, 0.0,    0.0069, 0.0597, 2000.0, 1800.0, 1600.0, 1.0, 15.0),
    142: ("SH2 moderate load shrub",0.0620, 0.1102, 0.0344, 0.0,    0.1768, 2000.0, 0.0,    1600.0, 1.0, 15.0),
    143: ("SH3 moderate load humid shrub",0.0207,0.1377,0.0,0.0,    0.2847, 1600.0, 0.0,    1400.0, 2.4, 40.0),
    144: ("SH4 low load humid timber-shrub",0.0390,0.0528,0.0092,0.0,0.1171,2000.0, 1800.0, 1600.0, 3.0, 30.0),
    145: ("SH5 high load shrub",    0.1653, 0.0964, 0.0,    0.0,    0.1331, 750.0,  0.0,    1600.0, 6.0, 15.0),
    146: ("SH6 low load humid shrub",0.1331,0.0666, 0.0,    0.0,    0.0643, 750.0,  0.0,    1600.0, 2.0, 30.0),
    147: ("SH7 very high load shrub",0.1607,0.2433, 0.1010, 0.0,    0.1561, 750.0,  0.0,    1600.0, 6.0, 15.0),
    148: ("SH8 high load humid shrub",0.0941,0.1561,0.0390, 0.0,    0.1997, 750.0,  0.0,    1600.0, 3.0, 40.0),
    149: ("SH9 very high load humid shrub",0.2066,0.1125,0.0,0.0712,0.3214, 750.0,  1800.0, 1500.0, 4.4, 40.0),
    # --- timber-understory TU1-5 ---
    161: ("TU1 light timber-grass-shrub",0.0092,0.0413,0.0689,0.0092,0.0413,2000.0, 1800.0, 1600.0, 0.6, 20.0),
    162: ("TU2 moderate timber-shrub",0.0436,0.0826, 0.0574, 0.0,   0.0092, 2000.0, 0.0,    1600.0, 1.0, 30.0),
    163: ("TU3 moderate timber-grass-shrub",0.0505,0.0069,0.0115,0.0298,0.0505,1800.0,1600.0,1400.0,1.3, 30.0),
    164: ("TU4 dwarf conifer with understory",0.2066,0.0,0.0,0.0,   0.0918, 2300.0, 0.0,    2000.0, 0.5, 12.0),
    165: ("TU5 very high timber-shrub",0.1837,0.1837,0.1377, 0.0,   0.1377, 1500.0, 0.0,    750.0,  1.0, 25.0),
    # --- timber litter TL1-9 ---
    181: ("TL1 low load compact conifer litter",0.0459,0.1010,0.1653,0.0,0.0,2000.0, 0.0,   0.0,    0.2, 30.0),
    182: ("TL2 low load broadleaf litter",0.0643,0.1056,0.1010,0.0, 0.0,    2000.0, 0.0,    0.0,    0.2, 25.0),
    183: ("TL3 moderate conifer litter",0.0230,0.1010,0.1286,0.0,   0.0,    2000.0, 0.0,    0.0,    0.3, 20.0),
    184: ("TL4 small downed logs",  0.0230, 0.0689, 0.1928, 0.0,    0.0,    2000.0, 0.0,    0.0,    0.4, 25.0),
    185: ("TL5 high load conifer litter",0.0528,0.1148,0.2020,0.0,  0.0,    2000.0, 0.0,    1600.0, 0.6, 25.0),
    186: ("TL6 moderate load broadleaf litter",0.1102,0.0551,0.0551,0.0,0.0,2000.0, 0.0,    0.0,    0.3, 25.0),
    187: ("TL7 large downed logs",  0.0138, 0.0643, 0.3719, 0.0,    0.0,    2000.0, 0.0,    0.0,    0.4, 25.0),
    188: ("TL8 long-needle litter", 0.2663, 0.0643, 0.0505, 0.0,    0.0,    1800.0, 0.0,    0.0,    0.3, 35.0),
    189: ("TL9 very high load broadleaf litter",0.3053,0.1515,0.1905,0.0,0.0,1800.0,0.0,    1600.0, 0.6, 35.0),
    # --- slash-blowdown SB1-4 ---
    201: ("SB1 low load blowdown",  0.0689, 0.1377, 0.5051, 0.0,    0.0,    2000.0, 0.0,    0.0,    1.0, 25.0),
    202: ("SB2 moderate load blowdown",0.2066,0.1951,0.1837, 0.0,   0.0,    2000.0, 0.0,    0.0,    1.0, 25.0),
    203: ("SB3 high load blowdown", 0.2525, 0.1263, 0.1377, 0.0,    0.0,    2000.0, 0.0,    0.0,    1.2, 25.0),
    204: ("SB4 very high load blowdown",0.2410,0.1607,0.2410,0.0,   0.0,    2000.0, 0.0,    0.0,    2.7, 25.0),
}


def _build(code: int, row: tuple) -> FuelModel:
    (name, w1, w10, w100, wherb, wwoody, s1, sherb, swoody, depth_ft, mx_pct) = row
    return FuelModel(
        code=code,
        name=name,
        # S&B semantics: any model carrying live herbaceous load is dynamic
        # (cured herb transfers to dead) — GR/GS + TU1 here. RMRS-GTR-153 table 8.
        dynamic=wherb > 0.0,
        load_1h_kgm2=w1 * LB_FT2_TO_KGM2,
        load_10h_kgm2=w10 * LB_FT2_TO_KGM2,
        load_100h_kgm2=w100 * LB_FT2_TO_KGM2,
        load_live_herb_kgm2=wherb * LB_FT2_TO_KGM2,
        load_live_woody_kgm2=wwoody * LB_FT2_TO_KGM2,
        sav_1h_1m=s1 * FT2FT3_TO_1M,
        sav_live_herb_1m=sherb * FT2FT3_TO_1M,
        sav_live_woody_1m=swoody * FT2FT3_TO_1M,
        depth_m=depth_ft * FT_TO_M,
        mx_dead_frac=mx_pct / 100.0,
        # 8000 Btu/lb = 18608 kJ/kg for every model except GR6 (9000 = 20934)
        heat_kjkg=_HEAT_OVERRIDE_BTU_LB.get(code, _HEAT_BTU_LB) * BTU_LB_TO_KJKG,
    )


#: FBFM40 code -> FuelModel (SI units). COMPLETE (Sprint 2 ticket A3): all 40
#: Scott & Burgan burnable models (GR1-9, GS1-4, SH1-9, TU1-5, TL1-9, SB1-4)
#: plus the non-burnable NB codes. Every code with is_burnable(code) True that
#: exists in the FBFM40 spec has a parameter row; spread.py additionally
#: guarantees that any burnable code WITHOUT a row (future/dynamic codes) falls
#: back to conservative-fast GR2 params rather than acting as a barrier.
FUEL_TABLE: dict = {code: _build(code, row) for code, row in _IMPERIAL_ROWS.items()}


def is_burnable(code: int) -> bool:
    """True iff `code` is a burnable FBFM40 fuel model.

    NB codes 91..99 are non-burnable (contracts). Burnable FBFM40 codes occupy
    101..204 (GR/GS/SH/TU/TL/SB). Anything outside the FBFM40 namespace
    (0, negative nodata, >204) is treated as non-burnable — fail-safe for
    nodata cells. Deliberately NOT `code < 91` (historic off-by-namespace bug).
    """
    return 100 <= code <= 204


def is_grass(code: int) -> bool:
    """True iff `code` is a pure GRASS FBFM40 model (GR1-9, codes 101-109).

    Used by the SIMULATOR's Cruz-Alexander grass head-ROS floor
    (spread.compute_arrival, cfg.grass_ros_floor_frac): Rothermel structurally
    under-predicts grass spread at low-to-moderate midflame wind
    (docs/review/fire_speed_review.md §2b). Grass-shrub is is_grass_shrub."""
    return 101 <= code <= 109


def is_grass_shrub(code: int) -> bool:
    """True iff `code` is a GRASS-SHRUB FBFM40 model (GS1-4, codes 121-124).

    The ROS floor applies to GS at a reduced fraction (the review's 0.07 GS /
    0.10 GR ratio) — grass-shrub carries live woody load and spreads slower than
    pure grass at the same wind, so it takes a softer floor."""
    return 121 <= code <= 124


def cold_black_minutes(code: int) -> float:
    """Per-class COLD-BLACK window (minutes) — stage 1 of the transit-hazard
    model (docs/transit_hazard_determination.md, founder 2026-07-30).

    How long after the flaming front's arrival a cell of this fuel remains
    actively hazardous for TRANSIT ROUTE SELECTION (the SEM-10 carve-out)
    and — under contracts old_black_refuge only (founder ruling 2026-09-26,
    "old burned ground does not count as a violation") — the age at which
    burned ground becomes OLD BLACK, a crew refuge the 10c audit no longer
    counts; with that knob off the audit never consults it. Derived as
    max(class floor,
    3 x Anderson 1969 residence t_r = 384/sigma of the heaviest-loaded
    timelag class present) — the same t_r formula the engine already uses in
    rothermel.surface_ros_from_bed. The legacy flat 45 min is numerically
    the residence time of a 1000-hour log: right for downed timber, 9x too
    long for cured grass. Grass opens up; timber gets STRICTER (snag /
    hazard-tree floor the surface model cannot see) — honest in both
    directions. Unknown burnable codes fail safe to the LONGEST window."""
    if 101 <= code <= 109:                      # GR grass
        # GR3/GR8/GR9 carry a 10-h load (t_r 3.5 min); the rest are 1-h only
        return 10.0 if code in (103, 108, 109) else 5.0
    if 121 <= code <= 124:                      # GS grass-shrub
        return 20.0 if code == 124 else 10.0    # GS4 carries 100-h load
    if 141 <= code <= 149:                      # SH shrub
        # SH2/4/7/8 carry 100-h load; the rest are live-woody-stem beds
        return 30.0 if code in (142, 144, 147, 148) else 20.0
    if 161 <= code <= 165:                      # TU timber-understory
        return 45.0                             # snag floor (unchanged)
    if 181 <= code <= 189:                      # TL timber-litter
        return 60.0                             # stricter: downed logs
    if 201 <= code <= 204:                      # SB slash-blowdown
        return 90.0                             # heaviest 100-h loads
    if not is_burnable(code):
        # Non-burnable cells never carry a finite front arrival, so the
        # window is unreachable behind the burned-cell guard — but a future
        # caller outside that guard must not inherit a 90-min claim about
        # pavement. Flat legacy window, explicit.
        return 45.0
    return 90.0                                 # unknown burnable: fail long


def is_shrub(code: int) -> bool:
    """True iff `code` is a SHRUB FBFM40 model (SH1-9, codes 141-149).

    Used by the fuel-TIERED corridor erosion (contracts
    corridor_erosion_tiered): shrub/chaparral spreads slowly relative to
    grass (small position-error halo) but carries the LONGEST flame lengths
    of the surface classes (SH5 ~9.6 m -> ~112 m standing standoff), so it
    gets its own erosion tier sized by flame standoff, not spread rate."""
    return 141 <= code <= 149


def is_slash(code: int) -> bool:
    """True iff `code` is a SLASH-BLOWDOWN FBFM40 model (SB1-4, 201-204)."""
    return 201 <= code <= 204


def is_timber_understory(code: int) -> bool:
    """True iff `code` is a TIMBER-UNDERSTORY FBFM40 model (TU1-5, codes 161-165).

    The SIMULATOR's ROS realism floor (spread.compute_arrival,
    cfg.grass_ros_floor_frac) ALSO lifts timber-understory — the fuel that
    carries the developed/timber tiles (santa_rosa_ca TU2, paradise_ca TU5).
    TU is a grass/shrub understory beneath a timber overstory that Rothermel
    drives at only ~0.3-2.2% of the 10-m wind (measured TU1 0.3-0.5%, TU2
    0.9-2.2%, TU5 0.9-1.3%) — structurally under-driven like grass but sheltered.
    Floored at a MODEST 0.5 * the grass frac (=> 0.05*U10 at the sim's 0.10),
    BELOW the grass-shrub floor, so it reaches a plausible wind-driven understory
    rate without matching open grass. See docs/review/fire_speed_review.md §2c."""
    return 161 <= code <= 165


def is_timber_litter(code: int) -> bool:
    """True iff `code` is a TIMBER-LITTER FBFM40 model (TL1-9, codes 181-189).

    The SIMULATOR's ROS realism floor applies to TL at the LOWEST fraction of the
    floored classes (0.3 * the grass frac => 0.03*U10 at the sim's 0.10):
    compact conifer/broadleaf litter is genuinely the slowest surface fuel
    (Rothermel drives it at 0.1-1.0% of the 10-m wind), so it STAYS the slowest
    class — but a demo fire crossing a WUI timber-litter mosaic under a 40-mph
    wind should not CRAWL at <0.2 km/h (santa_rosa_ca is ~20% TL, paradise_ca
    ~19%). 0.03 is a low, NON-crawling floor (1.35 km/h at 'high', 1.93 at
    'extreme'). See docs/review/fire_speed_review.md §2c."""
    return 181 <= code <= 189


def dead_fuel_moisture_frac(temp_c: float, rh_pct: float) -> float:
    """Dead 1-h fuel moisture content as a FRACTION (kg water / kg dry fuel).

    Inputs: air temperature in Celsius, relative humidity in percent (0-100).
    Simard (1968) equilibrium moisture content piecewise (T in Fahrenheit, H in
    %RH, EMC in percent — verified against WSL Wikifire, see research digest
    §1.7) multiplied by 1.03 (Fosberg/NFDRS 1-h fuel multiplier), floored at
    0.01 (1%) per NFDRS practice for extreme conditions.
    """
    t_f = temp_c * 9.0 / 5.0 + 32.0
    h = min(max(rh_pct, 0.0), 100.0)
    if h < 10.0:
        emc_pct = 0.03229 + 0.281073 * h - 0.000578 * h * t_f
    elif h < 50.0:
        emc_pct = 2.22749 + 0.160107 * h - 0.01478 * t_f
    else:
        emc_pct = 21.0606 + 0.005565 * h * h - 0.00035 * h * t_f - 0.483199 * h
    return max(0.01, 1.03 * emc_pct / 100.0)


# Season -> (live herbaceous, live woody) moisture FRACTIONS.
# Scott & Burgan 2005 moisture scenarios / NWCG PMS-437 defaults (research digest
# §1.8). Fire-season cured default (0.30, 0.60) per contracts; alerting polarity
# is fail-dangerous-fast, so within-band values sit at the dry end.
_LIVE_DEFAULTS = {
    "winter": (1.20, 1.20),        # CA rainy-season green-up; no herb curing at >= 1.20
    "spring": (1.20, 1.20),        # green-up
    "early_summer": (0.90, 0.90),
    "late_summer": (0.60, 0.75),   # partly cured
    "summer": (0.30, 0.60),        # fire season, cured (contracts default)
    "fall": (0.30, 0.60),
    "autumn": (0.30, 0.60),
    "fire_season": (0.30, 0.60),
    "extreme": (0.30, 0.60),       # Santa Ana / drought
}


def live_moisture_defaults(season: str) -> tuple:
    """(live_herb_frac, live_woody_frac) moisture defaults for `season`.

    Fractions (0.30 = 30% of dry weight). Known seasons (case-insensitive):
    winter, spring, early_summer, late_summer, summer, fall, autumn,
    fire_season, extreme. Unknown season raises ValueError (never silently
    guess moisture for an alerting product).
    """
    key = season.strip().lower().replace("-", "_").replace(" ", "_")
    if key not in _LIVE_DEFAULTS:
        raise ValueError(f"unknown season {season!r}; known: {sorted(_LIVE_DEFAULTS)}")
    return _LIVE_DEFAULTS[key]
