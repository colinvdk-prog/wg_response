"""scratch_review/worlds_shots.py — screenshots of the TWO WORLDS page
(console/world.html: the LIVE | TEST switch; contracts SAFETY SEMANTICS 13)
over a running watch daemon, for docs/img/worlds/ (docs/LIVE_WORLD_PLAN.md
"Test map and live map (2026-09-27)").

Drives the REAL page in headless Chrome over the DevTools protocol, reusing
the stdlib CDP client + launch / load / settle helpers of
scratch_review/world_viewer_shots.py, against a daemon started separately
(WGRESP_DEV_OPEN=1 on a local box). Frames (every one waits for the page's
own data and the basemap tiles):

    live_regional.jpg     LIVE (the default) framed on the target domain
    test_regional.jpg     the SAME view on the TEST map: amber banner +
                          frame, seeded fire(s) in the seeded colour with the
                          dashed ring, test crews vs ghosted live crews, the
                          drill panel with the seeds / retire / drill log
    test_seed_control.jpg the seed control armed: a click on the map
                          dropped the pending ignition, the intensity form
    live_local.jpg /      the local tier on each map (plan legs from the
    test_local.jpg        live crews vs from the virtual test crews)
    test_national.jpg     the TEST map at the AOI fit
    test_offline.jpg      the TEST map in the offline SVG shim (?nomap=1)

The target: the first SEEDED domain on the test map (else the top of the
test ranked list). Usage:
    py -3 scratch_review/worlds_shots.py --base http://127.0.0.1:18989
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import world_viewer_shots as wv  # noqa: E402

wv.DEVTOOLS_PORT = 9471          # its own DevTools port (other runs use 9357)
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(_REPO, "docs", "img", "worlds")


def _target(cdp):
    return cdp.js("""(() => {
      const rows = SUMMARY.incidents;
      const seeded = rows.filter(r => r.source === "seeded" && r.domain);
      const dom = rows.filter(r => r.domain);
      const pick = seeded[0] || dom[0] || null;
      if (!pick) return null;
      return { id: pick.incident_id, name: pick.name, bbox: pick.domain.bbox,
               lat: pick.lat, lon: pick.lon, seeded: pick.source === "seeded" };
    })()""")


def _frame_bbox(cdp, bbox, max_zoom=11):
    s, w, n, e = bbox
    cdp.js(f"map.fitBounds([[{s},{w}],[{n},{e}]], "
           f"{{maxZoom: {max_zoom}, animate: false}}); true")
    wv.settle(cdp)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:18989")
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--seed-at", default=None,
                    help="lat,lon for the armed seed-control frame (default: "
                         "3 km south-west of the target)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="wg_worlds_shots_")
    proc, cdp = wv.launch(profile)
    report = {}
    base = args.base.rstrip("/")
    try:
        # the TEST map first: it names the target (the seeded domain)
        wv.load(cdp, base + "/world?world=test")
        wv.settle(cdp)
        tgt = _target(cdp)
        report["target"] = tgt
        report["test_national"] = cdp.js(
            "({world: WORLD, banner: getComputedStyle(document.getElementById("
            "'testbanner')).display, drill: STATE.drill, crews: STATE.crews.length, "
            "live_crews: (STATE.live_crews || []).length, seeds: (STATE.seeds || [])"
            ".filter(s => s.status === 'active').length, incidents: "
            "SUMMARY.incidents.length})")
        cdp.shot(os.path.join(args.out, "test_national.jpg"))
        if tgt:
            _frame_bbox(cdp, tgt["bbox"])
            cdp.shot(os.path.join(args.out, "test_regional.jpg"))
            view = cdp.js("({z: map.getZoom(), c: map.getCenter()})")
            # the same view on the LIVE map (the default world)
            wv.load(cdp, base + "/world")
            wv.settle(cdp)
            _frame_bbox(cdp, tgt["bbox"])
            report["live_regional"] = cdp.js(
                "({world: WORLD, banner: getComputedStyle(document.getElementById("
                "'testbanner')).display, drillpanel: getComputedStyle("
                "document.getElementById('drillwrap')).display, seeded: "
                "SUMMARY.incidents.filter(r => r.source === 'seeded').length, "
                "incidents: SUMMARY.incidents.length})")
            cdp.shot(os.path.join(args.out, "live_regional.jpg"))
            # local tier on both maps, framed on the target
            cdp.js(f"map.setView([{tgt['lat']}, {tgt['lon']}], 12, "
                   f"{{animate: false}}); true")
            wv.settle(cdp)
            cdp.shot(os.path.join(args.out, "live_local.jpg"))
            wv.load(cdp, base + "/world?world=test")
            wv.settle(cdp)
            cdp.js(f"map.setView([{tgt['lat']}, {tgt['lon']}], 12, "
                   f"{{animate: false}}); true")
            wv.settle(cdp)
            cdp.shot(os.path.join(args.out, "test_local.jpg"))
            # the seed control armed: a synthetic map click drops the point
            _frame_bbox(cdp, tgt["bbox"])
            if args.seed_at:
                la, lo = (float(x) for x in args.seed_at.split(","))
            else:
                la, lo = tgt["lat"] - 0.02, tgt["lon"] - 0.03
            armed = cdp.js(f"""(() => {{
              document.getElementById('seedbtn').click();
              map.fire ? map.fire('click', {{latlng: L.latLng({la}, {lo})}})
                       : null;
              document.getElementById('seedint').value = 'extreme';
              document.getElementById('seedwind').value = '225';
              document.getElementById('seednote').value = 'drill';
              return {{armed: seedArmed, point: seedPoint,
                       form: !document.getElementById('seedform').hidden}};
            }})()""")
            time.sleep(0.6)
            report["test_seed_control"] = armed
            cdp.shot(os.path.join(args.out, "test_seed_control.jpg"))
            cdp.js("armSeed(false); true")
            # a STAGED test crew next to its ghosted live self (when one was
            # placed): the TEST map, then the same view on LIVE
            pair = cdp.js("""(() => {
              const live = {}; for (const c of (STATE.live_crews || [])) live[c.crew_id] = c;
              const t = (STATE.crews || []).find(c => c.origin === "placed"
                && live[c.crew_id] && c.lat != null && live[c.crew_id].lat != null);
              if (!t) return null;
              const l = live[t.crew_id];
              return { id: t.crew_id, s: Math.min(t.lat, l.lat) - 0.04,
                       n: Math.max(t.lat, l.lat) + 0.04,
                       w: Math.min(t.lon, l.lon) - 0.06,
                       e: Math.max(t.lon, l.lon) + 0.06 };
            })()""")
            report["crews_pair"] = pair
            if pair:
                bb = [pair["s"], pair["w"], pair["n"], pair["e"]]
                _frame_bbox(cdp, bb, max_zoom=12)
                cdp.shot(os.path.join(args.out, "test_crews.jpg"))
                wv.load(cdp, base + "/world")
                wv.settle(cdp)
                _frame_bbox(cdp, bb, max_zoom=12)
                cdp.shot(os.path.join(args.out, "live_crews.jpg"))
        wv.load(cdp, base + "/world?world=test&nomap=1")
        wv.settle(cdp)
        report["test_offline"] = {"fallback": cdp.js("!!L._fallback"),
                                  "world": cdp.js("WORLD")}
        cdp.shot(os.path.join(args.out, "test_offline.jpg"))
    finally:
        try:
            cdp.call("Browser.close")
        except Exception:
            pass
        proc.wait(timeout=10)
        shutil.rmtree(profile, ignore_errors=True)
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
