"""REST API routes."""

from __future__ import annotations

import hmac
import logging
import zipfile
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import geocoding, models
from .config import DEFAULT_CROWD_TOLERANCE, get_settings
from .cities import City, city_profile
from .city_data import prepare_city
from .crowd.estimator import CrowdEstimator
from .crowd.areas import predict_areas
from .crowd.features import EventInfo
from .crowd.laya_model import laya_client
from .db import get_session
from .geo import haversine_m
from .forecast import crowd_forecast, vehicle_predictions
from .planner import InvalidJourneyEdit, Itinerary, Leg, Place, Planner, Ride
from .realtime import RealtimeService, delay_at_stop, get_realtime
from .schemas import (
    AddressSuggestion,
    CrowdAreasResponse,
    CrowdAreasRequest,
    LayaStatus,
    EventIn,
    EventOut,
    HealthOut,
    ItineraryOut,
    LegOut,
    PlaceOut,
    PlanResponse,
    ContinueJourneyRequest,
    NearbyStopOut,
    CrowdForecastResponse,
    VehicleForecastRequest,
    VehicleForecastResponse,
    TrackingStatus,
    RouteOut,
    StopCallOut,
    StopDelayOut,
    TripRealtimeOut,
    VehicleOut,
    CityStatusOut,
)
from .transit_index import TransitIndex, TripInfo, get_index

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)

SessionDep = Annotated[Session, Depends(get_session)]


def index_dep(session: SessionDep) -> TransitIndex:
    return get_index(session)


IndexDep = Annotated[TransitIndex, Depends(index_dep)]
RealtimeDep = Annotated[RealtimeService, Depends(get_realtime)]


def ready_index_dep(index: IndexDep) -> TransitIndex:
    if not index.stops or not index.trips:
        raise HTTPException(503, "The selected city's timetable is not ready; prepare the city first")
    return index


ReadyIndexDep = Annotated[TransitIndex, Depends(ready_index_dep)]


def _check_city_location(city: City, lat: float, lon: float) -> None:
    south, west, north, east = city_profile(city).bounds
    if not (south <= lat <= north and west <= lon <= east):
        raise HTTPException(422, "The selected location is outside the selected city region")


@router.post("/cities/{city}/prepare", response_model=CityStatusOut)
def prepare_city_data(city: City) -> CityStatusOut:
    try:
        return prepare_city(city)
    except (httpx.HTTPError, zipfile.BadZipFile, ValueError, KeyError) as exc:
        log.exception("Cannot prepare %s timetable", city)
        raise HTTPException(502, f"Could not prepare the {city_profile(city).name} timetable") from exc


def _service_matches(actual: date | None, requested: date, trip: TripInfo, now: datetime) -> bool:
    if actual is not None:
        return actual == requested
    midnight = datetime.combine(requested, datetime.min.time(), tzinfo=now.tzinfo)
    return (
        midnight + timedelta(seconds=trip.departures[0], minutes=-15)
        <= now <= midnight + timedelta(seconds=trip.arrivals[-1], minutes=60)
    )


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def load_events(session: Session, start: datetime, end: datetime) -> list[EventInfo]:
    # Events matter from 2h before they start until 90 min after they end.
    rows = session.execute(
        select(models.LocalEvent).where(
            models.LocalEvent.starts_at <= _utc(end) + timedelta(hours=2),
            models.LocalEvent.ends_at >= _utc(start) - timedelta(minutes=90),
        )
    ).scalars()
    return [
        EventInfo(e.name, e.lat, e.lon, _utc(e.starts_at), _utc(e.ends_at), e.expected_attendance, e.radius_m)
        for e in rows
    ]


@router.get("/health", response_model=HealthOut)
def health(index: IndexDep) -> HealthOut:
    s = get_settings()
    return HealthOut(
        city=index.city, status="ok" if index.stops else "no-timetable",
        stops=len(index.stops),
        routes=len(index.routes),
        trips=len(index.trips),
        laya_enabled=laya_client.enabled,
        realtime_enabled=s.gtfs_rt_enabled and city_profile(index.city).realtime_supported,
    )


