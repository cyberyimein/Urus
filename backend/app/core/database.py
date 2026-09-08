from __future__ import annotations

from collections.abc import Generator
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


SQLITE_BUSY_TIMEOUT_MS = 30_000


class Base(DeclarativeBase):
    pass


def _configure_sqlite_connection(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.fetchone()
    finally:
        cursor.close()


def create_database(database_url: str) -> tuple[Engine, sessionmaker[Session]]:
    connect_args: dict[str, object] = {}
    engine_kwargs: dict[str, object] = {"pool_pre_ping": True}
    url = make_url(database_url)
    is_sqlite = url.drivername.startswith("sqlite")
    is_memory_sqlite = is_sqlite and url.database in {None, ":memory:"}
    if is_sqlite:
        connect_args["check_same_thread"] = False
        if is_memory_sqlite:
            engine_kwargs["poolclass"] = StaticPool
        else:
            connect_args["timeout"] = SQLITE_BUSY_TIMEOUT_MS / 1000

    engine = create_engine(database_url, connect_args=connect_args, **engine_kwargs)
    if is_sqlite and not is_memory_sqlite:
        event.listen(engine, "connect", _configure_sqlite_connection)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine, session_factory


def session_scope(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
