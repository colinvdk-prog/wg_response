import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch_review import rvclaude_audit_carveout as M
M.sweep()
