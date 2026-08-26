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
        # The developer .env intentionally points at the deployed Phase C
        # Universe. Tests must opt into that behavior explicitly instead of
        # making the local-sync contract depend on the host environment.
        observation_universe_source_url="",
        enabled_symbols="QQQ,INTC",
        # Keep workflow API tests focused on the disabled/mock adapter. The
        # production default is the full Stage 3A universe.
        instrument_validation_symbols="INTC,SMH",
        moomoo_enabled=False,
        fred_enabled=False,
        anomalo_enabled=False,
        expected_events_enabled=False,
        # Legacy event-workflow API assertions remain isolated from the CTA
        # branch default; CTA behavior has dedicated workflow/API coverage.
        workflow_research_variant="events",
        # Make the fixture independent of a developer's local .env.  A
        # paired pre_close run now reaches Step 4, so an inherited
        # URUS_AGENT_ENABLED=true would invoke the real preflight instead of
        # the mock adapter this API suite is designed to exercise.
        urus_agent_enabled=False,
    )
    return create_app(settings)


@pytest.fixture
def client(app) -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client
