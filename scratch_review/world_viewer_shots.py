"""scratch_review/world_viewer_shots.py — screenshots of the W6 thin world
viewer (console/world.html at /world) over a LIVE watch daemon, for
docs/img/world_viewer/ (docs/LIVE_WORLD_PLAN.md "W6 thin viewer").

Drives the REAL page in headless Chrome over the DevTools protocol (the
minimal stdlib websocket client of scratch_review/roads_inspection_shots.py,
no third-party packages) against a daemon started separately, e.g.

    set WGRESP_DEV_OPEN=1
    py -3 -m wgresponse.cli watch --aoi data/aoi_conus.json \
        --registry mock-presets --dry-notify ... --port 18977

and writes docs/img/world_viewer/w6_*.jpg: national (the CONUS fit);
regional (the domain box of the domain reaching the most homes, a
perimeter fire first) and regional_ranked1 (the top of the ranked list,
when different); regional_malibu (the Malibu arc at zoom 9, when domains
sit there); local (the domain incident nearest an enrolled home, framed on
the pair so the W1 note and the client cards share the frame); a "why no
domain?" hover on the largest no-domain incident; and the national tier in
the offline SVG shim (?nomap=1). Each frame waits for the page's own data
(SUMMARY loaded) and for the basemap tiles to settle.

Usage:
    py -3 scratch_review/world_viewer_shots.py --base http://127.0.0.1:18977
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
import tempfile
import time
import urllib.request

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(_REPO, "docs", "img", "world_viewer")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEVTOOLS_PORT = 9357
W, H = 1600, 1000


class CDP:
    """Minimal DevTools-protocol client over a hand-rolled RFC 6455 socket
    (same as scratch_review/roads_inspection_shots.py)."""

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

    def js(self, expr: str, timeout_s: float = 60.0):
        res = self.call("Runtime.evaluate", expression=expr, awaitPromise=True,
                        returnByValue=True, timeout=int(timeout_s * 1000))
        if res.get("exceptionDetails"):
            raise RuntimeError(f"JS error: {res['exceptionDetails']}")
        return res.get("result", {}).get("value")

    def shot(self, path: str) -> None:
        data = self.call("Page.captureScreenshot", format="jpeg",
                         quality=88)["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))


def launch(profile_dir: str):
    proc = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={DEVTOOLS_PORT}",
         f"--user-data-dir={profile_dir}", f"--window-size={W},{H}",
         "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
         "--disable-extensions", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    for _ in range(100):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{DEVTOOLS_PORT}/json/list", timeout=2) as r:
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
    cdp.call("Emulation.setDeviceMetricsOverride", width=W, height=H,
             deviceScaleFactor=1, mobile=False)
    cdp.call("Page.enable")
    return proc, cdp


def load(cdp: CDP, url: str, timeout_s: float = 90.0) -> None:
    cdp.call("Page.navigate", url=url)
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            ok = cdp.js("document.readyState === 'complete' && typeof map === "
                        "'object' && !!SUMMARY && Array.isArray(SUMMARY.incidents)"
                        " && !!STATE")
        except RuntimeError:
            ok = False
        if ok:
            return
        time.sleep(0.4)
    raise RuntimeError(f"world page did not load its data: {url}")


def settle(cdp: CDP, timeout_s: float = 15.0) -> None:
    """Let the page's 60 ms render throttle run and the tiles finish."""
    time.sleep(0.8)
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        done = cdp.js("(() => { const t = [...document.querySelectorAll("
                      "'.leaflet-tile')]; return t.length === 0 || "
                      "t.every(i => i.complete); })()")
        if done:
            break
        time.sleep(0.3)
    time.sleep(0.6)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:18977")
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="wg_world_shots_")
    proc, cdp = launch(profile)
    report = {}
    try:
        url = args.base.rstrip("/") + "/world"
        load(cdp, url)
        settle(cdp)
        info = cdp.js("""(() => {
          const rows = SUMMARY.incidents;
          const dom = rows.filter(r => r.domain);
          // regional frames: the domain reaching the most homes (a perimeter
          // fire first), and the top of the ranked list (earliest P10)
          const byHomes = dom.slice().sort((a, b) =>
            (!!b.perimeter - !!a.perimeter) || (b.domain.clients - a.domain.clients));
          const pick = byHomes[0] || null;
          const top = dom[0] || null;
          const nod = rows.filter(r => !r.domain && r.acreage != null)
                          .sort((a, b) => b.acreage - a.acreage)[0] || null;
          return { n: rows.length, domains: dom.length, zoom: map.getZoom(),
                   tier: tierFor(map.getZoom()), pick, top, nod };
        })()""")
        report["national"] = {k: info[k] for k in ("n", "domains", "zoom", "tier")}
        cdp.shot(os.path.join(args.out, "w6_national.jpg"))

        def regional(row, name):
            s, w, n, e = row["domain"]["bbox"]
            cdp.js(f"map.fitBounds([[{s},{w}],[{n},{e}]], "
                   f"{{maxZoom: 11, animate: false}}); true")
            settle(cdp)
            report[name] = {"incident": row["name"],
                            "homes": row["domain"]["clients"],
                            "zoom": cdp.js("map.getZoom()"),
                            "tier": cdp.js("tierFor(map.getZoom())")}
            cdp.shot(os.path.join(args.out, f"w6_{name}.jpg"))

        pick = info["pick"]
        if pick:
            regional(pick, "regional")
            top = info["top"]
            if top and top["incident_id"] != pick["incident_id"]:
                regional(top, "regional_ranked1")
            # local: the domain incident with the enrolled home nearest to it,
            # framed on the pair's midpoint — the W1 note (where the shaded
            # fire will be drawn) and the client cards in one frame; zoom 13
            # when they are within ~8 km, else 12 (the local tier's floor)
            target = cdp.js("""(() => {
              const cl = (STATE.clients || []).filter(x => x.lat != null);
              let best = null;
              for (const r of SUMMARY.incidents) {
                if (!r.domain || r.lat == null) continue;
                const k = Math.cos(r.lat * Math.PI / 180);
                for (const c of cl) {
                  const d = Math.hypot(c.lat - r.lat, (c.lon - r.lon) * k) * 111.32;
                  if (!best || d < best.d) best = { d, r, c };
                }
              }
              if (!best) return null;
              return { lat: (best.r.lat + best.c.lat) / 2,
                       lon: (best.r.lon + best.c.lon) / 2,
                       zoom: best.d < 8 ? 13 : 12, km: best.d,
                       incident: best.r.name, client: best.c.client_id };
            })()""")
            if target is None:
                target = {"lat": pick["lat"], "lon": pick["lon"], "zoom": 12}
            cdp.js(f"map.setView([{target['lat']}, {target['lon']}], "
                   f"{target['zoom']}, {{animate: false}}); true")
            settle(cdp)
            report["local"] = {"center": target, "zoom": cdp.js("map.getZoom()"),
                               "tier": cdp.js("tierFor(map.getZoom())"),
                               "cards": cdp.js("document.querySelectorAll("
                                               "'#cardlist .card').length")}
            cdp.shot(os.path.join(args.out, "w6_local.jpg"))

        # a second regional frame: the Malibu arc (the densest domain cluster
        # on 2026-09-26) — skipped when no domain sits within ~80 km of it
        mal = cdp.js("""(() => {
          const c = [34.055, -118.72];
          const near = SUMMARY.incidents.filter(r => r.domain && r.lat != null
            && Math.abs(r.lat - c[0]) < 0.8 && Math.abs(r.lon - c[1]) < 0.9);
          return near.length;
        })()""")
        if mal:
            cdp.js("map.setView([34.10, -118.55], 9, {animate: false}); true")
            settle(cdp)
            report["regional_malibu"] = {"domains_near": mal,
                                         "zoom": cdp.js("map.getZoom()")}
            cdp.shot(os.path.join(args.out, "w6_regional_malibu.jpg"))

        nod = info["nod"]
        if nod:
            # "why no domain?" — open the largest no-domain incident's tooltip
            cdp.js(f"map.setView([{nod['lat']}, {nod['lon']}], 6, {{animate: false}}); true")
            settle(cdp)
            opened = cdp.js(f"""(() => {{
              let hit = null;
              layers.incidents.eachLayer(l => {{
                const ll = l.getLatLng && l.getLatLng();
                if (ll && Math.abs(ll.lat - {nod['lat']}) < 1e-6
                    && Math.abs(ll.lng - {nod['lon']}) < 1e-6) hit = l;
              }});
              if (hit) hit.openTooltip();
              return !!hit;
            }})()""")
            time.sleep(0.5)
            report["why_no_domain"] = {"incident": nod["name"], "opened": opened,
                                       "text": nod.get("why_no_domain")}
            cdp.shot(os.path.join(args.out, "w6_why_no_domain.jpg"))

        load(cdp, url + "?nomap=1")
        settle(cdp)
        report["offline"] = {"fallback": cdp.js("!!L._fallback"),
                             "zoom": cdp.js("map.getZoom()"),
                             "tier": cdp.js("tierFor(map.getZoom())")}
        cdp.shot(os.path.join(args.out, "w6_national_offline.jpg"))
    finally:
        try:
            cdp.call("Browser.close")
        except Exception:
            pass
        proc.wait(timeout=10)
        shutil.rmtree(profile, ignore_errors=True)
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
