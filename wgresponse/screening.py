"""wgresponse.screening — HOTSPOT SCREENING STEP 0: SHADOW LABELS AND THE
RAW FIRMS LOG (contracts SAFETY SEMANTICS 43; the founder's decisions of
2026-11-01, docs/review/hotspot_screening_2026-11-01.md).

Nothing here changes a forecast, an alert, a crew or the heat-only listing.
It labels the satellite-only clusters for the operator, in shadow, and keeps
the raw record a later replay needs:

  FirmsPollLog    each FIRMS row once, at the poll that first returned it,
                  and again when it changes (a URT pixel reprocessed) or
                  vanishes before it ages out of the asked window (a
                  withdrawn detection) — NASA keeps neither. Day files
                  <dir>/firms_<UTC date>.jsonl, mirrored gzipped to S3
                  (<prefix>/firms_log/firms_<date>.jsonl.gz); a restart
                  replays the last two days and carries on.
  HeatSiteMemory  the detections of the last five UTC dates, grouped into
                  750 m sites by the FirmsFeed clustering law, for report
                  B's industrial signature (docs/research/hotspot_screening/
                  B_empirical.md §3.4).
  label(...)      one satellite-only incident's labels: a known heat site,
                  a registered prescribed burn, and its looks (passes) and
                  growth between them.

Missing data never demotes: a label that cannot be checked (no five-day
history yet, WFIGS blind, a detection the memory never saw) reads unknown
(None), never "not a fire". No wall clock: every time is the caller's."""
from __future__ import annotations

import calendar
import csv
import gzip
import io
import json
import logging
import math
import time
from pathlib import Path
from typing import Optional

from wgresponse.contracts import LatLon
from wgresponse.feeds import _Footprint, _cluster_detections, _haversine_m

log = logging.getLogger(__name__)

#: Report B §3.4, the industrial signature IS(3, 0.8, 5, 8): a 750 m site
#: hot on >= 3 of the last 5 UTC dates, >= 80 % of its detections at night,
#: peak FRP <= 5 MW, <= 8 detections on any one date (pooled 25-29 Oct
#: 2026: 11.3 % of clusters; 0 of 10 known live fires and 0 of 86
#: prescribed-burn clusters lost). A daytime detection in the cluster
#: itself releases it (B §7b.2).
SITE_M = 750.0
SITE_DAYS = 5
SITE_MIN_DATES = 3
SITE_NIGHT_FRAC = 0.8
SITE_MAX_FRP_MW = 5.0
SITE_MAX_PER_DAY = 8
#: the memory asks FIRMS for its five days again once the last answer is
#: this old (each poll's rows are merged in between), and not again within
#: MEMORY_RETRY_S of a failed ask
MEMORY_REFRESH_S = 24 * 3600.0
MEMORY_RETRY_S = 15 * 60.0
#: a look: one satellite's detections of a cluster within this span (a pass)
LOOK_GAP_S = 15 * 60.0
#: growth between looks (shadow measurement): the newest look holds at
#: least twice the pixels (and 2 more) of any earlier look, or twice the
#: peak FRP (and 2 MW more), or heat more than 1 km from all earlier heat
#: (a VIIRS pixel is 375 m at nadir and ~800 m at the swath edge: on the
#: 2026-11-01 CONUS day most 0.5-0.6 km "moves" were one pixel shifting
#: between the two satellites)
GROW_FOOTPRINT_M = 1000.0
GROW_FRP_FACTOR = 2.0
GROW_FRP_MIN_MW = 2.0
#: the log calls no row withdrawn within this long of 00:00 UTC (the asked
#: window of UTC dates rolls), or when a poll loses more than this share
#: (and this many) of the window's rows at once (a truncated answer)
LOG_MIDNIGHT_GUARD_S = 15 * 60.0
LOG_GONE_SUSPECT_FRAC = 0.2
LOG_GONE_SUSPECT_MIN = 20
#: local day files kept (the S3 mirror keeps every day)
LOG_LOCAL_DAYS = 3


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------

def parse_rows(text: str) -> list:
    """FIRMS area-API CSV -> one dict per row, every column as delivered."""
    return [dict(r) for r in csv.DictReader(io.StringIO(text))]


