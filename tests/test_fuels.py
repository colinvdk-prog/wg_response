"""Tests for wgresponse.fuels — FBFM40 table pins, moisture helpers."""
import pytest

from wgresponse import rothermel
from wgresponse.fuels import (
    BTU_LB_TO_KJKG,
    FT2FT3_TO_1M,
    FT_TO_M,
    FUEL_TABLE,
    LB_FT2_TO_KGM2,
    dead_fuel_moisture_frac,
    is_burnable,
    is_grass,
    is_grass_shrub,
    is_timber_litter,
    is_timber_understory,
    live_moisture_defaults,
)

REQUIRED_CODES = [101, 102, 104, 121, 122, 142, 145, 147, 161, 165, 183, 185, 188, 201,
                  91, 92, 93, 98, 99]

# All 40 Scott & Burgan FBFM40 burnable models (RMRS-GTR-153).
FBFM40_BURNABLE = ([101 + i for i in range(9)] +      # GR1-9
                   [121 + i for i in range(4)] +      # GS1-4
                   [141 + i for i in range(9)] +      # SH1-9
                   [161 + i for i in range(5)] +      # TU1-5
                   [181 + i for i in range(9)] +      # TL1-9
                   [201 + i for i in range(4)])       # SB1-4


def test_table_covers_required_codes():
    for code in REQUIRED_CODES:
        assert code in FUEL_TABLE
        assert FUEL_TABLE[code].code == code


# --- Sprint 2 ticket A3 (Council-1 model F1): FULL 40-model FBFM40 coverage ---

def test_full_fbfm40_coverage():
    """Every burnable FBFM40 code has a FUEL_TABLE row (Council-1 F1: missing
    rows acted as fireproof walls in spread.compute_arrival)."""
    assert len(FBFM40_BURNABLE) == 40
    missing = [c for c in FBFM40_BURNABLE if c not in FUEL_TABLE]
    assert missing == []
    for code in FBFM40_BURNABLE:
        assert is_burnable(code) is True
        assert FUEL_TABLE[code].code == code


def test_all_burnable_codes_ros_positive():
    """Ticket A3 test (b): all 40 burnable models produce ros > 0 under
    fire-season conditions (8% dead moisture, cured 0.30/0.60 live, 5 m/s
    10-m wind through the model's own WAF, flat ground)."""
    for code in FBFM40_BURNABLE:
        fm = FUEL_TABLE[code]
        u_mid = rothermel.midflame_wind_mps(5.0, fm)
        res = rothermel.surface_ros_mps(fm, 0.08, 0.30, 0.60, u_mid, 0.0)
        assert res.ros_mps > 0.0, f"{code} {fm.name} must spread (no-barrier)"


def test_gr3_si_pins():
    """GR3 (103) — the exp1 wall fuel. RMRS-GTR-153: 0.10/0.40/-/1.50 t/ac
    (x 0.0459137 = 0.0046/0.0184/-/0.0689 lb/ft^2), SAV 1500/1300, depth 2.0 ft,
    M_x 30% — matches the pyretechnics row."""
    gr3 = FUEL_TABLE[103]
    assert gr3.load_1h_kgm2 == pytest.approx(0.0046 * LB_FT2_TO_KGM2, rel=1e-6)
    assert gr3.load_10h_kgm2 == pytest.approx(0.0184 * LB_FT2_TO_KGM2, rel=1e-6)
    assert gr3.load_100h_kgm2 == 0.0
    assert gr3.load_live_herb_kgm2 == pytest.approx(0.0689 * LB_FT2_TO_KGM2, rel=1e-6)
    assert gr3.sav_1h_1m == pytest.approx(1500.0 / 0.3048, rel=1e-6)
    assert gr3.sav_live_herb_1m == pytest.approx(1300.0 / 0.3048, rel=1e-6)
    assert gr3.depth_m == pytest.approx(2.0 * 0.3048, rel=1e-9)
    assert gr3.mx_dead_frac == pytest.approx(0.30)
    assert gr3.dynamic is True


