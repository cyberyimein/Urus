from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote
from zoneinfo import ZoneInfo
from typing import Any

from sqlalchemy import update as sql_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.time import utc_now
from app.decision_harness.contracts import content_sha256
from app.models.runtime_settings import RuntimeSettingsModel
from app.repositories.universe import InstrumentUniverseRepository
from app.schemas.intraday_pool import (
    IntradayCollectionPlanResponse,
    IntradayPoolMember,
    IntradayPoolSettingsResponse,
    IntradayPoolUpdate,
    IntradayPoolVersion,
)
from app.schemas.universe import InstrumentConfig
from app.services.trading_calendar import exchange_session_time_calendar


BENCHMARK_SYMBOLS = ("SPY", "QQQ", "IWM")
POOL_KEY = "intraday_pool"
POOL_SCHEMA_VERSION = "urus.intraday.pool_settings.v1"
COLLECTION_PLAN_SCHEMA_VERSION = "urus.intraday.collection_plan.v1"


class IntradayPoolUnavailable(ValueError):
    """The collector pool cannot be resolved without guessing or trimming it."""


def market_today(settings: Settings) -> date:
    return datetime.now(ZoneInfo(settings.market_timezone)).date()


def next_trading_session(today: date, calendar_name: str) -> date:
    calendar = exchange_session_time_calendar(calendar_name)
    import pandas as pd

    is_today_session = bool(calendar.is_session(pd.Timestamp(today)))
    first_date = today + timedelta(days=1) if is_today_session else today
    final_date = first_date + timedelta(days=31)
    sessions = calendar.sessions_in_range(
        pd.Timestamp(first_date), pd.Timestamp(final_date)
    )
    if len(sessions) == 0:
        raise IntradayPoolUnavailable(
            f"無法在 {calendar_name} 日曆中找到 {today.isoformat()} 之後的交易日。"
        )
    return date.fromisoformat(str(sessions[0])[:10])


def _catalog(
    session: Session, settings: Settings
) -> tuple[list[InstrumentConfig], dict[str, InstrumentConfig]]:
    repository = InstrumentUniverseRepository(session)
    version = repository.active() or repository.ensure_default(settings)
    items = repository.response(version).items
    return items, {item.symbol: item for item in items}


def _available_members(items: list[InstrumentConfig]) -> list[IntradayPoolMember]:
    available = []
    for item in items:
        if not item.enabled or not item.collection.quote:
            continue
        themes = list(item.themes or ([item.theme] if item.theme else []))
        group = _display_group(item.symbol, themes)
        available.append(
            IntradayPoolMember(
                symbol=item.symbol,
                display_name=item.display_name,
                asset_type=item.asset_type,
                themes=themes,
                display_group=group,
                fixed=item.symbol in BENCHMARK_SYMBOLS,
            )
        )
    return available


def _display_group(symbol: str, themes: list[str]) -> str:
    if symbol in BENCHMARK_SYMBOLS:
        return "大盘基准"
    return themes[0].strip() if themes and themes[0].strip() else "未分组"


