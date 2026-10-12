"""Crown-fire study: the engine laws on the real tiles (2026-10-12; NOT
product code; docs/review/crown_fire_model_2026-10-12.md section 1.7).

For one-click scenarios under the `crown` variant (grass-only floor +
spot_fire_size + crown_fire), on the committed tile with its LANDFIRE canopy
and the session's own weather and cfg:
  memo       the nominal field with the ROS memo = WGRESP_NO_ROS_MEMO=1
             reference (bit for bit; also with any-angle);
  reanchor   a no-op re-anchor at 1, 2 and 3 h (kept + kept_wx + embers
             aloft [+ anchors]) = the continuous nominal field, cell for
             cell;
  off        the knob off on the canopy tile = the canopy-less tile.

Usage: py -3 scratch_review/crown_fire_laws_real.py [scenarios]
Output: scratch_review/crown_fire_laws_real.json
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import crown_fire_scenarios as C  # noqa: E402
import ember_spotting_probe as E  # noqa: E402
from wgresponse import spread  # noqa: E402

OUT = os.path.join(HERE, "crown_fire_laws_real.json")
DEFAULT = ("park_city_windstorm", "vail_westerly", "paradise_ne_wind")


def run(scen):
    s, fid = E.build(scen, C.VARIANTS["crown"])
    f = s._fires[fid]
    tile, wx, cfg = s._tile, s._wx, s._cfg
    t0 = f.t_ignite_s
    out = {"scenario": scen}
    for aa in (False, True):
        c = dataclasses.replace(cfg, spread_any_angle=aa)
        os.environ.pop("WGRESP_NO_ROS_MEMO", None)
        log, alog = [], {}
        memo = spread.compute_arrival(tile, [f.origin], wx, t0, c,
                                      incident_id=fid, ember_log=log,
                                      anchor_log=alog).data
        os.environ["WGRESP_NO_ROS_MEMO"] = "1"
        ref = spread.compute_arrival(tile, [f.origin], wx, t0, c,
                                     incident_id=fid).data
        os.environ.pop("WGRESP_NO_ROS_MEMO", None)
        out[f"memo_equals_reference{'_any_angle' if aa else ''}"] = memo == ref
        t_hi = t0 + c.horizon_h * 3600.0
        re_ok = []
        for h in (1, 2, 3):
            t_a = t0 + h * 3600.0
            kept = {i: v for i, v in enumerate(memo) if v <= t_a}
            aloft = sorted({(i, land) for i, thrown, land in log
                            if thrown <= t_a < land and i not in kept})
            kw = dict(incident_id=fid, seed_spotting=False, kept=kept,
                      kept_wx=[(t0, wx.at(t0))] if False else None,
                      embers_aloft=aloft)
            # the session's weather is hourly: hand the history the way
            # simui does — the forecast's own samples up to t_a
            kw["kept_wx"] = [(smp.t_s, smp) for smp in wx.samples
                             if smp.t_s <= t_a] or [(t0, wx.at(t0))]
            if aa:
                kw["kept_anchor"] = alog
            got = spread.compute_arrival(tile, [], wx, t_a, c, **kw).data
            diff = sum(1 for x, y in zip(got, memo)
                       if (x <= t_hi or y <= t_hi) and x != y)
            re_ok.append({"t_h": h, "cells_differing": diff,
                          "kept": len(kept)})
        out[f"noop_reanchor{'_any_angle' if aa else ''}"] = re_ok
    off = dataclasses.replace(cfg, crown_fire=False)
    a = spread.compute_arrival(tile, [f.origin], wx, t0, off,
                               incident_id=fid).data
    b = spread.compute_arrival(dataclasses.replace(tile, canopy=None),
                               [f.origin], wx, t0, off, incident_id=fid).data
    out["knob_off_canopy_inert"] = a == b
    print(json.dumps(out), flush=True)
    return out


def main(argv):
    scens = argv[0].split(",") if argv else list(DEFAULT)
    rows = [run(s) for s in scens]
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
