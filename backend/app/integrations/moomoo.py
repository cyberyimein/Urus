from __future__ import annotations

import math
import os
import logging
import socket
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.analytics.technical import calculate_relative_strength, calculate_technical_indicators

logger = logging.getLogger(__name__)


DEFAULT_MARKET_SNAPSHOT_SYMBOLS = (
    "QQQ",
    "SPY",
    "IWM",
    "DIA",
    "RSP",
    "SMH",
    "SOXX",
    "IGV",
    "HYG",
    "LQD",
    "TLT",
    "IEF",
    "UUP",
    "GLD",
    "USO",
)
MAX_SNAPSHOT_CODES_PER_REQUEST = 400


class MarketCollectorAdapter(Protocol):
    def market_card(self, symbol: str) -> dict[str, object]: ...


class MoomooAdapter(Protocol):
    """Future full Moomoo boundary; stage 1A only uses market_card."""

    def market_card(self, symbol: str) -> dict[str, object]: ...

    def instrument_card(self, symbol: str) -> dict[str, object]: ...

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
    """Deterministic offline adapter used when live stage 1A is disabled."""

    def market_card(self, symbol: str) -> dict[str, object]:
        if symbol != "QQQ":
            raise ValueError(f"mock market adapter only supports QQQ, got {symbol}")
        return MockQuote(
            symbol="QQQ",
            label="QQQ · mock market proxy",
            last_price=481.26,
            change_percent=0.42,
            trend="mock trend unavailable",
            session_note="framework sample session",
        ).as_dict()

    def instrument_card(self, symbol: str) -> dict[str, object]:
        quotes = {
            "INTC": MockQuote(
                symbol="INTC",
                label="INTC · mock instrument",
                last_price=31.25,
                change_percent=-0.18,
                trend="mock trend unavailable",
                technical_note="技术指标尚未实现；当前仅展示占位字段。",
            ),
            "SMH": MockQuote(
                symbol="SMH",
                label="SMH · mock ETF",
                last_price=250.0,
                change_percent=0.25,
                trend="mock trend unavailable",
                technical_note="技术指标尚未实现；当前仅展示占位字段。",
            ),
        }
        if symbol not in quotes:
            raise ValueError(f"mock instrument adapter only supports INTC and SMH, got {symbol}")
        return quotes[symbol].as_dict()

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
            "quality_warnings": ["3A 尚未启用 Moomoo OpenD，当前为 mock 结构。"],
            "note": "3A 真实行情尚未启用。",
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
    """OpenD adapter for the twice-daily stage 1A market collection.

    The SDK and TCP context are lazy so importing Urus and running tests never
    contacts OpenD.  The response is converted to plain JSON-compatible data;
    no Futu/moomoo SDK objects cross into workflows or schemas.

    The primary market call is a single batched snapshot for the configured ETF
    universe.  US index requests such as direct VIX are intentionally not sent
    to OpenD; the daily macro adapter owns Yahoo/FRED index context.
    """

    _connect_timeout_seconds = 3.0

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

        thread = threading.Thread(target=close_context, name="urus-opend-close", daemon=True)
        thread.start()
        if not finished.wait(timeout=2.0):
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

        history = self._history_summary(quote_code, previous_close)
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
            "session_note": f"{session_label}；报价来源为 OpenD，不调用旧 Anomalo 服务。",
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
        persistence = payload.get("_persistence")
        if isinstance(persistence, dict):
            card["_persistence"] = persistence
        return card

    def instrument_cards(self, symbols: list[str]) -> dict[str, object]:
        """Collect a small 3A universe in one quote snapshot and daily histories."""
        if not symbols:
            raise ValueError("3A 至少需要一个个股或 ETF 标的")
        quote_codes = _unique_quote_codes(["QQQ", *symbols])
        captured_at = datetime.now(UTC).isoformat()
        quota_before = self._quota_snapshot()
        market_snapshot = self._collect_market_snapshot(quote_codes)
        cards: list[dict[str, object]] = []
        persistence_symbols: list[dict[str, object]] = []
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

        benchmark_bars = records.get("QQQ", ({}, {}, []))[2]
        for display_symbol, (quote, history_public, bars) in records.items():
            if display_symbol == "QQQ":
                relative_strength = {
                    "is_mock": False,
                    "available": False,
                    "quality_status": "not_applicable",
                    "benchmark": "QQQ",
                    "source": "moomoo_opend_history",
                    "as_of": history_public.get("technical_indicators", {}).get("as_of")
                    if isinstance(history_public.get("technical_indicators"), dict)
                    else None,
                    "sample_count": 0,
                    "warnings": ["QQQ 是相对强弱基准，不对自身计算超额收益。"],
                }
            else:
                relative_strength = calculate_relative_strength(
                    bars,
                    benchmark_bars,
                    benchmark="QQQ",
                    source="moomoo_opend_history",
                )
            card_warnings = list(history_public.get("warnings", []))
            if relative_strength.get("warnings"):
                card_warnings.extend(str(item) for item in relative_strength["warnings"])
            card = {
                **quote,
                "is_mock": False,
                "data_mode": "opend",
                "data_state": "live",
                "source": "moomoo_opend_snapshot",
                "label": f"{display_symbol} · Moomoo OpenD",
                "trend": _trend_from_history(history_public),
                "technical_note": "日线技术指标由 SQLite 可重算的 Moomoo 历史 K 线生成。",
                "history": history_public,
                "relative_strength": relative_strength,
                "quality_status": card_quality,
                "quality_warnings": card_warnings,
                "note": "3A 当前只采集价格、成交、日线技术指标与相对基准输入；财务和事件属于后续步骤。",
            }
            cards.append(card)
            persistence_symbols.append(
                {
                    "symbol": display_symbol,
                    "asset_type": "etf" if display_symbol in DEFAULT_MARKET_SNAPSHOT_SYMBOLS else "equity",
                    "quote": quote,
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
            quality_warnings.extend(f"3A 未返回标的：{symbol}" for symbol in unavailable_symbols)
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
            "quota_audit": quota_audit,
            "quality_status": "ok" if cards and not unavailable_symbols and all(
                card.get("quality_status") == "ok" for card in cards
            ) else "partial" if cards else "unavailable",
            "quality_warnings": quality_warnings,
            "note": "3A 使用 Moomoo OpenD 批量快照和历史日线；未开启实时订阅。",
            "_persistence": {
                "captured_at": captured_at,
                "provider": "moomoo_openapi",
                "source_mode": "snapshot",
                "symbols": persistence_symbols,
            },
        }

    def _collect_market_snapshot(self, quote_codes: list[str]) -> dict[str, object]:
        rows: list[dict[str, Any]] = []
        request_errors: list[str] = []
        chunks = [
            quote_codes[index : index + MAX_SNAPSHOT_CODES_PER_REQUEST]
            for index in range(0, len(quote_codes), MAX_SNAPSHOT_CODES_PER_REQUEST)
        ]
        for chunk in chunks:
            try:
                rows.extend(_iter_rows(self._call("get_market_snapshot", chunk)))
            except Exception as exc:
                request_errors.append(f"{','.join(chunk)}：{exc}")

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
        if not by_code and request_errors:
            raise RuntimeError(f"OpenD 批量快照请求失败：{request_errors[0]}")
        return {
            "is_mock": False,
            "data_mode": "opend",
            "source": "moomoo_opend_snapshot",
            "requested_symbols": [_display_symbol(code) for code in quote_codes],
            "requested_quote_codes": quote_codes,
            "request_count": len(chunks),
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
            rows = _iter_rows(self._call("request_history_kline", **kwargs))
            bars = _normalise_bars(rows)
            if not bars:
                raise RuntimeError("OpenD returned no daily bars")
            closes = [bar["close"] for bar in bars]
            warnings: list[str] = []
            if len(bars) < self.history_days:
                warnings.append(
                    f"OpenD 只返回 {len(bars)} 根日线，低于请求的 {self.history_days} 根。"
                )
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
            result = {
                "is_mock": False,
                "available": True,
                "requested_days": self.history_days,
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
        except Exception as exc:
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

    def _quota_snapshot(self) -> dict[str, object]:
        """Read quota state without subscribing or changing OpenD state."""
        result: dict[str, object] = {"available": False}
        try:
            if hasattr(self._ensure_sdk(), "RET_OK") and hasattr(self._quote_ctx, "query_subscription"):
                try:
                    subscription = self._call("query_subscription", is_all_conn=True)
                except TypeError:
                    subscription = self._call("query_subscription")
                result["subscription"] = _normalise_subscription_quota(subscription)
                result["available"] = True
            if hasattr(self._quote_ctx, "get_history_kl_quota"):
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
        result = method(*args, **kwargs)
        if not isinstance(result, tuple) or len(result) < 2:
            return result
        ret, data = result[0], result[1]
        if ret != getattr(sdk, "RET_OK", 0):
            raise RuntimeError(f"OpenD {method_name} failed: {data}")
        return data


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
                "volume": _integer(row, ["volume"], default=0),
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
        warnings.append("3A 采集前后股票实时订阅状态发生变化，请检查是否有其他连接修改订阅。")
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
