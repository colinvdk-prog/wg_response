"""Feed-sanity study (2026-09-27) -- STEP 2: measure the hazards and what the
SAFETY SEMANTICS 18 guard changes, on RECORDED payloads only (no network).

Inputs (all recorded earlier, read-only):
  * out/feed_sanity/rec_*/      the live layers as the watcher reads them
                                (fs_record.py), replayed through the REAL
                                adapters + fusion (fs_common.load_recording)
  * out/feed_sanity/watcher_s3/fire_beliefs.json
                                the deployed watcher's belief file (copied
                                read-only from its S3 mirror)
  * the belief-vs-physics study's WFIGS Daily rows (out/bvp_data/rows/,
    11 fires, every published edit in OBJECTID = publication order)

Parts:
  A  the live snapshot(s): time sanity on every time field the adapters read
     (and the ones they do not), perimeter duplicates, the poly_DateCurrent -
     poly_PolygonDateTime re-stamp lag, area figure vs geometry, polygon vs
     its record's size, multi-source size disagreement in fusion, and the
     verdicts feedsanity would give each fused perimeter incident; between
     consecutive recordings, re-stamps / geometry changes / size changes.
  B  the Daily history replayed through the REAL FireBeliefStore (tracking;
     no spread, so windows are unmodeled: known / rapid / window shapes,
     not the posterior), legacy vs cfg.feed_sanity, one observation cycle
     per published row at its poly_DateCurrent + 10 min (the watcher's poll
     lag). Rows become FireIncidents exactly as feeds.WfigsFeed builds them
     (perimeter_at_s = poly_DateCurrent, perimeter_acres = poly_GISAcres,
     reported = attr_IncidentSize at attr_ModifiedOnDateTime_dt); the polygon
     is a circle of the row's GIS area (the rows carry no geometry here).
  C  the seed side on the same history: which rows the monitor would replace
     with the fire's last plausible polygon, and the area it would seed.
  D  the deployed watcher's belief file: its windows, classified.

Writes out/feed_sanity/measure.json and prints the tables the report cites.

Usage (repo root):
    py -3 scratch_review/feed_sanity/fs_measure.py
"""
from __future__ import annotations

import collections
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fs_common as C  # noqa: E402
from wgresponse import feedsanity, geo  # noqa: E402
from wgresponse import fire_belief as FB  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    FireIncident,
    IncidentSource,
    LatLon,
    ResponseConfig,
)

H = 3600.0
DAY = 86400.0
OUT = os.path.join(C.OUT_ROOT, "measure.json")
# The store keeps at most FB.MAX_WINDOWS windows per fire (oldest dropped):
# counting from the kept list would undercount the arm that forms more. The
# cap only bounds memory (the rapid lookback and the half-life never reach
# 64 windows here), so the measurement lifts it to count every window formed.
FB.MAX_WINDOWS = 10 ** 9
CFG_OFF = ResponseConfig(fire_belief_track=True)
CFG_ON = ResponseConfig(fire_belief_track=True, feed_sanity=True)


