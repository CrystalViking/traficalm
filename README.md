# 🌿 TrafiCalm

TrafiCalm is a public-transport journey planner for people who want to avoid crowds, for example
people with agoraphobia. It plans routes in **Gdańsk, Poznań, Wrocław and Warsaw**, estimates how
crowded each option will be (0–100 %) and lists the calmest routes first. It can also suggest
walking around busy stops and shows predicted crowded areas on the map.

![TrafiCalm demo: settings, planning a route, avoiding a crowded stop and picking an alternative](docs/demo.gif)

## How it works

* **Frontend** – React + TypeScript (Vite) with a Leaflet / OpenStreetMap map.
* **Backend** – Python FastAPI. It imports each city's public GTFS timetable into PostgreSQL or SQLite,
  plans journeys and calculates a crowd index from rush hours, busy places, local events and stop usage.
* **Live data** – GTFS-RT delays and vehicle positions; Nominatim for addresses and OSRM for walking routes.
* **Laya AI (optional)** – the open-source [Laya](https://github.com/NandhaKishorM/laya) model runs as a
  local service. When you turn on **Use Laya AI** in Settings, it reads a short description of each ride
  (time, places, events) and estimates how packed it will be. That estimate is blended with the
  built-in score to improve the ranking. It is off by default and the app works fully without it.

Crowd levels are estimates, not live measurements.

## Run locally (without Docker)

You need **Python 3.12+** and **Node.js 22+**. The steps below use SQLite, so no database server is needed.
Run each service in its own terminal, starting from the repository root.

**1. Settings** – create `backend/.env` with:

```dotenv
TRAFICALM_DATABASE_URL=sqlite:///data/traficalm.sqlite3
```

**2. Laya AI service** (optional; skip it if you don't need AI scoring) – terminal 1:

```powershell
# Windows (PowerShell)
cd backend
py -3.12 -m venv .venv-laya
.\.venv-laya\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.\.venv-laya\Scripts\python.exe -m pip install -r requirements-laya.txt
$env:LAYA_HOST="127.0.0.1"; $env:LAYA_PORT="8001"; $env:LAYA_DEVICE="cpu"
$env:LAYA_MODELS="english"; $env:LAYA_DEFAULT_MODEL="english"; $env:LAYA_PRELOAD="1"
.\.venv-laya\Scripts\laya-serve.exe
```

```bash
# macOS / Linux
cd backend
python3.12 -m venv .venv-laya
.venv-laya/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv-laya/bin/pip install -r requirements-laya.txt
LAYA_HOST=127.0.0.1 LAYA_PORT=8001 LAYA_DEVICE=cpu LAYA_MODELS=english \
  LAYA_DEFAULT_MODEL=english LAYA_PRELOAD=1 .venv-laya/bin/laya-serve
```

The first start downloads the model. It is ready when <http://localhost:8001/health> responds.

**3. Backend** – terminal 2:

```powershell
# Windows (PowerShell)
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app.cli load-gtfs --if-empty
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host localhost --port 8000
```

```bash
# macOS / Linux
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.cli load-gtfs --if-empty
.venv/bin/python -m uvicorn app.main:app --host localhost --port 8000
```

The first timetable import takes a few minutes. Check <http://localhost:8000/api/health>.

**4. Frontend** – terminal 3:

```bash
cd frontend
npm install        # on Windows PowerShell use npm.cmd
npm run dev        # http://localhost:5173
```

Open **<http://localhost:5173>**. Next time, only the last command of each step is needed
(for Laya, set the `LAYA_*` variables again). Stop everything with **Ctrl+C** in each terminal.

## Run with Docker

```bash
docker compose up --build
```

This starts PostgreSQL, the backend, the frontend and Laya. Open **<http://localhost:8080>**
(API docs at <http://localhost:8000/docs>). The first start downloads the timetable and the Laya model,
which takes a few minutes.

## More information

API reference, configuration options, the crowd model, walking logic, Laya details, tests and data
licences are in **[docs/TECHNICAL.md](docs/TECHNICAL.md)**.

## License and data

See [LICENSE](LICENSE). Timetables come from the cities' open data (ZTM Gdańsk, ZTM Poznań, UM Wrocław / MPK,
and a community Warsaw GTFS); maps © OpenStreetMap contributors. Details are in the
[technical document](docs/TECHNICAL.md#data-sources-and-licences).

---

*AI disclaimer: The project was created with Copilot Fusion.*
