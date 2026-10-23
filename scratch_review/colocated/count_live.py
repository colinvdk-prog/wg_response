"""Count what co-located fusion (contracts SAFETY SEMANTICS 24) merges on a
recorded live pull: legacy fusion vs fuse_incidents(cfg=knob on, now_s=the
pull's clock), every union with its members and evidence, and the incidents
the SEMANTICS 9 gate keeps before / after.

    py -3 scratch_review/colocated/count_live.py out/colocated/raw_<stamp>.json
    py -3 scratch_review/colocated/count_live.py <feedsnap snapshot dir>

A raw_*.json is pull_live.py's recording; a directory is a
scratch_review/contained/feedsnap.py snapshot (manifest.json + bodies).
Replays offline through the REAL adapters; no network.
"""
import dataclasses
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from wgresponse import feeds  # noqa: E402
from wgresponse.contracts import BBox, ResponseConfig  # noqa: E402

CONUS = BBox(west=-125.0, south=24.0, east=-66.0, north=50.0)


def _replay(src: str):
    """(http_get, bbox, pulled_at_s) replaying a recorded pull."""
    if os.path.isdir(src):
        man = json.load(open(os.path.join(src, "manifest.json")))
        bodies = {k.split("@")[0]: os.path.join(src, v["file"])
                  for k, v in man["bodies"].items()}

        def get(url, headers=None):
            for key, path in bodies.items():
                stem = key.split("_api_")[0]
                if (stem in url or stem.replace("_", "/") in url
                        or ("incidents.fire.ca.gov" in url
                            and key.startswith("incidents.fire.ca.gov"))):
                    with open(path, "rb") as fh:
                        return 200, fh.read()
            raise KeyError(url)
        return get, CONUS, float(man["recorded_at_s"])
    pull = json.load(open(src, encoding="utf-8"))
    by_url = {r["url"]: r for r in pull["responses"]}

    def get(url, headers=None):
        r = by_url[url]
        return r["status"], r["body"].encode("utf-8")
    return get, BBox(*pull["bbox"]), float(pull["pulled_at_s"])


def main(src: str) -> int:
    get, bbox, now = _replay(src)
    batches = []
    for feed in (feeds.WfigsFeed(http_get=get), feeds.FirisFeed(http_get=get),
                 feeds.CalFireFeed(http_get=get)):
        batches.append(feed.fetch_active(bbox))
        assert feed.last_fetch_ok, type(feed).__name__
    cfg_off = ResponseConfig()
    cfg_on = dataclasses.replace(cfg_off, fuse_colocated_incidents=True)
    stale_s = cfg_off.incident_stale_h * 3600.0

    def kept(fused):
        return [i for i in fused if i.active and now - i.updated_at_s <= stale_s]

    legacy = feeds.fuse_incidents(batches)
    merges: list = []
    fused = feeds.fuse_incidents(batches, cfg=cfg_on, now_s=now, merges=merges)
    pairs = sum(len(m["merged"]) - 1 for m in merges)
    print(f"pull {src}")
    print(f"raw records {[len(b) for b in batches]} (WFIGS, FIRIS, CAL FIRE)")
    print(f"legacy fused incidents {len(legacy)}; gate keeps {len(kept(legacy))}")
    print(f"knob on: fused {len(fused)}; gate keeps {len(kept(fused))}; "
          f"unions {len(merges)}; duplicate pairs merged {pairs}")
    by_id = {i.incident_id: i for i in fused}
    for m in merges:
        inc = by_id[m["incident_id"]]
        print(f"  {m['incident_id']} '{m['name']}' sources "
              f"{sorted(inc.source_ids)} evidence {'+'.join(m['evidence'])}")
        for iid, name in zip(m["merged"], m["names"]):
            print(f"      <- {iid} ({name})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
