"""scratch_review/escape_roads_shots.py — before/after frames of a fleeing crew
for the SEM-10d road-network escape (docs/review/escape_roads_2026-09-26.md).

Drives scratch_review/roads_inspection_shots.py (headless Chrome over the
DevTools protocol, the page's own api()/render() path) with this task's own
server URL, DevTools port and Chrome profile — other worktrees may run the
same tool concurrently on its defaults — and writes docs/img/escape_roads/.
Start the simulator first:
    py -3 -m wgresponse.cli simui --host 127.0.0.1 --port 18947
Usage:
    py -3 scratch_review/escape_roads_shots.py --probe <probe json> --shots <spec json>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from scratch_review import roads_inspection_shots as ris  # noqa: E402

ris.BASE = os.environ.get("ESCAPE_SHOTS_BASE", "http://127.0.0.1:18947")
ris.OUT_DIR = os.path.join(_REPO, "docs", "img", "escape_roads")
ris.PORT = int(os.environ.get("ESCAPE_SHOTS_CDP_PORT", "19471"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", required=True)
    ap.add_argument("--shots", required=True)
    ap.add_argument("--only", action="append")
    args = ap.parse_args(argv)
    os.makedirs(ris.OUT_DIR, exist_ok=True)
    runs = ris.load_probe(args.probe)
    with open(args.shots, encoding="utf-8") as f:
        shots = json.load(f)
    prof = os.path.join(os.environ.get("TEMP", "."), "wg_escape_roads_cdp_profile")
    proc, cdp = ris.launch(prof)
    try:
        for spec in shots:
            if args.only and spec["name"] not in args.only:
                continue
            print(f"[{spec['name']}] {spec['key']} {spec['zone']} tick {spec['tick']}",
                  flush=True)
            ris.run_shot(cdp, spec, runs)
    finally:
        try:
            cdp.call("Browser.close")
        except Exception:
            pass
        time.sleep(0.5)
        proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
