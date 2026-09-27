"""Cut the recorded-data test fixture (tests/fixtures/contained_2026_09_27.json)
from the 2026-09-27 national snapshot: the WFIGS point + perimeter features of
Willow (80 % contained, polygon 259 h old on a 19.5-h-old record) and the
WFIGS point + FIRIS polygon of Chileno (100 % contained). Attributes are kept
exactly as recorded; rings are thinned for size (largest rings first, every
k-th vertex) and the thinning is recorded in the fixture's provenance."""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(HERE))
SNAP = os.path.join(WT, "out", "contained", "snapshot")
OUT = os.path.join(WT, "tests", "fixtures", "contained_2026_09_27.json")
WILLOW = "548a8d28-1a04-48ee-9011-aba023175d43"
CHILENO = "df96eda9-9b27-47fb-86d5-dfb97a879a22"


def load(name):
    man = json.load(open(os.path.join(SNAP, "manifest.json"), encoding="utf-8"))
    ent = man["bodies"][name]
    return json.load(open(os.path.join(SNAP, ent["file"]), encoding="utf-8")), man


def norm(i):
    return (i or "").strip().strip("{}").lower()


def ring_area(r):
    s = 0.0
    lat0 = r[0][1]
    kx = math.cos(math.radians(lat0))
    for (x1, y1), (x2, y2) in zip(r, r[1:] + r[:1]):
        s += (x1 * kx) * y2 - (x2 * kx) * y1
    return abs(s)


def thin(rings, max_rings=3, max_verts=120):
    kept = sorted(rings, key=ring_area, reverse=True)[:max_rings]
    out = []
    for r in kept:
        step = max(1, math.ceil(len(r) / max_verts))
        rr = [[round(v[0], 6), round(v[1], 6)] for v in r[::step]]
        if rr[0] != rr[-1]:
            rr.append(list(rr[0]))
        out.append(rr)
    return out


pts, man = load("WFIGS_Incident_Locations_Current@0")
per, _ = load("WFIGS_Interagency_Perimeters_Current@0")
fir, _ = load("CA_Perimeters_NIFC_FIRIS_public_view@0")
fx = {"schema": "wgresponse-contained-fixture/1",
      "recorded_utc": man["recorded_utc"],
      "recorded_at_s": man["recorded_at_s"],
      "provenance": ("live WFIGS / FIRIS payloads recorded by "
                     "scratch_review/contained/feedsnap.py record; attributes verbatim, "
                     "rings thinned (largest 3 rings, <= ~120 vertices each)"),
      "willow": {}, "chileno": {}}
for f in pts["features"]:
    k = norm(f["attributes"].get("IrwinID"))
    if k == WILLOW:
        fx["willow"]["point"] = f
    if k == CHILENO:
        fx["chileno"]["point"] = f
for f in per["features"]:
    a = f["attributes"]
    if norm(a.get("poly_IRWINID") or a.get("attr_IrwinID")) == WILLOW:
        rings = f["geometry"]["rings"]
        fx["willow"]["perimeter"] = {
            "attributes": a,
            "geometry": {"rings": thin(rings)},
            "thinned_from": {"rings": len(rings),
                             "vertices": sum(len(r) for r in rings)}}
for f in fir["features"]:
    a = f["attributes"]
    if (a.get("incident_name") or "").upper() == "CHILENO":
        rings = f["geometry"]["rings"]
        fx["chileno"]["firis"] = {
            "attributes": a,
            "geometry": {"rings": thin(rings)},
            "thinned_from": {"rings": len(rings),
                             "vertices": sum(len(r) for r in rings)}}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8", newline="\n") as f:
    json.dump(fx, f, indent=1, sort_keys=True)
print("wrote", OUT, os.path.getsize(OUT), "bytes")
for k in ("willow", "chileno"):
    print(k, sorted(fx[k]))
    for part in fx[k].values():
        if "thinned_from" in part:
            print("   thinned from", part["thinned_from"], "to",
                  [len(r) for r in part["geometry"]["rings"]])