def _q(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    return xs[min(len(xs) - 1, max(0, int(math.ceil(q * len(xs))) - 1))]


def _irwin(a) -> str:
    return (a.get("poly_IRWINID") or a.get("attr_IrwinID") or a.get("IrwinID")
            or "").strip("{}").lower()


# ---------------------------------------------------------------------------
# A. the live snapshot(s)
# ---------------------------------------------------------------------------

def part_a(rec) -> dict:
    T = rec["t_s"]
    out = {"recording": os.path.basename(rec["dir"]), "t_utc": C.fmt_t(T),
           "sources": {k: len(v) for k, v in rec["sources"].items()},
           "fused": len(rec["fused"])}
    per = rec["side"].get("perimeters", [])
    pts = rec["side"].get("points", [])
    # time sanity: the fields the adapters read (poly_DateCurrent,
    # attr_ModifiedOnDateTime_dt, ModifiedOnDateTime_dt, FIRIS
    # poly_DateCurrent, CAL FIRE Updated) and poly_PolygonDateTime (not read)
    def tstats(vals, disc=None):
        fut = sum(1 for t in vals if t is not None and t > T + H)
        old = sum(1 for t in vals if t is not None and t < T - 365 * DAY)
        pre = 0
        if disc is not None:
            pre = sum(1 for t, d in zip(vals, disc)
                      if t is not None and d is not None and t < d - DAY)
        return {"n": sum(1 for t in vals if t is not None), "future": fut,
                "over_a_year_old": old, "before_discovery": pre,
                "missing": sum(1 for t in vals if t is None)}
    disc = [C.ms(a.get("attr_FireDiscoveryDateTime")) for a in per]
    out["time"] = {
        "wfigs_poly_DateCurrent (read)": tstats(
            [C.ms(a.get("poly_DateCurrent")) for a in per], disc),
        "wfigs_attr_ModifiedOnDateTime_dt (read)": tstats(
            [C.ms(a.get("attr_ModifiedOnDateTime_dt")) for a in per], disc),
        "wfigs_poly_PolygonDateTime (NOT read)": tstats(
            [C.ms(a.get("poly_PolygonDateTime")) for a in per], disc),
        "wfigs_points_ModifiedOnDateTime_dt (read)": tstats(
            [C.ms(a.get("ModifiedOnDateTime_dt")) for a in pts],
            [C.ms(a.get("FireDiscoveryDateTime")) for a in pts]),
        "firis_poly_DateCurrent (read)": tstats(
            [C.ms((f.get("attributes") or {}).get("poly_DateCurrent"))
             for f in rec["raw"].get("firis", [])]),
        "calfire_Updated (read)": tstats(
            [i.updated_at_s or None for i in rec["sources"]["calfire"]],
            [i.discovered_at_s for i in rec["sources"]["calfire"]]),
    }
    # perimeter duplicates per IRWIN in the WFIGS Current layer
    ids = collections.Counter(_irwin(a) for a in per)
    out["wfigs_duplicate_irwin_polygons"] = sum(1 for v in ids.values()
                                                if v > 1)
    # the re-stamp lag: poly_DateCurrent (the adapter's polygon time) after
    # poly_PolygonDateTime (the depiction time)
    lags = [(C.ms(a["poly_DateCurrent"]) - C.ms(a["poly_PolygonDateTime"])) / H
            for a in per if a.get("poly_DateCurrent")
            and a.get("poly_PolygonDateTime")]
    out["restamp_lag_h"] = {"n": len(lags), "median": _q(lags, 0.5),
                            "p90": _q(lags, 0.9), "max": max(lags or [0]),
                            "over_72h": sum(1 for x in lags if x > 72)}
    # area figure vs geometry, per source (the polygons the engine seeds)
    buckets = collections.Counter()
    off2 = []
    mm = {_irwin(a): a.get("poly_MapMethod") for a in per}
    for key in ("wfigs", "firis"):
        for inc in rec["sources"][key]:
            fig = (inc.meta or {}).get("perimeter_acres")
            if not inc.perimeter or not fig:
                continue
            geom = FB.rings_area_m2(inc.perimeter) / C.ACRE_M2
            r = fig / geom if geom > 0 else float("inf")
            b = ("within 5 %" if 0.95 <= r <= 1.05 else
                 "within 2x" if 0.5 <= r <= 2.0 else "more than 2x off")
            buckets[(key, b)] += 1
            if b == "more than 2x off":
                off2.append({"source": key, "name": inc.name,
                             "figure_ac": round(fig, 2),
                             "geometry_ac": round(geom, 2),
                             "map_method": mm.get(inc.irwin_id or "")})
    out["area_figure_vs_geometry"] = {f"{k}: {b}": v
                                      for (k, b), v in sorted(buckets.items())}
    out["area_figure_off_2x"] = off2
    # fused perimeter incidents: the guard's verdicts on this snapshot
    cfg = ResponseConfig(feed_sanity=True)
    verdicts = []
    for inc in rec["fused"]:
        if not inc.perimeter:
            continue
        meta = inc.meta or {}
        pat = meta.get("perimeter_at_s") or inc.updated_at_s
        geom = FB.rings_area_m2(inc.perimeter) / C.ACRE_M2
        why_t = feedsanity.time_problem(pat, inc.discovered_at_s, T, cfg)
        why_c = feedsanity.collapse_problem(geom, meta.get("reported_acres"),
                                            cfg)
        fig = meta.get("perimeter_acres")
        fig_bad = fig is not None and not feedsanity.feed_area_stands(
            fig, geom, cfg)
        stale = T - inc.updated_at_s > 168 * H
        if why_t or why_c or fig_bad:
            verdicts.append({"incident": inc.incident_id, "name": inc.name,
                             "sources": sorted(inc.source_ids),
                             "stale_gated": stale, "time": why_t,
                             "collapse": why_c,
                             "area_figure_off": bool(fig_bad),
                             "geometry_ac": round(geom, 1),
                             "reported_ac": meta.get("reported_acres")})
    out["guard_verdicts"] = verdicts
    # multi-source size disagreement inside fused groups
    dis = []
    for inc in rec["fused"]:
        by = (inc.meta or {}).get("acres_by_source") or {}
        vals = [v for v in by.values() if v and v > 0]
        if len(vals) >= 2 and max(vals) / min(vals) > 1.10:
            dis.append({"name": inc.name, "acres_by_source": by,
                        "fused_reported": (inc.meta or {}).get(
                            "reported_acres")})
    out["fused_size_disagreement_over_10pct"] = dis
    return out


def compare_recordings(a, b) -> dict:
    """Between two recordings: per WFIGS perimeter (IRWIN), did the polygon
    time move, and with or without a geometry / area change?"""
    def index(rec):
        side = {_irwin(x): x for x in rec["side"].get("perimeters", [])}
        out = {}
        for inc in rec["sources"]["wfigs"]:
            if not inc.perimeter or not inc.irwin_id:
                continue
            s = side.get(inc.irwin_id, {})
            out[inc.irwin_id] = {
                "dc": (inc.meta or {}).get("perimeter_at_s"),
                "pdt": C.ms(s.get("poly_PolygonDateTime")),
                "geom": FB.rings_area_m2(inc.perimeter) / C.ACRE_M2,
                "fig": (inc.meta or {}).get("perimeter_acres"),
                "rep": (inc.meta or {}).get("reported_acres"),
                "name": inc.name}
        return out
    ia, ib = index(a), index(b)
    c = collections.Counter()
    moved = []
    for k in sorted(set(ia) & set(ib)):
        x, y = ia[k], ib[k]
        if x["dc"] == y["dc"]:
            c["polygon time unchanged"] += 1
            continue
        same = abs(y["geom"] - x["geom"]) <= 1e-3 * max(x["geom"], 1e-9)
        kind = ("re-stamp (newer time, same geometry)" if same else
                "new geometry")
        c[kind] += 1
        moved.append({"name": x["name"], "kind": kind,
                      "geom_before": round(x["geom"], 1),
                      "geom_after": round(y["geom"], 1),
                      "pdt_moved": x["pdt"] != y["pdt"]})
    rep = collections.Counter()
    for k in sorted(set(ia) & set(ib)):
        ra, rb = ia[k]["rep"], ib[k]["rep"]
        if ra and rb and rb != ra:
            rep["reported size rose" if rb > ra else "reported size fell"] += 1
    return {"from": os.path.basename(a["dir"]),
            "to": os.path.basename(b["dir"]),
            "hours": round((b["t_s"] - a["t_s"]) / H, 2),
            "wfigs_perimeters_in_both": len(set(ia) & set(ib)),
            "counts": dict(c), "reported": dict(rep), "moved": moved}


# ---------------------------------------------------------------------------
# B/C. the Daily history through the real belief store and the seed check
# ---------------------------------------------------------------------------

def circle(center: LatLon, acres: float, n: int = 48) -> list:
    r = math.sqrt(max(acres, 1e-6) * C.ACRE_M2 / math.pi)
    return [geo.destination(center, 360.0 * i / n, r) for i in range(n)]


def row_incident(key: str, r: dict) -> FireIncident:
    """A Daily row as feeds.WfigsFeed would build it (see module doc)."""
    dc = C.ms(r.get("poly_DateCurrent"))
    mod = C.ms(r.get("attr_ModifiedOnDateTime_dt"))
    center = LatLon(45.0, -120.0)
    meta = {"perimeter_at_s": dc}
    if r.get("acres") and r["acres"] > 0:
        meta["perimeter_acres"] = float(r["acres"])
    size = r.get("attr_IncidentSize")
    if size is not None and mod:
        meta["reported_acres"] = float(size)
        meta["reported_at_s"] = mod
    return FireIncident(
        incident_id=f"bvp:{key}", name=key, source=IncidentSource.WFIGS,
        origin=center, updated_at_s=max(dc or 0.0, mod or 0.0),
        discovered_at_s=C.ms(r.get("attr_FireDiscoveryDateTime")),
        acreage=size, active=True,
        perimeter=[circle(center, r["acres"])], meta=meta)


def replay_history(key: str) -> dict:
    rows = [r for r in C.bvp_rows(key) if r.get("poly_DateCurrent")]
    stores = {"legacy": FB.FireBeliefStore(), "guarded": FB.FireBeliefStore()}
    rapid_steps = {"legacy": 0, "guarded": 0}
    notes = collections.Counter()
    now = None
    seed = {"rows": 0, "replaced": 0, "as_received": 0, "min_ratio": 1.0}
    memory = None               # (acres, seen_s)
    cfg_mem = CFG_ON
    seed["detail"] = []
    for r in rows:
        inc = row_incident(key, r)
        # the watcher can read a row only once both its polygon edit and its
        # record edit exist: max of the two + a 10-min poll lag
        t = max(C.ms(r["poly_DateCurrent"]),
                C.ms(r.get("attr_ModifiedOnDateTime_dt")) or 0.0) + 600.0
        now = t if now is None else max(now, t)
        for arm, cfg in (("legacy", CFG_OFF), ("guarded", CFG_ON)):
            evs = stores[arm].observe([inc], now, cfg)
            if arm == "guarded":
                for e in evs:
                    m = e["message"]
                    notes[("collapse" if "under 10%" in m else
                           "time" if "time" in m or "future" in m else
                           "held" if "held" in m else
                           "restart" if "restarts" in m else
                           "area figure" if "geometry" in m else "other")] += 1
            st = stores[arm].statuses([inc], now, cfg)[inc.incident_id]
            rapid_steps[arm] += bool(st.rapid)
        # C. the seed side's verdict for this row (threat._sane_seed_incident)
        seed["rows"] += 1
        geom = r["acres"]
        bad = feedsanity.collapse_problem(
            geom, inc.meta.get("reported_acres"), cfg_mem)
        if memory is not None and now - memory[1] > \
                cfg_mem.feed_sanity_memory_h * H:
            memory = None
        if bad is None and memory is not None and feedsanity.is_drop(
                geom * C.ACRE_M2, memory[0] * C.ACRE_M2, cfg_mem):
            bad = "drop"
        if bad is None:
            memory = (geom, now)
        elif memory is not None:
            seed["replaced"] += 1
            seed["min_ratio"] = min(seed["min_ratio"],
                                    geom / memory[0] if memory[0] else 1.0)
            seed["detail"].append({
                "oid": r["oid"], "dc": C.fmt_t(C.ms(r["poly_DateCurrent"])),
                "polygon_ac": round(geom, 1),
                "reported_ac": inc.meta.get("reported_acres"),
                "remembered_ac": round(memory[0], 1),
                "remembered_age_h": round((now - memory[1]) / H, 1),
                "why": "drop" if bad == "drop" else "collapse"})
        else:
            seed["as_received"] += 1

    def shape(store):
        bel = store.belief(f"bvp:{key}")
        c = collections.Counter()
        for w in bel.windows:
            aa, ab = w["aa"], w["ab"]
            if ab < 0.1 * aa:
                cls = "collapse (<10 %)"
            elif ab < aa and feedsanity.is_drop(ab, aa, CFG_ON):
                cls = "drop beyond noise"
            elif ab > 10.0 * aa:
                cls = "jump (>10x)"
            elif abs(ab - aa) <= 1e-3 * aa:
                cls = ("zero growth, static (3x error)" if w.get("static")
                       else "zero growth, full weight")
            else:
                cls = "growth or re-measurement"
            c[f"{w['kind']}: {cls}"] += 1
            c[f"{w['kind']}: windows"] += 1
        return dict(c)
    # each collapse row's spell: until the next published row whose polygon
    # is plausible again (publication order). A Daily row carries no
    # publication time: each is clocked at max(polygon edit, record edit),
    # the earliest the watcher could have read it — approximate.
    def seen(x):
        return max(C.ms(x["poly_DateCurrent"]),
                   C.ms(x.get("attr_ModifiedOnDateTime_dt")) or 0.0)
    spells = []
    for i, r in enumerate(rows):
        rep = r.get("attr_IncidentSize")
        if feedsanity.collapse_problem(r["acres"], rep, CFG_ON) is None:
            continue
        t0 = seen(r)
        nxt = next((seen(x) for x in rows[i + 1:]
                    if feedsanity.collapse_problem(
                        x["acres"], x.get("attr_IncidentSize"),
                        CFG_ON) is None), None)
        spells.append({"oid": r["oid"], "at": C.fmt_t(t0),
                       "polygon_ac": round(r["acres"], 1),
                       "reported_ac": rep,
                       "until_plausible_h": (None if nxt is None else
                                             round(max(0.0, nxt - t0) / H,
                                                   1))})
    return {"rows": len(rows), "legacy": shape(stores["legacy"]),
            "guarded": shape(stores["guarded"]),
            "rapid_steps": rapid_steps, "guard_notes": dict(notes),
            "seed": seed, "collapse_rows": spells}


# ---------------------------------------------------------------------------
# D. the deployed watcher's belief file
# ---------------------------------------------------------------------------

def part_d() -> dict:
    p = os.path.join(C.OUT_ROOT, "watcher_s3", "fire_beliefs.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)
    wins = []
    for iid, b in d["incidents"].items():
        for w in b["windows"]:
            wins.append({"incident": iid, "kind": w["kind"],
                         "from_ac": round(w["aa"] / C.ACRE_M2, 2),
                         "to_ac": round(w["ab"] / C.ACRE_M2, 2),
                         "hours": round((w["tb"] - w["ta"]) / H, 2),
                         "zero_growth": w["aa"] == w["ab"],
                         "modeled": w.get("vnom") is not None})
    return {"saved_utc": C.fmt_t(d.get("saved_at_s")),
            "incidents": len(d["incidents"]),
            "observations": {k: sum(len(b["obs"][k])
                                    for b in d["incidents"].values())
                             for k in ("perimeter", "reported")},
            "windows": wins}


def main() -> int:
    recs = [C.load_recording(r) for r in C.recordings()]
    res = {"A": [part_a(recs[0])] + ([part_a(recs[-1])]
                                     if len(recs) > 1 else []),
           "A_pairs": [compare_recordings(a, b)
                       for a, b in zip(recs, recs[1:])],
           "B": {k: replay_history(k) for k in C.BVP_FIRES
                 if C.bvp_rows(k)},
           "D": part_d()}
    tot = collections.Counter()
    for k, v in res["B"].items():
        for arm in ("legacy", "guarded"):
            for kk, n in v[arm].items():
                tot[(arm, kk)] += n
            tot[(arm, "rapid steps")] += v["rapid_steps"][arm]
        for kk, n in v["seed"].items():
            if kk not in ("min_ratio", "detail"):
                tot[("seed", kk)] += n
        for kk, n in v["guard_notes"].items():
            tot[("guard notes", kk)] += n
    res["B_totals"] = {f"{a}: {k}": n for (a, k), n in sorted(tot.items())}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, default=str)
    a0 = res["A"][-1]
    print("A. live snapshot", a0["recording"], a0["t_utc"], a0["sources"],
          "fused", a0["fused"])
    for k, v in a0["time"].items():
        print(f"   time {k:44s} {v}")
    print("   duplicate IRWIN polygons:", a0["wfigs_duplicate_irwin_polygons"])
    print("   re-stamp lag h:", a0["restamp_lag_h"])
    print("   area figure vs geometry:", a0["area_figure_vs_geometry"])
    print("   guard verdicts:")
    for v in a0["guard_verdicts"]:
        print("     ", v)
    print("   fused size disagreement >10 %:")
    for v in a0["fused_size_disagreement_over_10pct"]:
        print("     ", v)
    for p in res["A_pairs"]:
        print("A. pair", p["from"], "->", p["to"], p["hours"], "h:",
              p["counts"], p["reported"])
    print("B. history (legacy vs guarded):")
    for k, v in res["B"].items():
        print(f"   {k:12s} rows {v['rows']:4d} legacy {v['legacy']} | "
              f"guarded {v['guarded']} | rapid steps {v['rapid_steps']} | "
              f"notes {v['guard_notes']} | seed {v['seed']}")
    print("B totals:", json.dumps(res["B_totals"], indent=1))
    print("C. seed replacements:")
    for k, v in res["B"].items():
        for d in v["seed"]["detail"]:
            print(f"   {k:12s} {d}")
    print("B. collapse rows (polygon under 10 % of its own record's size):")
    for k, v in res["B"].items():
        for d in v["collapse_rows"]:
            print(f"   {k:12s} {d}")
    print("D. deployed belief file:", json.dumps(res["D"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
