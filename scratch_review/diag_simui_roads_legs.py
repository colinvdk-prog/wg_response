"""diag_simui_roads_legs.py — DEFECT 1 dynamic census on a live real-tile run.

Instruments a real SimSession (boulder_co + malibu_ca, 40 clients / 4 crews /
high fire at the pinned gate ignition, 36 ticks @ speed 1.0 = the founder's
300x run) and measures:

  A. LEG CENSUS (engine truth): every _SimCrew._begin_leg — kind
     (ingress/egress), on_roads flag, #polyline pts, chord length; % of legs
     that are straight fallbacks, by kind.
  B. LEG VISIBILITY (what the UI can ever draw): legs that begin and end
     inside the SAME tick() step never appear in any state() poll — the UI
     tween then has no polyline to follow and the marker takes the chord.
     Measured: distribution of leg lifetime in ticks; % of road-following
     polyline length that is never exposed at any tick boundary.
  C. WHAT THE FOUNDER SEES: sample the straight segment between consecutive
     tick-boundary positions of each moving crew every 100 m; distance to the
     nearest road edge; report % of samples > 75 m off-road. Compare with the
     same samples taken along the ACTUAL engine polylines (control).
  D. on_roads honesty: state() route_on_roads/egress_on_roads vs the active
     leg's flag at every tick.

Diagnosis only: monkeypatches _SimCrew._begin_leg in-process to LOG (behavior
unchanged); drives SimSession directly (no server).
"""
import os
import sys
import math
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo, roads
from wgresponse.contracts import LatLon
from wgresponse.simui import SimSession, _SimCrew

SCENARIOS = [
    ("boulder_co", 39.95048, -105.23039),
    ("malibu_ca", 34.08967, -118.67009),
]
N_CLIENTS, N_CREWS, TICKS = 40, 4, 36


# --- nearest-road-edge index -------------------------------------------------

class EdgeIndex:
    CELL = 0.005  # deg (~550 m lat)

    def __init__(self, g):
        self.segs = []           # (lat1, lon1, lat2, lon2)
        self.grid = defaultdict(list)
        for e in g.edges:
            pts = [g.nodes[e.u]] + list(e.geometry) + [g.nodes[e.v]]
            for a, b in zip(pts, pts[1:]):
                i = len(self.segs)
                self.segs.append((a.lat, a.lon, b.lat, b.lon))
                for la, lo in ((a.lat, a.lon), (b.lat, b.lon),
                               ((a.lat + b.lat) / 2, (a.lon + b.lon) / 2)):
                    self.grid[(math.floor(la / self.CELL),
                               math.floor(lo / self.CELL))].append(i)
        for k in self.grid:
            self.grid[k] = sorted(set(self.grid[k]))

    def dist_m(self, p, cap_rings=6):
        """Distance from p to the nearest indexed segment (planar local frame,
        exact enough at <10 km scale)."""
        k = math.cos(math.radians(p.lat))
        px, py = p.lon * k, p.lat
        qr, qc = math.floor(p.lat / self.CELL), math.floor(p.lon / self.CELL)
        best = float("inf")
        for ring in range(cap_rings + 1):
            if best < (ring - 1) * self.CELL * 111000 * min(1.0, k):
                break
            cells = []
            if ring == 0:
                cells = [(qr, qc)]
            else:
                for c in range(qc - ring, qc + ring + 1):
                    cells += [(qr - ring, c), (qr + ring, c)]
                for r in range(qr - ring + 1, qr + ring):
                    cells += [(r, qc - ring), (r, qc + ring)]
            for cell in cells:
                for i in self.grid.get(cell, ()):
                    la1, lo1, la2, lo2 = self.segs[i]
                    ax, ay = lo1 * k, la1
                    bx, by = lo2 * k, la2
                    vx, vy = bx - ax, by - ay
                    L2 = vx * vx + vy * vy
                    t = 0.0 if L2 <= 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / L2))
                    dx, dy = px - (ax + t * vx), py - (ay + t * vy)
                    d = math.hypot(dx, dy) * 111_194.9  # deg->m (IUGG-ish)
                    if d < best:
                        best = d
        return best


