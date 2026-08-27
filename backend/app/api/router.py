from fastapi import APIRouter

from app.api.health import router as health_router
from app.api.runs import router as runs_router
from app.api.agent import router as agent_router
from app.api.reports import router as reports_router
from app.api.settings import router as settings_router
from app.api.universe import router as universe_router
from app.api.daily_evidence import router as daily_evidence_router
from app.api.observation import router as observation_router
from app.api.remote_decisions import router as remote_decisions_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(runs_router)
api_router.include_router(agent_router)
api_router.include_router(reports_router)
api_router.include_router(settings_router)
api_router.include_router(universe_router)
api_router.include_router(daily_evidence_router)
api_router.include_router(observation_router)
api_router.include_router(remote_decisions_router)
