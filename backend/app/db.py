"""Database engine and session helpers (PostgreSQL in production, any SQLAlchemy URL works)."""

from collections.abc import Iterator
from pathlib import Path
from threading import Lock

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings
from .cities import City


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_city_engines: dict[City, Engine] = {}
_city_factories: dict[City, sessionmaker[Session]] = {}
_city_lock = Lock()


def configure(url: str | None = None, city: City = City.GDANSK) -> Engine:
    """(Re)configure the global engine. Used by the app at startup and by tests."""
    global _engine, _session_factory
    settings = get_settings()
    urls = {
        City.GDANSK: settings.sqlalchemy_url,
        City.POZNAN: settings.poznan_database_url,
        City.WROCLAW: settings.wroclaw_database_url,
        City.WARSAW: settings.warsaw_database_url,
    }
    url = url or urls[city]
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
        database = make_url(url).database
        if database and database != ":memory:" and not database.startswith("file:"):
            Path(database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    if city == City.GDANSK:
        _engine, _session_factory = engine, factory
    else:
        _city_engines[city], _city_factories[city] = engine, factory
    return engine


def get_engine(city: City = City.GDANSK) -> Engine:
    if city != City.GDANSK:
        with _city_lock:
            if city not in _city_engines:
                configure(city=city)
                from . import models  # noqa: F401
                Base.metadata.create_all(_city_engines[city])
        return _city_engines[city]
    if _engine is None:
        configure()
    assert _engine is not None
    return _engine


def session_scope(city: City = City.GDANSK) -> Session:
    if city != City.GDANSK:
        get_engine(city)
        session = _city_factories[city]()
        session.info["city"] = city
        return session
    if _session_factory is None:
        configure()
    assert _session_factory is not None
    session = _session_factory()
    session.info["city"] = city
    return session


def get_session(city: City = City.GDANSK) -> Iterator[Session]:
    """FastAPI dependency."""
    session = session_scope(city)
    try:
        yield session
    finally:
        session.close()


def create_all(city: City = City.GDANSK) -> None:
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(get_engine(city))
