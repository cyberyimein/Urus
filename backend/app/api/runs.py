import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_settings
from app.core.config import Settings
from app.schemas.read_model import (
    FrontendReadModel,
    ManualAnalysisCreateRequest,
    ManualAnalysisCreateResponse,
    RunCreateRequest,
    RunCreateResponse,
    RunDetailResponse,
    RunListItem,
    SnapshotResponse,
    WatchlistResponse,
)
from app.services import RunService

router = APIRouter()
logger = logging.getLogger(__name__)


def _execute_manual_analysis(session_factory, settings: Settings, run_id: str, symbols) -> None:
    with session_factory() as session:
        service = RunService(session, settings)
        try:
            service.create_run(
                RunCreateRequest(run_type="manual_analysis", symbols=symbols),
                queued_run_id=run_id,
            )
        except Exception:
            logger.exception("manual analysis failed run_id=%s", run_id)
            run = service.repository.get_run(run_id)
            if run is not None and run.status in {"pending", "running"}:
                from app.core.time import utc_now

                service.repository.update_run(
                    run,
                    status="failed",
                    completed_at=utc_now(),
                    error_message="手动分析后台任务异常终止。",
                )


def _retry_manual_ai(session_factory, settings: Settings, run_id: str) -> None:
    with session_factory() as session:
        try:
            RunService(session, settings).rerun_manual_ai(run_id)
        except Exception:
            logger.exception("manual AI retry failed run_id=%s", run_id)


@router.post("/runs", response_model=RunCreateResponse, status_code=201)
def create_run(
    request: RunCreateRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RunCreateResponse:
    return RunService(db, settings).create_run(request)


@router.post(
    "/analysis/runs",
    response_model=ManualAnalysisCreateResponse,
    status_code=202,
)
def create_manual_analysis(
    payload: ManualAnalysisCreateRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ManualAnalysisCreateResponse:
    service = RunService(db, settings)
    queued = service.initialize_run(
        RunCreateRequest(run_type="manual_analysis", symbols=payload.symbols)
    )
    run = service.repository.get_run(queued.run_id)
    session_context = service._session_context(run.cutoff_time) if run is not None else "unknown"
    background_tasks.add_task(
        _execute_manual_analysis,
        request.app.state.session_factory,
        settings,
        queued.run_id,
        payload.symbols,
    )
    return ManualAnalysisCreateResponse(
        run_id=queued.run_id,
        status=queued.status,
        session_context=session_context,
    )


@router.post("/analysis/runs/{run_id}/retry-ai", status_code=202)
def retry_manual_analysis_ai(
    run_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    service = RunService(db, settings)
    run = service.get_run(run_id)
    if run.run_type.value != "manual_analysis" or not run.snapshot_id:
        from app.core.errors import AppError

        raise AppError(
            "只有已冻结的手动分析可以重试 AI",
            code="manual_analysis_snapshot_required",
            status_code=409,
        )
    if not settings.urus_agent_enabled or not settings.openrouter_api_key:
        from app.core.errors import AppError

        raise AppError(
            "Urus Agent 未启用或未配置模型密钥",
            code="urus_agent_provider_not_configured",
            status_code=503,
        )
    background_tasks.add_task(
        _retry_manual_ai,
        request.app.state.session_factory,
        settings,
        run_id,
    )
    return {"run_id": run_id, "status": "retry_queued"}


@router.get("/runs", response_model=list[RunListItem])
def list_runs(
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[RunListItem]:
    return RunService(db, settings).list_runs(limit)


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
def get_run(
    run_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RunDetailResponse:
    return RunService(db, settings).get_run(run_id)


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotResponse)
def get_snapshot(
    snapshot_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SnapshotResponse:
    return RunService(db, settings).get_snapshot(snapshot_id)


@router.get("/snapshots/{snapshot_id}/frontend", response_model=FrontendReadModel)
def get_frontend_read_model(
    snapshot_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FrontendReadModel:
    return RunService(db, settings).get_frontend_read_model(snapshot_id)


@router.get("/watchlist", response_model=WatchlistResponse)
def get_watchlist(settings: Settings = Depends(get_settings)) -> WatchlistResponse:
    return WatchlistResponse(
        symbols=list(
            dict.fromkeys(
                settings.options_watchlist_symbol_list
                + settings.options_watchlist_excluded_symbol_list
            )
        ),
        option_symbols=settings.options_collection_symbol_list,
        option_excluded_symbols=settings.options_watchlist_excluded_symbol_list,
        is_development_allowlist=False,
        is_mock=False,
    )
