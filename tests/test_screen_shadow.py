"""tests/test_screen_shadow.py — HOTSPOT SCREENING STEP 0: SHADOW LABELS AND
THE RAW FIRMS LOG (founder decisions 2026-11-01, docs/review/
hotspot_screening_2026-11-01.md; contracts screen_labels, screen_rx_radius_m,
screen_rx_max_age_d, SAFETY SEMANTICS 43; watch --screen-labels,
--firms-log; wgresponse.screening).

  LOG       every row once with the poll that first returned it (prev_s
            bounds when it appeared); changed rows name their fields;
            withdrawn rows are "gone" only with an earlier poll, away from
            00:00 UTC and when a poll did not lose a fifth of the window at
            once; aged-out rows are not withdrawn; a duplicate keeps the
            reprocessed row; a failed ask is logged; a restart replays the
            mirror; a failed upload stays unmirrored.
  SITE      report B's industrial signature labels a known heat site; a
            daytime detection, a hot pixel, two dates, a busy date release
            it; unknown until every source answered its five days, or for a
            detection the history never saw.
  RX        an open prescribed-burn point within the radius and age, or a
            perimeter (inside or within its margin), labels heat; out,
            stale, far or unknown records do not.
  LOOKS     one look = grew None; more pixels, twice the heat, or new heat
            beyond 1 km = grew.
  FEEDS     WFIGS keep_rx keeps the records as labels: an RX point or
            polygon is never a fire (founder 2026-11-01), but an RX-typed
            polygon on a record of another type (an escaped burn re-typed
            WF) still joins it; the FIRMS log and memory change
            no incident and never blind the feed; the five-day ask runs once,
            then daily, and not again within 15 min of a failure.
  NOTHING   labels change no assessment, alert, ensemble or listing; off =
            no meta, note, status or state key.
  SURFACE   the heat rows, /api/state, the rule, the flags and notes, the
            page, the config."""
from __future__ import annotations

import calendar
import csv
import dataclasses
import gzip
import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import offmap_stack as om  # noqa: E402
import seeded_stack as ss  # noqa: E402
from tests.test_persist_s3 import FakeS3  # noqa: E402
from tests.test_threat import (  # noqa: E402
    StubFeed,
    StubRegistry,
    far_client,
    make_cfg,
    make_monitor,
    near_client,
)
from wgresponse import cli, feeds, geo, screening, worldview  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    FireIncident,
    IncidentSource,
    LatLon,
    ResponseConfig,
    validate_config,
)

PAGE = Path(__file__).resolve().parent.parent / "console" / "world.html"
#: 2026-10-30 12:00 UTC, away from midnight
T = float(calendar.timegm((2026, 10, 30, 12, 0, 0)))
DAY = 86400.0
N20, N21 = feeds.FirmsFeed.LIVE_SOURCES
SITE = LatLon(33.8, -118.2)          # a refinery-district site
COLS = ["latitude", "longitude", "bright_ti4", "scan", "track", "acq_date",
        "acq_time", "satellite", "instrument", "confidence", "version",
        "bright_ti5", "frp", "daynight"]


def _row(p: LatLon, t_s: float, frp=1.0, dn="N", version="2.0NRT",
         conf="n"):
    tm = __import__("time").gmtime(t_s)
    return {"latitude": f"{p.lat:.5f}", "longitude": f"{p.lon:.5f}",
            "bright_ti4": "300.0", "scan": "0.40", "track": "0.37",
            "acq_date": f"{tm.tm_year:04d}-{tm.tm_mon:02d}-{tm.tm_mday:02d}",
            "acq_time": f"{tm.tm_hour:02d}{tm.tm_min:02d}", "satellite": "N",
            "instrument": "VIIRS", "confidence": conf, "version": version,
            "bright_ti5": "290.0", "frp": f"{frp:.2f}", "daynight": dn}


def _csv(rows) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLS)
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _pt(p: LatLon, north_m=0.0, east_m=0.0) -> LatLon:
    q = geo.destination(p, 0.0, north_m) if north_m else p
    q = geo.destination(q, 90.0, east_m) if east_m else q
    return LatLon(round(q.lat, 5), round(q.lon, 5))


def _events(log: screening.FirmsPollLog, t_s: float) -> list:
    path = log.path_for(screening._utc_date(t_s))
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def _cluster(pts, ts, cid="firms:33.800,-118.200", frp=1.0):
    return FireIncident(
        incident_id=cid, name="FIRMS cluster", source=IncidentSource.FIRMS,
        origin=pts[0], updated_at_s=max(ts), active=True, hotspots=list(pts),
        source_ids={"firms": cid},
        meta={"n_detections": len(pts), "max_frp": frp,
              "hotspot_t_s": list(ts), "satellites": [N20]})


