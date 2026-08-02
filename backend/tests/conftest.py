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
        enabled_symbols="QQQ,INTC",
        moomoo_enabled=False,
        fred_enabled=False,
    )
    return create_app(settings)


@pytest.fixture
def client(app) -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client
