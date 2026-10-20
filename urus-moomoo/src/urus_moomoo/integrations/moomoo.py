from __future__ import annotations

import math
import os
import logging
import socket
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from urus_moomoo.analytics.technical import calculate_relative_strength, calculate_technical_indicators
from urus_moomoo.services.intraday_equities import (
    build_minute_timestamp_policy,
    build_intraday_equity_plan,
    normalize_equity_symbol,
)

logger = logging.getLogger(__name__)


DEFAULT_MARKET_SNAPSHOT_SYMBOLS: tuple[str, ...] = ()
MAX_SNAPSHOT_CODES_PER_REQUEST = 400
DEFAULT_EQUITY_REQUEST_TIMEOUT_SECONDS = 3.0


class _RequestBudgetExceeded(RuntimeError):
    pass

INSTRUMENT_THEMES_BY_SYMBOL: dict[str, list[str]] = {}


class MarketCollectorAdapter(Protocol):
    def market_card(self, symbol: str) -> dict[str, object]: ...

    def capital_flow_day(self, symbol: str, trading_date: date) -> dict[str, Any]: ...


class MoomooAdapter(Protocol):
    """Provider boundary for market, history, capital-flow and option evidence."""

    def market_card(self, symbol: str) -> dict[str, object]: ...

    def instrument_card(self, symbol: str) -> dict[str, object]: ...

    def capital_flow_day(self, symbol: str, trading_date: date) -> dict[str, Any]: ...

    def trading_days(self, start: date, end: date) -> list[dict[str, Any]]: ...

    def options_placeholder(self, symbol: str) -> dict[str, object]: ...


@dataclass(frozen=True)
class MockQuote:
    symbol: str
    label: str
    last_price: float
    change_percent: float
    trend: str
    session_note: str | None = None
    technical_note: str | None = None
    note: str = "固定 mock 占位数据，不代表实时市场事实。"

    def as_dict(self) -> dict[str, object]:
        return {
            "is_mock": True,
            "data_mode": "mock",
            "source": "mock_adapter",
            "data_state": "mock",
            "symbol": self.symbol,
            "label": self.label,
            "last_price": self.last_price,
            "change_percent": self.change_percent,
            "trend": self.trend,
            "session_note": self.session_note,
            "technical_note": self.technical_note,
            "note": self.note,
        }


class MockMoomooAdapter:
    """Deterministic offline adapter used when live collection is disabled."""

    is_live = False

    def market_card(self, symbol: str) -> dict[str, object]:
        return MockQuote(
            symbol=symbol,
            label=f"{symbol} · mock market proxy",
            last_price=481.26,
            change_percent=0.42,
            trend="mock trend unavailable",
            session_note="framework sample session",
        ).as_dict()

    def instrument_card(self, symbol: str) -> dict[str, object]:
        # The offline adapter must accept the same full universe as the live
        # configuration, while remaining explicitly mock/unavailable.
        return MockQuote(
            symbol=symbol,
            label=f"{symbol} · mock instrument",
            last_price=100.0,
            change_percent=0.0,
            trend="mock trend unavailable",
            technical_note="技术指标尚未实现；当前仅展示占位字段。",
        ).as_dict()

    def instrument_cards(self, symbols: list[str]) -> dict[str, object]:
        cards = [self.instrument_card(symbol) for symbol in symbols]
        for card in cards:
            card["data_state"] = "unavailable"
        return {
            "is_mock": True,
            "status": "unavailable",
            "available": False,
            "data_state": "unavailable",
            "provider": "mock_adapter",
            "source_mode": "mock",
            "requested_symbols": symbols,
            "unavailable_symbols": symbols,
            "instruments": cards,
            "quality_status": "unavailable",
            "quality_warnings": ["尚未启用 Moomoo OpenD，当前为 mock 结构。"],
            "note": "真实行情尚未启用。",
        }

    def options_placeholder(self, symbol: str) -> dict[str, object]:
        return {
            "is_mock": True,
            "status": "placeholder",
            "available": False,
            "note": f"{symbol} 的 IV/GEX/期权链尚未实现；此处不是实时期权数据。",
        }


class DisabledMoomooAdapter(MockMoomooAdapter):
    """Named disabled implementation for dependency injection and future replacement."""


