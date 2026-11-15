#!/bin/sh
set -e

python -m app.cli init-db

# Import the ZTM Gdańsk GTFS timetable on first start (set LOAD_GTFS_ON_START=0 to skip).
if [ "${LOAD_GTFS_ON_START:-1}" = "1" ]; then
  python -m app.cli load-gtfs --if-empty || echo "WARNING: could not load the GTFS timetable; run 'python -m app.cli load-gtfs' later"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers "$@"
