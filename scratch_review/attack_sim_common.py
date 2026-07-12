"""attack_sim_common.py — shared helpers for the simulator adversarial attacks.

INDEPENDENCE DISCIPLINE: everything used to *verify* engine claims is
recomputed here from first principles (contracts-documented formulas and the
LANDFIRE FBFM40 namespace), NOT by calling the engine's own audit/band code:

  - coordinate math: own copy of the contracts GridSpec cell formulas on the
    IUGG sphere (R = 6,371,008.8 m — pinned in wgresponse/geo.py's module
    docstring as the coordinate CONTRACT, re-derived here);
  - burned-mask RLE decoding: own decoder for the state()['terrain']['burned']
    rle01 payload;
  - WUI band: own FBFM40 classification (burnable = 101..204 namespace;
    developed = 91/93) + own Chebyshev-window band builder over a .wgt FUEL
    band parsed by an OWN minimal reader of the documented ascii format;
  - stop-deadline checks: payload-only recomputation
    (depart_s <= client eta_p10_s - (buffer+ember)*60).

Engine calls that appear here are SCENARIO SETUP ONLY (picking an ignition
point, driving SimSession commands) — never verification.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo  # noqa: E402  (setup-only: destination/upwind scan)
from wgresponse.simui import LOCATIONS  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TILE_DIR = os.path.join(REPO, "data", "tiles")

# --- independent coordinate contract (contracts.GridSpec + geo docstring) ---
EARTH_R_M = 6_371_008.8                      # IUGG mean radius (the contract)
MDEG_LAT = EARTH_R_M * math.pi / 180.0       # meters per degree latitude


def mdeg_lon(lat_deg: float) -> float:
    return MDEG_LAT * max(math.cos(math.radians(lat_deg)), 1e-6)


def my_latlon_to_rc(north_lat, west_lon, cell_m, nrows, ncols, lat, lon):
    """Own implementation of the contracts cell formula (row 0 = north)."""
    r = math.floor((north_lat - lat) * MDEG_LAT / cell_m)
    c = math.floor((lon - west_lon) * mdeg_lon(lat) / cell_m)
    if 0 <= r < nrows and 0 <= c < ncols:
        return int(r), int(c)
    return None


# --- independent .wgt reader (format documented in terrain.save_ascii_tile) --

def parse_wgt_fuel(path):
    """Minimal independent reader: header + FUEL band only.

    Returns dict(nrows, ncols, cell_m, north_lat, west_lon, codes[list[int]]).
    """
    with open(path, "r", encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f]
    head = lines[0].split()
    nrows, ncols = int(head[0]), int(head[1])
    cell_m, north_lat, west_lon = float(head[2]), float(head[3]), float(head[4])
    i = 1
    if lines[i].startswith("source "):
        i += 1
    assert lines[i].strip() == "FUEL", f"expected FUEL band first, got {lines[i]!r}"
    i += 1
    codes = []
    for r in range(nrows):
        row = lines[i + r].split()
        assert len(row) == ncols, f"row {r}: {len(row)} != {ncols}"
        codes.extend(int(round(float(v))) for v in row)
    return {"nrows": nrows, "ncols": ncols, "cell_m": cell_m,
            "north_lat": north_lat, "west_lon": west_lon, "codes": codes}


# --- independent FBFM40 classification + WUI band ---------------------------

DEV_CODES = frozenset((91, 93))
WATER_CODE = 98
#: full legal namespace: NB 91/92/93/98/99 + burnable 101..204 (FBFM40)
LEGAL_CODES = frozenset((91, 92, 93, 98, 99)) | frozenset(range(101, 205))


def my_burnable(code: int) -> bool:
    """FBFM40 burnable namespace: GR/GS/SH/TU/TL/SB occupy 101..204.
    Everything else (NB 91..99, nodata -9999, 0) is non-burnable."""
    return 101 <= code <= 204


def my_wui_band(nrows, ncols, codes):
    """Own band builder: developed (91/93) within Chebyshev 3 of burnable,
    UNION burnable within Chebyshev 1 of developed. Set of (r, c)."""
    burn = [my_burnable(v) for v in codes]
    dev = [v in DEV_CODES for v in codes]

    def any_in_window(flags, r, c, k):
        for rr in range(max(0, r - k), min(nrows, r + k + 1)):
            base = rr * ncols
            for cc in range(max(0, c - k), min(ncols, c + k + 1)):
                if flags[base + cc]:
                    return True
        return False

    band = set()
    for r in range(nrows):
        base = r * ncols
        for c in range(ncols):
            i = base + c
            if dev[i]:
                if any_in_window(burn, r, c, 3):
                    band.add((r, c))
            elif burn[i]:
                if any_in_window(dev, r, c, 1):
                    band.add((r, c))
    return band


# --- burned-mask RLE decoder -------------------------------------------------

def decode_burned(burned):
    """rle01 payload -> bytearray mask (1 = burned), validated length."""
    assert burned["encoding"] == "rle01", burned["encoding"]
    assert burned["first_value"] == 0
    n = burned["nrows"] * burned["ncols"]
    mask = bytearray()
    v = 0
    for run in burned["runs"]:
        mask.extend(bytes([v]) * run)
        v ^= 1
    assert len(mask) == n, f"RLE decodes to {len(mask)} cells, grid is {n}"
    return mask


def burned_count(state) -> int:
    return sum(state["terrain"]["burned"]["runs"][1::2])


# --- payload-only safety checks ----------------------------------------------

def client_p10_map(state):
    """client_id -> eta_p10_s (None if unreached) from the state payload."""
    return {c["client_id"]: c["eta_p10_s"] for c in state["clients"]}


def independent_stop_violations(state, buffer_min, ember_min=0.0, eps=1e-6):
    """Recompute SAFETY SEMANTICS 2 for every serviced stop from the payload:
    depart_s must be <= LOCAL p10 (client rollup eta) - (buffer+ember)*60.
    Single-fire sessions: the rollup min == the stop's incident p10.
    Returns list of (stop_dict, p10_now, deficit_min) for violations."""
    p10 = client_p10_map(state)
    bad = []
    for s in state["serviced"]:
        v = p10.get(s["client_id"])
        if v is None:
            continue                      # currently unreached -> no deadline
        deadline = v - (buffer_min + ember_min) * 60.0
        if s["depart_s"] > deadline + eps:
            bad.append((s, v, (s["depart_s"] - deadline) / 60.0))
    return bad


def crews_inside_burned(state):
    """Crews whose current cell is burned, via OWN rc math + OWN RLE decode.
    Returns list of (crew_id, r, c)."""
    t = state["terrain"]
    mask = decode_burned(t["burned"])
    nrows, ncols = t["nrows"], t["ncols"]
    north, west = t["bbox"]["north"], t["bbox"]["west"]
    cell = t["cell_m"]
    bad = []
    for crew in state["crews"]:
        rc = my_latlon_to_rc(north, west, cell, nrows, ncols,
                             crew["lat"], crew["lon"])
        if rc is not None and mask[rc[0] * ncols + rc[1]]:
            bad.append((crew["crew_id"], rc[0], rc[1]))
    return bad


# --- scenario setup helpers (engine calls allowed here) ----------------------

def tile_present(key) -> bool:
    return os.path.exists(os.path.join(TILE_DIR, f"{key}.wgt"))


def skip_reason(key) -> str:
    return (f"REAL TILE MISSING: data/tiles/{key}.wgt not on disk — refetch "
            f"with `py -3 tools/fetch_sim_tiles.py --only {key}` (skipping "
            f"LOUDLY per the verifier brief)")


def deep_burnable(tile, rc, depth=2, frac=1.0):
    """Cell rc has >= frac of its (2*depth+1)^2 window burnable (product tile
    object; setup-only)."""
    from wgresponse.fuels import is_burnable
    spec = tile.spec
    nr, nc = spec.nrows, spec.ncols
    r, c = rc
    tot = 0
    hit = 0
    for rr in range(max(0, r - depth), min(nr, r + depth + 1)):
        for cc in range(max(0, c - depth), min(nc, c + depth + 1)):
            tot += 1
            if is_burnable(int(round(tile.fuel.get(rr, cc)))):
                hit += 1
    return hit >= math.ceil(frac * tot)


def _is_fast_fuel(code: int) -> bool:
    """Grass/grass-shrub/shrub FBFM40 groups (101..149): the fuels that carry
    fire at demonstration-visible rates. Preference only, never a filter."""
    return 101 <= code <= 149


def pick_ignition(session, key, min_m=450, max_m=2500, step_m=90,
                  depth=2, frac=1.0, cluster=False):
    """First (ignition LatLon, target client) walking upwind of each client:
    deep-fuel cell so the fire takes hold and runs back onto the WUI.
    Preference tiers: deep fast fuel (grass/shrub) > deep any-burnable >
    75%-burnable window > plain burnable. cluster=True orders candidate
    target clients by how many other clients sit within 2.5 km (densest
    first) so the fire sweeps a neighborhood, not a lone rooftop."""
    from wgresponse.fuels import is_burnable
    tile = session._tile
    spec = tile.spec
    hazard = LOCATIONS[key].hazard_wind_dir_deg

    def fuel_at_rc(rc):
        return int(round(tile.fuel.get(*rc)))

    clients = list(session._clients)
    if cluster:
        def density(cl):
            return sum(1 for o in clients
                       if geo.haversine_m(cl.loc, o.loc) <= 2500.0)
        clients.sort(key=lambda cl: (-density(cl), cl.client_id))

    tiers = (("fast", frac), ("any", frac), ("any", 0.75), ("any", None))
    for kind, want_frac in tiers:
        for cl in clients:
            rc0 = geo.latlon_to_rc(spec, cl.loc)
            if rc0 is None or not is_burnable(fuel_at_rc(rc0)):
                continue
            for d in range(min_m, max_m + 1, step_m):
                p = geo.destination(cl.loc, hazard, d)
                rc = geo.latlon_to_rc(spec, p)
                if rc is None or not is_burnable(fuel_at_rc(rc)):
                    continue
                if kind == "fast" and not _is_fast_fuel(fuel_at_rc(rc)):
                    continue
                if want_frac is None or deep_burnable(tile, rc, depth, want_frac):
                    return p, cl
    raise AssertionError(
        f"{key}: no burnable upwind ignition found within {max_m} m of any "
        f"client — scenario cannot be armed (report this)")


def engine_state_hash(state, drop=("cmd_log",)):
    """Deterministic digest of the ENGINE state (server/window keys and any
    `drop` keys removed). json.dumps must succeed with allow_nan=False."""
    d = {k: v for k, v in state.items()
         if k not in drop and k not in ("session_id", "running", "speed_x",
                                        "tick_interval_s")}
    return hashlib.sha256(
        json.dumps(d, sort_keys=True, allow_nan=False).encode()).hexdigest()
