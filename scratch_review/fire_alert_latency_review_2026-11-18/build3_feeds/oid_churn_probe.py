"""BUILD-3 (feeds), item 6 groundwork: are the polygon layers' OBJECTIDs
stable across the layers' own refreshes (WFIGS re-syncs every few
minutes)? If a refresh re-issues every OBJECTID, an incremental fetch
re-downloads everything (correct, no gain). Every `period` s for `n`
rounds: the cheap query (OBJECTID + the adapter's attributes, no geometry,
CONUS envelope, cache-busted) per layer, plus the layer's
editingInfo.dataLastEditDate. Read-only GETs.

Run from the repo root: python oid_churn_probe.py n period
Writes oid_churn_out.jsonl beside this script; prints a churn summary."""
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.getcwd())
from wgresponse import feeds  # noqa: E402
from wgresponse.contracts import BBox  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
AOI = BBox(west=-125.0, south=24.0, east=-66.0, north=50.0)
LAYERS = {
    "wfigs_perims": (feeds.WfigsFeed.PERIMETERS_URL,
                     feeds.WfigsFeed.PERIM_OUT_FIELDS
                     + ",OBJECTID,Shape__Area,Shape__Length"),
    "firis": (feeds.FirisFeed.LAYER_URL, "*"),
}


def fetch(url):
    h = {"Accept": "application/json", "Accept-Encoding": "gzip"}
    with urllib.request.urlopen(urllib.request.Request(url, headers=h),
                                timeout=60) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return json.loads(body.decode("utf-8"))


def main():
    n, period = int(sys.argv[1]), float(sys.argv[2])
    out = os.path.join(HERE, "oid_churn_out.jsonl")
    prev = {}
    summary = {k: {"rounds": 0, "added": 0, "removed": 0, "changed": 0,
                   "same": 0} for k in LAYERS}
    for rnd in range(n):
        t = time.time()
        rec = {"t": t, "round": rnd}
        for name, (layer, fields) in LAYERS.items():
            p = {"where": "1=1", "outFields": fields, "f": "json",
                 "returnGeometry": "false", "outSR": "4326"}
            p.update(feeds._envelope_params(AOI))
            p["_t"] = str(int(t))
            try:
                d = fetch(layer + "?" + urllib.parse.urlencode(p))
                meta = fetch(layer[:-len("/query")] + "?f=json&_t=%d" % int(t))
            except Exception as exc:          # noqa: BLE001
                rec[name] = {"err": str(exc)[:120]}
                continue
            cur = {f["attributes"]["OBJECTID"]: hashlib.sha256(json.dumps(
                f["attributes"], sort_keys=True).encode()).hexdigest()[:12]
                for f in d.get("features") or []}
            last = prev.get(name)
            row = {"n": len(cur), "dataLastEditDate":
                   (meta.get("editingInfo") or {}).get("dataLastEditDate")}
            if last is not None:
                added = [o for o in cur if o not in last]
                removed = [o for o in last if o not in cur]
                changed = [o for o in cur if o in last and last[o] != cur[o]]
                row.update(added=len(added), removed=len(removed),
                           changed=len(changed),
                           same=len(cur) - len(added) - len(changed))
                s = summary[name]
                s["rounds"] += 1
                for k in ("added", "removed", "changed", "same"):
                    s[k] += row[k]
            prev[name] = cur
            rec[name] = row
        with open(out, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(rnd, {k: rec.get(k) for k in LAYERS}, flush=True)
        time.sleep(max(0.0, period - (time.time() - t)))
    print("summary (sums over consecutive rounds):", json.dumps(summary))


if __name__ == "__main__":
    main()
