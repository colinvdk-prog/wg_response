"""scratch_review/crew_placement_shots.py — JPEG frames of CREW PLACEMENT
(founder 2026-09-26 "allow the crews to be placed on the map") on the real
page, written to docs/img/crew_placement/.

A thin wrapper over scratch_review/roads_inspection_shots.py (its CDP client,
headless-Chrome launcher and page helpers; BASE / OUT_DIR / PORT are
overridden here). Everything is driven like a user: the Place-crews button,
REAL mouse clicks on the map (CDP Input.dispatchMouseEvent at the pixel of a
lat/lon), a REAL marker drag, the High intensity button + a map click to
ignite, and the Step button. The page state is read back after every action
and the key facts asserted (a click in placement mode never lights a fire;
every placement sits on a road node; the post-ignition lock).

Session A (eagle_vail_co, 40 clients, 4 in-zone + 1 +1h crew, seed
placement-demo, the page's physics defaults): crews g1-g3 placed by map
clicks, a click on roadless ski terrain refused, g4 dragged; then a HIGH fire
WSW of Edwards (wind FROM 240) and two ticks — crews dispatched from their
placed starts. Session B: g4 dragged ~2 km DOWNWIND of the same ignition —
the safety frames (tick 1: the 10c audit reports it inside the pessimistic
front while the sweep turns it out; tick 3: relocated, violations 0).

Usage (simulator served separately, e.g.
`py -3 -m wgresponse.cli simui --host 127.0.0.1 --port 8893`):
    py -3 scratch_review/crew_placement_shots.py [--base http://127.0.0.1:8893]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import roads_inspection_shots as ris  # noqa: E402

ris.OUT_DIR = os.path.join(ris._REPO, "docs", "img", "crew_placement")
ris.PORT = 9353                      # not the roads tool's 9339 (parallel runs)

LOC = "eagle_vail_co"
SEED = "placement-demo"
IGN = (39.62701, -106.64906)         # TU1 timber WSW of Edwards
CLICKS = [                           # session A: g1..g3 by map click
    (39.6452, -106.5935),            # g1 (from the Eagle base) -> Edwards, US-6
    (39.6560, -106.6580),            # g2 (from Gypsum) -> Squaw Creek Rd
    (39.6318, -106.5205),            # g3 (from Edwards) -> Avon Rd
]
ROADLESS = (39.595, -106.556)        # Beaver Creek ski terrain, 2.4 km off-road
DRAG_A = (39.6473, -106.6122)        # g4 (from Avon) -> 3.5 km downwind
DRAG_B = (39.6345, -106.6285)        # session B: g4 -> ~2 km DOWNWIND
VIEW_PTS = [IGN, ROADLESS, (39.6605, -106.6620), (39.6365, -106.5150)]

PAGE = r"""
window.__cp = {
  px(lat, lon) {
    const p = map.latLngToContainerPoint([lat, lon]);
    const r = $("map").getBoundingClientRect();
    return [r.left + p.x, r.top + p.y];
  },
  crewPx(cid) {
    const ll = crewReg[cid].marker.getLatLng();
    return this.px(ll.lat, ll.lng);
  },
  facts() {
    const st = lastState || {};
    const cp = st.crew_placement || {};
    return {
      tick: st.tick, fires: (st.fires || []).length, placing,
      armed, setup: cp.setup, locked_reason: cp.locked_reason,
      placed: (cp.placed || []).map(p => [p.crew_id, p.lat, p.lon, p.snap_m]),
      crews: (st.crews || []).map(c => [c.crew_id, c.lat, c.lon, c.status,
                                        c.relocating ? c.relocate_reason : null]),
      violations: (st.scoreboard || {}).violations,
      overruns: (st.crew_overruns || []).map(o => [o.crew_id, o.reason]),
      relocations: (st.scoreboard || {}).relocations,
      served: (st.scoreboard || {}).served,
      place_disabled: $("placecrews").disabled,
      place_title: $("placecrews").title,
      hint: $("placehint").textContent,
      maphint: $("placemaphint").style.display === "block"
                ? $("placemaphint").textContent : null,
      toast: $("toast").style.display === "block" ? $("toast").textContent : null,
    };
  },
  caption(text) {
    let el = document.getElementById("__cp_cap");
    if (!el) {
      el = document.createElement("div"); el.id = "__cp_cap";
      el.style.cssText = "position:fixed;z-index:3000;right:350px;bottom:84px;"
        + "background:#101820ee;color:#ffe;font:600 13px sans-serif;"
        + "padding:6px 12px;border:1px solid #4dd0e1;border-radius:6px;"
        + "max-width:560px;text-align:left";
      document.body.appendChild(el);
    }
    el.textContent = text; el.style.display = text ? "block" : "none";
  },
};
true
"""


def mouse_click(cdp, x, y):
    cdp.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
    cdp.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y,
             button="left", buttons=1, clickCount=1)
    cdp.call("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y,
             button="left", buttons=0, clickCount=1)


def mouse_drag(cdp, x0, y0, x1, y1, steps=16):
    cdp.call("Input.dispatchMouseEvent", type="mouseMoved", x=x0, y=y0)
    cdp.call("Input.dispatchMouseEvent", type="mousePressed", x=x0, y=y0,
             button="left", buttons=1, clickCount=1)
    for k in range(1, steps + 1):
        cdp.call("Input.dispatchMouseEvent", type="mouseMoved",
                 x=x0 + (x1 - x0) * k / steps, y=y0 + (y1 - y0) * k / steps,
                 button="left", buttons=1)
        time.sleep(0.03)
    cdp.call("Input.dispatchMouseEvent", type="mouseReleased", x=x1, y=y1,
             button="left", buttons=0, clickCount=1)


def click_latlon(cdp, lat, lon, settle_s=1.5):
    x, y = cdp.js(f"window.__cp.px({lat}, {lon})")
    mouse_click(cdp, x, y)
    time.sleep(settle_s)


def facts(cdp):
    return cdp.js("window.__cp.facts()")


def park_mouse(cdp):
    """Move the pointer off the map (bottom of the right panel) so no hover
    tooltip is frozen into a frame by accident."""
    cdp.call("Input.dispatchMouseEvent", type="mouseMoved", x=1590, y=995)
    time.sleep(0.2)


STILL_CSS = ("#crewoverrunbanner, #sb_violations.viol, #sb_overruns_row.hot "
             "#sb_overruns, .crew-icon.overrun { animation: none !important; }")


def shot(cdp, name, caption, tooltip_crew=None):
    park_mouse(cdp)
    # a still frame must not catch the red alarms mid-blink (faded): hold
    # them at full intensity — cosmetic, capture-only
    cdp.js("(() => { if (!document.getElementById('__cp_still')) { const s = "
           "document.createElement('style'); s.id = '__cp_still'; s.textContent"
           f" = {json.dumps(STILL_CSS)}; document.head.appendChild(s); }} "
           "return true; })()")
    if tooltip_crew:                  # a deliberate "hover" on one crew
        cdp.js(f'crewReg["{tooltip_crew}"].marker.openTooltip(); true')
    cdp.js(f"window.__cp.caption({json.dumps(caption)}); true")
    ris.wait_tiles(cdp)
    path = os.path.join(ris.OUT_DIR, name)
    cdp.shot(path)
    if tooltip_crew:
        cdp.js(f'crewReg["{tooltip_crew}"].marker.closeTooltip(); true')
    print(f"  wrote {path}", flush=True)


def step(cdp, n=1, cadence_s=2.0):
    for _ in range(n):
        cdp.js('$("step").click(); true')
        t0 = time.time()
        while time.time() - t0 < 60:
            time.sleep(0.4)
            if not cdp.js("busy"):
                break
        time.sleep(cadence_s)


def new_session(cdp):
    cdp.call("Page.navigate", url=f"{ris.BASE}/simulator")
    time.sleep(1.0)
    ris.wait_ready(cdp)
    cdp.js(ris.HELPERS)
    cdp.js(PAGE)
    body = {"location": LOC, "clients": 40, "crews": 4, "crews_1h": 1,
            "fire_zone": "standard", "spotting": True, "grass_realism": True,
            "seed": SEED}
    cdp.js(f"window.__insp.newSess({json.dumps(body)}).then(s => s.tick)")
    # the legend window opens over the left panel's placement list: fold it
    cdp.js('(() => { if (!document.getElementById("legend").classList'
           '.contains("collapsed")) $("lg-collapse").click(); return true; })()')
    lat, lon, zoom = ris.view_for([list(p) for p in VIEW_PTS])
    cdp.js(f"map.setView([{lat}, {lon}], {zoom}); true")
    time.sleep(1.2)


def ignite_via_ui(cdp):
    cdp.js('$("int-high").click(); true')          # arms High: leaves placement
    f = facts(cdp)
    assert f["armed"] == "high" and f["placing"] is False, f
    click_latlon(cdp, *IGN, settle_s=0.5)
    t0 = time.time()
    while facts(cdp)["fires"] == 0 and time.time() - t0 < 60:
        time.sleep(0.5)
    f = facts(cdp)
    assert f["fires"] == 1, f
    # disarm so later clicks are inert (the frame shows the post-fire lock)
    cdp.js('$("int-high").click(); true')
    return f


def run(base):
    ris.BASE = base
    os.makedirs(ris.OUT_DIR, exist_ok=True)
    prof = os.path.join(os.environ.get("TEMP", "."), "wg_crew_place_cdp_profile")
    proc, cdp = ris.launch(prof)
    report = {}
    try:
        # ---------------- session A: placement UX + dispatch from placed starts
        new_session(cdp)
        f = facts(cdp)
        assert f["setup"] is True and f["place_disabled"] is False, f
        cdp.js('$("placecrews").click(); true')       # enter Place-crews mode
        for lat, lon in CLICKS:                       # g1, g2, g3 (cycling)
            click_latlon(cdp, lat, lon)
        f = facts(cdp)
        assert f["fires"] == 0, f                     # clicks never ignited
        assert [p[0] for p in f["placed"]] == ["sim-g1", "sim-g2", "sim-g3"], f
        click_latlon(cdp, *ROADLESS, settle_s=0.8)    # next = g4: refused
        f = facts(cdp)
        assert f["fires"] == 0 and len(f["placed"]) == 3, f
        report["refused_toast"] = f["toast"]
        shot(cdp, "01_refused_off_road.jpg",
             "SETUP — Place crews mode: g1, g2, g3 were each put on the road "
             "nearest a map click (dotted tick = click -> road node). A click "
             "on roadless ski terrain is refused: no road within 2 km.")
        x0, y0 = cdp.js('window.__cp.crewPx("sim-g4")')
        x1, y1 = cdp.js(f"window.__cp.px({DRAG_A[0]}, {DRAG_A[1]})")
        mouse_drag(cdp, x0, y0, x1, y1)
        time.sleep(1.8)
        f = facts(cdp)
        assert [p[0] for p in f["placed"]] == ["sim-g1", "sim-g2", "sim-g3",
                                              "sim-g4"], f
        assert f["fires"] == 0, f
        assert "crew g1" in f["hint"], f            # the cursor cycled on
        report["setup_A"] = f
        shot(cdp, "02_setup_all_placed.jpg",
             "SETUP — all four in-zone crews placed on roads of the user's "
             "choice (g4 by dragging its marker). No fire is lit in this mode; "
             "escape targets stay each crew's safe zone (Z) — hover shown for g2.",
             tooltip_crew="sim-g2")
        ignite_via_ui(cdp)
        f = facts(cdp)
        assert f["place_disabled"] is True and "dispatched by the engine" in f["hint"], f
        step(cdp, 2)
        f = facts(cdp)
        report["after_A_tick2"] = f
        shot(cdp, "03_dispatched_from_placed_starts.jpg",
             f"AFTER IGNITION (HIGH, wind FROM 240), tick {f['tick']}: the "
             f"crews are dispatched from their placed starts along the roads. "
             f"Place crews is now disabled — crews are dispatched by the "
             f"engine; reset the scenario to re-stage.")
        # ---------------- session B: a crew placed IN the fire's path
        new_session(cdp)
        cdp.js('$("placecrews").click(); true')
        x0, y0 = cdp.js('window.__cp.crewPx("sim-g4")')
        x1, y1 = cdp.js(f"window.__cp.px({DRAG_B[0]}, {DRAG_B[1]})")
        mouse_drag(cdp, x0, y0, x1, y1)
        time.sleep(1.8)
        f = facts(cdp)
        assert [p[0] for p in f["placed"]] == ["sim-g4"], f
        report["setup_B"] = f
        ignite_via_ui(cdp)
        step(cdp, 1)
        f = facts(cdp)
        report["after_B_tick1"] = f
        shot(cdp, "04_fire_path_tick1_reported.jpg",
             f"SAFETY — g4 was placed ~2 km DOWNWIND of where the fire was "
             f"then lit. Tick {f['tick']}: it is inside the pessimistic front, "
             f"so the audit REPORTS it (never silent) while the SEM-10 sweep "
             f"turns it out (amber: repositioning).")
        step(cdp, 2)
        f = facts(cdp)
        report["after_B_tick3"] = f
        g4 = next(c for c in f["crews"] if c[0] == "sim-g4")
        assert f["violations"] == 0 and f["overruns"] == [], f
        shot(cdp, "05_fire_path_tick3_relocated.jpg",
             f"SAFETY — tick {f['tick']}: g4 has driven out of the fire's path "
             f"on the roads ({g4[3]}"
             f"{', ' + g4[4].replace('_', ' ') if g4[4] else ''}); crews caught "
             f"0, violations {f['violations']}, relocations {f['relocations']}, "
             f"departure violations 0.")
    finally:
        try:
            cdp.sock.close()
        except OSError:
            pass
        proc.kill()
    out = os.path.join(ris._REPO, "out", "crew_placement_shots_report.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    print(json.dumps(report, indent=1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8893")
    args = ap.parse_args(argv)
    run(args.base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
