"""Snapshot the WFIGS Daily-perimeter attribute series (no geometry) of every
incident in the three hindcast corpora, with source-record ids, create and
edit dates — the evidence behind hindcast_lib's SNAPSHOT RULE
(docs/calibration/stacked_rings_fix_2026-09-27.md). Read-only GETs, one per
incident (paged), cached; recorded 2026-09-27 into daily_rows_2026-09-27/.

    py -3 scratch_review/hindcast_stacked/fetch_daily_rows.py scratch_review/hindcast_stacked
"""
import json, os, sys, time, urllib.request, urllib.parse
SP = sys.argv[1]
URL = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
       "WFIGS_Daily_Perimeters_Public/FeatureServer/0/query")
FIELDS = ("OBJECTID,poly_PolygonDateTime,poly_DateCurrent,poly_CreateDate,"
          "poly_GISAcres,poly_SourceOID,poly_SourceGlobalID,poly_MapMethod,"
          "poly_FeatureCategory,attr_IrwinID,attr_IncidentName,attr_IncidentSize")
UA = {"User-Agent": "wg-response-hindcast/1 (ops@thewildfireguardian.com)",
      "Accept": "application/json"}
n_req = 0
def get(irwin):
    global n_req
    feats, offset = [], 0
    while True:
        q = {"where": f"attr_IrwinID = '{irwin}'", "outFields": FIELDS,
             "returnGeometry": "false", "orderByFields": "OBJECTID ASC",
             "resultOffset": str(offset), "f": "json"}
        req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(q), headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r:
            p = json.loads(r.read().decode())
        n_req += 1
        if "error" in p: raise RuntimeError(p["error"])
        fs = p.get("features") or []
        feats += fs
        if not p.get("exceededTransferLimit") or not fs: break
        offset += len(fs)
    return feats
out_dir = os.path.join(SP, "daily_rows_2026-09-27"); os.makedirs(out_dir, exist_ok=True)
for d in ("tools/hindcast_data", "scratch_review/hindcast_oos_data",
          "scratch_review/hindcast_aug_data"):
    for fn in sorted(os.listdir(os.path.join(d, "incidents"))):
        inc = json.load(open(os.path.join(d, "incidents", fn)))
        path = os.path.join(out_dir, fn)
        if os.path.exists(path):
            continue
        feats = get(inc["irwin"])
        json.dump({"irwin": inc["irwin"], "name": inc["name"], "features": feats},
                  open(path, "w"))
        print(f"{d.split('/')[-1]:18s} {inc['name'][:20]:20s} rows={len(feats)}", flush=True)
        time.sleep(0.3)
print("requests:", n_req)
