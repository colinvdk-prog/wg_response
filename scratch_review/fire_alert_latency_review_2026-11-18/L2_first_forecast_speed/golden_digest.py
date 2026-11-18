"""Lane L2: knob-off byte identity against the BASE commit. Run with a repo
root (this worktree, or `git archive 9d11793` extracted with this lane's
tests/test_first_forecast_speed.py copied in): prints the cycle digests of
the legacy paths — a stub terrain with and without forecast tiers, and the
synthetic-LFPS LfpsTerrain (cold fetch, then the cache) — which
tests/test_first_forecast_speed.py pins."""
from __future__ import annotations

import json
import os
import sys
import tempfile

root = os.path.abspath(sys.argv[1])
sys.path.insert(0, root)
os.chdir(root)

import tests.test_first_forecast_speed as F  # noqa: E402
from tests.seeded_stack import StubTerrain  # noqa: E402
import wgresponse  # noqa: E402

assert os.path.dirname(wgresponse.__file__).startswith(root)
out = {
    "stub": F._cycle_digest(F.make_cfg(), StubTerrain(), cycles=3),
    "stub_tiers": F._cycle_digest(F.make_cfg(forecast_tiers=True),
                                  StubTerrain(), cycles=3),
}
with tempfile.TemporaryDirectory() as d:
    import pathlib
    lt = F.lfps_for(pathlib.Path(d), F.FakeLfps())
    out["lfps"] = F._cycle_digest(F.make_cfg(), lt, cycles=3)
print(json.dumps(out))
