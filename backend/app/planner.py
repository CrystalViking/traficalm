"""Crowd-aware journey planner over the GTFS timetable.

Search strategy
---------------
1. Candidate access/egress stops within walking distance of A and B.
2. Direct rides and rides with one transfer departing within the search window
   (transfers use an egress map with suffix-minimum arrival times, so the search stays fast).
3. Crowd-avoiding variants: when a ride boards, alights or passes through stops with a high crowd
   index, low-crowd stops further away are combined with walking (board later / alight earlier /
   step off before a crowded section, walk around it and re-board the next vehicle of the same line).
4. Every itinerary gets a crowd index (0-100%) and the list is sorted by it, lowest first.
"""

from __future__ import annotations

import bisect
import hashlib
import heapq
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from shapely import get_parts
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from shapely.prepared import prep

from .config import get_settings
from .crowd.areas import _latlon, _xy, collect_samples
from .crowd.estimator import CrowdEstimator, LegCrowd, itinerary_crowd
from .geo import distance_to_path_m, haversine_m
from .realtime import TripUpdate, delay_at_stop
from .transit_index import RouteInfo, StopInfo, TransitIndex, TripInfo
from .walking import WalkPath, estimate_walk, route_walk

MAX_WALK_ONLY_M = 2500.0
DEPARTURES_PER_PATTERN = 2
AVOID_STOP_RADIUS_M = 100.0
# Strict walking: crowded areas are avoided on foot, except around the user's own endpoints.
HIGH_CROWD_INDEX = 50  # lower bound of the "high" crowd level shown in the app
MAX_WALK_ONLY_M = 2500
ENDPOINT_EXEMPT_M = 200.0
DETOUR_MARGIN_M = 15.0
ROUTED_DETOUR_MARGIN_M = 60.0
DETOUR_CORRIDOR_M = 1500.0
MAX_DETOUR_NODES = 150
ZONE_PAD_DEG = 0.04


class InvalidJourneyEdit(ValueError):
    pass


@dataclass(frozen=True)
class Ride:
    trip_id: str
    board: int  # position in trip
    alight: int
    offset: int  # seconds to add to trip times to obtain seconds after query-day midnight


@dataclass
class Place:
    lat: float
    lon: float
    name: str


@dataclass
class StopCall:
    stop: StopInfo
    stop_sequence: int
    arrival: datetime
    departure: datetime
    crowd: float
    rt_arrival: datetime | None = None
    rt_departure: datetime | None = None
    delay_s: int | None = None


@dataclass
class Leg:
    mode: str  # walk | transit
    start: Place
    end: Place
    departure: datetime
    arrival: datetime
    distance_m: float
    geometry: list[tuple[float, float]]
    crowd: LegCrowd
    ride: Ride | None = None
    trip: TripInfo | None = None
    route: RouteInfo | None = None
    calls: list[StopCall] = field(default_factory=list)
    service_date: date | None = None
    walk_routed: bool = False

    @property
    def duration_s(self) -> float:
        return (self.arrival - self.departure).total_seconds()


@dataclass
class Itinerary:
    rides: tuple[Ride, ...]
    legs: list[Leg]
    crowd_index: int
    crowd_avoiding: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        raw = "|".join(f"{r.trip_id}:{r.board}:{r.alight}:{r.offset}" for r in self.rides) or "walk"
        return hashlib.sha1(raw.encode()).hexdigest()[:12]

    @property
    def departure(self) -> datetime:
        return self.legs[0].departure

    @property
    def arrival(self) -> datetime:
        return self.legs[-1].arrival

    @property
    def transfers(self) -> int:
        return max(0, len(self.rides) - 1)

    @property
    def walk_distance_m(self) -> float:
        return sum(leg.distance_m for leg in self.legs if leg.mode == "walk")