@router.get("/geocode/search", response_model=list[AddressSuggestion])
def geocode_search(q: Annotated[str, Query(min_length=3, max_length=200)], limit: int = Query(6, ge=1, le=10), city: City = City.GDANSK):
    try:
        return geocoding.search(q, limit, city)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        log.warning("Address search unavailable for %s (%s)", city, type(exc).__name__)
        raise HTTPException(502, "Address service unavailable") from exc


@router.get("/geocode/reverse", response_model=AddressSuggestion | None)
def geocode_reverse(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180), city: City = City.GDANSK):
    _check_city_location(city, lat, lon)
    try:
        return geocoding.reverse(lat, lon)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Address service unavailable") from exc


def _crowd_level(value: int) -> str:
    if value < 25:
        return "low"
    if value < 50:
        return "moderate"
    if value < 75:
        return "high"
    return "very high"


def _leg_out(leg: Leg) -> LegOut:
    out = LegOut(
        mode=leg.mode,
        start=PlaceOut(name=leg.start.name, lat=leg.start.lat, lon=leg.start.lon),
        end=PlaceOut(name=leg.end.name, lat=leg.end.lat, lon=leg.end.lon),
        departure=leg.departure,
        arrival=leg.arrival,
        duration_seconds=int(leg.duration_s),
        distance_m=int(round(leg.distance_m)),
        geometry=[(round(lat, 6), round(lon, 6)) for lat, lon in leg.geometry],
        crowd_index=leg.crowd.percent,
        crowd_reasons=leg.crowd.reasons,
        crowd_rule_based=int(round(leg.crowd.rule_value * 100)),
        crowd_laya=int(round(leg.crowd.laya_value * 100)) if leg.crowd.laya_value is not None else None,
        walk_routed=leg.walk_routed,
    )
    if leg.mode == "transit" and leg.trip is not None:
        return out.model_copy(update={
            "trip_id": leg.trip.trip_id,
            "headsign": leg.trip.headsign,
            "service_date": leg.service_date,
            "route": _route_out(leg),
            "stops": [
                StopCallOut(
                    stop_id=c.stop.stop_id,
                    name=c.stop.name,
                    lat=c.stop.lat,
                    lon=c.stop.lon,
                    stop_sequence=c.stop_sequence,
                    scheduled_arrival=c.arrival,
                    scheduled_departure=c.departure,
                    realtime_arrival=c.rt_arrival,
                    realtime_departure=c.rt_departure,
                    delay_seconds=c.delay_s,
                    crowd_index=int(round(c.crowd * 100)),
                )
                for c in leg.calls
            ],
        })
    return out


def _route_out(leg: Leg) -> RouteOut | None:
    r = leg.route
    if r is None:
        return None
    return RouteOut(route_id=r.route_id, short_name=r.short_name, long_name=r.long_name, mode=r.mode, color=r.color)


def _itinerary_out(it: Itinerary) -> ItineraryOut:
    kind = "walk" if not it.rides else "direct" if len(it.rides) == 1 else "transfer"
    return ItineraryOut(
        id=it.id,
        crowd_index=it.crowd_index,
        crowd_level=_crowd_level(it.crowd_index),
        crowd_avoiding=it.crowd_avoiding,
        departure=it.departure,
        arrival=it.arrival,
        duration_seconds=int((it.arrival - it.departure).total_seconds()),
        transfers=it.transfers,
        walk_distance_m=int(round(it.walk_distance_m)),
        type=kind,
        legs=[_leg_out(leg) for leg in it.legs],
        notes=it.notes,
    )


