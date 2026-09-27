"""Record the WFIGS incident-point FIRE BEHAVIOUR fields (2026-09-27 off-domain
horizon study, SAFETY SEMANTICS 15). NOT product code — a measurement helper.

The recorded national snapshot (scratch_review/contained/feedsnap.py, 15:55Z)
predates the behaviour fields in the adapter's point query, so its bodies do
not carry them. This script queries the LIVE incident-locations layer once
over the CONUS envelope for exactly the fields the new adapter asks for
(IrwinID + ModifiedOnDateTime_dt identify the record revision) and writes the
raw body + a manifest to out/offmap/. behavior_augment() then copies the four
behaviour fields into the RECORDED point body only for records whose revision
(IrwinID and ModifiedOnDateTime_dt) is unchanged since the recording — the
behaviour of that exact revision — and leaves every other record without them
(unknown). Nothing else in the recorded world changes.

    py -3 scratch_review/offmap/behavior_snapshot.py record
    py -3 scratch_review/offmap/behavior_snapshot.py stats
"""
import json
import os
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, WT)

from wgresponse import feeds  # noqa: E402

DATA = os.path.join(WT, "out", "offmap")
BODY = os.path.join(DATA, "behavior_points.body")
MANIFEST = os.path.join(DATA, "behavior_manifest.json")
CONUS = dict(xmin=-125.0, ymin=24.0, xmax=-66.0, ymax=50.0)
FIELDS = ("IncidentName,IrwinID,ModifiedOnDateTime_dt,PercentContained,"
          "IncidentSize,FireBehaviorGeneral,FireBehaviorGeneral1,"
          "FireBehaviorGeneral2,FireBehaviorGeneral3,IncidentTypeCategory")
BEHAVIOR = ("FireBehaviorGeneral", "FireBehaviorGeneral1",
            "FireBehaviorGeneral2", "FireBehaviorGeneral3")


def record():
    os.makedirs(DATA, exist_ok=True)
    params = {"where": "1=1", "outFields": FIELDS, "f": "json",
              "returnGeometry": "false", "outSR": "4326",
              "geometry": json.dumps(dict(CONUS, spatialReference={"wkid": 4326})),
              "geometryType": "esriGeometryEnvelope", "inSR": "4326",
              "spatialRel": "esriSpatialRelIntersects"}
    url = feeds.WfigsFeed.POINTS_URL + "?" + urllib.parse.urlencode(params)
    t = time.time()
    status, body = feeds._default_http_get(url, {"Accept": "application/json"},
                                           timeout_s=120.0)
    assert status == 200, status
    payload = json.loads(body.decode("utf-8"))
    assert "error" not in payload and not payload.get("exceededTransferLimit")
    with open(BODY, "wb") as f:
        f.write(body)
    man = {"fetched_at_s": t,
           "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)),
           "features": len(payload.get("features") or []), "fields": FIELDS}
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(man, f, indent=1)
    print(json.dumps(man, indent=1))


def live_rows():
    with open(BODY, "rb") as f:
        payload = json.loads(f.read().decode("utf-8"))
    return [ft.get("attributes") or {} for ft in payload.get("features") or []]


def behavior_augment(recorded_body: bytes):
    """(augmented body bytes, counts): the recorded point body with the four
    behaviour fields copied in from the live body for every record whose
    (IrwinID, ModifiedOnDateTime_dt) revision is unchanged."""
    live = {}
    for a in live_rows():
        k = (str(a.get("IrwinID") or "").upper(), a.get("ModifiedOnDateTime_dt"))
        live[k] = a
    payload = json.loads(recorded_body.decode("utf-8"))
    n = same = changed = with_beh = 0
    for ft in payload.get("features") or []:
        a = ft.get("attributes") or {}
        n += 1
        k = (str(a.get("IrwinID") or "").upper(), a.get("ModifiedOnDateTime_dt"))
        src = live.get(k)
        if src is None:
            changed += 1
            continue
        same += 1
        for fld in BEHAVIOR:
            a[fld] = src.get(fld)
        if any(src.get(fld) for fld in BEHAVIOR):
            with_beh += 1
    return (json.dumps(payload).encode("utf-8"),
            {"records": n, "same_revision": same,
             "revision_changed_or_gone": changed,
             "same_revision_with_behavior": with_beh})


def stats():
    from collections import Counter
    rows = [a for a in live_rows() if a.get("IncidentTypeCategory") != "RX"]
    gen = Counter((a.get("FireBehaviorGeneral") or "") for a in rows)
    desc = Counter()
    for a in rows:
        for fld in BEHAVIOR[1:]:
            v = a.get(fld)
            if v:
                desc[v] += 1
    print("records (non-RX):", len(rows))
    print("FireBehaviorGeneral:", dict(gen.most_common()))
    print("descriptors:", dict(desc.most_common()))
    for name in ("Willow", "0433 BREWER", "Chileno"):
        for a in rows:
            if a.get("IncidentName") == name:
                print(name, {k: a.get(k) for k in (
                    "ModifiedOnDateTime_dt", "PercentContained") + BEHAVIOR})


if __name__ == "__main__":
    if sys.argv[1:2] == ["record"]:
        record()
    elif sys.argv[1:2] == ["stats"]:
        stats()
    else:
        print(__doc__)