# ---------------------------------------------------------------------------
# LOG
# ---------------------------------------------------------------------------

def test_log_new_rows_carry_their_first_poll_and_the_previous_one(tmp_path):
    log = screening.FirmsPollLog(tmp_path)
    a, b, c = (_row(_pt(SITE, 100.0 * k), T - 3600.0) for k in range(3))
    first = log.poll(N20, [a, b], T, 2)
    second = log.poll(N20, [a, b, c], T + 300.0, 2)
    assert (first["new"], second["new"]) == (2, 1)
    new = [e for e in _events(log, T) if e["ev"] == "new"]
    assert [e["prev_s"] for e in new] == [None, None, T]
    assert new[-1]["row"] == c and new[-1]["t_s"] == T + 300.0
    assert [e["ev"] for e in _events(log, T)][0] == "start"


def test_log_a_changed_row_names_its_fields(tmp_path):
    log = screening.FirmsPollLog(tmp_path)
    a = _row(SITE, T - 3600.0, frp=1.0, version="2.0URT")
    log.poll(N20, [a], T, 2)
    b = dict(a, frp="1.40", version="2.0NRT")
    assert log.poll(N20, [b], T + 300.0, 2)["changed"] == 1
    (ch,) = [e for e in _events(log, T) if e["ev"] == "changed"]
    assert ch["fields"] == ["frp", "version"] and ch["row"] == b


def test_log_withdrawn_rows_are_gone_only_with_the_guards(tmp_path):
    rows = [_row(_pt(SITE, 100.0 * k), T - 3600.0) for k in range(30)]
    log = screening.FirmsPollLog(tmp_path / "a")
    log.poll(N20, rows, T, 2)
    out = log.poll(N20, rows[1:], T + 300.0, 2)
    assert out["gone"] == 1 and out["gone_skipped"] is None
    (g,) = [e for e in _events(log, T) if e["ev"] == "gone"]
    assert g["key"] == [rows[0][f] for f in ("latitude", "longitude",
                                             "acq_date", "acq_time")]
    # rows replayed without any poll of theirs: no earlier poll to compare
    torn = tmp_path / "b"
    torn.mkdir()
    (torn / "firms_2026-10-30.jsonl").write_text(json.dumps(
        {"ev": "new", "t_s": T - 600.0, "prev_s": None, "src": N20,
         "row": rows[0]}) + "\n", encoding="utf-8")
    out = screening.FirmsPollLog(torn).poll(N20, rows[1:], T, 2)
    assert out["gone"] == 0 and out["gone_skipped"] == "no earlier poll"
    # 30 of 100 rows missing at once (more than 20 and a fifth): suspect
    many = [_row(_pt(SITE, 50.0 * k), T - 3600.0) for k in range(100)]
    big = screening.FirmsPollLog(tmp_path / "c")
    big.poll(N20, many, T, 2)
    out = big.poll(N20, many[30:], T + 300.0, 2)
    assert out["gone"] == 0 and "missing at once" in out["gone_skipped"]
    assert big.poll(N20, many, T + 600.0, 2)["new"] == 0      # still seen
    # within 15 min of 00:00 UTC the asked window rolls
    mid = float(calendar.timegm((2026, 10, 31, 0, 5, 0)))
    near = [_row(_pt(SITE, 100.0 * k), mid - 3600.0) for k in range(30)]
    late = screening.FirmsPollLog(tmp_path / "d")
    late.poll(N20, near, mid - 300.0, 2)
    out = late.poll(N20, near[1:], mid, 2)
    assert out["gone"] == 0 and out["gone_skipped"].startswith("within 15")


def test_log_rows_aging_out_of_the_window_are_not_withdrawn(tmp_path):
    log = screening.FirmsPollLog(tmp_path)
    old = _row(SITE, T - 2 * DAY)          # two UTC dates back
    today = _row(_pt(SITE, 300.0), T - 3600.0)
    log.poll(N20, [old, today], T - 300.0, 3)
    out = log.poll(N20, [today], T, 2)     # the window now holds 2 dates
    assert out["gone"] == 0 and out["gone_skipped"] is None
    assert log.status()["keys"] == {N20: 1}


def test_log_a_duplicate_key_keeps_the_reprocessed_row(tmp_path):
    log = screening.FirmsPollLog(tmp_path)
    urt = _row(SITE, T - 600.0, version="2.0URT")
    nrt = dict(urt, version="2.0NRT", frp="1.10")
    out = log.poll(N20, [nrt, urt], T, 2)
    assert out["dup"] == 1 and out["new"] == 1
    (new,) = [e for e in _events(log, T) if e["ev"] == "new"]
    assert new["row"]["version"] == "2.0NRT"