@router.get("/plan", response_model=PlanResponse)
def plan(
    session: SessionDep,
    index: ReadyIndexDep,
    realtime: RealtimeDep,
    from_lat: float = Query(ge=-90, le=90),
    from_lon: float = Query(ge=-180, le=180),
    to_lat: float = Query(ge=-90, le=90),
    to_lon: float = Query(ge=-180, le=180),
    from_name: str = Query("Start", max_length=300),
    to_name: str = Query("Destination", max_length=300),
    departure: datetime | None = None,
    max_results: int = Query(10, ge=1, le=30),
    crowd_tolerance: int = Query(DEFAULT_CROWD_TOLERANCE, ge=0, le=100),
    avoid_stop_ids: Annotated[list[str] | None, Query(max_length=20)] = None,
    use_laya: bool = False,
    strict_walking: bool = False,
    fastest: bool = False,
) -> PlanResponse:
    s = get_settings()
    tz = ZoneInfo(s.timezone)
    _check_city_location(index.city, from_lat, from_lon)
    _check_city_location(index.city, to_lat, to_lon)
    when = departure or datetime.now(tz)
    when = when.astimezone(tz) if when.tzinfo else when.replace(tzinfo=tz)
    avoided = frozenset(avoid_stop_ids or [])
    if avoided.difference(index.stops):
        raise HTTPException(422, "One or more avoided stops are unknown")
    events = load_events(session, when, when + timedelta(hours=4))
    estimator = CrowdEstimator(index, events, use_laya=use_laya)
    planner = Planner(
        index, estimator, realtime.trip_updates(),
        use_walk_router=s.walking_router_enabled, strict_walking=strict_walking,
                fastest=fastest,
    )
    origin, destination = Place(from_lat, from_lon, from_name), Place(to_lat, to_lon, to_name)
    itineraries = (planner.plan_strict if strict_walking and not fastest else planner.plan)(
        origin, destination, when, max_results=max_results,
        crowd_tolerance=crowd_tolerance, avoid_stop_ids=avoided,
    )
    return _plan_response(origin, destination, when, itineraries, crowd_tolerance, avoided, use_laya, index.city)


def _plan_response(
    origin: Place, destination: Place, when: datetime, itineraries: list[Itinerary],
    crowd_tolerance: int, avoided: frozenset[str], use_laya: bool, city: City,
) -> PlanResponse:
    transit = [leg for it in itineraries for leg in it.legs if leg.mode == "transit"]
    scored = sum(leg.crowd.laya_value is not None for leg in transit)
    laya_status: LayaStatus
    if not use_laya:
        laya_status = "disabled"
    elif not transit:
        laya_status = "not-needed"
    elif not scored:
        laya_status = "unavailable"
    elif scored < len(transit):
        laya_status = "partial"
    else:
        laya_status = "used"
    messages = {
        "unavailable": "Laya could not score this journey. These options use rule-based crowd estimates only.",
        "partial": "Laya scored only some rides. Other rides use rule-based crowd estimates.",
    }
    return PlanResponse(
        city=city,
        origin=PlaceOut(name=origin.name, lat=origin.lat, lon=origin.lon),
        destination=PlaceOut(name=destination.name, lat=destination.lat, lon=destination.lon),
        departure=when,
        laya_enabled=bool(scored),
        itineraries=[_itinerary_out(it) for it in itineraries],
        crowd_tolerance=crowd_tolerance,
        avoided_stop_ids=sorted(avoided),
        laya_requested=use_laya,
        laya_status=laya_status,
        laya_message=messages.get(laya_status),
    )


@router.get("/stops/nearby", response_model=list[NearbyStopOut])
def nearby_stops(
    session: SessionDep, index: ReadyIndexDep, stop_id: str,
    at: datetime | None = None,
    excluded_stop_ids: Annotated[list[str] | None, Query(max_length=20)] = None,
    radius_m: int = Query(500, ge=100, le=20000),
) -> list[NearbyStopOut]:
    excluded = set(excluded_stop_ids or [])
    if stop_id not in index.stops or excluded.difference(index.stops):
        raise HTTPException(422, "One or more selected stops are unknown")
    tz = ZoneInfo(get_settings().timezone)
    when = at or datetime.now(tz)
    when = when.astimezone(tz) if when.tzinfo else when.replace(tzinfo=tz)
    stop = index.stops[stop_id]
    estimator = CrowdEstimator(index, load_events(session, when, when + timedelta(hours=4)), use_laya=False)
    return [
        NearbyStopOut(
            stop_id=candidate.stop_id, name=candidate.name, lat=candidate.lat, lon=candidate.lon,
            distance_m=distance, crowd_index=int(round(estimator.stop_crowd(candidate, when) * 100)),
        )
        for candidate, distance in index.nearby_stops(stop.lat, stop.lon, radius_m)
        if candidate.stop_id != stop_id and candidate.stop_id not in excluded
    ]