def test_gr7_sh1_tl6_si_pins():
    """CA-relevant rows called out in the Council-1 F1 review: GR7 (foothill
    dry grass, 1.0/5.4 t/ac), SH1 (post-fire regen shrub), TL6 (oak-woodland
    litter, 2.4/1.2/1.2 t/ac)."""
    gr7 = FUEL_TABLE[107]
    assert gr7.load_1h_kgm2 == pytest.approx(0.0459 * LB_FT2_TO_KGM2, rel=1e-6)
    assert gr7.load_live_herb_kgm2 == pytest.approx(0.2479 * LB_FT2_TO_KGM2, rel=1e-6)
    assert gr7.depth_m == pytest.approx(3.0 * 0.3048, rel=1e-9)
    assert gr7.mx_dead_frac == pytest.approx(0.15)
    assert gr7.dynamic is True
    sh1 = FUEL_TABLE[141]
    assert sh1.load_1h_kgm2 == pytest.approx(0.0115 * LB_FT2_TO_KGM2, rel=1e-6)
    assert sh1.load_10h_kgm2 == pytest.approx(0.0115 * LB_FT2_TO_KGM2, rel=1e-6)
    assert sh1.load_live_herb_kgm2 == pytest.approx(0.0069 * LB_FT2_TO_KGM2, rel=1e-6)
    assert sh1.load_live_woody_kgm2 == pytest.approx(0.0597 * LB_FT2_TO_KGM2, rel=1e-6)
    assert sh1.depth_m == pytest.approx(1.0 * 0.3048, rel=1e-9)
    assert sh1.mx_dead_frac == pytest.approx(0.15)
    assert sh1.dynamic is True                      # carries live herb load
    tl6 = FUEL_TABLE[186]
    assert tl6.load_1h_kgm2 == pytest.approx(0.1102 * LB_FT2_TO_KGM2, rel=1e-6)
    assert tl6.load_10h_kgm2 == pytest.approx(0.0551 * LB_FT2_TO_KGM2, rel=1e-6)
    assert tl6.load_100h_kgm2 == pytest.approx(0.0551 * LB_FT2_TO_KGM2, rel=1e-6)
    assert tl6.depth_m == pytest.approx(0.3 * 0.3048, rel=1e-9)
    assert tl6.mx_dead_frac == pytest.approx(0.25)
    assert tl6.dynamic is False


def test_heat_content_gr6_exception():
    """GR6 is the ONE FBFM40 model with h = 9000 Btu/lb (pyretechnics h=9.0);
    every other burnable model is 8000 Btu/lb = 18608 kJ/kg."""
    assert FUEL_TABLE[106].heat_kjkg == pytest.approx(9000.0 * BTU_LB_TO_KJKG, rel=1e-9)
    assert FUEL_TABLE[106].heat_kjkg == pytest.approx(20934.0, rel=1e-9)
    for code in FBFM40_BURNABLE:
        if code == 106:
            continue
        assert FUEL_TABLE[code].heat_kjkg == pytest.approx(18608.0, rel=1e-9), code


