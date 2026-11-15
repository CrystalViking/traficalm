"""In-memory index of the static timetable, built from the database, used by the journey planner."""

from __future__ import annotations

import bisect
import logging
import math
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .cities import City
from .geo import haversine_m

log = logging.getLogger(__name__)

GRID_DEG = 0.01  # ~1.1 km latitude cells
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@dataclass(frozen=True)
class StopInfo:
    stop_id: str
    name: str
    lat: float
    lon: float
    code: str | None = None


@dataclass(frozen=True)
class RouteInfo:
    route_id: str
    short_name: str
    long_name: str
    route_type: int
    color: str | None = None

    @property
    def mode(self) -> str:
        return {0: "tram", 1: "metro", 2: "rail", 3: "bus", 4: "ferry", 11: "trolleybus"}.get(self.route_type, "bus")


@dataclass
class TripInfo:
    trip_id: str
    route_id: str
    service_id: str
    headsign: str
    direction_id: int | None
    shape_id: str | None
    stop_ids: list[str] = field(default_factory=list)
    stop_sequences: list[int] = field(default_factory=list)
    arrivals: list[int] = field(default_factory=list)
    departures: list[int] = field(default_factory=list)


@dataclass
class _TimeTable:
    """Per-stop list of events sorted by time, for bisect lookups."""

    times: list[int] = field(default_factory=list)
    entries: list[tuple[str, int]] = field(default_factory=list)  # (trip_id, position in trip)


