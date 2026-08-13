from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from math import isfinite
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.analytics.options import classify_strike_gex_structure
from app.core.time import as_utc
from app.models import (
    OptionAnalysisBatchModel,
    OptionExpirationAnalysisModel,
    OptionSymbolSnapshotModel,
)


DISPLAY_PROJECTION_SCHEMA = "urus.report_display_projection.v1"


def build_report_display_projection(
    session: Session,
    *,
    report_id: str,
    source_snapshot_ids: list[str],
    source_run_ids: list[str] | None = None,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    """Project normalized option snapshots into chart-ready report data.

    This function only reads normalized tables.  It never reads the compact
    decision packet, so trimming `by_strike` or profile points for AI remains
    safe and intentional.
    """

    snapshot_ids = _unique_strings(source_snapshot_ids)
    run_ids = _unique_strings(source_run_ids or [])
    batches = _load_batches(session, snapshot_ids)
    symbols: dict[str, dict[str, Any]] = {}
    latest_symbol_source: dict[str, tuple[int, datetime, str]] = {}
    warnings: list[str] = []
    missing_sections: list[str] = []

    # Earlier paired observations provide provenance, while the last source
    # snapshot is the report's current option surface. It replaces the whole
    # expiration set for a symbol without mutating either source snapshot.
    snapshot_rank = {snapshot_id: index for index, snapshot_id in enumerate(snapshot_ids)}
    batches.sort(
        key=lambda batch: (
            snapshot_rank.get(batch.snapshot_id, -1),
            as_utc(batch.captured_at),
        )
    )
    for batch in batches:
        batch_source = (
            snapshot_rank.get(batch.snapshot_id, -1),
            as_utc(batch.captured_at),
            str(batch.id),
        )
        for symbol_model in batch.symbols:
            symbol = str(symbol_model.symbol).upper()
            previous_source = latest_symbol_source.get(symbol)
            if previous_source is not None and batch_source < previous_source:
                # A defensive guard for callers that provide source IDs in a
                # non-chronological order. Older normalized rows must never
                # overwrite the report's latest symbol state.
                continue
            symbol_payload = symbols.setdefault(
                symbol,
                {
                    "spot": _finite_or_none(symbol_model.spot),
                    "as_of": symbol_model.spot_time or _iso(batch.captured_at),
                    "overview": dict(symbol_model.overview or {}),
                    "expirations": {},
                },
            )
            if previous_source is None or batch_source > previous_source:
                # The latest snapshot is the report's current option surface.
                # Replace the complete expiration set instead of retaining an
                # expiry that disappeared from the newer provider capture.
                symbol_payload["spot"] = _finite_or_none(symbol_model.spot)
                symbol_payload["as_of"] = symbol_model.spot_time or _iso(batch.captured_at)
                symbol_payload["overview"] = dict(symbol_model.overview or {})
                symbol_payload["expirations"] = {}
                latest_symbol_source[symbol] = batch_source
            for expiration_model in symbol_model.expirations:
                expiration = expiration_model.expiration.isoformat()
                expiry_payload = _project_expiration(symbol_model, expiration_model)
                symbol_payload["expirations"][expiration] = expiry_payload

    if not snapshot_ids:
        warnings.append("没有关联的 source snapshot，无法生成期权展示投影。")
        missing_sections.append("options")
    elif not batches:
        warnings.append("source snapshot 存在，但没有可用的标准化期权快照。")
        missing_sections.append("options")
    elif not symbols:
        warnings.append("标准化期权快照为空，没有可展示的标的。")
        missing_sections.append("options")

    for symbol, symbol_payload in symbols.items():
        if not symbol_payload["expirations"]:
            warnings.append(f"{symbol} 没有可用的期权到期日。")
            missing_sections.append(f"options.{symbol}.expirations")
        for expiration, expiry in symbol_payload["expirations"].items():
            if not expiry["strike_structure"]["is_complete"]:
                warnings.append(f"{symbol} {expiration} 行权价结构不是完整链。")
                missing_sections.append(f"options.{symbol}.expirations.{expiration}.strike_structure")
            if not expiry["gamma_profile"]["is_complete"]:
                warnings.append(f"{symbol} {expiration} Gamma Profile 点不完整或不可用。")
                missing_sections.append(f"options.{symbol}.expirations.{expiration}.gamma_profile")

    # Prefer the normalized provider capture timestamp; the report cutoff is
    # only a fallback for unavailable/empty source data.
    latest_capture = (batches[-1].captured_at if batches else None) or captured_at
    source = {
        "report_id": report_id,
        "run_id": run_ids[-1] if run_ids else None,
        "run_ids": run_ids,
        "snapshot_ids": snapshot_ids,
        "captured_at": _iso(latest_capture),
        "content_sha256": None,
    }
    payload: dict[str, Any] = {
        "schema_version": DISPLAY_PROJECTION_SCHEMA,
        "source": source,
        "options": {"symbols": symbols},
        "chart_specs": _chart_specs(symbols),
        "data_quality": {
            "warnings": _unique_strings(warnings),
            "missing_sections": _unique_strings(missing_sections),
            "source_available": bool(
                batches
                and any(symbol_payload.get("expirations") for symbol_payload in symbols.values())
            ),
        },
    }
    digest = _content_sha256(payload)
    source["content_sha256"] = digest
    return payload


def projection_content_sha256(payload: dict[str, Any]) -> str:
    """Return the stable hash used by the projection table and manifest."""

    return _content_sha256(payload)


def _load_batches(session: Session, snapshot_ids: list[str]) -> list[OptionAnalysisBatchModel]:
    if not snapshot_ids:
        return []
    statement = (
        select(OptionAnalysisBatchModel)
        .options(
            selectinload(OptionAnalysisBatchModel.symbols)
            .selectinload(OptionSymbolSnapshotModel.expirations)
            .selectinload(OptionExpirationAnalysisModel.contracts),
            selectinload(OptionAnalysisBatchModel.symbols)
            .selectinload(OptionSymbolSnapshotModel.expirations)
            .selectinload(OptionExpirationAnalysisModel.profile_points),
            selectinload(OptionAnalysisBatchModel.symbols)
            .selectinload(OptionSymbolSnapshotModel.expirations)
            .selectinload(OptionExpirationAnalysisModel.gamma_flips),
        )
        .where(OptionAnalysisBatchModel.snapshot_id.in_(snapshot_ids))
    )
    return list(session.scalars(statement))


def _project_expiration(
    symbol_model: OptionSymbolSnapshotModel,
    expiration_model: OptionExpirationAnalysisModel,
) -> dict[str, Any]:
    contracts = list(expiration_model.contracts or [])
    rows = _strike_rows(contracts, spot=float(symbol_model.spot or 0.0))
    spot = _finite_or_none(symbol_model.spot)
    spot_strike = min(
        (float(row["strike"]) for row in rows),
        key=lambda strike: abs(strike - spot) if spot is not None else abs(strike),
        default=None,
    )
    profile_points = [
        {
            "spot": float(point.hypothetical_spot),
            "call_gex": float(point.call_gex),
            "put_gex": float(point.put_gex),
            "net_gex": float(point.net_gex),
        }
        for point in sorted(expiration_model.profile_points or [], key=lambda item: item.point_index)
    ]
    flip_levels = sorted(expiration_model.gamma_flips or [], key=lambda item: item.position)
    flips = [
        {
            "spot": float(flip.level),
            "direction": _flip_direction(float(flip.level), profile_points),
            "is_primary": bool(flip.is_primary),
        }
        for flip in flip_levels
    ]
    profile_metadata = dict(expiration_model.profile_metadata or {})
    profile_available = bool(expiration_model.profile_available and profile_points)
    expected_move = {
        "amount": _finite_or_none(expiration_model.expected_move_amount),
        "percent": _finite_or_none(expiration_model.expected_move_percent),
        "atm_strike": _finite_or_none(expiration_model.expected_move_atm_strike),
    }
    point_count = int(profile_metadata.get("point_count") or len(profile_points))
    return {
        "dte": int(expiration_model.days_to_expiry),
        "days_to_expiry": int(expiration_model.days_to_expiry),
        "max_pain": _finite_or_none(expiration_model.max_pain),
        "expected_move": expected_move,
        "exposure_totals": dict(expiration_model.exposure_totals or {}),
        "walls": dict(expiration_model.exposure_walls or {}),
        "strike_structure": {
            "spot_strike": spot_strike,
            "range": {
                "min": float(rows[0]["strike"]) if rows else None,
                "max": float(rows[-1]["strike"]) if rows else None,
            },
            "rows": rows,
            "row_count": len(rows),
            "contract_count": len(contracts),
            "is_complete": bool(rows) and len(contracts) == int(expiration_model.contract_count),
        },
        "gamma_profile": {
            "current_spot": spot,
            "current_spot_net_gex": _finite_or_none(expiration_model.current_spot_net_gex),
            "primary_gamma_flip": _finite_or_none(expiration_model.primary_gamma_flip),
            "points": profile_points,
            "flips": flips,
            "point_count": len(profile_points),
            "expected_point_count": point_count,
            "is_complete": profile_available and len(profile_points) == point_count,
        },
    }


def _strike_rows(contracts: list[Any], *, spot: float) -> list[dict[str, Any]]:
    by_strike: dict[float, dict[str, Any]] = {}
    for contract in contracts:
        strike = float(contract.strike)
        row = by_strike.setdefault(
            strike,
            {
                "strike": strike,
                "call_oi": 0,
                "put_oi": 0,
                "call_volume": 0,
                "put_volume": 0,
                "call_dex": 0.0,
                "put_dex": 0.0,
                "net_dex": 0.0,
                "call_gex": 0.0,
                "put_gex": 0.0,
                "net_gex": 0.0,
            },
        )
        option_type = str(contract.option_type).upper()
        prefix = "call" if option_type == "CALL" else "put"
        row[f"{prefix}_oi"] += int(contract.open_interest or 0)
        row[f"{prefix}_volume"] += int(contract.volume or 0)
        if (
            int(contract.open_interest or 0) > 0
            and spot > 0
            and float(contract.multiplier or 0) > 0
        ):
            if contract.delta is not None and isfinite(float(contract.delta)):
                dex = float(contract.delta) * int(contract.open_interest) * float(contract.multiplier) * spot
                row[f"{prefix}_dex"] += dex
                row["net_dex"] += dex
            if contract.gamma is not None and isfinite(float(contract.gamma)):
                gex = (
                    float(contract.gamma)
                    * int(contract.open_interest)
                    * float(contract.multiplier)
                    * spot**2
                    * 0.01
                )
                signed_gex = gex if option_type == "CALL" else -gex
                row[f"{prefix}_gex"] += signed_gex
                row["net_gex"] += signed_gex
    normalized: list[dict[str, Any]] = []
    for strike in sorted(by_strike):
        row = by_strike[strike]
        row.update(
            {
                key: round(float(row[key]), 2)
                for key in (
                    "call_dex",
                    "put_dex",
                    "net_dex",
                    "call_gex",
                    "put_gex",
                    "net_gex",
                )
            }
        )
        row["modeled_net_gex"] = row["net_gex"]
        normalized.append(row)
    # Use the same 2% noise threshold as the canonical option analytics so
    # report charts and the operations view cannot disagree on gamma regime.
    classify_strike_gex_structure(normalized)
    return normalized


def _flip_direction(level: float, points: list[dict[str, Any]]) -> str:
    if not points:
        return "unknown"
    left = [point for point in points if float(point["spot"]) <= level]
    right = [point for point in points if float(point["spot"]) >= level]
    # A persisted flip can land exactly on a sampled profile point.  Use the
    # neighboring points in that case so the direction still describes the
    # actual sign transition rather than comparing the same point to itself.
    if left and right and float(left[-1]["spot"]) == float(right[0]["spot"]):
        left = [point for point in points if float(point["spot"]) < level]
        right = [point for point in points if float(point["spot"]) > level]
    left_value = float(left[-1]["net_gex"]) if left else None
    right_value = float(right[0]["net_gex"]) if right else None
    if left_value is None or right_value is None:
        return "unknown"
    if left_value < 0 <= right_value:
        return "negative_to_positive"
    if left_value > 0 >= right_value:
        return "positive_to_negative"
    return "zero_crossing"


def _chart_specs(symbols: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for symbol, payload in symbols.items():
        for expiration in payload.get("expirations", {}):
            safe_symbol = symbol.lower()
            safe_expiration = expiration
            base = f"options.symbols.{symbol}.expirations.{safe_expiration}"
            specs.extend(
                [
                    {
                        "id": f"{safe_symbol}-{safe_expiration}-strike-exposure",
                        "kind": "bar",
                        "data_ref": f"{base}.strike_structure.rows",
                        "x_field": "strike",
                        "series": ["net_dex", "net_gex"],
                        "unit": "exposure",
                    },
                    {
                        "id": f"{safe_symbol}-{safe_expiration}-gamma-profile",
                        "kind": "line",
                        "data_ref": f"{base}.gamma_profile.points",
                        "x_field": "spot",
                        "series": ["call_gex", "put_gex", "net_gex"],
                        "unit": "gex",
                    },
                ]
            )
    return specs


def _content_sha256(payload: dict[str, Any]) -> str:
    source = dict(payload.get("source") or {})
    source["content_sha256"] = None
    canonical_payload = dict(payload)
    canonical_payload["source"] = source
    encoded = json.dumps(canonical_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _unique_strings(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value))


def _finite_or_none(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if isfinite(numeric) else None


def _iso(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return as_utc(value).isoformat()
    return value.isoformat()
