"""Dissolved crowd-band polygons in a local metric projection of Tricity."""

import math
from dataclasses import dataclass
from datetime import datetime

from shapely import get_parts, set_precision, union_all
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from ..schemas import CrowdAreaOut
from ..cities import City, PROFILES
from .estimator import CrowdEstimator
from .features import extract, point_crowd

TRICITY_BOUNDS = (54.25, 18.30, 54.65, 18.95)
MAX_GRID_CELLS = 1000
LAT_M = 111_195.0
LON_M = LAT_M * math.cos(math.radians(54.45))
BAND_NAMES = ("Low", "Moderate", "High", "Very high")
GEOMETRY_PRECISION_M = 0.01


def _xy(lat: float, lon: float, city: City = City.GDANSK) -> tuple[float, float]:
    south, west, _, _ = PROFILES[city].bounds
    lon_m = LON_M if city == City.GDANSK else LAT_M * math.cos(math.radians(PROFILES[city].centre[0]))
    return (lon - west) * lon_m, (lat - south) * LAT_M


def _latlon(x: float, y: float, city: City = City.GDANSK) -> tuple[float, float]:
    south, west, _, _ = PROFILES[city].bounds
    lon_m = LON_M if city == City.GDANSK else LAT_M * math.cos(math.radians(PROFILES[city].centre[0]))
    return south + y / LAT_M, west + x / lon_m


@dataclass
class AreaSample:
    name: str
    kind: str
    footprint: Polygon
    crowd_index: int
    reasons: list[str]


def merge_areas(samples: list[AreaSample], corridor: BaseGeometry | None = None, city: City = City.GDANSK) -> list[CrowdAreaOut]:
    """Union matching bands, subtract higher bands, and retain each connected polygon."""
    areas: list[CrowdAreaOut] = []
    covered: BaseGeometry = set_precision(Polygon(), GEOMETRY_PRECISION_M)
    for band in range(3, -1, -1):
        members = [s for s in samples if min(3, s.crowd_index // 25) == band]
        # Snap in metres before overlay operations to avoid sub-pixel slivers on export.
        merged = union_all([set_precision(s.footprint, GEOMETRY_PRECISION_M) for s in members]).difference(covered)
        covered = union_all([covered, merged])
        if band < 2 and corridor is not None:
            merged = merged.intersection(set_precision(corridor, GEOMETRY_PRECISION_M))
        polygons = [part for part in get_parts(merged) if isinstance(part, Polygon)]
        for i, polygon in enumerate(polygons):
            if polygon.is_empty or polygon.area < 1:
                continue
            sources = [s for s in members if s.footprint.intersection(polygon).area > 0]
            if not sources:
                continue
            centre = polygon.centroid
            lat, lon = _latlon(centre.x, centre.y, city)
            west, south, east, north = polygon.bounds
            reasons = sorted({reason for s in sources for reason in s.reasons})
            areas.append(CrowdAreaOut(
                id=f"band-{band}-{i}",
                name=sources[0].name if len(sources) == 1 else f"{BAND_NAMES[band]} crowd area",
                kind=sources[0].kind if len({s.kind for s in sources}) == 1 else "area",
                lat=lat, lon=lon, radius_m=math.hypot(east - west, north - south) / 2,
                crowd_index=max(s.crowd_index for s in sources), reasons=reasons,
                geometry=[
                    [_latlon(x, y, city) for x, y in ring.coords]
                    for ring in [polygon.exterior, *polygon.interiors]
                ],
            ))
    return areas


def predict_areas(
    estimator: CrowdEstimator, when: datetime, bounds: tuple[float, float, float, float],
    route_geometry: list[tuple[float, float]] | None = None,
) -> list[CrowdAreaOut]:
    samples: list[AreaSample] = []
    city = estimator.index.city

    def add(name: str, kind: str, lat: float, lon: float, footprint: Polygon, load: float = 0.0) -> None:
        features = extract(lat, lon, when, estimator.hotspots, estimator.events, load)
        samples.append(AreaSample(name, kind, footprint, int(round(point_crowd(features) * 100)), features.reasons))

    for hs in estimator.hotspots:
        add(hs.name, hs.kind, hs.lat, hs.lon, Point(_xy(hs.lat, hs.lon, city)).buffer(hs.radius_m, quad_segs=8))
    for event in estimator.events:
        add(event.name, "event", event.lat, event.lon, Point(_xy(event.lat, event.lon, city)).buffer(event.radius_m, quad_segs=8))
    for stop in estimator.index.stops.values():
        features = estimator.stop_features(stop, when)
        if point_crowd(features) >= 0.5:
            add(stop.name, "stop", stop.lat, stop.lon, Point(_xy(stop.lat, stop.lon, city)).buffer(120, quad_segs=8), features.stop_load)

    south, west, north, east = bounds
    s, w, n, e = PROFILES[city].bounds
    south, west, north, east = max(s, south), max(w, west), min(n, north), min(e, east)
    if south < north and west < east:
        x0, y0 = _xy(south, west, city)
        x1, y1 = _xy(north, east, city)
        height, width = y1 - y0, x1 - x0
        spacing = max(250.0, math.sqrt(height * width / MAX_GRID_CELLS) * 1.1)
        rows, cols = max(1, math.ceil(height / spacing)), max(1, math.ceil(width / spacing))
        xs = [x0 + col * width / cols for col in range(cols + 1)]
        ys = [y0 + row * height / rows for row in range(rows + 1)]
        for row in range(rows):
            for col in range(cols):
                footprint = box(xs[col], ys[row], xs[col + 1], ys[row + 1])
                lat, lon = _latlon(footprint.centroid.x, footprint.centroid.y, city)
                add("Estimated area crowd", "area", lat, lon, footprint)
    corridor: BaseGeometry | None = None
    if route_geometry is not None:
        points = [_xy(lat, lon, city) for lat, lon in route_geometry]
        corridor = LineString(points).buffer(500) if len(points) > 1 else Point(points[0]).buffer(500) if points else Polygon()
    return merge_areas(samples, corridor, city)
