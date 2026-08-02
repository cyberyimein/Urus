from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class MarketCollectorAdapter(Protocol):
    def market_card(self, symbol: str) -> dict[str, object]: ...


class MoomooAdapter(Protocol):
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
    """Deterministic offline adapter used by the framework baseline."""

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
        if symbol != "INTC":
            raise ValueError(f"mock instrument adapter only supports INTC, got {symbol}")
        return MockQuote(
            symbol="INTC",
            label="INTC · mock instrument",
            last_price=31.25,
            change_percent=-0.18,
            trend="mock trend unavailable",
            technical_note="技术指标尚未实现；当前仅展示占位字段。",
        ).as_dict()

    def options_placeholder(self, symbol: str) -> dict[str, object]:
        return {
            "is_mock": True,
            "status": "not_implemented",
            "available": False,
            "note": f"{symbol} 的 IV/GEX/期权链尚未实现；此处不是实时期权数据。",
        }


class DisabledMoomooAdapter(MockMoomooAdapter):
    """Named disabled implementation for dependency injection."""
