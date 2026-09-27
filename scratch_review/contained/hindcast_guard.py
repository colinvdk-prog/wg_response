"""Hindcast guard for the SAFETY SEMANTICS 13 knobs (2026-09-27; NOT product
code). Runs tools/hindcast_harness.py `replay` unchanged, but with
CONTAINED_KNOBS=1 the variant config also carries drop_contained_incidents
+ perimeter_confirmed_anchor. The harness seeds each pair's snap0 polygon at
snap0.t_s through spread.ensemble_arrival directly — never through
ThreatMonitor — so the knobs cannot apply; the scored pairs must come out
byte-identical (compare with compare_guard())."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, WT)

from tools import hindcast_harness as HH  # noqa: E402

_orig = HH._variant_config_kwargs


def _patched(args):
    kw = _orig(args)
    if os.environ.get("CONTAINED_KNOBS") == "1":
        kw.update(drop_contained_incidents=True,
                  perimeter_confirmed_anchor=True)
    return kw


def compare_guard(a_path, b_path):
    a = json.load(open(a_path, encoding="utf-8"))
    b = json.load(open(b_path, encoding="utf-8"))
    same = (a["pairs"] == b["pairs"] and a["pairs_wide"] == b["pairs_wide"]
            and a["skips"] == b["skips"])
    print("pairs", len(a["pairs"]), "wide", len(a["pairs_wide"]),
          "skips", len(a["skips"]), "IDENTICAL" if same else "DIFFERENT")
    print("config off:", a["config"])
    print("config on :", b["config"])
    return same


if __name__ == "__main__":
    if sys.argv[1:2] == ["compare"]:
        sys.exit(0 if compare_guard(sys.argv[2], sys.argv[3]) else 1)
    HH._variant_config_kwargs = _patched
    sys.exit(HH.main(sys.argv[1:]))
