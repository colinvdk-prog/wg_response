"""wgresponse.clients — protected-client registries (contracts.ClientRegistry).

    MockClientRegistry     deterministic synthetic clients (contracts.seeded_rng)
    DynamoClientRegistry   reads the existing wg-consumer-profiles table
    preset_client_registry a MockClientRegistry over preset_roster(): N demo
                           homes on EVERY simulator preset, placed by the
                           simulator's own WUI placement (the watch daemon's
                           --registry mock-presets; W6 thin, 2026-09-26 —
                           see the PRESET DEMO ROSTER section at the bottom)

Source-of-truth mapping (docs/research/reusable_assets.md §1): the firerisk
consumer golden record ``ConsumerProfile`` (DynamoDB table
``wg-consumer-profiles-<stage>``, PK ``email``) carries
``graphgel_enrolled: bool`` — a projection of the canonical
``Homeowner.fr_graphgel_enrolled`` (models.py:136 -> profile.py:298 in the
firerisk repo). A profile is a WG Response protected client iff
``graphgel_enrolled`` is truthy AND it has usable ``lat``/``lon`` floats
(degrees WGS84). Field mapping into contracts.ClientProperty:

    client_id      <- email (profile PK)
    loc            <- (lat, lon) degrees
    address        <- primary_address (fallback: address)
    name           <- name
    value_usd      <- home_value_usd (REAL schema field, models.py:571 in the
                      firerisk repo; profile.py:289 writes it), legacy fallback
                      home_value, else $1M default WITH a logging.warning
                      (Council-1 OPS-7: the old silent default put every live
                      client at $1M and distorted dispatch triage).
                      home_value_estimated (models.py:572) is a bool flag, NOT
                      a dollar value, and is never read as one.
    priority_weight<- priority_weight if present, else 1.0
    contact_*      <- email / phone

Sprint 2 (Council-1 OPS-7 scan finding): DynamoClientRegistry caches the
mapped roster for cfg.registry_ttl_s (default 300 s) — ONE underlying table
scan per TTL window regardless of how many per-incident bbox queries the
monitor issues; bbox filtering is applied on the cached set. ``.invalidate()``
drops the cache explicitly (tests/ops). The clock is injectable for tests.

boto3 is an OPTIONAL dependency imported lazily; tests inject ``scan_fn``
(a zero-arg callable returning the full list of item dicts) so no AWS or
third-party dep is ever touched. All distances meters, money USD.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Callable, Optional

from .contracts import BBox, ClientProperty, LatLon, ResponseConfig, seeded_rng

log = logging.getLogger(__name__)

M_PER_DEG_LAT = 111_320.0
DEFAULT_VALUE_USD = 1_000_000.0    # mirrors contracts.ClientProperty.value_usd default


def _truthy(v) -> bool:
    """DynamoDB-tolerant boolean: True, 'true'/'True'/'1', 1, Decimal(1)."""
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "y")
    try:
        return float(v) != 0.0
    except (TypeError, ValueError):
        return False


def _maybe_float(v) -> Optional[float]:
    """float() with None/blank/garbage -> None (handles boto3 Decimal)."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _resolve_value_usd(item: dict, client_id: str = "") -> float:
    """Home-value mapping shared by EVERY registry (Council-1 OPS-7, Sprint 2 C3e).

    The real wg-consumer-profiles field is ``home_value_usd`` (firerisk
    persistence/models.py:571; consumer/profile.py:289 writes it).
    ``home_value_estimated`` (models.py:572) is a bool provenance flag, never a
    dollar amount. Preference order: home_value_usd > legacy home_value >
    DEFAULT_VALUE_USD — and the default WARNS, so a schema drift can never
    again silently price every client at $1M and distort dispatch triage."""
    v = _maybe_float(item.get("home_value_usd"))
    if v is not None:
        return v
    v = _maybe_float(item.get("home_value"))
    if v is not None:
        return v
    log.warning(
        "consumer profile %s has neither home_value_usd nor home_value — "
        "defaulting value_usd to $%.0f (dispatch triage weight is a guess; "
        "fix the profile upstream)", client_id or "<unknown>", DEFAULT_VALUE_USD)
    return DEFAULT_VALUE_USD


