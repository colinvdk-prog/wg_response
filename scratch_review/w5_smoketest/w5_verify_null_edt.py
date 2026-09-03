"""W5 verification: does tools/hindcast_lib.squared_edt reproduce
scratch_review/rv2sci_a2_null.py's edt2 bit-for-bit on THAT script's own
cached inputs (scratch_review/hindcast_aug_data, real August LFPS tiles)?

Loads rv2sci_a2_null.py by file path (it is not a package, and importing it
normally would need `scratch_review` on sys.path with no __init__.py) so its
edt2/_dt1 are callable without running its main(). Builds the SAME T0 mask
construction rv2sci_a2_null.py uses (engine-independent: perimeter-interior
rasterization of the first snapshot of each cached incident, no ensemble
run needed) and compares squared-distance fields elementwise.

Read-only against scratch_review/ and tools/; writes nothing outside this
scratch directory.
"""
from __future__ import annotations
import importlib.util
import os
import sys

REPO = r"C:\Users\colin\wg_response"
sys.path.insert(0, REPO)

from tools import hindcast_lib as H  # noqa: E402
from tools.hindcast_harness import _incident_domain, _load_all_incidents  # noqa: E402
from wgresponse.contracts import ResponseConfig  # noqa: E402
from wgresponse.terrain import LfpsTerrain, load_ascii_tile  # noqa: E402
from wgresponse.spread import _perimeter_interior_cells  # noqa: E402

DATA = os.path.join(REPO, "scratch_review", "hindcast_aug_data")
REF_PATH = os.path.join(REPO, "scratch_review", "rv2sci_a2_null.py")

spec = importlib.util.spec_from_file_location("rv2sci_a2_null_ref", REF_PATH)
ref = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ref)   # module-level defs only run; main() is guarded


def main() -> int:
    cfg0 = ResponseConfig()
    checked = 0
    max_abs_diff = 0.0
    mismatches = 0
    for slug, inc in _load_all_incidents(DATA):
        if checked >= 6:      # a handful of real incidents is plenty
            break
        bbox, cell_m = _incident_domain(inc, cfg0)
        lfps = LfpsTerrain(cache_dir=os.path.join(DATA, "lfps"))
        cp = lfps.cache_path(bbox, cell_m)
        if not (cp and os.path.exists(cp)):
            print(f"  SKIP {inc.name}: no cached tile")
            continue
        tile = load_ascii_tile(cp)
        gs = tile.spec
        nr, nc = gs.nrows, gs.ncols
        a = inc.snapshots[0]
        rings = [H.subsample_ring(r, H.MAX_RING_VERTS) for r in a.rings]
        t0mask = set(_perimeter_interior_cells(gs, rings))
        if not t0mask:
            print(f"  SKIP {inc.name}: empty T0 mask")
            continue

        # -- reference (rv2sci_a2_null.py), exactly as its own main() builds it --
        src = [False] * (nr * nc)
        for i in t0mask:
            src[i] = True
        d2_ref = ref.edt2(src, nr, nc)

        # -- ported version (tools/hindcast_lib.squared_edt) --
        d2_mine = H.squared_edt(t0mask, nr, nc)

        n = nr * nc
        assert len(d2_ref) == len(d2_mine) == n
        diff = max(abs(d2_ref[i] - d2_mine[i]) for i in range(n))
        ok = diff == 0.0
        max_abs_diff = max(max_abs_diff, diff)
        if not ok:
            mismatches += 1
        print(f"  {inc.name:<16} grid {nr}x{nc}={n:7d} cells  "
              f"T0 mask={len(t0mask):6d}  max|d2_ref-d2_mine|={diff!r}  "
              f"{'MATCH' if ok else 'MISMATCH'}")
        checked += 1

    print()
    print(f"incidents checked: {checked}, mismatches: {mismatches}, "
          f"worst max-abs-diff across all: {max_abs_diff!r}")
    return 1 if (checked == 0 or mismatches) else 0


if __name__ == "__main__":
    raise SystemExit(main())