def test_existing_rows_byte_identical_gr2_sh5_tl3():
    """Ticket A3 test (d): the pre-Sprint-2 rows are BYTE-IDENTICAL — exact
    float equality against the same conversion expressions _build uses (any
    change to the imperial source values or conversion order breaks this)."""
    gr2 = FUEL_TABLE[102]
    assert gr2.load_1h_kgm2 == 0.0046 * LB_FT2_TO_KGM2
    assert gr2.load_10h_kgm2 == 0.0
    assert gr2.load_100h_kgm2 == 0.0
    assert gr2.load_live_herb_kgm2 == 0.0459 * LB_FT2_TO_KGM2
    assert gr2.load_live_woody_kgm2 == 0.0
    assert gr2.sav_1h_1m == 2000.0 * FT2FT3_TO_1M
    assert gr2.sav_live_herb_1m == 1800.0 * FT2FT3_TO_1M
    assert gr2.sav_live_woody_1m == 0.0
    assert gr2.depth_m == 1.0 * FT_TO_M
    assert gr2.mx_dead_frac == 15.0 / 100.0
    assert gr2.heat_kjkg == 8000.0 * BTU_LB_TO_KJKG
    assert gr2.dynamic is True
    sh5 = FUEL_TABLE[145]
    assert sh5.load_1h_kgm2 == 0.1653 * LB_FT2_TO_KGM2
    assert sh5.load_10h_kgm2 == 0.0964 * LB_FT2_TO_KGM2
    assert sh5.load_100h_kgm2 == 0.0
    assert sh5.load_live_herb_kgm2 == 0.0
    assert sh5.load_live_woody_kgm2 == 0.1331 * LB_FT2_TO_KGM2
    assert sh5.sav_1h_1m == 750.0 * FT2FT3_TO_1M
    assert sh5.sav_live_herb_1m == 0.0
    assert sh5.sav_live_woody_1m == 1600.0 * FT2FT3_TO_1M
    assert sh5.depth_m == 6.0 * FT_TO_M
    assert sh5.mx_dead_frac == 15.0 / 100.0
    assert sh5.heat_kjkg == 8000.0 * BTU_LB_TO_KJKG
    assert sh5.dynamic is False
    tl3 = FUEL_TABLE[183]
    assert tl3.load_1h_kgm2 == 0.0230 * LB_FT2_TO_KGM2
    assert tl3.load_10h_kgm2 == 0.1010 * LB_FT2_TO_KGM2
    assert tl3.load_100h_kgm2 == 0.1286 * LB_FT2_TO_KGM2
    assert tl3.load_live_herb_kgm2 == 0.0
    assert tl3.load_live_woody_kgm2 == 0.0
    assert tl3.sav_1h_1m == 2000.0 * FT2FT3_TO_1M
    assert tl3.sav_live_herb_1m == 0.0
    assert tl3.sav_live_woody_1m == 0.0
    assert tl3.depth_m == 0.3 * FT_TO_M
    assert tl3.mx_dead_frac == 20.0 / 100.0
    assert tl3.heat_kjkg == 8000.0 * BTU_LB_TO_KJKG
    assert tl3.dynamic is False


def test_new_rows_dynamic_flags():
    """S&B semantics: dynamic iff the model carries live herbaceous load."""
    dynamic_new = [103, 105, 106, 107, 108, 109, 123, 124, 141, 149, 163]
    static_new = [143, 144, 146, 148, 162, 164, 181, 182, 184, 186, 187, 189,
                  202, 203, 204]
    for code in dynamic_new:
        assert FUEL_TABLE[code].dynamic is True, code
        assert FUEL_TABLE[code].load_live_herb_kgm2 > 0.0, code
    for code in static_new:
        assert FUEL_TABLE[code].dynamic is False, code
        assert FUEL_TABLE[code].load_live_herb_kgm2 == 0.0, code


# --- SI conversion pins against the pyretechnics/RMRS-GTR-153 imperial rows ---

def test_gr2_si_pins():
    gr2 = FUEL_TABLE[102]
    # 0.0046 lb/ft^2 (= 0.10 t/ac) and 0.0459 lb/ft^2 (= 1.0 t/ac)
    assert gr2.load_1h_kgm2 == pytest.approx(0.0046 * 4.8824276, rel=1e-4)
    assert gr2.load_live_herb_kgm2 == pytest.approx(0.0459 * 4.8824276, rel=1e-4)
    assert gr2.load_10h_kgm2 == 0.0
    assert gr2.sav_1h_1m == pytest.approx(2000.0 / 0.3048, rel=1e-6)      # 6561.7 1/m
    assert gr2.sav_live_herb_1m == pytest.approx(1800.0 / 0.3048, rel=1e-6)
    assert gr2.depth_m == pytest.approx(0.3048, rel=1e-9)                 # 1.0 ft
    assert gr2.mx_dead_frac == pytest.approx(0.15)
    assert gr2.dynamic is True


