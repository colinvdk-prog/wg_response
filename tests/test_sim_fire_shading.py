"""tests/test_sim_fire_shading.py — "shade the fire" (founder decision
2026-09-26): the simulator's shaded fire is the SIMULATED ACTUAL FIRE (the
engine's nominal member, the ground truth the crews are audited against),
and the h1/h5/h24 bands are the ladder's median (P50) projection BOUNDED by
it. Until then the page filled the pooled ensemble P50 <= now, which under-
drew a wind-driven fire ~20x (park_city_windstorm, T+1h45: 0.6 vs 12.4 km2;
scratch_review/p50_vs_nominal_probe.py; docs/SIMULATOR_DESIGN.md).

Logic tests run on a SYNTHETIC grass tile (tile_dir in tmp, roads pinned off)
with the page's fire physics (ember spotting + grass floor), so spot-fire
islands and the direction-perturbation median lag are both exercised; the
founder's two frames (park_city_windstorm extreme, an eagle_vail_co high
fire) run on the committed real tiles and skip loudly if a tile is absent.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from wgresponse import geo
from wgresponse.fuels import cold_black_minutes
from wgresponse.simui import (
    DEFAULT_TILE_DIR,
    FIRE_VIEW_CLASSES,
    LOCATIONS,
    SimSession,
    _COLD_BLACK_PASS_S,
    _pool_min,
    _threshold_rings,
    fire_view_corners,
)
from wgresponse.terrain import SyntheticTerrain, save_ascii_tile

HOUR = 3600.0
BURNED, BURNING, H1, H5, H24 = 1, 2, 3, 4, 5
FIRE = (BURNED, BURNING)
KEY = "boulder_co"
CELL_M = 200.0
SIM_HTML = Path(__file__).resolve().parent.parent / "console" / "simulator.html"

# the page's session physics (server /api/sim/new defaults) on a small grid;
# sim_weather_profile "generic": derived under the legacy constant intensity
# presets and pinned to them (the simulator default is now each community's
# own weather, 2026-10-09; tests/test_city_weather.py)
PAGE_FIRE = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10,
             "grid_cell_m": CELL_M, "horizon_h": 48.0,
             "sim_weather_profile": "generic"}


def _tile_dir(tmp_path, size_km=16.0):
    """Synthetic uniform-grass tile with a 3x3 non-burnable pocket and a
    one-cell non-burnable line east of centre — unburned holes and a break
    the embers jump, so the actual burn has islands and pockets."""
    loc = LOCATIONS[KEY]
    bbox = geo.bbox_around([loc.center], pad_m=size_km * 500.0)
    tile = SyntheticTerrain(seed=f"fire-shading|{KEY}",
                            kind="grass").tile(bbox, CELL_M)
    spec = tile.spec
    rm, cm = spec.nrows // 2, spec.ncols // 2
    for r in range(rm - 1, rm + 2):
        for c in range(cm - 1, cm + 2):
            tile.fuel.set(r, c, 91.0)
    for r in range(spec.nrows):
        if abs(r - rm) > 6:
            tile.fuel.set(r, cm + 8, 98.0)          # a river with a ford
    d = tmp_path / f"tiles_{KEY}"
    d.mkdir(exist_ok=True)
    save_ascii_tile(tile, str(d / f"{KEY}.wgt"))
    return d


def _session(tmp_path, seed="shade", cfg=None, n_clients=6, n_crews=1):
    over = dict(PAGE_FIRE)
    over.update(cfg or {})
    return SimSession(KEY, n_clients, n_crews, seed=seed, cfg_overrides=over,
                      tile_dir=_tile_dir(tmp_path), roads_module=None)


def _ignite(s, dist_m=4_000.0, bearing_off=0.0, intensity="high"):
    loc = LOCATIONS[KEY]
    p = geo.destination(loc.center, loc.hazard_wind_dir_deg + bearing_off,
                        dist_m)
    return s.ignite(p.lat, p.lon, intensity=intensity)


def _classes(fv):
    out = []
    for v, n in zip(fv["values"], fv["runs"]):
        out.extend([v] * n)
    assert len(out) == fv["nrows"] * fv["ncols"], "runs must tile the grid"
    return out


def _rle01(rle):
    out = set()
    i = 0
    val = rle["first_value"]
    for run in rle["runs"]:
        if val:
            out.update(range(i, i + run))
        i += run
        val = 1 - val
    return out


def _union(s, attr):
    """Per-cell min over the session's fires of members[0] ('nominal') or
    p50 ('p50')."""
    out = None
    for fid in s._fire_order:
        ens = s._fires[fid].ens
        if ens is None:
            continue
        data = ens.members[0].data if attr == "nominal" else ens.p50.data
        out = list(data) if out is None else [min(a, b)
                                              for a, b in zip(out, data)]
    return out


def _fire_cells(cls):
    return {i for i, v in enumerate(cls) if v in FIRE}


# ---------------------------------------------------------------------------
# the shaded fire IS the nominal burned mask
# ---------------------------------------------------------------------------

def test_fire_view_shape_before_any_fire(tmp_path):
    s = _session(tmp_path, seed="empty")
    st = s.state()
    fv = st["fire_view"]
    assert fv["encoding"] == "rle_class"
    assert fv["classes"] == list(FIRE_VIEW_CLASSES) == [
        "none", "burned", "burning", "h1", "h5", "h24"]
    assert fv["values"] == [0] and fv["runs"] == [fv["nrows"] * fv["ncols"]]
    assert fv["strokes"] == {"fire": [], "h1": [], "h5": [], "h24": []}
    assert set(fv["areas_km2"].values()) == {0.0}
    assert fv["stroke_coords"] == "grid_corner_chain"
    assert st["burned"] == [] and st["burned_p50"] == []
    assert st["fire_bands"] == {"h1": [], "h5": [], "h24": []}
    json.dumps(st, allow_nan=False)


def test_shaded_fire_equals_the_nominal_burned_mask(tmp_path):
    """Every tick: the cells the page shades as fire (burned + burning) are
    EXACTLY the union over fires of nominal arrival <= sim-now — the same
    mask as terrain.burned and the 10c audit — spot-fire islands included;
    the compat rings follow (burned = nominal rings, burned_p50 = the old
    median fill, verbatim)."""
    s = _session(tmp_path)
    _ignite(s)
    max_parts = 0
    for _ in range(10):
        st = s.tick()
        now = st["sim_now_s"]
        nom = _union(s, "nominal")
        truth = {i for i, v in enumerate(nom) if v <= now}
        cls = _classes(st["fire_view"])
        assert _fire_cells(cls) == truth
        assert _rle01(st["terrain"]["burned"]) == truth
        cell_km2 = (st["fire_view"]["grid"]["cell_m"] / 1000.0) ** 2
        assert st["fire_view"]["areas_km2"]["fire"] == pytest.approx(
            len(truth) * cell_km2)
        ens = s._fires["sim-f1"].ens
        assert st["burned"] == _threshold_rings(*_pool_min(ens.members[0]),
                                                now)
        assert st["burned_p50"] == _threshold_rings(*_pool_min(ens.p50), now)
        # every piece of the burn — the main fire and each spot-fire island
        # (4-connected) — gets exactly one outer outline loop
        parts = _components4(truth, st["fire_view"]["ncols"])
        outer = [x for x in st["fire_view"]["strokes"]["fire"]
                 if _loop_area(x) > 0]
        assert len(outer) == parts
        max_parts = max(max_parts, parts)
    assert max_parts > 1, "spotting should throw at least one island here"
    assert st["scoreboard"]["violations"] == 0


def _components4(cells, nc):
    left = set(cells)
    n = 0
    while left:
        n += 1
        stack = [left.pop()]
        while stack:
            i = stack.pop()
            r, c = divmod(i, nc)
            for j, ok in ((i - nc, r > 0), (i + nc, True), (i - 1, c > 0),
                          (i + 1, c < nc - 1)):
                if ok and j in left:
                    left.remove(j)
                    stack.append(j)
    return n


def test_bands_are_the_median_bounded_by_the_actual_fire(tmp_path):
    """Band cells never lie on the burn; every other cell's class is the
    ladder median against sim-now +1/+5/+24 h, and a cell the median says
    has already burned but the nominal front has not reached ('overdue') is
    drawn in <1 h. Non-vacuous: the raw median bands DO cover part of the
    actual burn here (the old page painted them over the fire)."""
    # the simulator ties the median to the fire by default since
    # 2026-09-26 (ensemble_p50_nominal_bound, "match production"); this
    # test exercises the display under a LAGGING median, so it pins the
    # bound off (the condition the shading was built for)
    s = _session(tmp_path, seed="bands",
                 cfg={"ensemble_p50_nominal_bound": False})
    _ignite(s)
    raw_inside = 0
    for _ in range(8):
        st = s.tick()
        now = st["sim_now_s"]
        nom = _union(s, "nominal")
        p50 = _union(s, "p50")
        cls = _classes(st["fire_view"])
        for i, v in enumerate(cls):
            if nom[i] <= now:
                assert v in FIRE, i
                if now < p50[i] <= now + 24 * HOUR:
                    raw_inside += 1
                continue
            q = p50[i]
            want = (H1 if q <= now + HOUR else H5 if q <= now + 5 * HOUR
                    else H24 if q <= now + 24 * HOUR else 0)
            assert v == want, (i, v, want)
        assert not (_fire_cells(cls)
                    & {i for i, v in enumerate(cls) if v in (H1, H5, H24)})
    assert raw_inside > 0, "the median should lag the actual fire somewhere"


def test_burning_now_is_the_engine_cold_black_window(tmp_path):
    """burning = fire cells whose nominal arrival is inside their fuel's
    cold-black window (the SEM-10 transit rule the simulator arms per fuel
    class); with the per-class rule off it is the flat 45 min."""
    for per_class in (True, False):
        s = _session(tmp_path, seed="burning",
                     cfg={"transit_cold_black_by_class": per_class})
        _ignite(s)
        for _ in range(12):                # 3 h: the grass run is on
            st = s.tick()
        now = st["sim_now_s"]
        fv = st["fire_view"]
        assert fv["burning_window"] == ("fuel_class" if per_class
                                        else "flat_45_min")
        nom = _union(s, "nominal")
        fuel = s._tile.fuel.data
        cls = _classes(fv)
        n_burning = 0
        for i in _fire_cells(cls):
            w = (cold_black_minutes(int(round(fuel[i]))) * 60.0 if per_class
                 else _COLD_BLACK_PASS_S)
            want = BURNING if now - nom[i] < w else BURNED
            assert cls[i] == want, (per_class, i)
            n_burning += cls[i] == BURNING
        assert 0 < n_burning < len(_fire_cells(cls))


def test_reanchor_never_relights_the_old_burn(tmp_path):
    """A world change re-anchors the field from the nominal burned mask with
    every consumed cell seeded AT the anchor — the display keeps each cell's
    TRUE arrival, so the old burn does not flash 'burning now' after a wind
    turn, and the shaded fire stays the nominal mask across the re-anchor."""
    s = _session(tmp_path, seed="reanchor",
                 cfg={"transit_cold_black_by_class": False})   # flat 45 min
    fid = _ignite(s)
    for _ in range(8):                     # 2 h
        st = s.tick()
    f = s._fires[fid]
    old = f.ens
    now0 = st["sim_now_s"]
    cold = {i for i, v in enumerate(old.members[0].data)
            if v <= now0 - _COLD_BLACK_PASS_S}
    assert cold, "scenario must have cold black before the wind turn"
    s.set_wind(LOCATIONS[KEY].hazard_wind_dir_deg + 30.0)
    st = s.tick()
    assert f.ens is not old and f.anchor_t0_s == now0, "must re-anchor"
    cls = _classes(st["fire_view"])
    assert all(cls[i] == BURNED for i in cold)
    assert all(f._fv_burn_t[i] == old.members[0].data[i] for i in cold)
    now = st["sim_now_s"]
    assert _fire_cells(cls) == {i for i, v in enumerate(f.ens.members[0].data)
                                if v <= now}


def test_time_scrub_replays_the_same_truth(tmp_path):
    """LAW 1 TIME-SCRUB: a recorded tick replays the fire_view it was served
    with, and that view is the nominal truth AT that tick — before and after
    a mid-run re-anchor."""
    s = _session(tmp_path, seed="scrub")
    fid = _ignite(s)
    live = {}
    fields = {}
    for k in range(1, 7):
        if k == 4:
            s.set_wind(LOCATIONS[KEY].hazard_wind_dir_deg - 40.0)
        st = s.tick()
        live[k] = json.loads(json.dumps(st["fire_view"]))
        fields[k] = (st["sim_now_s"], s._fires[fid].ens)
    for k in range(1, 7):
        snap = s.state_at(k)
        assert snap["fire_view"] == live[k]
        now, ens = fields[k]
        assert _fire_cells(_classes(snap["fire_view"])) == {
            i for i, v in enumerate(ens.members[0].data) if v <= now}
    assert fields[3][1] is not fields[4][1], "tick 4 must have re-anchored"


# ---------------------------------------------------------------------------
# the founder's frames on the real tiles
# ---------------------------------------------------------------------------

def _real_session(key, intensity, bearing, dist_m=None, ignition=None,
                  seed="shade-real", p50_bound=None):
    if not (DEFAULT_TILE_DIR / f"{key}.wgt").exists():
        pytest.skip(f"real tile data/tiles/{key}.wgt not present")
    s = SimSession(key, 8, 1, seed=seed,
                   cfg_overrides={"spotting_enabled": True,
                                  "grass_ros_floor_frac": 0.10,
                                  "sim_weather_profile": "generic",
                                  **({} if p50_bound is None else
                                     {"ensemble_p50_nominal_bound": p50_bound})},
                   roads_module=None)
    if ignition is not None:
        return s, s.ignite(ignition[0], ignition[1], intensity=intensity,
                           wind_dir_deg=bearing)
    center = LOCATIONS[key].center
    for db in (0, 10, -10, 20, -20, 30, -30):      # server preset loop
        for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
            p = geo.destination(center, bearing + db, dist_m * shrink)
            try:
                return s, s.ignite(p.lat, p.lon, intensity=intensity,
                                   wind_dir_deg=bearing)
            except ValueError:
                continue
    raise AssertionError("no burnable ignition cell")


@pytest.mark.parametrize("key,intensity,bearing,dist_m,ignition,ticks,gap", [
    # park_city_windstorm (server.SCENARIOS): T+1h45, the founder's frame
    ("park_city_ut", "extreme", 225.0, 7000.0, None, 7, 5.0),
    # a HIGH fire 8 km WSW of the Eagle -> Vail tile centre, T+4h
    ("eagle_vail_co", "high", 240.0, None, (39.598999, -106.700863), 16, 2.0),
])
def test_high_extreme_fire_is_shaded_as_the_nominal_truth(
        key, intensity, bearing, dist_m, ignition, ticks, gap):
    """On the real tiles the shaded fire equals the nominal cell count and is
    at least the P50's own burned area — here several times it: the pooled
    median lags the simulated truth (docs/SIMULATOR_DESIGN.md, 2026-09-26).
    Run with the simulator's median bound OFF: that is the lag the shading
    was built to expose (the bound is armed by default since 2026-09-26 —
    test_default_median_never_lags_the_shaded_fire pins that world)."""
    s, fid = _real_session(key, intensity, bearing, dist_m, ignition,
                           p50_bound=False)
    for _ in range(ticks):
        st = s.tick()
    now = st["sim_now_s"]
    ens = s._fires[fid].ens
    n_nom = sum(1 for v in ens.members[0].data if v <= now)
    n_p50 = sum(1 for v in ens.p50.data if v <= now)
    cls = _classes(st["fire_view"])
    assert len(_fire_cells(cls)) == n_nom
    a = st["fire_view"]["areas_km2"]
    cell_km2 = (s._tile.spec.cell_m / 1000.0) ** 2
    assert a["fire"] == pytest.approx(n_nom * cell_km2)
    assert a["p50_burned"] == pytest.approx(n_p50 * cell_km2)
    assert a["fire"] >= a["p50_burned"]
    assert a["fire"] >= gap * a["p50_burned"], (
        f"{key} {intensity}: actual {a['fire']:.2f} km2 vs P50 "
        f"{a['p50_burned']:.2f} km2 — the median-lag finding moved; re-run "
        f"scratch_review/p50_vs_nominal_probe.py and update the design doc")


# ---------------------------------------------------------------------------
# strokes: exact cell edges
# ---------------------------------------------------------------------------

def _unit_edges(stroke):
    pts = fire_view_corners(stroke)
    out = []
    for (r0, c0), (r1, c1) in zip(pts, pts[1:]):
        assert r0 == r1 or c0 == c1, "chain steps are axis-aligned"
        dr = (r1 > r0) - (r1 < r0)
        dc = (c1 > c0) - (c1 < c0)
        r, c = r0, c0
        while (r, c) != (r1, c1):
            out.append(((r, c), (r + dr, c + dc)))
            r, c = r + dr, c + dc
    return out, pts[0] == pts[-1]


def _loop_area(stroke):
    pts = fire_view_corners(stroke)
    if pts[0] != pts[-1]:
        return 0.0
    return 0.5 * sum(c0 * r1 - c1 * r0
                     for (r0, c0), (r1, c1) in zip(pts, pts[1:]))


def _sides(edge, nr, nc):
    """(left, right) flat cell index (None off-grid) of a unit edge a->b."""
    (r0, c0), (r1, c1) = edge
    if r0 == r1:
        c = min(c0, c1)
        above, below = (r0 - 1, c), (r0, c)
        right, left = (below, above) if c1 > c0 else (above, below)
    else:
        r = min(r0, r1)
        west, east = (r, c0 - 1), (r, c0)
        right, left = (west, east) if r1 > r0 else (east, west)

    def flat(rc):
        return (rc[0] * nc + rc[1]
                if 0 <= rc[0] < nr and 0 <= rc[1] < nc else None)
    return flat(left), flat(right)


def test_strokes_are_exact_cell_edges(tmp_path):
    """The fire outline runs on edges between a fire cell (on its right) and
    a non-fire cell; each band front on edges between a band cell of that
    lead (on its right) and a cell beyond it — never along the fire — every
    edge drawn once, loops closed; the chain code decodes in the page."""
    s = _session(tmp_path, seed="strokes")
    _ignite(s)
    for _ in range(8):
        st = s.tick()
    fv = st["fire_view"]
    nr, nc = fv["nrows"], fv["ncols"]
    cls = _classes(fv)
    assert fv["strokes"]["fire"] and fv["strokes"]["h24"], "nothing to check"
    for key, k in (("fire", BURNING), ("h1", H1), ("h5", H5), ("h24", H24)):
        seen = set()
        for stroke in fv["strokes"][key]:
            assert isinstance(stroke[2], str)
            assert re.fullmatch(r"([ESWN]\d*)+", stroke[2])
            edges, _closed = _unit_edges(stroke)
            for e in edges:
                key_e = frozenset(e)
                assert key_e not in seen, f"{key}: edge drawn twice"
                seen.add(key_e)
                left, right = _sides(e, nr, nc)
                rc = cls[right] if right is not None else None
                lc = cls[left] if left is not None else 0
                if key == "fire":
                    assert rc in FIRE and lc not in FIRE, (key, e)
                else:
                    assert rc is not None and H1 <= rc <= k, (key, e)
                    assert lc == 0 or lc > k, (key, e)
    for stroke in fv["strokes"]["fire"]:
        assert _unit_edges(stroke)[1], "fire outline loops are closed"


def test_two_fires_shade_the_union(tmp_path):
    """Multiple fires: the shaded fire is the union of every fire's nominal
    burn; bands come from the earliest median over fires."""
    s = _session(tmp_path, seed="two")
    _ignite(s, dist_m=4_000.0, bearing_off=25.0)
    _ignite(s, dist_m=4_000.0, bearing_off=-25.0)
    for _ in range(6):
        st = s.tick()
    now = st["sim_now_s"]
    nom = _union(s, "nominal")
    p50 = _union(s, "p50")
    cls = _classes(st["fire_view"])
    assert _fire_cells(cls) == {i for i, v in enumerate(nom) if v <= now}
    for i, v in enumerate(cls):
        if v in (H1, H5, H24):
            assert nom[i] > now and p50[i] <= now + 24 * HOUR
    json.dumps(st, allow_nan=False)


def test_compat_fields_and_bounded_band_rings(tmp_path):
    """fire_bands keep their shape (closed rings, nested h1 in h5 in h24)
    and now bound the median by the actual fire: every band ring set
    contains the actual burn."""
    s = _session(tmp_path, seed="compat")
    _ignite(s)
    for _ in range(8):
        st = s.tick()

    def bbox(rings):
        pts = [p for r in rings for p in r]
        return (min(p[0] for p in pts), min(p[1] for p in pts),
                max(p[0] for p in pts), max(p[1] for p in pts))
    b0 = bbox(st["burned"])
    boxes = [bbox(st["fire_bands"][k]) for k in ("h1", "h5", "h24")]
    for inner, outer in zip([b0] + boxes, boxes):
        assert outer[0] <= inner[0] + 1e-9 and outer[1] <= inner[1] + 1e-9
        assert outer[2] >= inner[2] - 1e-9 and outer[3] >= inner[3] - 1e-9
    for rings in [st["burned"], st["burned_p50"]] + [
            st["fire_bands"][k] for k in ("h1", "h5", "h24")]:
        for ring in rings:
            assert ring[0] == ring[-1] and len(ring) >= 4


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

def test_simulator_page_shades_the_fire_view():
    """The page shades state.fire_view (exact raster in its own pane under
    every vector layer, strokes decoded from the chain code on the GridSpec
    law), keeps the legacy ring/RLE fallbacks, draws the bands' FRONTS as
    lines only, and the legend tells the truth: the shaded area is the
    simulated fire, the bands are the median forecast from its edge."""
    html = SIM_HTML.read_text(encoding="utf-8")
    for frag in ("st.fire_view", 'fv.encoding === "rle_class"',
                 "drawFireRaster", "L.imageOverlay", '"firePane"',
                 "grid_corner_chain", "fireViewLines", "FIRE_FILL",
                 "m_per_deg_lat", "syncLinePool", "clearFireView",
                 # the switcher still hides the fire / each band
                 "fireClassVisible", "overlayadd overlayremove",
                 # legacy fallbacks survive (older backend)
                 "burnedSegments", "ringList(st.burned)"):
        assert frag in html, frag
    for frag in ("burned — the simulated fire", "burning now",
                 "reached in &lt;1 h (red)", "reached in &lt;5 h (orange)",
                 "reached in &lt;24 h (yellow)",
                 "bands = median (P50) forecast, from the fire's edge"):
        assert frag in html, frag
    # the offline SVG shim renders the raster too
    shim = html[html.index("installFallbackLeaflet"):
                html.index("_fallback: true")]
    assert "imageOverlay:" in shim and 'sp.kind === "image"' in shim
    assert "setUrl(url)" in shim


def test_default_median_never_lags_the_shaded_fire():
    """Founder 2026-09-26 "match production": the simulator arms
    ensemble_p50_nominal_bound, so the ladder median is never later than the
    fire the page shades (p50 <= nominal per cell): its burned area covers
    the actual fire and exceeds it only by cells the median reaches first,
    and every home on ground the fire burned at least a tick ago reads
    IMPACTED — the ladder no longer lags the map."""
    s, fid = _real_session("park_city_ut", "extreme", 225.0, dist_m=7000.0)
    assert s._cfg.ensemble_p50_nominal_bound is True
    for _ in range(7):
        st = s.tick()
    now = st["sim_now_s"]
    ens = s._fires[fid].ens
    nom, p50 = ens.members[0].data, ens.p50.data
    assert all(p <= n for p, n in zip(p50, nom))            # never later
    a = st["fire_view"]["areas_km2"]
    assert a["p50_burned"] >= a["fire"]
    assert a["p50_burned"] <= 1.05 * a["fire"], a
    spec = ens.members[0].spec
    by_id = {c.client_id: c for c in s._clients}
    checked = 0
    for row in st["clients"]:
        rc = geo.latlon_to_rc(spec, by_id[row["client_id"]].loc)
        if rc is None:
            continue
        t = nom[rc[0] * spec.ncols + rc[1]]
        if t <= now - s._cfg.replan_period_s:
            checked += 1
            assert row["state"] == "impacted", (row["client_id"], row["state"])
    assert checked >= 1, "no home inside the burn — rebuild the scenario"