@router.post("/plan/continue", response_model=PlanResponse)
def continue_journey(request: ContinueJourneyRequest, session: SessionDep, index: ReadyIndexDep, realtime: RealtimeDep) -> PlanResponse:
    if request.city != index.city:
        raise HTTPException(422, "The retained journey belongs to a different city")
    _check_city_location(index.city, request.origin.lat, request.origin.lon)
    _check_city_location(index.city, request.destination.lat, request.destination.lon)
    selected_ids = {request.exit_stop_id, request.boarding_stop_id, *request.avoid_stop_ids}
    if selected_ids.difference(index.stops):
        raise HTTPException(422, "One or more selected stops are unknown")
    exit_stop, boarding = index.stops[request.exit_stop_id], index.stops[request.boarding_stop_id]
    if boarding.stop_id == exit_stop.stop_id or boarding.stop_id in request.avoid_stop_ids:
        raise HTTPException(422, "Choose a different boarding stop that is not excluded")
    if haversine_m(exit_stop.lat, exit_stop.lon, boarding.lat, boarding.lon) > request.walk_radius_m:
        raise HTTPException(422, f"The boarding stop must be within {request.walk_radius_m / 1000:g} km of the exit stop")
    s = get_settings()
    tz = ZoneInfo(s.timezone)
    when = request.departure
    when = when.astimezone(tz) if when.tzinfo else when.replace(tzinfo=tz)
    rides = []
    for position, selection in enumerate(request.prefix_rides):
        trip = index.trips.get(selection.trip_id)
        if trip is None or trip.service_id not in index.active_services(selection.service_date):
            raise HTTPException(422, "A retained trip is unknown or inactive on its service date")
        if abs((selection.service_date - when.date()).days) > 1:
            raise HTTPException(422, "A retained trip has an invalid service date")
        try:
            board = trip.stop_sequences.index(selection.board_sequence)
            alight = trip.stop_sequences.index(selection.alight_sequence)
        except ValueError as exc:
            raise HTTPException(422, "A retained trip has an unknown stop sequence") from exc
        last = position == len(request.prefix_rides) - 1
        if board > alight or (board == alight and not last):
            raise HTTPException(422, "A retained trip has invalid boarding and alighting stops")
        if last and trip.stop_ids[alight] != request.exit_stop_id:
            raise HTTPException(422, "The retained journey must end at the selected exit stop")
        if board < alight:
            rides.append(Ride(trip.trip_id, board, alight, (selection.service_date - when.date()).days * 86400))
    estimator = CrowdEstimator(index, load_events(session, when, when + timedelta(hours=8)), use_laya=request.use_laya)
    planner = Planner(
        index, estimator, realtime.trip_updates(),
        use_walk_router=s.walking_router_enabled, strict_walking=request.strict_walking,
        fastest=request.fastest,
    )
    origin = Place(request.origin.lat, request.origin.lon, request.origin.name)
    destination = Place(request.destination.lat, request.destination.lon, request.destination.name)
    avoided = frozenset(request.avoid_stop_ids)
    try:
        itineraries = planner.continue_journey(
            origin, destination, when, tuple(rides), request.exit_stop_id, request.boarding_stop_id,
            request.crowd_tolerance, avoided,
        )
    except InvalidJourneyEdit as exc:
        raise HTTPException(422, str(exc)) from exc
    return _plan_response(origin, destination, when, itineraries, request.crowd_tolerance, avoided, request.use_laya, index.city)


@router.get("/crowd/areas", response_model=CrowdAreasResponse)
def crowd_areas(
    session: SessionDep, index: ReadyIndexDep,
    at: datetime | None = None,
    south: float | None = Query(None, ge=-90, le=90),
    west: float | None = Query(None, ge=-180, le=180),
    north: float | None = Query(None, ge=-90, le=90),
    east: float | None = Query(None, ge=-180, le=180),
) -> CrowdAreasResponse:
    defaults = city_profile(index.city).bounds
    bounds = tuple(value if value is not None else defaults[i] for i, value in enumerate((south, west, north, east)))
    return _crowd_areas_response(session, index, at, bounds)


