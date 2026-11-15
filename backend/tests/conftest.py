import io
import os
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

os.environ.update(
    {
        "TRAFICALM_DATABASE_URL": "sqlite://",
        "TRAFICALM_WALKING_ROUTER_ENABLED": "false",
        "TRAFICALM_GTFS_RT_ENABLED": "false",
        "TRAFICALM_LAYA_URL": "",
        "TRAFICALM_ADMIN_TOKEN": "",
    }
)

from app import db, transit_index  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.crowd.estimator import CrowdEstimator  # noqa: E402
from app.crowd.laya_model import LayaClient  # noqa: E402
from app.gtfs_static import load_gtfs_zip  # noqa: E402
from app.transit_index import TransitIndex  # noqa: E402

TZ = ZoneInfo("Europe/Warsaw")
MONDAY_RUSH = datetime(2026, 11, 16, 7, 20, tzinfo=TZ)
MONDAY_NIGHT = datetime(2026, 11, 16, 22, 30, tzinfo=TZ)

STOPS = {
    "S1": ("Siedlce", 54.3300, 18.6000),
    "S2": ("Zakopiańska", 54.3380, 18.6200),
    "S3": ("Okopowa", 54.3500, 18.6380),
    "MAIN": ("Gdańsk Główny", 54.3556, 18.6447),
    "S4": ("Wały Piastowskie", 54.3610, 18.6420),
    "S5": ("Jaśkowa Dolina", 54.3700, 18.6300),
    "T1": ("Siennicka", 54.3750, 18.6700),
    "T2": ("Wiślna", 54.3800, 18.6900),
    "H1": ("Długie Ogrody", 54.3450, 18.6600),
    "H2": ("Elbląska", 54.3400, 18.6800),
}


def _hms(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def build_gtfs_zip() -> bytes:
    trips, stop_times = [], []

    def add_line(route_id: str, stops: list[str], start: int, end: int, headway: int, hop: int) -> None:
        t = start
        n = 0
        while t <= end:
            trip_id = f"{route_id}_{n}"
            trips.append(f"{route_id},WD,{trip_id},{STOPS[stops[-1]][0]},0,")
            for seq, stop in enumerate(stops):
                tt = _hms(t + seq * hop)
                stop_times.append(f"{trip_id},{tt},{tt},{stop},{seq + 1}")
            t += headway
            n += 1

    add_line("8", ["S1", "S2", "S3", "MAIN", "S4", "S5"], 5 * 3600, 23 * 3600, 600, 180)
    add_line("2", ["MAIN", "H1", "H2"], 5 * 3600, 23 * 3600, 150, 180)
    add_line("2R", ["H2", "H1", "MAIN"], 5 * 3600, 23 * 3600, 150, 180)
    add_line("10", ["S5", "T1", "T2"], 5 * 3600, 23 * 3600, 600, 240)

    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\nZTM,ZTM Gdańsk,https://ztm.gda.pl,Europe/Warsaw\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
        + "".join(f"{sid},{name},{lat},{lon}\n" for sid, (name, lat, lon) in STOPS.items()),
        "routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\n"
        "8,ZTM,8,Siedlce - Jaśkowa Dolina,0\n2,ZTM,2,Główny - Elbląska,3\n2R,ZTM,2,Elbląska - Główny,3\n"
        "10,ZTM,10,Jaśkowa Dolina - Wiślna,3\n",
        "trips.txt": "route_id,service_id,trip_id,trip_headsign,direction_id,shape_id\n" + "\n".join(trips) + "\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n" + "\n".join(stop_times) + "\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        "WD,1,1,1,1,1,0,0,20260101,20271231\n",
        "calendar_dates.txt": "service_id,date,exception_type\nWD,20261111,2\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            # BOM on one file to make sure the loader copes with it
            zf.writestr(name, ("\ufeff" if name == "stops.txt" else "") + content)
    return buf.getvalue()


class FakeLaya(LayaClient):
    def __init__(self, value: float | None) -> None:
        super().__init__()
        self.value = value
        self.calls: list[list[dict]] = []

    @property
    def enabled(self) -> bool:
        return True

    def score_crowd(self, states):
        self.calls.append(states)
        return [self.value for _ in states]


@pytest.fixture()
def engine():
    get_settings.cache_clear()
    # In-memory SQLite: keep one connection so that every session sees the same database
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db._engine = eng
    db._session_factory = db.sessionmaker(bind=eng, expire_on_commit=False)
    db.create_all()
    transit_index.reset_index()
    yield eng
    transit_index.reset_index()


@pytest.fixture()
def loaded_db(engine):
    with db.session_scope() as session:
        load_gtfs_zip(session, build_gtfs_zip())
    return engine


@pytest.fixture()
def index(loaded_db) -> TransitIndex:
    with db.session_scope() as session:
        return TransitIndex.from_session(session)


@pytest.fixture()
def estimator(index) -> CrowdEstimator:
    return CrowdEstimator(index, events=[], laya=FakeLaya(None))
