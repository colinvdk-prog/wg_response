"""The hindcast harness's SNAPSHOT RULE (tools/hindcast_lib.py; fix of
2026-09-27, record docs/calibration/stacked_rings_fix_2026-09-27.md).

Bug pinned here: `_fetch_incident` unioned every WFIGS_Daily_Perimeters_Public
row sharing a capture time into one snapshot. Those rows are versions of one
polygon (re-copies, in-place edits, re-uploads), so their rings stack and
the even-odd rasterizers cancel them: the Madre Fire's 2025-07-02 15:51
capture (8,396 ac) rasterized to 0 cells and its 2025-07-04 23:37 capture
(4 versions, 70,800 -> 79,519 ac) to ~9,200 ac of slivers.

Pinned on the RECORDED Madre payloads (tests/fixtures/hindcast_madre_daily_
2026_09_27.json, verbatim WFIGS responses): the old union's failure, the
first-publication rule, gating on the first publication's acreage, the
fetch writing schema /2 with `dropped_oids`, `repair` rebuilding a pre-fix
file at its cached capture times, and `replay` refusing an unrepaired file.
Genuinely separate same-time pieces (disjoint rows) are kept — synthetic
rings, since the recorded corpora contain none.

Deterministic; no network (the fetch runs against the recorded payloads).
"""
from __future__ import annotations

import json
import math
import os
import types

import pytest

from wgresponse import geo
from wgresponse.contracts import GridSpec, LatLon
from tools import hindcast_harness as HH
from tools import hindcast_lib as H

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                       "hindcast_madre_daily_2026_09_27.json")
ACRE_M2 = 4046.8564224

# Madre capture times (UTC epoch s) and their rows, publication order
T_0702 = 1751471500.0      # 2025-07-02 15:51:40 — OBJECTIDs 37051, 37103
T_0703A = 1751535528.0     # 2025-07-03 09:38:48 — 37108 (no PolygonDateTime)
T_0703B = 1751559381.0     # 2025-07-03 16:16:21 — 37198
T_0704 = 1751672243.0      # 2025-07-04 23:37:23 — 37264, 37289, 37304, 37388
T_0705 = 1751748790.0      # 2025-07-05 20:53:10 — 37403 (no PolygonDateTime)


@pytest.fixture(scope="module")
def madre():
    with open(FIXTURE, "r", encoding="utf-8") as f:
        fx = json.load(f)
    rows = H.parse_series_attrs(fx["attrs_payload"])
    geoms = H.parse_snapshot_features(fx["geometry_payload"])
    return types.SimpleNamespace(fx=fx, rows=rows, geoms=geoms,
                                 acres={o: a for o, _t, a in rows})


def _grid_90m(rings: list) -> GridSpec:
    bb = H.rings_bbox([rings], 2000.0)
    return GridSpec(
        north_lat=bb.north, west_lon=bb.west, cell_m=90.0,
        nrows=math.ceil((bb.north - bb.south) * geo.M_PER_DEG_LAT / 90.0),
        ncols=math.ceil((bb.east - bb.west) * geo.m_per_deg_lon(bb.north) / 90.0))


def _raster_acres(spec: GridSpec, rings: list) -> float:
    return len(H.polygon_interior_mask(spec, rings)) * spec.cell_m ** 2 / ACRE_M2


def _union(geoms: dict, oids: list) -> list:
    return [ring for oid in sorted(oids) for ring in geoms[oid][1]]


# ---------------------------------------------------------------------------
# The recorded Madre payload
# ---------------------------------------------------------------------------

def test_recorded_same_time_rows_are_versions_of_one_polygon(madre):
    """What the fix rests on: each Madre capture time's rows are copies or
    edits of one polygon (they overlap almost completely), not pieces."""
    g = madre.geoms
    assert H.same_time_overlap_frac(g[37051][1], g[37103][1]) == 1.0
    for later in (37289, 37304, 37388):
        assert H.same_time_overlap_frac(g[37264][1], g[later][1]) > 0.95
    # the in-place edits grew the polygon 12 % under the same stamp
    assert madre.acres[37264] == pytest.approx(70800.49)
    assert madre.acres[37388] == pytest.approx(79518.67, abs=0.01)


def test_old_union_cancels_the_stacked_rings(madre):
    """The pre-fix law on the recorded rows: the 8,396-ac capture rasterizes
    to NOTHING and the 70,800-ac capture to ~9,200 ac of slivers."""
    g = madre.geoms
    stacked_0702 = _union(g, [37051, 37103])
    stacked_0704 = _union(g, [37264, 37289, 37304, 37388])
    assert _raster_acres(_grid_90m(stacked_0702), stacked_0702) == 0.0
    assert 8000.0 < _raster_acres(_grid_90m(stacked_0704), stacked_0704) < 10500.0