def test_sh5_si_pins():
    sh5 = FUEL_TABLE[145]
    assert sh5.load_1h_kgm2 == pytest.approx(0.1653 * LB_FT2_TO_KGM2, rel=1e-6)
    assert sh5.load_10h_kgm2 == pytest.approx(0.0964 * LB_FT2_TO_KGM2, rel=1e-6)
    assert sh5.load_live_woody_kgm2 == pytest.approx(0.1331 * LB_FT2_TO_KGM2, rel=1e-6)
    assert sh5.load_live_herb_kgm2 == 0.0
    assert sh5.sav_1h_1m == pytest.approx(750.0 / 0.3048, rel=1e-6)
    assert sh5.sav_live_woody_1m == pytest.approx(1600.0 / 0.3048, rel=1e-6)
    assert sh5.depth_m == pytest.approx(6.0 * 0.3048, rel=1e-9)
    assert sh5.mx_dead_frac == pytest.approx(0.15)
    assert sh5.dynamic is False


def test_tl3_si_pins():
    tl3 = FUEL_TABLE[183]
    assert tl3.load_1h_kgm2 == pytest.approx(0.0230 * LB_FT2_TO_KGM2, rel=1e-6)
    assert tl3.load_10h_kgm2 == pytest.approx(0.1010 * LB_FT2_TO_KGM2, rel=1e-6)
    assert tl3.load_100h_kgm2 == pytest.approx(0.1286 * LB_FT2_TO_KGM2, rel=1e-6)
    assert tl3.depth_m == pytest.approx(0.3 * 0.3048, rel=1e-9)
    assert tl3.mx_dead_frac == pytest.approx(0.20)
    assert tl3.dynamic is False


def test_heat_content_standard():
    # 8000 Btu/lb = 18608 kJ/kg for every standard model (matches contracts default)
    for code in REQUIRED_CODES:
        assert FUEL_TABLE[code].heat_kjkg == pytest.approx(18608.0, rel=1e-9)


def test_dynamic_flags_follow_live_herb_load():
    # S&B: models carrying live herbaceous load are dynamic (GR/GS + TU1 here)
    for code, expect in [(101, True), (102, True), (104, True), (121, True),
                         (122, True), (161, True),
                         (142, False), (145, False), (147, False), (165, False),
                         (183, False), (185, False), (188, False), (201, False)]:
        assert FUEL_TABLE[code].dynamic is expect, code


def test_nonburnable_rows_zero_load():
    for code in (91, 92, 93, 98, 99):
        fm = FUEL_TABLE[code]
        assert fm.load_1h_kgm2 == 0.0
        assert fm.load_live_herb_kgm2 == 0.0
        assert fm.load_live_woody_kgm2 == 0.0
        assert fm.depth_m == 0.0


def test_is_burnable():
    for code in (91, 92, 93, 98, 99):
        assert is_burnable(code) is False
    for code in (101, 102, 104, 121, 122, 142, 145, 147, 161, 165, 183, 185, 188, 201, 204):
        assert is_burnable(code) is True
    # outside FBFM40 namespace: nodata / legacy junk must not burn
    for code in (0, -9999, 50, 90, 205, 300):
        assert is_burnable(code) is False


# --- grass classification (grass ROS floor, docs/review/fire_speed_review.md) ---

def test_is_grass_covers_gr1_through_gr9_only():
    """is_grass is exactly the GR band 101-109 (GR1-9); GS, SH, TU, TL, SB and
    the non-burnables are NOT pure grass."""
    for code in range(101, 110):                      # GR1..GR9
        assert is_grass(code) is True, code
    for code in (100, 110, 121, 124, 142, 145, 161, 183, 201, 91, 98, 0, -9999):
        assert is_grass(code) is False, code
    # every is_grass code is a real burnable FBFM40 grass model
    for code in range(101, 110):
        assert is_burnable(code) is True
        assert FUEL_TABLE[code].name.startswith("GR")


def test_is_grass_shrub_covers_gs1_through_gs4_only():
    """is_grass_shrub is exactly the GS band 121-124 (GS1-4); disjoint from the
    pure-grass GR band."""
    for code in range(121, 125):                      # GS1..GS4
        assert is_grass_shrub(code) is True, code
        assert is_grass(code) is False, code          # disjoint from GR
        assert FUEL_TABLE[code].name.startswith("GS")
    for code in (120, 125, 101, 109, 141, 161, 183, 91, 98):
        assert is_grass_shrub(code) is False, code


