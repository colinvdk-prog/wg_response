"""wgresponse.rothermel — Rothermel (1972) / Albini (1976) surface fire spread.

Implements the Albini-adjusted Rothermel equation set per Andrews 2018
(RMRS-GTR-371), as pinned in docs/research/spread_model.md: reaction intensity
with multi-size-class weighting (1h/10h/100h/live-herb/live-woody, Albini f_ij
category weights + g_ij size-class-group net-load weights), dynamic cured-herb
transfer, wind factor phi_w, slope factor phi_s, propagating flux ratio xi,
heat sink rho_b*eps*Q_ig.

UNITS: imperial INTERNALLY (the coefficients are unit-bound: sigma in ft^2/ft^3,
loads lb/ft^2, R and U in ft/min, I_R in Btu/ft^2/min) per the contracts
exemption; every public seam speaks SI (m/s, kW/m, m). Outputs are clamped
never negative / never NaN; non-burnable fuels return zeros.

Wind limit: run UNCAPPED per Andrews, Cruz & Rothermel (2013) — the original
0.9*I_R cap wrongly throttles wind-driven grass fires; conservative-fast is the
correct polarity for an alerting product.

Memoization seams (Sprint 2 ticket B3, Council-1 perf S1+S2): fuel_bed /
surface_ros_from_bed / effective_wind_from_bed expose the public computations
split at the fuel-bed boundary, so a caller that knows the bed inputs repeat
(spread.py's per-compute_arrival ROS memo: the bed depends only on
(fuel, m_dead) per invocation, not per cell) can build the bed ONCE and reuse
it across wind/slope inputs. The classic seams surface_ros_mps /
effective_wind_mps are DEFINED as fuel_bed + the from-bed halves, so the two
paths execute identical float operations — byte-identical by construction
(pinned by tests/test_rothermel.py wrapper-equivalence sweeps). This module
holds NO cache of its own: every function here stays pure; callers own their
memo lifetimes.
"""
from __future__ import annotations

import math

from wgresponse.contracts import FuelModel, RosResult
from wgresponse.fuels import is_burnable

# --- unit conversions -------------------------------------------------------
_FT_PER_M = 1.0 / 0.3048
_M_PER_FT = 0.3048
_LBFT2_PER_KGM2 = 0.09290304 / 0.45359237     # kg/m^2 -> lb/ft^2 (inverse of fuels.py factor)
_FTMIN_TO_MPS = 0.3048 / 60.0                 # ft/min -> m/s
_MPS_TO_FTMIN = 60.0 / 0.3048                 # m/s -> ft/min
_MPS_TO_MPH = 2.2369362920544025              # m/s -> mph
_BTU_LB_PER_KJKG = 1.0 / 2.326                # kJ/kg -> Btu/lb
_BTUFTS_TO_KWM = 1.05505585 / 0.3048          # 1 Btu/ft/s = 3.46129 kW/m

# --- Rothermel/Albini constants (RMRS-GTR-371) ------------------------------
_RHO_P = 32.0            # oven-dry particle density, lb/ft^3
_S_T = 0.0555            # total mineral content
_S_E = 0.010             # effective (silica-free) mineral content
_ETA_S = 0.174 * _S_E ** -0.19   # mineral damping ~0.4174
_SAV_10H_FT = 109.0      # 10-h dead SAV, ft^2/ft^3 (S&B standard for all FBFM40)
_SAV_100H_FT = 30.0      # 100-h dead SAV, ft^2/ft^3 (S&B standard for all FBFM40)
_MAX_SLOPE_DEG = 85.0    # tan^2 blowup guard


def _size_class(sav_ft: float) -> int:
    """Albini size-class group index from SAV (ft^2/ft^3): >=1200, 192-1200,
    96-192, 48-96, 16-48, <16. Used for the g_ij net-load weights — using f_ij
    here is the classic home-rolled-Rothermel bug (digest §1.6)."""
    if sav_ft >= 1200.0:
        return 0
    if sav_ft >= 192.0:
        return 1
    if sav_ft >= 96.0:
        return 2
    if sav_ft >= 48.0:
        return 3
    if sav_ft >= 16.0:
        return 4
    return 5


def _eta_m(m_frac: float, mx_frac: float) -> float:
    """Moisture damping coefficient, dimensionless; 0 at/above extinction."""
    if mx_frac <= 0.0:
        return 0.0
    r = m_frac / mx_frac
    if r >= 1.0:
        return 0.0
    return max(0.0, 1.0 - 2.59 * r + 5.11 * r * r - 3.52 * r * r * r)