def test_log_a_failed_ask_is_logged_and_keeps_the_baseline(tmp_path):
    log = screening.FirmsPollLog(tmp_path)
    a = _row(SITE, T - 3600.0)
    log.poll(N20, [a], T, 2)
    log.blind(N20, T + 300.0, RuntimeError("HTTP 503"))
    assert log.poll(N20, [a], T + 600.0, 2)["new"] == 0
    (b,) = [e for e in _events(log, T) if e["ev"] == "blind"]
    assert b["error"] == "HTTP 503" and b["src"] == N20


def test_log_a_restart_replays_the_mirror(tmp_path):
    s3 = FakeS3()
    uri = "s3://bucket/watch"
    a, b = _row(SITE, T - 3600.0), _row(_pt(SITE, 300.0), T - 1800.0)
    one = screening.FirmsPollLog(tmp_path / "one", s3_uri=uri, s3_client=s3)
    one.poll(N20, [a, b], T, 2)
    one.flush(T)
    key = ("bucket", "watch/firms_log/firms_2026-10-30.jsonl.gz")
    assert key in s3.objects
    assert gzip.decompress(s3.objects[key]).decode().count('"ev":"new"') == 2
    two = screening.FirmsPollLog(tmp_path / "two", s3_uri=uri, s3_client=s3)
    out = two.poll(N20, [a, b], T + 3600.0, 2)
    assert out["new"] == 0 and two.status()["restored"] is True
    c = _row(_pt(SITE, 600.0), T + 1800.0)
    two.poll(N20, [a, b, c], T + 3900.0, 2)
    evs = _events(two, T)           # the restored day file, carried on
    starts = [i for i, e in enumerate(evs) if e["ev"] == "start"]
    assert len(starts) == 2 and evs[starts[1]]["restored"] is True
    new = [e for e in evs[starts[1]:] if e["ev"] == "new"]
    assert [e["prev_s"] for e in new] == [T + 3600.0]


def test_log_without_the_mirror_starts_on_an_unknown_baseline(tmp_path):
    log = screening.FirmsPollLog(tmp_path)
    log.poll(N20, [_row(SITE, T - 3600.0)], T, 2)
    (new,) = [e for e in _events(log, T) if e["ev"] == "new"]
    assert new["prev_s"] is None
    st = log.status()
    assert st["mirror"] is None and st["restored"] is True


def test_log_a_failed_upload_stays_unmirrored(tmp_path):
    s3 = FakeS3(fail_put=True)
    log = screening.FirmsPollLog(tmp_path, s3_uri="s3://bucket/w",
                                 s3_client=s3)
    log.poll(N20, [_row(SITE, T - 3600.0)], T, 2)
    log.flush(T)
    st = log.status()
    assert st["unmirrored_days"] == ["2026-10-30"]
    assert st["mirror"]["last_upload_ok"] is False
    s3.fail_put = False
    log.flush(T + 60.0)
    assert log.status()["unmirrored_days"] == []


def test_log_a_failed_restore_never_overwrites_the_day(tmp_path):
    s3 = FakeS3()
    uri = "s3://bucket/w"
    one = screening.FirmsPollLog(tmp_path / "one", s3_uri=uri, s3_client=s3)
    one.poll(N20, [_row(SITE, T - 3600.0)], T, 2)
    one.flush(T)
    day = ("bucket", "w/firms_log/firms_2026-10-30.jsonl.gz")
    before = s3.objects[day]
    s3.fail_get = True                     # the mirror cannot be read
    two = screening.FirmsPollLog(tmp_path / "two", s3_uri=uri, s3_client=s3)
    two.poll(N20, [_row(SITE, T - 3600.0)], T + 3600.0, 2)
    two.flush(T + 3600.0)
    st = two.status()
    assert st["restored"] is False and "restore failed" in \
        st["restore_notes"][0]
    assert s3.objects[day] == before
    side = ("bucket", f"w/firms_log/firms_2026-10-30.unmerged-"
                      f"{int(T + 3600.0)}.jsonl.gz")
    assert side in s3.objects


# ---------------------------------------------------------------------------
# SITE
# ---------------------------------------------------------------------------

def _night(p: LatLon, day: int, frp=1.0, hour=9):
    """A detection at 09:00 UTC (night in California) `day` dates back."""
    t = float(calendar.timegm((2026, 10, 30 - day, hour, 0, 0)))
    return t, _row(p, t, frp=frp, dn="N" if hour < 13 else "D")