def test_first_publication_is_kept_and_rasterizes_to_its_gis_area(madre):
    g = madre.geoms
    rings, kept, dropped, acres = H.assemble_snapshot([37103, 37051], g,
                                                      madre.acres)
    assert (kept, dropped) == ([37051], [37103])
    assert acres == pytest.approx(8396.0)
    assert rings == list(g[37051][1])
    assert _raster_acres(_grid_90m(rings), rings) == pytest.approx(8396.0, rel=0.02)

    rings, kept, dropped, acres = H.assemble_snapshot(
        [37264, 37289, 37304, 37388], g, madre.acres)
    assert kept == [37264]                    # first publication, not the
    assert dropped == [37289, 37304, 37388]   # last revision (79,519 ac)
    assert acres == pytest.approx(70800.49)
    assert _raster_acres(_grid_90m(rings), rings) == pytest.approx(70800.49, rel=0.02)


def test_picks_gate_on_the_first_publication(madre):
    """Madre's 07-04 23:37 capture is gated at its first publication's
    70,800 ac. The pre-fix gate used the group max (79,519 ac, a revision
    made ~24 h later), which made the next capture — 07-05 20:53 at 79,936
    ac — look like no growth and skipped it."""
    picks = H.pick_snapshot_times(madre.rows)
    assert [t for t, _o, _a in picks] == [T_0702, T_0703A, T_0703B, T_0704,
                                          T_0705]
    assert [oids for _t, oids, _a in picks] == [
        [37051, 37103], [37108], [37198], [37264, 37289, 37304, 37388],
        [37403]]
    assert [round(a) for _t, _o, a in picks] == [8396, 35530, 52594, 70800,
                                                79936]
    # the old gate: 79,936 - 79,519 = 417 ac < max(8 % x 79,519, 300 ac)
    assert 79935.65 - 79518.67 < 0.08 * 79518.67


class _RecordedHttp:
    """Serves the recorded payloads to the harness's fetch layer."""

    def __init__(self, fx: dict):
        self.fx = fx
        self.urls = []

    def __call__(self, url, headers=None, timeout_s=120.0):
        self.urls.append(url)
        if "returnGeometry=false" in url:
            body = self.fx["attrs_payload"]
        else:
            body = self.fx["geometry_payload"]
        return 200, json.dumps(body).encode("utf-8")


def test_fetch_writes_schema_2_with_first_publication_snapshots(madre, tmp_path,
                                                                 monkeypatch):
    # only the first four capture times have recorded geometry
    real = H.pick_snapshot_times
    monkeypatch.setattr(H, "pick_snapshot_times",
                        lambda rows: real(rows, max_snapshots=4))
    path = tmp_path / "madre.json"
    HH._fetch_incident(_RecordedHttp(madre.fx), madre.fx["irwin"], "Madre",
                       79936.0, str(path))
    d = json.loads(path.read_text(encoding="utf-8"))
    assert d["schema"] == HH.INCIDENT_SCHEMA == "hindcast-incident/2"
    assert "first publication" in d["snapshot_rule"]
    snaps = {s["t_s"]: s for s in d["snapshots"]}
    assert sorted(snaps) == [T_0702, T_0703A, T_0703B, T_0704]
    assert (snaps[T_0702]["oids"], snaps[T_0702]["dropped_oids"]) == ([37051], [37103])
    assert snaps[T_0704]["oids"] == [37264]
    assert snaps[T_0704]["dropped_oids"] == [37289, 37304, 37388]
    assert snaps[T_0704]["acres"] == pytest.approx(70800.49)
    assert len(snaps[T_0702]["rings"]) == 1
    inc = HH._load_incident(str(path))
    assert inc.stacked_legacy == [] and inc.domain_bbox is None


def _legacy_madre_dict(madre) -> dict:
    """The pinned corpus's pre-fix Madre file shape: every same-time row's
    rings unioned (hindcast-incident/1)."""
    snaps = []
    for t, oids, ac in ((T_0702, [37051, 37103], 8396.0),
                        (T_0703A, [37108], 35530.44),
                        (T_0703B, [37198], 52594.0),
                        (T_0704, [37264, 37289, 37304, 37388], 79518.67)):
        snaps.append({"t_s": t, "acres": ac, "oids": oids,
                      "rings": [[[p.lat, p.lon] for p in ring]
                                for ring in _union(madre.geoms, oids)]})
    return {"schema": "hindcast-incident/1", "irwin": madre.fx["irwin"],
            "name": "Madre", "max_acres": 79518.67, "snapshots": snaps}