def row_time_s(row: dict) -> Optional[float]:
    """A row's acquisition time (unix s, UTC) — FirmsFeed._parse_csv's law;
    None when unparseable."""
    try:
        day = calendar.timegm(time.strptime(row["acq_date"], "%Y-%m-%d"))
        hm = int(row["acq_time"])
    except (KeyError, ValueError, TypeError):
        return None
    if not (0 <= hm // 100 < 24 and 0 <= hm % 100 < 60):
        return None
    return float(day + (hm // 100) * 3600 + (hm % 100) * 60)


def _float(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _utc_date(t_s: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t_s))


def _date_minus(date: str, days: int) -> str:
    return _utc_date(calendar.timegm(time.strptime(date, "%Y-%m-%d"))
                     - 86400 * int(days))


def _tp_key(t: float, lat: float, lon: float) -> tuple:
    """A detection's identity without its instrument (FirmsFeed._det_key's
    rounding): two satellites never share an acquisition time and pixel."""
    return (int(round(t)), round(lat, 5), round(lon, 5))


def short_src(src) -> Optional[str]:
    """VIIRS_NOAA20_NRT -> N20, VIIRS_NOAA21_NRT -> N21, VIIRS_SNPP_NRT ->
    SNPP; None stays None."""
    if not src:
        return None
    s = str(src).replace("VIIRS_", "").replace("_NRT", "")
    return {"NOAA20": "N20", "NOAA21": "N21"}.get(s, s)


def _detail(src: str, row: dict) -> Optional[dict]:
    t = row_time_s(row)
    lat, lon = _float(row.get("latitude")), _float(row.get("longitude"))
    if t is None or lat is None or lon is None:
        return None
    return {"t": t, "lat": lat, "lon": lon, "src": src,
            "date": row.get("acq_date"),
            "night": (row.get("daynight") or "").strip().upper() == "N",
            "frp": _float(row.get("frp")),
            "conf": row.get("confidence")}


# ---------------------------------------------------------------------------
# the five-day heat-site memory
# ---------------------------------------------------------------------------

def _site_verdict(site: dict) -> tuple:
    """(static, why) for one 750 m site under report B's signature."""
    if site["dates"] < SITE_MIN_DATES:
        return False, (f"hot on {site['dates']} of the last {SITE_DAYS} UTC "
                       f"dates (a known site: {SITE_MIN_DATES} or more)")
    if site["night_frac"] < SITE_NIGHT_FRAC:
        return False, (f"{round(100 * site['night_frac'])} % of its "
                       f"detections at night (a known site: "
                       f"{round(100 * SITE_NIGHT_FRAC)} % or more)")
    if site["frp_max"] is None:
        return False, "a detection without FRP"
    if site["frp_max"] > SITE_MAX_FRP_MW:
        return False, (f"peak {site['frp_max']:.1f} MW (a known site stays "
                       f"at or under {SITE_MAX_FRP_MW:g} MW)")
    if site["max_per_day"] > SITE_MAX_PER_DAY:
        return False, (f"{site['max_per_day']} detections on one date (a "
                       f"known site: {SITE_MAX_PER_DAY} or fewer)")
    return True, (f"hot on {site['dates']} of the last {SITE_DAYS} UTC dates, "
                  f"{round(100 * site['night_frac'])} % at night, peak "
                  f"{site['frp_max']:.1f} MW")


class HeatSiteMemory:
    """The FIRMS detections of the last SITE_DAYS UTC dates (all of the
    feed's sources pooled), grouped into SITE_M sites on demand.

    Fed by the FirmsFeed (live mode, memory=): a five-day answer per source
    when wants_history() (at start, then daily; the answer replaces that
    source's rows) and every poll's rows merged in between. ready() only
    once every source has answered the five-day ask: until then no cluster
    is labelled a known site."""

    def __init__(self, sources, days: int = SITE_DAYS) -> None:
        self._sources = tuple(sources)
        self._days = int(days)
        self._dets: dict = {}        # _tp_key -> detail
        self._history: dict = {}     # src -> t_s of its last five-day answer
        self._failed: dict = {}      # src -> (t_s, error) of the last failed ask
        self._sites = None           # (sites, {_tp_key: site index}), lazily

    def wants_history(self, src: str, now_s: float) -> bool:
        failed = self._failed.get(src)
        if failed is not None and float(now_s) - failed[0] < MEMORY_RETRY_S:
            return False
        t = self._history.get(src)
        return t is None or float(now_s) - t >= MEMORY_REFRESH_S

    def add(self, src: str, rows: list, now_s: float,
            history: bool = False) -> int:
        """Merge one answer's rows; history=True: the source's five-day
        answer, which replaces its rows. Returns the rows taken."""
        if history:
            self._dets = {k: d for k, d in self._dets.items()
                          if d["src"] != src}
        n = 0
        for row in rows:
            d = _detail(src, row)
            if d is None:
                continue
            self._dets[_tp_key(d["t"], d["lat"], d["lon"])] = d
            n += 1
        if history:
            self._history[src] = float(now_s)
            self._failed.pop(src, None)
        start = _date_minus(_utc_date(float(now_s)), self._days - 1)
        self._dets = {k: d for k, d in self._dets.items()
                      if (d["date"] or "") >= start}
        self._sites = None
        return n

    def history_failed(self, src: str, now_s: float, error) -> None:
        self._failed[src] = (float(now_s), str(error)[:200])

    def ready(self) -> bool:
        return all(s in self._history for s in self._sources)

    def detail(self, t: float, p: LatLon) -> Optional[dict]:
        return self._dets.get(_tp_key(t, p.lat, p.lon))

    def site_of(self, t: float, p: LatLon) -> Optional[dict]:
        sites, index = self._computed()
        i = index.get(_tp_key(t, p.lat, p.lon))
        return None if i is None else sites[i]

    def _computed(self) -> tuple:
        if self._sites is None:
            dets = sorted(self._dets.items(),
                          key=lambda kv: (-kv[1]["t"], kv[1]["lat"],
                                          kv[1]["lon"], kv[1]["src"]))
            clusters = _cluster_detections(
                [(d["t"], LatLon(d["lat"], d["lon"]), k) for k, d in dets],
                SITE_M)
            sites, index = [], {}
            for cl in clusters:
                ds = [self._dets[det[2]] for det in cl["pts"]]
                per_day: dict = {}
                for d in ds:
                    per_day[d["date"]] = per_day.get(d["date"], 0) + 1
                frps = [d["frp"] for d in ds]
                site = {"lat": round(cl["lat"], 5), "lon": round(cl["lon"], 5),
                        "n": len(ds), "dates": len(per_day),
                        "night_frac": round(sum(1 for d in ds if d["night"])
                                            / len(ds), 3),
                        "frp_max": (None if any(f is None for f in frps)
                                    else max(frps)),
                        "max_per_day": max(per_day.values())}
                site["static"], site["why"] = _site_verdict(site)
                for det in cl["pts"]:
                    index[det[2]] = len(sites)
                sites.append(site)
            self._sites = (sites, index)
        return self._sites

    def status(self) -> dict:
        sites, _ = self._computed()
        return {"ready": self.ready(),
                "days": self._days,
                "detections": len(self._dets),
                "dates": sorted({d["date"] for d in self._dets.values()
                                 if d["date"]}),
                "sites": len(sites),
                "known_sites": sum(1 for s in sites if s["static"]),
                "sources": {s: {"history_s": self._history.get(s),
                                "failed": (None if s not in self._failed else
                                           {"t_s": self._failed[s][0],
                                            "error": self._failed[s][1]})}
                            for s in self._sources}}


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------

def prepare_rx(rx_records, now_s: float, cfg) -> Optional[list]:
    """The registered prescribed burns that can label heat this cycle, as
    (record, footprint, kind); None when the records are unknown (WFIGS
    blind or not asked). An RX record whose FireOutDateTime has passed is
    out; a point counts within cfg.screen_rx_radius_m when discovered (else
    last modified) within cfg.screen_rx_max_age_d days; a perimeter counts
    inside or within cfg.fuse_hotspots_perimeter_m of its rings, any age
    (report A, C10)."""
    if rx_records is None:
        return None
    out = []
    max_age = float(cfg.screen_rx_max_age_d) * 86400.0
    for rec in rx_records:
        meta = rec.meta or {}
        gone = _float(meta.get("out_at_s"))
        if gone is not None and gone <= now_s:
            continue
        if rec.perimeter:
            fp = _Footprint(rec, cfg.fuse_hotspots_perimeter_m, 0.0, 0.0,
                            False)
            if fp.rings:
                out.append((rec, fp, "perimeter"))
            continue
        when = rec.discovered_at_s or rec.updated_at_s
        if not when or now_s - float(when) > max_age:
            continue
        r = float(cfg.screen_rx_radius_m)
        out.append((rec, _Footprint(rec, 0.0, r, r, False), "point"))
    return out


def _rx_label(pts: list, rx_index: Optional[list]) -> dict:
    if rx_index is None:
        return {"match": None,
                "why": "prescribed-burn records unavailable this cycle"}
    best = None
    for rec, fp, kind in rx_index:
        ds = [d for d in (fp.distance(p) for p in pts) if d is not None]
        if not ds:
            continue
        key = (min(ds), str(rec.incident_id))
        if best is None or key < best[0]:
            best = (key, rec, kind)
    if best is None:
        return {"match": False, "why": "no open registered burn in reach"}
    (dist, _), rec, kind = best
    where = ("inside its perimeter" if kind == "perimeter" and dist == 0.0
             else f"{dist / 1000.0:.1f} km from its "
                  f"{'perimeter' if kind == 'perimeter' else 'point'}")
    return {"match": True, "name": rec.name, "id": rec.incident_id,
            "kind": kind, "distance_m": round(dist),
            "discovered_s": rec.discovered_at_s,
            "why": f"registered prescribed burn {rec.name}, {where}"}


def _site_label(pts: list, ts: list, details: list,
                memory: Optional[HeatSiteMemory]) -> dict:
    if memory is None:
        return {"static": None, "why": "no five-day satellite history"}
    if not memory.ready():
        return {"static": None, "why": "the five-day history is not loaded yet"}
    unknown = sum(1 for d in details if d is None)
    if unknown:
        return {"static": None,
                "why": f"{unknown} detection(s) not in the five-day history"}
    if any(not d["night"] for d in details):
        return {"static": False, "why": "a daytime detection"}
    sites = [memory.site_of(t, p) for t, p in zip(ts, pts)]
    if any(s is None for s in sites):
        return {"static": None,
                "why": "a detection outside the five-day sites"}
    for s in sites:
        if not s["static"]:
            return {"static": False, "why": s["why"]}
    s0 = max(sites, key=lambda s: (s["dates"], s["n"]))
    return {"static": True, "why": s0["why"], "dates": s0["dates"],
            "night_frac": s0["night_frac"], "frp_max": s0["frp_max"]}


def looks_of(pts: list, ts: list, details: list) -> list:
    """The cluster's looks, oldest first: one satellite's detections within
    LOOK_GAP_S of the look's first. [{t_s, src, n, frp, pts}]."""
    rows = sorted(zip(ts, pts, details),
                  key=lambda x: (x[0], x[1].lat, x[1].lon))
    looks: list = []
    for t, p, d in rows:
        src = d["src"] if d else None
        cur = looks[-1] if looks else None
        if cur is None or src != cur["src"] or t - cur["t_s"] > LOOK_GAP_S:
            cur = {"t_s": float(t), "src": src, "n": 0, "frp": None,
                   "frp_known": True, "pts": []}
            looks.append(cur)
        cur["n"] += 1
        cur["pts"].append(p)
        f = d["frp"] if d else None
        if f is None:
            cur["frp_known"] = False
        else:
            cur["frp"] = f if cur["frp"] is None else max(cur["frp"], f)
    return looks


def growth(looks: list) -> dict:
    """Did the newest look grow on the earlier ones? grew None with one look
    (nothing to compare: quiet is not seen, never out)."""
    if len(looks) < 2:
        return {"grew": None, "why": "one look so far"}
    last, prev = looks[-1], looks[:-1]
    why = []
    pn = max(lk["n"] for lk in prev)
    if last["n"] >= max(2 * pn, pn + 2):
        why.append(f"{pn} -> {last['n']} pixels")
    pf = [lk["frp"] for lk in prev if lk["frp"] is not None]
    if last["frp"] is not None and pf:
        pmax = max(pf)
        if (last["frp"] >= GROW_FRP_FACTOR * pmax
                and last["frp"] - pmax >= GROW_FRP_MIN_MW):
            why.append(f"{pmax:.1f} -> {last['frp']:.1f} MW")
    earlier = [q for lk in prev for q in lk["pts"]]
    far = max(min(_haversine_m(p, q) for q in earlier) for p in last["pts"])
    if far > GROW_FOOTPRINT_M:
        why.append(f"new heat {far / 1000.0:.1f} km from the earlier looks")
    return {"grew": bool(why),
            "why": "; ".join(why) if why else "no growth on the earlier looks"}


def label(inc, memory: Optional[HeatSiteMemory], rx_index: Optional[list]
          ) -> dict:
    """One satellite-only incident's shadow labels (SAFETY SEMANTICS 43):
    {labels, site, rx, looks, grew, growth}. labels lists what applies —
    "heat_site", "rx_burn", "grew"; site.static / rx.match / grew are None
    when they could not be checked."""
    meta = inc.meta or {}
    pts = list(inc.hotspots or []) or [inc.origin]
    ts = [float(t) for t in (meta.get("hotspot_t_s") or [])]
    if len(ts) != len(pts):
        ts = [float(inc.updated_at_s)] * len(pts)
    details = [memory.detail(t, p) if memory is not None else None
               for t, p in zip(ts, pts)]
    site = _site_label(pts, ts, details, memory)
    rx = _rx_label(pts, rx_index)
    looks = looks_of(pts, ts, details)
    grow = growth(looks)
    labels = ([n for n, on in (("heat_site", site["static"]),
                               ("rx_burn", rx["match"]),
                               ("grew", grow["grew"])) if on])
    return {"labels": labels, "site": site, "rx": rx,
            "looks": [{"t_s": lk["t_s"], "src": short_src(lk["src"]),
                       "n": lk["n"],
                       "frp": (round(lk["frp"], 2) if lk["frp"] is not None
                               else None)} for lk in looks],
            "grew": grow["grew"], "growth": grow["why"]}


def summary(labels: list, memory: Optional[HeatSiteMemory],
            rx_index: Optional[list]) -> dict:
    """This cycle's counts over the labelled incidents (the /api/state
    "screen" block)."""
    def n(pred):
        return sum(1 for lab in labels if pred(lab))
    return {"rule": "shadow",
            "clusters": len(labels),
            "heat_site": n(lambda x: x["site"]["static"] is True),
            "rx_burn": n(lambda x: x["rx"]["match"] is True),
            "grew": n(lambda x: x["grew"] is True),
            "one_look": n(lambda x: x["grew"] is None),
            "site_unknown": n(lambda x: x["site"]["static"] is None),
            "rx_unknown": n(lambda x: x["rx"]["match"] is None),
            "rx_records": None if rx_index is None else len(rx_index),
            "memory": None if memory is None else memory.status()}


def summary_note(s: dict) -> str:
    parts = [f"{s['heat_site']} known heat site(s)",
             f"{s['rx_burn']} on a registered prescribed burn",
             f"{s['grew']} grew between looks",
             f"{s['one_look']} seen on one look"]
    tail = []
    if s["site_unknown"]:
        tail.append(f"heat site unchecked for {s['site_unknown']}")
    if s["rx_unknown"]:
        tail.append(f"prescribed burns unchecked for {s['rx_unknown']}")
    return (f"screening (shadow): {s['clusters']} satellite-only cluster(s) — "
            + ", ".join(parts) + ("; " + ", ".join(tail) if tail else "")
            + " — labels only, nothing paged or changed (SAFETY SEMANTICS 43)")


# ---------------------------------------------------------------------------
# the raw FIRMS log
# ---------------------------------------------------------------------------

def _row_key(row: dict) -> Optional[tuple]:
    k = tuple(row.get(f) for f in ("latitude", "longitude", "acq_date",
                                   "acq_time"))
    return None if any(v in (None, "") for v in k) else k


def _key_str(k: tuple) -> str:
    return "|".join(k)


def _prefer(old: dict, new: dict) -> dict:
    """Two rows with one key in one answer: the reprocessed one (not URT)."""
    if str(new.get("version", "")).endswith("URT") and \
            not str(old.get("version", "")).endswith("URT"):
        return old
    return new


class FirmsPollLog:
    """Every FIRMS poll's rows with the time we first saw them (SAFETY
    SEMANTICS 43). One JSON object per line, in day files by the poll's UTC
    date:

      {"ev": "new", "t_s", "prev_s", "src", "row"}       first seen
      {"ev": "changed", "t_s", "src", "fields", "row"}   same pixel, new values
      {"ev": "gone", "t_s", "src", "key"}                withdrawn in-window
      {"ev": "poll", "t_s", "src", "rows", "new", "changed", "gone",
       "dup", "gone_skipped"}                           every answer
      {"ev": "blind", "t_s", "src", "error"}             a failed ask
      {"ev": "start", "t_s", "keys", "restored", "why"}  first use

    A row's key is its latitude, longitude, acq_date and acq_time as
    delivered (never its version: report A, C20). prev_s is the source's
    previous answered poll, so a row became available in (prev_s, t_s]; a
    null prev_s means it was already there when logging began. Nothing is
    called withdrawn without an earlier poll, within LOG_MIDNIGHT_GUARD_S of
    00:00 UTC, or when a poll loses more than LOG_GONE_SUSPECT_FRAC of the
    window's rows at once. A restart replays the last two day files (from
    S3 when the local disk is fresh) and carries on. The log never raises
    into the feed: the FirmsFeed guards every call."""

    def __init__(self, dir_path, s3_uri: Optional[str] = None,
                 s3_client=None) -> None:
        self._dir = Path(dir_path)
        self._mirror = None
        if s3_uri:
            from wgresponse.persist import S3Mirror
            self._mirror = S3Mirror(s3_uri, client=s3_client)
        self._seen: dict = {}         # src -> {key: row}
        self._prev: dict = {}         # src -> t_s of its last answered poll
        self._dirty: set = set()      # dates written since the last upload
        self._started = False
        self._restored: Optional[bool] = None
        self._restore_why: list = []
        self._today: dict = {}        # date -> counts
        self._last_poll_s: Optional[float] = None
        self._unmerged: dict = {}     # date -> start t_s: restore failed

    # -- files ---------------------------------------------------------------

    @property
    def dir(self) -> Path:
        return self._dir

    def path_for(self, date: str) -> Path:
        return self._dir / f"firms_{date}.jsonl"

    def key_for(self, date: str) -> Optional[str]:
        """The day's mirror key; a day whose restore failed writes beside
        the day's object (…unmerged-<start>.jsonl.gz), never over it."""
        if self._mirror is None:
            return None
        if date in self._unmerged:
            return self._mirror.key_for(f"firms_log/firms_{date}.unmerged-"
                                        f"{self._unmerged[date]}.jsonl.gz")
        return self._mirror.key_for(f"firms_log/firms_{date}.jsonl.gz")

    def _write(self, t_s: float, obj: dict) -> None:
        date = _utc_date(t_s)
        self._dir.mkdir(parents=True, exist_ok=True)
        with open(self.path_for(date), "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, sort_keys=True, separators=(",", ":"))
                    + "\n")
        self._dirty.add(date)
        c = self._today.setdefault(date, {"polls": 0, "blind": 0, "new": 0,
                                          "changed": 0, "gone": 0})
        if obj["ev"] in ("new", "changed", "gone"):
            c[obj["ev"]] += 1
        elif obj["ev"] in ("poll", "blind"):
            c["polls" if obj["ev"] == "poll" else "blind"] += 1

    # -- restart -------------------------------------------------------------

    def _start(self, t_s: float) -> None:
        if self._started:
            return
        self._started = True
        ok = True
        today = _utc_date(t_s)
        for date in (_date_minus(today, 1), today):
            path = self.path_for(date)
            local = path.exists()
            try:
                if not local and self._mirror is not None:
                    body = self._mirror.download(self.key_for(date))
                    if body is not None:
                        self._dir.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(gzip.decompress(body))
                if path.exists():
                    self._replay(path)
            except Exception as exc:              # noqa: BLE001 — logged
                ok = False
                self._restore_why.append(
                    f"{date}: restore failed ({type(exc).__name__})")
                log.error("FIRMS log: could not restore %s (%s) — logging "
                          "resumes without its rows", date, exc)
                if not local:
                    # the mirror's object may hold this day's earlier rows:
                    # this run's file goes beside it, never over it
                    self._unmerged[date] = int(t_s)
        self._restored = ok
        keys = sum(len(v) for v in self._seen.values())
        self._write(t_s, {"ev": "start", "t_s": t_s, "keys": keys,
                          "restored": ok, "why": self._restore_why or None})

    def _replay(self, path: Path) -> None:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue                  # a torn last line
                src = ev.get("src")
                if ev.get("ev") in ("new", "changed") and src:
                    k = _row_key(ev.get("row") or {})
                    if k is not None:
                        self._seen.setdefault(src, {})[k] = ev["row"]
                elif ev.get("ev") == "gone" and src:
                    self._seen.get(src, {}).pop(tuple(ev.get("key") or ()),
                                                None)
                elif ev.get("ev") == "poll" and src:
                    self._prev[src] = float(ev["t_s"])

    # -- polls ---------------------------------------------------------------

    def poll(self, src: str, rows: list, t_s: float, day_range: int) -> dict:
        """One answered ask for src (its rows, every column) at t_s, asking
        day_range UTC dates. Returns the poll's counts."""
        t_s = float(t_s)
        self._start(t_s)
        self._last_poll_s = t_s
        seen = self._seen.setdefault(src, {})
        prev = self._prev.get(src)
        cur: dict = {}
        dup = 0
        for row in rows:
            k = _row_key(row)
            if k is None:
                continue
            if k in cur:
                dup += 1
                cur[k] = _prefer(cur[k], row)
            else:
                cur[k] = row
        new = changed = 0
        for k, row in cur.items():
            old = seen.get(k)
            if old is None:
                self._write(t_s, {"ev": "new", "t_s": t_s, "prev_s": prev,
                                  "src": src, "row": row})
                new += 1
            elif old != row:
                fields = sorted(f for f in set(old) | set(row)
                                if old.get(f) != row.get(f))
                self._write(t_s, {"ev": "changed", "t_s": t_s, "src": src,
                                  "fields": fields, "row": row})
                changed += 1
            seen[k] = row
        start = _date_minus(_utc_date(t_s), max(1, int(day_range)) - 1)
        in_window = [k for k in seen if k[2] >= start]
        missing = [k for k in in_window if k not in cur]
        skipped = None
        if missing:
            since_midnight = t_s % 86400.0
            if prev is None:
                skipped = "no earlier poll"
            elif min(since_midnight, 86400.0 - since_midnight) < \
                    LOG_MIDNIGHT_GUARD_S:
                skipped = "within 15 min of 00:00 UTC"
            elif len(missing) > max(LOG_GONE_SUSPECT_MIN,
                                    LOG_GONE_SUSPECT_FRAC * len(in_window)):
                skipped = (f"{len(missing)} of {len(in_window)} rows missing "
                           f"at once")
        gone = 0
        if skipped is None:
            for k in sorted(missing):
                self._write(t_s, {"ev": "gone", "t_s": t_s, "src": src,
                                  "key": list(k)})
                seen.pop(k, None)
                gone += 1
        for k in [k for k in seen if k[2] < start]:
            del seen[k]                       # aged out of the asked window
        counts = {"ev": "poll", "t_s": t_s, "src": src, "rows": len(cur),
                  "new": new, "changed": changed, "gone": gone, "dup": dup,
                  "gone_skipped": skipped}
        self._write(t_s, counts)
        self._prev[src] = t_s
        return counts

    def blind(self, src: str, t_s: float, error) -> None:
        """A failed ask for src: logged; the source's baseline is kept."""
        t_s = float(t_s)
        self._start(t_s)
        self._write(t_s, {"ev": "blind", "t_s": t_s, "src": src,
                          "error": str(error)[:300]})

    def flush(self, t_s: float) -> None:
        """Mirror the day files written since the last flush (gzipped, whole
        file) and drop local day files older than LOG_LOCAL_DAYS. Never
        raises: a failed upload is the mirror's LOUD log and stays dirty."""
        if self._mirror is not None:
            for date in sorted(self._dirty):
                path = self.path_for(date)
                try:
                    body = gzip.compress(path.read_bytes())
                except OSError as exc:
                    log.error("FIRMS log: cannot read %s (%s)", path, exc)
                    continue
                if self._mirror.upload(self.key_for(date), body, now_s=t_s):
                    self._dirty.discard(date)
        else:
            self._dirty.clear()
        keep = _date_minus(_utc_date(float(t_s)), LOG_LOCAL_DAYS - 1)
        for path in self._dir.glob("firms_*.jsonl"):
            date = path.stem[len("firms_"):]
            if date < keep and date not in self._dirty:
                try:
                    path.unlink()
                except OSError:
                    pass
        for date in [d for d in self._today if d < keep]:
            del self._today[date]

    def status(self) -> dict:
        return {"dir": str(self._dir),
                "mirror": None if self._mirror is None else
                self._mirror.status(),
                "restored": self._restored,
                "restore_notes": list(self._restore_why) or None,
                "last_poll_s": self._last_poll_s,
                "unmirrored_days": sorted(self._dirty),
                "keys": {s: len(v) for s, v in sorted(self._seen.items())},
                "days": {d: dict(c) for d, c in sorted(self._today.items())}}
