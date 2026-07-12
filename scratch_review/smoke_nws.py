"""Live NWS weather smoke test (READ-ONLY GETs, keyless, User-Agent sent).

NwsWeather.forecast for a Malibu point at 96 h horizon: decode span, field
presence, latency; red_flag_active latency + result.
"""
import sys
import time

sys.path.insert(0, r"C:\Users\colin\wg_response")

from wgresponse.contracts import LatLon, ResponseConfig
from wgresponse.weather import NwsWeather

LOC = LatLon(34.035, -118.69)   # Malibu


def main():
    cfg = ResponseConfig()
    print(f"User-Agent: {cfg.user_agent}")
    wx = NwsWeather(cfg=cfg)

    t0 = time.perf_counter()
    fc = wx.forecast(LOC, 96.0)
    dt = time.perf_counter() - t0
    n = len(fc.samples)
    span_h = (fc.samples[-1].t_s - fc.samples[0].t_s) / 3600.0
    now = time.time()
    lead0_h = (fc.samples[0].t_s - now) / 3600.0
    lead_end_h = (fc.samples[-1].t_s - now) / 3600.0
    print(f"[NWS forecast] {dt:.1f}s  samples={n}  span={span_h:.0f}h "
          f"(from {lead0_h:+.1f}h to {lead_end_h:+.1f}h rel now)  source={fc.source}")
    missing = [s for s in fc.samples
               if s.wind_gust_mps is None or s.wind_dir_deg is None
               or s.rh_pct is None or s.temp_c is None]
    print(f"  samples missing gust/dir/rh/temp: {len(missing)}")
    ws = [s.wind_speed_mps for s in fc.samples]
    gs = [s.wind_gust_mps for s in fc.samples]
    rh = [s.rh_pct for s in fc.samples]
    print(f"  wind m/s: min={min(ws):.1f} max={max(ws):.1f}; "
          f"gust max={max(gs):.1f}; RH: min={min(rh):.0f}% max={max(rh):.0f}%")
    hourly_gaps = [(fc.samples[i+1].t_s - fc.samples[i].t_s)
                   for i in range(len(fc.samples)-1)]
    nonhourly = [g for g in hourly_gaps if abs(g - 3600.0) > 1.0]
    print(f"  non-hourly gaps: {len(nonhourly)}")
    print(f"  72h+ horizon decoded: {'YES' if lead_end_h >= 72.0 else 'NO'}")

    t0 = time.perf_counter()
    rf = wx.red_flag_active(LOC)
    dt = time.perf_counter() - t0
    print(f"[NWS red_flag_active] {dt:.1f}s -> {rf}")


if __name__ == "__main__":
    main()
