import pytest
from fastapi.testclient import TestClient

from app import geocoding
from app.config import get_settings
from app.main import create_app


@pytest.fixture()
def client(loaded_db):
    with TestClient(create_app()) as c:
        yield c


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["stops"] == 10 and body["laya_enabled"] is False


def test_plan_endpoint_sorted_by_crowd(client):
    r = client.get(
        "/api/plan",
        params={
            "from_lat": 54.3302, "from_lon": 18.6003, "to_lat": 54.3801, "to_lon": 18.6901,
            "from_name": "Home", "to_name": "Office", "departure": "2026-11-16T07:20:00+01:00",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    its = data["itineraries"]
    assert its
    assert [i["crowd_index"] for i in its] == sorted(i["crowd_index"] for i in its)
    assert {i["type"] for i in its} >= {"transfer"}
    transit = next(leg for i in its for leg in i["legs"] if leg["mode"] == "transit")
    assert transit["route"]["short_name"] in {"8", "10"}
    assert transit["stops"][0]["crowd_index"] >= 0
    assert transit["geometry"] and transit["service_date"] == "2026-11-16"
    assert all(0 <= i["crowd_index"] <= 100 and i["crowd_level"] for i in its)


def test_plan_validation(client):
    assert client.get("/api/plan", params={"from_lat": 100, "from_lon": 0, "to_lat": 0, "to_lon": 0}).status_code == 422


def test_plan_tolerance_and_multiple_avoided_stops(client):
    params = {
        "from_lat": 54.3302, "from_lon": 18.6003, "to_lat": 54.3702, "to_lon": 18.6302,
        "departure": "2026-11-16T07:20:00+01:00",
    }
    assert client.get("/api/plan", params=params).json()["crowd_tolerance"] == 60
    response = client.get("/api/plan", params={**params, "crowd_tolerance": 100, "avoid_stop_ids": ["MAIN", "H1"]})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["avoided_stop_ids"] == ["H1", "MAIN"]
    assert data["itineraries"]
    assert all(stop["stop_id"] not in {"MAIN", "H1"} for it in data["itineraries"] for leg in it["legs"] for stop in leg["stops"])
    assert client.get("/api/plan", params={**params, "crowd_tolerance": 101}).status_code == 422
    assert client.get("/api/plan", params={**params, "avoid_stop_ids": ["unknown"]}).status_code == 422
    assert client.get("/api/plan", params={**params, "avoid_stop_ids": ["MAIN"] * 21}).status_code == 422


def test_nearby_stops_are_sorted_and_limited_to_two_kilometres(client):
    from app.geo import haversine_m

    response = client.get("/api/stops/nearby", params={
        "stop_id": "MAIN", "at": "2026-11-16T07:39:00+01:00", "excluded_stop_ids": ["H1"], "radius_m": 2000,
    })
    assert response.status_code == 200, response.text
    stops = response.json()
    assert stops and [s["distance_m"] for s in stops] == sorted(s["distance_m"] for s in stops)
    assert all(s["stop_id"] not in {"MAIN", "H1"} and s["distance_m"] <= 2000 for s in stops)
    for stop in stops:
        assert stop["distance_m"] == pytest.approx(haversine_m(54.3556, 18.6447, stop["lat"], stop["lon"]))
    assert client.get("/api/stops/nearby", params={"stop_id": "unknown"}).status_code == 422


def continuation_request(exit_id="S3", sequence=3):
    return {
        "origin": {"name": "Home", "lat": 54.3302, "lon": 18.6003},
        "destination": {"name": "Office", "lat": 54.3801, "lon": 18.6901},
        "departure": "2026-11-16T07:20:00+01:00",
        "prefix_rides": [{
            "trip_id": "8_15", "board_sequence": 1, "alight_sequence": sequence, "service_date": "2026-11-16",
        }],
        "exit_stop_id": exit_id, "boarding_stop_id": "S4",
        "avoid_stop_ids": ["MAIN"], "crowd_tolerance": 35, "use_laya": False,
        "walk_radius_m": 2000,
    }


@pytest.mark.parametrize("exit_id,sequence", [("S3", 3), ("MAIN", 4)])
def test_continuation_retains_prefix_and_boards_the_selected_stop(client, exit_id, sequence):
    response = client.post("/api/plan/continue", json=continuation_request(exit_id, sequence))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["itineraries"] and data["laya_status"] == "disabled"
    for itinerary in data["itineraries"]:
        transit = [leg for leg in itinerary["legs"] if leg["mode"] == "transit"]
        prefix = transit[0]
        assert prefix["trip_id"] == "8_15"
        assert prefix["stops"][-1]["stop_id"] == exit_id
        assert prefix["geometry"][-1] == [prefix["stops"][-1]["lat"], prefix["stops"][-1]["lon"]]
        assert transit[1]["stops"][0]["stop_id"] == "S4"
        assert all(stop["stop_id"] != "MAIN" for leg in transit[1:] for stop in leg["stops"])
        assert sum(stop["stop_id"] == "MAIN" for leg in transit for stop in leg["stops"]) == (exit_id == "MAIN")
        assert all(a["arrival"] <= b["departure"] for a, b in zip(itinerary["legs"], itinerary["legs"][1:]))
        assert any(leg["mode"] == "walk" and leg["start"]["name"] == prefix["end"]["name"] for leg in itinerary["legs"])


def test_continuation_defaults_to_sixty_percent_tolerance(client):
    request = continuation_request()
    del request["crowd_tolerance"]
    response = client.post("/api/plan/continue", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["crowd_tolerance"] == 60


def test_continuation_rejects_invalid_edits_and_unreachable_connections(client):
    request = continuation_request()
    assert client.post("/api/plan/continue", json={**request, "boarding_stop_id": "T2"}).status_code == 422
    assert client.post("/api/plan/continue", json={**request, "boarding_stop_id": "MAIN"}).status_code == 422
    assert client.post("/api/plan/continue", json={**request, "exit_stop_id": "S4"}).status_code == 422
    assert client.post("/api/plan/continue", json={
        **request, "prefix_rides": [{**request["prefix_rides"][0], "alight_sequence": 999}],
    }).status_code == 422
    assert client.post("/api/plan/continue", json={
        **request, "departure": "2026-11-16T08:00:00+01:00",
    }).status_code == 422


@pytest.mark.parametrize("distance", [499, 501])
def test_default_half_kilometre_boundary_is_enforced_for_listing_and_planning(client, index, distance):
    import math
    from app.api import index_dep
    from app.geo import EARTH_RADIUS_M
    from app.transit_index import StopInfo

    index.stops["EDGE"] = StopInfo("EDGE", "Boundary stop", 54.3500 + math.degrees(distance / EARTH_RADIUS_M), 18.6380)
    index._build()
    client.app.dependency_overrides[index_dep] = lambda: index
    stops = client.get("/api/stops/nearby", params={"stop_id": "S3"}).json()
    assert any(stop["stop_id"] == "EDGE" for stop in stops) == (distance < 500)
    request = {**continuation_request(), "boarding_stop_id": "EDGE"}
    del request["walk_radius_m"]
    response = client.post("/api/plan/continue", json=request)
    assert response.status_code == (200 if distance < 500 else 422)
    if distance < 500:
        assert response.json()["itineraries"] == []  # No vehicle serves this stop; do not substitute another stop.
    wider = client.get("/api/stops/nearby", params={"stop_id": "S3", "radius_m": 3000}).json()
    assert any(stop["stop_id"] == "EDGE" for stop in wider)
    assert client.post("/api/plan/continue", json={
        **continuation_request(), "boarding_stop_id": "EDGE", "walk_radius_m": 3000,
    }).status_code == 200
    assert client.post("/api/plan/continue", json={
        **continuation_request(), "boarding_stop_id": "EDGE", "walk_radius_m": 100,
    }).status_code == 422
    assert client.get("/api/stops/nearby", params={"stop_id": "S3", "radius_m": 0}).status_code == 422


def test_crowd_map_is_bounded_and_high_areas_are_available_outside_viewport(client):
    from shapely.geometry import Polygon

    response = client.get("/api/crowd/areas", params={"at": "2026-11-16T07:30:00+01:00"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["estimated"] is True
    assert 0 < len([a for a in data["areas"] if a["kind"] == "area"]) <= 1000
    assert all(0 <= a["crowd_index"] <= 100 for a in data["areas"])
    assert any(a["crowd_index"] >= 50 for a in data["areas"])
    assert all(a["geometry"] and a["geometry"][0][0] == a["geometry"][0][-1] for a in data["areas"])
    assert all(Polygon(a["geometry"][0], a["geometry"][1:]).is_valid for a in data["areas"])
    outside = client.get("/api/crowd/areas", params={
        "at": "2026-11-16T07:30:00+01:00", "south": 50, "north": 51, "west": 15, "east": 16,
    }).json()["areas"]
    assert any(a["crowd_index"] >= 50 for a in outside)
    overview = client.post("/api/crowd/areas", json={"at": "2026-11-16T07:30:00+01:00"}).json()
    assert overview["areas"] and all(a["crowd_index"] >= 50 for a in overview["areas"])
    assert client.post("/api/crowd/areas", json={"route_geometry": [[100, 18]]}).status_code == 422
    assert client.get("/api/crowd/areas", params={"south": 55, "north": 54}).status_code == 422


def test_crowd_forecast_models_next_hour_and_event_demand_shifts(client, monkeypatch):
    from datetime import datetime

    monkeypatch.setattr(get_settings(), "admin_token", "test-token")
    created = client.post("/api/events", headers={"X-Admin-Token": "test-token"}, json={
        "name": "Evening concert", "lat": 54.3501, "lon": 18.6436,
        "starts_at": "2026-11-16T19:00:00+01:00", "ends_at": "2026-11-16T20:50:00+01:00",
        "expected_attendance": 40000, "radius_m": 200,
    })
    assert created.status_code == 201
    response = client.post("/api/crowd/forecast", json={"at": "2026-11-16T20:30:00+01:00"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert (datetime.fromisoformat(data["until"]) - datetime.fromisoformat(data["at"])).total_seconds() == 3600
    assert data["estimated"] and data["areas"]
    assert len(data["changes"]) <= 24 and len(data["flows"]) <= 16
    assert any(change["change"] > 0 for change in data["changes"])
    assert data["flows"] and all(flow["change_points"] > 0 for flow in data["flows"])
    assert client.post("/api/crowd/forecast", json={"south": 55, "north": 54}).status_code == 422


def test_vehicle_forecast_interpolates_selected_trip_and_respects_leg_bounds(client):
    from datetime import datetime

    ride = {"trip_id": "8_15", "service_date": "2026-11-16", "board_sequence": 1, "alight_sequence": 6}
    response = client.post("/api/vehicles/forecast", json={"at": "2026-11-16T07:34:00+01:00", "rides": [ride]})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["estimated"] and (datetime.fromisoformat(data["until"]) - datetime.fromisoformat(data["at"])).total_seconds() == 3600
    first = data["predictions"][0]
    assert first["trip_id"] == "8_15" and first["lat"] == pytest.approx(54.342)
    assert first["lon"] == pytest.approx(18.626)
    assert all(prediction["at"] <= "2026-11-16T07:45:00+01:00" for prediction in data["predictions"])
    partial = client.post("/api/vehicles/forecast", json={
        "at": "2026-11-16T07:40:00+01:00", "rides": [{**ride, "board_sequence": 5}],
    }).json()["predictions"]
    assert [(point["lat"], point["lon"]) for point in partial] == [(54.361, 18.642), (54.370, 18.630)]
    assert client.post("/api/vehicles/forecast", json={
        "at": "2026-11-16T07:40:00+01:00", "rides": [{**ride, "board_sequence": 999}],
    }).status_code == 422


def test_live_vehicle_tracking_matches_service_date_and_rejects_stale_positions(client, monkeypatch):
    from dataclasses import replace
    from datetime import UTC, date, datetime
    from app.realtime import VehiclePosition, realtime_service

    fresh = VehiclePosition("8_15", "8", "BUS", "Selected bus", 54.34, 18.62, None, None, int(datetime.now(UTC).timestamp()), date(2026, 11, 16))
    monkeypatch.setattr(realtime_service, "trip_updates", lambda: {})
    monkeypatch.setattr(realtime_service, "vehicle_positions", lambda: [fresh])
    params = {"service_date": "2026-11-16"}
    data = client.get("/api/trips/8_15/realtime", params=params).json()
    assert data["tracking_status"] == "live" and data["vehicle"]["vehicle_id"] == "BUS"
    monkeypatch.setattr(realtime_service, "vehicle_positions", lambda: [replace(fresh, service_date=date(2026, 11, 17))])
    mismatch = client.get("/api/trips/8_15/realtime", params=params).json()
    assert mismatch["tracking_status"] == "not-current-service" and mismatch["vehicle"] is None
    monkeypatch.setattr(realtime_service, "vehicle_positions", lambda: [replace(fresh, timestamp=fresh.timestamp - 121)])
    stale = client.get("/api/trips/8_15/realtime", params=params).json()
    assert stale["tracking_status"] == "stale" and stale["vehicle"] is None
    monkeypatch.setattr(realtime_service, "vehicle_error", "Live vehicle feed is unavailable")
    failed = client.get("/api/trips/8_15/realtime", params=params).json()
    assert failed["tracking_status"] == "unavailable" and failed["tracking_message"]


def test_undated_live_vehicle_matches_active_schedule_including_overnight(client, index, monkeypatch):
    from dataclasses import replace
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from app.api import index_dep
    from app.realtime import VehiclePosition, realtime_service

    monkeypatch.setitem(client.app.dependency_overrides, index_dep, lambda: index)
    now = datetime.now(ZoneInfo(get_settings().timezone))
    today = now.date()
    start = max(0, now.hour * 3600 + now.minute * 60 + now.second - 300)
    trip = index.trips["8_15"]
    times = tuple(start + i * 180 for i in range(len(trip.arrivals)))
    trip = replace(trip, arrivals=times, departures=times)
    monkeypatch.setitem(index.trips, "8_15", trip)
    vehicle = VehiclePosition("8_15", "8", "BUS", None, 54.34, 18.62, None, None, int(now.timestamp()))
    monkeypatch.setattr(realtime_service, "trip_updates", lambda: {})
    monkeypatch.setattr(realtime_service, "vehicle_positions", lambda: [vehicle])
    url = "/api/trips/8_15/realtime"
    assert client.get(url, params={"service_date": today.isoformat()}).json()["tracking_status"] == "live"
    assert client.get(url, params={"service_date": (today + timedelta(days=1)).isoformat()}).json()["vehicle"] is None
    monkeypatch.setitem(index.trips, "8_15", replace(
        trip, arrivals=tuple(t + 86400 for t in trip.arrivals),
        departures=tuple(t + 86400 for t in trip.departures),
    ))
    assert client.get(url, params={"service_date": (today - timedelta(days=1)).isoformat()}).json()["tracking_status"] == "live"
    assert client.get(url, params={"service_date": today.isoformat()}).json()["vehicle"] is None


def test_imported_large_event_changes_map_crowd(client, monkeypatch):
    at = "2026-11-16T21:10:00+01:00"
    monkeypatch.setattr(get_settings(), "admin_token", "s3cret-test")
    created = client.post("/api/events", headers={"X-Admin-Token": "s3cret-test"}, json={
        "name": "Football game", "lat": 54.3897, "lon": 18.6403,
        "starts_at": "2026-11-16T18:00:00+01:00", "ends_at": "2026-11-16T21:00:00+01:00",
        "expected_attendance": 40000, "radius_m": 1500,
    })
    assert created.status_code == 201
    areas = client.get("/api/crowd/areas", params={"at": at}).json()["areas"]
    event = next(a for a in areas if "event: Football game" in a["reasons"])
    assert event["crowd_index"] >= 60 and "event: Football game" in event["reasons"]
    expired = client.get("/api/crowd/areas", params={"at": "2026-11-17T04:00:00+01:00"}).json()["areas"]
    assert not any("event: Football game" in a["reasons"] for a in expired)


def test_laya_opt_in_status_reports_usage_and_failure(client, monkeypatch):
    from app.crowd.laya_model import laya_client

    params = {
        "from_lat": 54.3302, "from_lon": 18.6003, "to_lat": 54.3801, "to_lon": 18.6901,
        "departure": "2026-11-16T07:20:00+01:00",
    }
    monkeypatch.setattr(get_settings(), "laya_url", "http://localhost:8001")
    calls = []
    def score(states):
        calls.append(states)
        return [0.2] * len(states)
    monkeypatch.setattr(laya_client, "score_crowd", score)
    opted_out = client.get("/api/plan", params={**params, "use_laya": False}).json()
    assert opted_out["laya_status"] == "disabled" and not opted_out["laya_enabled"] and not calls
    opted_in = client.get("/api/plan", params={**params, "use_laya": True}).json()
    assert opted_in["laya_requested"] and opted_in["laya_enabled"] and opted_in["laya_status"] == "used"
    assert calls and any(leg["crowd_laya"] == 20 for it in opted_in["itineraries"] for leg in it["legs"])
    monkeypatch.setattr(laya_client, "score_crowd", lambda states: [None] * len(states))
    unavailable = client.get("/api/plan", params={**params, "use_laya": True}).json()
    assert not unavailable["laya_enabled"] and unavailable["laya_status"] == "unavailable"
    assert "rule-based" in unavailable["laya_message"]


def test_trip_realtime_without_feed(client):
    r = client.get("/api/trips/8_15/realtime", params={"service_date": "2026-11-16"})
    assert r.status_code == 200
    body = r.json()
    assert body["has_realtime"] is False and len(body["stops"]) == 6
    assert client.get("/api/trips/unknown/realtime").status_code == 404


def test_geocode_proxies_nominatim(client, monkeypatch):
    calls = []

    def fake_get(path, params):
        calls.append((path, params))
        if path == "search":
            return [{
                "display_name": "Długi Targ, Gdańsk", "lat": "54.3486", "lon": "18.6531",
                "type": "square", "address": {"road": "Długi Targ", "city": "Gdańsk"},
            }]
        return {"display_name": "Wały Jagiellońskie, Gdańsk", "lat": "54.35", "lon": "18.65"}

    monkeypatch.setattr(geocoding, "_get", fake_get)
    r = client.get("/api/geocode/search", params={"q": "Długi Targ"})
    assert r.json()[0]["label"].startswith("Długi Targ")
    assert calls[0][1]["countrycodes"] == "pl"
    assert calls[0][1]["addressdetails"] == 1
    assert calls[0][1]["accept-language"] == "pl"
    assert client.get("/api/geocode/search", params={"q": "a"}).status_code == 422
    r = client.get("/api/geocode/reverse", params={"lat": 54.35, "lon": 18.65})
    assert r.json()["label"].startswith("Wały")


def test_invalid_geocode_search_response_is_an_error(client, monkeypatch):
    monkeypatch.setattr(geocoding, "_get", lambda path, params: {"error": "invalid response"})
    response = client.get("/api/geocode/search", params={"q": "Długi Targ"})
    assert response.status_code == 502
    assert response.json()["detail"] == "Address service unavailable"


def test_events_require_admin_token(client, monkeypatch):
    event = {
        "name": "Concert", "lat": 54.39, "lon": 18.64,
        "starts_at": "2026-11-16T18:00:00+01:00", "ends_at": "2026-11-16T21:00:00+01:00",
        "expected_attendance": 30000, "radius_m": 1200,
    }
    assert client.post("/api/events", json=event).status_code == 403
    monkeypatch.setattr(get_settings(), "admin_token", "s3cret-test")
    assert client.post("/api/events", json=event, headers={"X-Admin-Token": "wrong"}).status_code == 401
    r = client.post("/api/events", json=event, headers={"X-Admin-Token": "s3cret-test"})
    assert r.status_code == 201
    event_id = r.json()["id"]
    listed = client.get("/api/events", params={"upcoming_only": False}).json()
    assert [e["name"] for e in listed] == ["Concert"]
    bad = {**event, "ends_at": "2026-11-16T17:00:00+01:00"}
    assert client.post("/api/events", json=bad, headers={"X-Admin-Token": "s3cret-test"}).status_code == 422
    assert client.delete(f"/api/events/{event_id}", headers={"X-Admin-Token": "s3cret-test"}).status_code == 204


def test_event_raises_crowd_index(client, monkeypatch):
    params = {
        "from_lat": 54.3302, "from_lon": 18.6003, "to_lat": 54.3801, "to_lon": 18.6901,
        "departure": "2026-11-16T21:10:00+01:00",
    }
    before = client.get("/api/plan", params=params).json()["itineraries"][0]["crowd_index"]
    monkeypatch.setattr(get_settings(), "admin_token", "s3cret-test")
    client.post(
        "/api/events",
        headers={"X-Admin-Token": "s3cret-test"},
        json={
            "name": "Stadium concert", "lat": 54.3700, "lon": 18.6300,
            "starts_at": "2026-11-16T18:00:00+01:00", "ends_at": "2026-11-16T21:00:00+01:00",
            "expected_attendance": 40000, "radius_m": 2000,
        },
    )
    after = client.get("/api/plan", params=params).json()["itineraries"][0]["crowd_index"]
    assert after > before