def _memory(rows_n20, rows_n21=(), now=T):
    mem = screening.HeatSiteMemory((N20, N21))
    mem.add(N20, [r for _, r in rows_n20], now, history=True)
    mem.add(N21, [r for _, r in rows_n21], now, history=True)
    return mem


def test_a_known_heat_site_three_nights_low_frp():
    hist = [_night(SITE, d) for d in (3, 2, 1)]
    t0, r0 = _night(SITE, 0)
    mem = _memory(hist + [(t0, r0)])
    lab = screening.label(_cluster([SITE], [t0]), mem, [])
    assert lab["site"]["static"] is True and "heat_site" in lab["labels"]
    assert lab["site"]["dates"] == 4 and "at night" in lab["site"]["why"]


def test_a_heat_site_is_released_by_day_heat_or_too_little_history():
    t0, r0 = _night(SITE, 0)
    hist = [_night(SITE, d) for d in (3, 2, 1)]
    # the cluster's own detection by day
    td, rd = _night(SITE, 0, hour=20)
    lab = screening.label(_cluster([SITE], [td]),
                          _memory(hist + [(t0, r0), (td, rd)]), [])
    assert lab["site"] == {"static": False, "why": "a daytime detection"}
    # a hot pixel at the site
    hot = [_night(SITE, 3), _night(SITE, 2), _night(SITE, 1, frp=7.5)]
    lab = screening.label(_cluster([SITE], [t0]), _memory(hot + [(t0, r0)]),
                          [])
    assert lab["site"]["static"] is False and "7.5 MW" in lab["site"]["why"]
    # only two dates
    two = [_night(SITE, 1)]
    lab = screening.label(_cluster([SITE], [t0]), _memory(two + [(t0, r0)]),
                          [])
    assert lab["site"]["static"] is False and "hot on 2" in lab["site"]["why"]
    # a busy date: nine detections in one night
    busy = [_night(_pt(SITE, 20.0 * k), 1) for k in range(9)]
    lab = screening.label(_cluster([SITE], [t0]),
                          _memory(hist[:2] + busy + [(t0, r0)]), [])
    assert lab["site"]["static"] is False and "9 detections" in \
        lab["site"]["why"]


def test_a_heat_site_is_unknown_until_both_sources_answer_five_days():
    hist = [_night(SITE, d) for d in (3, 2, 1, 0)]
    mem = screening.HeatSiteMemory((N20, N21))
    mem.add(N20, [r for _, r in hist], T, history=True)
    lab = screening.label(_cluster([SITE], [hist[-1][0]]), mem, [])
    assert lab["site"]["static"] is None and "not loaded" in lab["site"]["why"]
    assert "heat_site" not in lab["labels"]
    # a detection the history never saw
    mem.add(N21, [], T, history=True)
    other = _pt(SITE, 5000.0)
    lab = screening.label(_cluster([other], [T - 600.0]), mem, [])
    assert lab["site"]["static"] is None
    assert screening.label(_cluster([SITE], [T]), None, [])["site"][
        "static"] is None


# ---------------------------------------------------------------------------
# RX
# ---------------------------------------------------------------------------

def _rx(loc, discovered_s, perimeter=None, out_s=None, name="Unit 7 Rx"):
    meta = {} if out_s is None else {"out_at_s": out_s}
    return FireIncident(incident_id="rx-1", name=name,
                        source=IncidentSource.WFIGS, origin=loc,
                        updated_at_s=discovered_s, discovered_at_s=discovered_s,
                        active=True, perimeter=perimeter, meta=meta,
                        source_ids={"wfigs": "rx-1"})


def _rx_label(rec, heat: LatLon, cfg=None):
    cfg = cfg or ResponseConfig()
    idx = screening.prepare_rx([rec] if rec is not None else [], T, cfg)
    return screening.label(_cluster([heat], [T - 600.0]), None, idx)["rx"]


def test_a_registered_burn_point_labels_heat_within_radius_and_age():
    lab = _rx_label(_rx(SITE, T - 5 * DAY), _pt(SITE, 1000.0))
    assert lab["match"] is True and lab["kind"] == "point"
    assert 950 <= lab["distance_m"] <= 1050 and "Unit 7 Rx" in lab["why"]
    assert _rx_label(_rx(SITE, T - 30 * DAY), _pt(SITE, 1000.0))["match"] \
        is False                                         # discovered too long ago
    assert _rx_label(_rx(SITE, T - 5 * DAY, out_s=T - DAY),
                     _pt(SITE, 1000.0))["match"] is False   # out
    assert _rx_label(_rx(SITE, T - 5 * DAY), _pt(SITE, 4000.0))["match"] \
        is False                                         # beyond 3 km
    unknown = screening.label(_cluster([SITE], [T]), None, None)["rx"]
    assert unknown["match"] is None


