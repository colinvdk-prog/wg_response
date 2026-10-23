"""ONE FIRE IS ONE INCIDENT ACROSS FEEDS (contracts SAFETY SEMANTICS 24;
cfg.fuse_colocated_incidents, default OFF; watch --fuse-colocated).

The live watcher fused FIRIS row 1504 "CA-LAC-BIKE-N50X" and WFIGS IRWIN
aa44e04e "Bike Fire" — one 171.5-acre fire — as two incidents with two
domains. Every payload below is a REAL live record (tests/fixtures/
colocated_2026_10_15.json, parsed by the real adapters through
tests/colocated_stack.py) or a one-field variation of one; the 2026-10-14
WFIGS perimeter of the Bike Fire is reconstructed (see the fixture builder).

Byte-identity: with the knob OFF the fused lists and a three-cycle world
run reproduce goldens computed on commit bf2a868, BEFORE the rule existed
(scratch_review/colocated/golden.py).
"""
from __future__ import annotations

import copy
import dataclasses
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import colocated_stack as cst  # noqa: E402
import seeded_stack as ss  # noqa: E402
from wgresponse import feeds, geo  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    FireIncident, IncidentSource, LatLon, ResponseConfig, validate_config,
)
from wgresponse.sim import jsonable  # noqa: E402

#: KNOB-OFF GOLDENS — computed on commit bf2a868 (before SAFETY SEMANTICS 24)
#: by scratch_review/colocated/golden.py.
_GOLDEN_FIXTURE_FUSED = \
    "0a9a445b7092a5381a60b65098ebacf040b567fb27041225f1bfdccda39449f0"
_GOLDEN_BIKE_1014_FUSED = \
    "ff4c5879f6f0ed1a7ca201a4098f3e655188fc8705db4cde8101fe9b91b20b71"
_GOLDEN_WORLD_CYCLES = [
    "ae686311ab7d0fcea1c981d9a34b7ffeccd554e3e1f3718982ae1989ca5aeec8",
    "a43daa8e0ce89b7ba40150873c8861a934b82577e1d2e8a441d38aa14a585c70",
    "7410b0924a23f04ca657ea2e0a19e6fc5c94f365aae29f97294b23ee071492e7",
]
_GOLDEN_WORLD_API = \
    "f053399bc518f69827a36ae194f2c29bfbffa5f45f149cbbe99c1b60d0b4ec80"
_GOLDEN_WORLD_STATE = \
    "f8b2f5e79b5c518d2bcffc810766e752347a4ee5fb99919a8915030c775ab5cf"

ON = ResponseConfig(fuse_colocated_incidents=True)
H = 3600.0
MS = 1000.0


@pytest.fixture(scope="module")
def fx():
    return cst.load_fixture()


def _on(batches, now_s, cfg=ON):
    merges: list = []
    out = feeds.fuse_incidents(batches, cfg=cfg, now_s=now_s, merges=merges)
    return out, merges


def _by_id(incidents):
    return {i.incident_id: i for i in incidents}


def _firis(fx, oid, **attrs):
    """A deep copy of FIRIS row `oid` with attributes overridden."""
    (row,) = copy.deepcopy(cst.firis_rows(fx, oid))
    row["attributes"].update(attrs)
    return row


def _point(fx, irwin, **attrs):
    (pt,) = copy.deepcopy(cst.wfigs_points(fx, irwin))
    pt["attributes"].update(attrs)
    return pt


def _bike_pair(fx, firis_row=None, point=None):
    return cst.fixture_batches(
        fx, points=[point or _point(fx, cst.BIKE_IRWIN)], perims=[],
        firis=[firis_row or _firis(fx, 1504)], calfire=[])


# ---------------------------------------------------------------------------
# byte-identity: knob OFF reproduces the pre-change engine
# ---------------------------------------------------------------------------

def test_knob_off_fusion_reproduces_the_pre_change_digests(fx):
    batches = cst.fixture_batches(fx)
    assert cst.fused_digest(feeds.fuse_incidents(batches)) \
        == _GOLDEN_FIXTURE_FUSED
    # the knob carried but OFF, with a clock and a merges list: identical
    merges: list = []
    off = feeds.fuse_incidents(batches, cfg=ResponseConfig(),
                               now_s=cst.now_s(fx), merges=merges)
    assert cst.fused_digest(off) == _GOLDEN_FIXTURE_FUSED
    assert merges == []
    assert cst.fused_digest(feeds.fuse_incidents(
        cst.bike_2026_10_14_batches(fx))) == _GOLDEN_BIKE_1014_FUSED
    assert cst.fused_digest(feeds.fuse_incidents(
        cst.bike_2026_10_14_batches(fx), cfg=ResponseConfig())) \
        == _GOLDEN_BIKE_1014_FUSED


