"""Small geodesy helpers."""

import math

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def path_length_m(points: list[tuple[float, float]]) -> float:
    return sum(haversine_m(*points[i], *points[i + 1]) for i in range(len(points) - 1))


def distance_to_path_m(lat: float, lon: float, points: list[tuple[float, float]]) -> float:
    """Local tangent-plane distance to a polyline, including its segment interiors."""
    scale = math.cos(math.radians(lat))
    xy = [
        (math.radians(p_lon - lon) * EARTH_RADIUS_M * scale, math.radians(p_lat - lat) * EARTH_RADIUS_M)
        for p_lat, p_lon in points
    ]
    best = min((math.hypot(x, y) for x, y in xy), default=math.inf)
    for (ax, ay), (bx, by) in zip(xy, xy[1:]):
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        t = max(0.0, min(1.0, -(ax * dx + ay * dy) / length2)) if length2 else 0.0
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best
