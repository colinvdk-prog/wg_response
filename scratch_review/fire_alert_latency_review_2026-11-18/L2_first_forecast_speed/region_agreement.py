"""Lane L2: how closely a REGIONAL CUT (SAFETY SEMANTICS 53) matches a
directly fetched LANDFIRE tile — on real tiles from the S3 terrain mirror
(pulled read-only by catalog_mirror.py + pairs.json into MIRROR).

For each pair (A, B) with domain B inside domain A's LFPS job AOI:
  regional = RegionalTerrain's own _sample() over ONE piece: A's raw job
             zips (terrain + canopy, native 30 m, EPSG:4326), cut onto B's
             grid (B's bbox, B's cell size) — exactly the production cut
             code with A's AOI standing in for a lattice piece;
  direct   = B's own decoded tile (.wgtile + .wgcanopy) the live watcher
             made from B's own LFPS jobs.
Control: A's zips cut onto A's own grid must equal A's .wgtile/.wgcanopy
byte for byte (the cut IS the LFPS path's resampling).

Writes region_agreement.json. Needs lzw_shim (no imagecodecs here).
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORKTREE = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, WORKTREE)
sys.path.insert(0, HERE)
MIRROR = os.environ.get("L2_MIRROR") or os.path.join(
    os.environ["TEMP"], "claude", "78600f61c23682e1",
    "301cc633-83bf-4a52-9972-a841bce65068", "scratchpad", "mirror_pull")

import lzw_shim  # noqa: E402
lzw_shim.install()

from wgresponse import terrain as T  # noqa: E402
from wgresponse.contracts import BBox  # noqa: E402


class _Piece:
    def __init__(self, aoi):
        self.i = self.j = 0
        self.key = ("pair", 0)
        self.aoi = aoi


class OnePiece(T.RegionalTerrain):
    """The production cut with one given AOI as the only piece."""

    def __init__(self, lfps, aoi):
        super().__init__(lfps, piece_deg=0.5)
        self._piece = _Piece(aoi)

    def _needed(self, bbox, cell_m):
        spec, lats, lons = None, None, None
        _p, spec, lats, lons, _r, _c = super()._needed(bbox, cell_m)
        rows = {0: list(range(spec.nrows))}
        cols = {0: list(range(spec.ncols))}
        return [self._piece], spec, lats, lons, rows, cols


def bands_of(tile):
    out = {"fuel": tile.fuel.data, "slope_deg": tile.slope_deg.data,
           "aspect_deg": tile.aspect_deg.data, "elev_m": tile.elev_m.data}
    c = tile.canopy
    if c is not None:
        out.update(cbh_m=c.cbh_m.data, cbd_kgm3=c.cbd_kgm3.data,
                   cc_pct=c.cc_pct.data, ch_m=c.ch_m.data)
    return out


def compare(a, b):
    out = {}
    for k in a:
        x, y = a[k], b[k]
        n = len(x)
        same = sum(1 for p, q in zip(x, y) if p == q)
        diffs = [abs(p - q) for p, q in zip(x, y) if p != q]
        out[k] = {"cells": n, "identical_pct": round(100.0 * same / n, 3),
                  "mean_abs_diff_where_different": (
                      round(sum(diffs) / len(diffs), 3) if diffs else 0.0),
                  "mean_abs_diff_all": round(sum(diffs) / n, 4)}
        if k == "aspect_deg":
            # aspect wraps at 360 and is -1 on flat cells
            circ = [min(abs(p - q), 360 - abs(p - q)) for p, q in zip(x, y)
                    if p != q and p >= 0 and q >= 0]
            out[k]["mean_circular_diff_where_different"] = (
                round(sum(circ) / len(circ), 2) if circ else 0.0)
    return out


def within_one_pixel(lfps, abox, bbox, cell_m, b_direct):
    """Per band, the % of B's direct cells whose value is one of the 3 x 3
    native pixels around the pixel the cut took from A (a disagreement that
    is only LFPS's per-AOI sub-pixel realignment)."""
    import numpy as np
    out = {}
    spec = b_direct.spec
    m_lon = T._m_per_deg_lon((bbox.north + bbox.south) / 2.0)
    lats = np.array([spec.north_lat - (r + 0.5) * cell_m / T.M_PER_DEG_LAT
                     for r in range(spec.nrows)])
    lons = np.array([spec.west_lon + (c + 0.5) * cell_m / m_lon
                     for c in range(spec.ncols)])
    direct = bands_of(b_direct)
    for canopy, names in ((False, ("fuel", "slope_deg", "aspect_deg",
                                   "elev_m")),
                          (True, ("cbh_m", "cbd_kgm3", "cc_pct", "ch_m"))):
        path = lfps.local_job_zip(abox, canopy)
        bands, (xa, ye, x_ul, y_ul) = T._read_job_zip(
            open(path, "rb").read(), 4, lfps._decode_geotiff)
        sr = np.rint((lats - y_ul) / ye).astype(np.int64)
        sc = np.rint((lons - x_ul) / xa).astype(np.int64)
        R, C = bands[0].shape
        for b, name in enumerate(names):
            src = bands[b].astype(np.float64)
            if canopy:
                div = T.LfpsTerrain.CANOPY_DIVISORS[b]
                src = np.where(src > 0, src / div, 0.0)
            want = np.array(direct[name]).reshape(spec.nrows, spec.ncols)
            hit = np.zeros(want.shape, dtype=bool)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    rr = np.clip(sr + dr, 0, R - 1)
                    cc = np.clip(sc + dc, 0, C - 1)
                    hit |= src[np.ix_(rr, cc)] == want
            out[name] = round(100.0 * hit.mean(), 3)
    return out


def main():
    pairs = json.load(open(os.path.join(HERE, "pairs.json")))
    lfps = T.LfpsTerrain(cache_dir=MIRROR,
                         canopy_layer_list=T.LfpsTerrain.DEFAULT_CANOPY_LAYERS,
                         dep_probe=lambda: None,
                         http_get=lambda *a, **k: (_ for _ in ()).throw(
                             AssertionError("no network")))
    results = []
    for pr in pairs:
        A, B = pr["A"], pr["B"]
        abox = BBox(west=A["west"], south=A["south"], east=A["east"],
                    north=A["north"])
        bbox = BBox(west=B["west"], south=B["south"], east=B["east"],
                    north=B["north"])
        cutter = OnePiece(lfps, abox)
        t = time.time()
        ctrl = cutter.cut(abox, A["cell_m"])
        t_ctrl = time.time() - t
        a_direct = lfps.tile(abox, A["cell_m"])        # A's .wgtile/.wgcanopy
        ctrl_cmp = compare(bands_of(ctrl), bands_of(a_direct))
        t = time.time()
        cut_b = cutter.cut(bbox, B["cell_m"])
        t_cut = time.time() - t
        b_direct = lfps.tile(bbox, B["cell_m"])        # B's .wgtile/.wgcanopy
        assert cut_b.spec == b_direct.spec
        row = {"A": [A["west"], A["south"], A["east"], A["north"]],
               "B": [B["west"], B["south"], B["east"], B["north"]],
               "B_cell_m": B["cell_m"],
               "B_cells": b_direct.spec.nrows * b_direct.spec.ncols,
               "control_identical": all(v["identical_pct"] == 100.0
                                        for v in ctrl_cmp.values()),
               "control_s": round(t_ctrl, 2), "cut_s": round(t_cut, 2),
               "agreement": compare(bands_of(cut_b), bands_of(b_direct)),
               "direct_within_1_native_pixel_pct": within_one_pixel(
                   lfps, abox, bbox, B["cell_m"], b_direct)}
        results.append(row)
        print(json.dumps(row, indent=1))
    tot = {}
    for r in results:
        for k, v in r["agreement"].items():
            s = tot.setdefault(k, [0, 0])
            s[0] += v["identical_pct"] * v["cells"] / 100.0
            s[1] += v["cells"]
    summary = {k: round(100.0 * a / n, 3) for k, (a, n) in tot.items()}
    tot1 = {}
    for r in results:
        n = r["B_cells"]
        for k, v in r["direct_within_1_native_pixel_pct"].items():
            s = tot1.setdefault(k, [0, 0])
            s[0] += v * n / 100.0
            s[1] += n
    within = {k: round(100.0 * a / n, 3) for k, (a, n) in tot1.items()}
    print("pooled identical %:", summary)
    print("pooled within 1 native pixel %:", within)
    with open(os.path.join(HERE, "region_agreement.json"), "w") as f:
        json.dump({"pairs": results, "pooled_identical_pct": summary,
                   "pooled_within_1_native_pixel_pct": within,
                   "tfw_note": "each LFPS job's EPSG:4326 output has its own "
                               "pixel size and origin (e.g. CO pair A "
                               "0.0003401975 deg vs B 0.0003395146 deg): two "
                               "direct fetches of overlapping AOIs sample "
                               "different native pixels"}, f, indent=1)


if __name__ == "__main__":
    main()