def test_is_timber_understory_covers_tu1_through_tu5_only():
    """is_timber_understory is exactly the TU band 161-165 (TU1-5) — the
    sheltered understory fuel the extended ROS floor lifts (santa_rosa TU2,
    paradise TU5); disjoint from the grass, grass-shrub and timber-litter bands."""
    for code in range(161, 166):                      # TU1..TU5
        assert is_timber_understory(code) is True, code
        assert is_grass(code) is False, code
        assert is_grass_shrub(code) is False, code
        assert is_timber_litter(code) is False, code
        assert is_burnable(code) is True
        assert FUEL_TABLE[code].name.startswith("TU")
    for code in (160, 166, 109, 124, 145, 181, 189, 201, 91, 98, 0, -9999):
        assert is_timber_understory(code) is False, code


def test_is_timber_litter_covers_tl1_through_tl9_only():
    """is_timber_litter is exactly the TL band 181-189 (TL1-9) — the near-static
    litter fuel the extended ROS floor lifts to a non-crawling rate (santa_rosa
    ~20%, paradise ~19% of the tile); disjoint from the other floored bands."""
    for code in range(181, 190):                      # TL1..TL9
        assert is_timber_litter(code) is True, code
        assert is_grass(code) is False, code
        assert is_grass_shrub(code) is False, code
        assert is_timber_understory(code) is False, code
        assert is_burnable(code) is True
        assert FUEL_TABLE[code].name.startswith("TL")
    for code in (180, 190, 165, 145, 124, 201, 204, 91, 98, 0, -9999):
        assert is_timber_litter(code) is False, code


# --- Simard EMC ---------------------------------------------------------------

def test_simard_known_value():
    # T=25C (77F), RH=50%: EMC = 21.0606 + 0.005565*2500 - 0.00035*50*77
    #                            - 0.483199*50 = 9.46565% ; x1.03 -> 0.0974962
    assert dead_fuel_moisture_frac(25.0, 50.0) == pytest.approx(0.0974962, rel=1e-5)


def test_simard_hot_dry_band():
    # typical hot-dry: 35 C, 15% RH -> EMC*1.03 in the 0.02..0.08 band
    m = dead_fuel_moisture_frac(35.0, 15.0)
    assert 0.02 <= m <= 0.08


def test_simard_rh_monotone_and_90_vs_20():
    m90 = dead_fuel_moisture_frac(25.0, 90.0)
    m20 = dead_fuel_moisture_frac(25.0, 20.0)
    assert m90 > m20
    # monotone across all three piecewise branches at fixed temp
    seq = [dead_fuel_moisture_frac(30.0, h) for h in (5, 15, 30, 45, 55, 70, 90)]
    assert all(a < b for a, b in zip(seq, seq[1:]))


def test_simard_floor_and_clamps():
    assert dead_fuel_moisture_frac(45.0, 0.0) == 0.01      # floored at 1%
    assert dead_fuel_moisture_frac(45.0, -5.0) == 0.01     # RH clamped to 0
    # RH clamped to 100 equals RH=100
    assert dead_fuel_moisture_frac(10.0, 150.0) == dead_fuel_moisture_frac(10.0, 100.0)


# --- live moisture defaults ----------------------------------------------------

def test_live_defaults_summer_pin():
    assert live_moisture_defaults("summer") == (0.30, 0.60)
    assert live_moisture_defaults("SUMMER") == (0.30, 0.60)  # case-insensitive
    assert live_moisture_defaults("fire_season") == (0.30, 0.60)


def test_live_defaults_other_seasons():
    herb_w, woody_w = live_moisture_defaults("winter")
    assert herb_w >= 1.0 and woody_w >= 1.0          # green-up, no curing
    late_h, late_w = live_moisture_defaults("late_summer")
    assert 0.30 < late_h < 1.0                        # partly cured, between
    with pytest.raises(ValueError):
        live_moisture_defaults("monsoon")
