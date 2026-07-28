"""Out-of-sample hindcast fetch — post-calibration (2026-07-13+) fires
OUTSIDE the CA-only discovery path. Reuses the harness fetch helpers
verbatim; only the incident selection is hand-picked (multi-capture,
post-calibration, clean timestamps; AZ skipped for future-dated rows)."""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools.hindcast_harness as HH  # noqa: E402

PICKS = [
    ("{B1B09572-CD77-4B0A-8E06-71284617A28F}", "Cottonwood-UT", 97464.0),
    ("{A32B1A1F-AF64-4903-9B75-8BCB276EB1DE}", "Stookey-UT", 11881.0),
    ("{1CDF5E5A-F22E-4352-A582-C2A47663B93D}", "AspenAcres-CO", 101921.0),
    ("{548A8D28-1A04-48EE-9011-ABA023175D43}", "Willow-CO", 7196.0),
    ("{1B0219EE-5298-4FEF-9927-C2666D9D53FC}", "Crosswhite-OR", 277296.0),
    ("{EE6FCF97-7637-4721-B125-741098779D99}", "PorcupineRidge-OR", 80082.0),
    ("{B5597100-D6C9-49F9-BC74-6B8FC4432E92}", "KaiserCanyon-WA", 126402.0),
    ("{CCEFD7D4-FEBD-496D-855E-CA0C9944BF0C}", "Brewer-OR", 70821.0),
]

args = types.SimpleNamespace(data_dir="scratch_review/hindcast_oos_data",
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
