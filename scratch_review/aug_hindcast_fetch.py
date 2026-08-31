"""STEP 2 — fetch perimeter time series + observed weather + LFPS terrain for
the 8 August-2026 incidents selected from aug_hindcast_discover.json (prefer
many-rows + large-acres + span >= 12h; capped at 8 to bound LFPS cost).
Clones oos_fetch_states.py's use of tools.hindcast_harness fetch helpers
verbatim; only the incident selection differs. data_dir is a FRESH directory
(scratch_review/hindcast_aug_data) — no stale cache from any prior run.

NOTE: Crosswhite-OR (irwin 1B0219EE...) is a CONTINUING incident already
used in the pre-cutoff OOS work (oos_fetch_states.py) — it is kept here
because it grew ~78k acres INTO August (277,296 ac at last use -> 355,121 ac
now), so it likely carries genuine new Aug-dated snapshot pairs. STEP 3's
t0 >= 2026-08-01 guard will exclude any pair that isn't actually new.

ROUND-2 ADDENDUM (after inspecting the round-1 fetch): pick_snapshot_times
walks each incident's FULL unfiltered time series greedily from its EARLIEST
row (max_snapshots=5 default) — for any fire already burning before 2026-08-01
that consumes the whole 5-snapshot budget on pre-August growth, even though
the fire had real August activity (discovery's t_min only reflects the
earliest row >= since_iso, not the fire's true start). Measured result:
LittleGiant-WA, Crosswhite-OR, Grasshopper-OR and Sinlahekin-WA all landed
ENTIRELY pre-August this way (zero t0>=2026-08-01 pairs). Sand Creek-MT,
Stallion-NV, Border2-WA, Ptarmigan-WA were added as replacements, chosen for
t_min well clear of 2026-08-01 (true fresh ignitions) and t_max well clear
of 2026-08-31 (inside the ERA5 archive lag window)."""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools.hindcast_harness as HH  # noqa: E402

PICKS = [
    ("{1B0219EE-5298-4FEF-9927-C2666D9D53FC}", "Crosswhite-OR", 355121.0),
    ("{79BBB8A2-E93C-4514-9DD2-55A7DE7F260C}", "ColemanCreek-OR", 309829.0),
    ("{7820BFDB-1EDD-43FE-9B6E-2BD23FA5AF15}", "BaldMountain-OR", 308721.0),
    ("{78D35D3B-F791-4961-AE36-C6D1A4DFF5A0}", "Sinlahekin-WA", 166868.0),
    ("{DB5448A6-FCAC-4041-A817-6F8198161DBA}", "Tartar-ID", 158027.0),
    ("{091081ED-BD23-4610-AE4A-270F95D1711E}", "LittleGiant-WA", 146042.0),
    ("{6AA4C355-AD18-472F-AAAB-78BAA9DE31B1}", "Grasshopper-OR", 93961.0),
    ("{286B7F1D-8945-4A5D-9D81-5235C18AF1FE}", "Bug-CA", 93847.0),
    # round 2 replacements (clean fresh-August ignitions):
    ("{84FD3D7E-6B04-49CD-A729-ED3178A49C42}", "SandCreek-MT", 34189.0),
    ("{75B0C6D7-10D4-476A-B691-3D78231B4997}", "Stallion-NV", 40691.0),
    ("{A60CA689-BD59-4A2D-9BDC-CC551CEAFCA4}", "Border2-WA", 22540.0),
    ("{735FFADD-9F45-4188-A66B-335931316E23}", "Ptarmigan-WA", 25166.0),
]

args = types.SimpleNamespace(data_dir="scratch_review/hindcast_aug_data",
                             http_budget=600, gust_blend=0.0)
HH._ensure_dirs(args.data_dir)
http = HH.CountingHttp(args.http_budget)
notes: list = []
for irwin, name, ac in PICKS:
    slug = HH._irwin_slug(irwin)
    inc_path = os.path.join(args.data_dir, "incidents", f"{slug}.json")
    try:
        if not os.path.exists(inc_path):
            HH._fetch_incident(http, irwin, name, ac, inc_path)
        else:
            print(f"[ok] {name}: perimeter cache hit")
        HH._fetch_weather(http, args, slug, name, inc_path)
        HH._fetch_terrain(http, args, slug, name, inc_path, notes)
        print(f"[ok] {name}")
    except Exception as e:
        print(f"[FAIL] {name}: {e}")
for n in notes:
    print("[note]", n)
print("requests:", http.n_requests)