def _category(parts: list) -> dict:
    """Albini within-category weights for parts [(w lb/ft^2, sav ft^-1, m frac)].

    Returns surface area A, f_ij, area-weighted SAV & moisture, g_ij-weighted
    net load (lb/ft^2), and f_ij-weighted eps*Q_ig (Btu/ft^3 per unit rho_b)."""
    areas = [s * w / _RHO_P for (w, s, m) in parts]
    a_tot = sum(areas)
    if a_tot <= 0.0:
        return {}
    f = [a / a_tot for a in areas]
    cls = [_size_class(s) for (w, s, m) in parts]
    g = [sum(fk for fk, ck in zip(f, cls) if ck == cj) for cj in cls]
    return {
        "a": a_tot,
        "sigma": sum(fi * s for fi, (w, s, m) in zip(f, parts)),
        "m": sum(fi * m for fi, (w, s, m) in zip(f, parts)),
        "w_net": sum(gj * w * (1.0 - _S_T) for gj, (w, s, m) in zip(g, parts)),
        "eps_qig": sum(
            fi * math.exp(-138.0 / s) * (250.0 + 1116.0 * m)
            for fi, (w, s, m) in zip(f, parts)
        ),
    }


class _Bed:
    """Cached imperial fuel-bed intermediates for one (fuel, moistures) input."""

    __slots__ = ("sigma_char", "beta", "beta_rel", "i_r", "xi", "sink",
                 "r0_ftmin", "c", "b", "e")


def _bed(fuel: FuelModel, m_dead_frac: float, m_live_herb_frac: float,
         m_live_woody_frac: float):
    """Build fuel-bed intermediates (imperial). None => no spread possible."""
    if not is_burnable(fuel.code):
        return None
    depth_ft = fuel.depth_m * _FT_PER_M
    if depth_ft <= 0.0 or fuel.mx_dead_frac <= 0.0:
        return None

    w1 = fuel.load_1h_kgm2 * _LBFT2_PER_KGM2
    w10 = fuel.load_10h_kgm2 * _LBFT2_PER_KGM2
    w100 = fuel.load_100h_kgm2 * _LBFT2_PER_KGM2
    wherb = fuel.load_live_herb_kgm2 * _LBFT2_PER_KGM2
    wwoody = fuel.load_live_woody_kgm2 * _LBFT2_PER_KGM2
    s1 = fuel.sav_1h_1m * _M_PER_FT          # 1/m -> 1/ft
    sherb = fuel.sav_live_herb_1m * _M_PER_FT
    swoody = fuel.sav_live_woody_1m * _M_PER_FT

    # Dynamic-model curing transfer (S&B 2005 / digest §1.6): herb load moves to
    # a NEW dead class at the herb SAV; fully cured at herb moisture <= 30%,
    # fully green at >= 120%, linear between. Cured herb takes the 1-h dead
    # moisture (BehavePlus convention).
    w_cured = 0.0
    if fuel.dynamic and wherb > 0.0:
        cured_frac = min(max((1.20 - m_live_herb_frac) / 0.90, 0.0), 1.0)
        w_cured = wherb * cured_frac
        wherb -= w_cured

    m_d = max(0.0, m_dead_frac)
    dead_parts = [p for p in [
        (w1, s1, m_d),
        (w10, _SAV_10H_FT, m_d),
        (w100, _SAV_100H_FT, m_d),
        (w_cured, sherb, m_d),
    ] if p[0] > 0.0 and p[1] > 0.0]
    live_parts = [p for p in [
        (wherb, sherb, max(0.0, m_live_herb_frac)),
        (wwoody, swoody, max(0.0, m_live_woody_frac)),
    ] if p[0] > 0.0 and p[1] > 0.0]

    dead = _category(dead_parts)
    live = _category(live_parts)
    if not dead and not live:
        return None

    a_tot = (dead.get("a", 0.0)) + (live.get("a", 0.0))
    if a_tot <= 0.0:
        return None
    f_dead = dead.get("a", 0.0) / a_tot
    f_live = live.get("a", 0.0) / a_tot

    sigma = f_dead * dead.get("sigma", 0.0) + f_live * live.get("sigma", 0.0)
    total_load = w1 + w10 + w100 + w_cured + wherb + wwoody
    rho_b = total_load / depth_ft
    beta = rho_b / _RHO_P
    if sigma <= 0.0 or beta <= 0.0:
        return None

    beta_op = 3.348 * sigma ** -0.8189
    beta_rel = beta / beta_op
    aa = 133.0 * sigma ** -0.7913            # Albini 1976 A
    gamma_max = sigma ** 1.5 / (495.0 + 0.0594 * sigma ** 1.5)   # min^-1
    gamma = gamma_max * beta_rel ** aa * math.exp(aa * (1.0 - beta_rel))

    # Moisture damping. Live extinction moisture per Albini (digest §1.6).
    eta_dead = _eta_m(dead["m"], fuel.mx_dead_frac) if dead else 0.0
    eta_live = 0.0
    if live:
        mx_live = fuel.mx_dead_frac
        den = sum(w * math.exp(-500.0 / s) for (w, s, m) in live_parts)
        num = sum(w * math.exp(-138.0 / s) for (w, s, m) in dead_parts)
        if den > 0.0 and num > 0.0:
            big_w = num / den
            eps_load = sum(w * math.exp(-138.0 / s) for (w, s, m) in dead_parts)
            m_dead_fine = sum(w * m * math.exp(-138.0 / s)
                              for (w, s, m) in dead_parts) / eps_load
            mx_live = max(
                2.9 * big_w * (1.0 - m_dead_fine / fuel.mx_dead_frac) - 0.226,
                fuel.mx_dead_frac,
            )
        eta_live = _eta_m(live["m"], mx_live)

    heat_btu = fuel.heat_kjkg * _BTU_LB_PER_KJKG
    i_r = gamma * heat_btu * _ETA_S * (
        (dead.get("w_net", 0.0) * eta_dead) + (live.get("w_net", 0.0) * eta_live)
    )   # Btu/ft^2/min
    if i_r <= 0.0 or not math.isfinite(i_r):
        i_r = 0.0

    xi = math.exp((0.792 + 0.681 * math.sqrt(sigma)) * (beta + 0.1)) / \
        (192.0 + 0.2595 * sigma)
    sink = rho_b * (f_dead * dead.get("eps_qig", 0.0) +
                    f_live * live.get("eps_qig", 0.0))   # Btu/ft^3
    if sink <= 0.0:
        return None

    bed = _Bed()
    bed.sigma_char = sigma
    bed.beta = beta
    bed.beta_rel = beta_rel
    bed.i_r = i_r
    bed.xi = xi
    bed.sink = sink
    bed.r0_ftmin = i_r * xi / sink
    # Wind-factor coefficients from characteristic SAV (Rothermel 1972 eq. 47-50)
    bed.c = 7.47 * math.exp(-0.133 * sigma ** 0.55)
    bed.b = 0.02526 * sigma ** 0.54
    bed.e = 0.715 * math.exp(-3.59e-4 * sigma)
    return bed


