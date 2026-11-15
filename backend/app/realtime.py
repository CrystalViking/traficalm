"""GTFS-Realtime client (ZTM Gdańsk trip updates and vehicle positions) with short-lived caching."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date

import httpx
from google.transit import gtfs_realtime_pb2
from google.protobuf.message import DecodeError

from .config import get_settings
from .cities import City, city_profile

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class StopTimeUpdate:
    stop_sequence: int | None
    stop_id: str | None
    arrival_delay: int | None
    departure_delay: int | None
    arrival_time: int | None
    departure_time: int | None
    skipped: bool = False


@dataclass(frozen=True)
class TripUpdate:
    trip_id: str
    route_id: str | None
    trip_delay: int | None
    updates: tuple[StopTimeUpdate, ...]
    timestamp: int | None
    service_date: date | None = None


@dataclass(frozen=True)
class VehiclePosition:
    trip_id: str | None
    route_id: str | None
    vehicle_id: str | None
    label: str | None
    lat: float
    lon: float
    bearing: float | None
    speed: float | None
    timestamp: int | None
    service_date: date | None = None


def _service_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        log.warning("GTFS-RT has an invalid service date: %s", value)
        return None


def parse_trip_updates(data: bytes) -> dict[str, TripUpdate]:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(data)
    result: dict[str, TripUpdate] = {}
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        trip_id = tu.trip.trip_id
        if not trip_id:
            continue
        service_date = _service_date(tu.trip.start_date)
        if tu.trip.start_date and service_date is None:
            continue
        updates = []
        for stu in tu.stop_time_update:
            arr = stu.arrival if stu.HasField("arrival") else None
            dep = stu.departure if stu.HasField("departure") else None
            updates.append(
                StopTimeUpdate(
                    stop_sequence=stu.stop_sequence if stu.HasField("stop_sequence") else None,
                    stop_id=stu.stop_id or None,
                    arrival_delay=arr.delay if arr is not None and arr.HasField("delay") else None,
                    departure_delay=dep.delay if dep is not None and dep.HasField("delay") else None,
                    arrival_time=arr.time if arr is not None and arr.HasField("time") else None,
                    departure_time=dep.time if dep is not None and dep.HasField("time") else None,
                    skipped=stu.schedule_relationship == gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.SKIPPED,
                )
            )
        result[trip_id] = TripUpdate(
            trip_id=trip_id,
            route_id=tu.trip.route_id or None,
            trip_delay=tu.delay if tu.HasField("delay") else None,
            updates=tuple(updates),
            timestamp=tu.timestamp if tu.HasField("timestamp") else None,
            service_date=service_date,
        )
    return result


def parse_vehicle_positions(data: bytes) -> list[VehiclePosition]:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(data)
    result = []
    for entity in feed.entity:
        if not entity.HasField("vehicle") or not entity.vehicle.HasField("position"):
            continue
        v = entity.vehicle
        service_date = _service_date(v.trip.start_date)
        if v.trip.start_date and service_date is None:
            continue
        if not (-90 <= v.position.latitude <= 90 and -180 <= v.position.longitude <= 180):
            log.warning("GTFS-RT has invalid coordinates for vehicle entity %s", entity.id)
            continue
        result.append(
            VehiclePosition(
                trip_id=v.trip.trip_id or None,
                route_id=v.trip.route_id or None,
                vehicle_id=v.vehicle.id or None,
                label=v.vehicle.label or None,
                lat=v.position.latitude,
                lon=v.position.longitude,
                bearing=v.position.bearing if v.position.HasField("bearing") else None,
                speed=v.position.speed if v.position.HasField("speed") else None,
                timestamp=v.timestamp if v.HasField("timestamp") else feed.header.timestamp if feed.header.HasField("timestamp") else None,
                service_date=service_date,
            )
        )
    return result


def delay_at_stop(
    update: TripUpdate | None, stop_sequence: int, stop_id: str, scheduled_epoch: int, kind: str = "arrival"
) -> int | None:
    """Real-time delay (seconds) at a stop, propagating the last known delay downstream as per GTFS-RT."""
    if update is None:
        return None
    applicable: StopTimeUpdate | None = None
    for stu in update.updates:
        seq_match = stu.stop_sequence is not None and stu.stop_sequence <= stop_sequence
        if stu.stop_id == stop_id or seq_match:
            applicable = stu
        if stu.stop_id == stop_id or (stu.stop_sequence is not None and stu.stop_sequence >= stop_sequence):
            break
    if applicable is None:
        return update.trip_delay
    exact = applicable.stop_id == stop_id or applicable.stop_sequence == stop_sequence
    if kind == "arrival":
        candidates = (applicable.arrival_delay, applicable.departure_delay)
        absolute = applicable.arrival_time or applicable.departure_time
    else:
        candidates = (applicable.departure_delay, applicable.arrival_delay)
        absolute = applicable.departure_time or applicable.arrival_time
    for value in candidates:
        if value is not None:
            return value
    if absolute and exact:
        return int(absolute - scheduled_epoch)
    return update.trip_delay


class RealtimeService:
    def __init__(self, city: City = City.GDANSK) -> None:
        self.city = city
        self._lock = threading.Lock()
        self._trip_updates: tuple[float, dict[str, TripUpdate]] = (0.0, {})
        self._vehicles: tuple[float, list[VehiclePosition]] = (0.0, [])
        self.vehicle_error: str | None = None

    def _fetch(self, url: str) -> bytes | None:
        settings = get_settings()
        try:
            with httpx.Client(timeout=10, follow_redirects=True, headers={"User-Agent": settings.user_agent}) as c:
                response = c.get(url)
                response.raise_for_status()
                return response.content
        except httpx.HTTPError as exc:
            log.warning("GTFS-RT fetch failed for %s: %s", url, exc)
            return None

    def trip_updates(self) -> dict[str, TripUpdate]:
        settings = get_settings()
        if not settings.gtfs_rt_enabled:
            return {}
        url = city_profile(self.city).trip_updates_url
        if not url:
            return {}
        with self._lock:
            fetched_at, cached = self._trip_updates
            if time.monotonic() - fetched_at < settings.gtfs_rt_cache_seconds:
                return cached
            data = self._fetch(url)
            if data is not None:
                try:
                    cached = parse_trip_updates(data)
                except Exception as exc:  # malformed protobuf
                    log.warning("Cannot parse trip updates: %s", exc)
            self._trip_updates = (time.monotonic(), cached)
            return cached

    def vehicle_positions(self) -> list[VehiclePosition]:
        settings = get_settings()
        if not settings.gtfs_rt_enabled:
            self.vehicle_error = "Live vehicle tracking is disabled"
            return []
        url = city_profile(self.city).vehicle_positions_url
        if not url:
            self.vehicle_error = "This city's vehicle feed does not identify scheduled trips; selected-trip live tracking is unavailable"
            return []
        with self._lock:
            fetched_at, cached = self._vehicles
            if time.monotonic() - fetched_at < settings.gtfs_rt_cache_seconds:
                return cached
            data = self._fetch(url)
            self.vehicle_error = "Live vehicle feed is unavailable" if data is None else None
            if data is not None:
                try:
                    cached = parse_vehicle_positions(data)
                except (DecodeError, ValueError) as exc:
                    log.warning("Cannot parse vehicle positions: %s", exc)
                    self.vehicle_error = "Live vehicle feed could not be parsed"
            self._vehicles = (time.monotonic(), cached)
            return cached


realtime_service = RealtimeService()
_city_services = {city: RealtimeService(city) for city in City if city != City.GDANSK}


def get_realtime(city: City = City.GDANSK) -> RealtimeService:
    return realtime_service if city == City.GDANSK else _city_services[city]