def test_knob_off_world_reproduces_the_pre_change_engine(tmp_path):
    cycles, api, state = cst.baseline_world_run(tmp_path / "a")
    assert cycles == _GOLDEN_WORLD_CYCLES      # every CycleResult decision
    assert api == _GOLDEN_WORLD_API            # the /api/state payload
    assert state == _GOLDEN_WORLD_STATE        # the persisted state bytes
    explicit = cst.baseline_world_run(
        tmp_path / "b", cfg=ss.small_cfg(fuse_colocated_incidents=False))
    assert explicit == (_GOLDEN_WORLD_CYCLES, _GOLDEN_WORLD_API,
                        _GOLDEN_WORLD_STATE)


def test_knob_off_monitor_makes_the_legacy_one_argument_call(tmp_path,
                                                             monkeypatch):
    calls = []
    real = feeds.fuse_incidents

    def spy(*args, **kwargs):
        calls.append((len(args), dict(kwargs)))
        return real(*args, **kwargs)
    monkeypatch.setattr(feeds, "fuse_incidents", spy)
    clock = ss.SettableClock(ss.T0)
    daemon, *_ = ss.make_stack(tmp_path / "s", incidents=cst.world_incidents(),
                               clock=clock)
    clock.t = ss.T0 + 60.0
    daemon.run_cycle()
    assert calls == [(1, {})]


def test_default_is_off_and_the_seam_is_additive():
    cfg = ResponseConfig()
    assert cfg.fuse_colocated_incidents is False
    assert (cfg.fuse_colocated_max_m, cfg.fuse_colocated_size_tol_frac,
            cfg.fuse_colocated_min_acres, cfg.fuse_colocated_time_tol_h) \
        == (2000.0, 0.10, 10.0, 24.0)
    sig = inspect.signature(feeds.fuse_incidents)
    assert list(sig.parameters) == ["batches", "cfg", "now_s", "merges"]
    assert all(sig.parameters[k].default is None
               for k in ("cfg", "now_s", "merges"))


# ---------------------------------------------------------------------------
# the reported pair
# ---------------------------------------------------------------------------

def test_the_reported_pair_as_served_2026_10_14_merges(fx):
    """WFIGS 'Bike Fire' (perimeter record, 171.5 acres) + FIRIS
    'CA-LAC-BIKE-N50X' (171.497 acres): two incidents legacy, ONE with the
    knob — both sources listed, the IRWIN id kept, both rules agree."""
    batches = cst.bike_2026_10_14_batches(fx)
    legacy = feeds.fuse_incidents(batches)
    assert sorted(i.incident_id for i in legacy) == [cst.BIKE_IRWIN,
                                                     "firis:1504"]
    assert all(len(i.source_ids) == 1 for i in legacy)
    out, merges = _on(batches, cst.now_s(fx))
    (inc,) = out
    assert inc.incident_id == cst.BIKE_IRWIN
    assert sorted(inc.source_ids) == ["firis", "wfigs"]
    assert inc.acreage == 171.5 and inc.perimeter
    assert merges == [{"incident_id": cst.BIKE_IRWIN, "name": "Bike Fire",
                       "merged": [cst.BIKE_IRWIN, "firis:1504"],
                       "names": ["Bike Fire", "CA-LAC-BIKE-N50X"],
                       "evidence": ["name", "size"]}]


def test_the_reported_pair_as_served_2026_10_15_merges_on_the_name(fx):
    """A day later WFIGS served the fire as a point 'BIKE' with NO size: the
    size rule cannot speak, the designator's name does."""
    batches = cst.bike_2026_10_15_batches(fx)
    (w,), (f,), () = batches
    assert w.acreage is None and w.perimeter is None
    assert f.name == "CA-LAC-BIKE-N50X" and f.irwin_id is None
    assert len(feeds.fuse_incidents(batches)) == 2
    out, merges = _on(batches, cst.now_s(fx))
    (inc,) = out
    assert inc.incident_id == cst.BIKE_IRWIN
    assert sorted(inc.source_ids) == ["firis", "wfigs"]
    # the legacy merge: the newest record (WFIGS) names it, the FIRIS
    # polygon is the perimeter at its OWN time
    assert inc.name == "BIKE" and inc.perimeter == f.perimeter
    assert inc.meta["perimeter_at_s"] == f.updated_at_s
    assert merges[0]["evidence"] == ["name"]


