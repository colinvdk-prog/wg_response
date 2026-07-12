"""Regression: the ops console must render its operational layers WITHOUT any
external host (air-gapped / degraded-connectivity incident).

The console visualizes fire perimeter, arrival isochrones, protected clients,
crews, plan routes and safe zones on a map. Its convenience basemap (Leaflet +
OSM tiles) is loaded from a CDN, but a wildfire response may run with no network
to unpkg.com / tile.openstreetmap.org. These tests pin the dependency-free
offline fallback so the presentation layer's external hosts can never again
become a hard requirement:

  1. a self-contained SVG map shim is installed as ``window.L`` when the real
     Leaflet fails to load (no CDN, no basemap tiles);
  2. the basemap tile layer call is guarded so its failure cannot abort render;
  3. an operator can rehearse the air-gapped view on demand (``?nomap=1``).

No network, no third-party deps — the console HTML is read straight off disk.
"""
from __future__ import annotations

from pathlib import Path

import pytest

CONSOLE_HTML = (Path(__file__).resolve().parent.parent
                / "console" / "index.html")


@pytest.fixture(scope="module")
def html() -> str:
    return CONSOLE_HTML.read_text(encoding="utf-8")


def test_console_file_exists():
    assert CONSOLE_HTML.is_file(), CONSOLE_HTML


def test_offline_svg_map_shim_present(html: str):
    """When Leaflet is unreachable, a dependency-free SVG shim must stand in as
    window.L so the operational layers still draw."""
    assert "installFallbackLeaflet" in html
    # The shim is installed ONLY when the real Leaflet failed to load.
    assert 'typeof window.L === "undefined"' in html
    # The shim exposes the L.* surface the console actually calls.
    for api in ("map:", "layerGroup:", "circleMarker:", "polygon:",
                "polyline:", "marker:"):
        assert api in html, api
    # A tagged fallback marker so the offline branch is identifiable.
    assert "_fallback: true" in html


def test_offline_shim_uses_no_external_host_or_tiles(html: str):
    """The fallback must not itself reach out to a CDN or tile server."""
    # The fallback tileLayer is a no-op (no basemap offline).
    assert "no basemap offline" in html
    # Guard: the shim block must not embed an OSM/unpkg URL of its own.
    shim_start = html.index("installFallbackLeaflet")
    shim_end = html.index("const FILE", shim_start)
    shim_src = html[shim_start:shim_end]
    assert "unpkg.com" not in shim_src
    assert "openstreetmap" not in shim_src.lower()
    assert "tile." not in shim_src


def test_basemap_tilelayer_is_guarded(html: str):
    """The live OSM basemap call must be wrapped so an unreachable tile host
    degrades to a plain background instead of aborting the whole render."""
    idx = html.index("tile.openstreetmap.org")
    # A try{...}catch surrounds the tileLayer(...).addTo(map) call.
    pre = html.rindex("try", 0, idx)
    post = html.index("catch", idx)
    assert pre < idx < post
    between = html[pre:post]
    assert "tileLayer" in between and ".addTo(map)" in between


def test_air_gapped_rehearsal_switch_present(html: str):
    """Operators can force the offline presentation before an incident."""
    assert 'get("nomap")' in html
    # Forcing nomap clears window.L so the fallback path is exercised.
    nomap_line = next(l for l in html.splitlines() if 'get("nomap")' in l)
    assert "window.L = undefined" in nomap_line


def test_all_operational_layers_rendered(html: str):
    """The layers requirement (fire, isochrones, clients, crews, routes, safe
    zones) must all be wired — the offline map is only useful if it draws them."""
    for layer in ("layers.fire", "layers.iso", "layers.clients",
                  "layers.crews", "layers.routes", "layers.zones"):
        assert layer in html, layer
