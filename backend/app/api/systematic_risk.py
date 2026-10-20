from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_settings
from app.api.intraday import _load_latest_capture, _primary_database_file_exists
from app.core.config import Settings
from app.intraday.equities import build_intraday_projection
from app.systematic_risk.service import OVERVIEW_SCHEMA_VERSION, build_overview


router = APIRouter(prefix="/systematic-risk", tags=["systematic-risk"])


@router.get("/overview")
def systematic_risk_overview(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    now = datetime.now(UTC)
    if not settings.systematic_risk_enabled:
        return {
            "schema_version": OVERVIEW_SCHEMA_VERSION,
            "status": "disabled",
            "evaluated_at": now.isoformat(),
            "symbols": {},
        }

    if not _primary_database_file_exists(settings):
        return _overview_failure(
            now,
            status="unavailable",
            code="primary_database_missing",
            message="本地主库文件不存在；系统性风险基线不可读取。",
        )

    try:
        import exchange_calendars as xcals

        xcals.get_calendar(settings.market_calendar)
    except Exception as exc:
        return _overview_failure(
            now,
            status="unavailable",
            code="market_calendar_invalid",
            message=str(exc) or "市场日历配置无效，无法读取系统性风险基线。",
        )

    contract, source_error = _load_latest_capture(settings.sidecar_capture_database_path)
    source_reason: dict[str, str] | None = None
    if contract is None:
        projection: dict[str, Any] = {"symbols": {}}
        if source_error is not None:
            source_reason = {
                "code": "capture_source_invalid",
                "message": str(source_error.get("message") or "本地行情来源无效。"),
            }
        else:
            source_reason = {
                "code": "capture_unavailable",
                "message": "本地 sidecar 中没有可用的盘中股票采集槽。",
            }
    else:
        try:
            projection = build_intraday_projection(
                contract,
                now=now,
                market_timezone=settings.market_timezone,
                market_calendar=settings.market_calendar,
            )
        except (TypeError, ValueError, KeyError) as exc:
            projection = {"symbols": {}}
            source_reason = {
                "code": "capture_projection_invalid",
                "message": str(exc) or "本地盘中行情投影无效。",
            }

    try:
        result = build_overview(
            db,
            projection,
            now=now,
            market_calendar=settings.market_calendar,
        )
    except SQLAlchemyError:
        return _overview_failure(
            now,
            status="error",
            code="primary_database_read_failed",
            message="本地主库读取失败；未创建或修改数据库对象。",
        )
    except (TypeError, ValueError, KeyError) as exc:
        return _overview_failure(
            now,
            status="unavailable",
            code="market_calendar_invalid",
            message=str(exc) or "市场日历配置无效，无法读取系统性风险基线。",
        )
    except Exception:
        return _overview_failure(
            now,
            status="error",
            code="systematic_risk_read_failed",
            message="系统性风险只读计算失败。",
        )

    if source_reason is not None:
        result["reasons"] = [source_reason]
        result["warnings"] = [source_reason["message"]]
    return result


def _overview_failure(
    now: datetime,
    *,
    status: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "schema_version": OVERVIEW_SCHEMA_VERSION,
        "status": status,
        "evaluated_at": now.isoformat(),
        "symbols": {},
        "reasons": [{"code": code, "message": message}],
        "warnings": [message],
    }
