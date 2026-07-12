"""Shared helpers for the DISPATCH & ALERT QUALITY review experiments (throwaway).

Run from repo root:  py -3 scratch_review/da_exp1_optgap.py  etc.
Deterministic: all randomness via contracts.seeded_rng; fixed epochs; no wall clock.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse.contracts import (
    UNREACHED, ClientProperty, Crew, CrewBeacon, CrewStatus, DispatchJob,
    EnsembleArrival, GridSpec, LatLon, Raster, ResponseConfig, SafeZone,
    ThreatEta, seeded_rng,
)

T0 = 1_780_000_000.0

_M_PER_DEG_LAT = 111_320.0
_EARTH_R_M = 6_371_000.0


def destination(origin: LatLon, bearing_deg: float, dist_m: float) -> LatLon:
    br = math.radians(bearing_deg)
    la1, lo1 = math.radians(origin.lat), math.radians(origin.lon)
    ang = dist_m / _EARTH_R_M
    la2 = math.asin(math.sin(la1) * math.cos(ang)
                    + math.cos(la1) * math.sin(ang) * math.cos(br))
    lo2 = lo1 + math.atan2(math.sin(br) * math.sin(ang) * math.cos(la1),
                           math.cos(ang) - math.sin(la1) * math.sin(la2))
    return LatLon(math.degrees(la2), math.degrees(lo2))


def all_safe_ensemble(incident_id: str = "I-SAFE") -> EnsembleArrival:
    """Ensemble whose rasters sit 100+ km away from any experiment geometry and
    are all UNREACHED -> every corridor / egress check is trivially safe.
    Lets the optimality experiments isolate the routing/deadline math."""
    spec = GridSpec(north_lat=44.0, west_lon=-100.0, cell_m=1000.0, nrows=2, ncols=2)
    inf_r = Raster(spec, [UNREACHED] * 4)
    return EnsembleArrival(spec=spec, members=[inf_r],
                           p10=Raster(spec, [UNREACHED] * 4),
                           p50=Raster(spec, [UNREACHED] * 4),
                           p90=Raster(spec, [UNREACHED] * 4),
                           t0_s=T0, incident_id=incident_id)


def uniform_arrival_ensemble(center: LatLon, half_km: float, cell_m: float,
                             arrival_s: float, incident_id: str) -> EnsembleArrival:
    """Ensemble whose p10/p50/p90 rasters cover a (2*half_km)^2 box centred on
    `center` with a UNIFORM arrival time -- a crude 'the fire is here at time T'
    footprint for corridor-check experiments."""
    half_m = half_km * 1000.0
    dlat = half_m / _M_PER_DEG_LAT
    dlon = half_m / (_M_PER_DEG_LAT * math.cos(math.radians(center.lat)))
    nrows = max(2, int(math.ceil(2 * half_m / cell_m)))
    ncols = nrows
    spec = GridSpec(north_lat=center.lat + dlat, west_lon=center.lon - dlon,
                    cell_m=cell_m, nrows=nrows, ncols=ncols)
    n = nrows * ncols
    r = Raster(spec, [arrival_s] * n)
    return EnsembleArrival(spec=spec, members=[r],
                           p10=Raster(spec, [arrival_s] * n),
                           p50=Raster(spec, [arrival_s] * n),
                           p90=Raster(spec, [arrival_s] * n),
                           t0_s=T0, incident_id=incident_id)


def mk_job(client_id: str, loc: LatLon, value_usd: float, priority: float,
           incident_id: str, p10_s: float, p50_s: float) -> DispatchJob:
    return DispatchJob(
        client=ClientProperty(client_id=client_id, loc=loc, name=client_id,
                              value_usd=value_usd, priority_weight=priority),
        incident_id=incident_id,
        eta=ThreatEta(eta_p10_s=p10_s, eta_p50_s=p50_s,
                      eta_p90_s=p50_s + (p50_s - p10_s)))


def mk_crew(crew_id: str, loc: LatLon, now_s: float = T0) -> Crew:
    return Crew(crew_id=crew_id, name=crew_id, status=CrewStatus.AVAILABLE,
                last_beacon=CrewBeacon(crew_id=crew_id, loc=loc, t_s=now_s,
                                       status=CrewStatus.AVAILABLE),
                home_base=loc)