def _item_to_client(item: dict) -> Optional[ClientProperty]:
    """Map one raw ConsumerProfile item dict -> ClientProperty, or None if the
    row is not a protected client (unenrolled / no coordinates / no email PK).
    Single mapping path shared by DynamoClientRegistry AND MockClientRegistry
    so simulations exercise exactly the live field semantics."""
    if not _truthy(item.get("graphgel_enrolled")):
        return None
    lat = _maybe_float(item.get("lat"))
    lon = _maybe_float(item.get("lon"))
    email = str(item.get("email") or "").strip()
    if lat is None or lon is None:
        log.info("consumer profile %s enrolled but missing lat/lon — skipped",
                 email or "<no-email>")
        return None
    if not email:
        log.info("consumer profile at (%s, %s) has no email PK — skipped", lat, lon)
        return None
    weight = _maybe_float(item.get("priority_weight"))
    return ClientProperty(
        client_id=email,
        loc=LatLon(lat, lon),
        address=str(item.get("primary_address") or item.get("address") or ""),
        name=str(item.get("name") or ""),
        value_usd=_resolve_value_usd(item, email),
        graphgel_enrolled=True,
        priority_weight=weight if weight is not None else 1.0,
        contact_email=email,
        contact_phone=str(item.get("phone") or ""),
    )


class MockClientRegistry:
    """Deterministic synthetic protected clients for tests/simulation.

    Generates n GraphGel-enrolled properties scattered within spread_km
    kilometers of center (uniform in lat/lon offsets), or serves an explicit
    ``clients`` list, or maps raw ConsumerProfile-style ``items`` dicts through
    the SAME ``_item_to_client`` path as DynamoClientRegistry (Sprint 2 C3e:
    sim clients exercise the live field semantics, including the
    home_value_usd > home_value > default+warning preference). Generated
    values also route through the shared ``_resolve_value_usd``. Same
    (seed, center, n, spread_km) always yields the identical roster
    (contracts.seeded_rng; no wall-clock). protected_clients returns only
    graphgel_enrolled properties, bbox-filtered when given."""

    def __init__(self, seed: object,
                 clients: "Optional[list[ClientProperty]]" = None,
                 center: LatLon = LatLon(34.05, -118.70), n: int = 12,
                 spread_km: float = 15.0,
                 items: "Optional[list[dict]]" = None):
        if clients is not None and items is not None:
            raise ValueError("pass clients OR items, not both")
        if clients is not None:
            self._clients = list(clients)
            return
        if items is not None:
            mapped = [c for c in (_item_to_client(it) for it in items)
                      if c is not None]
            mapped.sort(key=lambda c: c.client_id)
            self._clients = mapped
            return
        self._clients = []
        m_per_deg_lon = M_PER_DEG_LAT * max(0.05, math.cos(math.radians(center.lat)))
        for i in range(n):
            rng = seeded_rng(seed, "client", i)
            dlat = rng.uniform(-spread_km, spread_km) * 1000.0 / M_PER_DEG_LAT
            dlon = rng.uniform(-spread_km, spread_km) * 1000.0 / m_per_deg_lon
            client_id = f"mock-client-{i:03d}"
            self._clients.append(ClientProperty(
                client_id=client_id,
                loc=LatLon(center.lat + dlat, center.lon + dlon),
                address=f"{100 + i} Mock Canyon Rd",
                name=f"Mock Client {i}",
                value_usd=_resolve_value_usd(
                    {"home_value_usd": round(rng.uniform(0.6, 8.0), 2) * 1_000_000.0},
                    client_id),
                graphgel_enrolled=True,
                priority_weight=rng.choice((1.0, 1.0, 1.5, 2.0)),
                contact_email=f"client{i:03d}@example.com",
                contact_phone=f"+1555010{i:04d}",
            ))

    def protected_clients(self, bbox: Optional[BBox] = None) -> "list[ClientProperty]":
        """Enrolled clients, optionally restricted to bbox (degrees)."""
        out = [c for c in self._clients if c.graphgel_enrolled]
        if bbox is not None:
            out = [c for c in out if bbox.contains(c.loc)]
        return out


