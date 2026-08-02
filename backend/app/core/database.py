from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def create_database(database_url: str) -> tuple[Engine, sessionmaker[Session]]:
    connect_args: dict[str, object] = {}
    engine_kwargs: dict[str, object] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if database_url.endswith(":memory:"):
            engine_kwargs["poolclass"] = StaticPool

    engine = create_engine(database_url, connect_args=connect_args, **engine_kwargs)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine, session_factory


def session_scope(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()

