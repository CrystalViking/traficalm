from datetime import datetime, timedelta

from app.crowd.estimator import LegCrowd, itinerary_crowd
from app.crowd.features import (
    CrowdFeatures,
    EventInfo,
    event_factor,
    extract,
    hotspot_factor,
    peak_factor,
    point_crowd,
    vehicle_crowd,
)
from app.crowd.hotspots import DEFAULT_HOTSPOTS
from app.crowd.hotspots import Hotspot
from app.crowd.laya_model import parse_crowd_answer
from tests.conftest import MONDAY_NIGHT, MONDAY_RUSH, TZ, FakeLaya


def test_peak_factor_rush_hours_vs_night_and_weekend():
    assert peak_factor(MONDAY_RUSH) > 0.9
    assert peak_factor(MONDAY_RUSH.replace(hour=16, minute=0)) > 0.9
    assert peak_factor(MONDAY_RUSH.replace(hour=3)) <= 0.12
    sunday_morning = datetime(2026, 11, 15, 7, 30, tzinfo=TZ)
    assert peak_factor(sunday_morning) < 0.3


def test_hotspot_factor_main_station_and_far_away():
    value, name = hotspot_factor(54.3556, 18.6447, DEFAULT_HOTSPOTS)
    assert value > 0.95 and "Główny" in name
    value, name = hotspot_factor(54.30, 18.50, DEFAULT_HOTSPOTS)
    assert value == 0 and name is None


def test_event_factor_peaks_after_event_end():
    start = MONDAY_RUSH.replace(hour=18, minute=0)
    ev = EventInfo("Lechia match", 54.3897, 18.6403, start, start + timedelta(hours=2), 35_000, 1500)
    after = event_factor(54.39, 18.64, start + timedelta(hours=2, minutes=10), [ev])[0]
    during = event_factor(54.39, 18.64, start + timedelta(hours=1), [ev])[0]
    before = event_factor(54.39, 18.64, start - timedelta(minutes=30), [ev])[0]
    unrelated_time = event_factor(54.39, 18.64, start - timedelta(hours=5), [ev])[0]
    far = event_factor(54.30, 18.50, start + timedelta(hours=2, minutes=10), [ev])[0]
    assert after > before > during > 0
    assert unrelated_time == 0 and far < 0.01


def test_point_crowd_rules():
    quiet = point_crowd(CrowdFeatures(peak=0.1, hotspot=0.0, event=0.0, stop_load=0.2))
    rush_hub = point_crowd(CrowdFeatures(peak=1.0, hotspot=1.0, event=0.0, stop_load=1.0))
    event = point_crowd(CrowdFeatures(peak=0.1, hotspot=0.0, event=0.9, stop_load=0.2))
    assert 0 <= quiet < 0.2
    assert rush_hub == 1.0
    assert event >= 0.9
    assert vehicle_crowd([0.2, 0.9], 1.0, 1.0) > vehicle_crowd([0.2, 0.3], 1.0, 1.0)
    assert vehicle_crowd([], 1.0, 1.0) == 0


def test_itinerary_crowd_weights_worst_leg():
    calm = LegCrowd(0.1, 0.1)
    packed = LegCrowd(0.9, 0.9)
    assert itinerary_crowd([]) == 0
    assert itinerary_crowd([(calm, 600)]) == 10
    mixed = itinerary_crowd([(calm, 1200), (packed, 60)])
    assert 54 <= mixed <= 60  # worst leg dominates even if short


def test_parse_laya_answer():
    assert parse_crowd_answer({"answers": {"crowd": {"score": 2.0}}}) == 0.5
    assert parse_crowd_answer({"answers": {"crowd": {"score": 9}}}) == 1.0
    assert parse_crowd_answer({"answers": {"crowd": {"probabilities": {"0": 0.5, "4": 0.5}}}}) == 0.5
    assert parse_crowd_answer({"answers": {}}) is None


