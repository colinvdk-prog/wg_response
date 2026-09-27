"""scratch_review/old_black_shots.py — frames for the founder rulings of
2026-09-26 ("old burned ground does not count as a violation. crews once
leave a map are no longer part of the simulation"; docs/SIMULATOR_DESIGN.md
"Old black is a refuge; crews that leave the map are out (2026-09-26)").

Drives scratch_review/roads_inspection_shots.py (headless Chrome over the
DevTools protocol, the page's own api()/render() path) with this task's own
server URL, DevTools port and Chrome profile — other worktrees may run the
same tool concurrently on its defaults — and writes docs/img/old_black_left_map/:

  a01_crew_resting_on_old_black   boulder_co: crew 1 placed on a grass road
  (+ a01b with its tooltip)       node, a HIGH fire lit 600 m upwind; its
                                  head overruns the crew inside the first tick
                                  (reported), then the crew rests on the black
                                  behind the front — no row, no relocation;
                                  the tooltip / crew card say why.
  a02_crews_left_the_map          beaver_creek_co, the roads-offroad probe
  (+ a02b: tooltip, greyed card)  session (the page's defaults): three crews
                                  fled by road to a node 147 m past the east
                                  edge of the terrain and are out — greyed at
                                  their last position, the crew cards greyed,
                                  the scoreboard's "crews left the map" line,
                                  pinned feed lines.

Start the simulator first (a private port):
    py -3 -m wgresponse.cli simui --host 127.0.0.1 --port 18956
Usage:
    py -3 scratch_review/old_black_shots.py --probe scratch_review/old_black/probe_oldblack_on2.json
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

ris.BASE = os.environ.get("OLDBLACK_SHOTS_BASE", "http://127.0.0.1:18956")
ris.OUT_DIR = os.path.join(_REPO, "docs", "img", "old_black_left_map")
ris.PORT = int(os.environ.get("OLDBLACK_SHOTS_CDP_PORT", "19566"))

#: a grass road node 2.8 km west of the boulder_co centre (graph node 22915;
#: every tile cell within two of it is GR/GS — picked by scanning the road
#: graph for such nodes and checking headless that a placed crew there is
#: overrun once, then rests on old black)
OLD_BLACK_SPOT = (39.95485, -105.19319)
#: 600 m upwind (west) of it, a burnable cell (the HIGH fire's head crosses
#: the crew inside the first 15-min tick)
IGNITION = (39.95485, -105.200229)


def _tooltip(cdp, crew, match):
    return cdp.js(
        "(() => { const re = new RegExp(" + json.dumps(match) + ");"
        " const R = crewReg[" + json.dumps(crew) + "]; if (!R) return null;"
        " const t = R.marker.getTooltip && R.marker.getTooltip();"
        " if (t && re.test(t.getContent())) { R.marker.openTooltip(); return true; }"
        " return false; })()")


COLLAPSE_LEGEND = ("(() => { try { localStorage.setItem('wg_legend_collapsed', '1'); }"
                   " catch (e) {} const lg = document.getElementById('legend');"
                   " if (lg) lg.classList.add('collapsed'); return true; })()")


def old_black_frame(cdp, ticks: int = 6) -> None:
    cdp.call("Page.navigate", url=f"{ris.BASE}/simulator")
    time.sleep(1.0)
    ris.wait_ready(cdp)
    cdp.js(ris.HELPERS)
    cdp.js(COLLAPSE_LEGEND)
    body = {"location": "boulder_co", "clients": 20, "crews": 2, "crews_1h": 0,
            "crews_3h": 0, "fire_zone": "standard", "spotting": True,
            "grass_realism": True, "seed": "oldblack-frame"}
    cdp.js(f"window.__insp.newSess({json.dumps(body)}).then(s => s.tick)")
    lat, lon = OLD_BLACK_SPOT
    rec = cdp.js("api('POST', `/api/sim/${sid}/crew`, "
                 + json.dumps({"crew_id": "sim-g1", "lat": lat, "lon": lon})
                 + ").then(o => { render(o.state); return o.placement; })")
    print("  placed:", rec, flush=True)
    # a HIGH fire lit 600 m UPWIND (west; wind FROM 270): its head runs over
    # the crew inside the first tick (reported), then the crew stands in
    # the black behind the front
    cdp.js(f"window.__insp.ignite({IGNITION[0]}, {IGNITION[1]}, 'high', 270)")
    cdp.js(f"map.setView([{rec['lat'] - 0.0006}, {rec['lon'] + 0.0012}], 16); true")
    rows = []
    for _k in range(ticks):
        st_rows = cdp.js("window.__insp.step().then(() => window.__insp.sleep(1400))"
                         ".then(() => (lastState.crew_overruns || [])"
                         ".filter(o => o.crew_id === 'sim-g1').map(o => o.reason))")
        rows.append(st_rows)
    ob = cdp.js("(lastState.crews || []).find(c => c.crew_id === 'sim-g1')")
    print(f"  rows per tick {rows}; on_old_black {ob.get('on_old_black')}; "
          f"relocating {ob.get('relocating')}", flush=True)
    ris.wait_tiles(cdp)
    cdp.js("window.__insp.caption(" + json.dumps(
        f"boulder_co · crew 1 (placed on a grass road) was overrun by a HIGH "
        f"fire lit 600 m upwind — reported at the first tick; since then it "
        f"rests on the black behind the front: not a violation, not moved "
        f"(tick {ticks})") + "); true")
    path = os.path.join(ris.OUT_DIR, "a01_crew_resting_on_old_black.jpg")
    cdp.shot(path)
    print("  wrote", path, flush=True)
    print("  tooltip:", _tooltip(cdp, "sim-g1", "old black"), flush=True)
    path = os.path.join(ris.OUT_DIR, "a01b_crew_on_old_black_tooltip.jpg")
    cdp.shot(path)
    print("  wrote", path, flush=True)


def left_map_frame(cdp, runs, tick: int = 10) -> None:
    spec = {"name": "a02_crews_left_the_map", "key": "beaver_creek_co",
            "zone": "standard", "tick": tick,
            "view": [39.6118, -106.4575, 15],
            "caption": ("beaver_creek_co · three crews fled by road to a node "
                        "147 m past the east edge of the terrain (dashed) and "
                        "left the map — greyed at their last position, out of "
                        "the simulation")}
    ris.run_shot(cdp, spec, runs)
    n = cdp.js("(lastState.scoreboard || {}).crews_left_map")
    print(f"  scoreboard crews_left_map = {n}", flush=True)
    print("  tooltip:", _tooltip(cdp, "sim-g6", "LEFT THE MAP"), flush=True)
    # the crew list: scroll the first greyed card into view
    cdp.js("(() => { const el = document.querySelector('.crew-block.leftmap');"
           " if (el) el.scrollIntoView({block: 'center'}); return !!el; })()")
    path = os.path.join(ris.OUT_DIR, "a02b_left_the_map_tooltip_and_cards.jpg")
    cdp.shot(path)
    print("  wrote", path, flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", required=True)
    ap.add_argument("--only", action="append")
    args = ap.parse_args(argv)
    os.makedirs(ris.OUT_DIR, exist_ok=True)
    runs = ris.load_probe(args.probe)
    prof = os.path.join(os.environ.get("TEMP", "."), "wg_old_black_cdp_profile")
    proc, cdp = ris.launch(prof)
    try:
        if not args.only or "old_black" in args.only:
            print("[a01] old black", flush=True)
            old_black_frame(cdp)
        if not args.only or "left_map" in args.only:
            print("[a02] left the map", flush=True)
            left_map_frame(cdp, runs)
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