def test_a_registered_burn_perimeter_labels_heat_inside_and_near():
    ring = [_pt(SITE, dy, dx) for dx, dy in ((-1000, -1000), (1000, -1000),
                                             (1000, 1000), (-1000, 1000))]
    rec = _rx(SITE, T - 200 * DAY, perimeter=[ring])
    inside = _rx_label(rec, SITE)
    assert inside["match"] is True and inside["why"].endswith(
        "inside its perimeter")
    assert _rx_label(rec, _pt(SITE, 1800.0))["match"] is True   # 800 m out
    assert _rx_label(rec, _pt(SITE, 2500.0))["match"] is False


# ---------------------------------------------------------------------------
# LOOKS
# ---------------------------------------------------------------------------

def _look_memory(rows_by_src):
    mem = screening.HeatSiteMemory((N20, N21))
    for src in (N20, N21):
        mem.add(src, rows_by_src.get(src, []), T, history=True)
    return mem


def test_one_look_has_no_growth_to_read():
    t1 = T - 3000.0
    mem = _look_memory({N21: [_row(SITE, t1, dn="D")]})
    lab = screening.label(_cluster([SITE], [t1]), mem, [])
    assert lab["grew"] is None and lab["growth"] == "one look so far"
    assert [lk["src"] for lk in lab["looks"]] == ["N21"]


def test_growth_between_looks():
    t1, t2 = T - 3000.0, T - 0.0                    # N21, then N20 50 min on
    first = [_pt(SITE)]
    more = [_pt(SITE, 375.0 * k) for k in range(3)]
    mem = _look_memory({N21: [_row(p, t1, dn="D") for p in first],
                        N20: [_row(p, t2, dn="D") for p in more]})
    lab = screening.label(_cluster(first + more, [t1] + [t2] * 3), mem, [])
    assert lab["grew"] is True and "1 -> 3 pixels" in lab["growth"]
    assert [(lk["src"], lk["n"]) for lk in lab["looks"]] == [("N21", 1),
                                                             ("N20", 3)]
    # twice the heat on one pixel
    mem = _look_memory({N21: [_row(SITE, t1, frp=2.0, dn="D")],
                        N20: [_row(SITE, t2, frp=6.0, dn="D")]})
    lab = screening.label(_cluster([SITE, SITE], [t1, t2]), mem, [])
    assert lab["grew"] is True and "2.0 -> 6.0 MW" in lab["growth"]
    # new heat 1.2 km from the first look
    far = _pt(SITE, 1200.0)
    mem = _look_memory({N21: [_row(SITE, t1, dn="D")],
                        N20: [_row(far, t2, dn="D")]})
    lab = screening.label(_cluster([SITE, far], [t1, t2]), mem, [])
    assert lab["grew"] is True and "km from the earlier looks" in lab["growth"]
    # the same again: no growth
    mem = _look_memory({N21: [_row(SITE, t1, dn="D")],
                        N20: [_row(SITE, t2, dn="D")]})
    lab = screening.label(_cluster([SITE, SITE], [t1, t2]), mem, [])
    assert lab["grew"] is False and "grew" not in lab["labels"]


# ---------------------------------------------------------------------------
# FEEDS
# ---------------------------------------------------------------------------

def _arcgis(features):
    return json.dumps({"features": features}).encode("utf-8")


def _wfigs_http(asked):
    ms = int((T - 2 * DAY) * 1000)
    points = [
        {"attributes": {"IncidentName": "Ridge", "IrwinID": "{WF-1}",
                        "IncidentTypeCategory": "WF",
                        "ModifiedOnDateTime_dt": ms,
                        "FireDiscoveryDateTime": ms},
         "geometry": {"x": -118.0, "y": 34.0}},
        {"attributes": {"IncidentName": "Unit 7 Rx", "IrwinID": "{RX-1}",
                        "IncidentTypeCategory": "RX",
                        "ModifiedOnDateTime_dt": ms,
                        "FireDiscoveryDateTime": ms},
         "geometry": {"x": -118.2, "y": 33.8}}]
    ring = [[-117.51, 33.49], [-117.49, 33.49], [-117.49, 33.51],
            [-117.51, 33.51], [-117.51, 33.49]]
    perims = [{"attributes": {"poly_IncidentName": "Mesa Rx",
                              "poly_IRWINID": "{RX-2}",
                              "attr_IncidentTypeCategory": "RX",
                              "poly_DateCurrent": ms,
                              "attr_ModifiedOnDateTime_dt": ms},
               "geometry": {"rings": [ring]}}]

    def get(url, headers):
        asked.append(url)
        return 200, _arcgis(points if "Incident_Locations" in url else perims)
    return get