def test_size_alone_merges_two_feeds_when_no_name_matches(fx):
    """The 2026-10-14 shape with FIRIS's row carrying an unrelated
    incident_name: no name evidence, the size + place + time still say one
    fire (714 m, 171.5 vs 171.497 acres)."""
    fi = _firis(fx, 1504, incident_name="LAC-371999")
    perim = fx["reconstructed"]["wfigs_bike_perimeter_2026_10_14"]
    batches = cst.fixture_batches(fx, points=[], perims=[perim], firis=[fi],
                                  calfire=[])
    assert len(feeds.fuse_incidents(batches)) == 2
    out, merges = _on(batches, cst.now_s(fx))
    assert len(out) == 1 and merges[0]["evidence"] == ["size"]


# ---------------------------------------------------------------------------
# the recorded 2026-10-15 pull: exactly the four duplicate fires
# ---------------------------------------------------------------------------

def test_the_live_pull_merges_exactly_the_four_duplicate_fires(fx):
    batches = cst.fixture_batches(fx)
    legacy = feeds.fuse_incidents(batches)
    out, merges = _on(batches, cst.now_s(fx))
    assert (len(legacy), len(out)) == (17, 13)
    got = {m["incident_id"]: (sorted(m["merged"]), m["evidence"])
           for m in merges}
    assert got == {
        cst.BIKE_IRWIN: (sorted([cst.BIKE_IRWIN, "firis:1504"]), ["name"]),
        cst.MARSHALL_CF: (sorted([cst.MARSHALL_CF, "firis:1506"]),
                          ["name", "size"]),
        "firis:1507": (["firis:1505", "firis:1507"], ["name"]),
        cst.CYRUS_IRWIN: (sorted([cst.CYRUS_IRWIN, "firis:1503"]), ["name"]),
    }
    by = _by_id(out)
    assert sorted(by[cst.MARSHALL_CF].source_ids) == ["calfire", "firis"]
    assert sorted(by["firis:1507"].source_ids) == ["calfire", "firis"]
    assert sorted(by[cst.CYRUS_IRWIN].source_ids) == ["calfire", "firis",
                                                      "wfigs"]
    # the distractors are NOT one fire and stay apart: WFIGS twins 6 m
    # apart, numbered fires 174 m apart, a start beside a 100-day zombie,
    # the unsized 'Marshall 1', a 99-day zombie near Marshall
    for iid in ("6bd82f3a-c0bf-4d6c-81bf-00d3d9cb17ff",      # Marshall 1
                "d4248445-2330-4cd9-8488-240fd94e1e27",      # TOWN
                "50ff5bd9-1b8f-42ac-a550-2cd4256ac5ad",      # LAC-371632
                "29113f7b-dd19-44a9-9476-9a499de0aa25",      # Schultz Wagon 1
                "8b0f976b-a4d4-45c5-bcb3-f8705953df2a",      # Schultz Wagon 2
                "5c5fa582-795b-48aa-87e1-14a44c4c82fb",      # Nelson Rd
                "d027e265-42bb-420f-a43e-cdaa15ea036d",      # Sundown
                "firis:188", "firis:196"):                   # zombies
        assert len(by[iid].source_ids) == 1, iid


def test_a_union_is_the_legacy_merge_and_nothing_else_moves(fx):
    batches = cst.fixture_batches(fx)
    flat = [i for b in batches for i in b]
    legacy = feeds.fuse_incidents(batches)
    out, merges = _on(batches, cst.now_s(fx))
    by = _by_id(out)
    merged_ids = {iid for m in merges for iid in m["merged"]}
    for inc in legacy:                       # untouched groups: identical
        if inc.incident_id not in merged_ids:
            assert jsonable(by[inc.incident_id]) == jsonable(inc)
    # Surgone: the four raw records, merged exactly as one legacy group
    members = [i for i in flat if i.incident_id in (
        "firis:1505", "firis:1507", "firis:1508", cst.SURGONE_CF)]
    expect = feeds._merge_group(members)
    expect.incident_id = "firis:1507"
    assert jsonable(by["firis:1507"]) == jsonable(expect)


