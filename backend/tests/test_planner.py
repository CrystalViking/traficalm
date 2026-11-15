from datetime import timedelta

from app.planner import Place, Planner
from tests.conftest import MONDAY_RUSH

ORIGIN = Place(54.3302, 18.6003, "Home")
NEAR_S5 = Place(54.3702, 18.6302, "Clinic")
NEAR_T2 = Place(54.3801, 18.6901, "Office")


def _plan(index, estimator, destination, when=MONDAY_RUSH, **kwargs):
    return Planner(index, estimator, use_walk_router=False).plan(ORIGIN, destination, when, **kwargs)


def test_direct_route_found_and_sorted_by_crowd(index, estimator):
    results = _plan(index, estimator, NEAR_S5)
    assert results
    crowd = [it.crowd_index for it in results]
    assert crowd == sorted(crowd)
    direct = [it for it in results if len(it.rides) == 1 and not it.crowd_avoiding]
    assert direct
    leg = next(leg for leg in direct[0].legs if leg.mode == "transit")
    assert leg.route.short_name == "8"
    assert [c.stop.stop_id for c in leg.calls] == ["S1", "S2", "S3", "MAIN", "S4", "S5"]
    assert leg.departure >= MONDAY_RUSH
    assert all(0 <= c.crowd <= 1 for c in leg.calls)


def test_crowd_avoiding_variant_walks_around_main_station(index, estimator):
    results = _plan(index, estimator, NEAR_S5)
    best = results[0]
    assert best.crowd_avoiding
    stop_ids = [c.stop.stop_id for leg in best.legs if leg.mode == "transit" for c in leg.calls]
    assert "MAIN" not in stop_ids
    modes = [leg.mode for leg in best.legs]
    assert modes.count("transit") == 2 and "walk" in modes[1:-1]
    original = min(it.crowd_index for it in results if not it.crowd_avoiding)
    assert best.crowd_index < original
    # connection is consistent in time
    for a, b in zip(best.legs, best.legs[1:]):
        assert a.arrival <= b.departure + timedelta(seconds=1)


def test_route_with_transfer(index, estimator):
    results = _plan(index, estimator, NEAR_T2)
    transfer = [it for it in results if it.transfers == 1 and not it.crowd_avoiding]
    assert transfer
    routes = [leg.route.short_name for leg in transfer[0].legs if leg.mode == "transit"]
    assert routes == ["8", "10"]
    avoiding = [it for it in results if it.crowd_avoiding]
    assert avoiding and avoiding[0].crowd_index < transfer[0].crowd_index


def test_three_rides_are_found_when_one_transfer_cannot_connect_the_journey(index):
    from app.crowd.estimator import CrowdEstimator
    from app.transit_index import RouteInfo, TransitIndex, TripInfo

    extended = TransitIndex()
    extended.stops = {key: index.stops[key] for key in ("S1", "S3", "T1", "T2")}
    extended.calendar = index.calendar
    extended.routes = {key: RouteInfo(key, key, "", 3) for key in ("A", "B", "C")}
    for trip_id, route, stops, times in (
        ("a", "A", ["S1", "S3"], [27000, 27360]),
        ("b1", "B", ["S3", "T1"], [27540, 28140]),
        ("b2", "B", ["S3", "T1"], [27540, 28140]),
        ("c", "C", ["T1", "T2"], [28320, 28680]),
    ):
        extended.trips[trip_id] = TripInfo(
            trip_id, route, "WD", "", None, None,
            stop_ids=stops, stop_sequences=[1, 2], arrivals=times, departures=times,
        )
    extended._build()
    results = Planner(extended, CrowdEstimator(extended, [], use_laya=False), use_walk_router=False).plan(
        ORIGIN, NEAR_T2, MONDAY_RUSH, crowd_tolerance=100,
    )
    connection = next(it for it in results if it.transfers == 2)
    assert [leg.route.short_name for leg in connection.legs if leg.mode == "transit"] == ["A", "B", "C"]
    assert all(a.arrival <= b.departure for a, b in zip(connection.legs, connection.legs[1:]))


def test_no_service_on_holiday_returns_walk_only(index, estimator):
    holiday = MONDAY_RUSH.replace(month=11, day=11)  # calendar_dates removes service
    results = _plan(index, estimator, NEAR_S5, when=holiday)
    assert len(results) == 1 and results[0].legs[0].mode == "walk" and not results[0].rides


def test_short_trip_includes_walking_option(index, estimator):
    results = _plan(index, estimator, Place(54.3380, 18.6200, "Nearby"))
    assert any(not it.rides for it in results)


