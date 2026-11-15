"""Application settings, configurable through environment variables (prefix ``TRAFICALM_``)."""

from functools import lru_cache

from sqlalchemy import URL
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_CROWD_TOLERANCE = 60


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRAFICALM_", env_file=".env", extra="ignore")

    # Either a full SQLAlchemy URL or the individual components below.
    database_url: str = ""
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "traficalm"
    db_user: str = "traficalm"
    db_password: str = "traficalm"
    timezone: str = "Europe/Warsaw"

    # ZTM Gdańsk open data (https://ckan.multimediagdansk.pl/dataset/tristar), CC BY 4.0
    gtfs_static_url: str = (
        "https://ckan.multimediagdansk.pl/dataset/c24aa637-3619-4dc2-a171-a23eec8f2172/"
        "resource/30e783e4-2bec-4a7d-bb22-ee3e3b26ca96/download/gtfsgoogle.zip"
    )
    gtfs_rt_trip_updates_url: str = "https://ckan2.multimediagdansk.pl/gtfs-rt?feed=tripUpdates"
    gtfs_rt_vehicle_positions_url: str = "https://ckan2.multimediagdansk.pl/gtfs-rt?feed=vehiclePositions"
    gtfs_rt_cache_seconds: int = 20
    gtfs_rt_enabled: bool = True

    poznan_database_url: str = "sqlite:///data/poznan.sqlite3"
    wroclaw_database_url: str = "sqlite:///data/wroclaw.sqlite3"
    warsaw_database_url: str = "sqlite:///data/warsaw.sqlite3"
    poznan_gtfs_static_url: str = "https://www.ztm.poznan.pl/pl/dla-deweloperow/getGTFSFile"
    poznan_gtfs_rt_trip_updates_url: str = "https://www.ztm.poznan.pl/pl/dla-deweloperow/getGtfsRtFile/?file=trip_updates.pb"
    poznan_gtfs_rt_vehicle_positions_url: str = "https://www.ztm.poznan.pl/pl/dla-deweloperow/getGtfsRtFile/?file=vehicle_positions.pb"
    wroclaw_gtfs_static_url: str = ""
    wroclaw_gtfs_listing_url: str = "https://open-data.cui.wroclaw.pl/hdb/ft/6/"
    warsaw_gtfs_static_url: str = "https://mkuran.pl/gtfs/warsaw.zip"
    poznan_hotspots_file: str = ""
    wroclaw_hotspots_file: str = ""
    warsaw_hotspots_file: str = ""

    # OpenStreetMap services
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    osrm_foot_url: str = "https://routing.openstreetmap.de/routed-foot"
    walking_router_enabled: bool = True
    user_agent: str = "TrafiCalm/0.1 (crowd-avoiding journey planner)"
    # Bound Gdańsk search candidates before filtering to the city (lon_min,lat_max,lon_max,lat_min)
    geocode_viewbox: str = "18.30,54.65,18.95,54.25"
    geocode_countrycodes: str = "pl"

    # Planner tuning
    walk_speed_mps: float = 1.2
    walk_detour_factor: float = 1.3
    max_access_walk_m: float = 900.0
    max_avoidance_walk_m: float = 1500.0
    max_transfer_walk_m: float = 300.0
    min_transfer_seconds: int = 120
    search_window_minutes: int = 90
    max_results: int = 12
    high_crowd_threshold: int = DEFAULT_CROWD_TOLERANCE

    # Laya open-source decision model (https://github.com/NandhaKishorM/laya), served by `laya-serve`.
    # Local Laya service; an empty URL disables the integration server-wide.
    laya_url: str = "http://127.0.0.1:8001"
    laya_api_key: str = ""
    laya_model: str = "english"
    laya_weight: float = 0.4
    laya_timeout_seconds: float = 60.0

    # Optional JSON file overriding the built-in crowd hotspots
    hotspots_file: str = ""

    cors_origins: str = "*"
    # Token required to create/delete local events through the API (event writes are disabled when empty)
    admin_token: str = ""

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        # URL.create(drivername, username, secret, host, port, database)
        url = URL.create("postgresql+psycopg", self.db_user, self.db_password, self.db_host, self.db_port, self.db_name)
        return url.render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