class DynamoClientRegistry:
    """Protected clients from the live wg-consumer-profiles DynamoDB table.

    Filters ``graphgel_enrolled`` truthy AND parseable ``lat``/``lon``
    (profiles without coordinates cannot be threat-assessed and are skipped
    with a log line — fix upstream by geocoding primary_address). See module
    docstring for the ConsumerProfile -> ClientProperty field mapping and the
    Homeowner.fr_graphgel_enrolled canonical source.

    ``scan_fn`` (zero-arg callable -> list of item dicts) is injectable so
    tests never need boto3/AWS; the live path lazily imports boto3 and
    paginates a full table scan, raising a clear RuntimeError if boto3 is
    absent. Region default us-west-2 (the WG stack's region).

    TTL roster cache (Sprint 2, Council-1 OPS-7 scan finding): the monitor
    calls protected_clients(bbox) once per active incident per cycle, which
    was a full table scan EACH time (~70k scans/day at CA scale). Now one
    underlying scan populates the mapped roster for ``ttl_s`` seconds
    (default = contracts.ResponseConfig.registry_ttl_s = 300 s) regardless of
    bbox; bbox filtering happens on the cached set. Cache invalidation is on
    TTL expiry only, plus an explicit ``invalidate()`` for tests/ops.
    ``clock`` is injectable (zero-arg -> seconds float; default
    time.monotonic) so tests never sleep. ``ttl_s=0`` disables caching."""

    def __init__(self, table_name: str = "wg-consumer-profiles-dev",
                 region_name: str = "us-west-2",
                 scan_fn: Optional[Callable[[], list]] = None,
                 ttl_s: Optional[float] = None,
                 clock: Optional[Callable[[], float]] = None):
        self._table_name = table_name
        self._region_name = region_name
        self._scan_fn = scan_fn
        self._ttl_s = float(ttl_s) if ttl_s is not None else ResponseConfig().registry_ttl_s
        self._clock = clock if clock is not None else time.monotonic
        self._roster: "Optional[list[ClientProperty]]" = None
        self._roster_at_s: float = -math.inf

    def invalidate(self) -> None:
        """Drop the cached roster; the next protected_clients() re-scans."""
        self._roster = None
        self._roster_at_s = -math.inf

    def protected_clients(self, bbox: Optional[BBox] = None) -> "list[ClientProperty]":
        """Enrolled clients with coordinates, optionally bbox-filtered.
        Deterministic order (sorted by client_id). At most one underlying
        table scan per TTL window; bbox filters the cached roster."""
        now = self._clock()
        if self._roster is None or (now - self._roster_at_s) >= self._ttl_s:
            items = self._scan_fn() if self._scan_fn is not None else self._boto3_scan()
            roster = [c for c in (_item_to_client(it) for it in items)
                      if c is not None]
            roster.sort(key=lambda c: c.client_id)
            self._roster = roster
            self._roster_at_s = now
        if bbox is not None:
            return [c for c in self._roster if bbox.contains(c.loc)]
        return list(self._roster)

    def _boto3_scan(self) -> list:
        try:
            import boto3
        except ImportError as e:
            raise RuntimeError(
                "DynamoClientRegistry live path requires boto3 — pip install "
                "boto3 (or inject scan_fn for tests/offline use)") from e
        table = boto3.resource("dynamodb",
                               region_name=self._region_name).Table(self._table_name)
        items: list = []
        kwargs: dict = {}
        while True:
            page = table.scan(**kwargs)
            items.extend(page.get("Items") or [])
            lek = page.get("LastEvaluatedKey")
            if not lek:
                return items
            kwargs["ExclusiveStartKey"] = lek


# ---------------------------------------------------------------------------
# PRESET DEMO ROSTER (W6 thin viewer, 2026-09-26; docs/LIVE_WORLD_PLAN.md §3)
# ---------------------------------------------------------------------------
#
# The watch daemon's `--registry mock` seeds 12 homes around the AOI CENTRE —
# at CONUS scale that is 12 random points up to ~1,300 km from southeast
# Kansas, nowhere near our communities, and every live fire stays a bare
# marker. `--registry mock-presets` instead puts N demo homes on EVERY
# simulator preset (wgresponse.simui.LOCATIONS: the five originals, Rancho
# Santa Fe, the six Vail -> Aspen corridor communities, the whole-valley
# Eagle -> Vail tile), placed by the SIMULATOR'S OWN WUI placement
# (simui.place_clients on the preset's cached tile, its city-limits polygon
# and its place_near_road_m driveway rule) with the simulator's seed law
# f"{key}|{seed}" — so preset K's demo homes sit on exactly the cells a
# simulator session of (K, N, seed) would place. Live WFIGS incidents within
# the threat radius of those homes build spread domains; everything else
# stays a marker. Same ClientRegistry seam (protected_clients(bbox)) as
# every registry; the plain `mock` roster is untouched.
#
# DEMO DATA, NOT CLIENTS: ids "<preset>-c<k:03d>", names "<preset name> ·
# demo home k", no contact address (nothing can ever be delivered to one).
# The whole-valley eagle_vail_co tile overlaps the vail_co / beaver_creek_co
# / eagle_co tiles, so a few demo homes from different presets may share a
# cell — harmless for a demo roster, and every id stays unique.