@router.post("/crowd/areas", response_model=CrowdAreasResponse)
def route_crowd_areas(request: CrowdAreasRequest, session: SessionDep, index: ReadyIndexDep) -> CrowdAreasResponse:
    return _crowd_areas_response(
        session, index, request.at, (request.south, request.west, request.north, request.east), request.route_geometry,
    )


@router.post("/crowd/forecast", response_model=CrowdForecastResponse)
def forecast_crowds(request: CrowdAreasRequest, session: SessionDep, index: ReadyIndexDep) -> CrowdForecastResponse:
    if request.south >= request.north or request.west >= request.east:
        raise HTTPException(422, "Bounds must have south < north and west < east")
    tz = ZoneInfo(get_settings().timezone)
    when = request.at or datetime.now(tz)
    when = when.astimezone(tz) if when.tzinfo else when.replace(tzinfo=tz)
    estimator = CrowdEstimator(index, load_events(session, when, when + timedelta(hours=2)), use_laya=False)
    return crowd_forecast(
        estimator, when, (request.south, request.west, request.north, request.east), request.route_geometry,
    )


@router.post("/vehicles/forecast", response_model=VehicleForecastResponse)
def forecast_vehicles(request: VehicleForecastRequest, index: ReadyIndexDep, realtime: RealtimeDep) -> VehicleForecastResponse:
    tz = ZoneInfo(get_settings().timezone)
    at = request.at.astimezone(tz) if request.at.tzinfo else request.at.replace(tzinfo=tz)
    now = datetime.now(tz)
    updates = realtime.trip_updates()
    predictions = []
    for selection in request.rides:
        trip = index.trips.get(selection.trip_id)
        if trip is None or trip.service_id not in index.active_services(selection.service_date):
            raise HTTPException(422, "A forecast trip is unknown or inactive on its service date")
        try:
            board = trip.stop_sequences.index(selection.board_sequence)
            alight = trip.stop_sequences.index(selection.alight_sequence)
        except ValueError as exc:
            raise HTTPException(422, "A forecast trip has an unknown stop sequence") from exc
        if board >= alight:
            raise HTTPException(422, "A forecast trip has invalid boarding and alighting stops")
        update = updates.get(trip.trip_id)
        if update is not None and (
            not _service_matches(update.service_date, selection.service_date, trip, now)
            or update.timestamp is None or datetime.now(UTC).timestamp() - update.timestamp > 120
        ):
            update = None
        predictions += vehicle_predictions(index, trip, selection.service_date, board, alight, at, update)
    return VehicleForecastResponse(at=at, until=at + timedelta(minutes=60), predictions=predictions)


def _crowd_areas_response(
    session: Session, index: TransitIndex, at: datetime | None,
    bounds: tuple[float, float, float, float], route_geometry: list[tuple[float, float]] | None = None,
) -> CrowdAreasResponse:
    south, west, north, east = bounds
    if south >= north or west >= east:
        raise HTTPException(422, "Bounds must have south < north and west < east")
    tz = ZoneInfo(get_settings().timezone)
    when = at or datetime.now(tz)
    when = when.astimezone(tz) if when.tzinfo else when.replace(tzinfo=tz)
    events = load_events(session, when, when)
    estimator = CrowdEstimator(index, events)
    return CrowdAreasResponse(at=when, areas=predict_areas(estimator, when, bounds, route_geometry))


def _vehicle_out(v) -> VehicleOut:
    return VehicleOut(
        trip_id=v.trip_id, route_id=v.route_id, vehicle_id=v.vehicle_id, label=v.label,
        lat=v.lat, lon=v.lon, bearing=v.bearing, timestamp=v.timestamp,
    )


