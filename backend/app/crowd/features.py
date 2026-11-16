"""Feature extraction and the built-in rule-based crowd decision model."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from ..geo import haversine_m
from .hotspots import Hotspot


@dataclass(frozen=True)
class EventInfo:
    name: str
    lat: float
    lon: float
    starts_at: datetime
    ends_at: datetime
    expected_attendance: int
    radius_m: float


@dataclass
class CrowdFeatures:
    """All values are normalised to 0..1."""

    peak: float = 0.0
    hotspot: float = 0.0
    event: float = 0.0
    stop_load: float = 0.0
    activity: float = 0.0
    hotspot_name: str | None = None
    event_name: str | None = None
    reasons: list[str] = field(default_factory=list)


def _gauss(x: float, mu: float, sigma: float) -> float:
    return math.exp(-0.5 * ((x - mu) / sigma) ** 2)


def peak_factor(when: datetime) -> float:
    """Typical demand profile by hour of day (rush hours) and day of week."""
    h = when.hour + when.minute / 60
    weekday = when.weekday()
    if weekday < 5:
        value = max(
            0.12,
            1.0 * _gauss(h, 7.5, 0.9),     # morning rush
            0.95 * _gauss(h, 16.0, 1.3),   # afternoon rush
            0.5 * _gauss(h, 12.0, 2.5),    # midday
            0.35 * _gauss(h, 20.0, 1.5),   # evening
        )
        if weekday == 4:  # Friday evening outings
            value = max(value, 0.55 * _gauss(h, 19.0, 2.0))
    elif weekday == 5:
        value = max(0.1, 0.6 * _gauss(h, 13.5, 3.0), 0.5 * _gauss(h, 20.5, 2.0))
    else:
        value = max(0.08, 0.45 * _gauss(h, 14.0, 3.0))
    if h < 5 or h >= 23.5:
        value = min(value, 0.12)
    return min(1.0, value)


def place_activity(kind: str, when: datetime) -> float:
    h = when.hour + when.minute / 60
    if kind == "nightlife":
        weekend_night = (when.weekday() in (4, 5) and h >= 19) or (when.weekday() in (5, 6) and h < 4)
        return 1.0 if weekend_night else 0.6 if h >= 20 or h < 2 else 0.05
    if kind == "beach":
        return max(0.05, _gauss(h, 14, 3)) if when.month in (6, 7, 8) else 0.05
    if kind == "centre":
        return max(0.05, 0.8 * _gauss(h, 14, 4), 0.65 * _gauss(h, 19, 2))
    if kind == "retail":
        return 0.7 if 9 <= h < 21 else 0.05
    if kind == "venue":
        return 0.1  # large-event demand comes from the events list
    return peak_factor(when)


def hotspot_factor(
    lat: float, lon: float, hotspots: tuple[Hotspot, ...], when: datetime | None = None,
) -> tuple[float, str | None]:
    best, name = 0.0, None
    for hs in hotspots:
        d = haversine_m(lat, lon, hs.lat, hs.lon)
        if d > hs.radius_m * 3:
            continue
        temporal = 1.0 if when is None else 0.15 + 0.85 * place_activity(hs.kind, when)
        value = hs.weight * temporal * math.exp(-((d / hs.radius_m) ** 2))
        if value > best:
            best, name = value, hs.name
    return best, name


def event_temporal_factor(when: datetime, start: datetime, end: datetime) -> float:
    """Arrival wave before the start, calmer during, strongest egress wave right after the end."""
    if when < start - timedelta(hours=2) or when > end + timedelta(minutes=90):
        return 0.0
    if when < start:
        return 0.3 + 0.6 * (1 - (start - when) / timedelta(hours=2))
    if when <= end:
        return 0.4
    return 1.0 - 0.6 * ((when - end) / timedelta(minutes=90))


def event_factor(lat: float, lon: float, when: datetime, events: list[EventInfo]) -> tuple[float, str | None]:
    total, name, best = 0.0, None, 0.0
    for ev in events:
        temporal = event_temporal_factor(when, ev.starts_at, ev.ends_at)
        if temporal <= 0:
            continue
        d = haversine_m(lat, lon, ev.lat, ev.lon)
        spatial = math.exp(-((d / max(ev.radius_m, 50)) ** 2))
        if spatial < 0.01:
            continue
        size = min(1.0, math.log10(max(ev.expected_attendance, 10)) / math.log10(40_000))
        value = size * spatial * temporal
        total += value
        if value > best:
            best, name = value, ev.name
    return min(1.0, total), name


def extract(
    lat: float,
    lon: float,
    when: datetime,
    hotspots: tuple[Hotspot, ...],
    events: list[EventInfo],
    stop_load: float = 0.0,
) -> CrowdFeatures:
    f = CrowdFeatures(peak=peak_factor(when), stop_load=stop_load)
    f.hotspot, f.hotspot_name = hotspot_factor(lat, lon, hotspots, when)
    f.activity = max((
        hs.weight * place_activity(hs.kind, when) * math.exp(-((haversine_m(lat, lon, hs.lat, hs.lon) / hs.radius_m) ** 2))
        for hs in hotspots
    ), default=0.0)
    f.event, f.event_name = event_factor(lat, lon, when, events)
    if f.peak >= 0.7:
        f.reasons.append("rush hour")
    if f.hotspot >= 0.35 and f.hotspot_name:
        f.reasons.append(f"near {f.hotspot_name}")
    if f.activity >= 0.5:
        f.reasons.append("busy local places at this time of day / season")
    if f.event >= 0.2 and f.event_name:
        f.reasons.append(f"event: {f.event_name}")
    if stop_load >= 0.85:
        f.reasons.append("heavily used stop")
    return f


def crowd_ceilings(
    when: datetime, hotspots: tuple[Hotspot, ...], events: list[EventInfo],
    hotspot_m: np.ndarray, event_m: np.ndarray,
) -> np.ndarray:
    """Upper bounds of point_crowd(extract(...)) without stop load at places whose distances to each hotspot
    and event (columns of hotspot_m and event_m) are at least the given metres."""
    # Time factors, place activity and event sizes are at most 1, and point_crowd never decreases when a
    # feature grows.
    weight = np.array([hs.weight for hs in hotspots]).reshape(1, -1)
    radius = np.array([hs.radius_m for hs in hotspots]).reshape(1, -1)
    near = weight * np.exp(-((hotspot_m / radius) ** 2))
    hotspot = np.where(hotspot_m <= radius * 3, near, 0.0).max(axis=1, initial=0.0)
    activity = near.max(axis=1, initial=0.0)
    spatial = np.exp(-((event_m / np.array([[max(ev.radius_m, 50) for ev in events]])) ** 2))
    event = np.minimum(1.0, np.where(spatial >= 0.01, spatial, 0.0).sum(axis=1))
    peak = peak_factor(when)
    return np.array([
        point_crowd(CrowdFeatures(peak=peak, hotspot=h, event=e, activity=a))
        for h, e, a in zip(hotspot.tolist(), event.tolist(), activity.tolist())
    ])


# --------------------------------------------------------------------------- rule-based model
def point_crowd(f: CrowdFeatures) -> float:
    """Crowd likelihood (0..1) at a location (stop / street) for the given features."""
    base = 0.32 * f.peak + 0.28 * f.hotspot + 0.25 * f.event + 0.15 * f.stop_load
    # Interactions: busy places get disproportionately crowded during rush hours.
    base += 0.25 * f.peak * f.hotspot + 0.15 * f.peak * f.stop_load
    base += 0.35 * f.activity + 0.2 * f.activity * f.hotspot
    # Decision rules
    if f.event >= 0.6:
        base = max(base, 0.6 + 0.4 * f.event)
    if f.peak >= 0.8 and f.hotspot >= 0.6:
        base = max(base, 0.75)
    if f.peak <= 0.15 and f.event < 0.2 and f.activity < 0.5:
        base = min(base, 0.35)
    return max(0.0, min(1.0, base))


def vehicle_crowd(stop_values: list[float], route_load: float, peak: float) -> float:
    """Crowd inside a vehicle over a ride, from the crowd at the stops it serves and route usage."""
    if not stop_values:
        return 0.0
    mean = sum(stop_values) / len(stop_values)
    peak_stop = max(stop_values)
    value = (0.55 * mean + 0.45 * peak_stop) * (0.75 + 0.25 * route_load) + 0.15 * peak * route_load
    return max(0.0, min(1.0, value))
