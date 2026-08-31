"""STEP 1 — discover August 2026 WFIGS candidate incidents across 8 western
states, NEVER seen by any calibration (last scored window ended 2026-07-24).
Mirrors oos_fetch_states.py's use of tools.hindcast_harness + the
H.candidates_query_url discovery probe (see tools/hindcast_lib.py). AZ is
skipped (known corrupt timestamps, per hindcast_oos ship log).

Read-only: one ArcGIS stats GET per state (8 total). Prints + writes
scratch_review/aug_hindcast_discover.json — every raw candidate plus which
ones pass the gate (n_rows >= 3 AND span >= 6h AND t_min >= 2026-08-01)."""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools.hindcast_harness as HH  # noqa: E402
from tools import hindcast_lib as H  # noqa: E402

STATES = ["US-CA", "US-OR", "US-WA", "US-ID", "US-MT", "US-NV", "US-UT", "US-CO"]
SINCE_ISO = "2026-08-01 00:00:00"
SINCE_S = datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc).timestamp()
MIN_ROWS = 3
MIN_SPAN_H = 6.0

OUT = "scratch_review/aug_hindcast_discover.json"

http = HH.CountingHttp(60)
all_rows = []
kept_rows = []
for state in STATES:
    url = H.candidates_query_url(min_acres=1000.0, state=state,
                                  since_iso=SINCE_ISO, limit=25)
    try:
        cands = H.parse_candidates(HH._get_json(http, url))
    except Exception as e:
        print(f"[FAIL] {state}: {e}")
        continue
    print(f"=== {state}: {len(cands)} raw candidates ===")
    for irwin, name, n_rows, max_acres, t_min, t_max in cands:
        span_h = (t_max - t_min) / 3600.0
        ok = (n_rows >= MIN_ROWS and span_h >= MIN_SPAN_H and t_min >= SINCE_S)
        t0 = datetime.datetime.fromtimestamp(t_min, tz=datetime.timezone.utc)
        t1 = datetime.datetime.fromtimestamp(t_max, tz=datetime.timezone.utc)
        flag = "KEEP" if ok else "skip"
        print(f"  [{flag}] {name:<26} {irwin}  rows={n_rows:<3} "
              f"acres={max_acres:>9,.0f}  span={span_h:6.1f}h  "
              f"[{t0:%m-%d %H:%M} .. {t1:%m-%d %H:%M}]")
        row = dict(state=state, irwin=irwin, name=name, n_rows=n_rows,
                   max_acres=max_acres, t_min=t_min, t_max=t_max,
                   span_h=span_h, kept=ok)
        all_rows.append(row)
        if ok:
            kept_rows.append(row)

print(f"\n=== KEPT ({len(kept_rows)}) sorted by acres desc ===")
for r in sorted(kept_rows, key=lambda r: -r["max_acres"]):
    print(f"  {r['name']:<26} {r['state']}  rows={r['n_rows']:<3} "
          f"acres={r['max_acres']:>9,.0f}  span={r['span_h']:6.1f}h  "
          f"irwin={r['irwin']}")

print(f"\nrequests: {http.n_requests}")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump({"states": STATES, "since_iso": SINCE_ISO, "all": all_rows,
           "kept": kept_rows}, open(OUT, "w"), indent=2)
print("wrote", OUT)
