"""Database tables: GTFS static timetable (subset needed for routing) and local crowd events."""

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class TransitFeed(Base):
    __tablename__ = "transit_feed"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    start_date: Mapped[str | None] = mapped_column(String(8), nullable=True)
    end_date: Mapped[str | None] = mapped_column(String(8), nullable=True)


class TransitAttribution(Base):
    __tablename__ = "transit_attribution"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_name: Mapped[str] = mapped_column(String(255))
    attribution_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)


class Agency(Base):
    __tablename__ = "gtfs_agency"
    agency_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agency_name: Mapped[str] = mapped_column(String(255))
    agency_timezone: Mapped[str] = mapped_column(String(64), default="Europe/Warsaw")


class Stop(Base):
    __tablename__ = "gtfs_stops"
    stop_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stop_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stop_name: Mapped[str] = mapped_column(String(255))
    stop_lat: Mapped[float] = mapped_column(Float)
    stop_lon: Mapped[float] = mapped_column(Float)


class Route(Base):
    __tablename__ = "gtfs_routes"
    route_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agency_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    route_short_name: Mapped[str] = mapped_column(String(64), default="")
    route_long_name: Mapped[str] = mapped_column(String(512), default="")
    route_type: Mapped[int] = mapped_column(Integer, default=3)
    route_color: Mapped[str | None] = mapped_column(String(16), nullable=True)


class Trip(Base):
    __tablename__ = "gtfs_trips"
    trip_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    route_id: Mapped[str] = mapped_column(String(64), index=True)
    service_id: Mapped[str] = mapped_column(String(64), index=True)
    trip_headsign: Mapped[str] = mapped_column(String(255), default="")
    direction_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shape_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class StopTime(Base):
    __tablename__ = "gtfs_stop_times"
    trip_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    stop_sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    stop_id: Mapped[str] = mapped_column(String(64), index=True)
    # Seconds after midnight of the service day (may exceed 24h as allowed by GTFS)
    arrival_time: Mapped[int] = mapped_column(Integer)
    departure_time: Mapped[int] = mapped_column(Integer)


class Calendar(Base):
    __tablename__ = "gtfs_calendar"
    service_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    monday: Mapped[int] = mapped_column(Integer)
    tuesday: Mapped[int] = mapped_column(Integer)
    wednesday: Mapped[int] = mapped_column(Integer)
    thursday: Mapped[int] = mapped_column(Integer)
    friday: Mapped[int] = mapped_column(Integer)
    saturday: Mapped[int] = mapped_column(Integer)
    sunday: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[str] = mapped_column(String(8))
    end_date: Mapped[str] = mapped_column(String(8))


class CalendarDate(Base):
    __tablename__ = "gtfs_calendar_dates"
    service_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    date: Mapped[str] = mapped_column(String(8), primary_key=True)
    exception_type: Mapped[int] = mapped_column(Integer)


class ShapePoint(Base):
    __tablename__ = "gtfs_shapes"
    shape_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    shape_pt_sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    shape_pt_lat: Mapped[float] = mapped_column(Float)
    shape_pt_lon: Mapped[float] = mapped_column(Float)


class LocalEvent(Base):
    """A large local event (concert, match, fair...) that attracts crowds."""

    __tablename__ = "local_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    venue: Mapped[str] = mapped_column(String(255), default="")
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expected_attendance: Mapped[int] = mapped_column(Integer, default=1000)
    radius_m: Mapped[float] = mapped_column(Float, default=1000.0)

    __table_args__ = (Index("ix_local_events_time", "starts_at", "ends_at"),)
