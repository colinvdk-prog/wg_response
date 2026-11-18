"""Write the before / after tables of every set to a markdown file (the
RESULTS.md body). NOT product code. Usage: python tables.py ARM OUT.md
ARM: after (the shipped arming) | arm46b (46b armed too)."""
from __future__ import annotations

import contextlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SETS = (("valley", "valley_0713", "the valley's 33 fires, 12 h, 07:13 start"),
        ("valley", "valley_1313", "the valley's 33 fires, 12 h, 13:13 start"),
        ("valley", "valley_24h", "the valley's 33 fires, 24 h, 07:13 start"),
        ("lanea", "main", "lane A's 32 non-valley fires"),
        ("lanea", "more24", "24-h fires on six more maps"),
        ("lanea", "valley24", "lane A's 24-h valley pair"))


def main(argv):
    import summarize_fix
    arm, out = argv[0], argv[1]
    buf = io.StringIO()
    for kind, name, title in SETS:
        a = os.path.join(HERE, "runs", "before_" + name)
        b = os.path.join(HERE, "runs", f"{arm}_{name}")
        if not (os.path.exists(os.path.join(a, "runs.jsonl"))
                and os.path.exists(os.path.join(b, "runs.jsonl"))):
            continue
        n_a = sum(1 for _ in open(os.path.join(a, "runs.jsonl")))
        n_b = sum(1 for _ in open(os.path.join(b, "runs.jsonl")))
        if n_b != n_a:
            buf.write(f"### {title} — {arm}: {n_b} of {n_a} runs (stopped), "
                      f"not tabled\n\n")
            continue
        buf.write(f"### {title} — before -> {arm} ({n_b} runs)\n\n")
        with contextlib.redirect_stdout(buf):
            summarize_fix.main([kind, a, b])
        buf.write("\n")
    open(out, "w", encoding="utf-8").write(buf.getvalue())


if __name__ == "__main__":
    main(sys.argv[1:])
