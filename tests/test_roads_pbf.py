"""PBF-cut road graphs vs the committed Overpass graphs.

``tools/fetch_roads_pbf.py`` cuts a ``.wgr`` from a local Geofabrik extract
and must land on the same graph ``tools/fetch_sim_roads.py`` built, within
the slack of two OSM snapshots a few days apart. The Colorado extract is
not in git (``out/`` is ignored, ~365 MB). When that file is absent the
comparison tests skip with an explicit reason naming the path and the
download URL — a missing fetch must stay visible, same as
``tests/test_roads.py`` does for a missing ``.wgr``.

The geometry fixture and the committed-graph guard do not need the extract.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from wgresponse.roads import load_road_graph, road_file, save_road_graph
from wgresponse.simui import LOCATIONS
from tools.fetch_roads_pbf import (
    GENERATOR, HIGHWAY_CLASSES, compare_graphs, cut_road_graph,
    default_out_path, format_comparison, get_index, is_protected_committed_graph,
    main, missing_osmium_reason, missing_pbf_reason, preset_bbox,
    DEFAULT_COLORADO_PBF)

# Presets the task requires. corridor_vail_aspen is a major-roads graph
# from scratch_review/corridor_roads.py and is not an Overpass equivalence
# target.
COMPARE_KEYS = ("vail_co", "eagle_co", "eagle_vail_co")


def _write_tiny_osm(path: Path) -> None:
    """A handful of ways around Vail used to pin bbox semantics.

    Box used by the test: W -106.37, S 39.62, E -106.35, N 39.64.
    * way 10 residential, both nodes inside
    * way 11 primary, one node inside and one far outside (kept whole)
    * way 12 residential, both nodes far outside (dropped)
    * way 13 secondary, the segment crosses the box but neither node is
      inside (dropped — Overpass keeps a way only when a node is inside,
      it does not test segment intersection)
    * way 14 track, both nodes inside (dropped — not in HIGHWAY_REGEX)
    """
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="test_roads_pbf">
  <node id="1" lat="39.63000" lon="-106.36000"/>
  <node id="2" lat="39.63100" lon="-106.35900"/>
  <node id="3" lat="39.70000" lon="-106.50000"/>
  <node id="4" lat="40.00000" lon="-105.00000"/>
  <node id="5" lat="40.01000" lon="-105.01000"/>
  <node id="6" lat="39.60000" lon="-106.40000"/>
  <node id="7" lat="39.66000" lon="-106.32000"/>
  <node id="8" lat="39.63200" lon="-106.35800"/>
  <way id="10">
    <nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="residential"/>
  </way>
  <way id="11">
    <nd ref="1"/><nd ref="3"/>
    <tag k="highway" v="primary"/>
  </way>
  <way id="12">
    <nd ref="4"/><nd ref="5"/>
    <tag k="highway" v="residential"/>
  </way>
  <way id="13">
    <nd ref="6"/><nd ref="7"/>
    <tag k="highway" v="secondary"/>
  </way>
  <way id="14">
    <nd ref="2"/><nd ref="8"/>
    <tag k="highway" v="track"/>
  </way>
</osm>
""",
        encoding="utf-8",
    )


def _near(g, lat: float, lon: float, tol: float = 1e-4) -> bool:
    return any(abs(p.lat - lat) < tol and abs(p.lon - lon) < tol
               for p in g.nodes.values())


# ---------------------------------------------------------------------------
# Guards that do not need the extract
# ---------------------------------------------------------------------------

def test_highway_classes_match_the_overpass_regex():
    assert "service" in HIGHWAY_CLASSES
    assert "residential" in HIGHWAY_CLASSES
    assert "motorway_link" in HIGHWAY_CLASSES
    assert "track" not in HIGHWAY_CLASSES
    assert "path" not in HIGHWAY_CLASSES
    assert len(HIGHWAY_CLASSES) == 13


