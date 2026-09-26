"""scratch_review/escape_roads_compare.py — before/after tables for the SEM-10d
road-network escape from scratch_review/roads_offroad_probe.py outputs.

  py -3 scratch_review/escape_roads_compare.py --base escroads_off --arms escroads_on escroads_on48 \
      [--committed after] [--label standard]

Per preset/zone: escape legs (how many end ON a road node), straight escape km
(spur + chord), whole-leg chord legs (km), relocations, 10c crew-overrun rows
summed over every tick (and by reason/mode), max departure-audit rows in any
tick, final violations, served. --committed checks the base arm's per-tick
state hashes (map_view stripped) against a committed probe JSON — the
knob-off arm must reproduce the pre-change engine byte for byte.
"""
from __future__ import annotations

import argparse
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SR = ROOT / "scratch_review"


def load(tag):
    d = json.load(open(SR / f"roads_offroad_probe_{tag}.json", encoding="utf-8"))
    runs = {(r["key"], r["zone_req"]): r for r in d["runs"]}
    summ = {(s["key"], s["zone_req"]): s for s in d["summary"]}
    return d, runs, summ


def cell(s):
    if s is None:
        return "—"
    esc = s["escape_legs"]
    on = s.get("escape_on_node", 0)
    return {
        "esc": f"{esc} ({on} on a node)",
        "straight": f"{s['escape_straight_km']}",
        "chord": f"{s['chord']} ({s['km_chord']})",
        "reloc": f"{s['relocations']}",
        "rows": f"{s.get('overrun_rows', '?')}",
        "dep": f"{s.get('departure_viol_max', '?')}",
        "viol": f"{s['violations']}",
        "served": f"{s['served']}",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--arms", nargs="+", required=True)
    ap.add_argument("--committed")
    ap.add_argument("--engine-ref", help="a probe JSON (path) run on the PRE-CHANGE "
                    "tree: the base arm's per-tick engine hashes (map_view and "
                    "cmd_log stripped) must equal it session for session")
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    _bd, bruns, bsum = load(a.base)
    arms = [(t,) + load(t)[1:] for t in a.arms]
    if a.engine_ref:
        ref = json.load(open(a.engine_ref, encoding="utf-8"))
        rruns = {(r["key"], r["zone_req"]): r for r in ref["runs"]}
        same = 0
        for k, r in sorted(bruns.items()):
            c = rruns.get(k)
            if c is None:
                print(f"  {k}: not in the engine reference")
                continue
            if r["engine_hashes"] == c["engine_hashes"]:
                same += 1
            else:
                first = next((i for i, (x, y) in enumerate(
                    zip(r["engine_hashes"], c["engine_hashes"])) if x != y), None)
                print(f"  ENGINE DIFFERS {k}: first at tick {first}")
        print(f"ENGINE byte identity {a.base} vs {a.engine_ref}: {same} of "
              f"{len(bruns)} sessions identical at every tick "
              f"({sum(len(r['engine_hashes']) for r in bruns.values())} states)")
    if a.committed:
        _cd, cruns, _cs = load(a.committed)
        same = diff = 0
        for k, r in bruns.items():
            c = cruns.get(k)
            if c is None:
                continue
            if r["hashes"] == c["hashes"]:
                same += 1
            else:
                diff += 1
                first = next(i for i, (x, y) in enumerate(zip(r["hashes"], c["hashes"])) if x != y)
                print(f"  NOT byte-identical to {a.committed}: {k} first differs at tick {first}")
        print(f"byte identity {a.base} vs committed {a.committed}: {same} identical "
              f"sessions, {diff} differ")
    for t, runs, _s in arms:
        ident = [k for k, r in runs.items() if bruns.get(k) and r["hashes"] == bruns[k]["hashes"]]
        print(f"{t}: {len(ident)} of {len(runs)} sessions byte-identical to {a.base} "
              f"(no area escape happened in them)")
    cols = ["esc", "straight", "chord", "reloc", "rows", "dep", "viol", "served"]
    head = ("| preset | zone | escape legs | straight escape km | whole-leg chords (km) "
            "| relocations | 10c rows (all ticks) | max departure rows | final violations "
            "| served |")
    print(f"\n{a.label} — {a.base} → " + " / ".join(t for t, *_ in arms))
    print(head)
    print("|---" * 10 + "|")
    tot = {}
    for k in sorted(bsum):
        b = cell(bsum[k])
        row = [k[0], "std" if k[1] == "standard" else "ext"]
        for c in cols:
            vals = [b[c]] + [cell(s.get(k))[c] if s.get(k) else "—" for _t, _r, s in arms]
            row.append(" → ".join(vals))
        print("| " + " | ".join(row) + " |")
        for name, s in [(a.base, bsum[k])] + [(t, s2.get(k)) for t, _r, s2 in arms]:
            if s is None:
                continue
            T = tot.setdefault(name, {"esc": 0, "on": 0, "straight": 0.0, "chords": 0,
                                      "chord_km": 0.0, "reloc": 0, "rows": 0, "dep": 0,
                                      "viol": 0, "served": 0, "kinds": {}})
            T["esc"] += s["escape_legs"]
            T["on"] += s.get("escape_on_node", 0)
            T["straight"] += s["escape_straight_km"]
            T["chords"] += s["chord"]
            T["chord_km"] += s["km_chord"]
            T["reloc"] += s["relocations"]
            T["rows"] += s.get("overrun_rows") or 0
            T["dep"] = max(T["dep"], s.get("departure_viol_max") or 0)
            T["viol"] += s["violations"]
            T["served"] += s["served"]
            for kk, n in (s.get("overrun_kinds") or {}).items():
                T["kinds"][kk] = T["kinds"].get(kk, 0) + n
    print("\nTOTALS")
    for name, T in tot.items():
        print(f"  {name}: escape legs {T['esc']} ({T['on']} on a node), straight escape "
              f"km {T['straight']:.1f}, chord legs {T['chords']} ({T['chord_km']:.1f} km), "
              f"relocations {T['reloc']}, 10c rows {T['rows']} {T['kinds']}, max departure "
              f"rows {T['dep']}, final violations {T['viol']}, served {T['served']}")


if __name__ == "__main__":
    main()
