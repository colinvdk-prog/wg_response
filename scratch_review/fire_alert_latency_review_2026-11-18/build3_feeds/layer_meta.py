"""BUILD-3 (feeds): the three ArcGIS layers' metadata - fields, edit fields,
maxRecordCount, capabilities - read-only GETs (cache-busted). Writes
layer_meta_out.json beside this script and prints a summary."""
import json
import os
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LAYERS = {
    "wfigs_points": "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0",
    "wfigs_perims": "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0",
    "firis": "https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/CA_Perimeters_NIFC_FIRIS_public_view/FeatureServer/0",
}
out = {}
for name, url in LAYERS.items():
    req = urllib.request.Request(url + "?f=json&_t=%d" % int(time.time()),
                                 headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        meta = json.loads(r.read())
    keep = {k: meta.get(k) for k in (
        "name", "type", "objectIdField", "maxRecordCount", "standardMaxRecordCount",
        "editFieldsInfo", "editingInfo", "capabilities", "supportsAdvancedQueries",
        "hasZ", "hasM", "geometryType", "timeInfo", "isView", "isDataVersioned")}
    keep["advancedQueryCapabilities"] = meta.get("advancedQueryCapabilities")
    keep["fields"] = [(f["name"], f["type"]) for f in meta.get("fields") or []]
    out[name] = keep
    print(f"== {name}: maxRecordCount={keep['maxRecordCount']} "
          f"oid={keep['objectIdField']} hasZ={keep['hasZ']} hasM={keep['hasM']}")
    print("   editFieldsInfo:", keep["editFieldsInfo"])
    print("   editingInfo:", keep["editingInfo"])
    print("   capabilities:", keep["capabilities"])
    print("   fields:", ", ".join(n for n, _ in keep["fields"]))
with open(os.path.join(HERE, "layer_meta_out.json"), "w") as fh:
    json.dump(out, fh, indent=1)
