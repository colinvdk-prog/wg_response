"""Tests for wgresponse.clients — mock registry + Dynamo mapping via injected scan.

Sprint 2 C3e additions (Council-1 OPS-7, docs/review/ops_readiness_review.md
§3.7): value-field preference order (home_value_usd > legacy home_value >
$1M default WITH warning) verified against the real ConsumerProfile schema
(firerisk persistence/models.py:571-572), the registry TTL roster cache
(one scan per TTL window, bbox on the cached set, injectable clock,
explicit invalidate), and Mock/Dynamo mapping parity. The OPS-7 finding was
a static code-read of the review lane (no scratch_review/ script existed
for it); the per-incident-scan pattern it targets is the
registry.protected_clients(bbox)-per-incident loop in threat.py."""
import logging
from decimal import Decimal

import pytest

from wgresponse.clients import (DEFAULT_VALUE_USD, DynamoClientRegistry,
                                MockClientRegistry, _resolve_value_usd, _truthy)
from wgresponse.contracts import BBox, ClientProperty, LatLon, ResponseConfig

CENTER = LatLon(34.05, -118.70)


class FakeClock:
    """Injectable deterministic clock (zero-arg callable, seconds)."""

    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class CountingScan:
    """scan_fn wrapper that counts underlying table scans."""

    def __init__(self, items):
        self.items = items
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return list(self.items)


class TestMockClientRegistry:
    def test_deterministic_same_seed(self):
        a = MockClientRegistry("s1").protected_clients()
        b = MockClientRegistry("s1").protected_clients()
        assert [(c.client_id, c.loc, c.value_usd, c.priority_weight) for c in a] == \
               [(c.client_id, c.loc, c.value_usd, c.priority_weight) for c in b]

    def test_different_seed_differs(self):
        a = MockClientRegistry("s1").protected_clients()
        b = MockClientRegistry("s2").protected_clients()
        assert [c.loc for c in a] != [c.loc for c in b]

    def test_count_and_enrollment(self):
        clients = MockClientRegistry("s1", n=7).protected_clients()
        assert len(clients) == 7
        assert all(c.graphgel_enrolled for c in clients)
        assert all(c.priority_weight >= 1.0 for c in clients)
        assert all(c.value_usd > 0 for c in clients)
        assert len({c.client_id for c in clients}) == 7      # unique ids

    def test_clients_scatter_within_spread(self):
        reg = MockClientRegistry("s1", center=CENTER, n=20, spread_km=10.0)
        for c in reg.protected_clients():
            assert abs(c.loc.lat - CENTER.lat) <= 10.0 * 1000.0 / 111_320.0 + 1e-9
            assert abs(c.loc.lon - CENTER.lon) <= 0.15

    def test_bbox_filter(self):
        reg = MockClientRegistry("s1", center=CENTER, n=12, spread_km=10.0)
        wide = BBox(CENTER.lon - 0.5, CENTER.lat - 0.5,
                    CENTER.lon + 0.5, CENTER.lat + 0.5)
        nowhere = BBox(10.0, 10.0, 11.0, 11.0)
        assert len(reg.protected_clients(wide)) == 12
        assert reg.protected_clients(nowhere) == []

    def test_explicit_list_filters_unenrolled(self):
        enrolled = ClientProperty(client_id="a", loc=LatLon(34.0, -118.0),
                                  graphgel_enrolled=True)
        lapsed = ClientProperty(client_id="b", loc=LatLon(34.0, -118.0),
                                graphgel_enrolled=False)
        reg = MockClientRegistry("s", clients=[enrolled, lapsed])
        assert reg.protected_clients() == [enrolled]


