"""BUILD-3 (feeds), item 8b: why the raw FIRMS log's `gone` check runs in so
few polls (SAFETY SEMANTICS 43, screening.FirmsPollLog.poll: a poll that
loses more than 20 % of the window's rows at once skips the check).
Reads lane A's S3 copies (A_detection/firms_log_raw, read-only) and, per
mass-missing poll, compares the rows missing with the NEW rows that same
poll logged: are the missing rows URT detections that came back as NRT rows
of the same acquisition (re-geolocated: same acq_date / acq_time /
satellite, a few hundred metres away)?

Run: python <this> <firms_log_raw dir>   -> prints a summary"""
import collections
import gzip
import json
import math
import os
import re
import sys


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def main(d):
    polls = collections.Counter()
    mass = []
    new_by_poll = collections.defaultdict(list)    # (src, t) -> [row]
    events = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".jsonl.gz"):
            continue
        with gzip.open(os.path.join(d, name), "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                events.append(ev)
                if ev.get("ev") == "new":
                    new_by_poll[(ev["src"], ev["t_s"])].append(ev["row"])
    # replay the seen set per source to find the rows missing at each poll:
    # a row is in-window seen until a `gone`; we approximate "missing" at a
    # mass-missing poll as the URT rows seen before that poll whose
    # acquisition reappears among the poll's NEW rows as a non-URT row
    seen = collections.defaultdict(dict)            # src -> key -> row
    regeo_total = missing_total = 0
    per_poll = []
    for ev in events:
        k = ev.get("ev")
        src = ev.get("src")
        if k in ("new", "changed"):
            r = ev["row"]
            seen[src][(r["latitude"], r["longitude"], r["acq_date"],
                       r["acq_time"])] = r
        elif k == "gone":
            seen[src].pop(tuple(ev["key"]), None)
        elif k == "poll":
            why = ev.get("gone_skipped") or ""
            polls["checked" if not why else
                  ("mass-missing" if "missing at once" in why else why)] += 1
            m = re.match(r"(\d+) of (\d+) rows missing", why)
            if not m:
                continue
            n_missing = int(m.group(1))
            news = new_by_poll.get((src, ev["t_s"]), [])
            acq = collections.defaultdict(list)
            for r in news:
                acq[(r["acq_date"], r["acq_time"], r.get("satellite"))].append(
                    (float(r["latitude"]), float(r["longitude"]),
                     str(r.get("version", ""))))
            regeo = 0
            for key, r in seen[src].items():
                if not str(r.get("version", "")).endswith("URT"):
                    continue
                cands = acq.get((r["acq_date"], r["acq_time"],
                                 r.get("satellite")), [])
                p = (float(r["latitude"]), float(r["longitude"]))
                if any(not v.endswith("URT") and km(p, (la, lo)) <= 1.5
                       for la, lo, v in cands):
                    regeo += 1
            per_poll.append((n_missing, regeo, len(news)))
            regeo_total += min(regeo, n_missing)
            missing_total += n_missing
    print("polls by gone-check outcome:", dict(polls))
    print(f"mass-missing polls: {len(per_poll)}; rows missing in them: "
          f"{missing_total:,}; of which an NRT row of the same acquisition "
          f"within 1.5 km arrived in the same poll (re-geolocated): "
          f"{regeo_total:,} ({100.0 * regeo_total / max(1, missing_total):.1f} %)")
    share = sorted(r / n for n, r, _ in per_poll if n)
    if share:
        print("per mass-missing poll, re-geolocated share of the missing: "
              f"p10 {share[len(share) // 10]:.2f} p50 "
              f"{share[len(share) // 2]:.2f} p90 "
              f"{share[9 * len(share) // 10]:.2f}")


if __name__ == "__main__":
    main(sys.argv[1])
