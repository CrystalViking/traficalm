"""Walking geometry/durations from an OSRM foot-profile server (OpenStreetMap data).

Falls back to a straight line with a detour factor when the router is disabled or unavailable.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from functools import lru_cache

import httpx

from .config import get_settings
from .geo import haversine_m

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WalkPath:
    distance_m: float
    duration_s: int
    geometry: tuple[tuple[float, float], ...]
    routed: bool


def estimate_walk(lat1: float, lon1: float, lat2: float, lon2: float) -> WalkPath:
    s = get_settings()
    dist = haversine_m(lat1, lon1, lat2, lon2) * s.walk_detour_factor
    return WalkPath(dist, int(round(dist / s.walk_speed_mps)), ((lat1, lon1), (lat2, lon2)), False)


_down_until = 0.0


@lru_cache(maxsize=4096)
def _osrm_cached(lat1: float, lon1: float, lat2: float, lon2: float) -> WalkPath:
    path = _osrm(lat1, lon1, lat2, lon2)
    if path is None:
        raise LookupError("no walking route")  # exceptions are not cached by lru_cache
    return path


def _osrm(lat1: float, lon1: float, lat2: float, lon2: float) -> WalkPath | None:
    global _down_until
    if time.monotonic() < _down_until:
        return None
    s = get_settings()
    url = f"{s.osrm_foot_url.rstrip('/')}/route/v1/foot/{lon1},{lat1};{lon2},{lat2}"
    try:
        with httpx.Client(timeout=5, headers={"User-Agent": s.user_agent}) as client:
            response = client.get(url, params={"overview": "full", "geometries": "geojson"})
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.info("Walking router unavailable: %s", exc)
        _down_until = time.monotonic() + 60  # back off instead of slowing every request
        return None
    routes = data.get("routes") or []
    if data.get("code") != "Ok" or not routes:
        return None
    route = routes[0]
    coords = tuple((lat, lon) for lon, lat in route["geometry"]["coordinates"])
    distance = float(route["distance"])
    # Use our own walking speed (the public OSRM foot profile is ~5 km/h)
    return WalkPath(distance, int(round(distance / s.walk_speed_mps)), coords, True)


def route_walk(lat1: float, lon1: float, lat2: float, lon2: float) -> WalkPath:
    if haversine_m(lat1, lon1, lat2, lon2) < 15:
        return WalkPath(0.0, 0, ((lat1, lon1), (lat2, lon2)), False)
    if get_settings().walking_router_enabled:
        try:
            return _osrm_cached(round(lat1, 6), round(lon1, 6), round(lat2, 6), round(lon2, 6))
        except LookupError:
            pass
    return estimate_walk(lat1, lon1, lat2, lon2)