DYNAMO_ITEMS = [
    {"email": "a@x.com", "graphgel_enrolled": True,
     "lat": Decimal("34.05"), "lon": Decimal("-118.70"),
     "primary_address": "1 A St, Malibu CA", "name": "Alice",
     # REAL ConsumerProfile field (firerisk persistence/models.py:571)
     "home_value_usd": Decimal("2500000"), "phone": "+15550001111"},
    {"email": "b@x.com", "graphgel_enrolled": "true",
     "lat": 34.06, "lon": -118.71},
    {"email": "c@x.com", "graphgel_enrolled": False,      # not enrolled
     "lat": 34.00, "lon": -118.00},
    {"email": "d@x.com", "graphgel_enrolled": True},       # missing lat/lon
    {"email": "e@x.com", "graphgel_enrolled": True,        # unparseable coords
     "lat": "not-a-number", "lon": -118.0},
    {"email": "f@x.com"},                                  # flag absent entirely
]


class TestDynamoClientRegistry:
    def _reg(self, items=DYNAMO_ITEMS):
        return DynamoClientRegistry(scan_fn=lambda: list(items))

    def test_filters_enrolled_with_coordinates(self):
        clients = self._reg().protected_clients()
        assert [c.client_id for c in clients] == ["a@x.com", "b@x.com"]

    def test_field_mapping_from_consumer_profile(self):
        a = self._reg().protected_clients()[0]
        assert a.client_id == "a@x.com"
        assert a.loc == LatLon(34.05, -118.70)               # Decimal -> float
        assert a.address == "1 A St, Malibu CA"
        assert a.name == "Alice"
        assert a.value_usd == 2500000.0
        assert a.graphgel_enrolled is True
        assert a.contact_email == "a@x.com"
        assert a.contact_phone == "+15550001111"
        assert a.priority_weight == 1.0

    def test_defaults_when_optional_fields_absent(self):
        b = self._reg().protected_clients()[1]
        assert b.value_usd == 1_000_000.0
        assert b.address == ""
        assert b.priority_weight == 1.0

    def test_string_boolean_enrollment_accepted(self):
        items = [{"email": "s@x.com", "graphgel_enrolled": "True",
                  "lat": 34.0, "lon": -118.0}]
        assert len(self._reg(items).protected_clients()) == 1

    def test_bbox_filter(self):
        inside = BBox(-118.8, 34.0, -118.6, 34.1)
        clients = self._reg().protected_clients(inside)
        assert [c.client_id for c in clients] == ["a@x.com", "b@x.com"]
        elsewhere = BBox(-117.0, 33.0, -116.0, 33.5)
        assert self._reg().protected_clients(elsewhere) == []

    def test_output_sorted_by_client_id(self):
        items = [
            {"email": "z@x.com", "graphgel_enrolled": True, "lat": 1.0, "lon": 1.0},
            {"email": "a@x.com", "graphgel_enrolled": True, "lat": 2.0, "lon": 2.0},
        ]
        ids = [c.client_id for c in self._reg(items).protected_clients()]
        assert ids == ["a@x.com", "z@x.com"]

    def test_scan_fn_injection_avoids_boto3(self):
        # No boto3 import may happen when scan_fn is provided.
        reg = DynamoClientRegistry(table_name="wg-consumer-profiles-dev",
                                   scan_fn=lambda: [])
        assert reg.protected_clients() == []

    def test_missing_email_skipped(self):
        items = [{"graphgel_enrolled": True, "lat": 34.0, "lon": -118.0}]
        assert self._reg(items).protected_clients() == []