def test_a_designator_flight_never_renames_the_fire_it_joins(fx):
    """The union keeps its ANCHOR's id: Surgone stays firis:1507 (not the
    older flight's smaller firis:1505), Marshall stays CAL FIRE's — with the
    designator flight joined and after it is gone alike."""
    now = cst.now_s(fx)
    with_flights = _by_id(_on(cst.fixture_batches(fx), now)[0])
    rows = [f for f in fx["firis"]
            if f["attributes"]["OBJECTID"] not in (1505, 1506)]
    without = _by_id(_on(cst.fixture_batches(fx, firis=rows), now)[0])
    for iid in ("firis:1507", cst.MARSHALL_CF):
        assert iid in with_flights and iid in without
    assert "firis:1505" not in with_flights


def test_union_does_not_depend_on_feed_or_record_order(fx):
    now = cst.now_s(fx)
    batches = cst.fixture_batches(fx)
    want = cst.fused_digest(_on(batches, now)[0])
    for order in ([2, 1, 0], [1, 2, 0], [0, 2, 1]):
        shuffled = [list(reversed(batches[i])) for i in order]
        assert cst.fused_digest(_on(shuffled, now)[0]) == want


# ---------------------------------------------------------------------------
# the guards
# ---------------------------------------------------------------------------

def test_a_polygon_from_before_the_fire_was_discovered_is_another_fire(fx):
    """FIRIS keeps rows for months: the same-named designator row whose
    polygon predates WFIGS's discovery by more than the tolerance never
    joins (25 h before: apart; 23 h before: one fire)."""
    disc_ms = _point(fx, cst.BIKE_IRWIN)["attributes"]["FireDiscoveryDateTime"]
    now = cst.now_s(fx)
    early = _firis(fx, 1504, poly_DateCurrent=disc_ms - 25 * H * MS)
    assert len(_on(_bike_pair(fx, firis_row=early), now)[0]) == 2
    near = _firis(fx, 1504, poly_DateCurrent=disc_ms - 23 * H * MS)
    assert len(_on(_bike_pair(fx, firis_row=near), now)[0]) == 1
    # the tolerance is the knob
    strict = dataclasses.replace(ON, fuse_colocated_time_tol_h=0.0)
    assert len(_on(_bike_pair(fx, firis_row=near), now, strict)[0]) == 2


def test_a_group_the_staleness_gate_drops_never_joins(fx):
    """SEMANTICS 9 + 24c: past incident_stale_h on the cycle clock the FIRIS
    row is excluded from the cycle — the pass must not bring it back."""
    batches = cst.bike_2026_10_15_batches(fx)
    f = batches[1][0]
    stale_s = ON.incident_stale_h * H
    assert len(_on(batches, f.updated_at_s + stale_s)[0]) == 1   # kept: joins
    assert len(_on(batches, f.updated_at_s + stale_s + 60.0)[0]) == 2
    assert len(_on(batches, None)[0]) == 1       # no clock: nothing is stale


def test_one_feeds_own_two_records_never_size_join(fx):
    """WFIGS 'Schultz Wagon 1' / '2' (174 m apart) given the same real-looking
    size: one feed lists two fires — no co-location evidence joins them."""
    a = _point(fx, "29113F7B-DD19-44A9-9476-9A499DE0AA25", IncidentSize=50.0)
    b = _point(fx, "8B0F976B-A4D4-45C5-BCB3-F8705953DF2A", IncidentSize=50.5)
    batches = cst.fixture_batches(fx, points=[a, b], perims=[], firis=[],
                                  calfire=[])
    assert len(_on(batches, cst.now_s(fx))[0]) == 2


def _town_calfire(fx, acres):
    """A CAL FIRE record at WFIGS 'TOWN''s point (CAL FIRE's real record
    shape), another name, `acres` both sides."""
    town = _point(fx, "D4248445-2330-4CD9-8488-240FD94E1E27",
                  IncidentSize=acres)
    (rec,) = copy.deepcopy(cst.calfire_records(fx, cst.MARSHALL_CF[8:]))
    rec.update(Name="Town Ridge Fire", UniqueId="00000000-town-ridge",
               Latitude=town["geometry"]["y"], Longitude=town["geometry"]["x"],
               AcresBurned=acres, Started="2026-10-14T23:00:00Z",
               Updated="2026-10-15T00:00:00Z")
    return cst.fixture_batches(fx, points=[town], perims=[], firis=[],
                               calfire=[rec])


