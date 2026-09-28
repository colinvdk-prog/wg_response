"""Re-run the 2026-08-31 tri-model review's hindcast scripts, unmodified
except for their data paths, on each mirror tree (build_mirrors.py):
documented (the committed files), before (pre-fix corpora, today's engine)
and after (repaired corpora). A script's absolute paths into the main
checkout's scratch_review/ and tools/ are pointed at the mirror; a script
that locates the repo from its own file is copied into the mirror's
scratch_review/ so it resolves there. Engine imports still come from this
checkout (PYTHONPATH), and nothing is written outside the mirror.
Output: results/review_scripts/<state>/<script>.txt
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MIRROR = os.path.join(HERE, "results", "mirror")
OUT = os.path.join(HERE, "results", "review_scripts")
SCRIPTS = ["rvclaude_dispersion.py", "rvclaude_aug_deep.py",
           "rvclaude_final_table.py", "rvclaude_semantics.py",
           "rvclaude_rederive_aug.py", "rvclaude_july_era.py",
           "rvgrok_stats_audit.py", "rv2sci_a7_disp.py", "rv2sci_a11_stale.py"]
MAIN = r"C:\Users\colin\wg_response"


def main() -> int:
    for state in ("documented", "before", "after"):
        mroot = os.path.join(MIRROR, state)
        os.makedirs(os.path.join(OUT, state), exist_ok=True)
        for name in SCRIPTS:
            with open(os.path.join(ROOT, "scratch_review", name), "r", encoding="utf-8") as f:
                src = f.read()
            for sub in ("scratch_review", "tools"):
                src = src.replace(MAIN + "\\" + sub, mroot + "\\" + sub)
                src = src.replace(MAIN.replace("\\", "/") + "/" + sub, mroot.replace("\\", "/") + "/" + sub)
            leftover = re.findall(r"wg_response[\\/](?:scratch_review|tools)", src)
            if leftover:
                raise SystemExit(f"{name}: unsubstituted data path {leftover}")
            dest = os.path.join(mroot, "scratch_review", name)
            with open(dest, "w", encoding="utf-8", newline="\n") as f:
                f.write(src)
            env = dict(os.environ, PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8",
                       PYTHONHASHSEED="0")
            r = subprocess.run([sys.executable, dest], cwd=mroot, env=env,
                               capture_output=True, text=True)
            with open(os.path.join(OUT, state, name.replace(".py", ".txt")), "w",
                      encoding="utf-8", newline="\n") as f:
                f.write(r.stdout)
                if r.returncode:
                    f.write("\n[exit %d]\n%s" % (r.returncode, r.stderr[-3000:]))
            print(f"{state:10s} {name:28s} exit {r.returncode}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
