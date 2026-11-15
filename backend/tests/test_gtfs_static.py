from sqlalchemy import func, select

from app import db, models
from app.gtfs_static import load_gtfs_zip, parse_gtfs_time
from tests.conftest import build_gtfs_zip


def test_parse_gtfs_time_supports_after_midnight():
    assert parse_gtfs_time("00:00:00") == 0
    assert parse_gtfs_time("7:05:30") == 7 * 3600 + 5 * 60 + 30
    assert parse_gtfs_time("25:10:00") == 25 * 3600 + 600


def test_load_gtfs_zip_imports_and_replaces(engine):
    with db.session_scope() as session:
        counts = load_gtfs_zip(session, build_gtfs_zip())
        assert counts["stops"] == 10
        assert counts["routes"] == 4
        # loading twice replaces data instead of duplicating it
        load_gtfs_zip(session, build_gtfs_zip())
        assert session.scalar(select(func.count()).select_from(models.Stop)) == 10
        main = session.get(models.Stop, "MAIN")
        assert main.stop_name == "Gdańsk Główny"
        assert session.scalar(select(func.count()).select_from(models.StopTime)) == counts["stop_times"]