def test_placeholder_sizes_identify_nothing(fx):
    now = cst.now_s(fx)
    assert len(_on(_town_calfire(fx, 0.1), now)[0]) == 2
    floorless = dataclasses.replace(ON, fuse_colocated_min_acres=0.0)
    assert len(_on(_town_calfire(fx, 0.1), now, floorless)[0]) == 1
    assert len(_on(_town_calfire(fx, 12.0), now)[0]) == 1


def test_size_tolerance_boundary(fx):
    perim = fx["reconstructed"]["wfigs_bike_perimeter_2026_10_14"]
    now = cst.now_s(fx)

    def n(acres):
        fi = _firis(fx, 1504, incident_name="LAC-371999", area_acres=acres)
        return len(_on(cst.fixture_batches(fx, points=[], perims=[perim],
                                           firis=[fi], calfire=[]), now)[0])
    assert n(171.5 * 0.905) == 1             # 9.5 % of the larger: same size
    assert n(171.5 * 0.895) == 2             # 10.5 %: not


def _moved(inc, center, bearing, dist_m):
    """`inc` translated so its origin lies dist_m from `center`."""
    tgt = geo.destination(center, bearing, dist_m)
    return cst.rehome(inc, tgt.lat - inc.origin.lat, tgt.lon - inc.origin.lon,
                      0.0)


def test_co_location_distance_boundary(fx):
    fi = _firis(fx, 1504, incident_name="LAC-371999")
    perim = fx["reconstructed"]["wfigs_bike_perimeter_2026_10_14"]
    (w,), (f,), () = cst.fixture_batches(fx, points=[], perims=[perim],
                                         firis=[fi], calfire=[])
    now = cst.now_s(fx)
    for d, n in ((1980.0, 1), (2020.0, 2)):
        g = _moved(f, w.origin, 30.0, d)
        assert abs(feeds._haversine_m(g.origin, w.origin) - d) < 5.0
        assert len(_on([[w], [g], []], now)[0]) == n, d


def test_designator_name_join_reaches_the_name_join_radius(fx):
    (w,), (f,), () = cst.bike_2026_10_15_batches(fx)
    now = cst.now_s(fx)
    for d, n in ((4900.0, 1), (5100.0, 2)):
        g = _moved(f, w.origin, 200.0, d)
        assert len(_on([[w], [g], []], now)[0]) == n, d


def test_inactive_and_seeded_records_never_join(fx):
    now = cst.now_s(fx)
    (rec,) = copy.deepcopy(cst.calfire_records(fx, cst.MARSHALL_CF[8:]))
    rec["IsActive"] = False
    batches = cst.fixture_batches(fx, points=[], perims=[],
                                  firis=cst.firis_rows(fx, 1506),
                                  calfire=[rec])
    assert len(_on(batches, now)[0]) == 2
    (w,), (f,), () = cst.bike_2026_10_15_batches(fx)
    seeded = FireIncident(incident_id="seeded:bike-drill", name="BIKE",
                          source=IncidentSource.SEEDED, origin=w.origin,
                          updated_at_s=now, discovered_at_s=now - 90 * H,
                          acreage=171.5)
    out, merges = _on([[seeded], [f], []], now)
    assert len(out) == 2 and merges == []
    out, merges = _on([[w, seeded], [f], []], now)
    assert sorted(i.incident_id for i in out) == [cst.BIKE_IRWIN,
                                                  "seeded:bike-drill"]


