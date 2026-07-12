"""ADVERSARIAL VERIFIER — simulator attack #3: WUI CLIENT PLACEMENT.

Brief item 3, per real tile (skip LOUDLY when missing):

  W1. >= 95% of clients inside the interface band, where the band is
      RECOMPUTED INDEPENDENTLY: own .wgt FUEL parser (documented ascii
      format), own FBFM40 classification (burnable = 101..204, developed =
      91/93), own Chebyshev-window band builder, own lat/lon->cell math on
      the IUGG sphere — zero calls into wgresponse.simui/fuels/geo for the
      verification path.
  W2. ZERO clients on water (98) or impossible cells (nodata/-9999, any
      out-of-namespace code, or off the tile grid entirely); all 200 client
      cells DISTINCT; values inside the documented $600k..$5M clamp.
  W3. REROLL changes the layout but stays in-band (>= 95%), preserves count,
      and is deterministic (same seed -> byte-identical placements) — both
      via SimSession.configure and via the server's reroll_clients ctl.

Runs at the maximum client count (200) — the harshest in-band ratio test.

Run from repo root:
    py -3 -m pytest scratch_review/attack_sim_wui.py -v
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scratch_review.attack_sim_common import (  # noqa: E402
    LEGAL_CODES, TILE_DIR, WATER_CODE, my_latlon_to_rc, my_wui_band,
    parse_wgt_fuel, skip_reason, tile_present)
from wgresponse.server import SimSessionManager  # noqa: E402
from wgresponse.simui import LOCATIONS, SimSession  # noqa: E402

KEYS = sorted(LOCATIONS)
N = 200

_BANDS = {}          # key -> (meta, band set)  (parse once per tile)


def _band(key):
    got = _BANDS.get(key)
    if got is None:
        meta = parse_wgt_fuel(os.path.join(TILE_DIR, f"{key}.wgt"))
        band = my_wui_band(meta["nrows"], meta["ncols"], meta["codes"])
        got = _BANDS[key] = (meta, band)
    return got


def _placements(state):
    return [(c["client_id"], c["lat"], c["lon"], c["value_usd"])
            for c in state["clients"]]


def _check_layout(key, state, tag):
    """W1 + W2 on one state snapshot; returns the set of client cells."""
    meta, band = _band(key)
    in_band = 0
    cells = []
    for c in state["clients"]:
        rc = my_latlon_to_rc(meta["north_lat"], meta["west_lon"],
                             meta["cell_m"], meta["nrows"], meta["ncols"],
                             c["lat"], c["lon"])
        assert rc is not None, (
            f"{key}/{tag}: client {c['client_id']} at {c['lat']},{c['lon']} "
            f"is OFF THE TILE GRID")
        code = meta["codes"][rc[0] * meta["ncols"] + rc[1]]
        assert code != WATER_CODE, (
            f"{key}/{tag}: client {c['client_id']} placed ON WATER "
            f"(FBFM40 98) at cell {rc}")
        assert code in LEGAL_CODES, (
            f"{key}/{tag}: client {c['client_id']} on IMPOSSIBLE cell {rc} "
            f"(fuel code {code}: nodata/out-of-namespace)")
        assert 600_000.0 <= c["value_usd"] <= 5_000_000.0, (
            f"{key}/{tag}: {c['client_id']} value {c['value_usd']} outside "
            f"the documented clamp")
        cells.append(rc)
        if rc in band:
            in_band += 1
    n = len(state["clients"])
    assert n == N, f"{key}/{tag}: expected {N} clients, got {n}"
    assert len(set(cells)) == n, (
        f"{key}/{tag}: client cells not distinct "
        f"({n - len(set(cells))} collisions)")
    frac = in_band / n
    assert frac >= 0.95, (
        f"{key}/{tag}: only {in_band}/{n} = {frac:.1%} of clients inside the "
        f"INDEPENDENTLY recomputed WUI band (>= 95% required)")
    return frac


@pytest.mark.parametrize("key", KEYS)
def test_wui_placement_band_and_impossible_cells(key):
    if not tile_present(key):
        pytest.skip(skip_reason(key))
    s = SimSession(key, N, 4, seed=f"atk-wui|{key}")
    st = s.state()
    assert st["terrain"]["synthetic"] is False
    frac = _check_layout(key, st, "base")

    # W3 determinism: same seed -> byte-identical placements
    s2 = SimSession(key, N, 4, seed=f"atk-wui|{key}")
    assert _placements(s2.state()) == _placements(st), (
        f"{key}: same seed produced a different layout")

    # W3 reroll via configure: layout changes, count kept, still in-band
    s.configure(key, N, 4, seed=f"atk-wui|{key}#r1", cfg_overrides=None)
    st_r = s.state()
    assert _placements(st_r) != _placements(st), (
        f"{key}: reroll produced an identical layout")
    frac_r = _check_layout(key, st_r, "reroll")
    sys.stderr.write(f"\n[wui {key}] in-band base {frac:.1%}, "
                     f"reroll {frac_r:.1%}\n")


def test_wui_reroll_via_server_ctl():
    """The server's reroll_clients path on one real tile: 200 clients, new
    seed suffix #r1, layout changes, in-band ratio holds, count preserved."""
    key = next((k for k in KEYS if tile_present(k)), None)
    if key is None:
        pytest.skip("no real tiles on disk")
    mgr = SimSessionManager()
    try:
        code, out = mgr.new_session({"location": key, "clients": N,
                                     "crews": 4, "seed": f"atk-wui|{key}"})
        assert code == 200, out
        sid = out["session_id"]
        base = _placements(out["state"])
        code, out = mgr.ctl(sid, {"action": "reroll_clients"})
        assert code == 200, out
        assert out["seed"] == f"atk-wui|{key}#r1"
        re1 = _placements(out["state"])
        assert re1 != base, "server reroll produced an identical layout"
        _check_layout(key, out["state"], "server-reroll")
        # determinism of the reroll chain: a fresh manager replays it
        mgr2 = SimSessionManager()
        try:
            _, o2 = mgr2.new_session({"location": key, "clients": N,
                                      "crews": 4, "seed": f"atk-wui|{key}"})
            _, o2 = mgr2.ctl(o2["session_id"], {"action": "reroll_clients"})
            assert _placements(o2["state"]) == re1, (
                "reroll chain not reproducible across managers")
        finally:
            mgr2.stop_all()
    finally:
        mgr.stop_all()