def test_wfigs_keep_rx_keeps_the_records_as_labels_never_fires():
    asked: list = []
    keep = feeds.WfigsFeed(http_get=_wfigs_http(asked), keep_rx=True)
    out = keep.fetch_active()
    assert keep.keeps_rx and keep.last_fetch_ok
    assert sorted(r.name for r in keep.rx_records) == ["Mesa Rx", "Unit 7 Rx"]
    assert "attr_IncidentTypeCategory" in asked[1]
    assert [i.name for i in out] == ["Ridge"]          # neither burn a fire
    plain_asked: list = []
    plain = feeds.WfigsFeed(http_get=_wfigs_http(plain_asked))
    legacy = plain.fetch_active()
    assert plain.rx_records is None and not plain.keeps_rx
    assert "attr_IncidentTypeCategory" not in plain_asked[1]
    # knob off: byte-identical — the polygon still enters as a fire
    assert sorted(i.name for i in legacy) == ["Mesa Rx", "Ridge"]


def test_wfigs_an_escaped_burn_keeps_its_polygon():
    ms = int((T - DAY) * 1000)
    ring = [[-118.01, 33.99], [-117.99, 33.99], [-117.99, 34.01],
            [-118.01, 34.01], [-118.01, 33.99]]

    def get(url, headers):
        if "Incident_Locations" in url:
            return 200, _arcgis([{"attributes": {
                "IncidentName": "Unit 9", "IrwinID": "{ESC-1}",
                "IncidentTypeCategory": "WF", "ModifiedOnDateTime_dt": ms},
                "geometry": {"x": -118.0, "y": 34.0}}])
        return 200, _arcgis([{"attributes": {
            "poly_IncidentName": "Unit 9", "poly_IRWINID": "{ESC-1}",
            "attr_IncidentTypeCategory": "RX", "poly_DateCurrent": ms,
            "attr_ModifiedOnDateTime_dt": ms},
            "geometry": {"rings": [ring]}}])
    feed = feeds.WfigsFeed(http_get=get, keep_rx=True)
    (fire,) = feed.fetch_active()
    assert fire.name == "Unit 9" and fire.perimeter
    assert [r.name for r in feed.rx_records] == ["Unit 9"]


def test_wfigs_a_partial_fetch_keeps_no_records():
    def get(url, headers):
        if "Perimeters" in url:
            return 500, b""
        return _wfigs_http([])(url, headers)
    feed = feeds.WfigsFeed(http_get=get, keep_rx=True)
    feed.fetch_active()
    assert feed.last_fetch_ok is False and feed.rx_records is None


def _firms_http(asked, rows_2, rows_5, fail=()):
    def get(url, headers):
        asked.append(url)
        src = next(s for s in (N20, N21) if f"/{s}/" in url)
        if src in fail:
            return 503, b""
        rows = rows_5 if url.endswith("/5") else rows_2
        return 200, _csv(rows.get(src, [])).encode("utf-8")
    return get


def _firms_rows():
    hist = [r for _, r in (_night(SITE, d) for d in (3, 2, 1))]
    now_rows = {N20: [_row(SITE, T - 1800.0)],
                N21: [_row(_pt(SITE, 300.0), T - 4800.0)]}
    five = {N20: hist + now_rows[N20], N21: now_rows[N21]}
    return now_rows, five


def test_firms_log_and_memory_change_no_incident(tmp_path):
    now_rows, five = _firms_rows()
    plain = feeds.FirmsFeed.live("k" * 32, http_get=_firms_http([], now_rows,
                                                                 five),
                                 clock=lambda: T)
    asked: list = []
    log = screening.FirmsPollLog(tmp_path)
    mem = screening.HeatSiteMemory(feeds.FirmsFeed.LIVE_SOURCES)
    shadow = feeds.FirmsFeed.live("k" * 32,
                                  http_get=_firms_http(asked, now_rows, five),
                                  clock=lambda: T, recorder=log, memory=mem)
    for _ in range(2):
        a, b = plain.fetch_active(), shadow.fetch_active()
        assert [(i.incident_id, i.hotspots, i.meta) for i in a] == \
            [(i.incident_id, i.hotspots, i.meta) for i in b]
        assert shadow.last_fetch_ok and plain.describe() == shadow.describe()
    assert sum(u.endswith("/5") for u in asked) == 2      # once per source
    assert mem.ready() and shadow.site_memory is mem and shadow.poll_log is log
    assert log.status()["days"]["2026-10-30"]["new"] == 2


