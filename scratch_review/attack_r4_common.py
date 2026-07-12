"""attack_r4_common.py — shared harness for the FOUNDER ROUND 4 adversarial
verification (ember/adjacency threat ETA, urgency-first dispatch, per-home
outcome tags).

INDEPENDENCE DISCIPLINE (same as attack_sim_common / attack_r3_common):
everything used to *verify* an engine claim is recomputed here from first
principles. In particular:
  * erode_min_indep: an OWN separable Chebyshev-window morphological MIN filter,
    re-derived from the geo.erode_min CONTRACT (docstring), never a call into
    wgresponse.geo.erode_min — used to independently reproduce the law-6 ember
    fallback and to locate the barrier-leak radius;
  * ember_eta_indep: an OWN reimplementation of spread.eta_at's fallback
    (own-cell wins; else erode-min sample), so the engine's eta_at is checked
    against a byte-independent oracle;
  * reached-fuel-within-window: an OWN Chebyshev-window scan over the raw ens
    rasters (taken as DATA) to decide whether a developed home has ANY
    fire-reached cell within the radius — the ground truth for "isolated home
    must stay UNREACHED".

Engine calls here are SCENARIO SETUP ONLY (build a SimSession, drive commands)
plus taking raw ens rasters / tile fuel as INPUT DATA — never as the verifier.

Runs from repo root:  py -3 -m pytest scratch_review/attack_r4_ember.py -q
The user's live :8878 server is NEVER touched (no server bound here).
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TILE_DIR = os.path.join(REPO, "data", "tiles")
UNREACHED = math.inf

REAL_TILES = ("malibu_ca", "paradise_ca", "santa_rosa_ca",
              "boulder_co", "park_city_ut")


def tile_present(key: str) -> bool:
    return os.path.exists(os.path.join(TILE_DIR, f"{key}.wgt"))


def skip_loud(key: str, what: str) -> str:
    return (f"SKIP-LOUDLY: {what} for {key} missing on disk — refetch with "
            f"`py -3 tools/fetch_sim_tiles.py --only {key}` and re-run "
            f"(verifier brief demands a loud skip, never a silent pass)")


# --------------------------------------------------------------------------
# INDEPENDENT erode_min: separable row/col Chebyshev-window minimum.
# Re-derived from the wgresponse.geo.erode_min CONTRACT docstring:
#   k = ceil(radius_m / cell_m) when radius_m > 0 else 0;  k<=0 -> copy.
#   window clipped at borders; separable (row pass then col pass); O(n*k).
# NOT a call into geo.erode_min.
# --------------------------------------------------------------------------
def erode_min_indep(data, nrows, ncols, cell_m, radius_m):
    """Own morphological MIN over a (2k+1)x(2k+1) square window. Returns a new
    flat row-major list; k = ceil(radius_m/cell_m)."""
    k = math.ceil(radius_m / cell_m) if radius_m > 0 else 0
    if k <= 0:
        return list(data)
    tmp = [0.0] * (nrows * ncols)
    for i in range(nrows):
        base = i * ncols
        row = data[base:base + ncols]
        for j in range(ncols):
            lo = j - k if j - k > 0 else 0
            hi = j + k + 1 if j + k + 1 < ncols else ncols
            tmp[base + j] = min(row[lo:hi])
    out = [0.0] * (nrows * ncols)
    for j in range(ncols):
        col = tmp[j::ncols]
        for i in range(nrows):
            lo = i - k if i - k > 0 else 0
            hi = i + k + 1 if i + k + 1 < nrows else nrows
            out[i * ncols + j] = min(col[lo:hi])
    return out


def rc_of(spec, lat, lon):
    """Own contracts cell formula (row 0 = north). None outside the grid."""
    MDEG_LAT = 6_371_008.8 * math.pi / 180.0
    mlon = MDEG_LAT * max(math.cos(math.radians(lat)), 1e-6)
    r = math.floor((spec.north_lat - lat) * MDEG_LAT / spec.cell_m)
    c = math.floor((lon - spec.west_lon) * mlon / spec.cell_m)
    if 0 <= r < spec.nrows and 0 <= c < spec.ncols:
        return int(r), int(c)
    return None


def ember_eta_indep(ens, loc, radius_m):
    """OWN reimplementation of spread.eta_at(fallback_radius_m): the own-cell
    nearest sample wins; only when p10's own cell is UNREACHED (inf) AND
    radius>0 do we fall back to the independent erode-min sample of each
    quantile. Returns (p10, p50, p90). Off-grid -> (inf, inf, inf)."""
    spec = ens.spec
    rc = rc_of(spec, loc.lat, loc.lon)
    if rc is None:
        return (UNREACHED, UNREACHED, UNREACHED)
    idx = rc[0] * spec.ncols + rc[1]
    p10 = ens.p10.data[idx]
    p50 = ens.p50.data[idx]
    p90 = ens.p90.data[idx]
    if radius_m > 0.0 and math.isinf(p10):
        nr, nc, cm = spec.nrows, spec.ncols, spec.cell_m
        e10 = erode_min_indep(ens.p10.data, nr, nc, cm, radius_m)
        e50 = erode_min_indep(ens.p50.data, nr, nc, cm, radius_m)
        e90 = erode_min_indep(ens.p90.data, nr, nc, cm, radius_m)
        p10, p50, p90 = e10[idx], e50[idx], e90[idx]
    return (p10, p50, p90)


def reached_finite_within_window(ens_raster_data, spec, rc, radius_m):
    """True iff ANY cell within Chebyshev k=ceil(radius/cell) of rc has a FINITE
    arrival (fire reached it). The ground-truth "is there fire-reached fuel the
    ember proxy could inherit from". Own window scan over the raw raster data."""
    k = math.ceil(radius_m / spec.cell_m) if radius_m > 0 else 0
    r0, c0 = rc
    for r in range(max(0, r0 - k), min(spec.nrows, r0 + k + 1)):
        base = r * spec.ncols
        for c in range(max(0, c0 - k), min(spec.ncols, c0 + k + 1)):
            if math.isfinite(ens_raster_data[base + c]):
                return True
    return False
