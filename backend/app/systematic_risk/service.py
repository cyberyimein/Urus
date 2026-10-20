"""Read-only assembly of frozen daily baselines and provisional quote scenarios."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
import math
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.orm import Session
from sqlalchemy.sql import cast

from app.decision_harness.contracts import content_sha256, hash_bars
from app.models.daily_evidence import DailyDecisionDatasetModel, DecisionChartProjectionModel
from app.systematic_risk.models import MODEL_VERSION, evaluate_trend_scenario


OVERVIEW_SCHEMA_VERSION = "urus.systematic_risk.overview.v1"
SYMBOLS = ("SPY", "QQQ", "IWM")
TREND_MODELS = ((10, 20), (20, 60), (50, 200))
TARGET_VOLATILITY = 0.10
EXPOSURE_CAP = 1.0
VOLATILITY_WINDOW = 20
MAX_QUOTE_AGE = timedelta(seconds=600)
_UNAVAILABLE_STATUS = "unavailable"


def build_overview(
    session: Session,
    projection: Mapping[str, Any],
    *,
    now: datetime,
    market_calendar: str = "XNYS",
) -> dict[str, Any]:
    """Build a bounded, read-only risk overview from existing frozen evidence.

    The caller supplies an already-built P3 projection and an explicit UTC
    evaluation time.  This function neither fetches market data nor freezes a
    dataset, and it never persists baselines or quote scenarios.
    """

    now_utc = _require_utc(now)
    calendar = _calendar(market_calendar)
    raw_symbols = projection.get("symbols") if isinstance(projection, Mapping) else None
    projection_symbols = raw_symbols if isinstance(raw_symbols, Mapping) else {}

    symbol_results: dict[str, dict[str, Any]] = {}
    for symbol in SYMBOLS:
        item = projection_symbols.get(symbol)
        quote, quote_as_of, quote_error = _validated_quote(item, now=now_utc)
        if quote_error is not None:
            symbol_results[symbol] = _unavailable_symbol(
                symbol,
                quote_error[0],
                quote_error[1],
                quote_as_of=quote_as_of,
            )
            continue

        assert quote is not None and quote_as_of is not None
        chart, dataset = _latest_known_frozen_chart(
            session,
            symbol,
            quote_as_of=quote_as_of,
        )
        if chart is None or dataset is None:
            symbol_results[symbol] = _unavailable_symbol(
                symbol,
                "frozen_chart_unavailable",
                "报价之前没有包含该标的的有效冻结日线图表。",
                quote_as_of=quote_as_of,
            )
            continue

        try:
            baseline_data = _validated_baseline(
                dataset,
                chart,
                symbol=symbol,
                quote=quote,
                quote_as_of=quote_as_of,
                calendar=calendar,
            )
        except _EvidenceUnavailable as exc:
            symbol_results[symbol] = _unavailable_symbol(
                symbol,
                exc.reason_code,
                exc.message,
                quote_as_of=quote_as_of,
                dataset_id=str(dataset.id),
                chart_hash=str(chart.content_sha256),
            )
            continue

        closes = [float(bar["close"]) for bar in baseline_data["bars"]]
        assumed_close = float(quote["last_price"])
        model_results: list[dict[str, Any]] = []
        model_reasons: list[dict[str, str]] = []
        for fast, slow in TREND_MODELS:
            evaluated = evaluate_trend_scenario(
                closes,
                assumed_close,
                fast=fast,
                slow=slow,
                target_vol=TARGET_VOLATILITY,
                cap=EXPOSURE_CAP,
                volatility_window=VOLATILITY_WINDOW,
            )
            model_results.append(
                {
                    "fast_slow": [fast, slow],
                    "parameters": {
                        "volatility_window": VOLATILITY_WINDOW,
                        "target_vol": TARGET_VOLATILITY,
                        "cap": EXPOSURE_CAP,
                    },
                    **evaluated,
                }
            )
            if evaluated.get("available") is not True:
                model_reasons.append(
                    {
                        "code": str(evaluated.get("reason_code") or "model_unavailable"),
                        "message": str(evaluated.get("message") or "该趋势模型暂不可用。"),
                    }
                )

        baseline_identity = {
            "symbol": symbol,
            "dataset_id": str(dataset.id),
            "dataset_hash": str(dataset.content_sha256),
            "chart_hash": str(chart.content_sha256),
            "input_hash": baseline_data["input_hash"],
            "last_bar_date": baseline_data["last_bar_date"].isoformat(),
            "adjustment": baseline_data["adjustment"],
            "source": baseline_data["source"],
            "model_version": MODEL_VERSION,
            "model_parameters": _model_parameters(),
        }
        baseline_id = content_sha256(baseline_identity)
        scenario_id = content_sha256(
            {
                "baseline_id": baseline_id,
                "quote_as_of": quote_as_of.isoformat(),
                "assumed_close": assumed_close,
            }
        )
        reasons = model_reasons
        available_model_count = sum(
            model.get("available") is True for model in model_results
        )
        if available_model_count == len(TREND_MODELS):
            model_status = "ok"
        elif available_model_count:
            model_status = "partial"
        else:
            model_status = _UNAVAILABLE_STATUS
        symbol_results[symbol] = {
            "status": model_status,
            "reasons": reasons,
            "baseline": {
                "dataset_id": str(dataset.id),
                "dataset_hash": str(dataset.content_sha256),
                "chart_hash": str(chart.content_sha256),
                "input_hash": baseline_data["input_hash"],
                "last_bar_date": baseline_data["last_bar_date"].isoformat(),
                "adjustment": baseline_data["adjustment"],
                "source": baseline_data["source"],
                "baseline_id": baseline_id,
            },
            "quote_as_of": quote_as_of.isoformat(),
            "scenario_id": scenario_id,
            "assumed_close": assumed_close,
            "provisional": True,
            "models": model_results,
        }

    statuses = [str(item.get("status") or _UNAVAILABLE_STATUS) for item in symbol_results.values()]
    if statuses and all(status == "ok" for status in statuses):
        overall_status = "ok"
    elif any(status in {"ok", "partial"} for status in statuses):
        overall_status = "partial"
    else:
        overall_status = _UNAVAILABLE_STATUS
    return {
        "schema_version": OVERVIEW_SCHEMA_VERSION,
        "status": overall_status,
        "evaluated_at": now_utc.isoformat(),
        "symbols": symbol_results,
    }


def _validated_quote(
    item: Any,
    *,
    now: datetime,
) -> tuple[dict[str, Any] | None, datetime | None, tuple[str, str] | None]:
    if not isinstance(item, Mapping):
        return None, None, ("quote_unavailable", "盘中投影中没有该 ETF 的报价。")
    quote = item.get("quote")
    if not isinstance(quote, Mapping):
        return None, None, ("quote_unavailable", "盘中投影中没有该 ETF 的有效报价对象。")

    quote_as_of = _parse_aware_utc(quote.get("quote_time"))
    if quote.get("quote_time_status") != "known" or quote_as_of is None:
        return None, None, ("quote_time_unavailable", "报价来源时间未知或格式无效。")
    if quote_as_of > now:
        return None, quote_as_of, ("future_quote", "报价时间晚于显式评估时间。")
    if now - quote_as_of > MAX_QUOTE_AGE:
        return None, quote_as_of, ("stale_quote", "报价来源时间已超过 600 秒新鲜度上限。")

    quality = str(quote.get("quality_status") or "").strip().lower()
    if quality not in {"ready", "ok", "available"}:
        return None, quote_as_of, ("quote_quality_unavailable", "报价质量状态不满足情景计算要求。")
    price = _positive_finite(quote.get("last_price"))
    if price is None:
        return None, quote_as_of, ("quote_price_unavailable", "报价价格必须是有限正数。")
    if str(quote.get("price_basis") or "").strip().lower() != "raw":
        return None, quote_as_of, ("quote_price_basis_unknown", "报价价格口径未知，不能与冻结日线比较。")

    return {**dict(quote), "last_price": price}, quote_as_of, None


def _latest_known_frozen_chart(
    session: Session,
    symbol: str,
    *,
    quote_as_of: datetime,
) -> tuple[DecisionChartProjectionModel | None, DailyDecisionDatasetModel | None]:
    dialect = session.get_bind().dialect.name
    chart_payload = DecisionChartProjectionModel.payload_json
    if dialect == "sqlite":
        instrument_path = f"$.instruments.{symbol}"
        bars_path = f"{instrument_path}.price.bars"
        instrument_type = func.json_type(chart_payload, instrument_path)
        bars_type = func.json_type(chart_payload, bars_path)
        bars_count = func.json_array_length(chart_payload, bars_path)
        symbol_present = and_(
            instrument_type == "object",
            bars_type == "array",
            bars_count > 0,
        )
    elif dialect == "postgresql":
        payload_jsonb = cast(chart_payload, JSONB)
        instrument_node = payload_jsonb.op("#>")(array(["instruments", symbol]))
        bars_node = payload_jsonb.op("#>")(array(["instruments", symbol, "price", "bars"]))
        symbol_present = and_(
            func.jsonb_typeof(instrument_node) == "object",
            func.jsonb_typeof(bars_node) == "array",
            func.jsonb_array_length(bars_node) > 0,
        )
    else:
        # Unknown database JSON operators are not a reason to decode every
        # frozen chart into Python.  Fail closed until the dialect has a
        # bounded JSON-path selector.
        return None, None

    statement = (
        select(DecisionChartProjectionModel, DailyDecisionDatasetModel)
        .join(
            DailyDecisionDatasetModel,
            DailyDecisionDatasetModel.id == DecisionChartProjectionModel.dataset_id,
        )
        .where(
            symbol_present,
            DecisionChartProjectionModel.created_at <= quote_as_of,
            DailyDecisionDatasetModel.created_at <= quote_as_of,
            DailyDecisionDatasetModel.cutoff_time <= quote_as_of,
        )
        .order_by(
            DailyDecisionDatasetModel.trading_date.desc(),
            DailyDecisionDatasetModel.created_at.desc(),
            DecisionChartProjectionModel.created_at.desc(),
        )
        .limit(1)
    )
    row = session.execute(statement).first()
    if row is None:
        return None, None
    return row[0], row[1]


def _validated_baseline(
    dataset: DailyDecisionDatasetModel,
    chart: DecisionChartProjectionModel,
    *,
    symbol: str,
    quote: Mapping[str, Any],
    quote_as_of: datetime,
    calendar: Any,
) -> dict[str, Any]:
    dataset_payload = dataset.payload_json
    chart_payload = chart.payload_json
    if not isinstance(dataset_payload, Mapping) or not isinstance(chart_payload, Mapping):
        raise _EvidenceUnavailable("frozen_payload_invalid", "冻结的日线 dataset 或 chart payload 不是对象。")

    if _database_time(dataset.created_at) > quote_as_of:
        raise _EvidenceUnavailable("dataset_known_after_quote", "Daily Decision Dataset 在报价之后才创建。")
    if _database_time(chart.created_at) > quote_as_of:
        raise _EvidenceUnavailable("chart_known_after_quote", "冻结 chart 在报价之后才创建。")
    cutoff_time = _parse_aware_utc(dataset_payload.get("cutoff_time"))
    if cutoff_time is None or cutoff_time > quote_as_of or _database_time(dataset.cutoff_time) > quote_as_of:
        raise _EvidenceUnavailable("dataset_cutoff_after_quote", "冻结日线截止时间晚于报价时间或不可验证。")

    dataset_digest = str(dataset.content_sha256 or "")
    dataset_payload_digest = str(dataset_payload.get("content_sha256") or "")
    expected_dataset_digest = content_sha256(
        {
            key: value
            for key, value in dataset_payload.items()
            if key not in {"dataset_id", "content_sha256"}
        }
    )
    if (
        dataset_payload.get("dataset_id") != dataset.id
        or not dataset_digest
        or dataset_payload_digest != dataset_digest
        or expected_dataset_digest != dataset_digest
    ):
        raise _EvidenceUnavailable("dataset_hash_mismatch", "Daily Decision Dataset 内容 hash 校验失败。")

    chart_digest = str(chart.content_sha256 or "")
    chart_payload_digest = str(chart_payload.get("content_sha256") or "")
    expected_chart_digest = content_sha256(
        {key: value for key, value in chart_payload.items() if key != "content_sha256"}
    )
    if (
        chart.dataset_id != dataset.id
        or chart_payload.get("dataset_id") != dataset.id
        or not chart_digest
        or chart_payload_digest != chart_digest
        or expected_chart_digest != chart_digest
    ):
        raise _EvidenceUnavailable("chart_hash_mismatch", "Decision Chart Projection 内容 hash 校验失败。")

    manifest_rows = dataset_payload.get("bar_manifest")
    if not isinstance(manifest_rows, list):
        raise _EvidenceUnavailable("manifest_unavailable", "冻结 Dataset 缺少日线 bar manifest。")
    manifests = [
        item
        for item in manifest_rows
        if isinstance(item, Mapping) and str(item.get("symbol") or "").upper() == symbol
    ]
    if len(manifests) != 1:
        raise _EvidenceUnavailable("manifest_symbol_unavailable", "日线 manifest 中没有唯一的标的记录。")
    manifest = manifests[0]

    instruments = chart_payload.get("instruments")
    instrument = instruments.get(symbol) if isinstance(instruments, Mapping) else None
    price = instrument.get("price") if isinstance(instrument, Mapping) else None
    raw_bars = price.get("bars") if isinstance(price, Mapping) else None
    if not isinstance(raw_bars, list) or not raw_bars:
        raise _EvidenceUnavailable("chart_bars_unavailable", "冻结 chart 中没有该 ETF 的日线 bar。")
    bars = [dict(bar) for bar in raw_bars if isinstance(bar, Mapping)]
    if len(bars) != len(raw_bars):
        raise _EvidenceUnavailable("chart_bars_invalid", "冻结 chart 日线 bar 包含非法对象。")

    try:
        bar_dates = [date.fromisoformat(str(bar.get("date") or "")) for bar in bars]
    except ValueError as exc:
        raise _EvidenceUnavailable("chart_dates_invalid", "冻结 chart 日线日期格式无效。") from exc
    if bar_dates != sorted(set(bar_dates)):
        raise _EvidenceUnavailable("chart_dates_invalid", "冻结 chart 日线日期重复或未按时间排序。")

    adjustment = str(manifest.get("adjustment") or "").strip().upper()
    source = str(manifest.get("source") or "").strip()
    source_revisions = manifest.get("source_revisions")
    manifest_bar_count = manifest.get("bar_count")
    if adjustment != "NONE" or str(quote.get("price_basis") or "").strip().lower() != "raw":
        raise _EvidenceUnavailable(
            "price_basis_mismatch",
            "首版只接受 NONE 日线与 raw 报价；复权价格口径不兼容。",
        )
    if not source or not isinstance(source_revisions, list) or not source_revisions:
        raise _EvidenceUnavailable("bar_source_unavailable", "日线来源或来源修订清单不可验证。")
    if any(str(bar.get("adjustment") or "").strip().upper() != adjustment for bar in bars):
        raise _EvidenceUnavailable("mixed_adjustment", "冻结日线包含混合复权口径。")
    if manifest_bar_count != len(bars):
        raise _EvidenceUnavailable("manifest_bar_count_mismatch", "冻结日线数量与 manifest 不一致。")

    symbol_quality = (
        (dataset_payload.get("quality") or {}).get("symbols", {}).get(symbol)
        if isinstance(dataset_payload.get("quality"), Mapping)
        else None
    )
    instrument_quality = instrument.get("quality") if isinstance(instrument, Mapping) else None
    manifest_quality_status = str(manifest.get("quality_status") or "").strip().lower()
    dataset_quality_status = (
        str(symbol_quality.get("status") or "").strip().lower()
        if isinstance(symbol_quality, Mapping)
        else ""
    )
    chart_quality_status = (
        str(instrument_quality.get("status") or "").strip().lower()
        if isinstance(instrument_quality, Mapping)
        else ""
    )
    for quality_status in (
        dataset_quality_status,
        chart_quality_status,
        manifest_quality_status,
    ):
        if quality_status and quality_status not in {"ok", "partial"}:
            raise _EvidenceUnavailable(
                "daily_quality_unavailable",
                "该 ETF 的冻结日线质量状态不是 ok 或可验证的 partial。",
            )
    if not dataset_quality_status or not chart_quality_status:
        raise _EvidenceUnavailable("daily_quality_unavailable", "缺少该 ETF 的冻结日线质量状态。")

    closes: list[float] = []
    for bar in bars:
        values: dict[str, float] = {}
        for field in ("open", "high", "low", "close"):
            value = _positive_finite(bar.get(field))
            if value is None:
                reason_code = "daily_close_invalid" if field == "close" else "daily_ohlcv_invalid"
                raise _EvidenceUnavailable(reason_code, "冻结日线 OHLC 必须是有限正数。")
            values[field] = value
        volume = _finite_number(bar.get("volume"))
        if volume is None or volume < 0:
            raise _EvidenceUnavailable("daily_volume_invalid", "冻结日线成交量必须是有限非负数。")
        if (
            values["high"] < max(values["open"], values["close"])
            or values["low"] > min(values["open"], values["close"])
            or values["low"] > values["high"]
        ):
            raise _EvidenceUnavailable("daily_ohlcv_invalid", "冻结日线 OHLC 关系非法。")
        closes.append(values["close"])
    try:
        input_hash = hash_bars(bars)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise _EvidenceUnavailable("bar_hash_unavailable", "冻结日线无法计算规范输入 hash。") from exc
    if str(manifest.get("input_bar_hash") or "") != input_hash:
        raise _EvidenceUnavailable("input_hash_mismatch", "日线 manifest input hash 与冻结 chart 不一致。")

    last_bar_date = bar_dates[-1]
    manifest_end = str(manifest.get("end_date") or "")
    if manifest_end != last_bar_date.isoformat() or dataset.trading_date != last_bar_date:
        raise _EvidenceUnavailable("dataset_bar_date_mismatch", "Dataset 日期与该 ETF 最后日线日期不一致。")
    quote_session_date = _quote_session_date(quote_as_of, calendar)
    if quote_session_date is None:
        raise _EvidenceUnavailable("quote_session_unavailable", "报价日期无法映射到真实交易时段。")
    expected_previous_session = _previous_session(quote_session_date, calendar)
    if expected_previous_session is None or last_bar_date != expected_previous_session:
        raise _EvidenceUnavailable(
            "daily_close_not_previous_session",
            "冻结日线必须截止到报价交易日前一个真实交易日。",
        )

    expected_sessions = _sessions_in_range(bar_dates[0], bar_dates[-1], calendar)
    if expected_sessions != bar_dates:
        raise _EvidenceUnavailable("daily_bars_discontinuous", "冻结日线交易日序列存在缺口或非交易日。")

    return {
        "bars": bars,
        "input_hash": input_hash,
        "last_bar_date": last_bar_date,
        "adjustment": adjustment,
        "source": source,
    }


class _EvidenceUnavailable(Exception):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


def _unavailable_symbol(
    symbol: str,
    reason_code: str,
    message: str,
    *,
    quote_as_of: datetime | None = None,
    dataset_id: str | None = None,
    chart_hash: str | None = None,
) -> dict[str, Any]:
    return {
        "status": _UNAVAILABLE_STATUS,
        "reasons": [{"code": reason_code, "message": message}],
        "baseline": {
            "dataset_id": dataset_id,
            "dataset_hash": None,
            "chart_hash": chart_hash,
            "input_hash": None,
            "last_bar_date": None,
            "adjustment": None,
            "source": None,
            "baseline_id": None,
        },
        "quote_as_of": quote_as_of.isoformat() if quote_as_of else None,
        "scenario_id": None,
        "assumed_close": None,
        "provisional": True,
        "models": [],
    }


def _model_parameters() -> dict[str, Any]:
    return {
        "fast_slow": [list(pair) for pair in TREND_MODELS],
        "target_vol": TARGET_VOLATILITY,
        "cap": EXPOSURE_CAP,
        "volatility_window": VOLATILITY_WINDOW,
    }


def _positive_finite(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _require_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be an aware UTC datetime")
    if value.utcoffset() != timedelta(0):
        raise ValueError("now must be expressed in UTC")
    return value.astimezone(UTC)


def _parse_aware_utc(value: Any) -> datetime | None:
    if not isinstance(value, (str, datetime)):
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _database_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        # SQLite strips timezone metadata for DateTime(timezone=True).  The
        # stored values were written as UTC by the existing evidence service.
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@lru_cache(maxsize=4)
def _calendar(name: str) -> Any:
    import exchange_calendars as xcals

    return xcals.get_calendar(name)


def _calendar_label_date(value: Any) -> date:
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value)[:10])


def _quote_session_date(value: datetime, calendar: Any) -> date | None:
    calendar_timezone = ZoneInfo(str(calendar.tz))
    candidate = value.astimezone(calendar_timezone).date()
    try:
        return candidate if calendar.is_session(candidate) else None
    except Exception:
        return None


def _previous_session(session_date: date, calendar: Any) -> date | None:
    previous = calendar.sessions_in_range(
        (session_date - timedelta(days=14)).isoformat(),
        session_date.isoformat(),
    )
    dates = sorted({_calendar_label_date(item) for item in previous})
    if len(dates) < 2 or dates[-1] != session_date:
        return None
    return dates[-2]


def _sessions_in_range(start: date, end: date, calendar: Any) -> list[date]:
    sessions = calendar.sessions_in_range(start.isoformat(), end.isoformat())
    return sorted({_calendar_label_date(item) for item in sessions})