@router.get("/trips/{trip_id}/realtime", response_model=TripRealtimeOut)
def trip_realtime(trip_id: str, index: ReadyIndexDep, realtime: RealtimeDep, service_date: date | None = None) -> TripRealtimeOut:
    trip = index.trips.get(trip_id)
    if trip is None:
        raise HTTPException(404, "Unknown trip")
    tz = ZoneInfo(get_settings().timezone)
    now = datetime.now(tz)
    today = now.date()
    service_date = service_date or today
    midnight = datetime.combine(service_date, datetime.min.time(), tzinfo=tz)
    update = realtime.trip_updates().get(trip_id)
    if update is not None and not _service_matches(update.service_date, service_date, trip, now):
        update = None
    stops = []
    for p, stop_id in enumerate(trip.stop_ids):
        arr = midnight + timedelta(seconds=trip.arrivals[p])
        dep = midnight + timedelta(seconds=trip.departures[p])
        delay = delay_at_stop(update, trip.stop_sequences[p], stop_id, int(arr.timestamp()))
        stops.append(StopDelayOut(
            stop_id=stop_id,
            stop_sequence=trip.stop_sequences[p],
            delay_seconds=delay,
            realtime_arrival=arr + timedelta(seconds=delay) if delay is not None else None,
            realtime_departure=dep + timedelta(seconds=delay) if delay is not None else None,
        ))
    positions = [v for v in realtime.vehicle_positions() if v.trip_id == trip_id]
    matched = [
        v for v in positions if _service_matches(v.service_date, service_date, trip, now)
    ]
    vehicle = max(matched, key=lambda v: v.timestamp or 0, default=None)
    status: TrackingStatus = "no-position"
    message = "No current GPS position for this selected trip"
    if realtime.vehicle_error:
        vehicle, status, message = None, "unavailable", realtime.vehicle_error
    elif vehicle is not None:
        age = datetime.now(UTC).timestamp() - vehicle.timestamp if vehicle.timestamp is not None else None
        if age is None or age > 120 or age < -60:
            vehicle, status, message = None, "stale", "The reported vehicle position is stale or has no timestamp"
        else:
            status, message = "live", None
    elif positions or service_date != today:
        status, message = "not-current-service", "No current GPS position matching the selected service date"
    return TripRealtimeOut(
        trip_id=trip_id,
        has_realtime=update is not None,
        stops=stops,
        vehicle=_vehicle_out(vehicle) if vehicle else None,
        tracking_status=status,
        tracking_message=message,
    )


@router.get("/vehicles", response_model=list[VehicleOut])
def vehicles(realtime: RealtimeDep, trip_ids: Annotated[str, Query(max_length=4000)] = "") -> list[VehicleOut]:
    if not city_profile(realtime.city).realtime_supported:
        raise HTTPException(503, "Selected-trip vehicle positions are not published for this city")
    wanted = {t for t in trip_ids.split(",") if t}
    return [_vehicle_out(v) for v in realtime.vehicle_positions() if not wanted or v.trip_id in wanted]


# ---------------------------------------------------------------------------- local events
def require_admin(x_admin_token: Annotated[str | None, Header()] = None) -> None:
    token = get_settings().admin_token
    if not token:
        raise HTTPException(403, "Event management is disabled (set TRAFICALM_ADMIN_TOKEN)")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, token):
        raise HTTPException(401, "Invalid admin token")


def _event_out(e: models.LocalEvent) -> EventOut:
    return EventOut(
        id=e.id, name=e.name, venue=e.venue, lat=e.lat, lon=e.lon,
        starts_at=_utc(e.starts_at), ends_at=_utc(e.ends_at),
        expected_attendance=e.expected_attendance, radius_m=e.radius_m,
    )


@router.get("/events", response_model=list[EventOut])
def list_events(session: SessionDep, upcoming_only: bool = True) -> list[EventOut]:
    query = select(models.LocalEvent).order_by(models.LocalEvent.starts_at)
    if upcoming_only:
        query = query.where(models.LocalEvent.ends_at >= datetime.now(UTC) - timedelta(hours=2))
    return [_event_out(e) for e in session.execute(query).scalars()]


@router.post("/events", response_model=EventOut, status_code=201, dependencies=[Depends(require_admin)])
def create_event(event: EventIn, session: SessionDep) -> EventOut:
    if event.ends_at <= event.starts_at:
        raise HTTPException(422, "ends_at must be after starts_at")
    row = models.LocalEvent(**{**event.model_dump(), "starts_at": _utc(event.starts_at), "ends_at": _utc(event.ends_at)})
    session.add(row)
    session.commit()
    return _event_out(row)


@router.delete("/events/{event_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_event(event_id: int, session: SessionDep) -> None:
    row = session.get(models.LocalEvent, event_id)
    if row is None:
        raise HTTPException(404, "Unknown event")
    session.delete(row)
    session.commit()
