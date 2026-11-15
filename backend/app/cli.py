"""Command line tools.

    python -m app.cli init-db
    python -m app.cli load-gtfs [--url URL | --file PATH] [--if-empty]
    python -m app.cli import-events events.json
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from . import models
from .db import create_all, session_scope
from .gtfs_static import download_gtfs, load_gtfs_zip
from .cities import City, city_profile
from .city_data import download_city_gtfs
from .transit_index import reset_index

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="traficalm")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-db", help="create database tables")
    gtfs = sub.add_parser("load-gtfs", help="download and import the static GTFS timetable")
    gtfs.add_argument("--url", help="override the selected city's GTFS zip URL")
    gtfs.add_argument("--file", help="local GTFS zip file")
    gtfs.add_argument("--if-empty", action="store_true", help="skip when a timetable is already loaded")
    events = sub.add_parser("import-events", help="import large local events from a JSON list")
    events.add_argument("path")
    for command in (init, gtfs, events):
        command.add_argument("--city", type=City, choices=list(City), default=City.GDANSK)
    args = parser.parse_args(argv)

    create_all(args.city)
    if args.command == "load-gtfs":
        if args.if_empty:
            with session_scope(args.city) as session:
                if session.scalar(select(models.Stop.stop_id).limit(1)) is not None:
                    print("GTFS timetable already loaded, skipping")
                    return
        data = Path(args.file).read_bytes() if args.file else download_gtfs(args.url) if args.url else download_city_gtfs(city_profile(args.city))
        with session_scope(args.city) as session:
            print(load_gtfs_zip(session, data))
            reset_index(session.get_bind())
    elif args.command == "import-events":
        items = json.loads(Path(args.path).read_text(encoding="utf-8"))
        with session_scope(args.city) as session:
            for item in items:
                item["starts_at"] = datetime.fromisoformat(item["starts_at"])
                item["ends_at"] = datetime.fromisoformat(item["ends_at"])
                session.add(models.LocalEvent(**item))
            session.commit()
        print(f"Imported {len(items)} events")


if __name__ == "__main__":
    main()