def test_estimator_blends_laya(index, monkeypatch):
    from app.config import get_settings
    from app.crowd.estimator import CrowdEstimator

    monkeypatch.setattr(get_settings(), "laya_weight", 0.5)
    laya = FakeLaya(0.0)
    est = CrowdEstimator(index, [], laya=laya)
    route = index.routes["8"]
    stops = [(index.stops[s], MONDAY_RUSH) for s in ("S3", "MAIN", "S4")]
    leg = est.transit_leg(route, "Jaśkowa Dolina", stops)
    assert leg.laya_state["line"].startswith("tram 8")
    assert "Gdańsk Główny (main station)" in leg.laya_state["near_busy_places"]
    assert est.refine_with_laya([leg])
    assert leg.laya_value == 0.0
    assert abs(leg.value - leg.rule_value * 0.5) < 1e-9
    assert len(laya.calls) == 1


def test_stop_crowd_higher_at_main_station_in_rush(estimator, index):
    main = estimator.stop_crowd(index.stops["MAIN"], MONDAY_RUSH)
    quiet = estimator.stop_crowd(index.stops["T2"], MONDAY_RUSH)
    night = estimator.stop_crowd(index.stops["MAIN"], MONDAY_NIGHT)
    assert main > quiet
    assert main > night


def test_nightlife_and_summer_beach_profiles_affect_point_predictions():
    nightlife = (Hotspot("Clubs", 54.35, 18.65, 400, 1.0, "nightlife"),)
    friday = MONDAY_RUSH.replace(day=20, hour=23)
    night = point_crowd(extract(54.35, 18.65, friday, nightlife, []))
    daytime = point_crowd(extract(54.35, 18.65, friday.replace(hour=10), nightlife, []))
    assert night >= 0.5 and night > daytime
    beach = (Hotspot("Beach", 54.41, 18.63, 500, 1.0, "beach"),)
    summer = friday.replace(month=7, hour=14)
    busy = point_crowd(extract(54.41, 18.63, summer, beach, []))
    winter = point_crowd(extract(54.41, 18.63, summer.replace(month=11), beach, []))
    assert busy >= 0.5 and busy > winter


def test_neighboring_crowd_bands_merge_and_preserve_high_crowd_holes():
    from shapely.geometry import Polygon, box
    from app.crowd.areas import AreaSample, _xy, merge_areas

    areas = merge_areas([
        AreaSample("Left", "area", box(0, 0, 100, 100), 10, []),
        AreaSample("Right", "area", box(100, 0, 200, 100), 20, []),
        AreaSample("Crowded centre", "event", box(50, 25, 150, 75), 85, ["event: match"]),
    ])
    assert len(areas) == 2
    low = next(a for a in areas if a.crowd_index < 25)
    high = next(a for a in areas if a.crowd_index >= 75)
    assert len(low.geometry) == 2
    polygons = [Polygon([_xy(*p) for p in a.geometry[0]], [[_xy(*p) for p in ring] for ring in a.geometry[1:]]) for a in (low, high)]
    assert abs(polygons[0].area - 15000) < 0.01
    assert polygons[0].intersection(polygons[1]).area < 0.01
    assert all(p.is_valid for p in polygons)
    assert "event: match" in high.reasons


def test_disconnected_regions_stay_separate_and_corridor_clips_only_lower_bands():
    from shapely.geometry import box
    from app.crowd.areas import AreaSample, merge_areas

    samples = [
        AreaSample("Near", "area", box(0, 0, 100, 100), 30, []),
        AreaSample("Far", "area", box(1000, 0, 1100, 100), 40, []),
        AreaSample("Always high", "area", box(2000, 0, 2100, 100), 65, []),
    ]
    assert len(merge_areas(samples)) == 3
    clipped = merge_areas(samples, box(-10, -10, 50, 110))
    assert len(clipped) == 2
    assert {a.name for a in clipped} == {"Near", "Always high"}


