"""pytest plugin: mutate ONE ResponseConfig default before collection, to test
the repo's byte-identity pin discipline (handoff §7.2). Usage:
  RVMUTATE=knob=value py -3 -m pytest tests/ -q -p rvclaude_mutate
Touches nothing on disk; patches the dataclass' generated __init__ defaults."""
import os, dataclasses

def pytest_configure(config):
    spec = os.environ.get("RVMUTATE")
    if not spec: return
    name, _, raw = spec.partition("=")
    from wgresponse.contracts import ResponseConfig
    flds = [f for f in dataclasses.fields(ResponseConfig)]
    tgt = {f.name: f for f in flds}[name]
    cur = tgt.default
    val = (raw.lower() in ("1","true","yes")) if isinstance(cur, bool) else (
        int(raw) if isinstance(cur, int) and not isinstance(cur, bool) else float(raw))
    # rebuild __init__ defaults tuple (fields with defaults, in order)
    withdef = [f for f in flds if f.default is not dataclasses.MISSING]
    idx = [f.name for f in withdef].index(name)
    d = list(ResponseConfig.__init__.__defaults__)
    assert len(d) == len(withdef), (len(d), len(withdef))
    old = d[idx]; d[idx] = val
    ResponseConfig.__init__.__defaults__ = tuple(d)
    setattr(ResponseConfig, name, val)
    tgt.default = val
    print(f"\n[RVMUTATE] ResponseConfig.{name}: {old!r} -> {val!r}\n")
