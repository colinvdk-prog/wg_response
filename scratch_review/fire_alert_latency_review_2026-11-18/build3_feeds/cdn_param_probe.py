"""BUILD-3 (feeds), item 1: which dummy query parameter does the ArcGIS
Online CDN (Azure Front Door) put in its cache key?

Method, per layer and per round: warm a FIXED query URL (two GETs, so the
edge holds it), then GET the same URL plus ONE never-seen dummy param
(`_cb`, `_t`, `_ts`, each with a fresh random value). A cache HIT on a value
nobody ever asked for means the edge IGNORED that param (it served the fixed
URL's entry); a MISS (or a fresh Last-Modified / Age 0) means the param is
in the cache key and busts the CDN. Read-only GETs, small answers
(returnCountOnly). Writes cdn_param_probe_out.jsonl beside this script.

Run: python cdn_param_probe.py [rounds]"""
import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LAYERS = {
    "wfigs_points": "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0/query",
    "wfigs_perims": "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query",
    "firis": "https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/CA_Perimeters_NIFC_FIRIS_public_view/FeatureServer/0/query",
}
PARAMS = ("_cb", "_t", "_ts")


def get(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()
        h = {k.lower(): v for k, v in r.getheaders()}
    return {"xc": h.get("x-cache"), "age": h.get("age"),
            "cc": h.get("cache-control")}


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    out = os.path.join(HERE, "cdn_param_probe_out.jsonl")
    tally = {}
    for rnd in range(rounds):
        for name, base in LAYERS.items():
            q = {"where": "1=1", "returnCountOnly": "true", "f": "json",
                 "outFields": "OBJECTID", "returnGeometry": "false"}
            fixed = base + "?" + urllib.parse.urlencode(q)
            warm = [get(fixed), get(fixed)]
            rec = {"t": time.time(), "round": rnd, "layer": name,
                   "warm": warm}
            for p in PARAMS:
                qq = dict(q)
                qq[p] = str(random.randrange(10 ** 12, 10 ** 13))
                rec[p] = get(base + "?" + urllib.parse.urlencode(qq))
                hit = "HIT" in (rec[p]["xc"] or "")
                warm_hit = "HIT" in (warm[1]["xc"] or "")
                key = (name, p)
                t = tally.setdefault(key, {"hit": 0, "miss": 0, "nocache": 0,
                                           "warm_hits": 0})
                if (rec[p]["xc"] or "").startswith("CONFIG_NOCACHE"):
                    t["nocache"] += 1
                elif hit:
                    t["hit"] += 1
                else:
                    t["miss"] += 1
                t["warm_hits"] += int(warm_hit)
            with open(out, "a") as fh:
                fh.write(json.dumps(rec) + "\n")
            print(name, rnd, "warm:", [w["xc"] for w in warm],
                  {p: rec[p]["xc"] for p in PARAMS})
        time.sleep(5)
    print("\nper (layer, param): a HIT on a never-seen value = the CDN "
          "ignores the param")
    for (name, p), t in sorted(tally.items()):
        print(f"  {name:13s} {p:4s} hit={t['hit']} miss={t['miss']} "
              f"nocache={t['nocache']} (fixed URL warm hits {t['warm_hits']})")


if __name__ == "__main__":
    main()
