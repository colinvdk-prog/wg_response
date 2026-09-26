"""scratch_review/roads_offroad_compare.py — before/after tables for the roads
inspection (docs/review/roads_inspection_2026-09-25.md) from the
roads_offroad_probe.py JSONs, plus the byte-identity verdict per session.

    py -3 scratch_review/roads_offroad_compare.py
Prints markdown tables; reads scratch_review/roads_offroad_probe_<tag>.json
for the tags before / after / before_extstress / after_extstress /
after_snap / after_extstress_snap.
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from scratch_review.roads_offroad_probe import summarize  # noqa: E402


def load(tag):
    path = os.path.join(_HERE, f"roads_offroad_probe_{tag}.json")
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    runs = {(r["key"], r["zone_req"]): r for r in d["runs"]}
    summ = {(s["key"], s["zone_req"]): s for s in (summarize(r) for r in d["runs"])}
    return runs, summ


def z(zone):
    return "ext" if zone == "extended" else "std"


def identity(rb, ra):
    hb, ha = rb["hashes"], ra["hashes"]
    if hb == ha:
        return f"identical ({len(ha)} ticks)"
    first = next(i for i, (x, y) in enumerate(zip(hb, ha)) if x != y)
    return f"changed from tick {first}"


def main():
    rb, sb = load("before")
    ra, sa = load("after")
    keys = sorted(sb)
    print("### Rendering (what the page draws) — before -> after\n")
    print("| preset | zone | moving crew-ticks | tween cut >250 m | max tween cut km | "
          "trail cut >250 m | max trail cut km | jump slides (max km) | "
          "queued-stop chords (max km) | relocation chords |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    tot = {"tw": [0, 0], "tr": [0, 0], "js": [0, 0], "q": [0, 0], "es": [0, 0], "mv": [0, 0]}
    for k in keys:
        b, a = sb[k], sa[k]
        print(f"| {k[0]} | {z(k[1])} | {b['moving_ticks']} -> {a['moving_ticks']} | "
              f"{b['tween_cut_gt250']} -> {a['tween_cut_gt250']} | "
              f"{b['tween_cut_max_km']} -> {a['tween_cut_max_km']} | "
              f"{b['trail_cut_gt250']} -> {a['trail_cut_gt250']} | "
              f"{b['trail_cut_max_km']} -> {a['trail_cut_max_km']} | "
              f"{b['jump_slides']} ({b['jump_slide_max_km']}) -> {a['jump_slides']} "
              f"({a['jump_slide_max_km']}) | "
              f"{b['queue_chords']} ({b['queue_chord_max_km']}) -> {a['queue_chords']} "
              f"({a['queue_chord_max_km']}) | "
              f"{b['egress_synth']} -> {a['egress_synth']} |")
        for key, fld in (("tw", "tween_cut_gt250"), ("tr", "trail_cut_gt250"),
                         ("js", "jump_slides"), ("q", "queue_chords"),
                         ("es", "egress_synth"), ("mv", "moving_ticks")):
            tot[key][0] += b[fld]
            tot[key][1] += a[fld]
    print(f"| **total** | | {tot['mv'][0]} -> {tot['mv'][1]} | **{tot['tw'][0]} -> {tot['tw'][1]}** | | "
          f"**{tot['tr'][0]} -> {tot['tr'][1]}** | | **{tot['js'][0]} -> {tot['js'][1]}** | "
          f"**{tot['q'][0]} -> {tot['q'][1]}** | **{tot['es'][0]} -> {tot['es'][1]}** |")

    print("\n### Engine (what the crews drive) — before -> after\n")
    print("| preset | zone | legs | chord legs (km) | spur km | spur p90 m | spur max m | "
          "spurs >2 km | escape legs (straight km) | homes >300 m / >2 km from a road | "
          "violations | served | engine bytes |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k in keys:
        b, a = sb[k], sa[k]
        f = lambda v: "-" if v is None else f"{v:.0f}"
        print(f"| {k[0]} | {z(k[1])} | {b['legs']} -> {a['legs']} | "
              f"{b['chord']} ({b['km_chord']}) -> {a['chord']} ({a['km_chord']}) | "
              f"{b['km_spur']} -> {a['km_spur']} | {f(b['spur_p90'])} -> {f(a['spur_p90'])} | "
              f"{f(b['spur_max'])} -> {f(a['spur_max'])} | {b['spur_gt2k']} -> {a['spur_gt2k']} | "
              f"{b['escape_legs']} ({b['escape_straight_km']}) -> {a['escape_legs']} "
              f"({a['escape_straight_km']}) | "
              f"{b['homes_gt300']}/{b['homes_gt2k']} -> {a['homes_gt300']}/{a['homes_gt2k']} | "
              f"{b['violations']} -> {a['violations']} | {b['served']} -> {a['served']} | "
              f"{identity(rb[k], ra[k])} |")

    for pre, post, title in (("before_extstress", "after_extstress",
                              "Extended fire zone, EXTREME, 14 km ignition, 80 ticks"),):
        rb2, sb2 = load(pre)
        ra2, sa2 = load(post)
        print(f"\n### {title} — before -> after\n")
        print("| preset | chord legs (km) | escape legs (straight km) | legs leaving the "
              "graph bbox | tween cut >250 m | jump slides (max km) | violations | served |")
        print("|---|---|---|---|---|---|---|---|")
        for k in sorted(sb2):
            b, a = sb2[k], sa2[k]
            print(f"| {k[0]} | {b['chord']} ({b['km_chord']}) -> {a['chord']} ({a['km_chord']}) | "
                  f"{b['escape_legs']} ({b['escape_straight_km']}) -> {a['escape_legs']} "
                  f"({a['escape_straight_km']}) | "
                  f"{b['egress_out_bbox'] + b['ingress_out_bbox']} -> "
                  f"{a['egress_out_bbox'] + a['ingress_out_bbox']} | "
                  f"{b['tween_cut_gt250']} -> {a['tween_cut_gt250']} | "
                  f"{b['jump_slides']} ({b['jump_slide_max_km']}) -> {a['jump_slides']} "
                  f"({a['jump_slide_max_km']}) | {b['violations']} -> {a['violations']} | "
                  f"{b['served']} -> {a['served']} |")

    print("\n### escape_road_snap_m = 1500 (default OFF) — A/B on the after code\n")
    print("| run | preset | zone | escape legs (straight km) | chord legs (km) | spurs >2 km | "
          "violations | served | relocations |")
    print("|---|---|---|---|---|---|---|---|---|")
    for off_tag, on_tag, label in (("after", "after_snap", "40 ticks high"),
                                   ("after_extstress", "after_extstress_snap",
                                    "ext extreme 80 ticks")):
        _ro, so = load(off_tag)
        _rn, sn = load(on_tag)
        for k in sorted(so):
            o, n = so[k], sn[k]
            if not (o["escape_legs"] or n["escape_legs"] or o["chord"] or n["chord"]):
                continue
            print(f"| {label} | {k[0]} | {z(k[1])} | {o['escape_legs']} ({o['escape_straight_km']}) -> "
                  f"{n['escape_legs']} ({n['escape_straight_km']}) | {o['chord']} ({o['km_chord']}) -> "
                  f"{n['chord']} ({n['km_chord']}) | {o['spur_gt2k']} -> {n['spur_gt2k']} | "
                  f"{o['violations']} -> {n['violations']} | {o['served']} -> {n['served']} | "
                  f"{o['relocations']} -> {n['relocations']} |")
        # (whole-state hashes always differ here: cmd_log echoes the extra
        # cfg override; the rows above are the sessions where it acted)


if __name__ == "__main__":
    main()
