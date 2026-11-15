from google.transit import gtfs_realtime_pb2

from app.planner import Place, Planner
from app.realtime import delay_at_stop, parse_trip_updates, parse_vehicle_positions
from tests.conftest import MONDAY_RUSH


def _feed() -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    e = feed.entity.add(id="1")
    e.trip_update.trip.trip_id = "8_15"  # 07:30 departure from S1
    stu = e.trip_update.stop_time_update.add(stop_sequence=2, stop_id="S2")
    stu.arrival.delay = 120
    stu = e.trip_update.stop_time_update.add(stop_sequence=4, stop_id="MAIN")
    stu.departure.delay = 300
    v = feed.entity.add(id="2")
    v.vehicle.trip.trip_id = "8_15"
    v.vehicle.vehicle.id = "1042"
    v.vehicle.position.latitude = 54.34
    v.vehicle.position.longitude = 18.62
    v.vehicle.timestamp = 1_800_000_000
    return feed.SerializeToString()


def test_parse_feed_and_propagate_delays():
    updates = parse_trip_updates(_feed())
    tu = updates["8_15"]
    assert delay_at_stop(tu, 1, "S1", 0) is None
    assert delay_at_stop(tu, 2, "S2", 0) == 120
    assert delay_at_stop(tu, 3, "S3", 0) == 120  # propagated downstream
    assert delay_at_stop(tu, 4, "MAIN", 0) == 300
    assert delay_at_stop(tu, 6, "S5", 0) == 300
    assert delay_at_stop(None, 1, "S1", 0) is None
    vehicles = parse_vehicle_positions(_feed())
    assert vehicles[0].trip_id == "8_15" and vehicles[0].vehicle_id == "1042"


def test_absolute_time_update():
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    e = feed.entity.add(id="1")
    e.trip_update.trip.trip_id = "X"
    stu = e.trip_update.stop_time_update.add(stop_sequence=3, stop_id="A")
    stu.arrival.time = 1_000_090
    tu = parse_trip_updates(feed.SerializeToString())["X"]
    assert delay_at_stop(tu, 3, "A", 1_000_000) == 90


def test_feed_service_dates_and_header_timestamp():
    from datetime import date

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(_feed())
    feed.entity[0].trip_update.trip.start_date = "20261116"
    feed.entity[1].vehicle.trip.start_date = "20261116"
    feed.header.timestamp = 1_800_000_100
    feed.entity[1].vehicle.ClearField("timestamp")
    data = feed.SerializeToString()
    assert parse_trip_updates(data)["8_15"].service_date == date(2026, 11, 16)
    vehicle = parse_vehicle_positions(data)[0]
    assert vehicle.service_date == date(2026, 11, 16)
    assert vehicle.timestamp == feed.header.timestamp


def test_invalid_explicit_service_dates_are_not_treated_as_missing(caplog):
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(_feed())
    feed.entity[0].trip_update.trip.start_date = "20261301"
    feed.entity[1].vehicle.trip.start_date = "invalid"
    data = feed.SerializeToString()
    assert parse_trip_updates(data) == {}
    assert parse_vehicle_positions(data) == []
    assert "invalid service date" in caplog.text


def test_invalid_vehicle_coordinates_are_rejected(caplog):
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(_feed())
    feed.entity[1].vehicle.position.latitude = float("nan")
    assert parse_vehicle_positions(feed.SerializeToString()) == []
    assert "invalid coordinates" in caplog.text


def test_planner_applies_realtime(index, estimator):
    updates = parse_trip_updates(_feed())
    planner = Planner(index, estimator, trip_updates=updates, use_walk_router=False)
    results = planner.plan(Place(54.3302, 18.6003, "A"), Place(54.3702, 18.6302, "B"), MONDAY_RUSH.replace(minute=25))
    legs = [
        leg for it in results for leg in it.legs
        if leg.mode == "transit" and leg.trip.trip_id == "8_15" and leg.calls[0].stop.stop_id == "S1"
    ]
    assert legs
    calls = {c.stop.stop_id: c for c in legs[0].calls}
    assert calls["S1"].delay_s is None and calls["S1"].rt_departure is None
    assert calls["S2"].delay_s == 120
    assert (calls["S2"].rt_arrival - calls["S2"].arrival).total_seconds() == 120
    assert calls["S3"].delay_s == 120  # propagated to later stops
