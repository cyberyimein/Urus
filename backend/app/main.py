from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

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
from app.core.time import utc_now
from app.frontend import SPAStaticFiles
from app.models import (  # noqa: F401 - register ORM tables
    AIDecisionRunModel,
    AIDecisionSessionModel,
    AIModelTurnModel,
    AITraceNodeModel,
    AIToolCallModel,
    EventAgentRunModel,
    EventDefinitionModel,
    EventMarketReactionModel,
    EventModel,
    EventResultModel,
    EventScheduleInitializationModel,
    EventSourceModel,
    InstrumentAnalysisBatchModel,
    InstrumentDailyBarModel,
    InstrumentSnapshotModel,
    OptionAnalysisBatchModel,
    OptionContractSnapshotModel,
    OptionExpirationAnalysisModel,
    OptionGammaFlipModel,
    OptionGammaProfilePointModel,
    OptionSymbolSnapshotModel,
    RunModel,
    SnapshotModel,
    StepRunModel,
    RuntimeSettingsModel,
    InstrumentUniverseItemModel,
    InstrumentUniverseVersionModel,
    StrategyResearchDatasetModel,
    ReportDisplayProjectionModel,
    DailyBarModel,
    DailyIndicatorSnapshotModel,
    DailyDecisionDatasetModel,
    DecisionChartProjectionModel,
    HistoryCollectionStateModel,
    HistoryQuotaSnapshotModel,
    StrategyDecisionModel,
    DeterministicSynthesisModel,
    ObservationGroupVersionModel,
    GroupDailySnapshotModel,
    ObservationRunModel,
    ObservationUniverseRevisionModel,
    DecisionWorkflowBindingModel,
    RemoteDecisionRunModel,
    RemoteDecisionEventModel,
    RemoteDecisionArtifactModel,
)
from app.repositories.runs import RunRepository
from app.repositories.runtime_settings import RuntimeSettingsRepository, apply_payload
from app.repositories.universe import InstrumentUniverseRepository
from app.integrations.anomalo_workflow import FakeWorkflowAdapter, HttpAnomaloWorkflowAdapter
from app.services.remote_decision_supervisor import RemoteDecisionSupervisor

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
        with session_factory() as session:
            persisted_settings = RuntimeSettingsRepository(session).get()
            if persisted_settings is not None:
                apply_payload(settings, persisted_settings.payload)
            InstrumentUniverseRepository(session).ensure_default(settings)
            recovered = RunRepository(session).recover_interrupted_runs(completed_at=utc_now())
        if recovered:
            logger.warning("Recovered %s interrupted workflow run(s)", recovered)
        remote_supervisor: RemoteDecisionSupervisor = app.state.remote_decision_supervisor
        await remote_supervisor.start()
        logger.info("Urus started environment=%s", settings.app_env)
        try:
            yield
        finally:
            await remote_supervisor.shutdown()
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
    if settings.anomalo_workflow_fake_adapter or settings.app_env == "test":
        workflow_adapter = FakeWorkflowAdapter()
    elif settings.anomalo_workflow_enabled and settings.anomalo_base_url and settings.anomalo_workflow_token:
        workflow_adapter = HttpAnomaloWorkflowAdapter(
            settings.anomalo_base_url,
            settings.anomalo_workflow_token,
            connect_timeout_seconds=settings.anomalo_workflow_connect_timeout_seconds,
            read_timeout_seconds=settings.anomalo_workflow_read_timeout_seconds,
        )
    else:
        # Keep app startup deterministic when the production Secret Store has
        # not yet injected a runtime token. Preflight will report the blocker;
        # no network client is created in this state.
        workflow_adapter = FakeWorkflowAdapter()
    app.state.remote_decision_supervisor = RemoteDecisionSupervisor(
        session_factory,
        settings,
        workflow_adapter,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    app.middleware("http")(request_logging_middleware)

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(422, validation_error_handler)
    app.add_exception_handler(404, http_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
    app.include_router(api_router)
    frontend_dist = Path(__file__).resolve().parents[1] / "frontend_dist"
    if frontend_dist.joinpath("index.html").is_file():
        # Mount last so API routes retain precedence while Vue history routes
        # are handled by the bundled production frontend.
        app.mount("/", SPAStaticFiles(directory=frontend_dist, html=True), name="frontend")
    return app


app = create_app()