class TransitIndex:
    def __init__(self, city: City = City.GDANSK) -> None:
        self.city = city
        self.stops: dict[str, StopInfo] = {}
        self.routes: dict[str, RouteInfo] = {}
        self.trips: dict[str, TripInfo] = {}
        self.shapes: dict[str, list[tuple[float, float]]] = {}
        self.departures: dict[str, _TimeTable] = {}
        self.arrivals: dict[str, _TimeTable] = {}
        self.calendar: dict[str, tuple[tuple[int, ...], str, str]] = {}
        self.calendar_dates: dict[str, dict[str, int]] = defaultdict(dict)
        self.grid: dict[tuple[int, int], list[str]] = defaultdict(list)
        # Normalised (0..1) usage indicators used by the crowd model
        self.stop_load: dict[str, float] = {}
        self.route_load: dict[str, float] = {}
        self._services_cache: dict[date, frozenset[str]] = {}

    # ------------------------------------------------------------------ loading
    @classmethod
    def from_session(cls, session: Session) -> TransitIndex:
        idx = cls(city=session.info.get("city", City.GDANSK))
        for s in session.execute(select(models.Stop)).scalars():
            idx.stops[s.stop_id] = StopInfo(s.stop_id, s.stop_name, s.stop_lat, s.stop_lon, s.stop_code)
        for r in session.execute(select(models.Route)).scalars():
            idx.routes[r.route_id] = RouteInfo(
                r.route_id, r.route_short_name or r.route_id, r.route_long_name or "", r.route_type, r.route_color
            )
        for t in session.execute(select(models.Trip)).scalars():
            idx.trips[t.trip_id] = TripInfo(
                t.trip_id, t.route_id, t.service_id, t.trip_headsign or "", t.direction_id, t.shape_id
            )
        rows = session.execute(
            select(
                models.StopTime.trip_id,
                models.StopTime.stop_sequence,
                models.StopTime.stop_id,
                models.StopTime.arrival_time,
                models.StopTime.departure_time,
            ).order_by(models.StopTime.trip_id, models.StopTime.stop_sequence)
        )
        for trip_id, seq, stop_id, arr, dep in rows:
            trip = idx.trips.get(trip_id)
            if trip is None or stop_id not in idx.stops:
                continue
            trip.stop_ids.append(stop_id)
            trip.stop_sequences.append(seq)
            trip.arrivals.append(arr)
            trip.departures.append(dep)
        for c in session.execute(select(models.Calendar)).scalars():
            idx.calendar[c.service_id] = (tuple(getattr(c, d) for d in WEEKDAYS), c.start_date, c.end_date)
        for cd in session.execute(select(models.CalendarDate)).scalars():
            idx.calendar_dates[cd.service_id][cd.date] = cd.exception_type
        shape_rows = session.execute(
            select(models.ShapePoint.shape_id, models.ShapePoint.shape_pt_lat, models.ShapePoint.shape_pt_lon).order_by(
                models.ShapePoint.shape_id, models.ShapePoint.shape_pt_sequence
            )
        )
        for shape_id, lat, lon in shape_rows:
            idx.shapes.setdefault(shape_id, []).append((lat, lon))
        idx._build()
        log.info("Transit index: %d stops, %d routes, %d trips", len(idx.stops), len(idx.routes), len(idx.trips))
        return idx

    def _build(self) -> None:
        self.trips = {tid: t for tid, t in self.trips.items() if len(t.stop_ids) >= 2}
        dep_events: dict[str, list[tuple[int, str, int]]] = defaultdict(list)
        arr_events: dict[str, list[tuple[int, str, int]]] = defaultdict(list)
        route_trips: dict[str, int] = defaultdict(int)
        for trip in self.trips.values():
            route_trips[trip.route_id] += 1
            last = len(trip.stop_ids) - 1
            for pos, stop_id in enumerate(trip.stop_ids):
                if pos < last:
                    dep_events[stop_id].append((trip.departures[pos], trip.trip_id, pos))
                if pos > 0:
                    arr_events[stop_id].append((trip.arrivals[pos], trip.trip_id, pos))
        for target, events in ((self.departures, dep_events), (self.arrivals, arr_events)):
            for stop_id, evs in events.items():
                evs.sort()
                target[stop_id] = _TimeTable([e[0] for e in evs], [(e[1], e[2]) for e in evs])
        for stop in self.stops.values():
            self.grid[self._cell(stop.lat, stop.lon)].append(stop.stop_id)
        # Usage indicators relative to the busiest stop / line (square-root scaled).
        max_stop = max((len(t.times) for t in self.departures.values()), default=0)
        for stop_id in self.stops:
            n = len(self.departures[stop_id].times) if stop_id in self.departures else 0
            self.stop_load[stop_id] = math.sqrt(n / max_stop) if max_stop else 0.0
        max_route = max(route_trips.values(), default=0)
        for route_id in self.routes:
            n = route_trips.get(route_id, 0)
            self.route_load[route_id] = math.sqrt(n / max_route) if max_route else 0.0

    # ------------------------------------------------------------------ queries
    @staticmethod
    def _cell(lat: float, lon: float) -> tuple[int, int]:
        return (int(math.floor(lat / GRID_DEG)), int(math.floor(lon / GRID_DEG)))

    def nearby_stops(self, lat: float, lon: float, radius_m: float, limit: int | None = None) -> list[tuple[StopInfo, float]]:
        span_lat = radius_m / 111_000 / GRID_DEG
        span_lon = radius_m / (111_000 * max(0.1, math.cos(math.radians(lat)))) / GRID_DEG
        clat, clon = self._cell(lat, lon)
        found: list[tuple[StopInfo, float]] = []
        for dlat in range(-math.ceil(span_lat), math.ceil(span_lat) + 1):
            for dlon in range(-math.ceil(span_lon), math.ceil(span_lon) + 1):
                for stop_id in self.grid.get((clat + dlat, clon + dlon), ()):
                    stop = self.stops[stop_id]
                    d = haversine_m(lat, lon, stop.lat, stop.lon)
                    if d <= radius_m:
                        found.append((stop, d))
        found.sort(key=lambda x: x[1])
        return found[:limit] if limit else found

    def active_services(self, day: date) -> frozenset[str]:
        cached = self._services_cache.get(day)
        if cached is not None:
            return cached
        key = day.strftime("%Y%m%d")
        weekday = day.weekday()
        active: set[str] = set()
        for service_id, (days, start, end) in self.calendar.items():
            if start <= key <= end and days[weekday]:
                active.add(service_id)
        for service_id, dates in self.calendar_dates.items():
            exc = dates.get(key)
            if exc == 1:
                active.add(service_id)
            elif exc == 2:
                active.discard(service_id)
        result = frozenset(active)
        if len(self._services_cache) > 32:
            self._services_cache.clear()
        self._services_cache[day] = result
        return result

    def service_days(self, day: date) -> list[tuple[date, int, frozenset[str]]]:
        """Service days relevant for ``day``: (service date, offset seconds vs ``day`` midnight, services).

        Yesterday's services are included because GTFS trips may run past midnight (times >= 24:00).
        """
        prev = day - timedelta(days=1)
        return [(day, 0, self.active_services(day)), (prev, -86_400, self.active_services(prev))]

    def departures_between(self, stop_id: str, start: int, end: int) -> list[tuple[int, str, int]]:
        """Departures (time, trip_id, position) at ``stop_id`` with start <= time <= end (service-day seconds)."""
        table = self.departures.get(stop_id)
        if table is None:
            return []
        lo = bisect.bisect_left(table.times, start)
        hi = bisect.bisect_right(table.times, end)
        return [(table.times[i], *table.entries[i]) for i in range(lo, hi)]

    def arrivals_between(self, stop_id: str, start: int, end: int) -> list[tuple[int, str, int]]:
        table = self.arrivals.get(stop_id)
        if table is None:
            return []
        lo = bisect.bisect_left(table.times, start)
        hi = bisect.bisect_right(table.times, end)
        return [(table.times[i], *table.entries[i]) for i in range(lo, hi)]

    def shape_segment(self, trip: TripInfo, start_pos: int, end_pos: int) -> list[tuple[float, float]]:
        """Geometry of the trip between two positions; uses shapes.txt if available, else stop points."""
        stop_points = [(self.stops[s].lat, self.stops[s].lon) for s in trip.stop_ids[start_pos : end_pos + 1]]
        shape = self.shapes.get(trip.shape_id or "")
        if not shape or len(shape) < 2:
            return stop_points
        first, last = stop_points[0], stop_points[-1]
        i0 = _nearest_index(shape, first, 0)
        i1 = _nearest_index(shape, last, i0)
        if i1 <= i0:
            return stop_points
        return [first, *shape[i0 + 1 : i1], last]


def _nearest_index(points: list[tuple[float, float]], target: tuple[float, float], start: int) -> int:
    best, best_d = start, float("inf")
    for i in range(start, len(points)):
        d = (points[i][0] - target[0]) ** 2 + (points[i][1] - target[1]) ** 2
        if d < best_d:
            best, best_d = i, d
    return best


_indices: dict[object, TransitIndex] = {}
_lock = threading.Lock()


def get_index(session: Session) -> TransitIndex:
    bind = session.get_bind()
    cached = _indices.get(bind)
    if cached is not None:
        return cached
    with _lock:
        if bind not in _indices:
            _indices[bind] = TransitIndex.from_session(session)
        return _indices[bind]


def reset_index(bind: object | None = None) -> None:
    with _lock:
        if bind is None:
            _indices.clear()
        else:
            _indices.pop(bind, None)