class Planner:
    def __init__(
        self,
        index: TransitIndex,
        estimator: CrowdEstimator,
        trip_updates: dict[str, TripUpdate] | None = None,
        use_walk_router: bool = True,
        strict_walking: bool = False,
        fastest: bool = False,
    ) -> None:
        self.fastest = fastest
        self.index = index
        self.crowd = estimator
        self.trip_updates = trip_updates or {}
        self.use_walk_router = use_walk_router
        self.strict_walking = strict_walking
        self.settings = get_settings()
        self.tz = ZoneInfo(self.settings.timezone)
        self._transfer_cache: dict[str, list[tuple[StopInfo, float]]] = {}
        self._zone = None
        self._zone_prep = None
        self._buffered: dict[float, object] = {}
        self._walk_cache: dict[tuple, WalkPath | None] = {}

    # ------------------------------------------------------------------ helpers
    def _walk_s(self, dist_m: float) -> int:
        s = self.settings
        return int(round(dist_m * s.walk_detour_factor / s.walk_speed_mps))

    def _abs(self, seconds: int) -> datetime:
        return self._midnight + timedelta(seconds=seconds)

    def _neighbors(self, stop_id: str) -> list[tuple[StopInfo, float]]:
        cached = self._transfer_cache.get(stop_id)
        if cached is None:
            stop = self.index.stops[stop_id]
            radius = self.settings.max_avoidance_walk_m if self._blocked else self.settings.max_transfer_walk_m
            cached = self.index.nearby_stops(stop.lat, stop.lon, radius)
            self._transfer_cache[stop_id] = cached
        return cached

    def _rank(self, it: Itinerary) -> tuple:
        if self.fastest:
            return (it.arrival, it.transfers, it.crowd_index)
        return (it.crowd_index, it.arrival, it.transfers)

    # ------------------------------------------------------------------ search
    def plan_strict(
        self, origin: Place, destination: Place, when: datetime, max_results: int | None = None,
        crowd_tolerance: int | None = None, avoid_stop_ids: frozenset[str] = frozenset(),
    ) -> list[Itinerary]:
        """Plan, then once drop the best route's most crowded stop and replan the whole journey.

        This automates "Avoid stop" followed by "Remove stop and replan".
        """
        self.strict_walking = False
        try:
            first = self.plan(origin, destination, when, max_results, crowd_tolerance, avoid_stop_ids)
            if not first:
                return first
            walk_stop = self._stop_before_crowded_walk(first[0])
            merged = {it.id: it for it in first}
            if walk_stop is not None and walk_stop not in avoid_stop_ids:
                second = self.plan(
                    origin, destination, when, max_results, crowd_tolerance, avoid_stop_ids | {walk_stop},
                )
                merged.update({it.id: it for it in second})
        finally:
            self.strict_walking = True

        ranked = sorted(merged.values(), key=lambda i: (i.crowd_index, i.arrival, i.transfers))
        # A long walk-only route is not a useful replacement for public transport.
        ranked = [
            it for it in ranked
            if it.rides or sum(leg.distance_m for leg in it.legs) <= MAX_WALK_ONLY_M
        ] or ranked
        return ranked[: max_results or self.settings.max_results]

    def _stop_before_crowded_walk(self, it: Itinerary) -> str | None:
        """Stop where the last walk through a high or very high crowded area starts (or ends, for the access walk)."""
        for n in range(len(it.legs) - 1, -1, -1):
            leg = it.legs[n]
            if leg.mode != "walk" or not self._intersects_zone(leg.geometry):
                continue
            if n > 0 and it.legs[n - 1].calls:
                return it.legs[n - 1].calls[-1].stop.stop_id
            if n + 1 < len(it.legs) and it.legs[n + 1].calls:
                return it.legs[n + 1].calls[0].stop.stop_id
        return None

    def plan(
        self, origin: Place, destination: Place, when: datetime, max_results: int | None = None,
        crowd_tolerance: int | None = None, avoid_stop_ids: frozenset[str] = frozenset(),
        boarding_stop_id: str | None = None, avoid_stop_areas: bool = True,
    ) -> list[Itinerary]:
        when = when.astimezone(self.tz) if when.tzinfo else when.replace(tzinfo=self.tz)
        self._prepare(origin, destination, when, crowd_tolerance, avoid_stop_ids, avoid_stop_areas)
        s = self.settings
        max_results = max_results or s.max_results

        access = (
            {boarding_stop_id: 0} if boarding_stop_id is not None else
            {st.stop_id: self._walk_s(d) for st, d in self.index.nearby_stops(origin.lat, origin.lon, s.max_access_walk_m, 15)}
        )
        egress = {st.stop_id: self._walk_s(d) for st, d in self.index.nearby_stops(destination.lat, destination.lon, s.max_access_walk_m, 15)}
        self._access, self._egress = access, egress

        candidates: list[tuple[Ride, ...]] = []
        for svc_date, day_offset, services in self.index.service_days(when.date()):
            candidates += self._direct(access, egress, services, day_offset)
            candidates += self._one_transfer(access, egress, services, day_offset)
            if self._blocked:
                # Keep original transfer journeys as seeds for walk-around edits.
                candidates += self._one_transfer(access, egress, services, day_offset, respect_avoidance=False)

        itineraries: dict[tuple[Ride, ...], Itinerary] = {}
        allowed = [rides for rides in candidates if self._rides_allowed(rides)] if self._blocked else candidates
        seeds: list[Itinerary] = []
        for rides in _select_candidates(self.index, allowed, self._arrival_of):
            it = self.build(rides)
            if it is not None:
                itineraries[rides] = it
        if self._blocked:
            blocked = [rides for rides in candidates if not self._rides_allowed(rides)]
            for rides in _select_candidates(self.index, blocked, self._arrival_of):
                seed = self.build(rides)
                if seed is not None:
                    seeds.append(seed)

        if not itineraries:
            extended = []
            for _, day_offset, services in self.index.service_days(when.date()):
                extended += self._two_transfers(access, egress, services, day_offset)
            for rides in _select_candidates(self.index, extended, self._arrival_of):
                it = self.build(rides)
                if it is not None and self._rides_allowed(rides):
                    itineraries[rides] = it

        # Crowd-avoiding variants
        for it in [*itineraries.values(), *seeds]:
            for rides in ([] if self.fastest and not self._blocked else self._avoidance_variants(it)):
                if rides in itineraries:
                    continue
                variant = self.build(rides, crowd_avoiding=True)
                if variant is not None and self._rides_allowed(rides) and (self._blocked or variant.crowd_index < it.crowd_index):
                    if self._blocked:
                        variant.notes.append("Changes boarding/alighting or walks around selected stops")
                    else:
                        variant.notes.append(
                            "Avoids crowded stops by walking to quieter stops "
                            f"(crowd index {it.crowd_index}% → {variant.crowd_index}%)"
                        )
                    itineraries[rides] = variant

        direct_dist = haversine_m(origin.lat, origin.lon, destination.lat, destination.lon)
        if boarding_stop_id is None and (direct_dist <= MAX_WALK_ONLY_M or not itineraries or self._blocked):
            walk = self.build(())
            if walk is not None:
                walk.notes.append("Walking only – no public transport")
                itineraries[()] = walk

        ranked = sorted(itineraries.values(), key=self._rank)
        if boarding_stop_id is not None:
            ranked = [
                it for it in ranked if it.rides
                and self.index.trips[it.rides[0].trip_id].stop_ids[it.rides[0].board] == boarding_stop_id
            ]
        if not self._blocked:
            ranked = ranked[: max_results * 2]
        # Refine with the Laya decision model (single batched call) and re-rank.
        legs = [leg.crowd for it in ranked for leg in it.legs if leg.mode == "transit"]
        if not self.fastest and self.crowd.refine_with_laya(legs):
            for it in ranked:
                it.crowd_index = itinerary_crowd([(leg.crowd, leg.duration_s) for leg in it.legs])
        ranked = sorted(ranked, key=self._rank)
        final: list[Itinerary] = []
        for it in ranked:
            if self.use_walk_router or self.strict_walking:
                if not self._route_walks(it):
                    continue
            if any(a.arrival > b.departure for a, b in zip(it.legs, it.legs[1:])) or it.departure < when:
                continue
            if self.strict_walking:
                if any(self._hits_zone(leg.geometry) for leg in it.legs if leg.mode == "walk"):
                    continue
                if any(leg.mode == "walk" and not leg.walk_routed and len(leg.geometry) > 2 for leg in it.legs):
                    it.notes.append("Walks around very crowded areas; walking geometry is estimated, check local footpaths")
            if self._blocked or boarding_stop_id is not None:
                # Never advertise a bypass whose walking path still crosses the selected stop.
                if avoid_stop_areas and any(
                    distance_to_path_m(stop.lat, stop.lon, leg.geometry) < AVOID_STOP_RADIUS_M
                    for leg in it.legs for stop in self._avoided
                ):
                    continue
                it.crowd_avoiding = bool(self._blocked)
                if self._blocked:
                    it.notes.append(
                        "Alternative journey avoiding selected stops and their 100 m surroundings"
                        if avoid_stop_areas else "Onward journey skips selected stops"
                    )
                if any(leg.mode == "walk" and not leg.walk_routed for leg in it.legs):
                    it.notes.append("Walking geometry is estimated; check local footpaths before travelling")
            final.append(it)
            if len(final) == max_results:
                break
        return final

    def _prepare(
        self, origin: Place, destination: Place, when: datetime,
        crowd_tolerance: int | None, avoided: frozenset[str], avoid_stop_areas: bool = True,
    ) -> None:
        self._origin, self._dest = origin, destination
        self._midnight = datetime.combine(when.date(), time(0), tzinfo=self.tz)
        self._t0 = int((when - self._midnight).total_seconds())
        self._threshold = (
            self.settings.high_crowd_threshold if crowd_tolerance is None else crowd_tolerance
        ) / 100
        self._avoided = [self.index.stops[stop_id] for stop_id in avoided]
        self._blocked = set(avoided)
        if avoid_stop_areas:
            self._blocked.update(
                stop.stop_id for selected in self._avoided
                for stop, _ in self.index.nearby_stops(selected.lat, selected.lon, AVOID_STOP_RADIUS_M)
            )
        self._transfer_cache.clear()
        self._zone = None
        self._zone_prep = None
        self._buffered.clear()
        self._walk_cache.clear()

    def continue_journey(
        self, origin: Place, destination: Place, when: datetime, prefix_rides: tuple[Ride, ...],
        exit_stop_id: str, boarding_stop_id: str, crowd_tolerance: int,
        avoid_stop_ids: frozenset[str],
    ) -> list[Itinerary]:
        exit_stop, boarding = self.index.stops[exit_stop_id], self.index.stops[boarding_stop_id]
        exit_place = Place(exit_stop.lat, exit_stop.lon, exit_stop.name)
        board_place = Place(boarding.lat, boarding.lon, boarding.name)
        self._prepare(origin, exit_place, when, crowd_tolerance, frozenset())
        prefix = self.build(prefix_rides)
        if prefix is None:
            raise InvalidJourneyEdit("The retained journey cannot reach the selected exit stop in time")
        if self.use_walk_router:
            self._route_walks(prefix)
        for position, leg in enumerate(prefix.legs):
            if leg.calls:
                leg.departure = max(leg.departure, leg.calls[0].rt_departure or leg.departure)
                leg.arrival = max(leg.arrival, leg.calls[-1].rt_arrival or leg.arrival)
            elif position:
                shift = max(timedelta(), prefix.legs[position - 1].arrival - leg.departure)
                leg.departure += shift
                leg.arrival += shift
        if prefix.departure < when or any(a.arrival > b.departure for a, b in zip(prefix.legs, prefix.legs[1:])):
            raise InvalidJourneyEdit("The retained journey has an infeasible walking connection")
        transit = [leg for leg in prefix.legs if leg.mode == "transit"]
        if any(leg.arrival < leg.departure for leg in prefix.legs) or any(
            a.arrival + timedelta(seconds=self.settings.min_transfer_seconds) > b.departure
            for a, b in zip(transit, transit[1:])
        ):
            raise InvalidJourneyEdit("Real-time delays make the retained journey infeasible")
        exit_time = prefix.arrival
        path = route_walk(exit_place.lat, exit_place.lon, board_place.lat, board_place.lon) if self.use_walk_router else estimate_walk(
            exit_place.lat, exit_place.lon, board_place.lat, board_place.lon,
        )
        bridge = self._walk_leg(
            exit_place, board_place, exit_time, exit_time + timedelta(seconds=path.duration_s), path.distance_m,
        )
        bridge.geometry, bridge.walk_routed = list(path.geometry), path.routed
        ready = exit_time + timedelta(seconds=max(path.duration_s, self.settings.min_transfer_seconds))
        onward_planner = Planner(self.index, self.crowd, self.trip_updates,         self.use_walk_router, self.strict_walking, self.fastest)
        onward = onward_planner.plan(
            board_place, destination, ready, crowd_tolerance=crowd_tolerance,
            avoid_stop_ids=avoid_stop_ids, boarding_stop_id=boarding_stop_id, avoid_stop_areas=False,
        )
        if onward:
            self.crowd.refine_with_laya([leg.crowd for leg in prefix.legs if leg.mode == "transit"])
        results = []
        for continuation in onward:
            legs = [*prefix.legs, bridge, *continuation.legs]
            notes = [
                *prefix.notes, *continuation.notes,
                f"Leave at {exit_stop.name}, walk to {boarding.name}, and continue from there",
            ]
            if not path.routed:
                notes.append("Walking geometry is estimated; check local footpaths before travelling")
            results.append(Itinerary(
                (*prefix_rides, *continuation.rides), legs,
                itinerary_crowd([(leg.crowd, leg.duration_s) for leg in legs]), True, notes,
            ))
        return sorted(results, key=self._rank)

    def _rides_allowed(self, rides: tuple[Ride, ...]) -> bool:
        return all(
            not self._blocked.intersection(self.index.trips[r.trip_id].stop_ids[r.board:r.alight + 1])
            for r in rides
        )

    def _arrival_of(self, rides: tuple[Ride, ...]) -> int:
        last = rides[-1]
        trip = self.index.trips[last.trip_id]
        return trip.arrivals[last.alight] + last.offset + self._egress.get(trip.stop_ids[last.alight], 0)

    def _active(self, trip: TripInfo, services: frozenset[str]) -> bool:
        return trip.service_id in services

    def _direct(self, access, egress, services, day_offset) -> list[tuple[Ride, ...]]:
        out = []
        window_end = self._t0 + self.settings.search_window_minutes * 60
        for o_id, walk in access.items():
            ready = self._t0 + walk - day_offset
            for _, trip_id, pos in self.index.departures_between(o_id, ready, window_end - day_offset):
                trip = self.index.trips[trip_id]
                if not self._active(trip, services):
                    continue
                best = None
                for j in range(pos + 1, len(trip.stop_ids)):
                    e = egress.get(trip.stop_ids[j])
                    if e is None:
                        continue
                    arr = trip.arrivals[j] + e
                    if best is None or arr < best[0]:
                        best = (arr, j)
                if best is not None:
                    out.append((Ride(trip_id, pos, best[1], day_offset),))
        return out

    def _one_transfer(self, access, egress, services, day_offset, respect_avoidance: bool = True) -> list[tuple[Ride, ...]]:
        s = self.settings
        window_end = self._t0 + s.search_window_minutes * 60 - day_offset
        blocked = self._blocked if respect_avoidance else set()
        tables = self._egress_tables(egress, services, day_offset, blocked)

        out: list[tuple[Ride, ...]] = []
        for o_id, walk in access.items():
            ready0 = self._t0 + walk - day_offset
            for _, trip_id, pos in self.index.departures_between(o_id, ready0, window_end):
                trip = self.index.trips[trip_id]
                if not self._active(trip, services):
                    continue
                best: tuple[int, tuple[Ride, ...]] | None = None
                for k in range(pos + 1, len(trip.stop_ids)):
                    if blocked and blocked.intersection(trip.stop_ids[pos:k + 1]):
                        break
                    s_id = trip.stop_ids[k]
                    for nb, dist in self._neighbors(s_id):
                        table = tables.get(nb.stop_id)
                        if table is None:
                            continue
                        ready = trip.arrivals[k] + max(s.min_transfer_seconds, self._walk_s(dist))
                        idx = bisect.bisect_left(table[0], ready)
                        if idx >= len(table[0]):
                            continue
                        _, final, trip2_id, i, j = table[1][idx]
                        trip2 = self.index.trips[trip2_id]
                        if trip2.route_id == trip.route_id:
                            continue
                        if best is None or final < best[0]:
                            best = (final, (Ride(trip_id, pos, k, day_offset), Ride(trip2_id, i, j, day_offset)))
                if best is not None:
                    out.append(best[1])
        return out

    def _egress_tables(self, egress, services, day_offset, blocked):
        horizon = self._t0 + self.settings.search_window_minutes * 60 - day_offset + 90 * 60
        # Egress map: boarding stop -> sorted (dep, final_arrival, trip_id, board_pos, alight_pos)
        best_by_board: dict[tuple[str, int], tuple[int, int, int]] = {}
        for d_id, walk in egress.items():
            for arr, trip_id, j in self.index.arrivals_between(d_id, self._t0 - day_offset, horizon):
                trip = self.index.trips[trip_id]
                if not self._active(trip, services):
                    continue
                final = arr + walk
                for i in range(j):
                    if blocked and blocked.intersection(trip.stop_ids[i:j + 1]):
                        continue
                    key = (trip_id, i)
                    cur = best_by_board.get(key)
                    if cur is None or final < cur[0]:
                        best_by_board[key] = (final, j, trip.departures[i])
        emap: dict[str, list[tuple[int, int, str, int, int]]] = defaultdict(list)
        for (trip_id, i), (final, j, dep) in best_by_board.items():
            emap[self.index.trips[trip_id].stop_ids[i]].append((dep, final, trip_id, i, j))
        tables: dict[str, tuple[list[int], list[tuple[int, int, str, int, int]]]] = {}
        for stop_id, entries in emap.items():
            entries.sort()
            suffix: list[tuple[int, int, str, int, int]] = [entries[-1]] * len(entries)
            best = entries[-1]
            for k in range(len(entries) - 1, -1, -1):
                if entries[k][1] <= best[1]:
                    best = entries[k]
                suffix[k] = best
            tables[stop_id] = ([e[0] for e in entries], suffix)

        return tables

    def _two_transfers(self, access, egress, services, day_offset) -> list[tuple[Ride, ...]]:
        s = self.settings
        window_end = self._t0 + s.search_window_minutes * 60 - day_offset
        horizon = window_end + 90 * 60
        tables = self._egress_tables(egress, services, day_offset, self._blocked)
        if not tables:
            return []
        interchanges: dict[tuple[str, str], tuple[int, Ride]] = {}
        for o_id, walk in access.items():
            for _, trip_id, pos in self.index.departures_between(o_id, self._t0 + walk - day_offset, window_end):
                trip = self.index.trips[trip_id]
                if not self._active(trip, services):
                    continue
                for k in range(pos + 1, len(trip.stop_ids)):
                    if self._blocked.intersection(trip.stop_ids[pos:k + 1]):
                        break
                    for nb, distance in self._neighbors(trip.stop_ids[k]):
                        ready = trip.arrivals[k] + max(s.min_transfer_seconds, self._walk_s(distance))
                        key = (nb.stop_id, trip.route_id)
                        if key not in interchanges or ready < interchanges[key][0]:
                            interchanges[key] = (ready, Ride(trip_id, pos, k, day_offset))
        middle: dict[tuple[str, int, str], Ride] = {}
        for (stop_id, route_id), (ready, first) in interchanges.items():
            for _, trip_id, pos in self.index.departures_between(stop_id, ready, horizon):
                trip = self.index.trips[trip_id]
                if not self._active(trip, services) or trip.route_id == route_id:
                    continue
                key = (trip_id, pos, route_id)
                previous = middle.get(key)
                if previous is None or self.index.trips[first.trip_id].departures[first.board] > self.index.trips[previous.trip_id].departures[previous.board]:
                    middle[key] = first
        out = []
        for (trip_id, pos, _), first in middle.items():
            trip = self.index.trips[trip_id]
            best: tuple[int, tuple[Ride, ...]] | None = None
            for k in range(pos + 1, len(trip.stop_ids)):
                if self._blocked.intersection(trip.stop_ids[pos:k + 1]):
                    break
                for nb, distance in self._neighbors(trip.stop_ids[k]):
                    ready = trip.arrivals[k] + max(s.min_transfer_seconds, self._walk_s(distance))
                    table = tables.get(nb.stop_id)
                    if table is not None:
                        i = bisect.bisect_left(table[0], ready)
                        if i >= len(table[0]):
                            continue
                        _, final, last_id, board, alight = table[1][i]
                        if self.index.trips[last_id].route_id == trip.route_id:
                            continue
                        if best is None or final < best[0]:
                            best = (final, (first, Ride(trip_id, pos, k, day_offset), Ride(last_id, board, alight, day_offset)))
            if best is not None:
                out.append(best[1])
        return out

    # ------------------------------------------------------------------ strict walking
    def _forbidden(self):
        """Crowd-map areas above the tolerance (metric map projection), minus the user's own endpoints."""
        if self._zone is None:
            o, d = self._origin, self._dest
            lon_pad = ZONE_PAD_DEG * 1.7
            bounds = (
                min(o.lat, d.lat) - ZONE_PAD_DEG, min(o.lon, d.lon) - lon_pad,
                max(o.lat, d.lat) + ZONE_PAD_DEG, max(o.lon, d.lon) + lon_pad,
            )
            city = self.index.city
            samples = collect_samples(self.crowd, self._abs(self._t0), bounds)
            zone = unary_union([s.footprint for s in samples if s.crowd_index >= HIGH_CROWD_INDEX])
            for place in (o, d):
                zone = zone.difference(Point(_xy(place.lat, place.lon, city)).buffer(ENDPOINT_EXEMPT_M))
            self._zone = zone.buffer(0)
            self._zone_prep = prep(self._zone)
        return self._zone

    def _hits_zone(self, points) -> bool:
        return self.strict_walking and self._intersects_zone(points)

    def _intersects_zone(self, points) -> bool:
        if len(points) < 2:
            return False
        zone = self._forbidden()
        if zone.is_empty:
            return False
        city = self.index.city
        return bool(self._zone_prep.intersects(LineString([_xy(lat, lon, city) for lat, lon in points])))

    def _detour(self, a: tuple[float, float], b: tuple[float, float], margin: float) -> list[tuple[float, float]] | None:
        """Shortest walk from a to b around the forbidden zone (A* over the zone's outline), or None."""
        city = self.index.city
        pa, pb = _xy(*a, city), _xy(*b, city)
        direct = math.dist(pa, pb)
        zone = self._forbidden()
        near = self._buffered.get(margin)
        if near is None:
            near = self._buffered[margin] = zone.buffer(margin)
        corridor = LineString([pa, pb]).buffer(DETOUR_CORRIDOR_M)
        obstacles = [p for p in get_parts(near) if p.intersects(corridor)]
        if not obstacles:
            return [a, b]
        simplified = [polygon.simplify(8) for polygon in obstacles if not polygon.is_empty]
        blocker = unary_union(simplified)
        blocker_prep = prep(blocker)
        nodes = [pa, pb]
        for polygon in get_parts(blocker):
            nodes += [p for p in polygon.exterior.coords]
        if len(nodes) > MAX_DETOUR_NODES + 2:
            line = LineString([pa, pb])
            nodes = nodes[:2] + sorted(nodes[2:], key=lambda p: line.distance(Point(p)))[:MAX_DETOUR_NODES]

        def clear(p, q) -> bool:
            seg = LineString([p, q])
            return not (blocker_prep.crosses(seg) or blocker_prep.contains(seg))

        limit = direct * 3 + 500
        best = {0: 0.0}
        parent: dict[int, int] = {}
        queue = [(direct, 0.0, 0)]
        done = set()
        while queue:
            _, g, i = heapq.heappop(queue)
            if i in done:
                continue
            done.add(i)
            if i == 1:
                path = [1]
                while path[-1] in parent:
                    path.append(parent[path[-1]])
                return [a if k == 0 else b if k == 1 else _latlon(*nodes[k], city) for k in reversed(path)]
            for j, q in enumerate(nodes):
                if j in done or j == i:
                    continue
                step = math.dist(nodes[i], q)
                cost = g + step
                if cost >= best.get(j, math.inf) or cost + math.dist(q, pb) > limit or not clear(nodes[i], q):
                    continue
                best[j], parent[j] = cost, i
                heapq.heappush(queue, (cost + math.dist(q, pb), cost, j))
        return None

    def _walk(self, a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> WalkPath | None:
        """Estimated walk; with strict walking it goes around crowded areas, or is None when that is impossible."""
        direct = estimate_walk(a_lat, a_lon, b_lat, b_lon)
        if not self.strict_walking or direct.distance_m <= 1:
            return direct
        key = (round(a_lat, 5), round(a_lon, 5), round(b_lat, 5), round(b_lon, 5))
        if key not in self._walk_cache:
            path = None
            if not self._hits_zone(list(direct.geometry)):
                path = direct
            else:
                around = self._detour((a_lat, a_lon), (b_lat, b_lon), DETOUR_MARGIN_M)
                if around is not None:
                    length = sum(haversine_m(*p, *q) for p, q in zip(around, around[1:]))
                    dist = length * self.settings.walk_detour_factor
                    path = WalkPath(dist, int(round(dist / self.settings.walk_speed_mps)), tuple(around), False)
            self._walk_cache[key] = path
        return self._walk_cache[key]

    def _strict_footpath(self, leg: Leg) -> WalkPath | None:
        """Routed footpath that stays out of the forbidden zone, falling back to an estimated walk-around."""
        a, b = (leg.start.lat, leg.start.lon), (leg.end.lat, leg.end.lon)
        if not self.use_walk_router:
            return self._walk(*a, *b)
        direct = route_walk(*a, *b)
        if not self._hits_zone(list(direct.geometry)):
            return direct
        around = self._detour(a, b, ROUTED_DETOUR_MARGIN_M)
        if around is not None and len(around) > 2:
            parts = [route_walk(*p, *q) for p, q in zip(around, around[1:])]
            geometry = [point for part in parts for point in part.geometry]
            if not self._hits_zone(geometry):
                return WalkPath(
                    sum(p.distance_m for p in parts), sum(p.duration_s for p in parts),
                    tuple(geometry), all(p.routed for p in parts),
                )
        return self._walk(*a, *b)

    # ------------------------------------------------------------------ itinerary construction
    def build(self, rides: tuple[Ride, ...], crowd_avoiding: bool = False) -> Itinerary | None:
        o, d = self._origin, self._dest
        legs: list[Leg] = []
        notes: list[str] = []
        if not rides:
            w = self._walk(o.lat, o.lon, d.lat, d.lon)
            if w is None:
                return None
            dep = self._abs(self._t0)
            legs.append(self._walk_leg(o, d, dep, dep + timedelta(seconds=w.duration_s), w.distance_m, w.geometry))
        else:
            prev_place, prev_time = o, None
            for n, ride in enumerate(rides):
                trip = self.index.trips[ride.trip_id]
                board = self.index.stops[trip.stop_ids[ride.board]]
                board_place = Place(board.lat, board.lon, board.name)
                dep = self._abs(trip.departures[ride.board] + ride.offset)
                w = self._walk(prev_place.lat, prev_place.lon, board.lat, board.lon)
                if w is None:
                    return None
                if prev_time is None:
                    start = dep - timedelta(seconds=w.duration_s)
                    if start < self._abs(self._t0):
                        return None
                else:
                    start = prev_time
                    need = max(w.duration_s, self.settings.min_transfer_seconds)
                    if start + timedelta(seconds=need) > dep:
                        return None
                if w.distance_m > 1:
                    legs.append(self._walk_leg(prev_place, board_place, start, start + timedelta(seconds=w.duration_s), w.distance_m, w.geometry))
                leg = self._transit_leg(ride, trip)
                legs.append(leg)
                prev_place, prev_time = leg.end, leg.arrival
            w = self._walk(prev_place.lat, prev_place.lon, d.lat, d.lon)
            if w is None:
                return None
            if w.distance_m > 1:
                legs.append(self._walk_leg(prev_place, d, prev_time, prev_time + timedelta(seconds=w.duration_s), w.distance_m, w.geometry))
            notes += self._realtime_notes(legs)
        crowd = itinerary_crowd([(leg.crowd, leg.duration_s) for leg in legs])
        return Itinerary(rides, legs, crowd, crowd_avoiding, notes)

    def _walk_leg(
        self, a: Place, b: Place, dep: datetime, arr: datetime, dist: float,
        geometry: tuple[tuple[float, float], ...] | None = None,
    ) -> Leg:
        mid = dep + (arr - dep) / 2
        crowd = self.crowd.walk_leg([(a.lat, a.lon, dep), ((a.lat + b.lat) / 2, (a.lon + b.lon) / 2, mid), (b.lat, b.lon, arr)])
        path = list(geometry) if geometry else [(a.lat, a.lon), (b.lat, b.lon)]
        return Leg("walk", a, b, dep, arr, dist, path, crowd)

    def _transit_leg(self, ride: Ride, trip: TripInfo) -> Leg:
        route = self.index.routes.get(trip.route_id)
        update = self.trip_updates.get(trip.trip_id)
        service_midnight = self._midnight + timedelta(seconds=ride.offset)
        if update is not None and update.service_date is not None and update.service_date != service_midnight.date():
            update = None
        calls: list[StopCall] = []
        crowd_input: list[tuple[StopInfo, datetime]] = []
        for p in range(ride.board, ride.alight + 1):
            stop = self.index.stops[trip.stop_ids[p]]
            arr = self._abs(trip.arrivals[p] + ride.offset)
            dep = self._abs(trip.departures[p] + ride.offset)
            call = StopCall(stop, trip.stop_sequences[p], arr, dep, 0.0)
            if update is not None:
                kind_time = arr if p > ride.board else dep
                delay = delay_at_stop(
                    update, trip.stop_sequences[p], stop.stop_id, int(kind_time.timestamp()),
                    "arrival" if p > ride.board else "departure",
                )
                if delay is not None:
                    call.delay_s = delay
                    call.rt_arrival = arr + timedelta(seconds=delay)
                    call.rt_departure = dep + timedelta(seconds=delay)
            calls.append(call)
            crowd_input.append((stop, dep))
        assert route is not None
        crowd = self.crowd.transit_leg(route, trip.headsign, crowd_input)
        for call, value in zip(calls, crowd.stop_values, strict=True):
            call.crowd = value
        first, last = calls[0], calls[-1]
        geometry = self.index.shape_segment(trip, ride.board, ride.alight)
        dist = sum(haversine_m(*geometry[i], *geometry[i + 1]) for i in range(len(geometry) - 1))
        return Leg(
            "transit",
            Place(first.stop.lat, first.stop.lon, first.stop.name),
            Place(last.stop.lat, last.stop.lon, last.stop.name),
            first.departure,
            last.arrival,
            dist,
            geometry,
            crowd,
            ride=ride,
            trip=trip,
            route=route,
            calls=calls,
            service_date=service_midnight.date(),
        )

    def _realtime_notes(self, legs: list[Leg]) -> list[str]:
        notes = []
        transit = [leg for leg in legs if leg.mode == "transit"]
        for a, b in zip(transit, transit[1:], strict=False):
            arr = a.calls[-1].rt_arrival or a.calls[-1].arrival
            dep = b.calls[0].rt_departure or b.calls[0].departure
            if arr + timedelta(seconds=60) > dep:
                notes.append(f"Transfer at {b.start.name} is at risk due to real-time delays")
        return notes

    def _route_walks(self, it: Itinerary) -> bool:
        """Replace straight-line walking geometry with OSM footpaths for the final results.

        With strict walking the footpaths must stay out of crowded areas; returns False when none exists.
        """
        for n, leg in enumerate(it.legs):
            if leg.mode != "walk" or leg.distance_m < 1:
                continue
            if self.strict_walking:
                path = self._strict_footpath(leg)
                if path is None:
                    return False
            else:
                path = route_walk(leg.start.lat, leg.start.lon, leg.end.lat, leg.end.lon)
            leg.geometry = list(path.geometry)
            leg.distance_m = path.distance_m
            leg.walk_routed = path.routed
            if not path.routed:
                continue
            dur = timedelta(seconds=path.duration_s)
            if n == 0 and len(it.legs) > 1:
                # access walk: must still reach the first vehicle in time
                leg.departure = leg.arrival - dur
            elif n == len(it.legs) - 1:
                leg.arrival = leg.departure + dur
            else:
                leg.arrival = leg.departure + dur
                if leg.arrival > it.legs[n + 1].departure:
                    it.notes.append(f"Tight transfer: walking to {leg.end.name} takes about {path.duration_s // 60} min")
        return True

    # ------------------------------------------------------------------ crowd avoidance
    def _avoidance_variants(self, it: Itinerary) -> list[tuple[Ride, ...]]:
        threshold = self._threshold
        max_walk = self.settings.max_avoidance_walk_m
        transit = [leg for leg in it.legs if leg.mode == "transit"]
        if not transit:
            return []
        variants: list[tuple[Ride, ...]] = []
        rides = list(it.rides)
        o, d = self._origin, self._dest
        new_board: int | None = None
        new_alight: int | None = None

        # 1. Crowded boarding stop of the first ride: walk further and board at a quieter stop.
        first = transit[0]
        vals = [round(value * 100) / 100 for value in first.crowd.stop_values]
        if vals[0] > threshold or first.calls[0].stop.stop_id in self._blocked:
            for k in range(1, len(vals) - 1):
                stop = first.calls[k].stop
                if (self._blocked or vals[k] <= threshold) and stop.stop_id not in self._blocked and haversine_m(o.lat, o.lon, stop.lat, stop.lon) <= max_walk:
                    r = rides[0]
                    new_board = r.board + k
                    variants.append((Ride(r.trip_id, new_board, r.alight, r.offset), *rides[1:]))
                    break

        # 2. Crowded alighting stop of the last ride: get off earlier and walk.
        last = transit[-1]
        vals = [round(value * 100) / 100 for value in last.crowd.stop_values]
        if vals[-1] > threshold or last.calls[-1].stop.stop_id in self._blocked:
            for k in range(len(vals) - 2, 0, -1):
                stop = last.calls[k].stop
                if (self._blocked or vals[k] <= threshold) and stop.stop_id not in self._blocked and haversine_m(stop.lat, stop.lon, d.lat, d.lon) <= max_walk:
                    r = rides[-1]
                    new_alight = r.board + k
                    variants.append((*rides[:-1], Ride(r.trip_id, r.board, new_alight, r.offset)))
                    break

        # Both ends of a single ride are crowded: combine 1 and 2.
        if len(rides) == 1 and new_board is not None and new_alight is not None:
            variants.append((Ride(rides[0].trip_id, new_board, new_alight, rides[0].offset),))

        # 3. Crowded section within a ride: step off before it, walk around, re-board the same line.
        for n, leg in enumerate(transit):
            split = self._split_around_crowd(leg, threshold, max_walk)
            if split is not None:
                variants.append((*rides[:n], *split, *rides[n + 1 :]))
        repaired = [self._repair(v) for v in variants if all(r.board < r.alight for r in v)]
        return [v for v in repaired if v is not None]

    def _repair(self, rides: tuple[Ride, ...]) -> tuple[Ride, ...] | None:
        """Re-time later rides (same line, same stops) when an earlier change made the connection impossible."""
        out: list[Ride] = []
        for ride in rides:
            if out:
                prev = out[-1]
                prev_trip = self.index.trips[prev.trip_id]
                trip = self.index.trips[ride.trip_id]
                a = self.index.stops[prev_trip.stop_ids[prev.alight]]
                b = self.index.stops[trip.stop_ids[ride.board]]
                ready = prev_trip.arrivals[prev.alight] + prev.offset + max(
                    self.settings.min_transfer_seconds, self._walk_s(haversine_m(a.lat, a.lon, b.lat, b.lon))
                )
                if trip.departures[ride.board] + ride.offset < ready:
                    replacement = self._next_trip(ride, ready)
                    if replacement is None:
                        return None
                    ride = replacement
            out.append(ride)
        return tuple(out)

    def _next_trip(self, ride: Ride, ready: int) -> Ride | None:
        trip = self.index.trips[ride.trip_id]
        board_stop, alight_stop = trip.stop_ids[ride.board], trip.stop_ids[ride.alight]
        services = self.index.active_services((self._midnight + timedelta(seconds=ride.offset)).date())
        start = ready - ride.offset
        for _, trip_id, pos in self.index.departures_between(board_stop, start, start + 60 * 60):
            cand = self.index.trips[trip_id]
            if cand.route_id != trip.route_id or cand.service_id not in services:
                continue
            if alight_stop in cand.stop_ids[pos + 1 :]:
                return Ride(trip_id, pos, cand.stop_ids.index(alight_stop, pos + 1), ride.offset)
        return None

    def _split_around_crowd(self, leg: Leg, threshold: float, max_walk: float) -> tuple[Ride, ...] | None:
        vals = [round(value * 100) / 100 for value in leg.crowd.stop_values]
        ride, trip = leg.ride, leg.trip
        assert ride is not None and trip is not None
        crowded = [
            k for k in range(1, len(vals) - 1)
            if (leg.calls[k].stop.stop_id in self._blocked if self._blocked else vals[k] > threshold)
        ]
        if not crowded:
            return None
        off, on = crowded[0] - 1, crowded[-1] + 1  # last quiet stop before / first stop after the crowded section
        if off < 1:
            return None  # would mean not riding at all before the crowded section
        off_stop, on_stop = leg.calls[off].stop, leg.calls[on].stop
        dist = haversine_m(off_stop.lat, off_stop.lon, on_stop.lat, on_stop.lon)
        if dist > max_walk:
            return None
        first_part = Ride(trip.trip_id, ride.board, ride.board + off, ride.offset)
        if on == len(vals) - 1:
            # The crowded section reaches the end of the ride: get off early and walk the rest.
            return (first_part,)
        if (not self._blocked and vals[on] > threshold) or on_stop.stop_id in self._blocked:
            return None
        ready = trip.arrivals[ride.board + off] + self._walk_s(dist) + 60
        target_stop = trip.stop_ids[ride.alight]
        services = self.index.active_services((self._midnight + timedelta(seconds=ride.offset)).date())
        for _, trip2_id, pos2 in self.index.departures_between(on_stop.stop_id, ready, ready + 45 * 60):
            trip2 = self.index.trips[trip2_id]
            if trip2.route_id != trip.route_id or trip2.service_id not in services:
                continue
            if target_stop in trip2.stop_ids[pos2 + 1 :]:
                alight2 = trip2.stop_ids.index(target_stop, pos2 + 1)
                return (first_part, Ride(trip2_id, pos2, alight2, ride.offset))
        return None


def _select_candidates(index: TransitIndex, candidates, arrival_of) -> list[tuple[Ride, ...]]:
    """Deduplicate by travel pattern and drop options much slower than the fastest one."""
    if not candidates:
        return []
    unique = list(dict.fromkeys(candidates))
    best_arrival = min(arrival_of(c) for c in unique)
    by_pattern: dict[tuple, list[tuple[int, int, tuple[Ride, ...]]]] = defaultdict(list)
    for rides in unique:
        arr = arrival_of(rides)
        if arr > best_arrival + 45 * 60:
            continue
        pattern = tuple(
            (index.trips[r.trip_id].route_id, index.trips[r.trip_id].stop_ids[r.board], index.trips[r.trip_id].stop_ids[r.alight])
            for r in rides
        )
        first = rides[0]
        dep = index.trips[first.trip_id].departures[first.board] + first.offset
        by_pattern[pattern].append((arr, -dep, rides))
    selected: list[tuple[Ride, ...]] = []
    for options in by_pattern.values():
        options.sort(key=lambda option: (option[0], option[1]))
        seen_arrivals: set[int] = set()
        for arr, _, rides in options:
            if arr in seen_arrivals:
                continue
            seen_arrivals.add(arr)
            selected.append(rides)
            if len(seen_arrivals) >= DEPARTURES_PER_PATTERN:
                break
    # Keep the search bounded: prefer fewer transfers then earlier arrival
    selected.sort(key=lambda r: (len(r), arrival_of(r)))
    return selected[:60]
