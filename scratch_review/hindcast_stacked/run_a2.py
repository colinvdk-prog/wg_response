"""Re-run the tri-model review's null-model verification
(scratch_review/rv2sci_a2_null.py: "1.85x more area-efficient than a
physics-free isotropic buffer", "5.8x area budget") on the before and after
states. The script needs the August corpus's incidents and tiles next to
aug_replay.json, and imports the harness, so each state gets a sandbox
corpus dir (that state's incident files + tiles + its mirror aug_replay.json)
and runs with that state's checkout on sys.path. The documented value is the
committed scratch_review/rv2sci_a2.json. Output:
results/review_scripts/<state>/rv2sci_a2.json and .txt
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BEFORE_ROOT = os.path.join(os.path.dirname(ROOT), "hindcast-before")
MIRROR = os.path.join(HERE, "results", "mirror")
OUT = os.path.join(HERE, "results", "review_scripts")
MAIN = r"C:\Users\colin\wg_response"

for state, code_root in (("before", BEFORE_ROOT), ("after", ROOT)):
    box = os.path.join(HERE, "results", "a2_sandbox", state, "hindcast_aug_data")
    if os.path.exists(box):
        shutil.rmtree(box)
    src = os.path.join(code_root, "scratch_review", "hindcast_aug_data")
    shutil.copytree(os.path.join(src, "incidents"), os.path.join(box, "incidents"))
    shutil.copytree(os.path.join(src, "lfps"), os.path.join(box, "lfps"))
    shutil.copy(os.path.join(MIRROR, state, "scratch_review", "hindcast_aug_data", "aug_replay.json"), box)
    with open(os.path.join(ROOT, "scratch_review", "rv2sci_a2_null.py"), encoding="utf-8") as f:
        code = f.read()
    code = code.replace('sys.path.insert(0, r"' + MAIN + '")', 'sys.path.insert(0, r"' + code_root + '")')
    code = code.replace('r"' + MAIN + r'\scratch_review\hindcast_aug_data"', 'r"' + box + '"')
    code = code.replace('r"' + MAIN + r'\scratch_review\rv2sci_a2.json"',
                        'r"' + os.path.join(OUT, state, "rv2sci_a2.json") + '"')
    for lit in ('r"' + MAIN + '"', 'r"' + MAIN + r'\scratch_review'):
        assert lit not in code, f"unsubstituted path {lit}"
    script = os.path.join(HERE, "results", "a2_sandbox", state, "rv2sci_a2_null.py")
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write(code)
    r = subprocess.run([sys.executable, script], capture_output=True, text=True,
                       env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONHASHSEED="0"))
    os.makedirs(os.path.join(OUT, state), exist_ok=True)
    with open(os.path.join(OUT, state, "rv2sci_a2.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(r.stdout + (f"\n[exit {r.returncode}]\n{r.stderr[-3000:]}" if r.returncode else ""))
    print(state, "exit", r.returncode, (r.stdout.strip().splitlines() or [""])[-1][:160], flush=True)