def _phi_wind(bed, u_ftmin: float) -> float:
    """Wind factor phi_w (dimensionless); U in ft/min. Uncapped (see module doc)."""
    if u_ftmin <= 0.0:
        return 0.0
    return bed.c * u_ftmin ** bed.b * bed.beta_rel ** -bed.e


def _phi_slope(bed, slope_deg: float) -> float:
    """Slope factor phi_s = 5.275 * beta^-0.3 * tan^2(slope) (Rothermel eq. 51).

    Upslope only; negative slopes treated as flat, clamped at 85 deg."""
    s = min(max(slope_deg, 0.0), _MAX_SLOPE_DEG)
    if s <= 0.0:
        return 0.0
    t = math.tan(math.radians(s))
    return 5.275 * bed.beta ** -0.3 * t * t


def _wind_from_phi(bed, phi: float) -> float:
    """Invert the wind-factor relation: midflame wind (ft/min) that alone gives phi."""
    if phi <= 0.0:
        return 0.0
    return (phi * bed.beta_rel ** bed.e / bed.c) ** (1.0 / bed.b)


def fuel_bed(fuel: FuelModel, m_dead_frac: float, m_live_herb_frac: float,
             m_live_woody_frac: float):
    """Fuel-bed intermediate for (fuel, moistures); None when the bed cannot
    carry fire (non-burnable code, zero depth, mx_dead <= 0, no load).

    PURE function of its four arguments (ticket B3): safe to memoize on exact
    values. When fuels come from fuels.FUEL_TABLE (code -> row is bijective and
    rows are never mutated), `fuel.code` identifies the fuel in a memo key.
    Consumers must treat the returned bed as immutable and pass it to
    surface_ros_from_bed / effective_wind_from_bed.
    """
    return _bed(fuel, m_dead_frac, m_live_herb_frac, m_live_woody_frac)


