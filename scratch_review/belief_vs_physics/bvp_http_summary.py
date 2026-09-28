"""HTTP provenance summary of every fetch invocation of this study (the
per-invocation logs bvp_fetch.py writes under out/bvp_data/fetch_logs/):
request counts by invocation and by host. Writes http_summary.json."""
import collections
import glob
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
logs = sorted(glob.glob(os.path.join(_REPO, "out", "bvp_data", "fetch_logs",
                                     "*.json")))
out = {"invocations": {}, "by_host": {}, "total": 0}
hosts = collections.Counter()
for p in logs:
    d = json.load(open(p))
    name = os.path.basename(p)[:-5]
    out["invocations"][name] = {"n_requests": d["n_requests"],
                                "notes": d.get("notes") or []}
    out["total"] += d["n_requests"]
    for r in d["requests"]:
        hosts[r["url"].split("/")[2]] += 1
out["by_host"] = dict(hosts)
for k, v in out["invocations"].items():
    print(f"{k:40s} {v['n_requests']:4d}")
print("total", out["total"], out["by_host"])
json.dump(out, open(os.path.join(_HERE, "http_summary.json"), "w"), indent=1,
          sort_keys=True)