class OpenDMarketAdapter:
    """OpenD adapter for market snapshots and completed daily history.

    The SDK and TCP context are lazy so importing Urus and running tests never
    contacts OpenD.  The response is converted to plain JSON-compatible data;
    no Futu/moomoo SDK objects cross into workflows or schemas.

    The primary market call is a single batched snapshot for the configured ETF
    universe.  US index requests such as direct VIX are intentionally not sent
    to OpenD; the daily macro adapter owns Yahoo/FRED index context.
    """

    _connect_timeout_seconds = 3.0
    _close_timeout_seconds = 2.0
    is_live = True

    def __init__(
        self,
        host: str,
        port: int,
        *,
        market_timezone: str = "America/New_York",
        history_days: int = 260,
        sdk_home: Path | None = None,
        market_symbols: list[str] | None = None,
        sdk: Any | None = None,
        quote_context: Any | None = None,
        history_admission: Any | None = None,
        rate_limiter: Any | None = None,
        history_request_interval_seconds: float = 0.55,
        snapshot_request_interval_seconds: float = 0.55,
        trading_calendar_request_interval_seconds: float = 1.05,
        monotonic_clock: Any = monotonic,
        sleeper: Any = sleep,
    ) -> None:
        self.host = host
        self.port = port
        self.market_timezone = market_timezone
        self.history_days = max(history_days, 20)
        self.sdk_home = sdk_home
        configured_symbols = market_symbols or list(DEFAULT_MARKET_SNAPSHOT_SYMBOLS)
        self.market_symbols = _unique_quote_codes(configured_symbols)
        self._sdk = sdk
        self._quote_ctx = quote_context
        # Optional persistence-backed gate.  Keeping it at this seam means
        # direct adapter tests and mock mode remain usable while every live
        # history call can be refused before touching OpenD.
        self._history_admission = history_admission
        self._rate_limiter = rate_limiter
        # 60 requests / 30 seconds is the OpenD ceiling for snapshot/history
        # calls. Keep a small margin even when deployment settings are lower.
        self.history_request_interval_seconds = max(0.51, float(history_request_interval_seconds))
        self.snapshot_request_interval_seconds = max(0.51, float(snapshot_request_interval_seconds))
        # Moomoo documents a 30 requests / 30 seconds limit for the trading
        # calendar endpoint. Calendar reads are cached by the caller, but keep
        # a provider-safe interval here as well for process restarts.
        self.trading_calendar_request_interval_seconds = max(
            1.01, float(trading_calendar_request_interval_seconds)
        )
        self._monotonic_clock = monotonic_clock
        self._sleeper = sleeper
        self._last_history_request_at: float | None = None
        self._last_snapshot_request_at: float | None = None
        self._last_trading_calendar_request_at: float | None = None
        self._active_request_deadline_at: datetime | None = None
        self._active_request_timeout_seconds = DEFAULT_EQUITY_REQUEST_TIMEOUT_SECONDS
        self._active_request_budget_exhausted = False
        # Keep one immutable-in-run snapshot projection so repeated reads do
        # not consume another OpenD frequency slot.
        self._snapshot_cache: dict[str, dict[str, Any]] = {}
        self._history_cache: dict[str, list[dict[str, object]]] = {}

    def reset_collection_run(self) -> None:
        """Drop caches whose values are only valid within one task."""

        self._snapshot_cache.clear()
        self._history_cache.clear()

    def quota_snapshot(self) -> dict[str, object]:
        """Expose the read-only quota probe for Capacity Planning."""

        return self._quota_snapshot()

    def fetch_intraday_history_5m(
        self,
        symbol: str,
        trading_date: str,
        *,
        deadline_at: datetime,
        timestamp_role: str = "start",
    ) -> list[dict[str, object]]:
        """Fetch one regular-session 5m history page set within a request budget."""

        if timestamp_role not in {"start", "end"}:
            raise ValueError("timestamp_role must be 'start' or 'end'")
        session_day = date.fromisoformat(str(trading_date))
        normalized_symbol = normalize_equity_symbol(symbol)
        budget_deadline = deadline_at
        if budget_deadline.tzinfo is None:
            budget_deadline = budget_deadline.replace(tzinfo=ZoneInfo(self.market_timezone))
        remaining = (budget_deadline.astimezone(UTC) - datetime.now(UTC)).total_seconds()
        timeout_seconds = max(0.1, min(DEFAULT_EQUITY_REQUEST_TIMEOUT_SECONDS, remaining))
        with self._request_budget(deadline_at, timeout_seconds):
            sdk = self._ensure_sdk()
            quote_context = self._quote_ctx
            request_method = getattr(quote_context, "request_history_kline", None)
            if not callable(request_method):
                raise RuntimeError("OpenD 5m history method unavailable")

            rows: list[dict[str, object]] = []
            page_req_key: Any = None
            while True:
                self._pace(
                    "_last_history_request_at",
                    self.history_request_interval_seconds,
                    rate_class="moomoo_quote_history",
                )
                with self._sdk_request_timeout():
                    response = request_method(
                        code=f"US.{normalized_symbol}",
                        start=session_day.isoformat(),
                        end=session_day.isoformat(),
                        ktype=getattr(getattr(sdk, "KLType", None), "K_5M", "K_5M"),
                        autype=getattr(getattr(sdk, "AuType", None), "NONE", "None"),
                        max_count=1000,
                        page_req_key=page_req_key,
                        extended_time=False,
                    )
                if not isinstance(response, tuple) or len(response) < 2:
                    raise RuntimeError(f"unexpected 5m history response: {response!r}")
                ret_code, raw_frame = response[0], response[1]
                next_page_req_key = response[2] if len(response) >= 3 else None
                if ret_code != getattr(sdk, "RET_OK", 0):
                    raise RuntimeError(
                        f"request_history_kline(US.{normalized_symbol}, K_5M) failed: {raw_frame}"
                    )
                rows.extend(_iter_rows(raw_frame))
                if next_page_req_key is None:
                    break
                if next_page_req_key == page_req_key:
                    raise RuntimeError("5m history pagination key did not advance")
                page_req_key = next_page_req_key

            bars: list[dict[str, object]] = []
            market_timezone = ZoneInfo(self.market_timezone)
            for row in rows:
                raw_time = str(row.get("time_key") or row.get("bar_time") or "").strip()
                if not raw_time:
                    continue
                try:
                    source_time = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                    if source_time.tzinfo is None:
                        source_time = source_time.replace(tzinfo=market_timezone)
                    source_time = source_time.astimezone(UTC)
                    if timestamp_role == "start":
                        bar_start = source_time
                        bar_end = bar_start + timedelta(minutes=5)
                    else:
                        bar_end = source_time
                        bar_start = bar_end - timedelta(minutes=5)
                    values = {
                        field: _number(row, [field])
                        for field in ("open", "high", "low", "close", "volume", "turnover")
                    }
                except (TypeError, ValueError, KeyError, OverflowError):
                    continue
                if bar_start.astimezone(market_timezone).date() != session_day:
                    continue
                if any(
                    not math.isfinite(value)
                    for value in values.values()
                ):
                    continue
                if any(values[field] <= 0 for field in ("open", "high", "low", "close")):
                    continue
                if values["volume"] < 0 or values["turnover"] < 0:
                    continue
                if not (
                    values["low"]
                    <= min(values["open"], values["close"])
                    <= max(values["open"], values["close"])
                    <= values["high"]
                ):
                    continue
                bars.append(
                    {
                        "provider": "moomoo_openapi",
                        "symbol": normalized_symbol,
                        "timeframe": "5m",
                        "adjustment": "none",
                        "trading_date": session_day.isoformat(),
                        "bar_start_at": bar_start.isoformat(),
                        "bar_end_at": bar_end.isoformat(),
                        **values,
                        "vwap": _optional_number(row, ["vwap", "wavg"]),
                        "quality_state": "ready",
                    }
                )
            return bars

    def trading_days(self, start: date, end: date) -> list[dict[str, Any]]:
        """Return Moomoo's US trading-date calendar for an inclusive range."""

        if start > end:
            return []
        sdk = self._ensure_sdk()
        market_enum = getattr(sdk, "TradeDateMarket", None)
        market = getattr(market_enum, "US", "US")
        self._pace(
            "_last_trading_calendar_request_at",
            self.trading_calendar_request_interval_seconds,
            rate_class="moomoo_trading_calendar",
        )
        rows = _iter_rows(
            self._call(
                "request_trading_days",
                market=market,
                start=start.isoformat(),
                end=end.isoformat(),
            )
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            raw_date = _value(row, ["time", "date", "trading_date", "trade_date"])
            result.append(
                {
                    "time": str(raw_date)[:10],
                    "trade_date_type": str(
                        _value(
                            row,
                            ["trade_date_type", "trading_day_type", "tradeDateType"],
                            default="WHOLE",
                        )
                    ),
                }
            )
        return result

    def close(self) -> None:
        if self._quote_ctx is None:
            return
        quote_context = self._quote_ctx
        self._quote_ctx = None
        close = getattr(quote_context, "close", None)
        if not close:
            return

        finished = threading.Event()

        def close_context() -> None:
            try:
                close()
            except Exception:
                logger.warning("Moomoo OpenD context close failed", exc_info=True)
            finally:
                finished.set()

        # The SDK close path can block inside its network thread. Keep cleanup
        # bounded so a completed workflow can still persist its final state.
        thread = threading.Thread(
            target=close_context,
            name="urus-opend-close",
            daemon=True,
        )
        thread.start()
        if not finished.wait(timeout=self._close_timeout_seconds):
            logger.warning("Moomoo OpenD context close timed out; continuing shutdown")

    def market_card(self, symbol: str) -> dict[str, object]:
        display_symbol = symbol.split(".")[-1].upper()
        quote_code = _normalise_quote_code(symbol)
        requested_codes = list(self.market_symbols)
        if quote_code not in requested_codes:
            requested_codes.insert(0, quote_code)

        market_snapshot = self._collect_market_snapshot(requested_codes)
        snapshot_row = market_snapshot["rows"].get(quote_code)
        if not isinstance(snapshot_row, dict):
            raise RuntimeError(f"OpenD 批量快照未返回必需标的 {quote_code}")
        previous_close = _number(
            snapshot_row,
            ["prev_close_price", "pre_close_price", "previous_close", "prev_close"],
        )
        last_price = _number(snapshot_row, ["last_price", "cur_price", "price"])
        premarket_price = _positive_or_none(
            _number(snapshot_row, ["pre_price", "premarket_price"], default=0)
        )
        afterhours_price = _positive_or_none(
            _number(snapshot_row, ["after_price", "afterhours_price"], default=0)
        )
        quote_time = _quote_time(snapshot_row)
        session, session_label = _classify_session(datetime.now(ZoneInfo(self.market_timezone)))
        display_price, display_source = _session_price(
            session,
            last_price=last_price,
            premarket_price=premarket_price,
            afterhours_price=afterhours_price,
        )

        history = self._history_summary(quote_code, previous_close, include_bars=True)
        history.pop("bars", None)
        warnings = list(history.get("warnings", []))
        warnings.extend(str(item) for item in market_snapshot["quality_warnings"])
        quality_status = (
            "ok"
            if (
                history.get("available")
                and market_snapshot["quality_status"] == "ok"
                and _history_indicators_ok(history)
            )
            else "partial"
        )
        change_percent = _percent(display_price, previous_close)
        regular_change_percent = _percent(last_price, previous_close)
        vix_snapshot = self._collect_vix_snapshot()
        if vix_snapshot.get("warning"):
            warnings.append(str(vix_snapshot["warning"]))

        market_snapshot_payload = {
            key: value
            for key, value in market_snapshot.items()
            if key != "rows"
        }
        market_snapshot_payload["quotes"] = [
            _normalise_snapshot_quote(row)
            for row in market_snapshot["rows"].values()
        ]
        market_snapshot_payload["vix"] = vix_snapshot

        return {
            "is_mock": False,
            "data_mode": "opend",
            "source": "moomoo_opend",
            "quote_code": quote_code,
            "symbol": display_symbol,
            "label": f"{display_symbol} · Moomoo OpenD",
            "last_price": display_price,
            "regular_price": last_price,
            "change_percent": change_percent,
            "regular_change_percent": regular_change_percent,
            "previous_close": previous_close,
            "volume": _integer(snapshot_row, ["volume"], default=0),
            "quote_time": quote_time,
            "session": session,
            "session_label": session_label,
            "session_price_source": display_source,
            "premarket_price": premarket_price,
            "premarket_volume": _integer(snapshot_row, ["pre_volume", "premarket_volume"], default=0),
            "premarket_change_percent": (
                _percent(premarket_price, previous_close) if premarket_price is not None else None
            ),
            "afterhours_price": afterhours_price,
            "afterhours_volume": _integer(snapshot_row, ["after_volume"], default=0),
            "afterhours_change_percent": (
                _percent(afterhours_price, previous_close) if afterhours_price is not None else None
            ),
            "trend": "not_calculated",
            "session_note": f"{session_label}；报价来源为 OpenD。",
            "history": history,
            "market_snapshot": market_snapshot_payload,
            "quality_status": quality_status,
            "quality_warnings": warnings,
            "note": (
                "大盘代理 ETF 使用一次 Moomoo OpenD 批量快照；"
                "VIX 等美国指数按策略不通过 Moomoo 请求，使用 Yahoo/FRED 宏观日频源；"
                "技术判断和其他流程仍未实现。"
            ),
        }

    def capital_flow_day(self, symbol: str, trading_date: date) -> dict[str, Any]:
        """Read one completed regular-session daily capital-flow row."""

        quote_code = _normalise_quote_code(symbol)
        sdk = self._ensure_sdk()
        period_type = (
            sdk.PeriodType.DAY
            if hasattr(sdk, "PeriodType")
            else "DAY"
        )
        rows = _iter_rows(
            self._call(
                "get_capital_flow",
                quote_code,
                period_type=period_type,
                start=trading_date.isoformat(),
                end=trading_date.isoformat(),
            )
        )
        matching = [
            row
            for row in rows
            if _capital_flow_date(row) == trading_date
        ]
        if not matching:
            raise RuntimeError(
                f"OpenD 未返回 {quote_code} {trading_date.isoformat()} 的日资金流"
            )
        row = matching[-1]
        super_flow = _optional_number(row, ["super_in_flow", "superInFlow"])
        big_flow = _optional_number(row, ["big_in_flow", "bigInFlow"])
        main_flow = _optional_number(row, ["main_in_flow", "mainInFlow"])
        warnings: list[str] = []
        if (
            main_flow is not None
            and super_flow is not None
            and big_flow is not None
            and not math.isclose(main_flow, super_flow + big_flow, rel_tol=1e-6, abs_tol=1.0)
        ):
            warnings.append("main_in_flow 与 super_in_flow + big_in_flow 不一致")
        normalized = {
            "in_flow": _optional_number(row, ["in_flow", "inFlow"]),
            "main_in_flow": main_flow,
            "super_in_flow": super_flow,
            "big_in_flow": big_flow,
            "mid_in_flow": _optional_number(row, ["mid_in_flow", "midInFlow"]),
            "sml_in_flow": _optional_number(row, ["sml_in_flow", "smlInFlow"]),
            "source_time": str(
                _value(
                    row,
                    ["capital_flow_item_time", "time"],
                    default=trading_date.isoformat(),
                )
            ),
            "quality_status": "ok" if not warnings else "partial",
            "quality_warnings": warnings,
        }
        return {
            **normalized,
            "raw_payload": {
                "trading_date": trading_date.isoformat(),
                **normalized,
            },
        }

    def instrument_card(self, symbol: str) -> dict[str, object]:
        """Collect one instrument card through the same snapshot-only boundary."""
        display_symbol = _display_symbol(_normalise_quote_code(symbol))
        payload = self.instrument_cards([display_symbol])
        cards = payload.get("instruments", [])
        if not isinstance(cards, list) or not cards:
            raise RuntimeError(f"OpenD 未返回 {symbol} 个股/ETF 数据")
        card_payload = next(
            (item for item in cards if isinstance(item, dict) and item.get("symbol") == display_symbol),
            None,
        )
        if card_payload is None:
            raise RuntimeError(f"OpenD 未返回 {symbol} 个股/ETF 数据")
        card = dict(card_payload)
        history_snapshots = payload.get("history_snapshots")
        if isinstance(history_snapshots, list):
            history_snapshot = next(
                (
                    item
                    for item in history_snapshots
                    if isinstance(item, dict) and item.get("symbol") == display_symbol
                ),
                None,
            )
            if history_snapshot is not None:
                card["history_snapshot"] = history_snapshot
        return card

    def instrument_cards(self, symbols: list[str]) -> dict[str, object]:
        """Collect the configured universe in one quote snapshot and daily histories."""
        if not symbols:
            raise ValueError("至少需要一个个股或 ETF 标的")
        quote_codes = _unique_quote_codes(symbols)
        captured_at = datetime.now(UTC).isoformat()
        quota_before = self._quota_snapshot()
        market_snapshot = self._collect_market_snapshot(quote_codes)
        cards: list[dict[str, object]] = []
        history_snapshots: list[dict[str, object]] = []
        unavailable_symbols: list[str] = []
        quality_warnings = list(market_snapshot.get("quality_warnings", []))
        records: dict[str, tuple[dict[str, object], dict[str, object], list[dict[str, object]]]] = {}

        for quote_code in quote_codes:
            row = market_snapshot["rows"].get(quote_code)
            if not isinstance(row, dict):
                unavailable_symbols.append(_display_symbol(quote_code))
                continue
            quote = _normalise_snapshot_quote(row)
            previous_close = quote.get("previous_close")
            history = self._history_summary(
                quote_code,
                float(previous_close) if isinstance(previous_close, (int, float)) else 0.0,
                include_bars=True,
            )
            history_public = dict(history)
            bars = history_public.pop("bars", [])
            display_symbol = _display_symbol(quote_code)
            card_warnings = list(history_public.get("warnings", []))
            card_quality = "ok" if history_public.get("available") and quote.get("last_price") is not None else "partial"
            quality_warnings.extend(f"{display_symbol}：{item}" for item in card_warnings)
            records[display_symbol] = (quote, history_public, bars)

        benchmark_bars: list[dict[str, object]] = []
        for display_symbol, (quote, history_public, bars) in records.items():
            if benchmark_bars:
                relative_strength = calculate_relative_strength(
                    bars,
                    benchmark_bars,
                    benchmark="configured_benchmark",
                    source="moomoo_opend_history",
                )
            else:
                relative_strength = {
                    "is_mock": False,
                    "available": False,
                    "quality_status": "unavailable",
                    "benchmark": None,
                    "source": "moomoo_opend_history",
                    "sample_count": 0,
                    "warnings": ["未配置相对强弱基准。"],
                }
            card_warnings = list(history_public.get("warnings", []))
            if relative_strength.get("warnings"):
                card_warnings.extend(str(item) for item in relative_strength["warnings"])
            card = {
                **quote,
                "is_mock": False,
                "data_mode": "opend",
                "data_state": "live",
                "provider": "moomoo_openapi",
                "source_mode": "snapshot",
                "captured_at": captured_at,
                "source": "moomoo_opend_snapshot",
                "asset_type": "etf" if display_symbol in DEFAULT_MARKET_SNAPSHOT_SYMBOLS else "equity",
                "theme": _instrument_theme(display_symbol),
                "themes": _instrument_themes(display_symbol),
                "label": f"{display_symbol} · Moomoo OpenD",
                "trend": _trend_from_history(history_public),
                "technical_note": "日线技术指标由 SQLite 可重算的 Moomoo 历史 K 线生成。",
                "history": history_public,
                "relative_strength": relative_strength,
                "quality_status": card_quality,
                "quality_warnings": card_warnings,
                "note": "当前采集价格、成交、日线技术指标与相对基准输入；财务和事件不属于本采集边界。",
            }
            cards.append(card)
            history_snapshots.append(
                {
                    "symbol": display_symbol,
                    "asset_type": "etf" if display_symbol in DEFAULT_MARKET_SNAPSHOT_SYMBOLS else "equity",
                    "history": {"bars": bars},
                }
            )

        quota_after = self._quota_snapshot()
        quota_audit = _quota_audit(
            quota_before,
            quota_after,
            requested_symbols=[_display_symbol(code) for code in quote_codes],
        )
        quality_warnings.extend(str(item) for item in quota_audit.get("warnings", []))
        if unavailable_symbols:
            quality_warnings.extend(f"采集未返回标的：{symbol}" for symbol in unavailable_symbols)
        return {
            "is_mock": False,
            "status": "available" if cards else "unavailable",
            "available": bool(cards),
            "data_state": "live" if cards else "unavailable",
            "provider": "moomoo_openapi",
            "source_mode": "snapshot",
            "captured_at": captured_at,
            "requested_symbols": [_display_symbol(code) for code in quote_codes],
            "unavailable_symbols": unavailable_symbols,
            "instruments": cards,
            "history_snapshots": history_snapshots,
            "quota_audit": quota_audit,
            "quality_status": "ok" if cards and not unavailable_symbols and all(
                card.get("quality_status") == "ok" for card in cards
            ) else "partial" if cards else "unavailable",
            "quality_warnings": quality_warnings,
            "note": "使用 Moomoo OpenD 批量快照和历史日线；未开启实时订阅。",
        }

    def capture_intraday_equities(
        self,
        *,
        symbols: list[str] | tuple[str, ...] | None = None,
        quote_symbols: list[str] | None = None,
        minute_symbols: list[str] | None = None,
        trading_date: date | str | None = None,
        scheduled_at: datetime | None = None,
        evidence_cutoff: datetime | None = None,
        groups: dict[str, list[str]] | None = None,
        group_by_symbol: dict[str, str] | None = None,
        slot_index: int = 0,
        interval: str = "K_1M",
        minute_timestamp_role: str = "start",
        minute_timestamp_verification: dict[str, Any] | None = None,
        capture_quotes: bool = True,
        shard_id: str | None = None,
        shard_symbols: list[str] | None = None,
        request_budget_deadline_at: datetime | str | None = None,
        request_timeout_seconds: float = DEFAULT_EQUITY_REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, object]:
        timeout = min(12.0, max(0.1, float(request_timeout_seconds)))
        deadline = request_budget_deadline_at
        if isinstance(deadline, str):
            try:
                deadline = datetime.fromisoformat(deadline)
            except ValueError as exc:
                raise ValueError("request_budget_deadline_at must be an ISO timestamp") from exc
        with self._request_budget(deadline, timeout):
            return self._capture_intraday_equities_impl(
                symbols=symbols,
                quote_symbols=quote_symbols,
                minute_symbols=minute_symbols,
                trading_date=trading_date,
                scheduled_at=scheduled_at,
                evidence_cutoff=evidence_cutoff,
                groups=groups,
                group_by_symbol=group_by_symbol,
                slot_index=slot_index,
                interval=interval,
                minute_timestamp_role=minute_timestamp_role,
                minute_timestamp_verification=minute_timestamp_verification,
                capture_quotes=capture_quotes,
                shard_id=shard_id,
                shard_symbols=shard_symbols,
            )

    def _capture_intraday_equities_impl(
        self,
        *,
        symbols: list[str] | tuple[str, ...] | None = None,
        quote_symbols: list[str] | None = None,
        minute_symbols: list[str] | None = None,
        trading_date: date | str | None = None,
        scheduled_at: datetime | None = None,
        evidence_cutoff: datetime | None = None,
        groups: dict[str, list[str]] | None = None,
        group_by_symbol: dict[str, str] | None = None,
        slot_index: int = 0,
        interval: str = "K_1M",
        minute_timestamp_role: str = "start",
        minute_timestamp_verification: dict[str, Any] | None = None,
        capture_quotes: bool = True,
        shard_id: str | None = None,
        shard_symbols: list[str] | None = None,
    ) -> dict[str, object]:
        """Capture bounded US equity quotes and admitted per-symbol minute bars.

        The default ``start`` timestamp role follows the existing collector
        convention. Moomoo's live-provider boundary semantics are unverified;
        confirm the role before enabling production capture.
        """

        if interval != "K_1M":
            raise ValueError("intraday equity capture currently supports only K_1M")
        if minute_timestamp_role not in {"start", "end"}:
            raise ValueError("minute_timestamp_role must be 'start' or 'end'")

        allowed_symbols = (
            list(dict.fromkeys(normalize_equity_symbol(symbol) for symbol in symbols))
            if symbols is not None
            else None
        )
        group_by_symbol: dict[str, str] = {}
        if quote_symbols is not None or minute_symbols is not None:
            if quote_symbols is None or minute_symbols is None or groups is not None:
                raise ValueError("supply both quote_symbols and minute_symbols, or supply groups")
            planned_quotes = list(dict.fromkeys(normalize_equity_symbol(item) for item in quote_symbols))
            planned_minutes = list(dict.fromkeys(normalize_equity_symbol(item) for item in minute_symbols))
            if not planned_quotes:
                raise ValueError("quote_symbols must not be empty")
            if not set(planned_minutes) <= set(planned_quotes):
                raise ValueError("minute_symbols must be contained in quote_symbols")
            if allowed_symbols is not None and not set(planned_quotes) <= set(allowed_symbols):
                raise ValueError("quote_symbols exceed the task allowed scope")
            supplied_groups = {
                normalize_equity_symbol(symbol): str(group).strip().upper()
                for symbol, group in dict(group_by_symbol or {}).items()
                if str(symbol).strip()
            }
            if not set(supplied_groups) <= set(planned_quotes):
                raise ValueError("group_by_symbol contains symbols outside quote_symbols")
            group_by_symbol = supplied_groups
        else:
            if groups is None:
                if allowed_symbols is None:
                    raise ValueError("symbols or a complete quote/minute plan is required")
                groups = {"C": allowed_symbols}
            plan = build_intraday_equity_plan(
                groups,
                slot_index=slot_index,
                allowed_symbols=allowed_symbols,
            )
            planned_quotes = list(plan["quote_symbols"])
            planned_minutes = list(plan["minute_symbols"])
            group_by_symbol = dict(plan["group_by_symbol"])

        if not planned_quotes:
            raise ValueError("intraday equity capture requires at least one quote symbol")

        if trading_date is None:
            if scheduled_at is None:
                raise ValueError("trading_date or scheduled_at is required")
            trading_day = scheduled_at.astimezone(ZoneInfo(self.market_timezone)).date()
        elif isinstance(trading_date, date):
            trading_day = trading_date
        else:
            trading_day = date.fromisoformat(str(trading_date))

        cutoff = evidence_cutoff or scheduled_at
        if cutoff is not None and cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=ZoneInfo(self.market_timezone))
        cutoff_utc = cutoff.astimezone(UTC) if cutoff is not None else None
        cutoff_text = cutoff.isoformat() if cutoff is not None else None
        captured_at = datetime.now(UTC).isoformat()
        quote_codes = [f"US.{symbol}" for symbol in planned_quotes]
        if capture_quotes:
            market_snapshot = self._collect_market_snapshot(
                quote_codes,
                raise_on_total_failure=False,
            )
        else:
            cached_rows = {
                code: self._snapshot_cache[code]
                for code in quote_codes
                if code in self._snapshot_cache
            }
            market_snapshot = {
                "rows": cached_rows,
                "request_batches": [],
                "quality_status": "ok" if len(cached_rows) == len(quote_codes) else "partial",
                "quality_warnings": (
                    []
                    if len(cached_rows) == len(quote_codes)
                    else ["本分片未重新请求报价；同槽 G1 快照不可用或尚未捕获。"]
                ),
            }
        quote_rows = market_snapshot.get("rows", {})
        quotes: list[dict[str, object]] = []
        unavailable_quote_symbols: list[str] = []
        for symbol, quote_code in zip(planned_quotes, quote_codes, strict=True):
            row = quote_rows.get(quote_code) if isinstance(quote_rows, dict) else None
            if not isinstance(row, dict):
                unavailable_quote_symbols.append(symbol)
                continue
            quotes.append({"symbol": symbol, "quote_code": quote_code, **row})

        history_admissions: list[dict[str, object]] = []
        admitted_symbols: list[str] = []
        deferred_symbols: list[str] = []
        history_batches: list[dict[str, object]] = []
        minute_bars: list[dict[str, object]] = []
        history_errors: list[dict[str, str]] = []
        missing_minute_symbols: list[str] = []
        budget_skipped_symbols: list[str] = []
        invalid_minute_bars: list[dict[str, object]] = []
        filtered_future_bars = 0

        sdk = self._ensure_sdk() if planned_minutes else None
        quote_context = self._quote_ctx
        if self._history_admission is None:
            batch_admissions = [
                {
                    "admitted": False,
                    "access_state": "pending_quota",
                    "reason_code": "quota_unavailable",
                    "symbol": normalize_equity_symbol(symbol),
                }
                for symbol in planned_minutes
            ]
        elif callable(getattr(self._history_admission, "acquire_many", None)):
            batch_admissions = self._history_admission.acquire_many(
                [f"US.{symbol}" for symbol in planned_minutes]
            )
        else:
            batch_admissions = [
                self._history_admission.acquire(f"US.{symbol}")
                for symbol in planned_minutes
            ]

        for symbol, admission in zip(planned_minutes, batch_admissions, strict=True):
            quote_code = f"US.{symbol}"
            history_admissions.append({"symbol": symbol, **admission})
            if self._active_request_budget_exhausted:
                budget_skipped_symbols.extend(planned_minutes[len(history_admissions) - 1 :])
                missing_minute_symbols.extend(planned_minutes[len(history_admissions) - 1 :])
                break
            if not bool(admission.get("admitted")):
                deferred_symbols.append(symbol)
                missing_minute_symbols.append(symbol)
                continue

            admitted_symbols.append(symbol)
            request_method = getattr(quote_context, "request_history_kline", None)
            if not callable(request_method) or sdk is None:
                message = "OpenD minute-history method unavailable"
                history_errors.append({"symbol": symbol, "error": message})
                history_batches.append(
                    {
                        "code": quote_code,
                        "ktype": interval,
                        "start": trading_day.isoformat(),
                        "end": trading_day.isoformat(),
                        "records": [],
                        "error": message,
                    }
                )
                missing_minute_symbols.append(symbol)
                continue

            series_rows: list[dict[str, Any]] = []
            page_req_key: Any = None
            page_index = 0
            symbol_error: str | None = None
            while True:
                request_started_at = datetime.now(UTC)
                try:
                    self._pace(
                        "_last_history_request_at",
                        self.history_request_interval_seconds,
                        rate_class="moomoo_quote_history",
                    )
                    request_started_at = datetime.now(UTC)
                    with self._sdk_request_timeout():
                        response = request_method(
                            code=quote_code,
                            start=trading_day.isoformat(),
                            end=trading_day.isoformat(),
                            ktype=getattr(getattr(sdk, "KLType", None), interval, interval),
                            autype=getattr(getattr(sdk, "AuType", None), "NONE", "None"),
                            max_count=1000,
                            page_req_key=page_req_key,
                            extended_time=False,
                        )
                    response_received_at = datetime.now(UTC)
                    if not isinstance(response, tuple) or len(response) < 2:
                        raise RuntimeError(f"unexpected minute-history response: {response!r}")
                    ret_code, raw_frame = response[0], response[1]
                    next_page_req_key = response[2] if len(response) >= 3 else None
                    if ret_code != getattr(sdk, "RET_OK", 0):
                        raise RuntimeError(f"request_history_kline({quote_code}, K_1M) failed: {raw_frame}")
                    rows = _iter_rows(raw_frame)
                    history_batches.append(
                        {
                            "code": quote_code,
                            "symbol": symbol,
                            "ktype": interval,
                            "start": trading_day.isoformat(),
                            "end": trading_day.isoformat(),
                            "page_index": page_index,
                            "request_started_at": request_started_at.isoformat(),
                            "response_received_at": response_received_at.isoformat(),
                            "records": rows,
                        }
                    )
                    series_rows.extend(rows)
                    if next_page_req_key is None:
                        break
                    if next_page_req_key == page_req_key:
                        raise RuntimeError("minute-history pagination key did not advance")
                    page_req_key = next_page_req_key
                    page_index += 1
                except _RequestBudgetExceeded:
                    budget_skipped_symbols.extend(planned_minutes[len(history_admissions) - 1 :])
                    missing_minute_symbols.extend(planned_minutes[len(history_admissions) - 1 :])
                    history_batches.append(
                        {
                            "code": quote_code,
                            "symbol": symbol,
                            "ktype": interval,
                            "start": trading_day.isoformat(),
                            "end": trading_day.isoformat(),
                            "page_index": page_index,
                            "request_started_at": request_started_at.isoformat(),
                            "response_received_at": datetime.now(UTC).isoformat(),
                            "records": [],
                            "skipped_reason": "skipped_budget",
                        }
                    )
                    break
                except Exception as exc:
                    symbol_error = str(exc)
                    response_received_at = datetime.now(UTC)
                    history_batches.append(
                        {
                            "code": quote_code,
                            "symbol": symbol,
                            "ktype": interval,
                            "start": trading_day.isoformat(),
                            "end": trading_day.isoformat(),
                            "page_index": page_index,
                            "request_started_at": request_started_at.isoformat(),
                            "response_received_at": response_received_at.isoformat(),
                            "records": [],
                            "error": symbol_error,
                        }
                    )
                    break

            added_bars = 0
            for row in series_rows:
                raw_bar_time = str(row.get("time_key") or row.get("bar_time") or "").strip()
                if not raw_bar_time:
                    continue
                try:
                    bar_time = datetime.fromisoformat(raw_bar_time.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if bar_time.tzinfo is None:
                    bar_time = bar_time.replace(tzinfo=ZoneInfo(self.market_timezone))
                source_time_utc = bar_time.astimezone(UTC)
                if minute_timestamp_role == "start":
                    bar_start_utc = source_time_utc
                    bar_end_utc = bar_start_utc + timedelta(minutes=1)
                else:
                    bar_end_utc = source_time_utc
                    bar_start_utc = bar_end_utc - timedelta(minutes=1)
                local_bar_start = bar_start_utc.astimezone(ZoneInfo(self.market_timezone))
                if local_bar_start.date() != trading_day:
                    continue
                if cutoff_utc is not None and bar_end_utc > cutoff_utc:
                    filtered_future_bars += 1
                    continue

                bar_values: dict[str, float] = {}
                invalid_reasons: list[str] = []
                for field in ("open", "high", "low", "close", "volume", "turnover"):
                    try:
                        value = _number(row, [field])
                    except (KeyError, TypeError, ValueError, OverflowError):
                        invalid_reasons.append(f"missing_or_invalid_{field}")
                        continue
                    if not math.isfinite(value):
                        invalid_reasons.append(f"non_finite_{field}")
                        continue
                    if field in {"open", "high", "low", "close"} and value <= 0:
                        invalid_reasons.append(f"non_positive_{field}")
                        continue
                    if field in {"volume", "turnover"} and value < 0:
                        invalid_reasons.append(f"negative_{field}")
                        continue
                    bar_values[field] = value

                if not invalid_reasons and not (
                    bar_values["low"]
                    <= min(bar_values["open"], bar_values["close"])
                    <= max(bar_values["open"], bar_values["close"])
                    <= bar_values["high"]
                ):
                    invalid_reasons.append("inconsistent_ohlc")

                if invalid_reasons:
                    invalid_minute_bars.append(
                        {
                            "symbol": symbol,
                            "bar_time": raw_bar_time,
                            "bar_start_at": bar_start_utc.isoformat(),
                            "bar_end_at": bar_end_utc.isoformat(),
                            "quality_state": "invalid",
                            "invalid_reasons": invalid_reasons,
                        }
                    )
                    continue

                try:
                    volume = bar_values["volume"]
                    turnover = bar_values["turnover"]
                    minute_bars.append(
                        {
                            "symbol": symbol,
                            "bar_time": raw_bar_time,
                            "bar_start_at": bar_start_utc.isoformat(),
                            "bar_end_at": bar_end_utc.isoformat(),
                            "open": bar_values["open"],
                            "high": bar_values["high"],
                            "low": bar_values["low"],
                            "close": bar_values["close"],
                            "volume": volume,
                            "turnover": turnover,
                            "vwap": turnover / volume if volume else None,
                            "provider": "moomoo_openapi",
                            "quality_state": "ready",
                        }
                    )
                    added_bars += 1
                except (KeyError, TypeError, ValueError):
                    continue
            if symbol_error:
                history_errors.append({"symbol": symbol, "error": symbol_error})
            if self._active_request_budget_exhausted:
                break
            if added_bars == 0:
                missing_minute_symbols.append(symbol)

        missing_minute_symbols = list(dict.fromkeys(missing_minute_symbols))
        quote_complete = not capture_quotes or (
            not unavailable_quote_symbols and market_snapshot.get("quality_status") == "ok"
        )
        history_complete = (
            not deferred_symbols
            and not history_errors
            and not missing_minute_symbols
            and not invalid_minute_bars
        )
        complete = quote_complete and history_complete
        warnings = list(market_snapshot.get("quality_warnings", []))
        warnings.extend(
            f"{symbol}：分钟线未准入或没有 cutoff 前的有效 bar。"
            for symbol in missing_minute_symbols
        )
        warnings.extend(f"{item['symbol']}：分钟线请求失败：{item['error']}" for item in history_errors)
        if invalid_minute_bars:
            warnings.append(f"{len(invalid_minute_bars)} 根分钟线因数据校验失败而未标记 ready。")
        if budget_skipped_symbols:
            warnings.append("采集工作预算已用尽；未在截止后继续发分钟线请求。")
        timestamp_policy = build_minute_timestamp_policy(
            minute_timestamp_role,
            minute_timestamp_verification,
            market_timezone=self.market_timezone,
        )
        slot_shard_status = (
            "skipped_budget"
            if budget_skipped_symbols and not minute_bars
            else "complete"
            if complete
            else "partial"
        )
        return {
            "schema_version": "moomoo-intraday-equities-v1",
            "provider": "moomoo_openapi",
            "captured_at": captured_at,
            "trading_date": trading_day.isoformat(),
            "scheduled_at": scheduled_at.isoformat() if scheduled_at is not None else None,
            "evidence_cutoff": cutoff_text,
            "minute_timestamp_role": minute_timestamp_role,
            "minute_timestamp_policy": timestamp_policy,
            "slot_index": slot_index,
            "rotation": slot_index % 3,
            "shard_id": shard_id,
            "shard_symbols": list(shard_symbols or planned_minutes),
            "capture_quotes": capture_quotes,
            "slot_shard_status": slot_shard_status,
            "request_budget": {
                "deadline_at": (
                    self._active_request_deadline_at.isoformat()
                    if self._active_request_deadline_at is not None
                    else None
                ),
                "request_timeout_seconds": self._active_request_timeout_seconds,
                "exhausted": bool(budget_skipped_symbols),
            },
            "requested_symbols": list(planned_quotes),
            "quote_symbols": list(planned_quotes),
            "minute_symbols": list(planned_minutes),
            "group_by_symbol": group_by_symbol,
            "quotes": quotes,
            "unavailable_quote_symbols": unavailable_quote_symbols,
            "minute_bars": minute_bars,
            "invalid_minute_bars": invalid_minute_bars,
            "minute_bar_unavailable_symbols": missing_minute_symbols,
            "history_quota": {
                "available": all(
                    item.get("reason_code") != "quota_unavailable" for item in history_admissions
                ),
                "requested_symbols": list(planned_minutes),
                "admitted_symbols": admitted_symbols,
                "deferred_symbols": deferred_symbols,
                "admissions": history_admissions,
            },
            "quality_summary": {
                "complete": complete,
                "status": "ok" if complete else "partial",
                "quote_coverage": len(quotes) / len(planned_quotes) if planned_quotes else 1.0,
                "minute_symbols_requested": len(planned_minutes),
                "minute_symbols_admitted": len(admitted_symbols),
                "minute_bars_returned": len(minute_bars),
                "invalid_minute_bars": len(invalid_minute_bars),
                "unavailable_quote_symbols": unavailable_quote_symbols,
                "unavailable_minute_symbols": missing_minute_symbols,
                "failed_history_symbols": [item["symbol"] for item in history_errors],
                "skipped_budget_symbols": list(dict.fromkeys(budget_skipped_symbols)),
                "filtered_future_bars": filtered_future_bars,
                "minute_timestamp_role": minute_timestamp_role,
                "minute_timestamp_policy": timestamp_policy,
                "evidence_cutoff": cutoff_text,
                "quality_warnings": warnings,
            },
            "provider_raw": {
                "market_snapshot_batches": market_snapshot.get("request_batches", []),
                "history_kline_batches": history_batches,
            },
        }

    def _collect_market_snapshot(
        self,
        quote_codes: list[str],
        *,
        raise_on_total_failure: bool = True,
    ) -> dict[str, object]:
        rows: list[dict[str, Any]] = []
        request_errors: list[str] = []
        request_batches: list[dict[str, object]] = []
        requested_codes = list(dict.fromkeys(quote_codes))
        missing_codes = [code for code in requested_codes if code not in self._snapshot_cache]
        chunks = [
            missing_codes[index : index + MAX_SNAPSHOT_CODES_PER_REQUEST]
            for index in range(0, len(missing_codes), MAX_SNAPSHOT_CODES_PER_REQUEST)
        ]
        for chunk in chunks:
            request_started_at = datetime.now(UTC)
            try:
                self._pace(
                    "_last_snapshot_request_at",
                    self.snapshot_request_interval_seconds,
                    # OpenD applies the 60/30s ceiling to the combined
                    # snapshot/history request class, not to each adapter
                    # instance. Keep both operations on one durable bucket.
                    rate_class="moomoo_quote_history",
                )
                request_started_at = datetime.now(UTC)
                fetched = _iter_rows(self._call("get_market_snapshot", chunk))
                response_received_at = datetime.now(UTC)
                rows.extend(fetched)
                request_batches.append(
                    {
                        "requested_codes": list(chunk),
                        "request_started_at": request_started_at.isoformat(),
                        "response_received_at": response_received_at.isoformat(),
                        "records": fetched,
                    }
                )
                for row in fetched:
                    code = str(row.get("code", "")).strip().upper()
                    if code:
                        self._snapshot_cache[code] = row
            except _RequestBudgetExceeded as exc:
                request_batches.append(
                    {
                        "requested_codes": list(chunk),
                        "request_started_at": request_started_at.isoformat(),
                        "response_received_at": datetime.now(UTC).isoformat(),
                        "records": [],
                        "skipped_reason": "skipped_budget",
                        "error": str(exc),
                    }
                )
                break
            except Exception as exc:
                error = f"{','.join(chunk)}：{exc}"
                request_errors.append(error)
                request_batches.append(
                    {
                        "requested_codes": list(chunk),
                        "request_started_at": request_started_at.isoformat(),
                        "response_received_at": datetime.now(UTC).isoformat(),
                        "records": [],
                        "error": error,
                    }
                )

        rows = [self._snapshot_cache[code] for code in requested_codes if code in self._snapshot_cache]
        by_code = {
            str(row.get("code", "")).strip().upper(): row
            for row in rows
            if str(row.get("code", "")).strip()
        }
        missing = [code for code in quote_codes if code not in by_code]
        warnings = [
            f"Moomoo 批量快照未返回 {code}；该标的在本次运行中不可用。"
            for code in missing
        ]
        warnings.extend(f"Moomoo 批量快照分组请求失败：{error}" for error in request_errors)
        if not by_code and request_errors and raise_on_total_failure:
            raise RuntimeError(f"OpenD 批量快照请求失败：{request_errors[0]}")
        return {
            "is_mock": False,
            "data_mode": "opend",
            "source": "moomoo_opend_snapshot",
            "requested_symbols": [_display_symbol(code) for code in quote_codes],
            "requested_quote_codes": quote_codes,
            "request_count": len(chunks),
            "request_batches": request_batches,
            "returned_symbols": [_display_symbol(code) for code in by_code],
            "unavailable_symbols": [_display_symbol(code) for code in missing],
            "quality_status": "ok" if not missing and not request_errors else "partial",
            "quality_warnings": warnings,
            "quality_errors": request_errors,
            "rows": by_code,
        }

    def _collect_vix_snapshot(self) -> dict[str, object]:
        return {
            "is_mock": False,
            "available": False,
            "status": "skipped",
            "symbol": "VIX",
            "quote_code": None,
            "source": "not_requested",
            "reason": "按策略不请求 Moomoo 美国指数；使用 Yahoo/FRED 宏观数据。",
        }

    def _history_summary(
        self,
        quote_code: str,
        previous_close: float,
        *,
        include_bars: bool = False,
    ) -> dict[str, object]:
        cached_in_run = self._history_cache.get(quote_code)
        if cached_in_run:
            return _history_summary_from_bars(
                cached_in_run,
                previous_close=previous_close,
                history_days=self.history_days,
                include_bars=include_bars,
            )
        admission_decision: dict[str, object] | None = None
        if self._history_admission is not None:
            admission_decision = self._history_admission.acquire(quote_code)
            if not bool(admission_decision.get("admitted")):
                message = str(admission_decision.get("message") or "历史 K 线额度尚未准入。")
                deferred: dict[str, object] = {
                    "is_mock": False,
                    "available": False,
                    "access_state": str(admission_decision.get("access_state") or "pending_quota"),
                    "requested_days": self.history_days,
                    "returned_days": 0,
                    "latest_completed_bar": None,
                    "returns_percent": {},
                    "moving_average": {},
                    "technical_indicators": calculate_technical_indicators(
                        [],
                        source="moomoo_opend_history",
                    ),
                    "reference_previous_close": previous_close,
                    "warnings": [message],
                    "reason_code": admission_decision.get("reason_code"),
                    "error": message,
                }
                if include_bars:
                    deferred["bars"] = []
                return deferred
            if admission_decision.get("cached"):
                cached_bars = admission_decision.get("bars")
                if isinstance(cached_bars, list):
                    self._history_cache[quote_code] = [
                        dict(item) for item in cached_bars if isinstance(item, dict)
                    ]
                    return _history_summary_from_bars(
                        cached_bars,
                        previous_close=previous_close,
                        history_days=self.history_days,
                        include_bars=include_bars,
                    )
        today = datetime.now(ZoneInfo(self.market_timezone)).date()
        start = today - timedelta(days=max(self.history_days * 2, 365))
        kwargs: dict[str, object] = {
            "code": quote_code,
            "start": start.isoformat(),
            "end": today.isoformat(),
        }
        sdk = self._ensure_sdk()
        if hasattr(sdk, "KLType"):
            kwargs["ktype"] = sdk.KLType.K_DAY
        if hasattr(sdk, "AuType"):
            kwargs["autype"] = sdk.AuType.QFQ

        try:
            self._pace(
                "_last_history_request_at",
                self.history_request_interval_seconds,
                rate_class="moomoo_quote_history",
            )
            rows = _iter_rows(self._call("request_history_kline", **kwargs))
            bars = _normalise_bars(rows)
            if not bars:
                raise RuntimeError("OpenD returned no daily bars")
            self._history_cache[quote_code] = [dict(item) for item in bars]
            return _history_summary_from_bars(
                bars,
                previous_close=previous_close,
                history_days=self.history_days,
                include_bars=include_bars,
            )
        except Exception as exc:
            if self._history_admission is not None:
                try:
                    self._history_admission.mark_failure(quote_code, f"历史日线不可用：{exc}")
                except Exception:
                    logger.warning("failed to persist history admission failure", exc_info=True)
            result = {
                "is_mock": False,
                "available": False,
                "requested_days": self.history_days,
                "returned_days": 0,
                "latest_completed_bar": None,
                "returns_percent": {},
                "moving_average": {},
                "technical_indicators": calculate_technical_indicators(
                    [],
                    source="moomoo_opend_history",
                ),
                "reference_previous_close": previous_close,
                "warnings": [f"历史日线不可用：{exc}"],
                "error": str(exc),
            }
            if include_bars:
                result["bars"] = []
            return result

    def _pace(
        self,
        attribute: str,
        interval_seconds: float,
        *,
        rate_class: str | None = None,
    ) -> None:
        deadline = self._active_request_deadline_at
        timeout = self._active_request_timeout_seconds
        latest_start_at = (
            deadline - timedelta(seconds=timeout)
            if deadline is not None
            else None
        )
        if self._rate_limiter is not None and rate_class:
            waited = self._rate_limiter.acquire_rate_slot(
                rate_class,
                interval_seconds,
                now=datetime.now(UTC),
                sleeper=self._sleeper,
                latest_start_at=latest_start_at,
            )
            if waited is not None and not math.isfinite(float(waited)):
                self._active_request_budget_exhausted = True
                raise _RequestBudgetExceeded("shared pacing would exceed the request budget")
            if deadline is not None and datetime.now(UTC) + timedelta(seconds=timeout) > deadline:
                self._active_request_budget_exhausted = True
                raise _RequestBudgetExceeded("request timeout would exceed the shard budget")
            setattr(self, attribute, self._monotonic_clock())
            return
        if interval_seconds <= 0:
            if deadline is not None and datetime.now(UTC) + timedelta(seconds=timeout) > deadline:
                self._active_request_budget_exhausted = True
                raise _RequestBudgetExceeded("request timeout would exceed the shard budget")
            setattr(self, attribute, self._monotonic_clock())
            return
        now = self._monotonic_clock()
        previous = getattr(self, attribute)
        remaining = 0.0
        if previous is not None:
            remaining = interval_seconds - (now - previous)
            remaining = max(0.0, remaining)
        if deadline is not None and (
            datetime.now(UTC)
            + timedelta(seconds=remaining + timeout)
            > deadline
        ):
            self._active_request_budget_exhausted = True
            raise _RequestBudgetExceeded("pacing and request timeout would exceed the shard budget")
        if remaining > 0:
            self._sleeper(remaining)
        if deadline is not None and datetime.now(UTC) + timedelta(seconds=timeout) > deadline:
            self._active_request_budget_exhausted = True
            raise _RequestBudgetExceeded("request timeout would exceed the shard budget")
        setattr(self, attribute, self._monotonic_clock())

    def _quota_snapshot(self) -> dict[str, object]:
        """Read quota state without subscribing or changing OpenD state."""
        result: dict[str, object] = {"available": False}
        try:
            if hasattr(self._ensure_sdk(), "RET_OK") and hasattr(self._quote_ctx, "query_subscription"):
                try:
                    self._pace(
                        "_last_snapshot_request_at",
                        self.snapshot_request_interval_seconds,
                        rate_class="moomoo_quote_history",
                    )
                    subscription = self._call("query_subscription", is_all_conn=True)
                except TypeError:
                    self._pace(
                        "_last_snapshot_request_at",
                        self.snapshot_request_interval_seconds,
                        rate_class="moomoo_quote_history",
                    )
                    subscription = self._call("query_subscription")
                result["subscription"] = _normalise_subscription_quota(subscription)
                result["available"] = True
            if hasattr(self._quote_ctx, "get_history_kl_quota"):
                self._pace(
                    "_last_snapshot_request_at",
                    self.snapshot_request_interval_seconds,
                    rate_class="moomoo_quote_history",
                )
                history = self._call("get_history_kl_quota", get_detail=True)
                result["history"] = _normalise_history_quota(history)
                result["available"] = True
        except Exception as exc:
            result["warning"] = f"Moomoo 额度查询不可用：{exc}"
        return result

    def _ensure_sdk(self) -> Any:
        if self._sdk is None:
            self._sdk = self._with_sdk_home(_import_moomoo_sdk)
        if self._quote_ctx is None:
            self._probe_endpoint()
            self._quote_ctx = self._with_sdk_home(
                lambda: self._sdk.OpenQuoteContext(host=self.host, port=self.port)
            )
        return self._sdk

    def _probe_endpoint(self) -> None:
        """Fail quickly when OpenD is not listening instead of waiting on SDK retries."""
        try:
            with socket.create_connection(
                (self.host, self.port),
                timeout=self._connect_timeout_seconds,
            ):
                return
        except OSError as exc:
            raise RuntimeError(
                f"无法连接 Moomoo OpenD {self.host}:{self.port}；"
                "请确认 OpenD 已启动且该端口可访问。"
            ) from exc

    def _with_sdk_home(self, callback):
        if self.sdk_home is None:
            return callback()
        self.sdk_home.mkdir(parents=True, exist_ok=True)
        original_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.sdk_home)
        try:
            return callback()
        finally:
            if original_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = original_home

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        sdk = self._ensure_sdk()
        method = getattr(self._quote_ctx, method_name)
        with self._sdk_request_timeout():
            result = method(*args, **kwargs)
        if not isinstance(result, tuple) or len(result) < 2:
            return result
        ret, data = result[0], result[1]
        if ret != getattr(sdk, "RET_OK", 0):
            raise RuntimeError(f"OpenD {method_name} failed: {data}")
        return data

    @contextmanager
    def _request_budget(
        self,
        deadline_at: datetime | None,
        timeout_seconds: float,
    ):
        previous_deadline = self._active_request_deadline_at
        previous_timeout = self._active_request_timeout_seconds
        previous_exhausted = self._active_request_budget_exhausted
        if deadline_at is not None and deadline_at.tzinfo is None:
            deadline_at = deadline_at.replace(tzinfo=ZoneInfo(self.market_timezone))
        self._active_request_deadline_at = (
            deadline_at.astimezone(UTC) if deadline_at is not None else None
        )
        self._active_request_timeout_seconds = timeout_seconds
        self._active_request_budget_exhausted = False
        try:
            yield
        finally:
            self._active_request_deadline_at = previous_deadline
            self._active_request_timeout_seconds = previous_timeout
            self._active_request_budget_exhausted = previous_exhausted

    @contextmanager
    def _sdk_request_timeout(self):
        context = self._quote_ctx
        if context is None or self._active_request_deadline_at is None:
            yield
            return
        previous: dict[str, object] = {}
        for attribute in ("_query_timeout", "_sync_query_connect_timeout"):
            if hasattr(context, attribute):
                previous[attribute] = getattr(context, attribute)
                setattr(context, attribute, self._active_request_timeout_seconds)
        try:
            yield
        finally:
            for attribute, value in previous.items():
                setattr(context, attribute, value)