def _normalized_symbols(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for raw in values:
        symbol = str(raw or "").strip().upper()
        if symbol.startswith("US."):
            symbol = symbol[3:]
        if symbol and symbol not in result:
            result.append(symbol)
    return result


def _latest_legacy_capture_symbols(
    path_value: str,
    market_timezone: str = "America/New_York",
) -> tuple[str, list[str], str]:
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise IntradayPoolUnavailable("没有可读取的历史日内股票 capture。")

    database_uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
    metadata_query = """
        SELECT scheduled.task_name, scheduled.scheduled_at,
               scheduled.capture_id, scheduled.kind AS scheduled_kind,
               raw.schema_version AS raw_schema_version, raw.kind AS raw_kind,
               CASE WHEN json_valid(raw.payload_json)
                    THEN json_extract(raw.payload_json, '$.capture_id') END AS contract_capture_id,
               CASE WHEN json_valid(raw.payload_json)
                    THEN json_extract(raw.payload_json, '$.schema_version') END AS contract_schema_version,
               CASE WHEN json_valid(raw.payload_json)
                    THEN json_extract(raw.payload_json, '$.kind') END AS contract_kind,
               CASE WHEN json_valid(raw.payload_json)
                    THEN json_extract(raw.payload_json, '$.task_name') END AS contract_task_name,
               CASE WHEN json_valid(raw.payload_json)
                    THEN json_extract(raw.payload_json, '$.scheduled_at') END AS contract_scheduled_at,
               CASE WHEN json_valid(raw.payload_json)
                    THEN json_extract(raw.payload_json, '$.provider') END AS contract_provider,
               CASE WHEN json_valid(raw.payload_json)
                    THEN json_extract(raw.payload_json, '$.trading_date') END AS contract_trading_date,
               CASE WHEN json_valid(raw.payload_json)
                    THEN json_extract(raw.payload_json, '$.requested_symbols') END AS requested_symbols_json
        FROM scheduled_captures AS scheduled
        LEFT JOIN raw_captures AS raw ON raw.capture_id = scheduled.capture_id
        WHERE scheduled.kind = ?
        ORDER BY julianday(scheduled.scheduled_at) DESC
    """
    try:
        connection = sqlite3.connect(database_uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                metadata_query, ("capture_intraday_equities",)
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise IntradayPoolUnavailable(f"无法只读日内股票 capture 元数据：{exc}") from exc

    if not rows:
        raise IntradayPoolUnavailable("没有历史日内股票 capture，当前池尚未配置。")

    last_problem = "没有身份有效的历史日内股票 capture。"
    for row in rows:
        capture_id = str(row["capture_id"] or "")
        scheduled_at = str(row["scheduled_at"] or "")
        expected_identity = (
            row["contract_capture_id"] == capture_id
            and row["contract_schema_version"]
            == row["raw_schema_version"]
            == "urus.moomoo.capture.v2"
            and row["contract_kind"]
            == row["raw_kind"]
            == row["scheduled_kind"]
            == "capture_intraday_equities"
            and row["contract_task_name"] == row["task_name"]
            and row["contract_scheduled_at"] == scheduled_at
            and row["contract_provider"] == "moomoo_openapi"
        )
        if not expected_identity:
            last_problem = "最近的股票 capture envelope 身份校验失败。"
            continue

        try:
            requested_symbols = json.loads(str(row["requested_symbols_json"] or "null"))
        except (TypeError, json.JSONDecodeError):
            last_problem = "最近的股票 capture requested_symbols 元数据无效。"
            continue
        symbols = _normalized_symbols(requested_symbols)
        if not isinstance(requested_symbols, list) or len(symbols) != len(requested_symbols) or not symbols:
            last_problem = "最近的股票 capture 缺少有效 requested_symbols。"
            continue
        missing_benchmarks = sorted(set(BENCHMARK_SYMBOLS) - set(symbols))
        if missing_benchmarks:
            last_problem = "最近的股票 capture 缺少固定基准：" + ", ".join(missing_benchmarks)
            continue
        try:
            captured_trading_date = date.fromisoformat(
                str(row["contract_trading_date"] or "")
            )
            scheduled_datetime = datetime.fromisoformat(scheduled_at)
            if scheduled_datetime.tzinfo is None or scheduled_datetime.utcoffset() is None:
                raise ValueError("scheduled_at must be timezone-aware")
            scheduled_market_date = scheduled_datetime.astimezone(
                ZoneInfo(market_timezone)
            ).date()
        except (TypeError, ValueError):
            last_problem = "最近的股票 capture 交易日无效。"
            continue
        if captured_trading_date != scheduled_market_date:
            last_problem = "最近的股票 capture 交易日与槽日期不一致。"
            continue
        return capture_id, symbols, captured_trading_date.isoformat()

    raise IntradayPoolUnavailable(last_problem)


def _legacy_version(
    session: Session,
    settings: Settings,
    catalog: dict[str, InstrumentConfig],
) -> dict[str, Any]:
    capture_id, symbols, captured_trading_date = _latest_legacy_capture_symbols(
        settings.sidecar_capture_database_path,
        settings.market_timezone,
    )
    missing = [symbol for symbol in symbols if symbol not in catalog]
    if missing:
        raise IntradayPoolUnavailable(
            "最新日内股票池无法在全局 Universe 中完整解析：" + ", ".join(missing)
        )
    group_by_symbol: dict[str, str] = {}
    for symbol in symbols:
        item = catalog[symbol]
        themes = list(item.themes or ([item.theme] if item.theme else []))
        group_by_symbol[symbol] = _display_group(symbol, themes)
    return {
        "revision": 0,
        "source": "legacy_capture",
        "effective_date": captured_trading_date,
        "selected_symbols": [symbol for symbol in symbols if symbol not in BENCHMARK_SYMBOLS],
        "member_symbols": symbols,
        "display_group_by_symbol": group_by_symbol,
        "source_capture_id": capture_id,
    }


def _state_from_payload(payload: object) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or POOL_KEY not in payload:
        return None
    state = payload[POOL_KEY]
    if not isinstance(state, dict) or state.get("schema_version") != POOL_SCHEMA_VERSION:
        raise IntradayPoolUnavailable("已保存的日内标的池设置版本无效。")
    revision = state.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise IntradayPoolUnavailable("已保存的日内标的池 revision 无效。")
    return state


def _stored_version(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise IntradayPoolUnavailable("已保存的日内标的池版本无效。")
    source = value.get("source")
    if source not in {"legacy_capture", "runtime"}:
        raise IntradayPoolUnavailable("已保存的日内标的池来源无效。")
    members = _normalized_symbols(value.get("member_symbols"))
    if not members or not set(BENCHMARK_SYMBOLS).issubset(members):
        raise IntradayPoolUnavailable("已保存的日内标的池缺少固定基准。")
    selected = _normalized_symbols(value.get("selected_symbols"))
    group_by_symbol = value.get("display_group_by_symbol")
    if not isinstance(group_by_symbol, dict) or set(group_by_symbol) != set(members):
        raise IntradayPoolUnavailable("已保存的日内业务分组与标的成员不一致。")
    effective_date = value.get("effective_date")
    if effective_date is not None:
        try:
            effective_date = date.fromisoformat(str(effective_date)).isoformat()
        except ValueError as exc:
            raise IntradayPoolUnavailable("已保存的日内池生效日无效。") from exc
    return {
        "revision": int(value.get("revision", 0)),
        "source": source,
        "effective_date": effective_date,
        "selected_symbols": selected,
        "member_symbols": members,
        "display_group_by_symbol": {
            symbol: str(group_by_symbol[symbol]) for symbol in members
        },
        "source_capture_id": str(value.get("source_capture_id") or "") or None,
    }


def _serialize_version(value: dict[str, Any] | None) -> IntradayPoolVersion | None:
    if value is None:
        return None
    return IntradayPoolVersion(**value)


def _ensure_fixed_benchmarks(catalog: dict[str, InstrumentConfig]) -> None:
    unavailable = [
        symbol
        for symbol in BENCHMARK_SYMBOLS
        if symbol not in catalog
        or not catalog[symbol].enabled
        or not catalog[symbol].collection.quote
    ]
    if unavailable:
        raise AppError(
            "全局 Universe 必须保留启用且可报价的固定基准："
            + ", ".join(unavailable),
            code="intraday_pool_baseline_unavailable",
            status_code=409,
            details={"symbols": unavailable},
        )


class IntradayPoolRepository:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings

    def _model(self) -> RuntimeSettingsModel | None:
        return self.session.get(RuntimeSettingsModel, 1)

    def _catalog(self) -> tuple[list[InstrumentConfig], dict[str, InstrumentConfig]]:
        return _catalog(self.session, self.settings)

    def settings_response(self, *, today: date | None = None) -> IntradayPoolSettingsResponse:
        items, catalog_by_symbol = self._catalog()
        _ensure_fixed_benchmarks(catalog_by_symbol)
        model = self._model()
        state = _state_from_payload(model.payload if model else None)
        warning = None
        if state is None:
            try:
                active = _legacy_version(
                    self.session, self.settings, catalog_by_symbol
                )
            except IntradayPoolUnavailable as exc:
                active = None
                warning = str(exc)
            pending = None
            revision = 0
        else:
            active = _stored_version(state.get("active"))
            pending = _stored_version(state.get("pending"))
            revision = int(state["revision"])
            effective_today = today or market_today(self.settings)
            if (
                pending is not None
                and pending["effective_date"] is not None
                and date.fromisoformat(pending["effective_date"]) <= effective_today
            ):
                active, pending = pending, None
        status = "configured" if active is not None else "pending_only" if pending else "unconfigured"
        return IntradayPoolSettingsResponse(
            status=status,
            revision=revision,
            benchmark_symbols=list(BENCHMARK_SYMBOLS),
            active=_serialize_version(active),
            pending=_serialize_version(pending),
            available_symbols=_available_members(items),
            warning=warning,
        )

    def update(
        self, update: IntradayPoolUpdate, *, today: date | None = None
    ) -> IntradayPoolSettingsResponse:
        model = self._model()
        current_payload = (
            dict(model.payload) if model and isinstance(model.payload, dict) else {}
        )
        state = _state_from_payload(current_payload)
        current_revision = int(state["revision"]) if state is not None else 0
        if update.base_revision != current_revision:
            raise AppError(
                "日内标的池已被其他页面更新，请刷新后再保存。",
                code="intraday_pool_revision_conflict",
                status_code=409,
                details={"current_revision": current_revision},
            )

        items, catalog = self._catalog()
        _ensure_fixed_benchmarks(catalog)
        selected_symbols = [
            symbol
            for symbol in update.selected_symbols
            if symbol not in BENCHMARK_SYMBOLS
        ]
        unavailable = [
            symbol
            for symbol in selected_symbols
            if symbol not in catalog
            or not catalog[symbol].enabled
            or not catalog[symbol].collection.quote
        ]
        if unavailable:
            raise AppError(
                "只能选择全局 Universe 中已启用且可报价的标的："
                + ", ".join(unavailable),
                code="intraday_pool_member_unavailable",
                status_code=422,
                details={"symbols": unavailable},
            )

        current_date = today or market_today(self.settings)
        active = _stored_version(state.get("active")) if state is not None else None
        pending = _stored_version(state.get("pending")) if state is not None else None
        if state is None:
            try:
                active = _legacy_version(self.session, self.settings, catalog)
            except IntradayPoolUnavailable:
                active = None
        if (
            pending is not None
            and pending["effective_date"] is not None
            and date.fromisoformat(pending["effective_date"]) <= current_date
        ):
            active, pending = pending, None

        member_symbols = list(selected_symbols)
        member_symbols.extend(
            symbol for symbol in BENCHMARK_SYMBOLS if symbol not in member_symbols
        )
        display_groups = {
            symbol: _display_group(
                symbol,
                list(catalog[symbol].themes or ([catalog[symbol].theme] if catalog[symbol].theme else [])),
            )
            for symbol in member_symbols
        }
        new_revision = current_revision + 1
        pending = {
            "revision": new_revision,
            "source": "runtime",
            "effective_date": next_trading_session(
                current_date, self.settings.market_calendar
            ).isoformat(),
            "selected_symbols": selected_symbols,
            "member_symbols": member_symbols,
            "display_group_by_symbol": display_groups,
            "source_capture_id": None,
        }
        new_state = {
            "schema_version": POOL_SCHEMA_VERSION,
            "revision": new_revision,
            "active": active,
            "pending": pending,
        }
        current_payload[POOL_KEY] = new_state
        if model is None:
            model = RuntimeSettingsModel(
                id=1,
                payload=current_payload,
                revision=1,
                updated_at=utc_now(),
            )
            self.session.add(model)
            try:
                self.session.commit()
            except IntegrityError as exc:
                self.session.rollback()
                raise AppError(
                    "其他设置刚刚完成保存，请刷新后再试。",
                    code="intraday_pool_revision_conflict",
                    status_code=409,
                ) from exc
        else:
            expected_runtime_revision = model.revision
            result = self.session.execute(
                sql_update(RuntimeSettingsModel)
                .where(
                    RuntimeSettingsModel.id == 1,
                    RuntimeSettingsModel.revision == expected_runtime_revision,
                )
                .values(
                    payload=current_payload,
                    revision=expected_runtime_revision + 1,
                    updated_at=utc_now(),
                )
            )
            if result.rowcount != 1:
                self.session.rollback()
                raise AppError(
                    "其他设置刚刚完成保存，请刷新后再试。",
                    code="intraday_pool_revision_conflict",
                    status_code=409,
                )
            self.session.commit()
        self.session.expire_all()
        model = self._model()
        if model is None:
            raise RuntimeError("runtime settings row missing after intraday pool save")
        return self.settings_response(today=current_date)

    def collection_plan(self, trading_date: date) -> IntradayCollectionPlanResponse:
        calendar = exchange_session_time_calendar(self.settings.market_calendar)
        import pandas as pd

        if not calendar.is_session(pd.Timestamp(trading_date)):
            raise AppError(
                f"{trading_date.isoformat()} 不是 {self.settings.market_calendar} 交易日。",
                code="intraday_collection_plan_invalid",
                status_code=409,
            )

        model = self._model()
        state = _state_from_payload(model.payload if model else None)
        _items, catalog = self._catalog()
        _ensure_fixed_benchmarks(catalog)
        active: dict[str, Any] | None
        pending: dict[str, Any] | None
        if state is None:
            try:
                active = _legacy_version(self.session, self.settings, catalog)
            except IntradayPoolUnavailable as exc:
                raise AppError(
                    str(exc), code="intraday_pool_unavailable", status_code=409
                ) from exc
            pending = None
        else:
            active = _stored_version(state.get("active"))
            pending = _stored_version(state.get("pending"))

        chosen = active
        if (
            pending is not None
            and pending["effective_date"] is not None
            and trading_date >= date.fromisoformat(pending["effective_date"])
        ):
            chosen = pending
        if chosen is None:
            raise AppError(
                "当前日内标的池尚无当日生效配置。",
                code="intraday_pool_unavailable",
                status_code=409,
            )
        effective_date = chosen["effective_date"]
        if effective_date is not None and trading_date < date.fromisoformat(effective_date):
            raise AppError(
                "该交易日没有可用的已冻结日内标的池版本。",
                code="intraday_pool_unavailable",
                status_code=409,
            )

        members = chosen["member_symbols"]
        missing_benchmarks = [symbol for symbol in BENCHMARK_SYMBOLS if symbol not in members]
        if missing_benchmarks:
            raise AppError(
                "冻结的日内标的池缺少固定基准：" + ", ".join(missing_benchmarks),
                code="intraday_pool_unavailable",
                status_code=409,
            )
        definition = {
            "schema_version": COLLECTION_PLAN_SCHEMA_VERSION,
            "status": "configured",
            "trading_date": trading_date.isoformat(),
            "pool_revision": int(chosen["revision"]),
            "selected_symbols": list(chosen["selected_symbols"]),
            "benchmark_symbols": list(BENCHMARK_SYMBOLS),
            "member_symbols": list(members),
            "quote_symbols": list(members),
            "minute_symbols": list(members),
            "display_group_by_symbol": dict(chosen["display_group_by_symbol"]),
        }
        return IntradayCollectionPlanResponse(
            plan_id=content_sha256(definition), **definition
        )
