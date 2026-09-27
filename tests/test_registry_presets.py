"""`--registry mock-presets` — the national demo roster (W6 thin viewer,
2026-09-26; docs/LIVE_WORLD_PLAN.md §3 step 1).

N demo homes on EVERY simulator preset, placed by the simulator's own WUI
placement (simui.place_clients with each preset's city-limits polygon and
place_near_road_m driveway rule, seed law f"{key}|{seed}"), served through
the SAME ClientRegistry seam (MockClientRegistry.protected_clients(bbox)).
Pinned here: determinism, the per-preset count and id law, bbox filtering,
cell-for-cell equality with a SimSession's own placement, the loud notes of
every fallback, the watch CLI wiring — and that the plain `mock` roster is
byte-identical to what it was before this registry existed.
"""
import hashlib
import json
from types import SimpleNamespace

import pytest

from wgresponse import cli, clients, geo, simui
from wgresponse.contracts import BBox

CONUS = BBox(west=-125.0, south=24.0, east=-66.0, north=50.0)


@pytest.fixture
def synth_roster_kwargs(tmp_path):
    """Fast, dependency-free seams: an EMPTY tile dir (every preset falls
    back to the simulator's deterministic synthetic tile) and no road
    graphs (the driveway rule falls back to the whole WUI band, loudly)."""
    return {"tile_dir": tmp_path / "no_tiles", "roads_module": None}


def _fingerprint(roster):
    rows = [(c.client_id, round(c.loc.lat, 7), round(c.loc.lon, 7),
             round(c.value_usd, 2), c.name) for c in roster]
    return hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# the roster
# ---------------------------------------------------------------------------

def test_roster_has_n_homes_on_every_preset_with_unique_ids(synth_roster_kwargs):
    notes = []
    roster = clients.preset_roster(5, seed="t", notes=notes,
                                   **synth_roster_kwargs)
    assert len(roster) == 5 * len(simui.LOCATIONS)
    ids = [c.client_id for c in roster]
    assert len(set(ids)) == len(ids)
    for key in simui.LOCATIONS:
        mine = [c for c in roster if c.client_id.startswith(f"{key}-c")]
        assert [c.client_id for c in mine] == [f"{key}-c{k:03d}"
                                               for k in range(1, 6)]
        for c in mine:
            assert c.graphgel_enrolled is True
            assert c.name.startswith(simui.LOCATIONS[key].name)
            assert "demo home" in c.name
            # demo data: nowhere to deliver an alert, ever
            assert c.contact_email == "" and c.contact_phone == ""
            # a home sits within its preset's tile (synthetic 12 km here)
            assert geo.haversine_m(c.loc, simui.LOCATIONS[key].center) < 12_000
    # sorted by preset key, then placement order
    assert ids == sorted(ids, key=lambda s: (s.rsplit("-c", 1)[0], s))


def test_roster_is_deterministic_and_seeded(synth_roster_kwargs):
    a = clients.preset_roster(4, seed="same", **synth_roster_kwargs)
    b = clients.preset_roster(4, seed="same", **synth_roster_kwargs)
    c = clients.preset_roster(4, seed="other", **synth_roster_kwargs)
    assert a == b
    assert _fingerprint(a) == _fingerprint(b)
    assert [x.loc for x in a] != [x.loc for x in c]


def test_roster_fallbacks_are_loud(synth_roster_kwargs):
    notes = []
    clients.preset_roster(2, seed="t", notes=notes, **synth_roster_kwargs)
    text = "\n".join(notes)
    for key in simui.LOCATIONS:
        assert f"preset roster: {key} tile not cached" in text, key
    # every preset with the driveway rule says it fell back to the band
    ruled = [k for k, loc in simui.LOCATIONS.items() if loc.place_near_road_m > 0]
    assert ruled, "the corridor presets carry the 300 m driveway rule"
    for key in ruled:
        assert f"preset roster: {key} has the 300 m driveway rule but no road graph" in text


def test_roster_rejects_nonpositive_n(synth_roster_kwargs):
    with pytest.raises(ValueError):
        clients.preset_roster(0, **synth_roster_kwargs)


@pytest.mark.parametrize("key", ["malibu_ca", "rancho_santa_fe_ca", "vail_co"])
def test_roster_is_the_simulators_own_placement(key):
    """The demo homes of preset K sit on EXACTLY the cells a simulator
    session of (K, N, seed) places — on the committed real tile, with the
    city-limits polygon (RSF) and the 300 m driveway rule (Vail) applied by
    the session's own road graph."""
    if not simui.tile_file(key).exists():
        pytest.skip(f"real tile for {key} not cached")
    session = simui.SimSession(key, 12, 1, seed="w6-parity")
    roster = clients.preset_roster(12, seed="w6-parity", keys=[key])
    assert [c.loc for c in roster] == [c.loc for c in session._clients]
    assert [c.value_usd for c in roster] == [c.value_usd for c in session._clients]


