"""One hindcast replay for the stacked-rings before/after (2026-09-27).

The same loop as `tools/hindcast_harness.py replay` (cmd_replay): every
consecutive cached snapshot pair with 0 < dt <= horizon - 6 h, replayed by
hindcast_lib.replay_pair, optionally with the harness's widened-band variant.
It runs against the checkout given by --root (the pre-fix worktree for
"before", the fixed one for "after"): that checkout's tools/ and wgresponse/
are imported, and the corpus is read from <root>/<data-dir>. The output JSON
has the harness's hindcast-replay/2 shape, so pool_metrics and the review
scripts read it unchanged. Offline: cached perimeters, weather and tiles only.

    py -3 replay_job.py --root <checkout> --data-dir scratch_review/hindcast_oos_data \
        --label D_gate_me071 --cfg '{"wind_gust_blend": 0.5, ...}' [--wide] --out X.json
"""
import argparse
import dataclasses
import json
import os
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--cfg", required=True)
    ap.add_argument("--wide", action="store_true")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    sys.path.insert(0, root)
    os.chdir(root)
    import tools.hindcast_harness as HH
    from tools import hindcast_lib as H
    from wgresponse.contracts import LatLon, ResponseConfig
    from wgresponse.terrain import LfpsTerrain, load_ascii_tile

    over = json.loads(args.cfg)
    cfg = dataclasses.replace(ResponseConfig(), **over)
    wide_cfg = dataclasses.replace(cfg, **HH._WIDE_VARIANT)
    t_start = time.time()
    pairs, pairs_wide, skips = [], [], []
    for slug, inc in HH._load_all_incidents(args.data_dir):
        if getattr(inc, "stacked_legacy", None):
            skips.append(f"{inc.name}: pre-fix stacked file — NOT replayed")
            continue
        wx_path = os.path.join(args.data_dir, "weather", f"{slug}.json")
        if not os.path.exists(wx_path):
            skips.append(f"{inc.name}: no cached weather")
            continue
        wd = HH._read_json(wx_path)
        wx = H.parse_archive_weather(wd["payload"],
                                     LatLon(wd["loc"][0], wd["loc"][1]))
        bbox, cell_m = HH._incident_domain(inc, cfg)
        cp = LfpsTerrain(cache_dir=os.path.join(args.data_dir, "lfps")) \
            .cache_path(bbox, cell_m)
        if not (cp and os.path.exists(cp)):
            skips.append(f"{inc.name}: no cached LFPS tile")
            continue
        tile = load_ascii_tile(cp)
        for a, b in zip(inc.snapshots, inc.snapshots[1:]):
            dt_h = (b.t_s - a.t_s) / 3600.0
            if not (0.0 < dt_h <= cfg.horizon_h - 6.0):
                skips.append(f"{inc.name}: pair dt {dt_h:.1f} h outside "
                             f"(0, horizon-6] — skipped")
                continue
            pairs.append(H.replay_pair(tile, wx, a, b, cfg, inc.irwin,
                                       name=inc.name))
            if args.wide:
                pairs_wide.append(H.replay_pair(tile, wx, a, b, wide_cfg,
                                                inc.irwin, name=inc.name,
                                                label="wide"))
    out = {"schema": "hindcast-replay/2", "label": args.label,
           "config": over, "root": os.path.basename(root),
           "data_dir": args.data_dir,
           "elapsed_s": round(time.time() - t_start, 1),
           "pairs": [dataclasses.asdict(p) for p in pairs],
           "pairs_wide": [dataclasses.asdict(p) for p in pairs_wide],
           "skips": skips}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    os.replace(tmp, args.out)
    print(f"[{args.label}] {len(pairs)} pairs in {out['elapsed_s']} s -> "
          f"{args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
