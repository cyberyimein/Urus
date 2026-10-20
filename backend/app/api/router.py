from fastapi import APIRouter

from app.api.health import router as health_router
from app.api.settings import router as settings_router
from app.api.universe import router as universe_router
from app.api.daily_evidence import router as daily_evidence_router
from app.api.observation import router as observation_router
from app.api.remote_decisions import router as remote_decisions_router
from app.api.focus_symbols import router as focus_symbols_router
from app.api.intraday import router as intraday_router
from app.api.intraday_pool import router as intraday_pool_router
from app.api.systematic_risk import router as systematic_risk_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(settings_router)
api_router.include_router(universe_router)
api_router.include_router(daily_evidence_router)
api_router.include_router(observation_router)
api_router.include_router(remote_decisions_router)
api_router.include_router(focus_symbols_router)
api_router.include_router(intraday_router)
api_router.include_router(intraday_pool_router)
api_router.include_router(systematic_risk_router)