def surface_ros_mps(fuel: FuelModel, m_dead_frac: float, m_live_herb_frac: float,
                    m_live_woody_frac: float, midflame_wind_mps: float,
                    slope_deg: float) -> RosResult:
    """Head-fire surface spread for one cell. All seam units SI.

    Inputs: moistures as FRACTIONS (kg water / kg dry fuel; the single
    m_dead_frac is applied to all dead timelag classes — the seam collapses
    1h/10h/100h into the ROS-dominant 1-h value; upgrade path documented in
    digest §1.7), midflame wind in m/s (use midflame_wind_mps() to convert a
    10-m wind), slope in degrees along the max-spread (upslope) direction.

    Returns RosResult: ros_mps (head ROS, m/s, floor 0), effective_wind_mps
    (wind that alone reproduces the combined wind+slope factor — feeds
    ellipse_lb), flame_length_m (Byram 1959 via L = 0.0775 * I^0.46, I in
    kW/m), intensity_kwm (Byram fireline intensity, kW/m, with Anderson 1969
    residence time t_r = 384/sigma).

    Equivalent to surface_ros_from_bed(fuel_bed(fuel, moistures...), wind,
    slope) — literally so (ticket B3): the body IS that composition, keeping
    the memoized-bed path byte-identical to this classic seam.
    """
    bed = _bed(fuel, m_dead_frac, m_live_herb_frac, m_live_woody_frac)
    return surface_ros_from_bed(bed, midflame_wind_mps, slope_deg)


def surface_ros_from_bed(bed, midflame_wind_mps: float,
                         slope_deg: float) -> RosResult:
    """surface_ros_mps continued from a prebuilt fuel_bed(...) result
    (ticket B3 memo seam; same RosResult semantics and clamps).

    bed=None (unburnable bed) returns the all-zero RosResult exactly like the
    wrapper. Byte-identity with surface_ros_mps for every input is pinned by
    tests/test_rothermel.py::test_from_bed_seams_match_wrappers_exactly.
    """
    if bed is None:
        return RosResult(ros_mps=0.0, effective_wind_mps=0.0,
                         flame_length_m=0.0, intensity_kwm=0.0)
    u_ftmin = max(0.0, midflame_wind_mps) * _MPS_TO_FTMIN
    phi_w = _phi_wind(bed, u_ftmin)
    phi_s = _phi_slope(bed, slope_deg)
    # Scalar (collinear, wind-aligned-upslope) combination — the conservative
    # worst case when only magnitudes are known; the vector combination for
    # off-axis wind/slope lives in effective_wind_mps(..., wind_slope_angle_deg).
    r_ftmin = bed.r0_ftmin * (1.0 + phi_w + phi_s)
    if not math.isfinite(r_ftmin) or r_ftmin < 0.0:
        r_ftmin = 0.0
    u_eff_ftmin = _wind_from_phi(bed, phi_w + phi_s)

    intensity_kwm = 0.0
    flame_m = 0.0
    if r_ftmin > 0.0 and bed.i_r > 0.0:
        t_r_min = 384.0 / bed.sigma_char          # Anderson 1969 residence time, min
        i_btufts = bed.i_r * t_r_min * r_ftmin / 60.0   # Btu/ft/s
        intensity_kwm = max(0.0, i_btufts * _BTUFTS_TO_KWM)
        if intensity_kwm > 0.0:
            # Byram 1959 flame length: L_m = 0.0775 * I_kW/m ^ 0.46
            flame_m = 0.0775 * intensity_kwm ** 0.46
    return RosResult(
        ros_mps=r_ftmin * _FTMIN_TO_MPS,
        effective_wind_mps=u_eff_ftmin * _FTMIN_TO_MPS,
        flame_length_m=flame_m,
        intensity_kwm=intensity_kwm,
    )