# ---------------------------------------------------------------------------
# the registry seam
# ---------------------------------------------------------------------------

def test_registry_serves_the_roster_through_the_bbox_seam(synth_roster_kwargs):
    reg = clients.preset_client_registry(3, seed="t", **synth_roster_kwargs)
    assert isinstance(reg, clients.MockClientRegistry)
    everyone = reg.protected_clients()
    assert len(everyone) == 3 * len(simui.LOCATIONS)
    # every preset is in the contiguous US
    assert len(reg.protected_clients(CONUS)) == len(everyone)
    # a bbox around one preset returns exactly that preset's homes (the
    # whole-valley Eagle -> Vail tile overlaps the Vail tile, so Vail's
    # box is judged by containment, not by id prefix)
    vail = geo.bbox_around([simui.LOCATIONS["malibu_ca"].center], 15_000.0)
    got = reg.protected_clients(vail)
    assert got and all(vail.contains(c.loc) for c in got)
    assert {c.client_id.rsplit("-c", 1)[0] for c in got} == {"malibu_ca"}
    # an empty box is empty
    assert reg.protected_clients(BBox(west=-80.0, south=40.0,
                                      east=-79.9, north=40.1)) == []


# ---------------------------------------------------------------------------
# the watch CLI
# ---------------------------------------------------------------------------

def _args(**kw):
    base = {"registry": "mock", "preset_homes": 40}
    base.update(kw)
    return SimpleNamespace(**base)


#: The plain `mock` roster for the SoCal shadow AOI, fingerprinted from the
#: pre-W6 code path (commit 09f94b5): 12 homes around the AOI centre. The
#: mock-presets registry must never change a byte of it.
SOCAL = BBox(west=-119.3, south=33.6, east=-117.5, north=34.6)
MOCK_SOCAL_FINGERPRINT = (
    "528bc0352d57ba924b8e02cddb5ba7851ecbbafa8afef2a3baeb33ac4b4142e4")


def test_mock_registry_is_byte_identical():
    reg, desc = cli._watch_registry(_args(registry="mock"), SOCAL)
    roster = reg.protected_clients()
    assert len(roster) == 12
    assert desc.startswith("MockClientRegistry (12 synthetic clients within ")
    assert [c.client_id for c in roster] == [f"mock-client-{i:03d}"
                                             for i in range(12)]
    assert _fingerprint(roster) == MOCK_SOCAL_FINGERPRINT
    # the notes seam is inert for the plain mock
    notes = []
    reg2, desc2 = cli._watch_registry(_args(registry="mock"), SOCAL, notes=notes)
    assert notes == [] and desc2 == desc
    assert _fingerprint(reg2.protected_clients()) == MOCK_SOCAL_FINGERPRINT


def test_watch_registry_mock_presets_branch(monkeypatch, tmp_path):
    from wgresponse import roads
    monkeypatch.setattr(simui, "DEFAULT_TILE_DIR", tmp_path / "no_tiles")
    monkeypatch.setattr(roads, "DEFAULT_ROAD_DIR", tmp_path / "no_roads")
    notes = []
    reg, desc = cli._watch_registry(_args(registry="mock-presets",
                                          preset_homes=3), CONUS, notes=notes)
    n = 3 * len(simui.LOCATIONS)
    assert len(reg.protected_clients()) == n
    assert f"{n} homes" in desc and f"{n} inside the AOI" in desc
    assert any("tile not cached" in s for s in notes)       # never silent
    # a regional AOI says how many demo homes fall outside it
    notes2 = []
    cli._watch_registry(_args(registry="mock-presets", preset_homes=2),
                        SOCAL, notes=notes2)
    assert any("lie OUTSIDE the AOI" in s for s in notes2)
    with pytest.raises(ValueError):
        cli._watch_registry(_args(registry="mock-presets", preset_homes=0),
                            CONUS)


def test_watch_help_lists_the_presets_registry(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["watch", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "mock-presets" in out and "--preset-homes" in out
    args = cli._build_parser().parse_args(
        ["watch", "--aoi", "data/aoi_conus.json", "--registry", "mock-presets"])
    assert args.registry == "mock-presets" and args.preset_homes == 40


def test_watch_rejects_nonpositive_preset_homes(tmp_path, capsys):
    rc = cli.main(["watch", "--aoi", "-121,32,-114,36", "--registry",
                   "mock-presets", "--preset-homes", "0",
                   "--synthetic-terrain", "--state-dir", str(tmp_path / "st"),
                   "--max-cycles", "1"])
    assert rc == 2
    assert "--preset-homes must be >= 1" in capsys.readouterr().err
