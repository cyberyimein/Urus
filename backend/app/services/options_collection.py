from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analytics.options_volatility import enrich_option_overview
from app.integrations.moomoo_options import (
    DisabledOptionsAdapter,
    MoomooOptionsAdapter,
    OptionsCollectorAdapter,
)
from app.models import StepStatus


@dataclass(frozen=True)
class OptionsCollectionResult:
    """Public option evidence plus the private persistence payload, if any."""

    status: StepStatus
    summary: str
    payload: dict[str, object]
    data_state: str
    persistence_payload: dict[str, object] | None = None
    error_message: str | None = None


class OptionsCollectionService:
    """Collect one snapshot through the provider-neutral options seam.

    Workflow runs and Observation Runs share the same provider adapter, but
    only the former need the legacy normalized contract tables. Keeping the
    public payload separate from that persistence detail lets the new
    Observation Run own an immutable options evidence payload without making
    it depend on the old workflow snapshot identity.
    """

    @staticmethod
    def build_adapter(
        settings: Any,
        *,
        target_symbols: list[str] | None = None,
        rate_limiter: object | None = None,
    ) -> OptionsCollectorAdapter:
        if not settings.moomoo_enabled:
            return DisabledOptionsAdapter()
        symbols = target_symbols
        if symbols is None:
            symbols = settings.options_collection_symbol_list
        symbols = list(dict.fromkeys(str(item).strip().upper() for item in symbols if str(item).strip()))
        if not symbols:
            return DisabledOptionsAdapter()
        return MoomooOptionsAdapter(
            host=settings.moomoo_host,
            port=settings.moomoo_port,
            symbols=symbols,
            target_dtes=settings.options_target_dte_list,
            max_dte=settings.options_max_dte,
            strike_range_percent=settings.options_strike_range_percent,
            batch_size=settings.options_snapshot_batch_size,
            snapshot_interval_seconds=settings.options_snapshot_interval_seconds,
            option_chain_interval_seconds=settings.options_chain_interval_seconds,
            option_metadata_interval_seconds=settings.options_metadata_interval_seconds,
            gamma_profile_range_percent=settings.options_gamma_profile_range_percent,
            gamma_profile_points=settings.options_gamma_profile_points,
            risk_free_rate_percent=settings.options_risk_free_rate_percent,
            dividend_yield_percent=settings.options_dividend_yield_percent,
            rate_limiter=rate_limiter,
        )

    @staticmethod
    def placeholder(
        symbols: list[str],
        *,
        status: str = "not_implemented",
        note: str = "Moomoo 期权快照未启用。",
        data_state: str = "placeholder",
    ) -> dict[str, object]:
        normalized = list(dict.fromkeys(str(item).strip().upper() for item in symbols if str(item).strip()))
        return {
            "is_mock": True,
            "status": status,
            "available": False,
            "provider": None,
            "source_mode": "snapshot",
            "requested_symbols": normalized,
            "unavailable_symbols": normalized,
            "symbols": [],
            "data_state": data_state,
            "note": note,
        }

    def collect(
        self,
        adapter: OptionsCollectorAdapter | None,
        symbols: list[str] | None = None,
    ) -> OptionsCollectionResult:
        requested_symbols = list(
            dict.fromkeys(
                str(item).strip().upper()
                for item in (symbols or [])
                if str(item).strip()
            )
        )
        if adapter is None:
            return OptionsCollectionResult(
                status=StepStatus.PLACEHOLDER,
                summary="期权数据源未配置，保留明确的占位状态。",
                payload=self.placeholder(requested_symbols, note="期权数据源未配置。"),
                data_state="placeholder",
            )

        try:
            try:
                raw_payload = adapter.options_snapshot(requested_symbols or None)
            except TypeError:
                # Preserve third-party/test adapters that implement the
                # original no-argument protocol.
                raw_payload = adapter.options_snapshot()
            if not isinstance(raw_payload, dict):
                raise TypeError("期权数据源必须返回 JSON object")

            payload = dict(raw_payload)
            persistence = payload.pop("_persistence", None)
            persistence_payload = persistence if isinstance(persistence, dict) else None
            payload_symbols = payload.get("symbols")
            payload_symbols = payload_symbols if isinstance(payload_symbols, list) else []
            for item in payload_symbols:
                if not isinstance(item, dict):
                    continue
                overview = item.get("overview")
                if isinstance(overview, dict):
                    item["overview"] = enrich_option_overview(overview)

            is_mock = bool(payload.get("is_mock", True))
            payload["data_state"] = "placeholder" if is_mock else "live"
            payload.setdefault("requested_symbols", requested_symbols)
            payload.setdefault("unavailable_symbols", [])
            payload.setdefault("symbols", payload_symbols)
            if is_mock:
                payload["status"] = StepStatus.PLACEHOLDER.value
                return OptionsCollectionResult(
                    status=StepStatus.PLACEHOLDER,
                    summary="期权数据源未启用，保留明确的 mock 状态。",
                    payload=payload,
                    data_state="placeholder",
                    persistence_payload=persistence_payload,
                )
            provider_status = str(payload.get("status") or "").strip().lower()
            if payload.get("available") is False or provider_status in {"failed", "unavailable"}:
                payload["status"] = StepStatus.UNAVAILABLE.value
                payload["available"] = False
                payload["data_state"] = "unavailable"
                return OptionsCollectionResult(
                    status=StepStatus.UNAVAILABLE,
                    summary="期权数据源已响应，但没有可用的期权结构。",
                    payload=payload,
                    data_state="unavailable",
                    persistence_payload=persistence_payload,
                )
            return OptionsCollectionResult(
                status=StepStatus.SUCCEEDED,
                summary="已生成 Moomoo 快照式 DEX/GEX、Gamma Wall 与 Max Pain。",
                payload=payload,
                data_state="live",
                persistence_payload=persistence_payload,
            )
        except Exception as exc:
            return OptionsCollectionResult(
                status=StepStatus.FAILED,
                summary="期权结构采集或计算失败。",
                payload=self.placeholder(
                    requested_symbols,
                    status="unavailable",
                    note="期权快照采集失败，未将不完整结构标记为真实数据。",
                    data_state="unavailable",
                ),
                data_state="unavailable",
                error_message=str(exc),
            )
