"""Supported transit publishers and city-specific geographic bounds."""

from dataclasses import dataclass, replace
from enum import StrEnum

from .config import get_settings


class City(StrEnum):
    GDANSK = "gdansk"
    POZNAN = "poznan"
    WROCLAW = "wroclaw"
    WARSAW = "warsaw"


@dataclass(frozen=True)
class CityProfile:
    city: City
    name: str
    agency: str
    bounds: tuple[float, float, float, float]
    centre: tuple[float, float]
    source_url: str
    license: str
    static_url: str = ""
    listing_url: str = ""
    trip_updates_url: str = ""
    vehicle_positions_url: str = ""
    hotspots_file: str = ""

    @property
    def viewbox(self) -> str:
        south, west, north, east = self.bounds
        return f"{west},{north},{east},{south}"

    @property
    def realtime_supported(self) -> bool:
        return bool(self.vehicle_positions_url)


PROFILES = {
    City.GDANSK: CityProfile(
        City.GDANSK, "Gdańsk", "ZTM Gdańsk", (54.25, 18.30, 54.65, 18.95), (54.352, 18.646),
        "https://ckan.multimediagdansk.pl/dataset/tristar", "CC BY 4.0",
    ),
    City.POZNAN: CityProfile(
        City.POZNAN, "Poznań", "ZTM Poznań", (52.15, 16.55, 52.65, 17.25), (52.4064, 16.9252),
        "https://www.ztm.poznan.pl/otwarte-dane/dla-deweloperow/", "ZTM Poznań reuse terms",
    ),
    City.WROCLAW: CityProfile(
        City.WROCLAW, "Wrocław", "UM Wrocław / MPK", (50.90, 16.75, 51.30, 17.35), (51.1079, 17.0385),
        "https://open-data.cui.wroclaw.pl/hdb/metadane/13/", "CC0 1.0",
    ),
    City.WARSAW: CityProfile(
        City.WARSAW, "Warszawa", "ZTM Warszawa / Mikołaj Kuranowski",
        (51.95, 20.65, 52.55, 21.45), (52.2297, 21.0122),
        "https://mkuran.pl/gtfs/", "ZTM Warszawa reuse terms / ODbL (bus shapes)",
    ),
}


def city_profile(city: City) -> CityProfile:
    settings = get_settings()
    profile = PROFILES[city]
    if city == City.GDANSK:
        return replace(
            profile, static_url=settings.gtfs_static_url,
            trip_updates_url=settings.gtfs_rt_trip_updates_url,
            vehicle_positions_url=settings.gtfs_rt_vehicle_positions_url,
            hotspots_file=settings.hotspots_file,
        )
    if city == City.POZNAN:
        return replace(
            profile, static_url=settings.poznan_gtfs_static_url,
            trip_updates_url=settings.poznan_gtfs_rt_trip_updates_url,
            vehicle_positions_url=settings.poznan_gtfs_rt_vehicle_positions_url,
            hotspots_file=settings.poznan_hotspots_file,
        )
    if city == City.WROCLAW:
        return replace(
            profile, static_url=settings.wroclaw_gtfs_static_url,
            listing_url=settings.wroclaw_gtfs_listing_url, hotspots_file=settings.wroclaw_hotspots_file,
        )
    return replace(profile, static_url=settings.warsaw_gtfs_static_url, hotspots_file=settings.warsaw_hotspots_file)
