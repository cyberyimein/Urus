from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def app(tmp_path: Path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        cors_origins="http://testserver",
        # The developer .env points at a deployed observation Universe. Tests
        # must opt into that behavior explicitly instead of
        # making the local-sync contract depend on the host environment.
        observation_universe_source_url="",
        enabled_symbols="QQQ,INTC",
        # Keep the fixture focused on the disabled market-data adapters. The
        # production default is the full configured universe.
        instrument_validation_symbols="INTC,SMH",
        moomoo_enabled=False,
        fred_enabled=False,
        yahoo_enabled=False,
        anomalo_workflow_enabled=False,
    )
    return create_app(settings)


@pytest.fixture
def client(app) -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client