#: Default demo homes per simulator preset (13 presets -> 520 homes).
PRESET_HOMES_DEFAULT = 40

#: Sentinel: resolve the real wgresponse.roads module lazily.
_ROADS_AUTO = object()


def preset_roster(n_per_preset: int = PRESET_HOMES_DEFAULT,
                  seed: object = "watch-presets",
                  keys=None, tile_dir=None, road_dir=None,
                  roads_module=_ROADS_AUTO,
                  notes: Optional[list] = None) -> "list[ClientProperty]":
    """Deterministic demo roster: `n_per_preset` homes on every simulator
    preset (or on `keys`), sorted by preset key then placement order.

    Placement = simui.place_clients(tile, n, seed=f"{key}|{seed}",
    bounds_poly=preset.bounds_poly, road_graph=<preset road graph>,
    near_road_m=preset.place_near_road_m) on simui.load_tile(key) — the
    simulator's own call (a SimSession on the standard zone places the same
    cells). A missing cached tile falls back to the simulator's synthetic
    tile and a missing road graph to the whole WUI band; both are appended
    to `notes` LOUDLY, never silent. `tile_dir` / `road_dir` /
    `roads_module` are test seams (roads_module=None = no road graphs).
    Same (n_per_preset, seed, keys, tiles, graphs) -> identical list."""
    import dataclasses

    from wgresponse import simui    # lazy: heavy module, no import cycle
    if n_per_preset < 1:
        raise ValueError("n_per_preset must be >= 1")
    roads_mod = roads_module
    if roads_mod is _ROADS_AUTO:
        try:
            from wgresponse import roads as roads_mod
        except Exception:        # pragma: no cover - roads ships in-repo
            roads_mod = None
    out: list = []
    for key in sorted(keys if keys is not None else simui.LOCATIONS):
        loc = simui.LOCATIONS[key]
        tile, synthetic = simui.load_tile(key, tile_dir=tile_dir)
        if synthetic and notes is not None:
            notes.append(f"preset roster: {key} tile not cached — homes "
                         f"placed on the SYNTHETIC fallback tile")
        near_road_m = float(getattr(loc, "place_near_road_m", 0.0) or 0.0)
        graph = None
        if near_road_m > 0.0 and roads_mod is not None:
            try:
                graph = roads_mod.load_road_graph(key, road_dir=road_dir)
            except Exception as exc:
                graph = None
                if notes is not None:
                    notes.append(f"preset roster: {key} road graph failed "
                                 f"to load ({exc})")
        if near_road_m > 0.0 and graph is None and notes is not None:
            notes.append(f"preset roster: {key} has the {near_road_m:g} m "
                         f"driveway rule but no road graph — homes drawn "
                         f"from the WHOLE WUI band")
        place_notes: list = []
        placed = simui.place_clients(
            tile, n_per_preset, seed=f"{key}|{seed}", notes=place_notes,
            bounds_poly=getattr(loc, "bounds_poly", ()), road_graph=graph,
            near_road_m=near_road_m)
        if notes is not None:
            # the placement's own record (driveway-rule / city-limits pool
            # sizes, any burnable-adjacent overflow) — never silent
            notes.extend(f"preset roster: {key}: {n}" for n in place_notes)
        for k, c in enumerate(placed, start=1):
            out.append(dataclasses.replace(
                c, client_id=f"{key}-c{k:03d}",
                name=f"{loc.name} · demo home {k}",
                address="", contact_email="", contact_phone=""))
    return out


def preset_client_registry(n_per_preset: int = PRESET_HOMES_DEFAULT,
                           seed: object = "watch-presets",
                           notes: Optional[list] = None,
                           **kwargs) -> MockClientRegistry:
    """The watch daemon's `--registry mock-presets`: a MockClientRegistry
    serving preset_roster(...) — the same protected_clients(bbox) seam, the
    same bbox filter, no new registry semantics."""
    roster = preset_roster(n_per_preset, seed=seed, notes=notes, **kwargs)
    return MockClientRegistry(seed, clients=roster)