def test_repair_rebuilds_a_prefix_file_at_its_cached_capture_times(madre):
    legacy = _legacy_madre_dict(madre)
    new, summary = HH.repair_incident(legacy, madre.rows, madre.geoms,
                                      weather_loc=LatLon(34.9, -119.9))
    assert new["schema"] == "hindcast-incident/2"
    assert [s["t_s"] for s in new["snapshots"]] == [T_0702, T_0703A, T_0703B,
                                                   T_0704]
    by_t = {s["t_s"]: s for s in new["snapshots"]}
    assert by_t[T_0702]["oids"] == [37051]
    assert by_t[T_0704]["oids"] == [37264]
    assert by_t[T_0704]["acres"] == pytest.approx(70800.49)
    # single-row snapshots are untouched
    assert by_t[T_0703B]["rings"] == legacy["snapshots"][2]["rings"]
    assert [c["t_s"] for c in summary["rebuilt_snapshots"]] == [T_0702, T_0704]
    # the fixed picker would add 07-05 20:53 on these rows (reported, not applied)
    assert summary["fixed_picker_today"]["same_as_cached"] is False
    # a domain pin is recorded exactly when the rebuilt rings move the bbox
    if "domain_bbox" in new:
        assert summary["domain"].startswith("pinned")
        inc_domain = HH._incident_domain(
            H.HindcastIncident(irwin="x", name="x",
                               domain_bbox=tuple(new["domain_bbox"])),
            HH.ResponseConfig())[0]
        assert (inc_domain.west, inc_domain.south, inc_domain.east,
                inc_domain.north) == tuple(new["domain_bbox"])
    else:
        assert summary["domain"] == "unchanged"


def test_repair_refuses_when_the_rows_no_longer_reproduce_the_file(madre):
    legacy = _legacy_madre_dict(madre)
    legacy["snapshots"][1]["rings"][0][0][0] += 1e-5     # a changed vertex
    with pytest.raises(HH.RepairMismatch):
        HH.repair_incident(legacy, madre.rows, madre.geoms)


def test_replay_refuses_an_unrepaired_stacked_file(madre, tmp_path, capsys):
    inc_dir = tmp_path / "incidents"
    inc_dir.mkdir()
    (inc_dir / "madre.json").write_text(json.dumps(_legacy_madre_dict(madre)),
                                        encoding="utf-8")
    inc = HH._load_incident(str(inc_dir / "madre.json"))
    assert inc.stacked_legacy == [T_0702, T_0704]
    assert "pre-fix" in capsys.readouterr().err
    args = types.SimpleNamespace(
        data_dir=str(tmp_path), out="replay_results.json", label="",
        gust_blend=0.0, grass_floor=0.0, floor_gate=False, grass_only=False,
        model_error=0.0, p50_bound=False, spotting=False)
    assert HH.cmd_replay(args) == 0
    res = json.loads((tmp_path / "replay_results.json").read_text(encoding="utf-8"))
    assert res["pairs"] == []
    assert any("pre-fix" in s and "NOT replayed" in s for s in res["skips"])


# ---------------------------------------------------------------------------
# Genuinely separate pieces (synthetic: the recorded corpora contain none)
# ---------------------------------------------------------------------------

def _square(lat0: float, lon0: float, side_deg: float) -> list:
    return [LatLon(lat0, lon0), LatLon(lat0, lon0 + side_deg),
            LatLon(lat0 + side_deg, lon0 + side_deg),
            LatLon(lat0 + side_deg, lon0)]


def test_disjoint_same_time_rows_are_kept_as_pieces():
    a = _square(40.00, -120.00, 0.02)          # main body
    b = _square(40.00, -119.95, 0.01)          # a separate lobe, 3 km east
    a2 = _square(40.001, -120.001, 0.021)      # a later revision of a
    geoms = {1: (0.0, [a]), 2: (0.0, [b]), 3: (0.0, [a2])}
    rings, kept, dropped, acres = H.assemble_snapshot(
        [3, 2, 1], geoms, {1: 1000.0, 2: 250.0, 3: 1100.0})
    assert (kept, dropped) == ([1, 2], [3])
    assert acres == 1250.0
    assert rings == [a, b]
    spec = _grid_90m(rings)
    alone = len(H.polygon_interior_mask(spec, [a]))
    both = len(H.polygon_interior_mask(spec, rings))
    assert both == alone + len(H.polygon_interior_mask(spec, [b]))


def test_touching_pieces_are_kept_but_a_piece_inside_is_a_version():
    left = _square(40.00, -120.00, 0.02)
    right = _square(40.00, -119.98, 0.02)      # shares left's east edge
    inner = _square(40.005, -119.995, 0.005)   # inside left
    assert H.same_time_overlap_frac([right], [left]) <= H.SAME_TIME_OVERLAP_TOL
    _r, kept, dropped, _a = H.assemble_snapshot(
        [1, 2, 3], {1: (0.0, [left]), 2: (0.0, [right]), 3: (0.0, [inner])})
    assert (kept, dropped) == ([1, 2], [3])


def test_first_row_without_geometry_passes_the_base_to_the_next():
    sq = _square(40.0, -120.0, 0.01)
    _r, kept, dropped, _a = H.assemble_snapshot(
        [5, 6, 7], {6: (0.0, [sq]), 7: (0.0, [sq])})
    assert (kept, dropped) == ([6], [5, 7])


def test_a_multipolygon_row_keeps_all_its_own_rings():
    outer = _square(40.0, -120.0, 0.03)
    hole = _square(40.01, -119.99, 0.01)
    spot = _square(40.0, -119.9, 0.005)
    geoms = {1: (0.0, [outer, hole, spot]), 2: (0.0, [outer, spot])}
    rings, kept, dropped, _a = H.assemble_snapshot([1, 2], geoms)
    assert rings == [outer, hole, spot]
    assert (kept, dropped) == ([1], [2])
