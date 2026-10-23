"""THE PAGE SAYS WHAT THE AUDIT SAW — RENDERED (2026-10-21; the Eagle River
valley review's iteration 4; SAFETY SEMANTICS 33; console/simulator.html).

The independent review of iterations 2-3 rendered the page in a local server
and found, for a TRANSIT row (a crew that drove through the pessimistic
front's band between two tick ends — SEMANTICS 25): the alert feed still said
"caught in fire" and stamped the row with the tick end, not its pass time;
the scoreboard row label, the legend and the red-flashing marker still said
"caught in fire"; scoreboard.overrun_ledger was sent but never rendered — and
the page's tests only grepped its source.

These tests RENDER the real page in a headless Chromium (Chrome or Edge, or
WG_TEST_BROWSER; a LOUD skip when none is installed) against real engine
states — the rug-pull control's transit row (tests/test_overrun_ledger.py) at
its tick and two ticks later — served by a stub of the two /api/sim endpoints
the page reads at boot, the map in its offline shim (?nomap=1; every host
but 127.0.0.1 resolves to nothing), and read the DOM the page built: the
feed entry (its text and its stamp), the banner, the scoreboard row and its
ledger, the crew card, the legend, and the crew marker's classes (an
observer script hands the shim's markers a real element, which the page's
own styleCrewMarker then styles). One crafted contrast: the same state with
the row turned into a tick-end row still reads "caught in fire", red."""
from __future__ import annotations

import copy
import datetime
import http.server
import json
import os
import shutil
import subprocess
import threading
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tests.test_overrun_ledger import LEDGER_ONLY, _rugpull

PAGE = Path(__file__).resolve().parent.parent / "console" / "simulator.html"

#: the observer script, injected right after the page's own script (which
#: boots by fetching the session asynchronously, so this runs before the
#: first render): each shim marker gets a real element — the page's
#: styleCrewMarker styles it exactly as it styles a Leaflet marker's — and
#: once the crew cards exist, every crew marker's classes are written into
#: <pre id="__probe"> for the dump
PROBE = r"""<script>
(function () {
  const els = [];
  const mk = L.marker;
  L.marker = function (latlng, opts) {
    const m = mk(latlng, opts);
    const icon = (opts && opts.icon) || {};
    const el = document.createElement("div");
    el.className = icon.className || "";
    el.innerHTML = icon.html || "";
    m.getElement = () => el;
    els.push(el);
    return m;
  };
  function done() {
    const out = {};
    els.forEach(el => {
      const n = el.querySelector(".cnum");
      if (n && el.classList.contains("crew-icon")) out["sim-" + n.textContent] = el.className;
    });
    const pre = document.createElement("pre");
    pre.id = "__probe";
    pre.textContent = JSON.stringify(out);
    document.body.appendChild(pre);
  }
  function wait() {
    const il = document.getElementById("itinlist");
    if (il && il.innerHTML.indexOf("crew-block") !== -1) setTimeout(done, 50);
    else setTimeout(wait, 50);
  }
  setTimeout(wait, 50);
})();
</script>"""

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}


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

    def find(self, cls=None, tag=None):
        return [e for e in self.walk()
                if (cls is None or cls in e.classes)
                and (tag is None or e.tag == tag)]


class _Dom(HTMLParser):
    """The dumped DOM as a small tree (ids indexed)."""

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

    def __getitem__(self, el_id):
        return self.ids[el_id]


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
    """The page and the two endpoints it reads at boot (POST /api/sim/new,
    GET /api/sim/<id>/state) — serving one recorded engine state."""
    page = b""
    state: dict = {}

    def log_message(self, *_a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/simulator.html"):
            return self._send(200, self.page, "text/html; charset=utf-8")
        if self.path.startswith("/api/sim/sim-1/state"):
            return self._send(200, json.dumps(self.state).encode(),
                              "application/json")
        return self._send(404, b'{"error": "not found"}', "application/json")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        if self.path == "/api/sim/new":
            return self._send(200, json.dumps(
                {"session_id": "sim-1", "state": self.state}).encode(),
                "application/json")
        return self._send(404, b'{"error": "not found"}', "application/json")


def _render(state, prof_dir):
    """Render the page on `state`; returns (dom, marker classes by crew)."""
    browser = _browser()
    if browser is None:
        pytest.skip("LOUD SKIP: no headless Chromium (Chrome / Edge) found — "
                    "set WG_TEST_BROWSER to render the simulator page")
    page = PAGE.read_text(encoding="utf-8")
    anchor = "guard(newSession);\n</script>"
    i = page.index(anchor) + len(anchor)
    page = page[:i] + PROBE + page[i:]
    handler = type("H", (_Stub,), {"page": page.encode("utf-8"),
                                   "state": state})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        url = (f"http://127.0.0.1:{srv.server_address[1]}"
               f"/simulator.html?nomap=1")
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run",
               "--no-default-browser-check", "--disable-extensions",
               "--disable-background-networking", "--disable-sync",
               "--mute-audio", f"--user-data-dir={prof_dir}",
               "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
               "--virtual-time-budget=20000", "--dump-dom", url]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
    finally:
        srv.shutdown()
        srv.server_close()
    dom = _Dom(p.stdout)
    assert "__probe" in dom.ids, (p.returncode, p.stderr[-3000:])
    return dom, json.loads(dom["__probe"].text())


def _clock(t_s):
    """The page's fmtClock: 'MM-DD HH:MMZ' (UTC, seconds dropped)."""
    d = datetime.datetime.fromtimestamp(t_s, tz=datetime.timezone.utc)
    return d.strftime("%m-%d %H:%M") + "Z"


