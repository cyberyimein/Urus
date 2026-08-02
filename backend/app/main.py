from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.core.config import Settings, get_settings
from app.core.database import Base, create_database
from app.core.errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    unexpected_error_handler,
    validation_error_handler,
)
from app.core.logging import configure_logging, request_logging_middleware
from app.models import RunModel, SnapshotModel, StepRunModel  # noqa: F401 - register ORM tables

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine, session_factory = create_database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        # This keeps a fresh local checkout runnable. Alembic remains the
        # explicit schema migration path for environments that manage schema state.
        Base.metadata.create_all(bind=engine)
        logger.info("Urus started environment=%s", settings.app_env)
        try:
            yield
        finally:
            engine.dispose()
            logger.info("Urus stopped")

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Urus stock analysis workflow framework API",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    app.middleware("http")(request_logging_middleware)

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(422, validation_error_handler)
    app.add_exception_handler(404, http_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
    app.include_router(api_router)
    return app


app = create_app()