def test_missing_pbf_skip_reason_names_the_download():
    reason = missing_pbf_reason(Path("out/osm/does-not-exist.osm.pbf"))
    assert reason is not None
    assert "OSM PBF MISSING" in reason
    assert "colorado-latest.osm.pbf" in reason
    assert "https://download.geofabrik.de/north-america/us/colorado-latest.osm.pbf" in reason
    assert missing_pbf_reason(DEFAULT_COLORADO_PBF) is None or not DEFAULT_COLORADO_PBF.is_file()


def test_default_out_is_not_the_committed_graph():
    out = default_out_path("vail_co")
    assert out.name == "vail_co.wgr"
    assert "out" in out.parts
    assert is_protected_committed_graph(road_file("vail_co"))
    assert not is_protected_committed_graph(out)


def test_refuses_committed_graph_without_force():
    """--out pointing at a shipped .wgr must not touch the file."""
    target = road_file("vail_co")
    before = target.read_bytes()
    rc = main([
        "--pbf", "out/osm/does-not-exist.osm.pbf",
        "--key", "vail_co",
        "--out", str(target),
    ])
    assert rc == 2
    assert target.read_bytes() == before


def test_existing_output_is_left_untouched(tmp_path):
    out = tmp_path / "vail_co.wgr"
    out.write_text("sentinel", encoding="utf-8")
    rc = main([
        "--pbf", str(tmp_path / "missing.osm.pbf"),
        "--key", "vail_co",
        "--out", str(out),
    ])
    assert rc == 0
    assert out.read_text(encoding="utf-8") == "sentinel"


def test_bbox_requires_out_and_compare_requires_key():
    assert main(["--pbf", "x.osm.pbf", "--bbox", "-106.5,39.5,-106.2,39.8"]) == 2
    assert main(["--pbf", "x.osm.pbf", "--bbox", "-106.5,39.5,-106.2,39.8",
                 "--out", "x.wgr", "--compare"]) == 2
    assert main(["--pbf", "x.osm.pbf"]) == 2
    assert main(["--pbf", "x.osm.pbf", "--key", "vail_co",
                 "--bbox", "-106.5,39.5,-106.2,39.8",
                 "--out", "x.wgr"]) == 2


def test_bbox_keeps_full_way_when_any_node_is_inside(tmp_path):
    """Overpass semantics: any node inside, full geometry, regex filter."""
    reason = missing_osmium_reason()
    if reason:
        pytest.skip(reason)
    from wgresponse.contracts import BBox

    osm = tmp_path / "tiny.osm"
    _write_tiny_osm(osm)
    bbox = BBox(west=-106.37, south=39.62, east=-106.35, north=39.64)
    graph, _timing = cut_road_graph(
        osm, bbox, key="tiny", name="tiny",
        fetched_utc="2026-09-26T00:00:00Z")
    assert graph.meta["ways_fetched"] == 2
    assert graph.meta["generator"] == GENERATOR
    assert graph.meta["source"].startswith("tiny.osm (Geofabrik ")
    assert len(graph.nodes) == 3
    assert len(graph.edges) == 2
    # Full geometry of the way that only kisses the box: the outside node
    # is kept. The crossing-without-a-node way and the track are not.
    assert _near(graph, 39.63000, -106.36000)
    assert _near(graph, 39.63100, -106.35900)
    assert _near(graph, 39.70000, -106.50000)
    assert not _near(graph, 40.00000, -105.00000)
    assert not _near(graph, 39.60000, -106.40000)
    assert not _near(graph, 39.66000, -106.32000)
    assert not _near(graph, 39.63200, -106.35800)
    classes = sorted(e.highway_class for e in graph.edges)
    assert classes == ["primary", "residential"]
    # Second cut reads the index back from disk, same geometry.
    from tools import fetch_roads_pbf as mod
    mod._INDEX_CACHE.clear()
    again, _t = cut_road_graph(
        osm, bbox, key="tiny", name="tiny",
        fetched_utc="2026-09-26T00:00:00Z")
    again.meta["fetch_seconds"] = graph.meta["fetch_seconds"]
    again.meta["index_seconds"] = graph.meta["index_seconds"]
    graph.meta["fetch_seconds"] = again.meta["fetch_seconds"]
    p1, p2 = tmp_path / "a.wgr", tmp_path / "b.wgr"
    save_road_graph(graph, p1)
    save_road_graph(again, p2)
    assert p1.read_bytes() == p2.read_bytes()


