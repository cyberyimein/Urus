from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any, Protocol

from app.decision_harness.contracts import content_sha256, normalise_symbol
from app.repositories.daily_evidence import DailyEvidenceRepository

from .contracts import (
    EvidenceBar,
    EvidenceReference,
    FrozenEvidence,
    SOURCE_MODES,
)


class EvidenceUnavailable(RuntimeError):
    """Raised when a symbol has no authorized frozen evidence."""


class DailyEvidenceReader(Protocol):
    def load(self, symbol: str, *, knowledge_at: datetime) -> FrozenEvidence:
        """Return only evidence visible at ``knowledge_at``."""


def _bar_from_mapping(
    raw: Mapping[str, Any],
    *,
    symbol: str,
    knowledge_at: datetime,
    default_source: str,
    default_adjustment: str,
    require_available_at: bool,
) -> EvidenceBar:
    source = str(raw.get("source") or default_source).strip()
    source_revision = str(raw.get("source_revision") or content_sha256(dict(raw)))
    raw_available_at = raw.get("available_at")
    if require_available_at and raw_available_at is None:
        raise ValueError("observed_pit 证据必须提供真实 available_at")
    collected_at = raw.get("collected_at") or raw_available_at or knowledge_at
    available_at = raw_available_at or collected_at
    return EvidenceBar(
        date=raw.get("date"),
        open=raw.get("open"),
        high=raw.get("high"),
        low=raw.get("low"),
        close=raw.get("close"),
        volume=raw.get("volume", 0),
        turnover=raw.get("turnover"),
        turnover_rate=raw.get("turnover_rate"),
        adjustment=str(raw.get("adjustment") or default_adjustment).strip().upper(),
        available_at=available_at,
        collected_at=collected_at,
        source=source,
        source_revision=source_revision,
        session_id=raw.get("session_id"),
        session_open_at=raw.get("session_open_at"),
        session_close_at=raw.get("session_close_at"),
    )


def freeze_daily_evidence(
    *,
    symbol: str,
    asset_type: str,
    bars: Iterable[EvidenceBar | Mapping[str, Any]],
    knowledge_at: datetime,
    source_mode: str,
    price_basis: str,
    benchmark: str | None = None,
    price_mapping_id: str | None = None,
    data_source: str = "unknown",
    trading_date=None,
    session_dates: Sequence[date] | None = None,
    default_source: str = "frozen_daily_bars",
    default_adjustment: str = "QFQ",
) -> FrozenEvidence:
    """Build a point-in-time evidence package without performing I/O."""

    normalized_symbol = normalise_symbol(symbol)
    if asset_type not in {"equity", "etf"}:
        raise ValueError(f"不支持的 opportunity asset_type：{asset_type}")
    if source_mode not in SOURCE_MODES:
        raise ValueError(f"不支持的 source_mode：{source_mode}")
    if price_basis not in {"raw", "adjusted"}:
        raise ValueError(f"不支持的 price_basis：{price_basis}")
    if data_source not in {"real", "synthetic", "unknown"}:
        raise ValueError(f"不支持的 data_source：{data_source}")

    parsed = [
        item
        if isinstance(item, EvidenceBar)
        else _bar_from_mapping(
            item,
            symbol=normalized_symbol,
            knowledge_at=knowledge_at,
            default_source=default_source,
            default_adjustment=default_adjustment,
            require_available_at=source_mode == "observed_pit",
        )
        for item in bars
    ]
    if knowledge_at.tzinfo is None or knowledge_at.utcoffset() is None:
        raise ValueError("knowledge_at 必须包含时区")
    known_sessions = set(session_dates) if session_dates is not None else None
    visible: list[EvidenceBar] = []
    date_hidden_count = 0
    calendar_hidden_count = 0
    for item in parsed:
        if item.available_at > knowledge_at or item.date > knowledge_at.date():
            date_hidden_count += 1
            continue
        if known_sessions is not None and item.date not in known_sessions:
            calendar_hidden_count += 1
            continue
        if item.session_close_at is not None and item.session_close_at > knowledge_at:
            date_hidden_count += 1
            continue
        visible.append(item)
    hidden_count = len(parsed) - len(visible)
    selected_by_date: dict[Any, EvidenceBar] = {}
    for item in visible:
        current = selected_by_date.get(item.date)
        if current is None or (
            item.available_at,
            item.collected_at,
            item.source_revision,
            item.content_sha256,
        ) > (
            current.available_at,
            current.collected_at,
            current.source_revision,
            current.content_sha256,
        ):
            selected_by_date[item.date] = item
    visible = [selected_by_date[day] for day in sorted(selected_by_date)]

    warnings = []
    if hidden_count:
        warnings.append(f"{hidden_count} 根日线在 knowledge_at 之后才可见，已排除。")
    if calendar_hidden_count:
        warnings.append(f"{calendar_hidden_count} 根日线不属于冻结交易日历，已排除。")
    quality_status = "ok" if visible else "unavailable"
    manifest = [
        EvidenceReference(
            symbol=normalized_symbol,
            bar_date=item.date,
            source=item.source,
            source_revision=item.source_revision,
            available_at=item.available_at,
            content_sha256=item.content_sha256,
            session_id=item.session_id,
        )
        for item in visible
    ]
    return FrozenEvidence(
        symbol=normalized_symbol,
        asset_type=asset_type,
        benchmark=benchmark,
        source_mode=source_mode,
        data_source=data_source,
        price_basis=price_basis,
        price_mapping_id=price_mapping_id,
        knowledge_at=knowledge_at,
        trading_date=trading_date,
        quality_status=quality_status,
        warnings=warnings,
        bars=visible,
        source_manifest=manifest,
    )