def _normalise_quote_code(symbol: str | None) -> str:
    value = str(symbol or "").strip().upper()
    if not value:
        raise ValueError("行情代码不能为空")
    return value if "." in value else f"US.{value}"


def _unique_quote_codes(symbols: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    for symbol in symbols:
        code = _normalise_quote_code(symbol)
        if code not in result:
            result.append(code)
    return result


def _display_symbol(quote_code: str) -> str:
    return quote_code.split(".")[-1].upper()


def _instrument_theme(symbol: str) -> str:
    return _instrument_themes(symbol)[0]


def _instrument_themes(symbol: str) -> list[str]:
    return list(INSTRUMENT_THEMES_BY_SYMBOL.get(symbol.upper(), ["未分类"]))


def _optional_number(row: dict[str, Any], keys: list[str]) -> float | None:
    try:
        return _number(row, keys)
    except (KeyError, TypeError, ValueError):
        return None


def _optional_integer(row: dict[str, Any], keys: list[str]) -> int | None:
    value = _optional_number(row, keys)
    return int(value) if value is not None else None


def _normalise_snapshot_quote(row: dict[str, Any]) -> dict[str, object]:
    quote_code = str(row.get("code", "")).strip().upper() or None
    last_price = _optional_number(row, ["last_price", "cur_price", "price"])
    previous_close = _optional_number(
        row,
        ["prev_close_price", "pre_close_price", "previous_close", "prev_close"],
    )
    premarket_price = _positive_or_none(
        _optional_number(row, ["pre_price", "premarket_price"]) or 0
    )
    afterhours_price = _positive_or_none(
        _optional_number(row, ["after_price", "afterhours_price"]) or 0
    )
    return {
        "is_mock": False,
        "data_mode": "opend",
        "source": "moomoo_opend_snapshot",
        "quote_code": quote_code,
        "symbol": _display_symbol(quote_code or ""),
        "label": str(row.get("name") or row.get("stock_name") or quote_code or "未知标的"),
        "last_price": last_price,
        "regular_price": last_price,
        "change_percent": _percent(last_price, previous_close),
        "previous_close": previous_close,
        "open_price": _optional_number(row, ["open_price", "open"]),
        "high_price": _optional_number(row, ["high_price", "high"]),
        "low_price": _optional_number(row, ["low_price", "low"]),
        "volume": _optional_integer(row, ["volume"]),
        "turnover": _optional_number(row, ["turnover"]),
        "turnover_rate": _optional_number(row, ["turnover_rate"]),
        "bid_price": _optional_number(row, ["bid_price", "bid"]),
        "ask_price": _optional_number(row, ["ask_price", "ask"]),
        "bid_volume": _optional_integer(row, ["bid_vol", "bid_volume"]),
        "ask_volume": _optional_integer(row, ["ask_vol", "ask_volume"]),
        "price_spread": _optional_number(row, ["price_spread", "spread"]),
        "quote_time": _quote_time(row),
        "premarket_price": premarket_price,
        "premarket_volume": _optional_integer(row, ["pre_volume", "premarket_volume"]),
        "premarket_change_percent": _percent(premarket_price, previous_close),
        "afterhours_price": afterhours_price,
        "afterhours_volume": _optional_integer(row, ["after_volume", "afterhours_volume"]),
        "afterhours_change_percent": _percent(afterhours_price, previous_close),
    }


def _import_moomoo_sdk() -> Any:
    try:
        import moomoo as sdk  # type: ignore

        return sdk
    except ModuleNotFoundError:
        try:
            import futu as sdk  # type: ignore

            return sdk
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Neither moomoo nor futu Python SDK is installed. "
                "Run uv sync before enabling MOOMOO_ENABLED."
            ) from exc