# ---------------------------------------------------------------------------
# Colorado extract vs committed Overpass graphs
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def colorado_index():
    reason = missing_pbf_reason(DEFAULT_COLORADO_PBF)
    if reason:
        pytest.skip(reason)
    reason = missing_osmium_reason()
    if reason:
        pytest.skip(reason)
    return get_index(DEFAULT_COLORADO_PBF)


@pytest.mark.parametrize("key", COMPARE_KEYS)
def test_pbf_matches_committed_overpass(key, colorado_index, tmp_path, capsys):
    committed = load_road_graph(key)
    if committed is None:
        pytest.skip(
            f"COMMITTED ROAD GRAPH MISSING: data/roads/{key}.wgr — "
            f"the Overpass fetch for {key!r} is not in the tree, so the PBF "
            f"cut has nothing to be compared against.")
    graph, timing = cut_road_graph(
        DEFAULT_COLORADO_PBF, preset_bbox(key), key=key,
        name=LOCATIONS[key].name, index=colorado_index,
        fetched_utc="2026-09-26T00:00:00Z")
    save_road_graph(graph, tmp_path / f"{key}.wgr")
    reloaded = load_road_graph(key, road_dir=tmp_path)
    assert reloaded is not None
    assert reloaded.meta.get("generator") == GENERATOR
    src = reloaded.meta.get("source", "")
    assert "colorado-latest.osm.pbf" in src
    assert "Geofabrik" in src
    assert reloaded.meta.get("pbf_timestamp")
    report = compare_graphs(committed, reloaded, key, timing)
    text = format_comparison(report)
    # Quantified deltas (counts, LCC, snap meters, route distance) are the
    # assertion message, not a silent band.
    print(text)
    assert report.ok, text
    gated = [c for c in report.checks if c.name in ("nodes", "edges",
                                                     "largest_component_pct",
                                                     "route_on_roads",
                                                     "route_dist_m")
             or c.name.startswith("snap ")]
    assert gated, text
    assert any(c.name.startswith("snap ") and "z" in c.name for c in gated), text


def test_vail_cut_geometry_is_deterministic(colorado_index, tmp_path):
    kw = dict(
        pbf_path=DEFAULT_COLORADO_PBF, bbox=preset_bbox("vail_co"),
        key="vail_co", name=LOCATIONS["vail_co"].name, index=colorado_index,
        fetched_utc="2026-09-26T00:00:00Z")
    g1, _t1 = cut_road_graph(**kw)
    g2, _t2 = cut_road_graph(**kw)
    for g in (g1, g2):
        g.meta["fetch_seconds"] = 0
        g.meta["index_seconds"] = 0
    p1, p2 = tmp_path / "a.wgr", tmp_path / "b.wgr"
    save_road_graph(g1, p1)
    save_road_graph(g2, p2)
    assert p1.read_bytes() == p2.read_bytes()


def test_cli_compare_vail_and_second_run_is_idempotent(colorado_index, tmp_path,
                                                       capsys):
    out = tmp_path / "vail_co.wgr"
    rc = main([
        "--pbf", str(DEFAULT_COLORADO_PBF),
        "--key", "vail_co",
        "--compare",
        "--out", str(out),
    ])
    captured = capsys.readouterr().out
    assert rc == 0, captured
    assert out.is_file()
    assert "route_on_roads" in captured
    assert "route_dist_m" in captured
    assert "snap vail_co-z1" in captured
    first = out.read_bytes()
    rc2 = main([
        "--pbf", str(DEFAULT_COLORADO_PBF),
        "--key", "vail_co",
        "--out", str(out),
    ])
    assert rc2 == 0
    assert out.read_bytes() == first
