"""Parse live /api/state dump + RSF/wind artifacts. Network: optional refresh."""
from __future__ import annotations

import json
import os
import statistics
import time
import urllib.request
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "scratch_review", "rvgrok_live_and_rsf_out.json")

WATCH = "https://y372zmpfuj.us-west-2.awsapprunner.com"
SIM = "https://m897yqgsmt.us-west-2.awsapprunner.com"


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "wg-review/rvgrok"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return r.status, dict(r.headers), body, time.time() - t0


def load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


def rsf_coat(path):
    d = load(path)
    rows = d.get("rows") or []
    belt_g = belt_n = 0
    thr_g = thr_n = 0
    viol = ovt = 0
    margins = []
    outcome = defaultdict(int)
    n_homes = 0
    sample_home = None
    for r in rows:
        viol += r.get("viol") or 0
        ovt += r.get("ov_ticks") or 0
        if r.get("min_margin_h") is not None:
            margins.append(r["min_margin_h"])
        homes = r.get("homes") or []
        n_homes += len(homes)
        for h in homes:
            if sample_home is None and isinstance(h, dict):
                sample_home = sorted(h.keys())
            if not isinstance(h, dict):
                continue
            oc = h.get("outcome") or h.get("tag") or ""
            outcome[str(oc)] += 1
            # belt e1000
            e = h.get("e1000") or h.get("in_belt") or h.get("belt")
            dist = h.get("dist_m") or h.get("dist_to_burn_m") or h.get("ember_m")
            gelled = h.get("gelled") or h.get("gel_applied") or oc in (
                "saved", "protected_then_impacted")
            # common opt_ember home schema
            if "gelled" in h:
                gelled = bool(h["gelled"])
            if h.get("e1000") is True or h.get("in_e1000"):
                belt_n += 1
                if gelled:
                    belt_g += 1
            if oc not in ("not_threatened", "none", ""):
                thr_n += 1
                if gelled or oc in ("saved", "protected_then_impacted"):
                    thr_g += 1
    return {
        "n_rows": len(rows),
        "n_homes": n_homes,
        "sample_home_keys": sample_home,
        "outcomes": dict(outcome),
        "viol": viol,
        "ovt": ovt,
        "min_margin": min(margins) if margins else None,
        "row0_keys": sorted(rows[0].keys()) if rows else [],
        "row0_home0": (rows[0]["homes"][0] if rows and rows[0].get("homes") else None),
    }


# Live endpoints
live = {}
for name, url in [
    ("watch_healthz", WATCH + "/healthz"),
    ("sim_healthz", SIM + "/healthz"),
    ("watch_state", WATCH + "/api/state"),
    ("sim_state", SIM + "/api/state"),
]:
    try:
        status, hdrs, body, dt = get(url)
        live[name] = {
            "http": status,
            "secs": round(dt, 3),
            "content_type": hdrs.get("Content-Type"),
            "bytes": len(body),
            "json": json.loads(body.decode("utf-8")) if body[:1] in (b"{", b"[") else body[:200].decode("utf-8", "replace"),
        }
    except Exception as e:
        live[name] = {"error": repr(e)}

st = live.get("watch_state", {}).get("json")
watch_summary = None
if isinstance(st, dict):
    started = st.get("started_at_s")
    last = st.get("last_cycle_t_s")
    now = st.get("now_s")
    cycles = st.get("cycles_run")
    period = st.get("poll_period_s") or 300.0
    uptime_s = (last - started) if (started and last) else None
    expected = (uptime_s / period) if uptime_s else None
    clients = st.get("clients") or []
    by_state = defaultdict(int)
    for c in clients:
        by_state[c.get("state") or "none"] += 1
    events = st.get("events") or []
    ev_states = defaultdict(int)
    for e in events:
        ev_states[e.get("state") or e.get("kind") or "?"] += 1
    notes = st.get("startup_notes") or []
    watch_summary = {
        "live": st.get("live"),
        "aoi_desc": st.get("aoi_desc"),
        "poll_period_s": period,
        "cycles_run": cycles,
        "started_at_s": started,
        "last_cycle_t_s": last,
        "now_s": now,
        "last_cycle_age_s": st.get("last_cycle_age_s"),
        "uptime_h": uptime_s / 3600.0 if uptime_s else None,
        "expected_cycles_at_period": expected,
        "cycle_efficiency": (cycles / expected) if expected else None,
        "mean_cycle_interval_s": (uptime_s / cycles) if cycles and uptime_s else None,
        "restored": st.get("restored"),
        "degraded": st.get("degraded"),
        "n_crews": len(st.get("crews") or []),
        "n_clients": len(clients),
        "client_ids_prefix": sorted({c.get("client_id", "")[:12] for c in clients}),
        "client_states": dict(by_state),
        "n_incidents": len(st.get("incidents") or []),
        "n_events": len(events),
        "event_states": dict(ev_states),
        "n_operator_events": len(st.get("operator_events") or []),
        "n_failsafe_events": len(st.get("failsafe_events") or st.get("recovery_events") or []),
        "startup_notes": notes,
        "runtime_notes": st.get("notes"),
        "safe_zones": [z.get("name") for z in (st.get("safe_zones") or [])],
        "plan_unserved": (st.get("plan") or {}).get("unserved"),
        "plan_breakdown": (st.get("plan") or {}).get("objective_breakdown"),
        "terrain_note": next((n for n in notes if "terrain" in n.lower() or "tmp" in n.lower()), None),
        "healthz": live.get("watch_healthz", {}).get("json"),
        "api_state_unauthenticated": True,
    }

