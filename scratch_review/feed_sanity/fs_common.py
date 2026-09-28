"""Feed-sanity study (2026-09-27): shared helpers.

replay_http_get(rec_dir) serves a recording made by fs_record.py by URL, so
the REAL adapters (wgresponse.feeds) parse the frozen payloads exactly as a
live cycle would. load_recording(rec_dir) -> (manifest, per-source incident
lists, fused incidents, raw side-query attributes).
"""
from __future__ import annotations

import json
import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO)

from wgresponse import feeds  # noqa: E402
from wgresponse.contracts import BBox  # noqa: E402

OUT_ROOT = os.path.join(_REPO, "out", "feed_sanity")
ACRE_M2 = 4046.8564224
#: the belief-vs-physics study's recorded WFIGS Daily rows
#: (scratch_review/belief_vs_physics/bvp_fetch.py fetch writes them to
#: out/bvp_data/rows/<fire>.json): $FS_BVP_ROWS, else this tree's out/, else
#: the worktree that study ran in (2026-09-27)
_BVP_CANDIDATES = [os.environ.get("FS_BVP_ROWS") or "",
                   os.path.join(_REPO, "out", "bvp_data", "rows"),
                   os.path.join(_REPO, "..", "agent-a4ec5ffe8d93e5bdd", "out",
                                "bvp_data", "rows")]
BVP_ROWS = next((p for p in _BVP_CANDIDATES if p and os.path.isdir(p)),
                _BVP_CANDIDATES[1])


def recordings() -> list:
    """Every recording dir under out/feed_sanity, oldest first."""
    if not os.path.isdir(OUT_ROOT):
        return []
    return sorted(os.path.join(OUT_ROOT, d) for d in os.listdir(OUT_ROOT)
                  if d.startswith("rec_"))


def replay_http_get(rec_dir: str):
    with open(os.path.join(rec_dir, "manifest.json"), encoding="utf-8") as fh:
        man = json.load(fh)
    by_url = {r["url"]: r for r in man["requests"]}

    def get(url, headers):
        r = by_url.get(url)
        if r is None:
            raise RuntimeError(f"not in the recording: {url[:120]}")
        with open(os.path.join(rec_dir, r["file"]), "rb") as fh:
            return r["status"], fh.read()
    return man, get


def load_recording(rec_dir: str) -> dict:
    man, get = replay_http_get(rec_dir)
    bbox = BBox(*man["bbox"])
    out = {"manifest": man, "t_s": man["t_s"], "dir": rec_dir}
    per_source = {}
    for key, cls in (("wfigs", feeds.WfigsFeed), ("firis", feeds.FirisFeed),
                     ("calfire", feeds.CalFireFeed)):
        adapter = cls(http_get=get)
        per_source[key] = adapter.fetch_active(bbox)
        assert adapter.last_fetch_ok, f"{key}: replay unhealthy"
    out["sources"] = per_source
    out["fused"] = feeds.fuse_incidents([per_source["wfigs"],
                                         per_source["firis"],
                                         per_source["calfire"]])
    side = [r for r in man["requests"] if r["role"] == "side"]
    attrs = {}
    for r in side:
        with open(os.path.join(rec_dir, r["file"]), encoding="utf-8") as fh:
            payload = json.load(fh)
        layer = "perimeters" if "Perimeters" in r["url"] else "points"
        attrs.setdefault(layer, []).extend(
            f.get("attributes") or {} for f in payload.get("features") or [])
    out["side"] = attrs
    # the raw adapter-query features (geometry included) per layer
    raw = {}
    for r in man["requests"]:
        if r["role"] != "adapter" or r["status"] != 200:
            continue
        url = r["url"]
        layer = ("wfigs_perimeters" if "Perimeters_Current" in url else
                 "wfigs_points" if "Incident_Locations" in url else
                 "firis" if "FIRIS" in url else "calfire")
        with open(os.path.join(rec_dir, r["file"]), encoding="utf-8") as fh:
            payload = json.load(fh)
        if isinstance(payload, dict):
            raw.setdefault(layer, []).extend(payload.get("features") or [])
        else:
            raw.setdefault(layer, []).extend(payload)
    out["raw"] = raw
    return out


def fmt_t(t) -> str:
    if t is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(float(t)))


def ms(v):
    """ArcGIS epoch ms -> unix s (None-safe)."""
    return None if v is None else float(v) / 1000.0


def bvp_rows(key: str) -> list:
    """The belief-vs-physics study's recorded WFIGS Daily rows for one fire
    (OBJECTID = publication order), or [] when that worktree is gone."""
    p = os.path.join(BVP_ROWS, f"{key}.json")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        rows = json.load(fh)["rows"]
    return sorted(rows, key=lambda r: r["oid"])


BVP_FIRES = ("willow", "aspenacres", "grasshopper", "hagen", "littlegiant",
             "sinlahekin", "border2", "sandcreek", "bobcatlakes", "timber",
             "plaskett")