@pytest.fixture(scope="module")
def rugpull_states(tmp_path_factory):
    """The rug-pull control (LEDGER_ONLY): sim-g1's transit row at tick 8,
    then two ticks with no row (the ledger keeps it)."""
    states = _rugpull(tmp_path_factory.mktemp("rug"), LEDGER_ONLY, until=10)
    by_tick = {st["tick"]: st for st in states}
    t8, t10 = by_tick[8], by_tick[10]
    (row,) = t8["crew_overruns"]
    assert row["transit"] is True and row["crew_id"] == "sim-g1"
    # the world discriminates the stamp: the pass time is not the tick end
    assert _clock(row["at_s"]) != _clock(t8["sim_now_s"])
    return t8, t10, row


def test_a_transit_row_renders_as_a_drive_through_the_band(rugpull_states,
                                                           tmp_path):
    t8, _t10, row = rugpull_states
    dom, markers = _render(t8, tmp_path / "p8")
    stamp = _clock(row["at_s"])
    hhmm = stamp.split(" ")[1]
    phrase = f"sim-g1 drove through the band at {hhmm}"
    # the alert feed: the row on top, stamped with ITS pass time, said as a
    # drive through the band — nowhere "caught in fire"
    feed = dom["alertlist"]
    first = feed.find("evt")[0]
    assert "transit" in first.classes and "over" not in first.classes
    assert first.find("t")[0].text() == stamp
    assert phrase in first.text()
    assert first.find("chip")[0].text() == "band transit"
    assert "caught in fire" not in feed.text()
    assert _clock(t8["sim_now_s"]) not in first.text()
    # the banner names the drive
    banner = dom["crewoverrunbanner"]
    assert "display: block" in banner.attrs.get("style", "")
    assert phrase in banner.text()
    assert "caught in fire" not in banner.text()
    # the scoreboard: a row label that is not "caught in fire", the run's
    # count, and the ledger rendered under it
    label = dom["sb_overruns_row"].find("k")[0].text()
    assert label == "crew overruns (in the fire / through the band)"
    assert dom["sb_overruns"].text() == (
        "1 now · 1 this run (1 drove through the band)")
    ledger = dom["sb_ledger"]
    assert "on" in ledger.classes
    (lrow,) = ledger.find("lrow")
    assert "transit" in lrow.classes
    assert lrow.text().startswith(f"tick 8 · {phrase}")
    # the crew card and its chip: transit, not caught
    (card,) = [b for b in dom["itinlist"].find("crew-block")
               if b.find("cid")[0].text() == "sim-g1"]
    assert "transit" in card.classes and "caught" not in card.classes
    chip = [c for c in card.find("chip") if "transit" in c.classes]
    assert chip and "DROVE THROUGH THE BAND at " + hhmm in chip[0].text()
    # the marker: the steady transit glow, never the red "caught" flash
    cls = set(markers["sim-g1"].split())
    assert "transit" in cls and "overrun" not in cls
    # the legend says what each marker means
    legend = dom["legend"].text()
    assert "crew OVERRUN — caught in fire" in legend
    assert ("crew drove through the band between ticks (a transit overrun — "
            "not in the fire now)") in legend


def test_the_ledger_keeps_the_row_on_the_page_after_its_tick(rugpull_states,
                                                             tmp_path):
    t8, t10, row = rugpull_states
    assert t10["crew_overruns"] == []
    dom, markers = _render(t10, tmp_path / "p10")
    stamp = _clock(row["at_s"])
    first = dom["alertlist"].find("evt")[0]
    assert "transit" in first.classes
    assert first.find("t")[0].text() == stamp
    assert "sim-g1 drove through the band at " + stamp.split(" ")[1] in (
        first.text())
    assert "tick 8" in first.text()
    # no row this tick: no banner, no marker glow — and never "SAFE"
    assert "display: none" in dom["crewoverrunbanner"].attrs.get("style", "")
    assert dom["sb_violations"].text() == "0 now — 1 this run"
    assert "past" in dom["sb_violations"].classes
    assert not ({"transit", "overrun"} & set(markers["sim-g1"].split()))
    (lrow,) = dom["sb_ledger"].find("lrow")
    assert lrow.text().startswith("tick 8 · sim-g1 drove through the band")


def test_a_tick_end_row_still_reads_caught_in_fire(rugpull_states, tmp_path):
    """Contrast (crafted from the tick-8 state): the same row as a TICK-END
    row — a crew caught where it stands — keeps the red alarm."""
    t8, _t10, _row = rugpull_states
    st = copy.deepcopy(t8)
    st["crew_overruns"][0]["transit"] = False
    for r in st["scoreboard"]["overrun_ledger"]:
        r["transit"] = False
    st["scoreboard"]["transit_overruns_total"] = 0
    dom, markers = _render(st, tmp_path / "pc")
    first = dom["alertlist"].find("evt")[0]
    assert "over" in first.classes and "transit" not in first.classes
    assert first.find("chip")[0].text() == "crew overrun"
    assert "sim-g1 caught in fire" in first.text()
    assert "1 crew caught in fire (sim-g1)" in dom["crewoverrunbanner"].text()
    (card,) = [b for b in dom["itinlist"].find("crew-block")
               if b.find("cid")[0].text() == "sim-g1"]
    assert "caught" in card.classes
    cls = set(markers["sim-g1"].split())
    assert "overrun" in cls and "transit" not in cls
