"""Live feeds freshness smoke test (READ-ONLY GETs to public keyless services).

Pulls WFIGS + FIRIS + CAL FIRE for a SoCal bbox and a CA-wide bbox, times each,
reports updated_at_s recency distribution and fusion provenance survival.
Also documents the FirmsFeed MAP_KEY constructor requirement.
"""
import json
import statistics
import sys
import time

sys.path.insert(0, r"C:\Users\colin\wg_response")

from wgresponse.contracts import BBox
from wgresponse import feeds

NOW = time.time()


def age_h(t):
    return (NOW - t) / 3600.0


def summarize(name, batch):
    if not batch:
        print(f"  {name}: 0 incidents")
        return
    ages = sorted(age_h(i.updated_at_s) for i in batch if i.updated_at_s > 0)
    n_perim = sum(1 for i in batch if i.perimeter)
    n_zero_ts = sum(1 for i in batch if i.updated_at_s <= 0)
    med = statistics.median(ages) if ages else float("nan")
    print(f"  {name}: {len(batch)} incidents, {n_perim} with perimeter, "
          f"{n_zero_ts} with updated_at_s<=0")
    if ages:
        print(f"    updated_at age (h): fresh={ages[0]:.2f} median={med:.2f} "
              f"stalest={ages[-1]:.2f}")


def main():
    ca = BBox(west=-124.5, south=32.4, east=-114.0, north=42.1)

    results = {}
    for name, feed in (
        ("WFIGS", feeds.WfigsFeed()),
        ("FIRIS", feeds.FirisFeed()),
        ("CALFIRE", feeds.CalFireFeed()),
    ):
        t0 = time.perf_counter()
        batch = feed.fetch_active(ca)
        dt = time.perf_counter() - t0
        print(f"[{name}] fetch {dt:.1f}s")
        summarize(name, batch)
        results[name] = batch

    # FIRMS: constructor requires map_key positional
    try:
        feeds.FirmsFeed()  # type: ignore[call-arg]
        print("[FIRMS] constructed WITHOUT map key (unexpected)")
    except TypeError as e:
        print(f"[FIRMS] requires MAP_KEY at construction: TypeError: {e}")

    # what does the CLI do with FirmsFeed? (mirrors cli._cmd_pull_feeds)
    try:
        cls = feeds.FirmsFeed
        cls().fetch_active(ca)
    except Exception as e:
        print(f"[FIRMS] CLI-style bare construction fails with: {type(e).__name__}: {e}")

    t0 = time.perf_counter()
    fused = feeds.fuse_incidents(list(results.values()))
    dt = time.perf_counter() - t0
    print(f"\n[FUSE] {len(fused)} fused incidents in {dt:.2f}s")
    multi = [i for i in fused if len(i.source_ids) > 1]
    print(f"[FUSE] {len(multi)} incidents with multi-source provenance")
    for i in multi[:6]:
        print(f"   {i.incident_id[:36]:<38} {i.name[:24]:<26} "
              f"sources={sorted(i.source_ids)} age_h={age_h(i.updated_at_s):.2f}")

    ages = sorted(age_h(i.updated_at_s) for i in fused if i.updated_at_s > 0)
    if ages:
        print(f"[FUSE] fused updated_at age (h): fresh={ages[0]:.2f} "
              f"median={statistics.median(ages):.2f} stalest={ages[-1]:.2f}")

    # persist candidates for the e2e pick: active CA incidents with perimeters,
    # sorted freshest first
    cands = sorted((i for i in fused if i.perimeter),
                   key=lambda i: -i.updated_at_s)[:15]
    out = [{
        "incident_id": i.incident_id, "name": i.name,
        "lat": i.origin.lat, "lon": i.origin.lon,
        "age_h": round(age_h(i.updated_at_s), 2),
        "acreage": i.acreage, "containment": i.containment_pct,
        "sources": sorted(i.source_ids),
    } for i in cands]
    with open(r"C:\Users\colin\wg_response\scratch_review\feed_candidates.json",
              "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print("\n[FUSE] freshest perimeter incidents (e2e candidates):")
    for c in out[:8]:
        print(f"   {c['name'][:26]:<28} ({c['lat']:.3f},{c['lon']:.3f}) "
              f"age={c['age_h']:>6.2f}h acres={c['acreage']} src={c['sources']}")


if __name__ == "__main__":
    main()
