"""attack_r3_common.py — shared harness for the FOUNDER ROUND 3 adversarial
verification (roads adherence, time-scrub byte-identity, full utilization,
homes-first triage, gel rings).

INDEPENDENCE DISCIPLINE (same as attack_sim_common): everything used to
*verify* an engine claim is recomputed here from first principles. In
particular the point-to-polyline geometry (roads adherence) is an OWN local
ENU projection + point/segment distance, never a call into wgresponse.geo's
own path math; the byte-identity comparison is a raw json.dumps digest with
only the DOCUMENTED live-only server keys stripped.

Engine calls here are SCENARIO SETUP ONLY (build a SimSession, drive commands,
start an ephemeral-port server) — never verification.

Runs from repo root:  py -3 -m pytest scratch_review/attack_r3_roads.py -q
The user's live server on :8878 is NEVER touched — every HTTP harness binds
port 0 (ephemeral) on 127.0.0.1.
"""
from __future__ import annotations

import http.client
import json
import math
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TILE_DIR = os.path.join(REPO, "data", "tiles")
ROAD_DIR = os.path.join(REPO, "data", "roads")

REAL_TILES = ("malibu_ca", "paradise_ca", "santa_rosa_ca",
              "boulder_co", "park_city_ut")

# The documented live-only server keys (_state_payload adds these on top of the
# pure SimSession.state(); the scrub snapshot deliberately omits them).
LIVE_ONLY_KEYS = ("session_id", "running", "speed_x", "tick_interval_s",
                  "safety_surface")


def tile_present(key: str) -> bool:
    return os.path.exists(os.path.join(TILE_DIR, f"{key}.wgt"))


def road_present(key: str) -> bool:
    return os.path.exists(os.path.join(ROAD_DIR, f"{key}.wgr"))


def skip_loud(key: str, what: str) -> str:
    return (f"SKIP-LOUDLY: {what} for {key} missing on disk — "
            f"refetch and re-run (verifier brief demands a loud skip, "
            f"never a silent pass)")


# --------------------------------------------------------------------------
# Independent geometry: local ENU projection + point-to-polyline distance (m)
# --------------------------------------------------------------------------
_LATM = 111_320.0                              # meters per degree latitude


def enu(ref, lat, lon):
    """(east_m, north_m) of (lat,lon) relative to ref=(lat0,lon0)."""
    lat0, lon0 = ref
    lonm = _LATM * math.cos(math.radians(lat0))
    return ((lon - lon0) * lonm, (lat - lat0) * _LATM)


def pt_seg_dist_m(p, a, b):
    """Distance (m) from point p=[lat,lon] to segment a->b (both [lat,lon]),
    computed in a local ENU frame anchored at p (exact at these scales)."""
    ref = (p[0], p[1])
    px, py = 0.0, 0.0                          # p is the ENU origin
    ax, ay = enu(ref, a[0], a[1])
    bx, by = enu(ref, b[0], b[1])
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 <= 1e-18:
        return math.hypot(ax, ay)
    t = -(ax * dx + ay * dy) / seg2            # projection of p onto a->b
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(cx - px, cy - py)


def pt_polyline_dist_m(p, poly):
    """Min distance (m) from p=[lat,lon] to a polyline (list of [lat,lon]).
    A single-vertex polyline degenerates to the point distance."""
    if not poly:
        return float("inf")
    if len(poly) == 1:
        return math.hypot(*enu((p[0], p[1]), poly[0][0], poly[0][1]))
    return min(pt_seg_dist_m(p, poly[i], poly[i + 1])
               for i in range(len(poly) - 1))


def polyline_len_m(poly):
    d = 0.0
    for i in range(len(poly) - 1):
        d += math.hypot(*enu((poly[i][0], poly[i][1]),
                             poly[i + 1][0], poly[i + 1][1]))
    return d