def sample_segment(a, b, step_m=100.0):
    d = geo.haversine_m(a, b)
    n = max(1, int(d // step_m))
    out = []
    for i in range(1, n + 1):
        f = i / (n + 1.0)
        out.append(LatLon(a.lat + f * (b.lat - a.lat), a.lon + f * (b.lon - a.lon)))
    return out


def run_location(key, ig_lat, ig_lon):
    print(f"\n================ {key} : {N_CLIENTS} clients / {N_CREWS} crews / "
          f"high fire / {TICKS} ticks (900 s each) ================")
    g = roads.load_road_graph(key)
    idx = EdgeIndex(g)

    legs = []          # log records
    tick_now = [0]

    orig_begin = _SimCrew._begin_leg

    def spy_begin(self, kind, target):
        orig_begin(self, kind, target)
        leg = self._leg
        chord = geo.haversine_m(self.pos, target)
        legs.append({
            "crew": self.crew.crew_id, "kind": kind, "tick0": tick_now[0],
            "tick1": None, "on_roads": leg["on_roads"], "npts": len(leg["pts"]),
            "chord_m": chord, "from": self.pos, "to": target,
            "pts": [self.pos] + list(leg["pts"]),
        })
        self._diag_leg_ref = legs[-1]

    _SimCrew._begin_leg = spy_begin
    try:
        sess = SimSession(key, N_CLIENTS, N_CREWS, seed="demo")
        assert sess._road_graph is not None, "roads offline!?"
        sess.ignite(ig_lat, ig_lon, "high")
        move_samples = []      # (crew, tick, pos) at tick boundaries while moving
        flag_rows = []
        for t in range(1, TICKS + 1):
            tick_now[0] = t
            st = sess.tick()
            for c in st["crews"]:
                cs = sess._crew_sims[c["crew_id"]]
                if cs._mode in ("travel", "egress"):
                    move_samples.append((c["crew_id"], t, LatLon(c["lat"], c["lon"])))
                    flag_rows.append((cs._mode,
                                      c["route_on_roads"] if cs._mode == "travel"
                                      else c["egress_on_roads"],
                                      cs._leg["on_roads"] if cs._leg else None))
                # close out legs that finished (leg ref replaced or None)
            for rec in legs:
                if rec["tick1"] is None:
                    crew = sess._crew_sims[rec["crew"]]
                    if getattr(crew, "_diag_leg_ref", None) is not rec or crew._leg is None:
                        rec["tick1"] = t
        for rec in legs:
            if rec["tick1"] is None:
                rec["tick1"] = TICKS + 1
    finally:
        _SimCrew._begin_leg = orig_begin

    # --- A. leg census
    by_kind = defaultdict(lambda: [0, 0])
    for r in legs:
        b = by_kind[r["kind"]]
        b[0] += 1
        if not r["on_roads"]:
            b[1] += 1
    print("A. legs begun:", dict((k, tuple(v)) for k, v in by_kind.items()),
          "(count, straight-fallbacks)")
    n_road = sum(1 for r in legs if r["on_roads"])
    n_chord = sum(1 for r in legs if not r["on_roads"])
    print(f"   total {len(legs)} legs: {n_road} road-following, {n_chord} straight "
          f"({100.0*n_chord/max(1,len(legs)):.1f}% chords)")

    # --- B. visibility: leg lifetime in ticks
    same_tick = sum(1 for r in legs if r["tick1"] == r["tick0"])
    life = [r["tick1"] - r["tick0"] for r in legs]
    print(f"B. legs completing INSIDE one tick (never in any state() poll): "
          f"{same_tick}/{len(legs)} = {100.0*same_tick/max(1,len(legs)):.1f}%")
    print(f"   leg lifetime ticks: min {min(life) if life else '-'} "
          f"med {sorted(life)[len(life)//2] if life else '-'} max {max(life) if life else '-'}")
    # polyline length never exposed at a tick boundary
    hidden_m = sum(sum(geo.haversine_m(a, b) for a, b in zip(r["pts"], r["pts"][1:]))
                   for r in legs if r["tick1"] == r["tick0"] and r["on_roads"])
    total_m = sum(sum(geo.haversine_m(a, b) for a, b in zip(r["pts"], r["pts"][1:]))
                  for r in legs if r["on_roads"])
    print(f"   road-polyline meters never exposed to the UI: {hidden_m:,.0f} of "
          f"{total_m:,.0f} ({100.0*hidden_m/max(1,total_m):.1f}%)")

    # --- C. what the founder sees: chords between consecutive tick positions
    seen_off = 0
    seen_n = 0
    by_crew = defaultdict(list)
    for cid, t, p in move_samples:
        by_crew[cid].append((t, p))
    for cid, rows in by_crew.items():
        rows.sort(key=lambda r: r[0])
        for (t0, p0), (t1, p1) in zip(rows, rows[1:]):
            if t1 - t0 != 1:
                continue
            for s in sample_segment(p0, p1):
                seen_n += 1
                if idx.dist_m(s) > 75.0:
                    seen_off += 1
    print(f"C. tick-boundary chord samples (what the track/tween shows): "
          f"{seen_off}/{seen_n} = {100.0*seen_off/max(1,seen_n):.1f}% are >75 m from any road")
    # control: the ACTUAL engine polylines
    ctl_off = 0
    ctl_n = 0
    for r in legs:
        if not r["on_roads"]:
            continue
        for a, b in zip(r["pts"], r["pts"][1:]):
            for s in sample_segment(a, b, 150.0):
                ctl_n += 1
                if idx.dist_m(s) > 75.0:
                    ctl_off += 1
    print(f"   control, engine road-leg polylines: {ctl_off}/{ctl_n} = "
          f"{100.0*ctl_off/max(1,ctl_n):.1f}% >75 m off-road")
    ch_off = 0
    ch_n = 0
    for r in legs:
        if r["on_roads"]:
            continue
        for s in sample_segment(r["from"], r["to"]):
            ch_n += 1
            if idx.dist_m(s) > 75.0:
                ch_off += 1
    print(f"   control, straight-fallback legs: {ch_off}/{ch_n} = "
          f"{100.0*ch_off/max(1,ch_n):.1f}% >75 m off-road")

    # --- D. flag honesty at tick boundaries
    bad = [r for r in flag_rows if r[1] != r[2]]
    print(f"D. state() on_roads flag vs active leg flag: {len(bad)} mismatches "
          f"over {len(flag_rows)} moving tick-samples")
    return legs


if __name__ == "__main__":
    for key, la, lo in SCENARIOS:
        run_location(key, la, lo)