def test_max_results(index, estimator):
    assert len(_plan(index, estimator, NEAR_T2, max_results=2)) <= 2


def test_tolerance_controls_automatic_variants(index, estimator):
    tolerant = _plan(index, estimator, NEAR_S5, crowd_tolerance=100)
    sensitive = _plan(index, estimator, NEAR_S5, crowd_tolerance=60)
    assert not any(it.crowd_avoiding for it in tolerant)
    assert any(it.crowd_avoiding for it in sensitive)


def test_selected_stop_alternatives_include_walk_and_bypass(index, estimator):
    from app.geo import distance_to_path_m
    from app.planner import AVOID_STOP_RADIUS_M

    results = _plan(index, estimator, NEAR_S5, crowd_tolerance=100, avoid_stop_ids=frozenset({"MAIN"}))
    assert any(not it.rides for it in results)
    assert any(it.rides for it in results)
    main = index.stops["MAIN"]
    for it in results:
        assert it.crowd_avoiding
        for leg in it.legs:
            assert all(call.stop.stop_id != "MAIN" for call in leg.calls)
            assert distance_to_path_m(main.lat, main.lon, leg.geometry) >= AVOID_STOP_RADIUS_M
        assert all(a.arrival <= b.departure for a, b in zip(it.legs, it.legs[1:]))


def test_no_alternative_when_origin_is_inside_avoided_area(index, estimator):
    origin = index.stops["MAIN"]
    results = Planner(index, estimator, use_walk_router=False).plan(
        Place(origin.lat, origin.lon, origin.name), NEAR_S5, MONDAY_RUSH,
        avoid_stop_ids=frozenset({"MAIN"}),
    )
    assert results == []


def test_continuation_waits_for_reported_exit_delay(index, estimator):
    from app.planner import Ride
    from app.realtime import TripUpdate

    planner = Planner(index, estimator, {"8_15": TripUpdate("8_15", "8", 600, (), None)}, use_walk_router=False)
    results = planner.continue_journey(
        ORIGIN, NEAR_T2, MONDAY_RUSH, (Ride("8_15", 0, 2, 0),),
        "S3", "S4", 35, frozenset({"MAIN"}),
    )
    assert results
    for itinerary in results:
        prefix = next(leg for leg in itinerary.legs if leg.mode == "transit")
        assert prefix.arrival == prefix.calls[-1].arrival + timedelta(seconds=600)
        bridge = itinerary.legs[itinerary.legs.index(prefix) + 1]
        assert bridge.departure >= prefix.arrival
        assert all(a.arrival <= b.departure for a, b in zip(itinerary.legs, itinerary.legs[1:]))


def test_alternative_can_use_a_different_line(index):
    from dataclasses import replace
    from app.crowd.estimator import CrowdEstimator
    from app.transit_index import TransitIndex
    from tests.conftest import FakeLaya

    updated = TransitIndex()
    updated.stops = index.stops.copy()
    updated.routes = index.routes.copy()
    updated.routes["quiet"] = replace(index.routes["8"], route_id="quiet", short_name="9")
    updated.calendar = index.calendar.copy()
    updated.trips = index.trips.copy()
    original = index.trips["8_15"]
    positions = [p for p, stop_id in enumerate(original.stop_ids) if stop_id != "MAIN"]
    updated.trips["quiet-trip"] = replace(
        original, trip_id="quiet-trip", route_id="quiet",
        stop_ids=[original.stop_ids[p] for p in positions],
        stop_sequences=[original.stop_sequences[p] for p in positions],
        arrivals=[original.arrivals[p] + 300 for p in positions],
        departures=[original.departures[p] + 300 for p in positions],
    )
    updated._build()
    estimator = CrowdEstimator(updated, [], laya=FakeLaya(None))
    results = _plan(updated, estimator, NEAR_S5, crowd_tolerance=100, avoid_stop_ids=frozenset({"MAIN"}))
    assert any(leg.route and leg.route.short_name == "9" for it in results for leg in it.legs)


def test_walking_alternative_crossing_avoided_stop_is_rejected(index, estimator, monkeypatch):
    from app import planner, walking

    main = index.stops["MAIN"]
    monkeypatch.setattr(planner, "route_walk", lambda lat1, lon1, lat2, lon2: walking.WalkPath(
        5000, 4000, ((lat1, lon1), (main.lat, main.lon), (lat2, lon2)), True,
    ))
    results = Planner(index, estimator, use_walk_router=True).plan(
        ORIGIN, NEAR_S5, MONDAY_RUSH, avoid_stop_ids=frozenset({"MAIN"}),
    )
    assert results == []