# --------------------------------------------------------------------------
# Segment / spur-metadata validators (STATE payload shape)
# --------------------------------------------------------------------------
def segments_reconstruct(polyline, segments):
    """True iff the ordered same-kind segment cover reconstructs `polyline`
    EXACTLY (seg[0].points + each later seg.points[1:] == polyline, shared
    boundary vertices)."""
    if not segments:
        return len(polyline) < 2
    flat = [list(segments[0]["points"][0])]
    for seg in segments:
        pts = seg["points"]
        # each segment's first point must equal the running last point
        if [round(flat[-1][0], 9), round(flat[-1][1], 9)] != \
           [round(pts[0][0], 9), round(pts[0][1], 9)]:
            return False
        for q in pts[1:]:
            flat.append(list(q))
    a = [[round(x, 9), round(y, 9)] for x, y in polyline]
    b = [[round(x, 9), round(y, 9)] for x, y in flat]
    return a == b


def spur_mask_consistent(polyline, spur_mask, segments):
    """spur_mask[i] must equal (segment covering edge i is not road)."""
    if spur_mask is None:
        return segments is None
    if len(spur_mask) != max(0, len(polyline) - 1):
        return False
    expect = []
    for seg in segments:
        edges = len(seg["points"]) - 1
        expect.extend([seg["kind"] != "road"] * edges)
    return expect == list(spur_mask)


# --------------------------------------------------------------------------
# Byte-identity digest (raw json, live-only keys stripped)
# --------------------------------------------------------------------------
def canon(state, drop=()):
    """Deterministic json string of `state` with live-only server keys (and any
    extra `drop` keys) removed. allow_nan=False => any inf/nan leak raises."""
    d = {k: v for k, v in state.items()
         if k not in LIVE_ONLY_KEYS and k not in drop}
    return json.dumps(d, sort_keys=True, allow_nan=False)


# --------------------------------------------------------------------------
# Ephemeral in-process HTTP server (REAL tiles + REAL road graphs)
# --------------------------------------------------------------------------
class SimServer:
    """A wgresponse.server ThreadingHTTPServer on an ephemeral port, wired to
    the REAL committed tiles and (unless pinned) the REAL cached road graphs.
    Never binds the user's :8878."""

    KEY = "attack-r3-key"

    def __init__(self, tile_dir=TILE_DIR, synth_size_km=None):
        from wgresponse import server
        os.environ["WGRESP_API_KEY"] = self.KEY
        os.environ.pop("WGRESP_DEV_OPEN", None)
        self._srv = server.serve(0, sim_tile_dir=tile_dir,
                                 sim_synth_size_km=synth_size_km)
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(
            target=lambda: self._srv.serve_forever(0.05), daemon=True)
        self._thread.start()

    def close(self):
        try:
            self._srv.app.sim_manager.stop_all()
        finally:
            self._srv.shutdown()
            self._thread.join(timeout=5)
            self._srv.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def _req(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            hdrs = {"X-Internal-API-Key": self.KEY}
            payload = None
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                hdrs["Content-Type"] = "application/json"
            conn.request(method, path, body=payload, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read()
            parsed = json.loads(data) if data else None
            return resp.status, parsed
        finally:
            conn.close()

    def get(self, path):
        return self._req("GET", path)

    def post(self, path, body):
        return self._req("POST", path, body)

    def new(self, location, clients, crews, seed="r3", cfg=None,
            fire_zone="standard"):
        body = {"location": location, "clients": clients, "crews": crews,
                "seed": seed, "fire_zone": fire_zone}
        if cfg:
            body["cfg"] = cfg
        return self.post("/api/sim/new", body)

    def step(self, sid):
        return self.post(f"/api/sim/{sid}/ctl", {"action": "step"})

    def state(self, sid, query=""):
        return self.get(f"/api/sim/{sid}/state{query}")


# --------------------------------------------------------------------------
# SimSession scenario setup (direct, no HTTP) — reuses attack_sim_common
# --------------------------------------------------------------------------
def make_session(key, n_clients, n_crews, seed="r3", cfg=None,
                 fire_zone="standard"):
    from wgresponse.simui import SimSession
    return SimSession(key, n_clients, n_crews, seed=seed,
                      cfg_overrides=(cfg or {}), fire_zone=fire_zone,
                      tile_dir=TILE_DIR)


def arm_fire(sess, key, intensity="high", cluster=True):
    """Pick a burnable upwind ignition and light it. Returns incident id."""
    from attack_sim_common import pick_ignition
    p, _target = pick_ignition(sess, key, cluster=cluster)
    return sess.ignite(p.lat, p.lon, intensity=intensity)