def _iter_rows(data: Any) -> list[dict[str, Any]]:
    if hasattr(data, "to_dict"):
        return [dict(item) for item in data.to_dict("records")]
    if isinstance(data, dict):
        return [dict(data)]
    if isinstance(data, (list, tuple)):
        return [dict(item) for item in data]
    raise TypeError(f"Unsupported OpenD response type: {type(data).__name__}")


def _first_row(data: Any, label: str) -> dict[str, Any]:
    rows = _iter_rows(data)
    if not rows:
        raise RuntimeError(f"OpenD returned no rows for {label}")
    return rows[0]


def _value(row: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    for key in keys:
        if key not in row:
            continue
        value = row[key]
        if value is None:
            continue
        try:
            if isinstance(value, float) and math.isnan(value):
                continue
        except TypeError:
            pass
        if value != "":
            return value
    if default is not None:
        return default
    raise KeyError(f"None of {keys} found in OpenD row with columns {sorted(row)}")


def _number(row: dict[str, Any], keys: list[str], default: float | None = None) -> float:
    value = _value(row, keys, default=default)
    if value is None or str(value).strip() in {"", "N/A", "--", "nan"}:
        if default is not None:
            return default
        raise ValueError(f"Missing numeric value for {keys}")
    return float(value)


def _integer(row: dict[str, Any], keys: list[str], default: int = 0) -> int:
    return int(_number(row, keys, default=float(default)))


def _positive_or_none(value: float) -> float | None:
    return value if value > 0 else None


def _quote_time(row: dict[str, Any]) -> str | None:
    update = str(_value(row, ["update_time"], default="")).strip()
    if update:
        return update
    parts = [
        str(_value(row, ["data_date"], default="")).strip(),
        str(_value(row, ["data_time"], default="")).strip(),
    ]
    value = " ".join(part for part in parts if part)
    return value or None


def _normalise_bars(rows: list[dict[str, Any]]) -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    for row in rows:
        bars.append(
            {
                "date": _date(_value(row, ["time_key", "date", "time"])).isoformat(),
                "open": _number(row, ["open"]),
                "high": _number(row, ["high"]),
                "low": _number(row, ["low"]),
                "close": _number(row, ["close"]),
                "volume": _optional_integer(row, ["volume"]),
                "turnover": _optional_number(row, ["turnover"]),
                "turnover_rate": _optional_number(row, ["turnover_rate"]),
            }
        )
    return sorted(bars, key=lambda item: str(item["date"]))


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _capital_flow_date(row: object) -> date | None:
    try:
        value = _value(row, ["capital_flow_item_time", "time"])
    except KeyError:
        return None
    if value in {None, ""}:
        return None
    try:
        return _date(value)
    except (TypeError, ValueError):
        return None


def _average(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return round(sum(values[-window:]) / window, 4)


def _lookback_return(values: list[float], periods: int) -> float | None:
    if len(values) <= periods or values[-periods - 1] == 0:
        return None
    return _percent(values[-1], values[-periods - 1])


def _history_indicators_ok(history: dict[str, object]) -> bool:
    indicators = history.get("technical_indicators")
    return isinstance(indicators, dict) and indicators.get("quality_status") == "ok"


def _history_summary_from_bars(
    bars: list[dict[str, object]],
    *,
    previous_close: float,
    history_days: int,
    include_bars: bool,
) -> dict[str, object]:
    """Build the adapter history projection from fetched or canonical bars."""

    closes = [float(bar["close"]) for bar in bars if isinstance(bar.get("close"), (int, float))]
    if not bars or not closes:
        raise RuntimeError("OpenD returned no daily bars")
    warnings: list[str] = []
    if len(bars) < history_days:
        warnings.append(f"OpenD 只返回 {len(bars)} 根日线，低于请求的 {history_days} 根。")
    latest = bars[-1]
    technical_indicators = calculate_technical_indicators(
        bars,
        source="moomoo_opend_history",
    )
    warnings.extend(str(item) for item in technical_indicators.get("warnings", []))
    technical_returns = technical_indicators.get("returns_percent")
    returns_percent = (
        dict(technical_returns)
        if isinstance(technical_returns, dict)
        else {
            "1d": _lookback_return(closes, 1),
            "5d": _lookback_return(closes, 5),
            "20d": _lookback_return(closes, 20),
        }
    )
    technical_moving_average = technical_indicators.get("moving_average")
    moving_average = (
        dict(technical_moving_average)
        if isinstance(technical_moving_average, dict)
        else {
            "20d": _average(closes, 20),
            "50d": _average(closes, 50),
            "200d": _average(closes, 200),
        }
    )
    result: dict[str, object] = {
        "is_mock": False,
        "available": True,
        "requested_days": history_days,
        "returned_days": len(bars),
        "latest_completed_bar": latest,
        "returns_percent": returns_percent,
        "moving_average": moving_average,
        "technical_indicators": technical_indicators,
        "reference_previous_close": previous_close,
        "warnings": warnings,
    }
    if include_bars:
        result["bars"] = bars
    return result


def _trend_from_history(history: dict[str, object]) -> str:
    indicators = history.get("technical_indicators")
    if not isinstance(indicators, dict):
        return "不可用"
    current = _optional_float(indicators.get("moving_average"), "20d")
    latest = history.get("latest_completed_bar")
    latest_close = latest.get("close") if isinstance(latest, dict) else None
    if current is None or not isinstance(latest_close, (int, float)):
        return "不可用"
    return "高于 MA20" if float(latest_close) >= current else "低于 MA20"


def _optional_float(value: object, key: str) -> float | None:
    if not isinstance(value, dict):
        return None
    candidate = value.get(key)
    return float(candidate) if isinstance(candidate, (int, float)) else None


def _normalise_subscription_quota(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"raw": value}
    result: dict[str, object] = {
        "total_used": value.get("total_used", value.get("totalUsed")),
        "own_used": value.get("own_used", value.get("ownUsed")),
        "remain": value.get("remain", value.get("remainQuota")),
        "sub_list": value.get("sub_list", value.get("subList", {})),
    }
    return result


def _normalise_history_quota(value: object) -> dict[str, object]:
    if isinstance(value, tuple) and len(value) >= 2:
        used, remain = value[0], value[1]
        details = value[2] if len(value) > 2 else []
        return {"used_quota": used, "remain_quota": remain, "detail_list": details}
    if isinstance(value, dict):
        return {
            "used_quota": value.get("used_quota", value.get("usedQuota")),
            "remain_quota": value.get("remain_quota", value.get("remainQuota")),
            "detail_list": value.get("detail_list", value.get("detailList", [])),
        }
    return {"raw": value}


def _quota_audit(
    before: dict[str, object],
    after: dict[str, object],
    *,
    requested_symbols: list[str],
) -> dict[str, object]:
    warnings: list[str] = []
    before_subscription = before.get("subscription")
    after_subscription = after.get("subscription")
    subscription_unchanged = before_subscription == after_subscription
    if before.get("warning"):
        warnings.append(str(before["warning"]))
    if after.get("warning"):
        warnings.append(str(after["warning"]))
    if before_subscription is not None and after_subscription is not None and not subscription_unchanged:
        warnings.append("采集前后股票实时订阅状态发生变化，请检查是否有其他连接修改订阅。")
    before_history = before.get("history")
    after_history = after.get("history")
    history_delta = None
    if isinstance(before_history, dict) and isinstance(after_history, dict):
        before_used = before_history.get("used_quota")
        after_used = after_history.get("used_quota")
        if isinstance(before_used, int) and isinstance(after_used, int):
            history_delta = after_used - before_used
    return {
        "subscription_before": before_subscription,
        "subscription_after": after_subscription,
        "subscription_unchanged": subscription_unchanged,
        "history_before": before_history,
        "history_after": after_history,
        "history_used_delta": history_delta,
        "requested_symbols": requested_symbols,
        "warnings": warnings,
        "quality_status": "ok" if not warnings else "partial",
    }


def _percent(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in {None, 0}:
        return None
    return round((current - previous) / previous * 100, 4)


def _classify_session(now: datetime) -> tuple[str, str]:
    if now.weekday() >= 5:
        return "closed", "休市"
    current = now.time()
    if time(4, 0) <= current < time(9, 30):
        return "premarket", "盘前"
    if time(9, 30) <= current < time(16, 0):
        return "regular", "正常交易时段"
    if time(16, 0) <= current < time(20, 0):
        return "afterhours", "盘后"
    return "overnight", "隔夜/休市时段"


def _session_price(
    session: str,
    *,
    last_price: float,
    premarket_price: float | None,
    afterhours_price: float | None,
) -> tuple[float, str]:
    if session == "premarket" and premarket_price is not None:
        return premarket_price, "premarket_price"
    if session == "afterhours" and afterhours_price is not None:
        return afterhours_price, "afterhours_price"
    return last_price, "last_price"
