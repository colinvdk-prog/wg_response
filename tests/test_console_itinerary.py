"""tests/test_console_itinerary.py — THE CONSOLE'S ITINERARY PANE READS THE
LIVE PLAN (SAFETY SEMANTICS 45h; the crew-program review's lane C finding F7:
console/index.html read `d.routes`, which only the offline sim file carries —
the live /api/state carries the dispatch plan — so the pane always said "no
active itineraries").

The page now builds the routes from `d.plan` when `d.routes` is absent:
each crew's stops in plan order with their margins, its egress zone, the
straight chords the live planner judges, and a home held for a crew that
reports working it (SEMANTICS 45d); the crew watch (45a-c) is listed above.

RENDERED in a headless Chromium (Chrome or Edge, or WG_TEST_BROWSER; a LOUD
skip when none is installed) against real /api/state payloads of the live
watch stack (tests/seeded_stack.py), served as the page's offline ?file=
input with every host but 127.0.0.1 unresolvable (the map in its offline
shim); the DOM the page built is read back."""
from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from html.parser import HTMLParser
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
from wgresponse.contracts import CrewBeacon, CrewStatus, LatLon  # noqa: E402
from wgresponse.crews import CrewTracker  # noqa: E402

PAGE = Path(__file__).resolve().parent.parent / "console" / "index.html"
T0 = ss.T0


# ---------------------------------------------------------------------------
# static conformance
# ---------------------------------------------------------------------------

def test_the_page_builds_routes_from_the_live_plan():
    html = PAGE.read_text(encoding="utf-8")
    assert "function routesFromPlan(d)" in html
    assert "Array.isArray(d.routes) ? d.routes : routesFromPlan(d)" in html
    assert 'const HELD_PREFIX = "in_progress_by:"' in html
    assert "function crewWatchHtml(d)" in html and "d.crew_watch" in html
    # the offline sim file keeps its own routes
    assert "(d.routes || []).forEach" not in html


# ---------------------------------------------------------------------------
# rendered
# ---------------------------------------------------------------------------

class _El:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    @property
    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def text(self):
        return "".join(c if isinstance(c, str) else c.text()
                       for c in self.children)

    def walk(self):
        for c in self.children:
            if isinstance(c, _El):
                yield c
                yield from c.walk()

    def find(self, cls):
        return [e for e in self.walk() if cls in e.classes]


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}


class _Dom(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = _El("#root", {}, None)
        self.cur = self.root
        self.ids = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        el = _El(tag, attrs, self.cur)
        self.cur.children.append(el)
        if el.attrs.get("id"):
            self.ids[el.attrs["id"]] = el
        if tag not in _VOID:
            self.cur = el

    def handle_startendtag(self, tag, attrs):
        el = _El(tag, attrs, self.cur)
        self.cur.children.append(el)
        if el.attrs.get("id"):
            self.ids[el.attrs["id"]] = el

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        el = self.cur
        while el is not self.root and el.tag != tag:
            el = el.parent
        if el is not self.root:
            self.cur = el.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def _browser():
    cands = [os.environ.get("WG_TEST_BROWSER")]
    for name in ("chrome", "google-chrome", "chromium", "chromium-browser",
                 "msedge", "microsoft-edge"):
        cands.append(shutil.which(name))
    cands += [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


class _Stub(http.server.BaseHTTPRequestHandler):
    page = b""
    state = b""

    def log_message(self, *_a):
        pass

    def do_GET(self):
        if self.path.startswith("/index.html"):
            body, ctype = self.page, "text/html; charset=utf-8"
        elif self.path.startswith("/state.json"):
            body, ctype = self.state, "application/json"
        else:
            body, ctype = b"{}", "application/json"
            self.send_response(404)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _render(state: dict, prof_dir) -> _Dom:
    browser = _browser()
    if browser is None:
        pytest.skip("LOUD SKIP: no headless Chromium (Chrome / Edge) found — "
                    "set WG_TEST_BROWSER to render the ops console")
    handler = type("H", (_Stub,), {
        "page": PAGE.read_bytes(),
        "state": json.dumps(ss.jsonable(state)).encode("utf-8")})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        url = (f"http://127.0.0.1:{srv.server_address[1]}"
               f"/index.html?file=/state.json&nomap=1")
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run",
               "--no-default-browser-check", "--disable-extensions",
               "--disable-background-networking", "--disable-sync",
               "--mute-audio", f"--user-data-dir={prof_dir}",
               "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
               "--virtual-time-budget=10000", "--dump-dom", url]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
    finally:
        srv.shutdown()
        srv.server_close()
    dom = _Dom(p.stdout)
    assert "itinlist" in dom.ids, (p.returncode, p.stderr[-3000:])
    return dom


def _live_state(tmp_path, deploying=False, **cfgkw):
    """A real /api/state of the live watch stack: G1 planned for c1 (and,
    with deploying, then working c1 — held and watched)."""
    cfg = ss.small_cfg(**cfgkw)
    tracker = CrewTracker(cfg)
    clock = ss.SettableClock(T0)
    d, *_ = ss.make_stack(tmp_path, cfg=cfg, clock=clock, tracker=tracker,
                          test_world=False)
    tracker.ingest(CrewBeacon("G1", LatLon(39.10, -121.05), T0 + 30))
    clock.t = T0 + 60
    d.run_cycle()
    if deploying:
        tracker.ingest(CrewBeacon("G1", ss.NEAR_LOC, T0 + 1800,
                                  CrewStatus.DEPLOYING, client_id="c1"))
        clock.t = T0 + 1860
        d.run_cycle()
    return d.state()


def test_the_live_plan_renders_as_an_itinerary(tmp_path):
    st = _live_state(tmp_path / "st")
    assert "routes" not in st                     # the live state, as is
    (stop,) = st["plan"].plans["G1"].stops
    dom = _render(st, tmp_path / "prof")
    pane = dom.ids["itinlist"]
    assert "no active itineraries" not in pane.text()
    (block,) = pane.find("crew-block")
    assert block.find("cid")[0].text() == "G1"
    assert "→ egress Z1" in block.text()
    (row,) = block.find("stop")
    assert row.text().startswith("c1")
    assert f"margin {stop.margin_h:.2f} h" in row.text()
    assert pane.find("watch-block") == []         # the watch is not armed


def test_a_held_home_and_the_crew_watch_render(tmp_path):
    st = _live_state(tmp_path / "st", deploying=True, crew_watch=True,
                     crew_hold_worked=True)
    dom = _render(st, tmp_path / "prof")
    pane = dom.ids["itinlist"]
    (block,) = pane.find("crew-block")
    assert block.find("cid")[0].text() == "G1" and "working" in block.text()
    (held,) = block.find("held")
    assert "c1" in held.text() and "in progress (held)" in held.text()
    (watch,) = pane.find("watch-row")
    assert watch.attrs["data-level"] == st["crew_watch"]["crews"][0]["level"]
    assert "G1" in watch.text() and "deploying at c1" in watch.text()
