"""Dissolved crowd-band polygons in a local metric projection of Tricity."""

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from shapely import get_parts, set_precision, union_all
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from ..schemas import CrowdAreaOut
from ..cities import City, PROFILES
from .estimator import CrowdEstimator
from .features import crowd_ceilings, extract, point_crowd

TRICITY_BOUNDS = (54.25, 18.30, 54.65, 18.95)
GRID_BASE_M = 250.0  # finest crowd grid cell; coarser grids double it, so their cells line up with finer ones
MAX_GRID_CELLS = 1500
STOP_RADIUS_M = 120.0
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


def grid_spacing(area_m2: float) -> float:
    """Cell size of the crowd grid over an area: GRID_BASE_M doubled until at most MAX_GRID_CELLS cells fit."""
    spacing = GRID_BASE_M
    while spacing < math.sqrt(area_m2 / MAX_GRID_CELLS) * 1.1:
        spacing *= 2
    return spacing


def collect_samples(
    estimator: CrowdEstimator, when: datetime, bounds: tuple[float, float, float, float],
    spacings: tuple[float, ...] | None = None, min_crowd: int = 0,
) -> list[AreaSample]:
    """Crowd footprints (hotspots, events, busy stops and a grid) behind the crowd map.

    Grid cells are squares of a city-wide lattice, so a place always falls in the same cell whatever the
    requested bounds; spacings overrides the cell sizes chosen for the bounds. With min_crowd only footprints at
    least that crowded are returned, and grid cells that cannot reach it are not evaluated.
    """
    samples: list[AreaSample] = []
    city = estimator.index.city

    def add(name: str, kind: str, lat: float, lon: float, footprint: Polygon, load: float = 0.0) -> None:
        features = extract(lat, lon, when, estimator.hotspots, estimator.events, load)
        samples.append(AreaSample(name, kind, footprint, int(round(point_crowd(features) * 100)), features.reasons))

    for hs in estimator.hotspots:
        add(hs.name, hs.kind, hs.lat, hs.lon, Point(_xy(hs.lat, hs.lon, city)).buffer(hs.radius_m, quad_segs=8))
    for event in estimator.events:
        add(event.name, "event", event.lat, event.lon, Point(_xy(event.lat, event.lon, city)).buffer(event.radius_m, quad_segs=8))
    south, west, north, east = bounds
    left, bottom = _xy(south, west, city)
    right, top = _xy(north, east, city)
    for stop in estimator.index.stops.values():
        x, y = _xy(stop.lat, stop.lon, city)
        if not (left - STOP_RADIUS_M <= x <= right + STOP_RADIUS_M and bottom - STOP_RADIUS_M <= y <= top + STOP_RADIUS_M):
            continue
        features = estimator.stop_features(stop, when)
        if point_crowd(features) >= 0.5:
            add(stop.name, "stop", stop.lat, stop.lon, Point(x, y).buffer(STOP_RADIUS_M, quad_segs=8), features.stop_load)

    s, w, n, e = PROFILES[city].bounds
    south, west, north, east = max(s, south), max(w, west), min(n, north), min(e, east)
    if south < north and west < east:
        x0, y0 = _xy(south, west, city)
        x1, y1 = _xy(north, east, city)
        hotspots_xy = np.array([_xy(hs.lat, hs.lon, city) for hs in estimator.hotspots]).reshape(-1, 2)
        events_xy = np.array([_xy(ev.lat, ev.lon, city) for ev in estimator.events]).reshape(-1, 2)
        for spacing in spacings or (grid_spacing((x1 - x0) * (y1 - y0)),):
            cols, rows = np.meshgrid(
                np.arange(math.floor(x0 / spacing), math.ceil(x1 / spacing)),
                np.arange(math.floor(y0 / spacing), math.ceil(y1 / spacing)),
            )
            cells = np.column_stack([cols.ravel(), rows.ravel()])
            if min_crowd > 0:
                # Skip cells that cannot reach min_crowd; distances are shortened to cover the planar projection.
                centres = (cells + 0.5) * spacing

                def gaps(points: np.ndarray) -> np.ndarray:
                    planar = np.hypot(*(centres[:, None, :] - points[None, :, :]).transpose(2, 0, 1))
                    return np.maximum(0.0, planar * 0.98 - 10)

                ceiling = crowd_ceilings(when, estimator.hotspots, estimator.events, gaps(hotspots_xy), gaps(events_xy))
                cells = cells[ceiling * 100 >= min_crowd - 0.5]
            for col, row in cells.tolist():
                cx, cy = (col + 0.5) * spacing, (row + 0.5) * spacing
                footprint = box(col * spacing, row * spacing, (col + 1) * spacing, (row + 1) * spacing)
                add("Estimated area crowd", "area", *_latlon(cx, cy, city), footprint)
    return [sample for sample in samples if sample.crowd_index >= min_crowd]


def predict_areas(
    estimator: CrowdEstimator, when: datetime, bounds: tuple[float, float, float, float],
    route_geometry: list[tuple[float, float]] | None = None,
) -> list[CrowdAreaOut]:
    samples = collect_samples(estimator, when, bounds)
    city = estimator.index.city
    corridor: BaseGeometry | None = None
    if route_geometry is not None:
        points = [_xy(lat, lon, city) for lat, lon in route_geometry]
        corridor = LineString(points).buffer(500) if len(points) > 1 else Point(points[0]).buffer(500) if points else Polygon()
    return merge_areas(samples, corridor, city)
