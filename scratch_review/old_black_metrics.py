"""scratch_review/old_black_metrics.py — per-tick measurements for the founder
rulings of 2026-09-26 ("old burned ground does not count as a violation.
crews once leave a map are no longer part of the simulation"; contracts
old_black_refuge / left_map_out / escape_prefer_on_map; docs/SIMULATOR_DESIGN.md
"Old black is a refuge; crews that leave the map are out (2026-09-26)").

Shared by the validation probes (roads_offroad_probe.py, bench_dispatch.py and
corridor_grid.py behind their OLD_BLACK_METRICS env hooks). READ-ONLY: it reads
the session after a tick and never changes it.

What one tick contributes (Acc.add_tick):
  * rows          10c crew-overrun rows of the tick by "reason|mode" (what the
                  scoreboard counts under the session's own law);
  * stopped       crew-ticks the STRICT legacy audit would count (nominal
                  front passed the crew's cell, or eroded P10 within the
                  egress buffer) that the session's audit did NOT count — with
                  the old-black refuge armed these are exactly the crews
                  resting on old black; each carries the doctrine-nuance
                  numbers below;
  * in_active     of those, how many sit INSIDE the eroded-P10 imminent band
                  of an ACTIVE front: the band recomputed from the P10 of
                  every cell that is not old black (unburned or still burning
                  fuel), with the session's own erosion (tiered or uniform);
  * dist_burning  metres to the nearest actively burning cell (nominal
                  arrival inside its cold-black window) for each stopped
                  crew-tick (None: nothing burning on the tile);
  * hist_only     10c IMMINENT rows the session still counts whose band comes
                  only from old black (not in the active band): a crew on an
                  unburnable cell (road, developed) in the scar — the ruling is
                  per cell, so those still count;
  * left          crews that left the map (cumulative, scoreboard).
"""
from __future__ import annotations

import math

from wgresponse import geo, spread
from wgresponse.contracts import CrewStatus, Raster
from wgresponse.fuels import cold_black_minutes

INF = math.inf


def _window_fn(s):
    per_class = bool(getattr(s._cfg, "transit_cold_black_by_class", False))
    memo = {}

    def win(code):
        if not per_class:
            return 45.0 * 60.0
        w = memo.get(code)
        if w is None:
            w = memo[code] = cold_black_minutes(code) * 60.0
        return w
    return win


