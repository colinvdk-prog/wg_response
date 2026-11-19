"""BUILD-3 (feeds), items 5-6 groundwork (read-only GETs, cache-busted):
  - does the attributes-only query (returnGeometry=false) list the features
    in the SAME order as the watcher's full query? (incremental ordering)
  - FIRIS outFields=* vs only the fields the adapter reads: bytes.
  - do the layers answer objectIds=<list> with the watcher's other params?
Run from the repo root. Prints a summary; writes explore_order_trim_out.json."""
import gzip
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


def fetch(url, gz=True):
    h = {"Accept": "application/json"}
    if gz:
        h["Accept-Encoding"] = "gzip"
    t0 = time.monotonic()
    with urllib.request.urlopen(urllib.request.Request(url, headers=h),
                                timeout=90) as r:
        body = r.read()
        enc = r.headers.get("Content-Encoding")
    wire = len(body)
    if enc == "gzip":
        body = gzip.decompress(body)
    return json.loads(body.decode("utf-8")), wire, len(body), time.monotonic() - t0


def url_of(layer, fields, geom=True, extra=None):
    p = {"where": "1=1", "outFields": fields, "f": "json",
         "returnGeometry": "true" if geom else "false", "outSR": "4326"}
    p.update(feeds._envelope_params(AOI))
    if extra:
        p.update(extra)
    p["_t"] = str(int(time.time()))
    return layer + "?" + urllib.parse.urlencode(p)


out = {}
cases = [
    ("wfigs_points", feeds.WfigsFeed.POINTS_URL, feeds.WfigsFeed.POINT_OUT_FIELDS),
    ("wfigs_perims", feeds.WfigsFeed.PERIMETERS_URL, feeds.WfigsFeed.PERIM_OUT_FIELDS),
    ("firis", feeds.FirisFeed.LAYER_URL, "*"),
]
for name, layer, fields in cases:
    legacy, lwire, lraw, ls = fetch(url_of(layer, fields))
    withoid = fields if fields == "*" else fields + ",OBJECTID"
    full, wire, raw, s = fetch(url_of(layer, withoid))
    stripped = [dict(f, attributes={k: v for k, v in f["attributes"].items()
                                    if fields == "*" or k != "OBJECTID"})
                for f in full.get("features") or []]
    same_as_legacy = stripped == (legacy.get("features") or [])
    oids = [f["attributes"].get("OBJECTID") for f in full.get("features") or []]
    attrs, awire, araw, a_s = fetch(url_of(layer, fields + ",OBJECTID"
                                            if fields != "*" else "*",
                                            geom=False))
    aoids = [f["attributes"].get("OBJECTID") for f in attrs.get("features") or []]
    rec = {"legacy_eq_stripped_with_oid": same_as_legacy, "legacy_s": round(ls, 2),
           "full_n": len(oids), "full_wire": wire, "full_raw": raw,
           "full_s": round(s, 2), "attrs_wire": awire, "attrs_raw": araw,
           "attrs_s": round(a_s, 2), "same_order": oids == aoids,
           "oid_ascending": oids == sorted(oids),
           "exceeded": full.get("exceededTransferLimit")}
    some = oids[:3]
    if some:
        sub, swire, sraw, ss = fetch(url_of(layer, withoid, extra={
            "objectIds": ",".join(str(o) for o in some)}))
        rec["objectIds_ok"] = ([f["attributes"].get("OBJECTID")
                                for f in sub.get("features") or []] == some
                               and all(a == b for a, b in zip(
                                   sub["features"], full["features"][:3])))
    out[name] = rec
    print(name, rec)

trim = "OBJECTID,incident_name,mission,poly_DateCurrent,EditDate,area_acres"
full, wire, raw, s = fetch(url_of(feeds.FirisFeed.LAYER_URL, "*"), gz=False)
t, twire, traw, ts = fetch(url_of(feeds.FirisFeed.LAYER_URL, trim), gz=False)
geom_bytes = sum(len(json.dumps(f.get("geometry"))) for f in full["features"])
attr_bytes = sum(len(json.dumps(f.get("attributes"))) for f in full["features"])
out["firis_trim"] = {"star_raw": raw, "trim_raw": traw,
                     "saved_frac": round(1 - traw / raw, 4),
                     "geometry_json_bytes": geom_bytes,
                     "attribute_json_bytes": attr_bytes,
                     "display_status": sorted({f["attributes"].get("displayStatus")
                                               for f in full["features"]},
                                              key=str)}
print("firis_trim", out["firis_trim"])
with open(os.path.join(HERE, "explore_order_trim_out.json"), "w") as fh:
    json.dump(out, fh, indent=1)
