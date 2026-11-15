"""Import a static GTFS feed (e.g. ZTM Gdańsk ``gtfsgoogle.zip``) into the database."""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from . import models
from .config import get_settings

log = logging.getLogger(__name__)

CHUNK = 5000


def parse_gtfs_time(value: str) -> int:
    """Convert ``HH:MM:SS`` (hours may be >= 24) into seconds after midnight."""
    h, m, s = (int(part) for part in value.strip().split(":"))
    return h * 3600 + m * 60 + s


def _rows(zf: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    try:
        raw = zf.open(name)
    except KeyError:
        return
    with raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        for row in reader:
            yield {(k or "").strip(): (v or "").strip() for k, v in row.items()}


def _opt_int(value: str | None) -> int | None:
    return int(value) if value not in (None, "") else None


def _bulk(session: Session, table: Any, rows: Iterable[dict[str, Any]]) -> int:
    batch: list[dict[str, Any]] = []
    total = 0
    for row in rows:
        batch.append(row)
        if len(batch) >= CHUNK:
            session.execute(insert(table), batch)
            total += len(batch)
            batch = []
    if batch:
        session.execute(insert(table), batch)
        total += len(batch)
    return total


def _dedupe(rows: Iterable[dict[str, Any]], key: Callable[[dict[str, Any]], Any]) -> Iterator[dict[str, Any]]:
    seen: set[Any] = set()
    for row in rows:
        k = key(row)
        if k in seen:
            continue
        seen.add(k)
        yield row


def load_gtfs_zip(session: Session, data: bytes) -> dict[str, int]:
    """Replace the timetable tables with the content of a GTFS zip archive."""
    zf = zipfile.ZipFile(io.BytesIO(data))
    required = {"stops.txt", "routes.txt", "trips.txt", "stop_times.txt"}
    if not required.issubset(zf.namelist()):
        raise ValueError("The GTFS archive is missing required timetable files")
    tables = [
        models.TransitAttribution,
        models.StopTime, models.Trip, models.Route, models.Stop, models.Calendar,
        models.CalendarDate, models.ShapePoint, models.Agency,
    ]
    for table in tables:
        session.execute(delete(table))

    counts: dict[str, int] = {}
    counts["agency"] = _bulk(session, models.Agency, _dedupe((
        {
            "agency_id": r.get("agency_id") or "default",
            "agency_name": r.get("agency_name", ""),
            "agency_timezone": r.get("agency_timezone") or "Europe/Warsaw",
        }
        for r in _rows(zf, "agency.txt")
    ), lambda r: r["agency_id"]))
    counts["stops"] = _bulk(session, models.Stop, _dedupe((
        {
            "stop_id": r["stop_id"],
            "stop_code": r.get("stop_code") or None,
            "stop_name": r.get("stop_name", ""),
            "stop_lat": float(r["stop_lat"]),
            "stop_lon": float(r["stop_lon"]),
        }
        for r in _rows(zf, "stops.txt")
        if r.get("stop_lat") and r.get("stop_lon")
    ), lambda r: r["stop_id"]))
    counts["routes"] = _bulk(session, models.Route, _dedupe((
        {
            "route_id": r["route_id"],
            "agency_id": r.get("agency_id") or None,
            "route_short_name": r.get("route_short_name", ""),
            "route_long_name": r.get("route_long_name", ""),
            "route_type": int(r.get("route_type") or 3),
            "route_color": r.get("route_color") or None,
        }
        for r in _rows(zf, "routes.txt")
    ), lambda r: r["route_id"]))
    counts["trips"] = _bulk(session, models.Trip, _dedupe((
        {
            "trip_id": r["trip_id"],
            "route_id": r["route_id"],
            "service_id": r["service_id"],
            "trip_headsign": r.get("trip_headsign", ""),
            "direction_id": _opt_int(r.get("direction_id")),
            "shape_id": r.get("shape_id") or None,
        }
        for r in _rows(zf, "trips.txt")
    ), lambda r: r["trip_id"]))
    counts["stop_times"] = _bulk(session, models.StopTime, _dedupe((
        {
            "trip_id": r["trip_id"],
            "stop_sequence": int(r["stop_sequence"]),
            "stop_id": r["stop_id"],
            "arrival_time": parse_gtfs_time(r.get("arrival_time") or r["departure_time"]),
            "departure_time": parse_gtfs_time(r.get("departure_time") or r["arrival_time"]),
        }
        for r in _rows(zf, "stop_times.txt")
        if (r.get("arrival_time") or r.get("departure_time"))
    ), lambda r: (r["trip_id"], r["stop_sequence"])))
    counts["calendar"] = _bulk(session, models.Calendar, _dedupe((
        {
            "service_id": r["service_id"],
            **{day: int(r.get(day) or 0) for day in
               ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")},
            "start_date": r["start_date"],
            "end_date": r["end_date"],
        }
        for r in _rows(zf, "calendar.txt")
    ), lambda r: r["service_id"]))
    counts["calendar_dates"] = _bulk(session, models.CalendarDate, _dedupe((
        {"service_id": r["service_id"], "date": r["date"], "exception_type": int(r["exception_type"])}
        for r in _rows(zf, "calendar_dates.txt")
    ), lambda r: (r["service_id"], r["date"])))
    counts["shapes"] = _bulk(session, models.ShapePoint, _dedupe((
        {
            "shape_id": r["shape_id"],
            "shape_pt_sequence": int(r["shape_pt_sequence"]),
            "shape_pt_lat": float(r["shape_pt_lat"]),
            "shape_pt_lon": float(r["shape_pt_lon"]),
        }
        for r in _rows(zf, "shapes.txt")
    ), lambda r: (r["shape_id"], r["shape_pt_sequence"])))
    if any(not counts[name] for name in ("stops", "routes", "trips", "stop_times")):
        raise ValueError("The GTFS archive contains an empty timetable")
    info = next(_rows(zf, "feed_info.txt"), {})
    counts["attributions"] = _bulk(session, models.TransitAttribution, (
        {"organization_name": r["organization_name"], "attribution_url": r.get("attribution_url") or None}
        for r in _rows(zf, "attributions.txt")
    ))
    session.merge(models.TransitFeed(
        id=1, imported_at=datetime.now(UTC),
        start_date=info.get("feed_start_date") or None, end_date=info.get("feed_end_date") or None,
    ))
    session.commit()
    log.info("Imported GTFS: %s", counts)
    return counts


def download_gtfs(url: str | None = None) -> bytes:
    settings = get_settings()
    url = url or settings.gtfs_static_url
    log.info("Downloading GTFS from %s", url)
    with httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": settings.user_agent}) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.content