def test_crowd_grid_cells_snap_to_a_city_lattice(estimator):
    from app.crowd.areas import collect_samples

    def cells(bounds):
        samples = collect_samples(estimator, MONDAY_RUSH, bounds, (250.0,))
        return {s.footprint.bounds for s in samples if s.name == "Estimated area crowd"}

    # The map and strict walking request different bounds, but must see the same cells.
    shifted = cells((54.3412, 18.6037, 54.3801, 18.6655))
    assert shifted and shifted <= cells((54.33, 18.58, 54.39, 18.68))


def test_high_crowd_sampling_only_skips_cells_that_cannot_be_high(index):
    from app.crowd.areas import collect_samples
    from app.crowd.estimator import CrowdEstimator

    match = EventInfo("Lechia match", 54.3897, 18.6403, MONDAY_RUSH, MONDAY_RUSH + timedelta(hours=2), 35_000, 600)
    estimator = CrowdEstimator(index, events=[match], laya=FakeLaya(None))
    bounds = (54.32, 18.55, 54.42, 18.72)
    found = 0
    for when in (MONDAY_RUSH, MONDAY_RUSH.replace(hour=13), MONDAY_NIGHT):
        fast = collect_samples(estimator, when, bounds, (250.0, 500.0), 50)
        full = [s for s in collect_samples(estimator, when, bounds, (250.0, 500.0)) if s.crowd_index >= 50]
        assert sorted((s.footprint.bounds, s.crowd_index) for s in fast) == sorted(
            (s.footprint.bounds, s.crowd_index) for s in full
        )
        found += sum(s.name == "Estimated area crowd" for s in fast)
    assert found


def test_laya_opt_out_does_not_call_model_or_affect_other_estimators(index):
    from app.crowd.estimator import CrowdEstimator

    model = FakeLaya(0.9)
    opted_out = CrowdEstimator(index, [], laya=model, use_laya=False)
    opted_in = CrowdEstimator(index, [], laya=model)
    stops = [(index.stops[s], MONDAY_RUSH) for s in ("S3", "MAIN", "S4")]
    out_leg = opted_out.transit_leg(index.routes["8"], "Test", stops)
    in_leg = opted_in.transit_leg(index.routes["8"], "Test", stops)
    assert not opted_out.refine_with_laya([out_leg])
    assert model.calls == [] and out_leg.laya_value is None
    assert opted_in.refine_with_laya([in_leg])
    assert len(model.calls) == 1 and in_leg.laya_value == 0.9


def test_identical_ride_contexts_share_a_laya_score(index):
    from app.crowd.estimator import CrowdEstimator

    model = FakeLaya(0.8)
    estimator = CrowdEstimator(index, [], laya=model)
    stops = [(index.stops[s], MONDAY_RUSH) for s in ("S3", "MAIN", "S4")]
    legs = [estimator.transit_leg(index.routes["8"], "Test", stops) for _ in range(2)]
    assert estimator.refine_with_laya(legs)
    assert len(model.calls[0]) == 1
    assert all(leg.laya_value == 0.8 for leg in legs)


def test_laya_client_batches_and_backs_off(monkeypatch):
    import httpx

    from app.config import get_settings
    from app.crowd.laya_model import CROWD_QUESTION, LayaClient

    monkeypatch.setattr(get_settings(), "laya_url", "http://laya:8000")
    client = LayaClient()
    payloads = []

    def fake_post(payload):
        payloads.append(payload)
        return {"results": [{"answers": {"crowd": {"score": 1.0}}} for _ in payload["states"]]}

    monkeypatch.setattr(client, "_post", fake_post)
    states = [{"line": f"bus {i}"} for i in range(70)]
    assert client.score_crowd(states) == [0.25] * 70
    assert [len(p["states"]) for p in payloads] == [64, 6]
    assert payloads[0]["questions"] == CROWD_QUESTION and payloads[0]["model"] == "english"

    def failing_post(payload):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(client, "_post", failing_post)
    assert client.score_crowd(states[:2]) == [None, None]
    monkeypatch.setattr(client, "_post", fake_post)
    assert client.score_crowd(states[:2]) == [None, None]  # backing off after a failure
