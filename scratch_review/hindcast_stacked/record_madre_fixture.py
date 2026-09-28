"""Record the Madre Fire (2025, CA) WFIGS Daily-perimeter payloads that pin
the stacked-rings fix (tests/test_hindcast_snapshot_rule.py).

Two read-only GETs through the harness's own URL builders and budgeted
client (tools/hindcast_harness.CountingHttp): the attribute series exactly as
`_fetch_incident` asks for it, and the geometry of the rows under Madre's
first four capture times (the same geometry query, same generalisation).
Payloads are stored verbatim.

    py -3 scratch_review/hindcast_stacked/record_madre_fixture.py
"""
import json
import os
import sys
from datetime import datetime, timezone

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO)
import tools.hindcast_harness as HH  # noqa: E402
from tools import hindcast_lib as H  # noqa: E402

IRWIN = "{1AB682F7-CC0F-4DCE-87A4-FA4A9B67AB05}"
# 2025-07-02 15:51 (2 rows), 07-03 09:38, 07-03 16:16, 07-04 23:37 (4 rows)
OIDS = [37051, 37103, 37108, 37198, 37264, 37289, 37304, 37388]
OUT = os.path.join(_REPO, "tests", "fixtures", "hindcast_madre_daily_2026_09_27.json")

http = HH.CountingHttp(2)
attrs = HH._get_json(http, H.series_attrs_url(IRWIN))
geom = HH._get_json(http, H.geometry_query_url(OIDS))
now = datetime.now(timezone.utc)
doc = {
    "schema": "wgresponse-hindcast-daily-fixture/1",
    "recorded_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "recorded_at_s": int(now.timestamp()),
    "provenance": (
        "NIFC WFIGS_Daily_Perimeters_Public (FeatureServer/0), Madre Fire "
        f"{IRWIN}: attrs = hindcast_lib.series_attrs_url, geometry = "
        f"hindcast_lib.geometry_query_url(OBJECTIDs {OIDS}); payloads verbatim; "
        "recorded by scratch_review/hindcast_stacked/record_madre_fixture.py"),
    "irwin": IRWIN,
    "attrs_payload": attrs,
    "geometry_payload": geom,
}
with open(OUT, "w", encoding="utf-8", newline="\n") as f:
    json.dump(doc, f, indent=None, separators=(",", ":"), sort_keys=True)
print(f"wrote {OUT} ({os.path.getsize(OUT):,} bytes); {http.n_requests} GETs; "
      f"{len(attrs.get('features') or [])} attr rows, "
      f"{len(geom.get('features') or [])} geometry features")