# failsafe_events key hunt
if isinstance(st, dict):
    watch_summary["top_keys"] = sorted(st.keys())
    for k in st:
        if "fail" in k.lower() or "blind" in k.lower() or "recover" in k.lower():
            v = st[k]
            watch_summary[k] = (len(v) if isinstance(v, list) else type(v).__name__)

# RSF
rsf_stats = {}
for p in [
    "scratch_review/rsf_city_c8_scatter.json",
    "scratch_review/rsf_city_c10_scatter.json",
    "scratch_review/rsf_city_c10_cluster.json",
    "scratch_review/rsf_city_c12_scatter.json",
    "scratch_review/rsf_grid_rsf_c8_scatter.json",
    "scratch_review/rsf_grid_rsf_c10_scatter.json",
    "scratch_review/rsf_grid_rsf_c12_scatter.json",
]:
    fp = os.path.join(ROOT, p)
    if os.path.exists(fp):
        rsf_stats[os.path.basename(p)] = rsf_coat(p)

# axis intensity
axis = load("scratch_review/census_axis_sensitivity.json")
inten = {}
for tag, rec in (axis.get("axis_tables") or {}).items():
    t = (rec.get("tables") or {}).get("by_inten") or {}
    if t:
        inten[tag] = t

# high/extreme census files if any
wind_extra = {}
for fn in os.listdir(os.path.join(ROOT, "scratch_review")):
    if fn.startswith("census_wind") and fn.endswith(".json"):
        d = load("scratch_review/" + fn)
        stt = d.get("stats") or {}
        e = stt.get("e1000")
        wind_extra[fn] = {
            "mph": d.get("wind_mph") or d.get("intensity"),
            "e1000_pct": 100.0 * e[0] / e[1] if e and e[1] else None,
            "e1000": e,
            "viol": stt.get("viol"),
            "ovt": stt.get("ov_ticks"),
        }

# gel_life_h reference
import sys
sys.path.insert(0, ROOT)
from wgresponse.safety import gel_life_h
from wgresponse.contracts import ResponseConfig
cfg = ResponseConfig()
gel_pts = {
    "ref_25C_50rh_0mps": gel_life_h(25.0, 50.0, 0.0),
    "hot_35C_20rh_5mps": gel_life_h(35.0, 20.0, 5.0),
    "fire_33C_12rh_8mps": gel_life_h(33.0, 12.0, 8.0),
    "extreme_38C_8rh_15mps": gel_life_h(38.0, 8.0, 15.0),
    "cfg.gel_window_h": cfg.gel_window_h,
    "cfg.gel_full_strength_h": cfg.gel_full_strength_h,
    "cfg.gel_effectiveness": cfg.gel_effectiveness,
    "cfg.gel_env_model_enabled": cfg.gel_env_model_enabled,
    "cfg.ember_threat_radius_m": cfg.ember_threat_radius_m,
    "cfg.deploy_min": cfg.deploy_min,
    "cfg.dispatch_hold_h": cfg.dispatch_hold_h,
    "cfg.circuity": getattr(cfg, "circuity", None),
    "cfg.route_margin_min": getattr(cfg, "route_margin_min", None),
}

# window bins from stats audit
audit = load("scratch_review/rvgrok_stats_audit_out.json")

# DEV_OPEN
from wgresponse import server as srv

report = {
    "watch_summary": watch_summary,
    "sim_healthz": live.get("sim_healthz"),
    "sim_state_preview": (
        {k: (live["sim_state"]["json"].get(k) if isinstance(live.get("sim_state", {}).get("json"), dict) else None)
         for k in ("ok", "error", "live")}
        if "sim_state" in live else None
    ),
    "sim_state_raw_head": (
        str(live.get("sim_state", {}).get("json"))[:400]
        if live.get("sim_state") else None
    ),
    "rsf": rsf_stats,
    "axis_by_inten": inten,
    "wind_extra": wind_extra,
    "gel_and_cfg": gel_pts,
    "aug_window_bins": audit.get("august_window_bins"),
    "july_window_bins_floor": audit.get("july_window_bins_floor"),
}

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, default=str)
print("wrote", OUT)
print("\n=== WATCH ===")
print(json.dumps(watch_summary, indent=2, default=str)[:4000])
print("\n=== gel/cfg ===", gel_pts)
print("\n=== wind extra ===")
for k, v in sorted(wind_extra.items()):
    print(f"  {k}: {v}")
print("\n=== axis inten (v_c8d10) ===")
print(json.dumps(inten.get("v_c8d10"), indent=2)[:2000])
print("\n=== RSF ===")
for k, v in rsf_stats.items():
    print(f"  {k}: homes={v['n_homes']} outcomes={v['outcomes']} viol={v['viol']} "
          f"keys={v['sample_home_keys']} min_m={v['min_margin']}")
print("\n=== window bins AUG ===", audit.get("august_window_bins"))
print("=== window bins JUL floor ===", audit.get("july_window_bins_floor"))
print("\nsim_state", live.get("sim_state", {}).get("http"), live.get("sim_state", {}).get("error"),
      str(live.get("sim_state", {}).get("json"))[:200] if live.get("sim_state") else None)