def test_firms_a_shadow_failure_never_blinds_the_feed(tmp_path):
    class Boom:
        def poll(self, *a):
            raise RuntimeError("disk full")

        def blind(self, *a):
            raise RuntimeError("disk full")

        def flush(self, *a):
            raise RuntimeError("disk full")
    now_rows, five = _firms_rows()
    feed = feeds.FirmsFeed.live("k" * 32, http_get=_firms_http([], now_rows,
                                                                five),
                                clock=lambda: T, recorder=Boom())
    assert len(feed.fetch_active()) == 1 and feed.last_fetch_ok


def test_firms_a_failed_source_is_a_blind_log_event(tmp_path):
    now_rows, five = _firms_rows()
    log = screening.FirmsPollLog(tmp_path)
    feed = feeds.FirmsFeed.live("k" * 32,
                                http_get=_firms_http([], now_rows, five,
                                                     fail=(N21,)),
                                clock=lambda: T, recorder=log)
    feed.fetch_active()
    assert feed.last_fetch_ok is False
    kinds = [(e["ev"], e.get("src")) for e in _events(log, T)]
    assert ("blind", N21) in kinds and ("poll", N20) in kinds


def test_the_five_day_ask_runs_daily_and_waits_after_a_failure():
    mem = screening.HeatSiteMemory((N20,))
    assert mem.wants_history(N20, T)
    mem.history_failed(N20, T, "HTTP 503")
    assert not mem.wants_history(N20, T + 600.0)
    assert mem.wants_history(N20, T + 900.0)
    mem.add(N20, [], T + 900.0, history=True)
    assert not mem.wants_history(N20, T + 900.0 + DAY - 1.0)
    assert mem.wants_history(N20, T + 900.0 + DAY)


# ---------------------------------------------------------------------------
# NOTHING changes / off
# ---------------------------------------------------------------------------

def _near_cluster():
    from tests.test_hotspots import _cluster as hs_cluster
    from tests.test_threat import ORIGIN
    return hs_cluster([geo.destination(ORIGIN, 0.0, 500.0),
                       geo.destination(ORIGIN, 0.0, 700.0)])


def _cycle(cfg, cl):
    mon = make_monitor(feeds=[StubFeed([cl])], cfg=cfg,
                       registry=StubRegistry([near_client(), far_client()]))
    from tests.test_threat import NOW
    return mon, mon.cycle(NOW)


def test_labels_change_nothing_and_off_adds_nothing():
    for heat_only in (False, True):
        off_m, off = _cycle(make_cfg(hotspot_heat_only=heat_only),
                            _near_cluster())
        on_m, on = _cycle(make_cfg(hotspot_heat_only=heat_only,
                                   screen_labels=True), _near_cluster())
        assert [(a.client_id, a.incident_id, a.eta) for a in off.assessments] \
            == [(a.client_id, a.incident_id, a.eta) for a in on.assessments]
        assert [(e.client_id, e.incident_id, e.state, e.message)
                for e in off.events] == \
            [(e.client_id, e.incident_id, e.state, e.message)
             for e in on.events]
        assert sorted(off.ensembles) == sorted(on.ensembles)
        assert [n for n in on.notes if not n.startswith("screening")] == \
            off.notes
        assert [i.incident_id for i in off.incidents] == \
            [i.incident_id for i in on.incidents]
        assert all("screen" not in (i.meta or {}) for i in off.incidents)
        assert off_m.screen_status() is None
        (lab,) = [i.meta["screen"] for i in on.incidents
                  if "screen" in (i.meta or {})]
        assert lab["site"]["static"] is None and lab["rx"]["match"] is None
        st = on_m.screen_status()
        assert st["clusters"] == 1 and st["site_unknown"] == 1


def test_a_reported_fire_is_never_labelled():
    from tests.test_threat import ORIGIN, incident
    _m, res = _cycle(make_cfg(screen_labels=True),
                     incident("wf-1", ORIGIN))
    assert all("screen" not in (i.meta or {}) for i in res.incidents)


# ---------------------------------------------------------------------------
# SURFACE
# ---------------------------------------------------------------------------

