"""Download and prepare isolated city timetables on first use."""

import io
import logging
import re
import threading
import zipfile
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from . import db, models
from .cities import City, CityProfile, city_profile
from .config import get_settings
from .gtfs_static import _rows, download_gtfs, load_gtfs_zip
from .schemas import AttributionOut, CityStatusOut
from .transit_index import get_index, reset_index

log = logging.getLogger(__name__)
_locks = {city: threading.Lock() for city in City}


class _DownloadLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str, str]] = []
        self._text: list[str] = []
        self._href = ""

    def handle_starttag(self, tag, attrs) -> None:
        if tag == "tr":
            self._text, self._href = [], ""
        if tag == "a":
            href = dict(attrs).get("href", "")
            if re.fullmatch(r"/hdb/download/\d+/", href):
                self._href = href

    def handle_data(self, data) -> None:
        self._text.append(data)

    def handle_endtag(self, tag) -> None:
        if tag == "tr" and self._href:
            self.rows.append((" ".join(self._text), self._href))


def download_city_gtfs(profile: CityProfile) -> bytes:
    if profile.static_url:
        return download_gtfs(profile.static_url)
    with httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": get_settings().user_agent}) as client:
        listing = client.get(profile.listing_url)
        listing.raise_for_status()
    links = _DownloadLinks()
    links.feed(listing.text)
    candidates = []
    for text, href in links.rows:
        match = re.search(r"GTFS_(\d{8})", text)
        if match:
            version = datetime.strptime(match[1], "%d%m%Y")
            candidates.append((version, href))
    today = datetime.now(ZoneInfo(get_settings().timezone)).strftime("%Y%m%d")
    for _, href in sorted(candidates, reverse=True)[:8]:
        data = download_gtfs(urljoin(profile.listing_url, href))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = next(_rows(archive, "feed_info.txt"), {})
        start, end = info.get("feed_start_date", ""), info.get("feed_end_date", "")
        if start and end and start <= today <= end:
            return data
        log.info("Skipping %s timetable outside current validity (%s-%s)", profile.name, start, end)
    raise ValueError(f"No currently valid timetable was found for {profile.name}")


def prepare_city(city: City) -> CityStatusOut:
    profile = city_profile(city)
    with _locks[city]:
        db.create_all(city)
        with db.session_scope(city) as session:
            feed = session.get(models.TransitFeed, 1)
            today = datetime.now(ZoneInfo(get_settings().timezone)).strftime("%Y%m%d")
            has_stops = session.scalar(select(models.Stop.stop_id).limit(1)) is not None
            has_trips = session.scalar(select(models.Trip.trip_id).limit(1)) is not None
            expired = feed is not None and feed.end_date is not None and feed.end_date < today
            if not has_stops or not has_trips or expired:
                data = download_city_gtfs(profile)
                load_gtfs_zip(session, data)
                reset_index(session.get_bind())
                feed = session.get(models.TransitFeed, 1)
            index = get_index(session)
            if not index.stops or not index.trips:
                raise ValueError(f"The {profile.name} timetable has no usable journeys")
            imported_at = feed.imported_at if feed else None
            if imported_at is not None and imported_at.tzinfo is None:
                imported_at = imported_at.replace(tzinfo=UTC)
            return CityStatusOut(
                city=city, name=profile.name, agency=profile.agency, source_url=profile.source_url,
                license=profile.license, ready=True,
                imported_at=imported_at,
                start_date=feed.start_date if feed else None, end_date=feed.end_date if feed else None,
                realtime_supported=profile.realtime_supported and get_settings().gtfs_rt_enabled,
                attributions=[
                    AttributionOut(organization_name=row.organization_name, attribution_url=row.attribution_url)
                    for row in session.scalars(select(models.TransitAttribution).order_by(models.TransitAttribution.id))
                ],
            )
