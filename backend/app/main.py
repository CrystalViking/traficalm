"""FastAPI application entry point: ``uvicorn app.main:app``."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router
from .config import get_settings
from .db import create_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_all()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="TrafiCalm API",
        description="Crowd-avoiding public transport planner for Gdańsk, Poznań, Wrocław and Warsaw (GTFS, OpenStreetMap).",
        version="0.1.0",
        lifespan=lifespan,
    )
    origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "DELETE"], allow_headers=["*"])
    app.include_router(router)
    return app


app = create_app()
