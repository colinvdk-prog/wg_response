"""RV2-CODE R6: what the ensemble band ACTUALLY is at runtime.

Claims under test:
  (a) contracts.py ~:954  `ensemble_n: int = 6   # perturbed members (+1 nominal)`
      vs spread.ensemble_arrival treating ensemble_n as TOTAL.
  (b) the unconditional *0.5 at spread.py ~:803-804 makes the realized wind
      band ~+-10% not the advertised +-25%, and direction ~+-24% not +-30 deg.

Method: monkeypatch spread.compute_arrival to LOG every member's actual
(moisture_scale, wind_scale, dir_offset_deg) as ensemble_arrival calls it.
"""
from __future__ import annotations

import sys

from _common import T0, cfg_default, flat_tile  # noqa: F401

from wgresponse import spread
from wgresponse.contracts import LatLon, ResponseConfig, WeatherForecast, WeatherSample


def mock_wx(t0, n=100, ws=8.0, gust=8.0, wdir=270.0):
    return WeatherForecast(loc=LatLon(34.1, -117.1), samples=[
        WeatherSample(t_s=t0 + i * 3600.0, wind_speed_mps=ws, wind_gust_mps=gust,
                      wind_dir_deg=wdir, rh_pct=15.0, temp_c=32.0)
        for i in range(n)], source="rv2")


def instrument(cfg, incident_id="RV2-TEST"):
    calls = []
    real = spread.compute_arrival

    def spy(tile, ignition, wx, t0_s, cfg_, **kw):
        calls.append(dict(member_k=kw.get("member_k"),
                          wind_scale=kw.get("wind_scale", 1.0),
                          moisture_scale=kw.get("moisture_scale", 1.0),
                          dir_offset_deg=kw.get("dir_offset_deg", 0.0)))
        return real(tile, ignition, wx, t0_s, cfg_, **kw)

    spread.compute_arrival = spy
    try:
        tile = flat_tile(34.20, -117.20, 90.0, 12, 12, 102)
        spread.ensemble_arrival(tile, [LatLon(34.195, -117.195)], mock_wx(T0),
                                T0, cfg, incident_id=incident_id)
    finally:
        spread.compute_arrival = real
    return calls


def main():
    cfg = cfg_default()
    print(f"cfg.ensemble_n            = {cfg.ensemble_n}")
    print(f"cfg.wind_perturb_frac     = {cfg.wind_perturb_frac}")
    print(f"cfg.moisture_perturb_frac = {cfg.moisture_perturb_frac}")
    print(f"cfg.dir_perturb_deg       = {cfg.dir_perturb_deg}")
    print(f"cfg.horizon_h             = {cfg.horizon_h}")
    print(f"  -> dir_lead = min(1,(horizon_h*0.5)/48) = "
          f"{min(1.0, (cfg.horizon_h*0.5)/48.0):.4f}")

    calls = instrument(cfg)
    print(f"\nMEMBERS ACTUALLY RUN: {len(calls)}  "
          f"(contracts comment implies {cfg.ensemble_n}+1 = {cfg.ensemble_n+1})")
    print(f"{'k':>3s} {'wind_scale':>11s} {'moist_scale':>12s} {'dir_off_deg':>12s}")
    for c in calls:
        print(f"{c['member_k']!s:>3s} {c['wind_scale']:11.6f} "
              f"{c['moisture_scale']:12.6f} {c['dir_offset_deg']:12.4f}")

    ws = [c["wind_scale"] for c in calls]
    ms = [c["moisture_scale"] for c in calls]
    ds = [c["dir_offset_deg"] for c in calls]
    print(f"\nREALIZED BANDS over all {len(calls)} members:")
    print(f"  wind_scale     [{min(ws):.4f}, {max(ws):.4f}]  "
          f"= {(min(ws)-1)*100:+.1f}% .. {(max(ws)-1)*100:+.1f}%   "
          f"(advertised +-{cfg.wind_perturb_frac*100:.0f}%)")
    print(f"  moisture_scale [{min(ms):.4f}, {max(ms):.4f}]  "
          f"= {(min(ms)-1)*100:+.1f}% .. {(max(ms)-1)*100:+.1f}%   "
          f"(advertised +-{cfg.moisture_perturb_frac*100:.0f}%)")
    print(f"  dir_offset     [{min(ds):.2f}, {max(ds):.2f}] deg   "
          f"(advertised +-{cfg.dir_perturb_deg:.0f} deg)")

    # decomposition of the shortfall
    draws = spread._stratified_member_draws("RV2-TEST", cfg.ensemble_n - 1)
    us = sorted(d[0] for d in draws)
    print(f"\nstratum midpoints for m={cfg.ensemble_n-1} perturbed members: "
          f"{[f'{u:+.2f}' for u in us]}  (max |u| = {max(abs(u) for u in us):.2f})")
    print(f"shortfall = max|u| ({max(abs(u) for u in us):.2f}) "
          f"x the 0.5 damping = {max(abs(u) for u in us)*0.5:.2f} "
          f"of the advertised fraction")

    # is 0.5 the *only* damping, or does m matter?  Show n=6 vs n=11 vs n=21.
    print("\n{:>4s} {:>8s} {:>12s} {:>12s}".format("n", "members", "wind band %", "dir band deg"))
    for n in (2, 3, 6, 11, 21, 41):
        c2 = cfg_default(ensemble_n=n)
        cl = instrument(c2)
        w = [x["wind_scale"] for x in cl]
        d = [x["dir_offset_deg"] for x in cl]
        print(f"{n:>4d} {len(cl):>8d} {(max(w)-1)*100:>11.2f}% {max(d):>12.2f}")

    # what does the nominal-bounded p10 do to a band this narrow?
    print("\nNOTE: p10 = min(Hazen p10 over 6 members, nominal). With the band")
    print("      halved, the Hazen p10 sits much closer to nominal, so the")
    print("      clamp binds more often and p10 -> nominal (band collapse).")


if __name__ == "__main__":
    sys.exit(main())
