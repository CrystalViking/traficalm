# TrafiCalm – technical details

Back to the [README](../README.md).

* [Architecture](#architecture)
* [Configuration](#configuration)
* [City timetables](#city-timetables)
* [How the crowd index works](#how-the-crowd-index-works)
* [Laya AI integration](#laya-ai-integration)
* [Walking logic](#walking-logic)
* [Editing a planned journey](#editing-a-planned-journey)
* [Map forecasts and live vehicles](#map-forecasts-and-live-vehicles)
* [REST API](#rest-api)
* [Tests](#tests)
* [Data sources and licences](#data-sources-and-licences)

## Architecture

| Part | Technology | Role |
| --- | --- | --- |
| `frontend/` | React + TypeScript (Vite), Leaflet / react-leaflet | UI, map, settings (saved in `localStorage`) |
| `backend/` | Python 3.12, FastAPI, SQLAlchemy | REST API, GTFS import, journey planner, crowd model |
| Database | PostgreSQL or SQLite | One independent database per city |
| Laya service | `laya-serve` (CPU) | Optional AI crowd estimate for transit rides |
| External | OpenStreetMap tiles, Nominatim, OSRM foot routing, GTFS / GTFS-RT feeds | Map, addresses, walking paths, timetables, delays |

The backend builds an in-memory routing index from the timetable at start-up. The Vite dev server
proxies `/api` to the backend; the Docker frontend image serves the built app with nginx.

## Configuration

Backend settings are environment variables with the `TRAFICALM_` prefix; `backend/.env` is also read.

| Variable | Default | Description |
| --- | --- | --- |
| `TRAFICALM_DB_HOST` / `_DB_PORT` / `_DB_NAME` / `_DB_USER` / `_DB_PASSWORD` | `localhost` / `5432` / `traficalm` / `traficalm` / `traficalm` | PostgreSQL connection (Gdańsk) |
| `TRAFICALM_DATABASE_URL` | – | Full SQLAlchemy URL, overrides the values above (e.g. `sqlite:///data/traficalm.sqlite3`) |
| `TRAFICALM_POZNAN_DATABASE_URL` | `sqlite:///data/poznan.sqlite3` | Poznań database |
| `TRAFICALM_WROCLAW_DATABASE_URL` | `sqlite:///data/wroclaw.sqlite3` | Wrocław database |
| `TRAFICALM_WARSAW_DATABASE_URL` | `sqlite:///data/warsaw.sqlite3` | Warsaw database |
| `TRAFICALM_GTFS_STATIC_URL` | ZTM Gdańsk GTFS | Static timetable zip |
| `TRAFICALM_GTFS_RT_TRIP_UPDATES_URL`, `TRAFICALM_GTFS_RT_VEHICLE_POSITIONS_URL` | ZTM Gdańsk GTFS-RT | Real-time feeds |
| `TRAFICALM_GTFS_RT_ENABLED` | `true` | Disable real-time lookups |
| `TRAFICALM_POZNAN_GTFS_STATIC_URL` | ZTM Poznań latest GTFS | Static timetable zip |
| `TRAFICALM_POZNAN_GTFS_RT_TRIP_UPDATES_URL`, `TRAFICALM_POZNAN_GTFS_RT_VEHICLE_POSITIONS_URL` | ZTM Poznań GTFS-RT | Real-time feeds |
| `TRAFICALM_WROCLAW_GTFS_LISTING_URL` | CUI Wrocław dated GTFS listing | Finds a currently valid feed |
| `TRAFICALM_WROCLAW_GTFS_STATIC_URL` | empty | Direct zip, overrides listing discovery |
| `TRAFICALM_WARSAW_GTFS_STATIC_URL` | `https://mkuran.pl/gtfs/warsaw.zip` | Community ZTM Warszawa conversion |
| `TRAFICALM_NOMINATIM_URL` | `https://nominatim.openstreetmap.org` | Geocoder (use your own instance for heavy traffic) |
| `TRAFICALM_OSRM_FOOT_URL` | `https://routing.openstreetmap.de/routed-foot` | Walking router |
| `TRAFICALM_WALKING_ROUTER_ENABLED` | `true` | When `false`, uses straight lines × detour factor |
| `TRAFICALM_HIGH_CROWD_THRESHOLD` | `60` | Internal fallback; `/api/plan` uses `crowd_tolerance` instead |
| `TRAFICALM_MAX_AVOIDANCE_WALK_M` | `1500` | Longest extra walk used to avoid crowds |
| `TRAFICALM_LAYA_URL` | `http://127.0.0.1:8001` | `laya-serve` URL (Docker: `http://laya:8000`); empty disables Laya |
| `TRAFICALM_LAYA_API_KEY` | – | Sent to `laya-serve` when it runs with `LAYA_API_KEY` |
| `TRAFICALM_LAYA_WEIGHT` | `0.4` | Share of the Laya estimate in the final crowd index |
| `TRAFICALM_LAYA_TIMEOUT_SECONDS` | `60` | Per-batch scoring timeout |
| `TRAFICALM_HOTSPOTS_FILE` | – | JSON overriding the built-in crowded places (Gdańsk) |
| `TRAFICALM_POZNAN_HOTSPOTS_FILE`, `TRAFICALM_WROCLAW_HOTSPOTS_FILE`, `TRAFICALM_WARSAW_HOTSPOTS_FILE` | – | Per-city hotspot overrides |
| `TRAFICALM_ADMIN_TOKEN` | – | Enables `POST/DELETE /api/events` (header `X-Admin-Token`) |
| `TRAFICALM_CORS_ORIGINS` | `*` | Comma-separated allowed origins |

Frontend: `VITE_API_URL` (API base URL for production builds, default same origin) and
`VITE_API_PROXY` (dev-server proxy target, default `http://localhost:8000`).

## City timetables

Select a city in Settings; the first selection downloads and imports its timetable. Data persists
between restarts, and an expired feed with a published end date is refreshed automatically.
Never point two cities at the same database: GTFS identifiers overlap between publishers.

Manual import or refresh (from `backend`, using `gdansk`, `poznan`, `wroclaw` or `warsaw`):

```bash
python -m app.cli init-db --city warsaw
python -m app.cli load-gtfs --city warsaw
python -m app.cli load-gtfs --file path/to/feed.zip --city warsaw   # local archive
```

Restart the backend after a CLI import so the routing index uses the new data
(the in-app preparation endpoint rebuilds its own index without a restart).
Optional large local events (matches, concerts, fairs) for the crowd model:

```bash
python -m app.cli import-events data/events.example.json
```

Wrocław and Warsaw do not assign live GPS to selected trips (Wrocław's public GPS cannot identify
them; Warsaw's community matching is best-effort). Timetable-based forecasts still work.

## How the crowd index works

For every stop visit the backend computes features (each 0–1):

* **Rush hours** – weekday peaks around 7:30 and 16:00, weaker at weekends and at night.
* **Busy places** – distance to known crowded places (main stations, city centres, shopping centres,
  stadiums, airports…; `backend/app/crowd/hotspots.py`), with kind-specific profiles for nightlife,
  retail hours, city-centre activity and summer beach demand.
* **Large local events** – from the `local_events` table; crowds build before the start and peak after the end.
* **Heavily used stops and lines** – number of departures in the timetable.

A ride's index combines its stops (busiest part weighs most) with line usage; walking legs are
weighted lower. Itinerary index = `0.6 × worst leg + 0.4 × duration-weighted mean`. Options are sorted
by crowd index, then arrival time. Bands: low 0–24 %, moderate 25–49 %, high 50–74 %, very high 75–100 %.

The crowd map uses 250 m grid cells (500 m when zoomed out) on a fixed city-wide lattice, plus hotspots,
events and busy stops. Same-band neighbours are merged into polygons. High/very-high areas are always
shown; lower bands are clipped to a 500 m corridor around the route. These are estimates, not measurements.

## Laya AI integration

[Laya](https://github.com/NandhaKishorM/laya) (Apache-2.0) is an open-source decision model served over HTTP
by `laya-serve`. When the user enables **Use Laya AI** in Settings (off by default), each transit ride is
described in plain language (time, places, events, usage) and Laya answers on a 5-level scale (empty → packed).
That answer is blended into the rule-based ride score with weight `TRAFICALM_LAYA_WEIGHT` (0.4).

* Laya only refines transit-ride scores and therefore ranking. It does not create routes, find events,
  change stop warnings or colour the crowd map.
* Identical ride descriptions are scored once per plan, in small batches. CPU scoring can take tens of seconds.
* Responses report `used`, `partial`, `unavailable`, `disabled` or `not-needed`; the UI never labels
  unscored results as AI-scored.
* **Fastest** walking logic never uses Laya.

## Walking logic

* **Fastest** – ignores crowd rules and ranks by earliest arrival.
* **Flexible** (default) – the standard crowd-aware planner with crowd-avoiding variants
  (board later, get off earlier, or split a ride and walk around a crowded section).
* **Strict** – keeps every walk out of high and very high crowd-map areas (50 %+), from departure
  until an hour later and during each walk, using the same grid as the map. Walks detour around
  such areas; journeys that cannot avoid them are dropped. A walk may cross an area only to leave a
  start or reach a destination inside it, along the shortest way out plus 50 m. Results are ranked by
  metres walked inside such areas (50 m steps), then crowd index.

## Editing a planned journey

Click a stop in the list or on the map. Stops above your crowd tolerance offer avoidance immediately;
others show **Avoid stop**.

* **Remove stop and replan** searches again from the original endpoints, excluding the stop and its 100 m surroundings.
* Or choose **where to get off** (up to three preceding stops, or the stop itself) and a nearby boarding stop
  within the walking radius (0.5 km default). The journey up to the exit is kept; a walk and onward transit replace the rest.

Pick a suggestion and press **Confirm new route**. Nothing changes until you confirm.

## Map forecasts and live vehicles

* Forecasts cover 60 minutes from departure (or now). Purple arrows are **inferred demand shifts** within 3 km,
  not observed people. The optional time slider explores 0–24 h in 15-minute steps.
* Dotted rings are **timetable-estimated** vehicle positions; solid markers are live GPS for the selected trips only,
  matched by trip ID and service date and dropped when older than 120 s.
* Real-time delays (GTFS-RT) refresh every 30 s.

## REST API

Interactive OpenAPI docs: <http://localhost:8000/docs>.
City-dependent endpoints take `city=gdansk|poznan|wroclaw|warsaw` (default `gdansk`).
Prepare a city before planning; missing transit data returns **503**.

| Method & path | Description |
| --- | --- |
| `GET /api/health` | Status and loaded timetable size |
| `POST /api/cities/{city}/prepare` | Download/import if missing or expired, build the index; returns readiness, validity, source and credits |
| `GET /api/geocode/search?q=` | Address suggestions within the city (min. 3 characters) |
| `GET /api/geocode/reverse?lat=&lon=` | Address of a GPS position |
| `GET /api/plan?from_lat=&from_lon=&to_lat=&to_lon=[&departure=][&max_results=][&crowd_tolerance=60][&avoid_stop_ids=][&use_laya=false][&strict_walking=false][&fastest=false]` | Journey options sorted by crowd index; repeat `avoid_stop_ids` (max 20); `strict_walking` / `fastest` select the walking logic |
| `GET /api/stops/nearby?stop_id=[&at=][&excluded_stop_ids=][&radius_m=500]` | Stops within 100–20,000 m, nearest first, with crowd estimates |
| `POST /api/plan/continue` | Rebuild a journey after an edit: original endpoints and departure, `prefix_rides`, `exit_stop_id`, `boarding_stop_id`, `avoid_stop_ids`, `crowd_tolerance`, `use_laya`, `walk_radius_m` |
| `GET /api/crowd/areas[?at=&south=&west=&north=&east=]` | Crowd-band polygons for the bounds |
| `POST /api/crowd/areas` | Same, with `route_geometry` (≤ 20,000 `[lat, lon]` points) for corridor clipping |
| `POST /api/crowd/forecast` | End-of-hour polygons, crowd changes and demand-shift arrows |
| `POST /api/vehicles/forecast` | Estimated positions of selected rides over the next hour |
| `GET /api/trips/{trip_id}/realtime[?service_date=YYYYMMDD]` | Delays per stop and vehicle position |
| `GET /api/vehicles[?trip_ids=a,b]` | Live vehicle positions |
| `GET /api/events`, `POST /api/events`, `DELETE /api/events/{id}` | Local events (writes need `X-Admin-Token`) |

## Tests

```bash
cd backend && python -m pytest -q        # in-memory SQLite and a synthetic GTFS feed
cd frontend && npm test && npm run lint && npm run build
```

## Data sources and licences

* Gdańsk: [ZTM Gdańsk Open Data](https://ckan.multimediagdansk.pl/dataset/tristar), CC BY 4.0.
* Poznań: [ZTM Poznań developer data](https://www.ztm.poznan.pl/otwarte-dane/dla-deweloperow/), under its [reuse terms](https://www.ztm.poznan.pl/wp-content/uploads/2024/07/Zasady-korzystania-ZTM-Poznan.pdf).
* Wrocław: [UM Wrocław / MPK timetable data](https://open-data.cui.wroclaw.pl/hdb/metadane/13/), CC0 1.0.
* Warsaw: [Mikołaj Kuranowski's community GTFS](https://mkuran.pl/gtfs/) based on ZTM Warszawa data
  ([usage terms](https://www.ztm.waw.pl/pliki-do-pobrania/dane-rozkladowe/)); not an official API.
* Maps, geocoding and walking routes: © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors (ODbL).
  Follow the [Nominatim](https://operations.osmfoundation.org/policies/nominatim/) and
  [tile](https://operations.osmfoundation.org/policies/tiles/) usage policies; use your own instances in production.
* [Laya](https://github.com/NandhaKishorM/laya) – Apache-2.0.
