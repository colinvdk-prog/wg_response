"""BUILD-3 (feeds), item 8a: how many live FIRIS rows carry FireDiscoveryDate
(the field FirisFeed does not read into discovered_at_s)? Read-only,
cache-busted GETs. Prints counts and five sample rows."""
import gzip
import json
import time
import urllib.parse
import urllib.request

L = ("https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/"
     "CA_Perimeters_NIFC_FIRIS_public_view/FeatureServer/0/query")


def q(**p):
    p.update(f="json", _t=str(int(time.time())))
    req = urllib.request.Request(L + "?" + urllib.parse.urlencode(p),
                                 headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=60) as r:
        b = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            b = gzip.decompress(b)
    return json.loads(b)


print("all rows", q(where="1=1", returnCountOnly="true").get("count"))
print("FireDiscoveryDate set",
      q(where="FireDiscoveryDate IS NOT NULL",
        returnCountOnly="true").get("count"))
print("incident_name set",
      q(where="incident_name IS NOT NULL", returnCountOnly="true").get("count"))
d = q(where="FireDiscoveryDate IS NOT NULL",
      outFields="OBJECTID,FireDiscoveryDate,CreationDate,poly_DateCurrent,"
                "incident_name,mission",
      returnGeometry="false", resultRecordCount="5")
for f in d.get("features", []):
    print(f["attributes"])
