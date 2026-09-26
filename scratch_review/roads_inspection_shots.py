"""scratch_review/roads_inspection_shots.py — PNG screenshots of the public
simulator page at the moments crews visibly leave the roads (roads
inspection 2026-09-25, docs/review/roads_inspection_2026-09-25.md).

Drives the REAL page (console/simulator.html served by
`py -3 -m wgresponse.cli simui --host 127.0.0.1 --port 8877`, started
separately) in headless Chrome over the DevTools protocol — a minimal
stdlib websocket client, no third-party packages — and writes
docs/img/roads_inspection/<name>.jpg.

Every shot builds its OWN session through the page's own api()/render()
path with an explicit seed and the probe's parameters (40 clients, 6 in-zone
+ 1 h + 3 h reinforcements, HIGH fire at the probe's exact ignition point),
so each frame is the deterministic tick-k picture of the matching
scratch_review/roads_offroad_probe.py run. Ticks are stepped one at a time
(POST ctl step) at a steady wall cadence so the page's own marker tween runs
exactly as it does live; a frame can be taken mid-tween (`mid`).

Inspection overlay (`overlay`): draws, in magenta dots, the polyline the
page's marker tween is sliding each crew along at that instant (the page's
own anim[cid].pts) and, in green, the road path the crew actually drove that
tick (probe JSON, when given) — a still image cannot show an animation, so
the overlay makes the tween path visible. It is labelled in the frame.

Usage:
    py -3 scratch_review/roads_inspection_shots.py --probe scratch_review/roads_offroad_probe_before.json --shots scratch_review/roads_inspection_shots_before.json
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(_REPO, "docs", "img", "roads_inspection")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BASE = "http://127.0.0.1:8877"
PORT = 9339


class CDP:
    """Minimal DevTools-protocol client over a hand-rolled RFC 6455 socket."""

    def __init__(self, ws_url: str):
        rest = ws_url[len("ws://"):]
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=120)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\n"
                           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\n"
                           "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        head, _, self.buf = buf.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise RuntimeError(f"websocket upgrade failed: {head[:200]!r}")
        self.next_id = 0

    def _exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                raise ConnectionError("devtools socket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _frame(self):
        b1, b2 = self._exact(2)
        ln = b2 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self._exact(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self._exact(8))[0]
        mask = self._exact(4) if b2 & 0x80 else None
        data = self._exact(ln)
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return bool(b1 & 0x80), b1 & 0x0F, data

    def _send(self, op: int, payload: bytes) -> None:
        n = len(payload)
        hdr = bytes([0x80 | op])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(hdr + mask + body)

    def _message(self) -> dict:
        parts = []
        while True:
            fin, op, data = self._frame()
            if op == 0x9:
                self._send(0xA, data)
                continue
            if op == 0x8:
                raise ConnectionError("devtools closed the socket")
            parts.append(data)
            if fin:
                return json.loads(b"".join(parts).decode("utf-8"))

    def call(self, method: str, **params):
        self.next_id += 1
        mid = self.next_id
        self._send(0x1, json.dumps({"id": mid, "method": method,
                                    "params": params}).encode("utf-8"))
        while True:
            msg = self._message()
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr: str, timeout_s: float = 120.0):
        res = self.call("Runtime.evaluate", expression=expr, awaitPromise=True,
                        returnByValue=True, timeout=int(timeout_s * 1000))
        if res.get("exceptionDetails"):
            raise RuntimeError(f"JS error: {res['exceptionDetails']}")
        return res.get("result", {}).get("value")

    def shot(self, path: str) -> None:
        """JPEG q88 (a 1600x1000 map frame ~0.3 MB vs ~0.7 MB as PNG) unless
        the path ends in .png."""
        if path.lower().endswith(".png"):
            data = self.call("Page.captureScreenshot", format="png")["data"]
        else:
            data = self.call("Page.captureScreenshot", format="jpeg",
                             quality=88)["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))


def launch(profile_dir: str):
    if os.path.isdir(profile_dir):
        shutil.rmtree(profile_dir, ignore_errors=True)
    proc = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
         f"--user-data-dir={profile_dir}", "--window-size=1600,1000",
         "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
         "--disable-extensions", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list",
                                        timeout=2) as r:
                tabs = json.loads(r.read().decode())
            pages = [t for t in tabs if t.get("type") == "page"]
            if pages:
                ws = pages[0]["webSocketDebuggerUrl"]
                break
        except OSError:
            pass
        time.sleep(0.2)
    if ws is None:
        proc.kill()
        raise RuntimeError("headless chrome did not expose a page target")
    cdp = CDP(ws)
    cdp.call("Emulation.setDeviceMetricsOverride", width=1600, height=1000,
             deviceScaleFactor=1, mobile=False)
    return proc, cdp


# JS helpers injected into the page once per load ---------------------------
HELPERS = r"""
window.__insp = {
  sleep: ms => new Promise(r => setTimeout(r, ms)),
  async newSess(body) {
    const out = await api("POST", "/api/sim/new", body);
    sid = out.session_id;
    // mirror the session in the left-panel controls (no change events fire)
    const st = out.state;
    try {
      $("loc").value = st.location;
      $("firezone").value = st.fire_zone || "standard";
      $("clients").value = st.clients.length;
      $("clientsval").textContent = st.clients.length;
      const reg = r => st.crews.filter(c => (c.region || "in_zone") === r).length;
      $("crews").value = reg("in_zone");
      $("crews_1h").value = reg("reg_1h");
      $("crews_3h").value = reg("reg_3h");
    } catch (e) { /* cosmetic only */ }
    render(out.state); return out.state;
  },
  async ignite(lat, lon, intensity, wd) {
    const out = await api("POST", `/api/sim/${sid}/ignite`,
                          { lat, lon, intensity, wind_dir_deg: wd });
    render(out.state); return out.incident_id;
  },
  async step() {
    const out = await api("POST", `/api/sim/${sid}/ctl`, { action: "step" });
    render(out.state); return out.state.tick;
  },
  layer: null,
  clear() { if (this.layer) this.layer.clearLayers(); },
  ensure() { if (!this.layer) this.layer = L.layerGroup().addTo(map); return this.layer; },
  drawTweens(only) {
    const g = this.ensure(); let n = 0;
    for (const cid in anim) {
      if (only && only.indexOf(cid) === -1) continue;
      const a = anim[cid]; if (!a || !a.pts || a.pts.length < 2) continue;
      L.polyline(a.pts, { color: "#ff2bd6", weight: 5, opacity: 1,
                          dashArray: "1 9", lineCap: "round" }).addTo(g); n++;
    }
    return n;
  },
  drawPath(pts, color, label) {
    const g = this.ensure();
    L.polyline(pts, { color, weight: 4, opacity: 0.9 }).addTo(g);
    if (label) L.marker(pts[pts.length - 1], { icon: L.divIcon({ className: "",
      html: `<div style="background:${color};color:#000;font:bold 11px sans-serif;`
          + `padding:1px 4px;border-radius:3px;white-space:nowrap">${label}</div>`,
      iconSize: null }) }).addTo(g);
  },
  caption(text) {
    let el = document.getElementById("__insp_cap");
    if (!el) { el = document.createElement("div"); el.id = "__insp_cap";
      el.style.cssText = "position:fixed;z-index:3000;left:770px;bottom:26px;"
        + "transform:translateX(-50%);background:#101820ee;color:#ffe;"
        + "font:600 13px sans-serif;padding:6px 12px;border:1px solid #ff2bd6;"
        + "border-radius:6px;max-width:900px;text-align:center";
      document.body.appendChild(el); }
    el.textContent = text; el.style.display = text ? "block" : "none";
  },
  tilesIdle() {
    const imgs = [...document.querySelectorAll(".leaflet-tile")];
    return imgs.length > 0 && imgs.every(i => i.complete);
  },
};
true
"""


def wait_ready(cdp: CDP, timeout_s: float = 30.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        ok = cdp.js("document.readyState === 'complete' && typeof api === "
                    "'function' && typeof render === 'function' && "
                    "typeof map === 'object' && !!sid")
        if ok:
            return
        time.sleep(0.3)
    raise RuntimeError("simulator page did not become ready")


def wait_tiles(cdp: CDP, timeout_s: float = 12.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if cdp.js("window.__insp.tilesIdle()"):
            return
        time.sleep(0.25)


def view_for(points, width_px: float = 900.0, height_px: float = 820.0,
             max_zoom: int = 15, min_zoom: int = 9):
    """[lat, lon, zoom] framing `points` (list of [lat, lon]) in the page's
    ~1000 px map column, web-mercator metres-per-pixel law."""
    import math
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    lat_c = (min(lats) + max(lats)) / 2.0
    lon_c = (min(lons) + max(lons)) / 2.0
    k = math.cos(math.radians(lat_c))
    ext_x = max(300.0, (max(lons) - min(lons)) * 111320.0 * k)
    ext_y = max(300.0, (max(lats) - min(lats)) * 111320.0)
    mpp = max(ext_x / width_px, ext_y / height_px)
    z = int(math.floor(math.log2(156543.03 * k / mpp)))
    return [lat_c, lon_c, max(min_zoom, min(max_zoom, z))]


def run_shot(cdp: CDP, spec: dict, probe_runs: dict) -> None:
    """spec: name, key, zone, tick, view [lat, lon, zoom] (or `frame`: a move
    {tick, crew} whose from/to/driven path is framed), mid (0..1 of the
    tween, or None = at rest), overlay (bool), crews (list | None), caption,
    driven (list of {crew, tick} whose driven path to draw from the probe),
    scenario (a server one-click scenario instead of the probe session)."""
    run = probe_runs.get((spec["key"], spec["zone"]))
    params = run["_params"] if run else None
    cdp.call("Page.navigate", url=f"{BASE}/simulator")
    time.sleep(1.0)
    wait_ready(cdp)
    cdp.js(HELPERS)
    if spec.get("scenario"):
        body = {"scenario": spec["scenario"], "fire_zone": spec["zone"],
                "spotting": True, "grass_realism": True}
        cdp.js(f"window.__insp.newSess({json.dumps(body)}).then(s => s.tick)")
    else:
        body = {"location": spec["key"], "clients": params["clients"],
                "crews": params["crews"], "crews_1h": params["crews_1h"],
                "crews_3h": params["crews_3h"], "fire_zone": spec["zone"],
                "spotting": True, "grass_realism": True, "seed": params["seed"]}
        extra = dict(params.get("extra_cfg") or {}, **(spec.get("cfg") or {}))
        if extra:
            body["cfg"] = extra          # the probe's A/B knob (server cfg)
        cdp.js(f"window.__insp.newSess({json.dumps(body)}).then(s => s.tick)")
        ign = run["ignition"]
        cdp.js(f"window.__insp.ignite({ign['lat']}, {ign['lon']}, "
               f"{json.dumps(params['intensity'])}, {run['_wind']})")
    if spec.get("view"):
        lat, lon, zoom = spec["view"]
    else:
        fr = spec["frame"]
        pts = []
        for m in run["moves"]:
            if m["crew"] == fr["crew"] and m["tick"] == fr["tick"]:
                pts = [m["from"], m["to"]] + (m.get("path") or [])
        for extra in spec.get("frame_extra", []):
            pts.append(extra)
        if not pts:
            raise RuntimeError(f"{spec['name']}: no probe move {fr}")
        lat, lon, zoom = view_for(pts)
        zoom += int(spec.get("zoom_delta", 0))
    cdp.js(f"map.setView([{lat}, {lon}], {zoom}); true")
    cadence_ms = 1400
    for _k in range(spec["tick"] - 1):
        cdp.js(f"window.__insp.step().then(t => window.__insp.sleep({cadence_ms}).then(() => t))")
    wait_tiles(cdp)
    cdp.js(f"window.__insp.sleep({cadence_ms}).then(() => true)")
    tick = cdp.js("window.__insp.step()")
    mid = spec.get("mid")
    if mid is not None:
        # tickGapMs is what the page measured between the last two renders
        gap = cdp.js("tickGapMs") or cadence_ms
        time.sleep(max(0.0, mid * gap / 1000.0 - 0.05))
    # the driven road path first (green, underneath), then the tween paths
    # (magenta dots on top): where they coincide the dots sit ON the green
    for d in spec.get("driven", []):
        mv = [m for m in run["moves"] if m["crew"] == d["crew"] and m["tick"] == d["tick"]]
        if mv and mv[0].get("path"):
            cdp.js(f"window.__insp.drawPath({json.dumps(mv[0]['path'])}, '#39ff14', "
                   f"{json.dumps(d.get('label', 'driven road path'))}); true")
    if spec.get("overlay"):
        cdp.js(f"window.__insp.drawTweens({json.dumps(spec.get('crews'))})")
    if spec.get("graph_bbox") and run and run.get("graph_bbox"):
        s_, w_, n_, e_ = run["graph_bbox"]
        cdp.js("(() => { const g = window.__insp.ensure(); "
               f"L.rectangle([[{s_}, {w_}], [{n_}, {e_}]], {{color: '#00e5ff', "
               "weight: 2.5, dashArray: '10 6', fill: false}).addTo(g); "
               f"L.marker([{n_}, {w_}], {{icon: L.divIcon({{className: '', "
               "html: '<div style=\"background:#00e5ff;color:#000;font:bold 12px "
               "sans-serif;padding:1px 5px;border-radius:3px;white-space:nowrap\">"
               f"{spec.get('graph_label', 'road graph coverage')}</div>', "
               "iconSize: null})}).addTo(g); return true; })()")
    tip = spec.get("tooltip")
    if tip:
        # open the first drawn segment (or crew marker) tooltip whose text
        # matches — the "hover for why" reason, frozen into the frame
        opened = cdp.js(
            "(() => { const re = new RegExp(" + json.dumps(tip["match"]) + ");"
            " const want = " + json.dumps(tip.get("crew")) + ";"
            " for (const cid in crewReg) { if (want && cid !== want) continue;"
            "  const R = crewReg[cid];"
            "  if (" + json.dumps(tip.get("marker", False)) + ") {"
            "   const t = R.marker.getTooltip && R.marker.getTooltip();"
            "   if (t && re.test(t.getContent())) { R.marker.openTooltip(); return 'marker ' + cid; } continue; }"
            "  const pools = [].concat(R.routeSegs || [], R.egressSegs || [], R.queueSegs || []);"
            "  for (const lay of pools) { const ll = lay.getLatLngs ? lay.getLatLngs() : [];"
            "   const t = lay.getTooltip && lay.getTooltip();"
            "   if (!ll.length || !t || !re.test(t.getContent())) continue;"
            "   const a = ll[0], b = ll[ll.length - 1];"
            "   lay.openTooltip(L.latLng((a.lat + b.lat) / 2, (a.lng + b.lng) / 2));"
            "   return 'segment ' + cid; } }"
            " return null; })()")
        print(f"  tooltip: {opened}", flush=True)
    cdp.js(f"window.__insp.caption({json.dumps(spec.get('caption', ''))}); true")
    if mid is None:
        wait_tiles(cdp)
    path = os.path.join(OUT_DIR, spec["name"] + ".jpg")
    cdp.shot(path)
    print(f"  wrote {path} (tick {tick})", flush=True)


def load_probe(path: str) -> dict:
    if _REPO not in sys.path:
        sys.path.insert(0, _REPO)
    from wgresponse.simui import LOCATIONS
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    out = {}
    for r in d["runs"]:
        r["_params"] = d["params"]
        # the probe ignites with wind FROM the location's hazard bearing
        r["_wind"] = LOCATIONS[r["key"]].hazard_wind_dir_deg
        out[(r["key"], r["zone_req"])] = r
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", required=True)
    ap.add_argument("--shots", required=True, help="JSON file: list of shot specs")
    ap.add_argument("--only", action="append")
    args = ap.parse_args(argv)
    os.makedirs(OUT_DIR, exist_ok=True)
    runs = load_probe(args.probe)
    with open(args.shots, encoding="utf-8") as f:
        shots = json.load(f)
    prof = os.path.join(os.environ.get("TEMP", "."), "wg_roads_cdp_profile")
    proc, cdp = launch(prof)
    try:
        for spec in shots:
            if args.only and spec["name"] not in args.only:
                continue
            print(f"[{spec['name']}] {spec['key']} {spec['zone']} tick {spec['tick']}",
                  flush=True)
            run_shot(cdp, spec, runs)
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
