"""Docs-conformance tests (Cluster G).

Verify the documentation set stays truthful against contracts.py: every
ResponseConfig knob is documented in the README with its shipped default, the
UNVERIFIED gel parameters are flagged loudly, the safety formulas quoted in
ARCHITECTURE.md match contracts verbatim, and the open-questions register keeps
its owner tags. Pure stdlib + pytest; no network.
"""
from __future__ import annotations

import dataclasses
import pathlib
import re

import pytest

from wgresponse.contracts import AlertState, ResponseConfig

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
ARCH = ROOT / "docs" / "ARCHITECTURE.md"
INTEG = ROOT / "docs" / "INTEGRATION.md"
OPENQ = ROOT / "docs" / "OPEN_QUESTIONS.md"
DRAFT = ROOT / "docs" / "DESIGN_DRAFT.md"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Existence / structure
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [README, ARCH, INTEG, OPENQ, DRAFT])
def test_doc_exists_and_nonempty(path):
    assert path.is_file(), f"missing doc: {path}"
    assert len(_read(path)) > 200 or path == DRAFT, f"suspiciously short: {path}"


def test_design_draft_is_a_pointer():
    text = _read(DRAFT)
    assert "ARCHITECTURE.md" in text
    assert "superseded" in text.lower()
    # The old draft content must be gone.
    assert "Pending recon" not in text
    assert "Firm decisions" not in text


# ---------------------------------------------------------------------------
# README: config knobs table vs ResponseConfig
# ---------------------------------------------------------------------------

def _config_defaults() -> dict:
    return {f.name: f.default for f in dataclasses.fields(ResponseConfig)}


def test_readme_documents_every_config_field():
    text = _read(README)
    missing = [n for n in _config_defaults() if f"`{n}`" not in text]
    assert not missing, f"README config table missing knobs: {missing}"


def test_readme_config_defaults_match_contracts():
    """Each table row naming a knob must also carry its shipped default value."""
    lines = _read(README).splitlines()
    bad = []
    for name, default in _config_defaults().items():
        rows = [ln for ln in lines if f"`{name}`" in ln and ln.strip().startswith("|")]
        if not rows:
            bad.append(f"{name}: no table row")
            continue
        if not any(str(default) in ln for ln in rows):
            bad.append(f"{name}: default {default!r} not shown")
    assert not bad, "README defaults out of sync with ResponseConfig:\n" + "\n".join(bad)


def test_readme_flags_gel_params_unverified():
    text = _read(README)
    for knob in ("gel_window_h", "gel_full_strength_h", "gel_effectiveness"):
        row = next(
            (ln for ln in text.splitlines() if f"`{knob}`" in ln and ln.strip().startswith("|")),
            "",
        )
        assert row, f"no README row for {knob}"
        if knob != "gel_effectiveness":
            assert "UNVERIFIED" in row, f"{knob} row not flagged UNVERIFIED"
    # The loud warning block must exist and name the 6h-vs-72h conflict.
    assert "PENDING SUPPLIER DATA" in text
    assert "6 h" in text and "72 h" in text


def test_readme_licensing_posture():
    text = _read(README)
    for term in ("Watch Duty", "WFIGS", "FIRIS", "CAL FIRE", "FIRMS",
                 "Open-Meteo", "User-Agent", "ToS-banned", "non-commercial"):
        assert term in text, f"README licensing section missing: {term}"


# ---------------------------------------------------------------------------
# ARCHITECTURE: safety semantics quoted from contracts
# ---------------------------------------------------------------------------

def test_architecture_quotes_safety_formulas_verbatim():
    text = _read(ARCH)
    assert "eta_p10_s - egress_buffer_min*60" in text
    assert "latest_depart_s - deploy_min*60" in text


def test_architecture_covers_alert_states_and_hysteresis():
    text = _read(ARCH)
    for state in AlertState:
        assert state.name in text, f"ARCHITECTURE missing alert state {state.name}"
    for term in ("hysteresis", "clear_cycles", "clear_margin_h", "P50", "P10"):
        assert term in text


def test_architecture_covers_required_sections():
    text = _read(ARCH)
    for term in (
        "Rothermel", "Anderson", "16-neighbor", "Dijkstra", "ensemble",
        "TOPTW", "regret-2", "ILS", "grid_max_cells", "corridor_erosion_m",
        "route_margin_min", "Camp", "Tubbs", "East Troublesome",
    ):
        assert term in text, f"ARCHITECTURE missing: {term}"


def test_architecture_minute_budgets_match_config():
    """The 30/30/60 minute budgets and 500 m erosion radius appear as shipped."""
    cfg = ResponseConfig()
    text = _read(ARCH)
    flat = text.replace("minutes", "min").replace("minute", "min")
    assert f"{cfg.egress_buffer_min:.0f} min" in flat or f"{cfg.egress_buffer_min:.0f}-min" in flat
    assert f"{cfg.route_margin_min:.0f} min" in flat
    assert str(int(cfg.corridor_erosion_m)) in text  # 500
    assert "egress_buffer_min" in text and "deploy_min" in text and "route_margin_min" in text


# ---------------------------------------------------------------------------
# INTEGRATION: estate wiring facts
# ---------------------------------------------------------------------------

def test_integration_names_estate_seams():
    text = _read(INTEG)
    for term in (
        "wg-consumer-profiles-dev", "wg-technicians-dev", "graphgel_enrolled",
        "segments.py:261", "BackgroundJobsQueue", "is_mailable",
        "jwt/signing-key", "graphgel_dispatch", "propagation.py",
        "Watch Duty", "FIRMS", "lfps.usgs.gov",
    ):
        assert term in text, f"INTEGRATION missing: {term}"


def test_integration_recommends_separate_stack_first():
    text = _read(INTEG)
    assert "(b) now, (a) later" in text or "recommend" in text.lower()
    assert "blast-radius" in text.lower() or "blast radius" in text.lower()


# ---------------------------------------------------------------------------
# OPEN_QUESTIONS: coverage + owner tags
# ---------------------------------------------------------------------------

def test_open_questions_cover_required_topics():
    text = _read(OPENQ)
    for term in (
        "6 h vs 72 h", "Watch Duty", "Open-Meteo", "LFPS", "Valhalla",
        "N=20 / M=200", "quantile", "SMS", "retention", "consent",
        "Safe-zone", "circuity",
    ):
        assert term in text, f"OPEN_QUESTIONS missing topic: {term}"


def test_open_questions_every_item_has_owner_and_unblocker():
    text = _read(OPENQ)
    items = re.findall(r"^### ([A-Z]-\d+) — (.+)$", text, flags=re.M)
    assert len(items) >= 12, f"expected a full register, found {len(items)} items"
    for item_id, heading in items:
        assert re.search(r"\b(Colin|supplier|eng)\b", heading), (
            f"{item_id} heading lacks an owner tag: {heading!r}"
        )
    # Every item body must state what unblocks it.
    bodies = re.split(r"^### ", text, flags=re.M)[1:]
    for body in bodies:
        item_id = body.split(" ", 1)[0]
        assert "Unblocks:" in body, f"{item_id} has no 'Unblocks:' line"


def test_open_questions_ids_referenced_elsewhere_exist():
    """Any P-/L-/T-/O- id cited in the other docs must exist in the register."""
    register = set(re.findall(r"^### ([A-Z]-\d+) —", _read(OPENQ), flags=re.M))
    cited = set()
    for path in (README, ARCH, INTEG):
        cited |= set(re.findall(r"\b([PLTO]-\d+)\b", _read(path)))
    unknown = cited - register
    assert not unknown, f"docs cite unknown open-question ids: {sorted(unknown)}"
