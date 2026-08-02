from fastapi import APIRouter

from app.api.health import router as health_router
from app.api.runs import router as runs_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(runs_router)