class TestValueFieldMapping:
    """Sprint 2 C3e / Council-1 OPS-7 (ops_readiness_review.md §3.7): the real
    wg-consumer-profiles value field is ``home_value_usd`` (firerisk
    persistence/models.py:571; consumer/profile.py:289 writes it). The old
    code read ``home_value``/``estimated_home_value`` so every live client
    silently got the $1M default that drives dispatch triage. Preference
    order under test: home_value_usd > legacy home_value > default+WARNING."""

    def _one(self, extra: dict) -> ClientProperty:
        item = {"email": "v@x.com", "graphgel_enrolled": True,
                "lat": 34.0, "lon": -118.0}
        item.update(extra)
        reg = DynamoClientRegistry(scan_fn=lambda: [item])
        return reg.protected_clients()[0]

    def test_prefers_home_value_usd_over_legacy(self, caplog):
        with caplog.at_level(logging.WARNING, logger="wgresponse.clients"):
            c = self._one({"home_value_usd": Decimal("3200000"),
                           "home_value": Decimal("2500000")})
        assert c.value_usd == 3_200_000.0
        assert caplog.records == []                      # no silent-default warning

    def test_legacy_home_value_fallback_no_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="wgresponse.clients"):
            c = self._one({"home_value": Decimal("2500000")})
        assert c.value_usd == 2_500_000.0
        assert caplog.records == []

    def test_missing_both_fields_defaults_with_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="wgresponse.clients"):
            c = self._one({})
        assert c.value_usd == DEFAULT_VALUE_USD == 1_000_000.0
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        msg = warnings[0].getMessage()
        assert "v@x.com" in msg                          # names the profile
        assert "home_value_usd" in msg                   # says what to fix

    def test_home_value_estimated_bool_flag_is_not_a_value(self, caplog):
        # models.py:572: home_value_estimated is a bool provenance flag. It
        # must never be parsed as money (bool True would float() to 1.0 USD).
        with caplog.at_level(logging.WARNING, logger="wgresponse.clients"):
            c = self._one({"home_value_estimated": True})
        assert c.value_usd == DEFAULT_VALUE_USD
        assert len(caplog.records) == 1                  # loud, not silent

    def test_garbage_usd_field_falls_back_to_legacy(self):
        c = self._one({"home_value_usd": "not-a-number",
                       "home_value": Decimal("1750000")})
        assert c.value_usd == 1_750_000.0

    def test_resolver_direct(self):
        assert _resolve_value_usd({"home_value_usd": 2e6}) == 2_000_000.0
        assert _resolve_value_usd({"home_value": 3e6}) == 3_000_000.0
        assert _resolve_value_usd({}) == DEFAULT_VALUE_USD


class TestRegistryTtlCache:
    """Sprint 2 C3e / Council-1 OPS-7 scan finding (ops_readiness_review.md
    §3.7): the monitor calls protected_clients(bbox) once per active incident
    per cycle — previously a full table scan EACH call (~70k scans/day at CA
    scale). The registry must do ONE underlying scan per registry_ttl_s
    window regardless of bbox, filter bbox on the cached set, re-scan on TTL
    expiry, and support explicit invalidate()."""

    INSIDE = BBox(-118.8, 34.0, -118.6, 34.1)
    ELSEWHERE = BBox(-117.0, 33.0, -116.0, 33.5)

    def _reg(self, items=DYNAMO_ITEMS, **kw):
        scan = CountingScan(items)
        return DynamoClientRegistry(scan_fn=scan, **kw), scan

    def test_two_calls_one_scan(self):
        reg, scan = self._reg(clock=FakeClock())
        a = reg.protected_clients()
        b = reg.protected_clients()
        assert scan.calls == 1
        assert a == b

    def test_bbox_filter_applied_on_cached_set(self):
        reg, scan = self._reg(clock=FakeClock())
        assert [c.client_id for c in reg.protected_clients()] == ["a@x.com", "b@x.com"]
        assert [c.client_id for c in reg.protected_clients(self.INSIDE)] == \
               ["a@x.com", "b@x.com"]
        assert reg.protected_clients(self.ELSEWHERE) == []
        assert scan.calls == 1                           # bbox never re-scans

    def test_ttl_expiry_rescans_and_picks_up_changes(self):
        clock = FakeClock(0.0)
        items = [{"email": "old@x.com", "graphgel_enrolled": True,
                  "lat": 34.0, "lon": -118.0, "home_value_usd": 2e6}]
        scan = CountingScan(items)
        reg = DynamoClientRegistry(scan_fn=scan, ttl_s=300.0, clock=clock)
        reg.protected_clients()
        scan.items = [{"email": "new@x.com", "graphgel_enrolled": True,
                       "lat": 34.0, "lon": -118.0, "home_value_usd": 2e6}]
        clock.advance(299.9)                             # still inside window
        assert [c.client_id for c in reg.protected_clients()] == ["old@x.com"]
        assert scan.calls == 1
        clock.advance(0.1)                               # age == ttl -> expired
        assert [c.client_id for c in reg.protected_clients()] == ["new@x.com"]
        assert scan.calls == 2

    def test_default_ttl_matches_config_knob(self):
        # cfg.registry_ttl_s is the single source of truth for the default.
        clock = FakeClock(0.0)
        reg, scan = self._reg(clock=clock)               # no ttl_s passed
        reg.protected_clients()
        clock.advance(ResponseConfig().registry_ttl_s - 0.001)
        reg.protected_clients()
        assert scan.calls == 1
        clock.advance(0.001)
        reg.protected_clients()
        assert scan.calls == 2

    def test_invalidate_forces_rescan(self):
        reg, scan = self._reg(clock=FakeClock())
        reg.protected_clients()
        reg.invalidate()
        reg.protected_clients()
        assert scan.calls == 2

    def test_ttl_zero_disables_cache(self):
        reg, scan = self._reg(ttl_s=0.0, clock=FakeClock())
        reg.protected_clients()
        reg.protected_clients()
        assert scan.calls == 2

    def test_returned_list_is_a_copy_of_the_cache(self):
        reg, scan = self._reg(clock=FakeClock())
        first = reg.protected_clients()
        first.clear()                                    # caller mutation
        assert [c.client_id for c in reg.protected_clients()] == \
               ["a@x.com", "b@x.com"]
        assert scan.calls == 1