def midflame_wind_mps(wind10_mps: float, fuel: FuelModel) -> float:
    """Midflame wind speed (m/s) from a 10-m sustained wind (m/s).

    10-m -> 20-ft via U_20ft = U_10m / 1.15, then the unsheltered
    Albini & Baughman (1979) wind adjustment factor from fuel-bed depth
    (digest §1.4): WAF = 1.83 / ln[(20 + 0.36*d) / (0.13*d)], d in ft
    (~0.36 for 1-ft grass, ~0.55 for 6-ft shrub). Non-burnable / zero-depth
    fuels return 0.
    """
    depth_ft = fuel.depth_m * _FT_PER_M
    if depth_ft <= 0.0 or wind10_mps <= 0.0:
        return 0.0
    waf = 1.83 / math.log((20.0 + 0.36 * depth_ft) / (0.13 * depth_ft))
    return (wind10_mps / 1.15) * waf


def effective_wind_mps(fuel: FuelModel, midflame_wind_mps: float, slope_deg: float,
                       m_dead_frac: float = 0.08, m_live_herb_frac: float = 0.30,
                       m_live_woody_frac: float = 0.60,
                       wind_slope_angle_deg: float = 0.0) -> float:
    """Effective wind (m/s): the midflame wind that alone reproduces the combined
    wind + slope-equivalent-wind spread factor.

    Vector combination per the ELMFIRE/FARSITE convention (digest §3.2): phi_w
    points along the wind-blows-TO direction, phi_s points upslope;
    `wind_slope_angle_deg` is the angle between those two directions (0 =
    aligned, the conservative default used inside surface_ros_mps; 180 = wind
    directly opposing the slope). Combined magnitude
    phi_E = sqrt(phi_w^2 + phi_s^2 + 2*phi_w*phi_s*cos(angle)), then inverted
    through the phi_w relation: U_eff = [phi_E * (beta/beta_op)^E / C]^(1/B).
    Moistures (fractions) default to the cured fire-season scenario; they enter
    only through the dynamic-transfer effect on the bed weights.

    Equivalent to effective_wind_from_bed(fuel_bed(fuel, moistures...), wind,
    slope, angle) — literally so (ticket B3 memo seam).
    """
    bed = _bed(fuel, m_dead_frac, m_live_herb_frac, m_live_woody_frac)
    return effective_wind_from_bed(bed, midflame_wind_mps, slope_deg,
                                   wind_slope_angle_deg)


def effective_wind_from_bed(bed, midflame_wind_mps: float, slope_deg: float,
                            wind_slope_angle_deg: float = 0.0) -> float:
    """effective_wind_mps continued from a prebuilt fuel_bed(...) result
    (ticket B3 memo seam). bed=None returns 0.0 exactly like the wrapper."""
    if bed is None:
        return 0.0
    phi_w = _phi_wind(bed, max(0.0, midflame_wind_mps) * _MPS_TO_FTMIN)
    phi_s = _phi_slope(bed, slope_deg)
    cos_a = math.cos(math.radians(wind_slope_angle_deg))
    phi_e_sq = phi_w * phi_w + phi_s * phi_s + 2.0 * phi_w * phi_s * cos_a
    phi_e = math.sqrt(max(0.0, phi_e_sq))
    return _wind_from_phi(bed, phi_e) * _FTMIN_TO_MPS


def ellipse_lb(effective_wind_mps: float) -> float:
    """Fire-ellipse length/breadth ratio from effective wind (m/s).

    Anderson (1983) as implemented in ELMFIRE/FARSITE (digest §3.2), U in mph
    inside the formula: L/B = 0.936*exp(0.2566*U) + 0.461*exp(-0.1548*U) - 0.397,
    clamped to [1, 8] (= 1 at zero wind, capped at 8).
    """
    u_mph = max(0.0, effective_wind_mps) * _MPS_TO_MPH
    lb = 0.936 * math.exp(0.2566 * u_mph) + 0.461 * math.exp(-0.1548 * u_mph) - 0.397
    return min(max(lb, 1.0), 8.0)


def directional_ros_mps(head_ros_mps: float, lb: float, theta_deg: float) -> float:
    """Spread rate (m/s) at angle `theta_deg` from the max-spread (head) direction.

    Ellipse with ignition at the rear focus: R(theta) = R_head*(1-e)/(1-e*cos(theta)),
    e = sqrt(lb^2 - 1)/lb (digest §3.2). theta=0 -> head, 90 -> flank-ish,
    180 -> backing. lb is floored at 1 (e=0 -> uniform circle).
    """
    if head_ros_mps <= 0.0:
        return 0.0
    lb = max(1.0, lb)
    ecc = math.sqrt(lb * lb - 1.0) / lb
    r = head_ros_mps * (1.0 - ecc) / (1.0 - ecc * math.cos(math.radians(theta_deg)))
    return max(0.0, r)
