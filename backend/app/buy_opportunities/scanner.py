from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from statistics import median
from typing import Any

from app.decision_harness.contracts import content_sha256, normalise_symbol

from .breakout import build_s1_setup
from .contracts import ScanRecord, SetupRecord
from .evidence import DailyEvidenceReader, EvidenceUnavailable, FrozenEvidence


MINIMUM_HISTORY_BARS = 61
MINIMUM_CLOSE = 5.0
MINIMUM_MEDIAN_DOLLAR_VOLUME = 20_000_000.0


def _item_value(item: object, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _item_bool(item: object, key: str, default: bool = False) -> bool:
    value = _item_value(item, key, default)
    return bool(value)


def _scan_hash(payload: dict[str, Any]) -> str:
    return content_sha256({key: value for key, value in payload.items() if key != "content_sha256"})


def _new_scan(
    *,
    pool_version: str,
    knowledge_at: datetime,
    symbol: str,
    asset_type: str,
    source_mode: str,
    price_basis: str,
    adjustment: str,
    status: str,
    bar_count: int,
    covered_through,
    source_manifest,
    eligibility_by_dimension: dict[str, bool | str | None],
    features: dict[str, Any],
    rejection_codes: list[str],
    setup_id: str | None,
    scan_id: str | None = None,
) -> ScanRecord:
    payload: dict[str, Any] = {
        "pool_version": pool_version,
        "knowledge_at": knowledge_at.isoformat(),
        "symbol": symbol,
        "asset_type": asset_type,
        "source_mode": source_mode,
        "price_basis": price_basis,
        "adjustment": adjustment,
        "status": status,
        "bar_count": bar_count,
        "covered_through": covered_through.isoformat() if covered_through else None,
        "source_manifest": [item.model_dump(mode="json") for item in source_manifest],
        "eligibility_by_dimension": eligibility_by_dimension,
        "features": features,
        "rejection_codes": rejection_codes,
        "setup_id": setup_id,
    }
    digest = _scan_hash(payload)
    return ScanRecord(
        scan_id=scan_id or f"{symbol}:scan:{digest[:20]}",
        content_sha256=digest,
        **payload,
    )


def scan_universe(
    *,
    universe_items: Iterable[object],
    evidence_reader: DailyEvidenceReader,
    pool_version: str,
    knowledge_at: datetime,
    source_mode: str = "historical_reconstruction",
    include_source_manifest: bool = True,
    min_history_bars: int = MINIMUM_HISTORY_BARS,
    min_close: float = MINIMUM_CLOSE,
    min_median_dollar_volume: float = MINIMUM_MEDIAN_DOLLAR_VOLUME,
) -> tuple[list[ScanRecord], list[SetupRecord]]:
    """Scan every configured Universe item and retain every rejection reason."""

    scans: list[ScanRecord] = []
    setups: list[SetupRecord] = []
    for item in universe_items:
        raw_symbol = _item_value(item, "symbol")
        symbol = normalise_symbol(str(raw_symbol or ""))
        asset_type = str(_item_value(item, "asset_type", "equity"))
        enabled = _item_bool(item, "enabled", True)
        collection = _item_value(item, "collection", None)
        daily_history = (
            _item_bool(collection, "daily_history", True)
            if collection is not None
            else _item_bool(item, "daily_history", True)
        )
        if not enabled:
            scans.append(
                _new_scan(
                    pool_version=pool_version,
                    knowledge_at=knowledge_at,
                    symbol=symbol,
                    asset_type=asset_type,
                    source_mode=source_mode,
                    price_basis="adjusted",
                    adjustment="QFQ",
                    status="rejected",
                    bar_count=0,
                    covered_through=None,
                    source_manifest=[],
                    eligibility_by_dimension={"enabled": False},
                    features={},
                    rejection_codes=["not_enabled"],
                    setup_id=None,
                )
            )
            continue
        if not daily_history:
            scans.append(
                _new_scan(
                    pool_version=pool_version,
                    knowledge_at=knowledge_at,
                    symbol=symbol,
                    asset_type=asset_type,
                    source_mode=source_mode,
                    price_basis="adjusted",
                    adjustment="QFQ",
                    status="coverage_miss",
                    bar_count=0,
                    covered_through=None,
                    source_manifest=[],
                    eligibility_by_dimension={"enabled": True, "daily_history": False},
                    features={},
                    rejection_codes=["coverage_miss"],
                    setup_id=None,
                )
            )
            continue
        if asset_type not in {"equity", "etf"}:
            scans.append(
                _new_scan(
                    pool_version=pool_version,
                    knowledge_at=knowledge_at,
                    symbol=symbol,
                    asset_type=asset_type,
                    source_mode=source_mode,
                    price_basis="adjusted",
                    adjustment="QFQ",
                    status="rejected",
                    bar_count=0,
                    covered_through=None,
                    source_manifest=[],
                    eligibility_by_dimension={"enabled": True, "asset_type": False},
                    features={},
                    rejection_codes=["asset_type_ineligible"],
                    setup_id=None,
                )
            )
            continue

        try:
            evidence = evidence_reader.load(symbol, knowledge_at=knowledge_at)
        except EvidenceUnavailable:
            scans.append(
                _new_scan(
                    pool_version=pool_version,
                    knowledge_at=knowledge_at,
                    symbol=symbol,
                    asset_type=asset_type,
                    source_mode=source_mode,
                    price_basis="adjusted",
                    adjustment="QFQ",
                    status="coverage_miss",
                    bar_count=0,
                    covered_through=None,
                    source_manifest=[],
                    eligibility_by_dimension={"enabled": True, "asset_type": True, "coverage": False},
                    features={},
                    rejection_codes=["coverage_miss"],
                    setup_id=None,
                )
            )
            continue

        bars = evidence.bars
        latest_close = float(bars[-1].close) if bars else None
        recent = bars[-20:]
        dollar_volumes = [
            float(item.turnover)
            if item.turnover is not None
            else float(item.close) * float(item.volume)
            for item in recent
        ]
        median_dollar_volume = median(dollar_volumes) if dollar_volumes else None
        rejection_codes: list[str] = []
        eligibility: dict[str, bool | str | None] = {
            "enabled": True,
            "asset_type": True,
            "coverage": evidence.quality_status == "ok",
            "history": len(bars) >= min_history_bars,
            "price": latest_close is not None and latest_close >= min_close,
            "liquidity": (
                median_dollar_volume is not None
                and median_dollar_volume >= min_median_dollar_volume
            ),
            "price_basis": evidence.price_basis == "adjusted",
        }
        if evidence.quality_status in {"unavailable", "failed"}:
            rejection_codes.append("coverage_miss")
        if len(bars) < min_history_bars:
            rejection_codes.append("insufficient_history")
        if latest_close is None or latest_close < min_close:
            rejection_codes.append("price_below_minimum")
        if median_dollar_volume is None or median_dollar_volume < min_median_dollar_volume:
            rejection_codes.append("liquidity_below_minimum")
        if evidence.price_basis != "adjusted":
            rejection_codes.append("invalid_price_basis")

        features: dict[str, Any] = {
            "latest_close": latest_close,
            "median_dollar_volume_20d": median_dollar_volume,
            "dollar_volume_source": "turnover" if any(item.turnover is not None for item in recent) else "proxy",
            "bar_count": len(bars),
            "quality_status": evidence.quality_status,
            "warnings": list(evidence.warnings),
        }
        setup: SetupRecord | None = None
        if not rejection_codes:
            try:
                setup = build_s1_setup(
                    evidence=evidence,
                    origin_scan_id="pending",
                )
            except ValueError as exc:
                rejection_codes.append("invalid_structure")
                features["structure_error"] = str(exc)
        if setup is not None:
            payload = {
                "pool_version": pool_version,
                "knowledge_at": knowledge_at.isoformat(),
                "symbol": symbol,
                "setup_fingerprint": setup.fingerprint,
            }
            scan_id = f"{symbol}:scan:{content_sha256(payload)[:20]}"
            setup = setup.model_copy(
                update={
                    "origin_scan_id": scan_id,
                    "content_sha256": content_sha256(
                        {
                            **setup.model_dump(mode="json"),
                            "origin_scan_id": scan_id,
                        }
                    ),
                }
            )
            setups.append(setup)
            features.update(
                {
                    "upper_trigger": setup.upper_trigger,
                    "invalidation_stop": setup.invalidation_stop,
                    "atr": setup.atr,
                    "upper_limit": setup.upper_limit,
                }
            )
        status = "eligible" if setup is not None and not rejection_codes else "rejected"
        scans.append(
            _new_scan(
                pool_version=pool_version,
                knowledge_at=knowledge_at,
                symbol=symbol,
                asset_type=asset_type,
                source_mode=evidence.source_mode,
                price_basis=evidence.price_basis,
                adjustment=(
                    evidence.bars[-1].adjustment
                    if evidence.bars
                    else "QFQ"
                ),
                status=status,
                bar_count=len(bars),
                covered_through=evidence.covered_through,
                source_manifest=(evidence.source_manifest if include_source_manifest else []),
                eligibility_by_dimension=eligibility,
                features=features,
                rejection_codes=rejection_codes,
                setup_id=setup.setup_id if setup else None,
                scan_id=(
                    f"{symbol}:scan:{content_sha256(payload)[:20]}"
                    if setup is not None
                    else None
                ),
            )
        )
    return scans, setups