class TestMockRegistryParity:
    """Sprint 2 C3e: MockClientRegistry must exercise the SAME ConsumerProfile
    mapping path as DynamoClientRegistry (shared _item_to_client /
    _resolve_value_usd), so simulations can't drift from live semantics."""

    def test_items_roster_identical_to_dynamo(self):
        dyn = DynamoClientRegistry(scan_fn=lambda: list(DYNAMO_ITEMS))
        mock = MockClientRegistry("parity", items=list(DYNAMO_ITEMS))
        assert mock.protected_clients() == dyn.protected_clients()

    def test_mock_items_value_preference_order(self):
        items = [{"email": "p@x.com", "graphgel_enrolled": True,
                  "lat": 34.0, "lon": -118.0,
                  "home_value_usd": Decimal("3200000"),
                  "home_value": Decimal("2500000")}]
        (c,) = MockClientRegistry("parity", items=items).protected_clients()
        assert c.value_usd == 3_200_000.0

    def test_mock_items_default_warns(self, caplog):
        items = [{"email": "q@x.com", "graphgel_enrolled": True,
                  "lat": 34.0, "lon": -118.0}]
        with caplog.at_level(logging.WARNING, logger="wgresponse.clients"):
            (c,) = MockClientRegistry("parity", items=items).protected_clients()
        assert c.value_usd == DEFAULT_VALUE_USD
        assert any("q@x.com" in r.getMessage() for r in caplog.records)

    def test_mock_items_bbox_filter(self):
        mock = MockClientRegistry("parity", items=list(DYNAMO_ITEMS))
        assert mock.protected_clients(BBox(-117.0, 33.0, -116.0, 33.5)) == []

    def test_generated_clients_route_through_shared_resolver(self, monkeypatch):
        import wgresponse.clients as clients_mod
        calls = []
        real = clients_mod._resolve_value_usd

        def spy(item, client_id=""):
            calls.append((dict(item), client_id))
            return real(item, client_id)

        monkeypatch.setattr(clients_mod, "_resolve_value_usd", spy)
        roster = MockClientRegistry("s1", n=5).protected_clients()
        assert len(calls) == 5                           # every sim client
        assert all("home_value_usd" in item for item, _ in calls)  # real schema
        assert all(c.value_usd > 0 for c in roster)

    def test_clients_and_items_mutually_exclusive(self):
        with pytest.raises(ValueError):
            MockClientRegistry("s", clients=[], items=[])


class TestTruthy:
    @pytest.mark.parametrize("v,expect", [
        (True, True), (False, False), (None, False),
        ("true", True), ("True", True), ("1", True), ("yes", True),
        ("false", False), ("", False), ("no", False),
        (1, True), (0, False), (Decimal(1), True), (Decimal(0), False),
        ([], False),
    ])
    def test_values(self, v, expect):
        assert _truthy(v) is expect