class _TickView:
    """Lazily built per-tick rasters (union nominal, burning cells, the
    active-front eroded band)."""

    def __init__(self, s):
        self.s = s
        self.now = s._now_s
        self.spec = s._tile.spec
        self._nom = None
        self._burning = None
        self._band = None

    def nom(self):
        if self._nom is None:
            n = self.spec.nrows * self.spec.ncols
            out = [INF] * n
            for fid in self.s._fire_order:
                f = self.s._fires[fid]
                if f.ens is None:
                    continue
                d = f.ens.members[0].data
                out = [a if a <= b else b for a, b in zip(out, d)]
            self._nom = out
        return self._nom

    def burning(self):
        """(lat, lon) of every cell whose nominal arrival is inside its
        cold-black window at now."""
        if self._burning is None:
            win = _window_fn(self.s)
            fuel = self.s._tile.fuel.data
            nc = self.spec.ncols
            pts = []
            for i, a in enumerate(self.nom()):
                if a <= self.now and (self.now - a) < win(int(round(fuel[i]))):
                    p = geo.rc_to_latlon(self.spec, i // nc, i % nc)
                    pts.append((p.lat, p.lon))
            self._burning = pts
        return self._burning

    def band(self):
        """The active-front eroded P10 (min over fires): each fire's P10 with
        every OLD-BLACK cell masked out, eroded exactly as the session erodes
        (tiered by fuel when corridor_erosion_tiered, else uniform)."""
        if self._band is None:
            cfg = self.s._cfg
            win = _window_fn(self.s)
            fuel = self.s._tile.fuel.data
            nom = self.nom()
            n = len(nom)
            old = [nom[i] + win(int(round(fuel[i]))) <= self.now
                   for i in range(n)]
            out = None
            for fid in self.s._fire_order:
                f = self.s._fires[fid]
                if f.ens is None:
                    continue
                p10 = f.ens.p10
                masked = Raster(p10.spec, [INF if old[i] else p10.data[i]
                                           for i in range(n)])
                if getattr(cfg, "corridor_erosion_tiered", False):
                    er = spread.tiered_eroded_p10(masked, self.s._tile.fuel, cfg)
                else:
                    er = geo.erode_min(
                        masked, cfg.corridor_erosion_m,
                        shape=("euclidean" if getattr(
                            cfg, "corridor_erosion_euclidean", False)
                            else "chebyshev"))
                d = er.data
                out = list(d) if out is None else [a if a <= b else b
                                                   for a, b in zip(out, d)]
            self._band = out
        return self._band

    def in_band(self, pos) -> bool:
        band = self.band()
        if band is None:
            return False
        rc = geo.latlon_to_rc(self.spec, pos)
        if rc is None:
            return False
        v = band[rc[0] * self.spec.ncols + rc[1]]
        return v < self.now + self.s._cfg.egress_buffer_min * 60.0

    def front(self):
        """(lat, lon) of every cell the nominal front crosses within one
        15-min page tick of now (arrival in (now - 900 s, now + 900 s]) — the
        live front line, robust to a 5-min grass window that a tick boundary
        can straddle with no cell 'burning'."""
        if getattr(self, "_front", None) is None:
            nc = self.spec.ncols
            pts = []
            for i, a in enumerate(self.nom()):
                if self.now - 900.0 < a <= self.now + 900.0:
                    p = geo.rc_to_latlon(self.spec, i // nc, i % nc)
                    pts.append((p.lat, p.lon))
            self._front = pts
        return self._front

    @staticmethod
    def _nearest(pos, pts):
        if not pts:
            return None
        k = math.cos(math.radians(pos.lat)) * geo.M_PER_DEG_LAT
        best = INF
        for la, lo in pts:
            dx = (lo - pos.lon) * k
            dy = (la - pos.lat) * geo.M_PER_DEG_LAT
            d = dx * dx + dy * dy
            if d < best:
                best = d
        return math.sqrt(best)

    def dist_burning(self, pos):
        return self._nearest(pos, self.burning())

    def dist_front(self, pos):
        return self._nearest(pos, self.front())


class Acc:
    """Accumulates the per-tick measurements of one session."""

    def __init__(self):
        self.rows = {}
        self.stopped = 0
        self.in_active = 0
        self.dist = []            # dist_burning per stopped crew-tick (None ok)
        self.dist_front = []      # dist to the +-15 min front line (None ok)
        self.ages_min = []
        self.windows_min = []
        self.stopped_modes = {}
        self.hist_only = 0
        self.examples = []        # first few stopped crew-ticks, for the record
        self.left = 0

    def add_tick(self, s, st, tick=None):
        now = s._now_s
        buffer_s = s._cfg.egress_buffer_min * 60.0
        rows = st.get("crew_overruns") or []
        counted = {o["crew_id"] for o in rows}
        for o in rows:
            k = f"{o['reason']}|{o['mode']}"
            self.rows[k] = self.rows.get(k, 0) + 1
        view = _TickView(s)
        left = getattr(s, "_left_map", {}) or {}
        for cid in sorted(s._crew_sims):
            cs = s._crew_sims[cid]
            if cs.status == CrewStatus.INBOUND or cid in left:
                continue
            nom, p10 = s._crew_exposure(cs.pos)
            burned = (not math.isinf(nom)) and nom <= now
            imminent = (not math.isinf(p10)) and p10 < now + buffer_s
            if cid in counted:
                o = next(r for r in rows if r["crew_id"] == cid)
                if (o["reason"] == "imminent" and not burned
                        and not view.in_band(cs.pos)):
                    self.hist_only += 1
                continue
            if not (burned or imminent):
                continue
            # the strict audit would count this crew-tick; this session's
            # audit did not (the old-black refuge)
            self.stopped += 1
            act = view.in_band(cs.pos)
            self.in_active += int(act)
            d = view.dist_burning(cs.pos)
            self.dist.append(None if d is None else round(d, 1))
            df = view.dist_front(cs.pos)
            self.dist_front.append(None if df is None else round(df, 1))
            age = (now - nom) / 60.0 if burned else None
            w = s._cold_black_pass_s(cs.pos) / 60.0
            self.ages_min.append(None if age is None else round(age, 1))
            self.windows_min.append(round(w, 1))
            self.stopped_modes[cs._mode] = self.stopped_modes.get(cs._mode, 0) + 1
            if len(self.examples) < 12:
                self.examples.append({
                    "tick": tick, "crew": cid, "mode": cs._mode,
                    "lat": round(cs.pos.lat, 6), "lon": round(cs.pos.lon, 6),
                    "age_min": None if age is None else round(age, 1),
                    "window_min": round(w, 1), "in_active_band": act,
                    "dist_burning_m": None if d is None else round(d, 1)})
        self.left = int((st.get("scoreboard") or {}).get("crews_left_map", 0) or 0)

    def as_dict(self):
        ds = [d for d in self.dist if d is not None]
        return {
            "rows_by_reason_mode": dict(sorted(self.rows.items())),
            "rows": sum(self.rows.values()),
            "stopped_crew_ticks": self.stopped,
            "stopped_in_active_band": self.in_active,
            "stopped_modes": dict(sorted(self.stopped_modes.items())),
            "stopped_dist_burning_m": self.dist,
            "stopped_dist_burning_min_m": min(ds) if ds else None,
            "stopped_dist_burning_median_m": (sorted(ds)[len(ds) // 2]
                                               if ds else None),
            "stopped_no_burning_on_tile": sum(1 for d in self.dist if d is None),
            "stopped_dist_front_m": self.dist_front,
            "stopped_ages_min": self.ages_min,
            "stopped_windows_min": self.windows_min,
            "imminent_rows_history_only": self.hist_only,
            "crews_left_map": self.left,
            "examples": self.examples,
        }


def dist_buckets(dists) -> dict:
    """Doctrine-nuance buckets of the nearest actively burning cell."""
    out = {"none_burning": 0, "<100m": 0, "100-300m": 0, "300m-1km": 0,
           "1-3km": 0, ">=3km": 0}
    for d in dists:
        if d is None:
            out["none_burning"] += 1
        elif d < 100.0:
            out["<100m"] += 1
        elif d < 300.0:
            out["100-300m"] += 1
        elif d < 1000.0:
            out["300m-1km"] += 1
        elif d < 3000.0:
            out["1-3km"] += 1
        else:
            out[">=3km"] += 1
    return out
