"""Address suggestions and reverse geocoding using OpenStreetMap Nominatim (proxied + cached).

Nominatim's usage policy requires a descriptive User-Agent, at most ~1 request/s and caching,
so the frontend debounces input and the backend caches responses.
"""

from __future__ import annotations

import logging
import threading
import time
import unicodedata
from collections import OrderedDict
from typing import Any

import httpx

from .config import get_settings
from .cities import City, city_profile

log = logging.getLogger(__name__)


class _TTLCache:
    def __init__(self, maxsize: int = 1024, ttl: float = 3600) -> None:
        self.maxsize, self.ttl = maxsize, ttl
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any:
        with self._lock:
            item = self._data.get(key)
            if item is None or time.monotonic() - item[0] > self.ttl:
                return None
            self._data.move_to_end(key)
            return item[1]

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)


_cache = _TTLCache()
_rate_lock = threading.Lock()
_last_request = 0.0


def _get(path: str, params: dict[str, Any]) -> Any:
    global _last_request
    settings = get_settings()
    key = (path, tuple(sorted(params.items())))
    cached = _cache.get(key)
    if cached is not None:
        return cached
    with _rate_lock:  # keep within Nominatim's 1 request/second policy
        wait = 1.0 - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
    with httpx.Client(timeout=10, headers={"User-Agent": settings.user_agent}) as client:
        response = client.get(f"{settings.nominatim_url.rstrip('/')}/{path}", params=params)
        response.raise_for_status()
        data = response.json()
    _cache.set(key, data)
    return data


def _format(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": item.get("display_name", ""),
        "lat": float(item["lat"]),
        "lon": float(item["lon"]),
        "type": item.get("type"),
    }


def search(query: str, limit: int = 6, city: City = City.GDANSK) -> list[dict[str, Any]]:
    settings = get_settings()
    profile = city_profile(city)
    params: dict[str, Any] = {
        "q": query, "format": "jsonv2", "addressdetails": 1, "accept-language": "pl", "limit": 40,
    }
    viewbox = settings.geocode_viewbox if city == City.GDANSK else profile.viewbox
    if viewbox:
        params["viewbox"] = viewbox
        params["bounded"] = 1
    if settings.geocode_countrycodes:
        params["countrycodes"] = settings.geocode_countrycodes
    results = _get("search", params)
    if not isinstance(results, list):
        raise ValueError("Invalid geocoding search response")
    expected_city = unicodedata.normalize("NFC", profile.name).casefold()
    south, west, north, east = profile.bounds
    suggestions = []
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("Invalid geocoding search result")
        address = item.get("address")
        if not isinstance(address, dict):
            log.warning("Ignoring %s geocoding result without address details", city)
            continue
        locality = address.get("city") or address.get("town") or address.get("village")
        if not isinstance(locality, str):
            log.warning("Ignoring %s geocoding result without a locality", city)
            continue
        if unicodedata.normalize("NFC", locality).strip().casefold() != expected_city:
            continue
        suggestion = _format(item)
        if south <= suggestion["lat"] <= north and west <= suggestion["lon"] <= east:
            suggestions.append(suggestion)
            if len(suggestions) == limit:
                break
    return suggestions


def reverse(lat: float, lon: float) -> dict[str, Any] | None:
    data = _get("reverse", {"lat": round(lat, 6), "lon": round(lon, 6), "format": "jsonv2", "zoom": 18})
    if not data or "lat" not in data:
        return None
    return _format(data)
