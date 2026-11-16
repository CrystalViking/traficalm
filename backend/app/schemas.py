"""API response/request models."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from .cities import City
from .config import DEFAULT_CROWD_TOLERANCE

LayaStatus = Literal["used", "partial", "disabled", "unavailable", "not-needed"]
TrackingStatus = Literal["live", "no-position", "unavailable", "stale", "not-current-service"]


class AddressSuggestion(BaseModel):
    label: str
    lat: float
    lon: float
    type: str | None = None


class PlaceOut(BaseModel):
    name: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class StopCallOut(BaseModel):
    stop_id: str
    name: str
    lat: float
    lon: float
    stop_sequence: int
    scheduled_arrival: datetime
    scheduled_departure: datetime
    realtime_arrival: datetime | None = None
    realtime_departure: datetime | None = None
    delay_seconds: int | None = None
    crowd_index: int = Field(ge=0, le=100)


class RouteOut(BaseModel):
    route_id: str
    short_name: str
    long_name: str
    mode: str
    color: str | None = None


class LegOut(BaseModel):
    mode: str
    start: PlaceOut
    end: PlaceOut
    departure: datetime
    arrival: datetime
    duration_seconds: int
    distance_m: int
    geometry: list[tuple[float, float]]
    crowd_index: int = Field(ge=0, le=100)
    crowd_reasons: list[str] = []
    crowd_rule_based: int | None = None
    crowd_laya: int | None = None
    route: RouteOut | None = None
    trip_id: str | None = None
    headsign: str | None = None
    service_date: date | None = None
    stops: list[StopCallOut] = []
    walk_routed: bool = False


class ItineraryOut(BaseModel):
    id: str
    crowd_index: int = Field(ge=0, le=100)
    crowd_level: str
    crowd_avoiding: bool
    departure: datetime
    arrival: datetime
    duration_seconds: int
    transfers: int
    walk_distance_m: int
    type: str  # walk | direct | transfer
    legs: list[LegOut]
    notes: list[str] = []


class PlanResponse(BaseModel):
    city: City = City.GDANSK
    origin: PlaceOut
    destination: PlaceOut
    departure: datetime
    laya_enabled: bool
    itineraries: list[ItineraryOut]
    crowd_tolerance: int = Field(default=DEFAULT_CROWD_TOLERANCE, ge=0, le=100)
    avoided_stop_ids: list[str] = []
    laya_requested: bool = False
    laya_status: LayaStatus = "disabled"
    laya_message: str | None = None


class NearbyStopOut(PlaceOut):
    stop_id: str
    distance_m: float
    crowd_index: int = Field(ge=0, le=100)


class RideSelection(BaseModel):
    trip_id: str = Field(min_length=1, max_length=255)
    board_sequence: int
    alight_sequence: int
    service_date: date


class ContinueJourneyRequest(BaseModel):
    city: City = City.GDANSK
    origin: PlaceOut
    destination: PlaceOut
    departure: datetime
    prefix_rides: list[RideSelection] = Field(min_length=1, max_length=10)
    exit_stop_id: str = Field(min_length=1, max_length=255)
    boarding_stop_id: str = Field(min_length=1, max_length=255)
    avoid_stop_ids: list[str] = Field(default_factory=list, max_length=20)
    crowd_tolerance: int = Field(default=DEFAULT_CROWD_TOLERANCE, ge=0, le=100)
    use_laya: bool = False
    strict_walking: bool = False
    walk_radius_m: int = Field(default=500, ge=100, le=20000)


class CrowdAreaOut(BaseModel):
    id: str
    name: str
    kind: str
    lat: float
    lon: float
    radius_m: float
    crowd_index: int = Field(ge=0, le=100)
    reasons: list[str]
    geometry: list[list[tuple[float, float]]]


class CrowdAreasResponse(BaseModel):
    at: datetime
    areas: list[CrowdAreaOut]
    estimated: bool = True


class CrowdAreasRequest(BaseModel):
    at: datetime | None = None
    south: float = Field(default=54.25, ge=-90, le=90)
    west: float = Field(default=18.30, ge=-180, le=180)
    north: float = Field(default=54.65, ge=-90, le=90)
    east: float = Field(default=18.95, ge=-180, le=180)
    route_geometry: list[tuple[
        Annotated[float, Field(ge=-90, le=90)],
        Annotated[float, Field(ge=-180, le=180)],
    ]] = Field(default_factory=list, max_length=20_000)


class CrowdChangeOut(PlaceOut):
    current_index: int
    predicted_index: int
    change: int


class CrowdFlowOut(BaseModel):
    origin: PlaceOut
    destination: PlaceOut
    change_points: int


class CrowdForecastResponse(CrowdAreasResponse):
    until: datetime
    changes: list[CrowdChangeOut]
    flows: list[CrowdFlowOut]


class VehicleForecastRequest(BaseModel):
    at: datetime
    rides: list[RideSelection] = Field(max_length=10)


class VehiclePredictionOut(PlaceOut):
    trip_id: str
    at: datetime


class VehicleForecastResponse(BaseModel):
    at: datetime
    until: datetime
    predictions: list[VehiclePredictionOut]
    estimated: bool = True


class StopDelayOut(BaseModel):
    stop_id: str
    stop_sequence: int
    delay_seconds: int | None
    realtime_arrival: datetime | None
    realtime_departure: datetime | None


class VehicleOut(BaseModel):
    trip_id: str | None
    route_id: str | None
    vehicle_id: str | None
    label: str | None
    lat: float
    lon: float
    bearing: float | None
    timestamp: int | None


class TripRealtimeOut(BaseModel):
    trip_id: str
    has_realtime: bool
    stops: list[StopDelayOut]
    vehicle: VehicleOut | None
    tracking_status: TrackingStatus = "no-position"
    tracking_message: str | None = None


class EventIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    venue: str = Field(default="", max_length=255)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    starts_at: datetime
    ends_at: datetime
    expected_attendance: int = Field(default=1000, ge=0, le=1_000_000)
    radius_m: float = Field(default=1000.0, gt=0, le=20_000)


class EventOut(EventIn):
    id: int


class HealthOut(BaseModel):
    city: City = City.GDANSK
    status: str
    stops: int
    routes: int
    trips: int
    laya_enabled: bool
    realtime_enabled: bool


class AttributionOut(BaseModel):
    organization_name: str
    attribution_url: str | None


class CityStatusOut(BaseModel):
    city: City
    name: str
    agency: str
    source_url: str
    license: str
    ready: bool
    imported_at: datetime | None
    start_date: str | None
    end_date: str | None
    realtime_supported: bool
    attributions: list[AttributionOut] = Field(default_factory=list)