@pytest.mark.parametrize("name,want", [
    ("CA-LAC-BIKE-N50X", "BIKE"),
    ("CA-HIA-MARSHALL-N57B", "MARSHALL"),
    ("CA-SQF-CYRUS-N5852K", "CYRUS"),
    ("CA-YNP-DOME-N874EB", "DOME"),
    ("NV-TMFX-BULL-N57B", "BULL"),
    ("CA-FS-NOD-S1-N512ME", "NOD-S1"),
    ("CA-BDU-CinderComplex-5-4-N42Z", "CinderComplex-5-4"),
    ("CA-KRN-WYLY", "WYLY"),                     # no aircraft
    ("CA-VNC-SANDY-42Z", "SANDY-42Z"),           # no N: not an aircraft
    ("CYRUS", None),                             # an incident_name
    ("2026-CABEU-004557", None),                 # a WFIGS unique fire id
    ("SANTA-ROSA", None),                        # a CAL FIRE intel mission
    ("", None),
])
def test_firis_designator_names(name, want):
    inc = FireIncident(incident_id="firis:1", name=name,
                       source=IncidentSource.FIRIS, origin=LatLon(34.0, -118.0),
                       updated_at_s=1.0)
    assert feeds._firis_designator_name(inc) == want
    other = dataclasses.replace(inc, source=IncidentSource.WFIGS)
    assert feeds._firis_designator_name(other) is None   # FIRIS rows only


# ---------------------------------------------------------------------------
# the watcher: one domain, one alert stream, a note
# ---------------------------------------------------------------------------

def test_the_watcher_solves_the_reported_pair_once(tmp_path):
    clock = ss.SettableClock(ss.T0)
    daemon, *_ = ss.make_stack(
        tmp_path / "s", cfg=ss.small_cfg(fuse_colocated_incidents=True),
        incidents=cst.world_incidents(), clock=clock)
    clock.t = ss.T0 + 60.0
    res = daemon.run_cycle()
    (inc,) = res.incidents
    assert inc.incident_id == cst.BIKE_IRWIN
    assert sorted(inc.source_ids) == ["firis", "wfigs"]
    assert list(res.ensembles) == [cst.BIKE_IRWIN]          # ONE domain
    assert {a.incident_id for a in res.assessments} == {cst.BIKE_IRWIN}
    (note,) = [n for n in res.notes if n.startswith("co-located fusion")]
    assert "SAFETY SEMANTICS 24" in note and "firis:1504" in note
    summary = daemon.world_summary()
    (row,) = [i for i in summary["incidents"]
              if i["incident_id"] == cst.BIKE_IRWIN]
    assert row["sources"] == ["firis", "wfigs"]
    # legacy: the same world is two incidents, two domains
    legacy_clock = ss.SettableClock(ss.T0 + 60.0)
    legacy, *_ = ss.make_stack(tmp_path / "l", incidents=cst.world_incidents(),
                               clock=legacy_clock)
    assert len(legacy.run_cycle().ensembles) == 2


# ---------------------------------------------------------------------------
# config + watch flag
# ---------------------------------------------------------------------------

def test_validate_config_bounds_the_knobs():
    assert validate_config(ResponseConfig()) == []
    assert validate_config(ON) == []
    bad = {
        "fuse_colocated_max_m": (0.0, 5001.0, float("inf")),
        "fuse_colocated_size_tol_frac": (-0.01, 0.51),
        "fuse_colocated_min_acres": (-1.0, float("nan")),
        "fuse_colocated_time_tol_h": (-1.0, 169.0, float("nan")),
    }
    for knob, values in bad.items():
        for v in values:
            probs = validate_config(dataclasses.replace(ON, **{knob: v}))
            assert any(knob in p for p in probs), (knob, v)
    probs = validate_config(dataclasses.replace(ON, fuse_colocated_incidents=1))
    assert any("fuse_colocated_incidents must be a bool" in p for p in probs)


def test_watch_flag_arms_the_knob_and_says_so():
    from wgresponse.cli import (_build_parser, _fuse_colocated_cfg,
                                _fuse_colocated_notes)
    from tests.test_fix_program_2 import PRODUCTION_WATCH_ARGS
    off = _build_parser().parse_args(PRODUCTION_WATCH_ARGS)
    assert off.fuse_colocated is False
    assert _fuse_colocated_cfg(ResponseConfig(), off) == ResponseConfig()
    assert _fuse_colocated_notes(ResponseConfig()) == []
    on = _build_parser().parse_args(list(PRODUCTION_WATCH_ARGS)
                                    + ["--fuse-colocated"])
    cfg = _fuse_colocated_cfg(ResponseConfig(), on)
    assert cfg == ON
    (note,) = _fuse_colocated_notes(cfg)
    assert "SAFETY SEMANTICS 24" in note and "2000 m" in note