def test_normal_plans_reject_transfers_missed_after_footpath_routing(index, estimator, monkeypatch):
    from app import planner, walking

    def routed_walk(lat1, lon1, lat2, lon2):
        path = walking.estimate_walk(lat1, lon1, lat2, lon2)
        extra = 3600 if lat1 == index.stops["S3"].lat and lat2 == index.stops["S4"].lat else 0
        return walking.WalkPath(path.distance_m, path.duration_s + extra, path.geometry, True)

    monkeypatch.setattr(planner, "route_walk", routed_walk)
    results = Planner(index, estimator).plan(ORIGIN, NEAR_S5, MONDAY_RUSH, crowd_tolerance=60)
    assert any(it.rides for it in results)
    assert all(a.arrival <= b.departure for it in results for a, b in zip(it.legs, it.legs[1:]))


def test_normal_plans_never_move_departure_before_requested_time(index, estimator, monkeypatch):
    from app import planner, walking

    monkeypatch.setattr(planner, "route_walk", lambda lat1, lon1, lat2, lon2: walking.WalkPath(
        9000, 7200, ((lat1, lon1), (lat2, lon2)), True,
    ))
    results = Planner(index, estimator).plan(ORIGIN, NEAR_S5, MONDAY_RUSH)
    assert not any(it.departure < MONDAY_RUSH for it in results)


def test_avoiding_stop_can_walk_to_next_stop_and_change_line(index):
    from dataclasses import replace
    from app.crowd.estimator import CrowdEstimator
    from app.transit_index import TransitIndex
    from tests.conftest import FakeLaya

    updated = TransitIndex()
    updated.stops = index.stops.copy()
    updated.routes = {**index.routes, "quiet": replace(index.routes["8"], route_id="quiet", short_name="9")}
    updated.calendar = index.calendar.copy()
    updated.trips = index.trips.copy()
    original = index.trips["8_15"]
    updated.trips["next-stop-line"] = replace(
        original, trip_id="next-stop-line", route_id="quiet",
        stop_ids=["S4", "S5"], stop_sequences=[1, 2],
        arrivals=[8 * 3600 + 20 * 60, 8 * 3600 + 23 * 60],
        departures=[8 * 3600 + 20 * 60, 8 * 3600 + 23 * 60],
    )
    updated._build()
    results = _plan(updated, CrowdEstimator(updated, [], laya=FakeLaya(None)), NEAR_S5,
                    crowd_tolerance=100, avoid_stop_ids=frozenset({"MAIN"}), max_results=30)
    alternative = next(it for it in results if any(leg.trip and leg.trip.trip_id == "next-stop-line" for leg in it.legs))
    assert any(leg.mode == "walk" and leg.end.name == updated.stops["S4"].name and leg.distance_m > 300 for leg in alternative.legs)


def test_walk_router_fallback_and_geometry(monkeypatch):
    from app import walking

    monkeypatch.setattr(walking, "_down_until", 0.0)
    walking._osrm_cached.cache_clear()
    calls = []

    def fake_osrm(lat1, lon1, lat2, lon2):
        calls.append(1)
        return walking.WalkPath(500.0, 400, ((lat1, lon1), (54.0, 18.0), (lat2, lon2)), True)

    monkeypatch.setattr(walking, "_osrm", fake_osrm)
    monkeypatch.setattr(walking.get_settings(), "walking_router_enabled", True)
    path = walking.route_walk(54.33, 18.60, 54.34, 18.61)
    assert path.routed and len(path.geometry) == 3
    walking.route_walk(54.33, 18.60, 54.34, 18.61)
    assert len(calls) == 1  # cached
    monkeypatch.setattr(walking, "_osrm", lambda *a: None)
    fallback = walking.route_walk(54.35, 18.60, 54.36, 18.61)
    assert not fallback.routed and fallback.duration_s > 0
    walking._osrm_cached.cache_clear()


def test_routed_walk_only_option_keeps_departure_time(index, estimator, monkeypatch):
    from app import planner, walking

    def fake_route_walk(lat1, lon1, lat2, lon2):
        return walking.WalkPath(9000.0, 7200, ((lat1, lon1), (lat2, lon2)), True)

    monkeypatch.setattr(planner, "route_walk", fake_route_walk)
    holiday = MONDAY_RUSH.replace(day=11)
    results = Planner(index, estimator, use_walk_router=True).plan(ORIGIN, NEAR_S5, holiday)
    walk = results[0]
    assert not walk.rides
    assert walk.departure == holiday
    assert walk.arrival == holiday + timedelta(seconds=7200)
