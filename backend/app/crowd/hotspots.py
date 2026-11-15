"""Places that are typically crowded (main stations, city centre, malls, stadiums...).

Defaults cover each supported city. Override with the city's configured hotspots
JSON file containing a list of objects with the same fields.
Coordinates are approximate centroids.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..cities import City, city_profile


@dataclass(frozen=True)
class Hotspot:
    name: str
    lat: float
    lon: float
    radius_m: float
    weight: float  # 0..1, how crowded the place gets at its busiest
    kind: str = "hub"  # hub | centre | retail | venue | airport | nightlife | beach


DEFAULT_HOTSPOTS: tuple[Hotspot, ...] = (
    Hotspot("Gdańsk Główny (main station)", 54.3556, 18.6447, 450, 1.0, "hub"),
    Hotspot("Śródmieście / Main Town (Długi Targ)", 54.3486, 18.6531, 600, 0.85, "centre"),
    Hotspot("Brama Wyżynna / Hucisko", 54.3510, 18.6440, 300, 0.75, "hub"),
    Hotspot("Gdańsk Wrzeszcz station", 54.3810, 18.6058, 400, 0.85, "hub"),
    Hotspot("Galeria Bałtycka", 54.3832, 18.5996, 350, 0.75, "retail"),
    Hotspot("Gdańsk Oliwa station", 54.4119, 18.5626, 300, 0.6, "hub"),
    Hotspot("Galeria Przymorze / Alchemia", 54.4072, 18.5755, 300, 0.55, "retail"),
    Hotspot("Gdańsk Przymorze-Uniwersytet (university)", 54.3970, 18.5730, 450, 0.6, "hub"),
    Hotspot("Polsat Plus Arena Gdańsk (stadium)", 54.3897, 18.6403, 600, 0.5, "venue"),
    Hotspot("AmberExpo", 54.3925, 18.6240, 400, 0.4, "venue"),
    Hotspot("Ergo Arena", 54.4277, 18.5835, 500, 0.4, "venue"),
    Hotspot("Gdańsk Airport", 54.3776, 18.4662, 500, 0.6, "airport"),
    Hotspot("Forum Gdańsk", 54.3501, 18.6436, 250, 0.7, "retail"),
    Hotspot("Main Town pubs and clubs", 54.3505, 18.6550, 350, 0.95, "nightlife"),
    Hotspot("Sopot / Monte Cassino nightlife", 54.4430, 18.5650, 450, 1.0, "nightlife"),
    Hotspot("Gdynia downtown", 54.5180, 18.5400, 500, 0.85, "centre"),
    Hotspot("Gdynia Główna station", 54.5210, 18.5300, 400, 0.9, "hub"),
    Hotspot("Brzeźno beach", 54.4105, 18.6320, 550, 0.95, "beach"),
    Hotspot("Jelitkowo beach", 54.4225, 18.5980, 500, 0.9, "beach"),
    Hotspot("Sopot beach and pier", 54.4465, 18.5730, 600, 1.0, "beach"),
)


CITY_HOTSPOTS = {
    City.GDANSK: DEFAULT_HOTSPOTS,
    City.POZNAN: (
        Hotspot("Poznań Główny", 52.4013, 16.9115, 450, 1.0, "hub"),
        Hotspot("Stary Rynek", 52.4083, 16.9343, 550, 0.85, "centre"),
        Hotspot("Stary Rynek pubs and clubs", 52.4083, 16.9343, 350, 0.95, "nightlife"),
        Hotspot("Stary Browar", 52.4025, 16.9283, 300, 0.75, "retail"),
        Hotspot("Międzynarodowe Targi Poznańskie", 52.4024, 16.9009, 500, 0.5, "venue"),
        Hotspot("Stadion Poznań", 52.3976, 16.8573, 600, 0.5, "venue"),
    ),
    City.WROCLAW: (
        Hotspot("Wrocław Główny", 51.0980, 17.0366, 450, 1.0, "hub"),
        Hotspot("Rynek", 51.1100, 17.0320, 550, 0.85, "centre"),
        Hotspot("Dzielnica Czterech Wyznań pubs and clubs", 51.1089, 17.0255, 350, 0.95, "nightlife"),
        Hotspot("Galeria Dominikańska", 51.1081, 17.0459, 300, 0.75, "retail"),
        Hotspot("Hala Stulecia", 51.1068, 17.0773, 450, 0.5, "venue"),
        Hotspot("Tarczyński Arena", 51.1418, 16.9434, 600, 0.5, "venue"),
    ),
    City.WARSAW: (
        Hotspot("Warszawa Centralna", 52.2287, 21.0033, 500, 1.0, "hub"),
        Hotspot("Śródmieście / Pałac Kultury", 52.2318, 21.0060, 600, 0.85, "centre"),
        Hotspot("Stare Miasto", 52.2497, 21.0122, 500, 0.85, "centre"),
        Hotspot("Nowy Świat pubs and clubs", 52.2344, 21.0197, 400, 0.95, "nightlife"),
        Hotspot("Złote Tarasy", 52.2300, 21.0023, 350, 0.75, "retail"),
        Hotspot("PGE Narodowy", 52.2395, 21.0458, 650, 0.5, "venue"),
        Hotspot("Lotnisko Chopina", 52.1672, 20.9679, 500, 0.6, "airport"),
        Hotspot("Plaża Poniatówka", 52.2350, 21.0460, 450, 0.9, "beach"),
    ),
}


@lru_cache
def load_hotspots(city: City = City.GDANSK) -> tuple[Hotspot, ...]:
    path = city_profile(city).hotspots_file
    if not path:
        return CITY_HOTSPOTS[city]
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return tuple(Hotspot(**item) for item in data)
