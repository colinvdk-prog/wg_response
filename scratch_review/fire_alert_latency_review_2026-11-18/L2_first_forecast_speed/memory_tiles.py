"""Lane L2: TERRAIN MEMORY TILES — the per-refresh cost of re-reading a
Willow-scale decoded tile (300k cells, terrain + canopy, real files from
the S3 mirror) without and with the memory, pinned to 2 CPUs like Lane C's
harness, and the memory one held tile costs (tracemalloc).
Writes results/memory_tiles.json."""
from __future__ import annotations

import json
import os
import sys
import time
import tracemalloc

HERE = os.path.dirname(os.path.abspath(__file__))
WORKTREE = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, WORKTREE)
SCRATCH = os.path.join(os.environ["TEMP"], "claude", "78600f61c23682e1",
                       "301cc633-83bf-4a52-9972-a841bce65068", "scratchpad")
MIRROR = os.path.join(SCRATCH, "mirror_pull")

from wgresponse import terrain as T  # noqa: E402
from wgresponse.contracts import BBox  # noqa: E402


def main():
    import psutil
    psutil.Process().cpu_affinity([0, 1])
    pairs = json.load(open(os.path.join(HERE, "pairs.json")))
    A = pairs[0]["A"]                          # the Willow-scale domain
    bbox = BBox(west=A["west"], south=A["south"], east=A["east"],
                north=A["north"])
    out = {"domain": [A["west"], A["south"], A["east"], A["north"]],
           "cell_m": A["cell_m"]}
    plain = T.LfpsTerrain(cache_dir=MIRROR,
                          canopy_layer_list=T.LfpsTerrain.DEFAULT_CANOPY_LAYERS)
    times = []
    for _ in range(3):
        t = time.perf_counter()
        tile = plain.tile(bbox, A["cell_m"])
        times.append(round(time.perf_counter() - t, 3))
    out["cells"] = tile.spec.nrows * tile.spec.ncols
    out["off_s_per_refresh"] = times
    mem = T.TileMemory(2)
    lt = T.LfpsTerrain(cache_dir=MIRROR, memory=mem,
                       canopy_layer_list=T.LfpsTerrain.DEFAULT_CANOPY_LAYERS)
    times = []
    for _ in range(4):
        t = time.perf_counter()
        lt.tile(bbox, A["cell_m"])
        times.append(round(time.perf_counter() - t, 4))
    out["on_s_per_refresh"] = times          # the first is the one read
    out["memory_status"] = mem.status()
    del tile, lt, mem
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    held = T.LfpsTerrain(cache_dir=MIRROR, canopy_layer_list=T.LfpsTerrain
                         .DEFAULT_CANOPY_LAYERS).tile(bbox, A["cell_m"])
    cur = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    out["held_tile_mb"] = round((cur - base) / 1e6, 1)
    out["bands"] = 4 + (4 if held.canopy is not None else 0)
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", "memory_tiles.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
