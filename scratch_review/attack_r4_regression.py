"""attack_r4_regression.py — FOUNDER ROUND 4 item 5 (REGRESSION).

Headline SIM regressions re-checked directly here; the named tests/ pins
(byte-identity B1/B3, frozen-fire persisted field, scrub byte-identity, the
re-pinned served-3->9 policy test, and every prior r3 attack suite) are run
from the shell against the SAME frozen snapshot — see the verifier report.

  * flagship canyon_wind_event 96 h: 12/12 serviced, 0 unserved, 0 violations;
  * slow_creeper 48 h: 0 violations;
  * the 200 m ember default really gels MORE developed homes than ember-off in a
    controlled Malibu control run (the legitimacy of the served-3->9 re-pin:
    strictly-more direction, from first principles).

Run:  py -3 -m pytest scratch_review/attack_r4_regression.py -q -s
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import sim                                             # noqa: E402
from attack_r4_common import TILE_DIR, tile_present, skip_loud, rc_of   # noqa: E402

FLAGSHIP = {}


def test_flagship_canyon_wind_event_12_of_12_zero_violations(tmp_path):
    r = sim.run(sim.build_scenario("canyon_wind_event", seed="flagship"),
                hours=96, out_dir=str(tmp_path))
    n_clients = len({k.split("|")[0] for k in r.final_states})
    FLAGSHIP["canyon_wind_event"] = (len(r.serviced), n_clients,
                                     len(r.unserved), len(r.violations),
                                     r.min_margin_h)
    assert r.violations == [], f"flagship had {len(r.violations)} violations"
    assert len(r.serviced) == 12 and n_clients == 12, \
        f"flagship served {len(r.serviced)}/{n_clients}, expected 12/12"
    assert len(r.unserved) == 0
    assert r.min_margin_h is not None and r.min_margin_h > 0.0


def test_slow_creeper_clean(tmp_path):
    r = sim.run(sim.build_scenario("slow_creeper", seed="flagship"),
                hours=48, out_dir=str(tmp_path))
    FLAGSHIP["slow_creeper"] = (len(r.serviced),
                                len({k.split("|")[0] for k in r.final_states}),
                                len(r.unserved), len(r.violations), r.min_margin_h)
    assert r.violations == [], f"slow_creeper had {len(r.violations)} violations"
    assert r.min_margin_h is None or r.min_margin_h > 0.0


def _burnable(code):
    return 101 <= int(round(code)) <= 204


@pytest.mark.skipif(not tile_present("malibu_ca"),
                    reason=skip_loud("malibu_ca", "tile"))
def test_ember_default_gels_more_developed_homes_than_off():
    """Legitimacy of the served-3->9 re-pin (strictly-more direction): with the
    200 m ember radius the control run gels MORE developed-cell homes than with
    the radius OFF (0 m), because fire-adjacent developed homes now get finite
    ETAs -> jobs -> gel. Same seed + identical script; the ONLY difference is
    ember_threat_radius_m."""
    from wgresponse.simui import SimSession
    from attack_sim_common import pick_ignition

    def run(radius):
        s = SimSession("malibu_ca", 60, 3, seed="repin",
                       cfg_overrides={"ember_threat_radius_m": radius},
                       fire_zone="standard", tile_dir=TILE_DIR)
        p, _ = pick_ignition(s, "malibu_ca", cluster=True)
        s.ignite(p.lat, p.lon, intensity="high")
        spec = s._tile.spec
        dev_ids = set()
        for cl in s._clients:
            rc = rc_of(spec, cl.loc.lat, cl.loc.lon)
            if rc is not None and not _burnable(s._tile.fuel.get(*rc)):
                dev_ids.add(cl.client_id)
        st = None
        for _ in range(60):
            st = s.tick()
        served = {sv["client_id"] for sv in st["serviced"]}
        dev_served = served & dev_ids
        return st["scoreboard"]["served"], len(dev_served), st["scoreboard"]["violations"]

    served_on, dev_on, v_on = run(200.0)
    served_off, dev_off, v_off = run(0.0)
    assert v_on == 0 and v_off == 0
    print(f"\n[repin] ember 200 m: served={served_on} (developed {dev_on}); "
          f"ember 0 m: served={served_off} (developed {dev_off})")
    # the whole point of law 6: with ember ON, strictly MORE developed homes are
    # gel-protected (the 3 -> 9 direction), and never fewer total.
    assert dev_on > dev_off, (dev_on, dev_off,
                              "ember default did NOT gel more developed homes")
    assert served_on >= served_off


def test_zzz_regression_summary():
    print("\n===== FLAGSHIP / CREEPER (round-4 tree) =====")
    for name, (srv, ncl, uns, viol, mm) in FLAGSHIP.items():
        mm_s = "None" if mm is None else f"{mm:.2f}"
        print(f"  {name:<20} served/clients={srv}/{ncl} unserved={uns} "
              f"violations={viol} min_margin_h={mm_s}")