def test_the_labels_ride_on_the_heat_rows_and_the_state(tmp_path):
    near_b = FireIncident(
        incident_id="firms:39.000,-120.000", name="FIRMS cluster",
        source=IncidentSource.FIRMS,
        origin=geo.destination(om.CB_LOC, 0.0, 3000.0),
        updated_at_s=ss.T0 - 900.0, active=True,
        hotspots=[geo.destination(om.CB_LOC, 0.0, 3000.0)],
        source_ids={"firms": "firms:39.000,-120.000"},
        meta={"hotspot_t_s": [ss.T0 - 900.0], "satellites": [N21],
              "max_frp": 7.0})
    cfg = ss.small_cfg(fuse_hotspots=True, hotspot_heat_only=True,
                       screen_labels=True)
    daemon, monitor, _feed, _engine = om.make_stack(
        str(tmp_path), cfg, incidents=[om.slow_incident(), near_b])
    daemon.run_cycle()
    st = daemon.state()
    assert st["screen"]["rule"] == "shadow" and st["screen"]["clusters"] == 1
    (row,) = st["heat_near_homes"]["near"]
    assert row["screen"]["grew"] is None
    inc = next(r for r in st["incidents"] if r["incident_id"] == near_b.incident_id)
    assert inc["hotspots"]["screen"]["labels"] == []
    assert st["hotspot_rule"]["labels"]["heat_site"]["min_dates"] == 3
    assert "firms_log" not in st
    off, *_ = om.make_stack(str(tmp_path / "off"),
                            ss.small_cfg(fuse_hotspots=True,
                                         hotspot_heat_only=True),
                            incidents=[om.slow_incident(), near_b])
    off.run_cycle()
    st = off.state()
    assert "screen" not in st and "labels" not in st["hotspot_rule"]
    assert "screen" not in st["heat_near_homes"]["near"][0]


def test_the_watch_flags_arm_the_labels_and_the_log(tmp_path, monkeypatch):
    monkeypatch.setenv("FIRMS_MAP_KEY", "k" * 32)
    args = cli._build_parser().parse_args(
        ["watch", "--aoi", "data/aoi_conus.json", "--fuse-hotspots",
         "--screen-labels", "--firms-log"])
    cfg, notes = cli._screen_cfg(ResponseConfig(), args)
    assert cfg.screen_labels and len(notes) == 2
    assert notes[0].startswith("SCREENING STEP 0") and "INERT" not in notes[0]
    assert notes[1].startswith("FIRMS RAW LOG") and "SAFETY SEMANTICS 43" in \
        notes[1]
    plain = cli._build_parser().parse_args(
        ["watch", "--aoi", "data/aoi_conus.json"])
    assert cli._screen_cfg(ResponseConfig(), plain) == (ResponseConfig(), [])
    log = screening.FirmsPollLog(tmp_path)
    stack, _ = cli._live_feed_stack("k" * 32, live=True, screen=True,
                                    recorder=log)
    wfigs, firms = stack[0], stack[-1]
    assert wfigs.keeps_rx and firms.poll_log is log
    assert firms.site_memory is not None
    stack, _ = cli._live_feed_stack("k" * 32, live=True)
    assert not stack[0].keeps_rx and stack[-1].poll_log is None
    assert stack[-1].site_memory is None
    monkeypatch.delenv("FIRMS_MAP_KEY")
    _cfg, notes = cli._screen_cfg(ResponseConfig(), args)
    assert "INERT" in notes[0] and "INERT" in notes[1]


def test_the_rule_lists_the_labels_only_when_armed():
    rule = worldview.hotspot_rule(make_cfg(fuse_hotspots=True,
                                           screen_labels=True))
    assert rule["labels"]["rx_burn"]["radius_m"] == 3000.0
    assert "labels" not in worldview.hotspot_rule(make_cfg(fuse_hotspots=True))


def test_the_page_shows_the_labels():
    html = PAGE.read_text(encoding="utf-8")
    assert "function screenChip(d)" in html
    assert "heatNearChip(d) + screenChip(d)" in html
    assert "screenLine(h.screen)" in html
    assert "SAFETY SEMANTICS 43" in html


def test_config_defaults_and_validation():
    cfg = ResponseConfig()
    assert (cfg.screen_labels, cfg.screen_rx_radius_m,
            cfg.screen_rx_max_age_d) == (False, 3000.0, 21.0)
    assert validate_config(cfg) == []
    for name, bad in (("screen_rx_radius_m", (-1.0, 20_001.0, float("nan"))),
                      ("screen_rx_max_age_d", (-1.0, 366.0, float("inf")))):
        for v in bad:
            probs = validate_config(dataclasses.replace(cfg, **{name: v}))
            assert any(name in p for p in probs), (name, v)
