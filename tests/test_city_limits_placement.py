"""CITY-LIMITS placement (SimLocation.bounds_poly; founder 2026-08-02
"rancho santa fe needs to be 100 homes within the city limits")."""
from __future__ import annotations

from wgresponse import geo, simui
from wgresponse.contracts import LatLon
from wgresponse.simui import LOCATIONS


SQUARE = (LatLon(0.0, 0.0), LatLon(0.0, 1.0),
          LatLon(1.0, 1.0), LatLon(1.0, 0.0))


def test_point_in_poly_basics():
    assert geo.point_in_poly(LatLon(0.5, 0.5), SQUARE)
    assert not geo.point_in_poly(LatLon(1.5, 0.5), SQUARE)
    assert not geo.point_in_poly(LatLon(0.5, -0.1), SQUARE)
    assert not geo.point_in_poly(LatLon(0.5, 0.5), SQUARE[:2])  # degenerate


def test_only_rsf_defines_bounds():
    for key, loc in LOCATIONS.items():
        poly = getattr(loc, "bounds_poly", ())
        if key == "rancho_santa_fe_ca":
            assert len(poly) >= 6
        else:
            assert poly == ()               # every other location: byte-identical


def test_rsf_clients_all_inside_city_limits():
    s = simui.SimSession("rancho_santa_fe_ca", 100, 10, seed="city-test")
    poly = LOCATIONS["rancho_santa_fe_ca"].bounds_poly
    assert len(s._clients) == 100
    for c in s._clients:
        assert geo.point_in_poly(c.loc, poly), c.client_id
    assert any("city-limits polygon" in n for n in s._notes)


def test_rsf_cluster_locs_stay_inside_city_limits():
    # The scratch_review cluster axis hands EXACT coordinates to SimSession
    # via client_locs (the onboarding/bench seam), which never calls
    # place_clients() — so cluster_locs must apply the city-limits polygon
    # itself. Unbounded draw from the whole-tile WUI band lands outside
    # (the rsf_city_c10/c12 cluster-run bug); bounded draw must not.
    from scratch_review.opt_ember import cluster_locs
    poly = LOCATIONS["rancho_santa_fe_ca"].bounds_poly
    unbounded = cluster_locs("rancho_santa_fe_ca", 100, 8, "city-test")
    assert not all(geo.point_in_poly(p, poly) for p in unbounded)
    bounded = cluster_locs("rancho_santa_fe_ca", 100, 8, "city-test",
                           bounds_poly=poly)
    assert len(bounded) == 100
    for p in bounded:
        assert geo.point_in_poly(p, poly), (p.lat, p.lon)


def test_rsf_bounds_are_meaningfully_smaller_than_tile():
    # the polygon must actually constrain: candidate pool well under the
    # full WUI band, yet comfortably above 100 homes
    from wgresponse.simui import load_tile, wui_cells
    tile, syn = load_tile("rancho_santa_fe_ca", cell_m=90.0)
    if syn:
        import pytest
        pytest.skip("real RSF tile missing — placement constraint unverifiable")
    poly = LOCATIONS["rancho_santa_fe_ca"].bounds_poly
    band = wui_cells(tile)
    inside = [rc for rc in band
              if geo.point_in_poly(geo.rc_to_latlon(tile.spec, rc[0], rc[1]),
                                   poly)]
    assert 100 <= len(inside) < len(band) * 0.5, (len(inside), len(band))
