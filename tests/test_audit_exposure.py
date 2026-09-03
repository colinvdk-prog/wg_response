"""SAFETY SEMANTICS 10(c): the crew-overrun audit counts IMMINENT exposure in
EVERY crew mode (tri-model review 2026-08-31 finding A8; fix-program item 4).

The shipped audit exempted non-idle crews from the imminent branch, so 106
crew-ticks in the 64-scenario sweep — up to 5.6 h past eroded-P10, all in
egress mode — never entered the count, and the only guarding test pinned the
zero-exposure case. These tests pin the non-zero-exposure cases."""
from __future__ import annotations

import pytest

from wgresponse import simui

UNREACHED = float("inf")


def _session(**cfg):
    return simui.SimSession("boulder_co", 2, 1, seed="audit-test",
                            cfg_overrides=cfg)


def _crew(s):
    return next(iter(s._crew_sims.values()))


def _expose(s, nom=UNREACHED, p10=UNREACHED):
    """Every position on the tile reads (nominal, eroded-p10) = (nom, p10)."""
    s._crew_exposure = lambda pos: (nom, p10)


@pytest.mark.parametrize("mode", ["idle", "egress", "en_route", "deploying"])
def test_imminent_exposure_counts_in_every_mode(mode):
    s = _session()
    cs = _crew(s)
    cs._mode = mode
    buffer_s = s._cfg.egress_buffer_min * 60.0
    _expose(s, p10=s._now_s + 0.5 * buffer_s)      # inside the egress buffer
    s._run_audit()
    rows = s._crew_overruns
    assert len(rows) == 1
    assert rows[0]["reason"] == "imminent"
    assert rows[0]["mode"] == mode
    assert rows[0]["crew_id"] == cs.crew.crew_id


@pytest.mark.parametrize("mode", ["idle", "egress"])
def test_burned_exposure_counts_in_every_mode(mode):
    s = _session()
    cs = _crew(s)
    cs._mode = mode
    _expose(s, nom=s._now_s - 60.0, p10=s._now_s - 3600.0)
    s._run_audit()
    rows = s._crew_overruns
    assert len(rows) == 1 and rows[0]["reason"] == "burned"
    assert rows[0]["mode"] == mode


def test_clear_cell_produces_no_row_in_any_mode():
    s = _session()
    cs = _crew(s)
    buffer_s = s._cfg.egress_buffer_min * 60.0
    for mode in ("idle", "egress"):
        cs._mode = mode
        _expose(s, p10=s._now_s + 2.0 * buffer_s)   # outside the buffer
        s._run_audit()
        assert s._crew_overruns == []


def test_audit_is_independent_of_the_sweep():
    """The backstop fires with the crew already in egress (the sweep having
    'handled' it) — the exact case the exemption hid."""
    s = _session()
    cs = _crew(s)
    cs._mode = "egress"
    _expose(s, p10=s._now_s - 5.6 * 3600.0)     # the review's worst case
    s._run_audit()
    assert s._crew_overruns and s._crew_overruns[0]["reason"] == "imminent"
    assert s._crew_overruns[0]["eroded_p10_s"] == s._now_s - 5.6 * 3600.0
