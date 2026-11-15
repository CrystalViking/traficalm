"""Rule-based demand shifts and timetable-based vehicle positions; not observed pedestrian flows."""

from datetime import date, datetime, time, timedelta

from .crowd.areas import predict_areas
from .crowd.estimator import CrowdEstimator
from .crowd.features import extract, point_crowd
from .geo import distance_to_path_m, haversine_m
from .realtime import TripUpdate, delay_at_stop
from .schemas import CrowdChangeOut, CrowdFlowOut, CrowdForecastResponse, PlaceOut, VehiclePredictionOut
from .transit_index import TransitIndex, TripInfo

HORIZON = timedelta(minutes=60)


def crowd_forecast(
    estimator: CrowdEstimator, at: datetime, bounds: tuple[float, float, float, float],
    route_geometry: list[tuple[float, float]],
) -> CrowdForecastResponse:
    until = at + HORIZON
    anchors = [(h.name, h.lat, h.lon, 0.0) for h in estimator.hotspots]
    anchors += [(e.name, e.lat, e.lon, 0.0) for e in estimator.events]
    busy = sorted(estimator.index.stop_load.items(), key=lambda item: item[1], reverse=True)[:40]
    anchors += [
        (stop.name, stop.lat, stop.lon, load)
        for stop_id, load in busy if (stop := estimator.index.stops.get(stop_id)) is not None
    ]
    changes = []
    seen = set()
    for name, lat, lon, load in anchors:
        key = (round(lat, 5), round(lon, 5))
        if key in seen:
            continue
        seen.add(key)
        current, predicted = [
            int(round(point_crowd(extract(lat, lon, when, estimator.hotspots, estimator.events, load)) * 100))
            for when in (at, until)
        ]
        if abs(predicted - current) < 3:
            continue
        if max(current, predicted) < 50 and (
            not route_geometry or distance_to_path_m(lat, lon, route_geometry) > 500
        ):
            continue
        changes.append(CrowdChangeOut(
            name=name, lat=lat, lon=lon, current_index=current,
            predicted_index=predicted, change=predicted - current,
        ))
    changes.sort(key=lambda change: abs(change.change), reverse=True)
    changes = changes[:24]
    declining = {i: -change.change for i, change in enumerate(changes) if change.change < 0}
    flows = []
    for destination in (change for change in changes if change.change > 0):
        remaining = destination.change
        sources = sorted(declining, key=lambda i: haversine_m(changes[i].lat, changes[i].lon, destination.lat, destination.lon))
        for i in sources:
            source = changes[i]
            distance = haversine_m(source.lat, source.lon, destination.lat, destination.lon)
            if not declining[i] or distance < 100 or distance > 3000:
                continue
            amount = min(remaining, declining[i])
            flows.append(CrowdFlowOut(
                origin=PlaceOut(name=source.name, lat=source.lat, lon=source.lon),
                destination=PlaceOut(name=destination.name, lat=destination.lat, lon=destination.lon),
                change_points=amount,
            ))
            declining[i] -= amount
            remaining -= amount
            if not remaining or len(flows) >= 16:
                break
        if len(flows) >= 16:
            break
    return CrowdForecastResponse(
        at=at, until=until, areas=predict_areas(estimator, until, bounds, route_geometry),
        changes=changes, flows=flows,
    )


def vehicle_predictions(
    index: TransitIndex, trip: TripInfo, service_date: date, board: int, alight: int,
    at: datetime, update: TripUpdate | None,
) -> list[VehiclePredictionOut]:
    midnight = datetime.combine(service_date, time(), tzinfo=at.tzinfo)
    arrivals, departures = [], []
    for position in range(board, alight + 1):
        stop_id, sequence = trip.stop_ids[position], trip.stop_sequences[position]
        arr, dep = midnight + timedelta(seconds=trip.arrivals[position]), midnight + timedelta(seconds=trip.departures[position])
        arrivals.append(arr + timedelta(seconds=delay_at_stop(update, sequence, stop_id, int(arr.timestamp())) or 0))
        departures.append(dep + timedelta(seconds=delay_at_stop(update, sequence, stop_id, int(dep.timestamp()), "departure") or 0))
    predictions = []
    times = {at + timedelta(minutes=minutes) for minutes in (0, 15, 30, 45, 60)}
    times.update((departures[0], arrivals[-1]))
    for when in sorted(times):
        if when < at or when > at + HORIZON or when < departures[0] or when > arrivals[-1]:
            continue
        point = None
        for offset, position in enumerate(range(board, alight + 1)):
            if arrivals[offset] <= when <= departures[offset]:
                stop = index.stops[trip.stop_ids[position]]
                point = (stop.lat, stop.lon)
                break
            if position < alight and departures[offset] <= when <= arrivals[offset + 1]:
                seconds = (arrivals[offset + 1] - departures[offset]).total_seconds()
                fraction = (when - departures[offset]).total_seconds() / seconds if seconds > 0 else 0
                geometry = index.shape_segment(trip, position, position + 1)
                lengths = [haversine_m(*a, *b) for a, b in zip(geometry, geometry[1:])]
                target = sum(lengths) * fraction
                for a, b, distance in zip(geometry, geometry[1:], lengths):
                    if target <= distance:
                        ratio = target / distance if distance else 0
                        point = (a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio)
                        break
                    target -= distance
                if point is None:
                    point = geometry[-1]
                break
        if point is not None:
            predictions.append(VehiclePredictionOut(
                trip_id=trip.trip_id, name=index.routes[trip.route_id].short_name,
                lat=point[0], lon=point[1], at=when,
            ))
    return predictions
