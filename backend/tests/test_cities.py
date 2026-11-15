import csv
import io
import unicodedata
import zipfile
from datetime import datetime, timedelta
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app import city_data, db, geocoding, models, realtime
from app.cities import City, city_profile
from app.crowd.areas import predict_areas
from app.crowd.estimator import CrowdEstimator
from app.gtfs_static import load_gtfs_zip
from app.main import create_app
from app.transit_index import get_index
from tests.conftest import build_gtfs_zip


def city_feed(city: City, start: str = "", end: str = "") -> bytes:
    offsets = {City.POZNAN: (-1.95, -1.70), City.WROCLAW: (-3.25, -1.60), City.WARSAW: (-2.12, 2.36)}
    lat_offset, lon_offset = offsets[city]
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(build_gtfs_zip())) as source, zipfile.ZipFile(output, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "stops.txt":
                rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
                for row in rows:
                    row["stop_name"] = f"{city} {row['stop_name']}"
                    row["stop_lat"] = str(float(row["stop_lat"]) + lat_offset)
                    row["stop_lon"] = str(float(row["stop_lon"]) + lon_offset)
                text = io.StringIO()
                writer = csv.DictWriter(text, rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
                data = text.getvalue().encode("utf-8")
            target.writestr(name, data)
        target.writestr("feed_info.txt", f"feed_start_date,feed_end_date\n{start},{end}\n")
        if city == City.WARSAW:
            target.writestr("attributions.txt",
                           "organization_name,attribution_url\nZTM Warszawa,https://wtp.waw.pl/\n"
                           "Mikołaj Kuranowski,https://mkuran.pl/\nOpenStreetMap contributors,https://www.openstreetmap.org/copyright\n")
    return output.getvalue()


@pytest.fixture()
def city_client(loaded_db, monkeypatch, tmp_path):
    monkeypatch.setattr(db, "_city_engines", {})
    monkeypatch.setattr(db, "_city_factories", {})
    cities = (City.POZNAN, City.WROCLAW, City.WARSAW)
    engines = [db.configure(f"sqlite:///{tmp_path / (city + '.sqlite3')}", city) for city in cities]
    for city in cities:
        db.create_all(city)
    with TestClient(create_app()) as client:
        yield client
    for engine in engines:
        engine.dispose()


def test_first_selection_downloads_once_and_keeps_overlapping_city_ids_isolated(city_client, monkeypatch):
    download = Mock(side_effect=lambda profile: city_feed(profile.city))
    monkeypatch.setattr(city_data, "download_city_gtfs", download)
    assert city_client.get("/api/health", params={"city": "invalid"}).status_code == 422
    assert city_client.post("/api/cities/invalid/prepare").status_code == 422
    cold = city_client.get("/api/plan", params={
        "city": "poznan", "from_lat": 52.38, "from_lon": 16.9, "to_lat": 52.43, "to_lon": 16.99,
    })
    assert cold.status_code == 503
    prepared = city_client.post("/api/cities/poznan/prepare")
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["ready"] and prepared.json()["imported_at"]
    assert city_client.post("/api/cities/poznan/prepare").status_code == 200
    assert download.call_count == 1
    assert city_client.post("/api/cities/wroclaw/prepare").json()["realtime_supported"] is False
    warsaw = city_client.post("/api/cities/warsaw/prepare")
    assert warsaw.status_code == 200, warsaw.text
    assert warsaw.json()["realtime_supported"] is False
    assert [row["organization_name"] for row in warsaw.json()["attributions"]] == [
        "ZTM Warszawa", "Mikołaj Kuranowski", "OpenStreetMap contributors",
    ]
    assert city_client.post("/api/cities/warsaw/prepare").json()["attributions"] == warsaw.json()["attributions"]
    assert download.call_count == 3
    for city, expected_lat in ((City.GDANSK, 54.35), (City.POZNAN, 52.4), (City.WROCLAW, 51.1), (City.WARSAW, 52.23)):
        with db.session_scope(city) as session:
            index = get_index(session)
            assert index.city == city
            assert index.stops["S3"].lat == pytest.approx(expected_lat)
            assert session.get(models.Stop, "S3").stop_lat == pytest.approx(expected_lat)
    result = city_client.get("/api/plan", params={
        "city": "poznan", "from_lat": 52.3802, "from_lon": 16.9003, "to_lat": 52.4301, "to_lon": 16.9901,
        "departure": "2026-11-16T07:20:00+01:00",
    })
    assert result.status_code == 200, result.text
    assert result.json()["city"] == "poznan"
    assert any(leg["mode"] == "transit" for it in result.json()["itineraries"] for leg in it["legs"])
    assert all(52 <= stop["lat"] <= 53 for it in result.json()["itineraries"] for leg in it["legs"] for stop in leg["stops"])
    wrong_location = city_client.get("/api/plan", params={
        "city": "poznan", "from_lat": 54.35, "from_lon": 18.64, "to_lat": 52.43, "to_lon": 16.99,
    })
    assert wrong_location.status_code == 422


def test_download_failures_are_explicit_and_a_retry_can_prepare_the_city(city_client, monkeypatch):
    download = Mock(side_effect=[ValueError("invalid archive"), city_feed(City.POZNAN)])
    monkeypatch.setattr(city_data, "download_city_gtfs", download)
    assert city_client.post("/api/cities/poznan/prepare").status_code == 502
    assert city_client.post("/api/cities/poznan/prepare").json()["ready"]
    assert download.call_count == 2


def test_city_search_bounds_and_hotspots_do_not_leak_from_gdansk(city_client, monkeypatch):
    calls = []
    monkeypatch.setattr(geocoding, "_get", lambda path, params: calls.append(params) or [])
    for city in City:
        assert city_client.get("/api/geocode/search", params={"city": city, "q": "station"}).status_code == 200
        assert list(map(float, calls[-1]["viewbox"].split(","))) == list(map(float, city_profile(city).viewbox.split(",")))
        assert calls[-1]["bounded"] == 1
    for city in (City.POZNAN, City.WROCLAW, City.WARSAW):
        with db.session_scope(city) as session:
            load_gtfs_zip(session, city_feed(city))
            index = get_index(session)
        estimator = CrowdEstimator(index, [], use_laya=False)
        assert not any("Gdańsk" in hotspot.name for hotspot in estimator.hotspots)
        areas = predict_areas(estimator, datetime(2026, 11, 16, 18), city_profile(city).bounds)
        assert areas
        south, west, north, east = city_profile(city).bounds
        assert all(south <= area.lat <= north and west <= area.lon <= east for area in areas)


@pytest.mark.parametrize(("city", "neighbor"), [
    (City.GDANSK, "Sopot"), (City.POZNAN, "Luboń"),
    (City.WROCLAW, "Siechnice"), (City.WARSAW, "Piaseczno"),
])
def test_suggestions_require_the_selected_locality_and_keep_order_and_limit(monkeypatch, city, neighbor):
    profile = city_profile(city)
    lat, lon = profile.centre

    def item(label, address, item_lat=lat):
        return {"display_name": label, "address": address, "lat": str(item_lat), "lon": str(lon)}

    results = [
        item(f"{profile.name} hotel, {neighbor}", {"city": neighbor, "city_district": profile.name}),
        item(f"{neighbor} hotel, {profile.name}", {"city": profile.name, "suburb": "Centre"}),
        item("Different district", {"city": unicodedata.normalize("NFD", profile.name.upper())}),
        item("Town classification", {"town": profile.name}),
        item("Outside bounds", {"city": profile.name}, profile.bounds[2] + 1),
        item("Missing locality", {"road": profile.name}),
        item("Missing details", None),
    ]
    calls = []
    monkeypatch.setattr(geocoding, "_get", lambda path, params: calls.append((path, params)) or results)

    suggestions = geocoding.search("common name", limit=2, city=city)
    assert [suggestion["label"] for suggestion in suggestions] == [
        f"{neighbor} hotel, {profile.name}", "Different district",
    ]
    assert calls[0][1]["limit"] > 2
    assert [suggestion["label"] for suggestion in geocoding.search("common name", limit=10, city=city)] == [
        f"{neighbor} hotel, {profile.name}", "Different district", "Town classification",
    ]


def test_wroclaw_discovery_checks_actual_feed_validity_instead_of_the_first_listing_row(monkeypatch):
    today = datetime.now().date()
    future, valid = today + timedelta(days=1), today - timedelta(days=2)
    html = "".join(
        f'<tr><td>OtwartyWroclaw_rozklad_jazdy_GTFS_{day:%d%m%Y}</td><td><a href="/hdb/download/{id}/">Download</a></td></tr>'
        for id, day in ((160, valid), (162, future))
    )
    response = httpx.Response(200, text=html, request=httpx.Request("GET", "https://example.com"))
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.get.return_value = response
    monkeypatch.setattr(city_data.httpx, "Client", Mock(return_value=client))
    valid_data = city_feed(City.WROCLAW, valid.strftime("%Y%m%d"), (today + timedelta(days=7)).strftime("%Y%m%d"))
    future_data = city_feed(City.WROCLAW, future.strftime("%Y%m%d"), (future + timedelta(days=7)).strftime("%Y%m%d"))
    download = Mock(side_effect=lambda url: future_data if url.endswith("/162/") else valid_data)
    monkeypatch.setattr(city_data, "download_gtfs", download)
    assert city_data.download_city_gtfs(city_profile(City.WROCLAW)) == valid_data
    assert download.call_count == 2


def test_realtime_uses_separate_publisher_urls_and_never_guesses_wroclaw_trips(monkeypatch):
    from app.config import get_settings
    from tests.test_realtime import _feed

    monkeypatch.setattr(get_settings(), "gtfs_rt_enabled", True)
    for city in (City.GDANSK, City.POZNAN):
        service = realtime.RealtimeService(city)
        fetch = Mock(return_value=_feed())
        monkeypatch.setattr(service, "_fetch", fetch)
        assert service.trip_updates() and service.vehicle_positions()
        assert [call.args[0] for call in fetch.call_args_list] == [
            city_profile(city).trip_updates_url, city_profile(city).vehicle_positions_url,
        ]
    for city in (City.WROCLAW, City.WARSAW):
        service = realtime.RealtimeService(city)
        fetch = Mock()
        monkeypatch.setattr(service, "_fetch", fetch)
        assert service.vehicle_positions() == [] and service.vehicle_error
        assert not fetch.called
