"""Crowd estimator combining the rule-based model with the (optional) Laya decision model."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..config import get_settings
from ..transit_index import RouteInfo, StopInfo, TransitIndex
from .features import CrowdFeatures, EventInfo, extract, point_crowd, vehicle_crowd
from .hotspots import Hotspot, load_hotspots
from .laya_model import CROWD_LEVELS, LayaClient, laya_client

WALK_EXPOSURE = 0.6  # walking outdoors is less stressful than a packed vehicle


@dataclass
class LegCrowd:
    rule_value: float
    value: float
    stop_values: list[float] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    laya_value: float | None = None
    laya_state: dict[str, Any] | None = None

    @property
    def percent(self) -> int:
        return int(round(self.value * 100))


def _level_name(value: float) -> str:
    return CROWD_LEVELS[min(len(CROWD_LEVELS) - 1, int(value * len(CROWD_LEVELS)))].split(":")[0]


class CrowdEstimator:
    def __init__(
        self,
        index: TransitIndex,
        events: list[EventInfo],
        hotspots: tuple[Hotspot, ...] | None = None,
        laya: LayaClient | None = None,
        use_laya: bool = True,
    ) -> None:
        self.index = index
        self.events = events
        self.hotspots = hotspots if hotspots is not None else load_hotspots(index.city)
        self.laya = laya if laya is not None else laya_client
        self.use_laya = use_laya
        self._cache: dict[tuple[str, datetime], CrowdFeatures] = {}

    def stop_features(self, stop: StopInfo, when: datetime) -> CrowdFeatures:
        bucket = when.replace(minute=(when.minute // 10) * 10, second=0, microsecond=0)
        key = (stop.stop_id, bucket)
        cached = self._cache.get(key)
        if cached is None:
            cached = extract(
                stop.lat, stop.lon, bucket, self.hotspots, self.events, self.index.stop_load.get(stop.stop_id, 0.0)
            )
            self._cache[key] = cached
        return cached

    def stop_crowd(self, stop: StopInfo, when: datetime) -> float:
        return point_crowd(self.stop_features(stop, when))

    def transit_leg(self, route: RouteInfo, headsign: str, stops: list[tuple[StopInfo, datetime]]) -> LegCrowd:
        feats = [self.stop_features(stop, when) for stop, when in stops]
        stop_values = [point_crowd(f) for f in feats]
        route_load = self.index.route_load.get(route.route_id, 0.0)
        peak = max((f.peak for f in feats), default=0.0)
        value = vehicle_crowd(stop_values, route_load, peak)
        reasons: list[str] = []
        for f in feats:
            for reason in f.reasons:
                if reason not in reasons:
                    reasons.append(reason)
        if route_load >= 0.85:
            reasons.append("heavily used line")
        start = stops[0][1]
        state = {
            "line": f"{route.mode} {route.short_name} towards {headsign}".strip(),
            "day": start.strftime("%A"),
            "time": start.strftime("%H:%M"),
            "ride_minutes": int((stops[-1][1] - start).total_seconds() // 60),
            "stops": ", ".join(s.name for s, _ in stops),
            "rush_hour": peak >= 0.7,
            "near_busy_places": sorted({f.hotspot_name for f in feats if f.hotspot_name and f.hotspot >= 0.35}),
            "large_events_nearby": sorted({f.event_name for f in feats if f.event_name and f.event >= 0.2}),
            "line_usage": "high" if route_load >= 0.85 else "medium" if route_load >= 0.6 else "low",
            "stop_usage": _level_name(max((f.stop_load for f in feats), default=0.0)),
        }
        return LegCrowd(rule_value=value, value=value, stop_values=stop_values, reasons=reasons, laya_state=state)

    def walk_leg(self, points: list[tuple[float, float, datetime]]) -> LegCrowd:
        values = []
        reasons: list[str] = []
        for lat, lon, when in points:
            f = extract(lat, lon, when, self.hotspots, self.events)
            values.append(point_crowd(f) * WALK_EXPOSURE)
            for reason in f.reasons:
                if reason not in reasons:
                    reasons.append(reason)
        value = max(values) if values else 0.0
        return LegCrowd(rule_value=value, value=value, stop_values=[], reasons=reasons)

    def refine_with_laya(self, legs: list[LegCrowd]) -> bool:
        """Blend Laya's estimate into the transit legs (one batched request). Returns True if Laya answered."""
        targets = [leg for leg in legs if leg.laya_state is not None and leg.laya_value is None]
        if not self.use_laya or not targets or not self.laya.enabled:
            return False
        weight = max(0.0, min(1.0, get_settings().laya_weight))
        groups: dict[str, list[LegCrowd]] = {}
        states: list[dict[str, Any]] = []
        for leg in targets:
            state = leg.laya_state
            if state is None:
                continue
            key = json.dumps(state, sort_keys=True)
            if key not in groups:
                states.append(state)
                groups[key] = []
            groups[key].append(leg)
        answers = self.laya.score_crowd(states)
        answered = False
        for group, answer in zip(groups.values(), answers, strict=True):
            if answer is None:
                continue
            answered = True
            for leg in group:
                leg.laya_value = answer
                leg.value = (1 - weight) * leg.rule_value + weight * answer
        return answered


def itinerary_crowd(legs: list[tuple[LegCrowd, float]]) -> int:
    """Combine (crowd, duration seconds) of legs: the worst leg matters most for a crowd-averse traveller."""
    if not legs:
        return 0
    total = sum(max(d, 1.0) for _, d in legs)
    mean = sum(c.value * max(d, 1.0) for c, d in legs) / total
    worst = max(c.value for c, _ in legs)
    return int(round(100 * min(1.0, 0.6 * worst + 0.4 * mean)))
