"""Build tests/fixtures/colocated_2026_10_15.json from a recorded live pull
(pull_live.py) — the real WFIGS / FIRIS / CAL FIRE payloads of the
co-located duplicates the 2026-10-15 pull holds, plus nearby distractors
(contracts SAFETY SEMANTICS 24; tests/test_fuse_colocated.py).

Attributes are VERBATIM; geometry is kept in the feed's own ring order (the
FIRIS adapter's origin is the vertex mean of rings[0], so ring 0 must stay
ring 0) and each ring longer than MAX_VERTS is thinned by stride.

One record is RECONSTRUCTED, not recorded, and says so under its own key:
the WFIGS perimeter the live watcher fused as incident aa44e04e "Bike Fire"
(171.5 acres, origin 34.38744,-118.42818) on 2026-10-14 — it had left the
WFIGS perimeter layer by the 2026-10-15 pull. It is rebuilt from that day's
/api/world/summary facts (name, IRWIN id, 171.5 acres) on the FIRIS polygon
of the same fire with its main ring first (its vertex mean lands 172 m from
the summary's origin), the time fields copied from the recorded WFIGS point
and FIRIS row.

    py -3 scratch_review/colocated/make_fixture.py out/colocated/raw_20261015T151558.json
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "tests", "fixtures", "colocated_2026_10_15.json")
MAX_VERTS = 120

WFIGS_POINTS = {                      # IrwinID (upper, no braces) -> why
    "AA44E04E-B724-4ADF-8EBF-A945159EB058": "BIKE: the reported pair's WFIGS half",
    "A5B711D9-8ACA-457B-9212-88FA8CA7C9D8": "CYRUS: WFIGS point (+ perimeter)",
    "6BD82F3A-C0BF-4D6C-81BF-00D3D9CB17FF": "Marshall 1: ambiguous, no size",
    "D4248445-2330-4CD9-8488-240FD94E1E27": "TOWN: same-source twin of LAC-371632",
    "50FF5BD9-1B8F-42AC-A550-2CD4256AC5AD": "LAC-371632: next to a FIRIS zombie",
    "29113F7B-DD19-44A9-9476-9A499DE0AA25": "Schultz Wagon 1: distinct, 174 m",
    "8B0F976B-A4D4-45C5-BCB3-F8705953DF2A": "Schultz Wagon 2: distinct, 174 m",
    "5C5FA582-795B-48AA-87E1-14A44C4C82FB": "Nelson Rd: 6 m from Sundown",
    "D027E265-42BB-420F-A43E-CDAA15EA036D": "Sundown: 6 m from Nelson Rd",
}
WFIGS_PERIMETERS = {"A5B711D9-8ACA-457B-9212-88FA8CA7C9D8": "CYRUS"}
FIRIS = {
    1504: "CA-LAC-BIKE-N50X: the reported pair's FIRIS half",
    1506: "CA-HIA-MARSHALL-N57B: CAL FIRE Marshall Fire's polygon",
    1505: "CA-HUU-SURGONE-N57B: older flight, no incident_name",
    1507: "SURGONE (CAL FIRE intel flight)",
    1508: "SURGONE (CAL FIRE intel flight, newest)",
    1502: "CYRUS (FIRIS, incident_name set)",
    1503: "CA-SQF-CYRUS-N5852K: USFS flight, no incident_name",
    188: "CA-ANF-MARTINS-N50X: a 100-day-old zombie next to LAC-371632",
    196: "MILEPOST 16: a 99-day-old zombie 3.4 km from Marshall",
}
CALFIRE = {
    "7f5d9531-c8cc-46cf-b4a2-0287e5f82934": "Marshall Fire",
    "7d84da78-6d70-4661-b7bf-8cfc1c657482": "Surgone Fire",
    "880d63ed-08f2-4d06-bdab-94a692d08fa5": "Cyrus Fire",
}


def _irwin(v):
    return str(v or "").strip("{}").upper()


def _thin(feat):
    g = feat.get("geometry") or {}
    if "rings" in g:
        rings = []
        for r in g["rings"]:
            if len(r) > MAX_VERTS:
                stride = -(-len(r) // MAX_VERTS)
                r = r[::stride]
            rings.append(r)
        g = dict(g, rings=rings)
    return {"attributes": feat["attributes"], "geometry": g}


def main(path: str) -> int:
    pull = json.load(open(path, encoding="utf-8"))
    bodies = {}
    for r in pull["responses"]:
        if "WFIGS_Incident_Locations" in r["url"]:
            bodies["points"] = json.loads(r["body"])["features"]
        elif "WFIGS_Interagency_Perimeters" in r["url"]:
            bodies["perims"] = json.loads(r["body"])["features"]
        elif "FIRIS" in r["url"]:
            bodies["firis"] = json.loads(r["body"])["features"]
        elif "incidents.fire.ca.gov" in r["url"]:
            bodies["calfire"] = json.loads(r["body"])
    points = [_thin(f) for f in bodies["points"]
              if _irwin(f["attributes"].get("IrwinID")) in WFIGS_POINTS]
    perims = [_thin(f) for f in bodies["perims"]
              if _irwin(f["attributes"].get("poly_IRWINID")
                        or f["attributes"].get("attr_IrwinID")) in WFIGS_PERIMETERS]
    firis = [_thin(f) for f in bodies["firis"]
             if f["attributes"].get("OBJECTID") in FIRIS]
    calfire = [r for r in bodies["calfire"]
               if str(r.get("UniqueId")).lower() in CALFIRE]
    assert len(points) == len(WFIGS_POINTS), len(points)
    assert len(perims) == len(WFIGS_PERIMETERS), len(perims)
    assert len(firis) == len(FIRIS), len(firis)
    assert len(calfire) == len(CALFIRE), len(calfire)
    firis.sort(key=lambda f: f["attributes"]["OBJECTID"])

    # the 2026-10-14 WFIGS perimeter record of the Bike Fire (RECONSTRUCTED)
    bike_pt = next(f for f in points if _irwin(f["attributes"]["IrwinID"])
                   == "AA44E04E-B724-4ADF-8EBF-A945159EB058")["attributes"]
    bike_fi = next(f for f in firis if f["attributes"]["OBJECTID"] == 1504)
    rings = list(bike_fi["geometry"]["rings"])
    main_i = max(range(len(rings)), key=lambda i: len(rings[i]))
    rings = [rings[main_i]] + rings[:main_i] + rings[main_i + 1:]
    reconstructed = {"attributes": {
        "poly_IncidentName": "Bike Fire", "attr_IncidentName": "BIKE",
        "poly_IRWINID": "{AA44E04E-B724-4ADF-8EBF-A945159EB058}",
        "attr_IrwinID": bike_pt["IrwinID"],
        "poly_GISAcres": 171.5, "attr_CalculatedAcres": None,
        "attr_FinalAcres": None, "attr_IncidentSize": 171.5,
        "attr_PercentContained": None,
        "attr_FireDiscoveryDateTime": bike_pt["FireDiscoveryDateTime"],
        "attr_ModifiedOnDateTime_dt": bike_pt["ModifiedOnDateTime_dt"],
        "poly_DateCurrent": bike_fi["attributes"]["poly_DateCurrent"],
        "attr_ContainmentDateTime": None, "attr_ControlDateTime": None,
        "attr_FireOutDateTime": None},
        "geometry": {"rings": rings}}

    out = {
        "schema": "wgresponse-colocated-fixture/1",
        "provenance": ("live WFIGS / FIRIS / CAL FIRE payloads recorded by "
                       "scratch_review/colocated/pull_live.py (CONUS AOI); "
                       "attributes verbatim, ring order kept, rings > "
                       f"{MAX_VERTS} vertices thinned by stride; "
                       "'reconstructed' is NOT a recorded payload (see "
                       "scratch_review/colocated/make_fixture.py)"),
        "recorded_at_s": pull["pulled_at_s"],
        "recorded_utc": __import__("time").strftime(
            "%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime(pull["pulled_at_s"])),
        "why": {"wfigs_points": WFIGS_POINTS, "wfigs_perimeters": WFIGS_PERIMETERS,
                "firis": {str(k): v for k, v in FIRIS.items()},
                "calfire": CALFIRE},
        "wfigs_points": points,
        "wfigs_perimeters": perims,
        "firis": firis,
        "calfire": calfire,
        "reconstructed": {"wfigs_bike_perimeter_2026_10_14": reconstructed},
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(OUT, os.path.getsize(OUT), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