class InMemoryDailyEvidenceReader:
    """Deterministic reader used by replay jobs and unit tests."""

    def __init__(self, evidence: Mapping[str, FrozenEvidence]):
        self._evidence = {
            normalise_symbol(symbol): value
            for symbol, value in evidence.items()
        }

    def load(self, symbol: str, *, knowledge_at: datetime) -> FrozenEvidence:
        normalized = normalise_symbol(symbol)
        source = self._evidence.get(normalized)
        if source is None:
            raise EvidenceUnavailable(f"{normalized} 没有授权的冻结日线证据")
        frozen = freeze_daily_evidence(
            symbol=source.symbol,
            asset_type=source.asset_type,
            bars=source.bars,
            knowledge_at=knowledge_at,
            source_mode=source.source_mode,
            price_basis=source.price_basis,
            benchmark=source.benchmark,
            price_mapping_id=source.price_mapping_id,
            data_source=source.data_source,
            trading_date=source.trading_date,
            default_source=source.bars[0].source if source.bars else "frozen_daily_bars",
        )
        # Re-freezing applies the requested knowledge cutoff, but it must not
        # erase quality decisions or provenance warnings already attached to
        # the immutable source package.  In particular, a partial/conflicted
        # package with visible bars must remain partial after replay.
        severity = {"ok": 0, "partial": 1, "unavailable": 2, "failed": 3}
        source_quality = source.quality_status
        effective_quality = (
            source_quality
            if severity[source_quality] >= severity[frozen.quality_status]
            else frozen.quality_status
        )
        warnings = list(dict.fromkeys([*source.warnings, *frozen.warnings]))
        return frozen.model_copy(
            update={"quality_status": effective_quality, "warnings": warnings}
        )


class RepositoryDailyEvidenceReader:
    """Read canonical daily bars without mutating the evidence store."""

    def __init__(
        self,
        session,
        *,
        asset_types: Mapping[str, str],
        source_mode: str = "historical_reconstruction",
        price_basis: str = "adjusted",
        adjustment: str = "QFQ",
        source: str | None = None,
    ):
        self.repository = DailyEvidenceRepository(session)
        self.asset_types = {
            normalise_symbol(symbol): str(asset_type)
            for symbol, asset_type in asset_types.items()
        }
        self.source_mode = source_mode
        self.price_basis = price_basis
        self.adjustment = adjustment
        self.source = source

    def load(self, symbol: str, *, knowledge_at: datetime) -> FrozenEvidence:
        normalized = normalise_symbol(symbol)
        asset_type = self.asset_types.get(normalized)
        if asset_type not in {"equity", "etf"}:
            raise EvidenceUnavailable(f"{normalized} 没有机会引擎允许的资产类型")
        rows = self.repository.bars(
            [normalized],
            cutoff_time=knowledge_at,
            source=self.source,
            adjustment=self.adjustment,
        ).get(normalized, [])
        if not rows:
            raise EvidenceUnavailable(f"{normalized} 在 knowledge_at 前没有 canonical 日线")
        bars = [
            {
                "date": row.bar_date,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "turnover": row.turnover,
                "turnover_rate": row.turnover_rate,
                "adjustment": row.adjustment,
                "available_at": row.collected_at,
                "collected_at": row.collected_at,
                "source": row.source,
                "source_revision": row.source_revision,
            }
            for row in rows
        ]
        return freeze_daily_evidence(
            symbol=normalized,
            asset_type=asset_type,
            bars=bars,
            knowledge_at=knowledge_at,
            source_mode=self.source_mode,
            price_basis=self.price_basis,
            trading_date=rows[-1].bar_date,
            default_source=rows[0].source,
            default_adjustment=rows[0].adjustment,
        )


def evidence_input_hash(evidence: FrozenEvidence) -> str:
    return content_sha256(
        {
            "symbol": evidence.symbol,
            "knowledge_at": evidence.knowledge_at.isoformat(),
            "price_basis": evidence.price_basis,
            "price_mapping_id": evidence.price_mapping_id,
            "source_mode": evidence.source_mode,
            "data_source": evidence.data_source,
            "bars": [bar.model_dump(mode="json") for bar in evidence.bars],
        }
    )
