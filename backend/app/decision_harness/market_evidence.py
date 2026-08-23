from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable
from uuid import uuid4

from app.analytics.technical import calculate_technical_indicators, calculate_technical_series
from app.core.config import Settings
from app.core.time import as_utc, utc_now
from app.decision_harness.contracts import (
    CHART_PROJECTION_SCHEMA,
    BAR_COMPLETION_POLICY,
    DAILY_DATASET_SCHEMA,
    FEATURE_VERSION,
    SUPPORTED_SCOPE_TYPES,
    compact_bar,
    content_sha256,
    hash_bars,
    normalise_symbol,
)
from app.repositories.daily_evidence import DailyEvidenceRepository
from app.services.capital_flow import (
    is_trading_session_date,
    latest_completed_session_date,
    trading_session_dates,
)


class DailyMarketEvidenceService:
    """Freeze daily bars, indicators, quality and chart data behind one Interface."""

    def __init__(self, session, settings: Settings):
        self.session = session
        self.settings = settings
        self.repository = DailyEvidenceRepository(session)
        self.market_timezone = settings.market_timezone
        self.calendar_name = settings.market_calendar
        self.minimum_history_bars = max(20, int(getattr(settings, "daily_min_history_bars", 260)))

    def freeze(
        self,
        *,
        scope_type: str,
        scope_id: str,
        symbols: Iterable[str],
        benchmark_symbols: Iterable[str] = (),
        scope_version: int | None = None,
        trading_date: date | None = None,
        cutoff_time: datetime | None = None,
        bar_source: Any | None = None,
    ) -> dict[str, Any]:
        cutoff = as_utc(cutoff_time or utc_now())
        scope_type = str(scope_type).strip().lower()
        if scope_type not in SUPPORTED_SCOPE_TYPES:
            raise ValueError(f"不支持的 Decision Scope 类型：{scope_type}")
        scope_id = str(scope_id).strip()
        if not scope_id:
            raise ValueError("scope_id 不能为空")
        requested_symbols = list(dict.fromkeys(normalise_symbol(item) for item in symbols))
        if not requested_symbols:
            raise ValueError("至少需要一个 symbol")
        benchmarks = list(dict.fromkeys(normalise_symbol(item) for item in benchmark_symbols))
        all_symbols = list(dict.fromkeys(requested_symbols + benchmarks))
        latest_completed = latest_completed_session_date(cutoff, self.calendar_name)
        target_date = trading_date or latest_completed
        if target_date > latest_completed:
            raise ValueError(
                f"{target_date.isoformat()} 尚未完成收市；当前最后完整交易日为 {latest_completed.isoformat()}"
            )
        if not is_trading_session_date(target_date, self.calendar_name):
            raise ValueError(f"{target_date.isoformat()} 不是 {self.calendar_name} 的交易日")

        # A newly collected 3A snapshot is copied during persistence. This
        # fallback makes the first Phase A freeze work against existing data.
        self.repository.sync_legacy_for_symbols(
            all_symbols,
            through_date=target_date,
            cutoff_time=cutoff,
            market_timezone=self.market_timezone,
        )
        collection = {"status": "not_requested", "fetched_symbols": [], "warnings": []}
        if bar_source is not None:
            collection = self.refresh_missing_bars(
                all_symbols,
                through_date=target_date,
                cutoff_time=cutoff,
                source_adapter=bar_source,
            )
        grouped = self.repository.bars(all_symbols, through_date=target_date, cutoff_time=cutoff)
        quality, manifests, indicator_ids, chart_instruments = self._build_symbol_evidence(
            grouped,
            requested_symbols=requested_symbols,
            benchmark_symbols=benchmarks,
            target_date=target_date,
        )
        if collection["warnings"]:
            quality["warnings"].extend(str(item) for item in collection["warnings"])
        quality["collection"] = collection
        overall_status = self._overall_status(quality)
        scope = {
            "scope_type": scope_type,
            "scope_id": scope_id,
            "scope_version": scope_version,
            "symbols": requested_symbols,
            "benchmark_symbols": benchmarks,
            "trading_date": target_date.isoformat(),
        }
        payload_without_id = {
            "schema_version": DAILY_DATASET_SCHEMA,
            "trading_date": target_date.isoformat(),
            "cutoff_time": cutoff.isoformat(),
            "market_timezone": self.market_timezone,
            "bar_completion_policy": BAR_COMPLETION_POLICY,
            "scope": scope,
            "bar_manifest": manifests,
            "indicator_snapshot_ids": indicator_ids,
            "group_snapshot_ids": [],
            "quality": quality,
            "status": overall_status,
        }
        digest = content_sha256(payload_without_id)
        dataset_id = str(uuid4())
        dataset_payload = {"dataset_id": dataset_id, **payload_without_id, "content_sha256": digest}
        dataset = self.repository.save_dataset(dataset_payload, digest=digest, created_at=utc_now())
        # A content-identical freeze is idempotent.  Reuse the persisted
        # dataset id before deriving the chart projection so its foreign key,
        # payload and hash remain aligned after retries.
        dataset_payload = dict(dataset.payload_json)
        chart_payload = self._chart_projection(
            dataset_payload,
            chart_instruments,
            benchmark_symbols=benchmarks,
            quality=quality,
        )
        chart_digest = content_sha256({key: value for key, value in chart_payload.items() if key != "content_sha256"})
        chart_payload["content_sha256"] = chart_digest
        chart = self.repository.save_chart(
            dataset_id=dataset.id,
            scope_type=scope_type,
            scope_id=scope_id,
            payload=chart_payload,
            digest=chart_digest,
            created_at=utc_now(),
        )
        self.session.commit()
        return {
            "dataset": dict(dataset.payload_json),
            "chart": dict(chart.payload_json),
        }

    def refresh_missing_bars(
        self,
        symbols: Iterable[str],
        *,
        through_date: date,
        cutoff_time: datetime,
        source_adapter: Any,
    ) -> dict[str, Any]:
        """Fetch only symbols that cannot currently satisfy the daily contract.

        The adapter is deliberately narrow at this seam: the existing OpenD
        implementation exposes ``instrument_cards`` and its persistence
        payload already contains normalized history bars.  A future provider
        can implement the same output without changing dataset construction.
        """

        normalized = list(dict.fromkeys(normalise_symbol(item) for item in symbols))
        current = self.repository.bars(normalized, through_date=through_date, cutoff_time=cutoff_time)
        missing_symbols = [
            symbol
            for symbol in normalized
            if len(current.get(symbol, [])) < self.minimum_history_bars
            or not current.get(symbol)
            or current[symbol][-1].bar_date < through_date
        ]
        if not missing_symbols:
            return {
                "status": "cache_hit",
                "requested_symbols": normalized,
                "fetched_symbols": [],
                "warnings": [],
            }
        collector = getattr(source_adapter, "instrument_cards", None)
        if not callable(collector):
            return {
                "status": "unavailable",
                "requested_symbols": normalized,
                "fetched_symbols": [],
                "warnings": ["日 K 数据源未提供 instrument_cards 采集 Interface。"],
            }
        try:
            collected = dict(collector(missing_symbols))
        except Exception as exc:
            return {
                "status": "partial",
                "requested_symbols": normalized,
                "fetched_symbols": [],
                "warnings": [f"日 K 增量补数失败：{exc}"],
            }
        persistence = collected.get("_persistence")
        if not isinstance(persistence, dict):
            return {
                "status": "partial",
                "requested_symbols": normalized,
                "fetched_symbols": [],
                "warnings": ["日 K 数据源未返回可持久化的历史 K 线。"],
            }
        self.repository.sync_legacy_snapshot_bars(
            persistence,
            source="moomoo_opend_history",
            market_timezone=self.market_timezone,
            collected_at=utc_now(),
        )
        refreshed = self.repository.bars(normalized, through_date=through_date, cutoff_time=cutoff_time)
        fetched = [
            symbol
            for symbol in missing_symbols
            if refreshed.get(symbol)
            and len(refreshed[symbol]) >= len(current.get(symbol, []))
            and refreshed[symbol][-1].bar_date >= through_date
        ]
        return {
            "status": "ok" if len(fetched) == len(missing_symbols) else "partial",
            "requested_symbols": normalized,
            "fetched_symbols": fetched,
            "warnings": (
                []
                if len(fetched) == len(missing_symbols)
                else [f"以下标的补数后仍未达到完整日 K 要求：{', '.join(sorted(set(missing_symbols) - set(fetched)))}。"]
            ),
        }

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        model = self.repository.dataset(dataset_id)
        return dict(model.payload_json) if model is not None else None

    def get_chart(self, dataset_id: str) -> dict[str, Any] | None:
        model = self.repository.chart(dataset_id)
        return dict(model.payload_json) if model is not None else None

    def _build_symbol_evidence(
        self,
        grouped: dict[str, list[Any]],
        *,
        requested_symbols: list[str],
        benchmark_symbols: list[str],
        target_date: date,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], dict[str, dict[str, Any]]]:
        quality_symbols: dict[str, dict[str, Any]] = {}
        manifests: list[dict[str, Any]] = []
        indicator_ids: list[str] = []
        chart_instruments: dict[str, dict[str, Any]] = {}
        for symbol in list(dict.fromkeys(requested_symbols + benchmark_symbols)):
            models = grouped.get(symbol, [])
            bars = [compact_bar(item) for item in models]
            status, warnings = self._bar_quality(bars, target_date)
            bar_hash = hash_bars(bars) if bars else None
            quality_symbols[symbol] = {
                "status": status,
                "bar_count": len(bars),
                "latest_bar_date": bars[-1]["date"] if bars else None,
                "input_bar_hash": bar_hash,
                "warnings": warnings,
                "is_benchmark": symbol in benchmark_symbols,
            }
            manifests.append(
                {
                    "symbol": symbol,
                    "bar_count": len(bars),
                    "start_date": bars[0]["date"] if bars else None,
                    "end_date": bars[-1]["date"] if bars else None,
                    "input_bar_hash": bar_hash,
                    "source": models[-1].source if models else None,
                    "adjustment": models[-1].adjustment if models else None,
                    "exchange": models[-1].exchange if models else None,
                    "source_revisions": list(dict.fromkeys(item.source_revision for item in models)),
                    "quality_status": status,
                }
            )
            if bars:
                indicators = calculate_technical_indicators(
                    bars,
                    source="daily_bars",
                )
                indicator_quality = {
                    "status": status if status != "ok" else str(indicators.get("quality_status") or "partial"),
                    "bar_quality_status": status,
                    "warnings": [*warnings, *[str(item) for item in indicators.get("warnings", [])]],
                    "sample_count": len(bars),
                }
                snapshot = self.repository.save_indicator(
                    symbol=symbol,
                    bar_date=target_date,
                    feature_version=FEATURE_VERSION,
                    input_bar_hash=bar_hash or "",
                    payload=indicators,
                    quality=indicator_quality,
                )
                indicator_ids.append(snapshot.id)
                chart_instruments[symbol] = {
                    "symbol": symbol,
                    "bars": bars,
                    "technical_series": calculate_technical_series(bars, source="daily_bars"),
                    "indicator_snapshot_id": snapshot.id,
                    "quality": quality_symbols[symbol],
                }
            else:
                chart_instruments[symbol] = {
                    "symbol": symbol,
                    "bars": [],
                    "technical_series": {"available": False, "series": []},
                    "indicator_snapshot_id": None,
                    "quality": quality_symbols[symbol],
                }
        quality = {
            "status": self._overall_status({"symbols": quality_symbols}),
            "symbols": quality_symbols,
            "requested_symbol_count": len(requested_symbols),
            "available_symbol_count": sum(
                item["status"] in {"ok", "partial", "stale"}
                for symbol, item in quality_symbols.items()
                if symbol in requested_symbols
            ),
            "errors": [
                f"{symbol}: {warning}"
                for symbol, item in quality_symbols.items()
                for warning in item["warnings"]
                if item["status"] in {"missing", "conflicted"}
            ],
            "warnings": [
                f"{symbol}: {warning}"
                for symbol, item in quality_symbols.items()
                for warning in item["warnings"]
                if item["status"] not in {"missing", "conflicted"}
            ],
        }
        return quality, manifests, indicator_ids, chart_instruments

    def _chart_projection(
        self,
        dataset: dict[str, Any],
        chart_instruments: dict[str, dict[str, Any]],
        *,
        benchmark_symbols: list[str],
        quality: dict[str, Any],
    ) -> dict[str, Any]:
        scope = dict(dataset["scope"])
        output: dict[str, Any] = {
            "schema_version": CHART_PROJECTION_SCHEMA,
            "dataset_id": dataset["dataset_id"],
            "scope": scope,
            "timezone": self.market_timezone,
            "instruments": {},
            "overlays": [],
            "state_segments": [],
            "events": [],
            "quality": quality,
        }
        for symbol, item in chart_instruments.items():
            series = list(item["technical_series"].get("series") or [])
            benchmark_match = next(
                (
                    (name, chart_instruments[name])
                    for name in benchmark_symbols
                    if chart_instruments.get(name, {}).get("bars")
                ),
                None,
            )
            if benchmark_match:
                benchmark_symbol, benchmark = benchmark_match
                series.append(
                    self._relative_performance_series(item["bars"], benchmark["bars"], benchmark_symbol)
                )
            output["instruments"][symbol] = {
                "symbol": symbol,
                "price": {
                    "symbol": symbol,
                    "price_format": {"precision": 2, "currency": "USD"},
                    "bars": item["bars"],
                },
                "series": series,
                "indicator_snapshot_id": item["indicator_snapshot_id"],
                "quality": item["quality"],
            }
        if scope["scope_type"] == "instrument" and scope["scope_id"] in output["instruments"]:
            selected = output["instruments"][scope["scope_id"]]
            output["price"] = selected["price"]
            output["series"] = selected["series"]
            output["indicator_snapshot_id"] = selected["indicator_snapshot_id"]
        return output

    @staticmethod
    def _relative_performance_series(
        bars: list[dict[str, Any]], benchmark_bars: list[dict[str, Any]], benchmark: str
    ) -> dict[str, Any]:
        instrument = {str(item["date"]): float(item["close"]) for item in bars}
        benchmark_values = {str(item["date"]): float(item["close"]) for item in benchmark_bars}
        dates = sorted(set(instrument) & set(benchmark_values))
        if not dates:
            return {
                "series_id": f"relative_performance_vs_{benchmark}",
                "pane": "relative_strength",
                "kind": "line",
                "unit": "index",
                "benchmark": benchmark,
                "points": [],
            }
        first_instrument = instrument[dates[0]]
        first_benchmark = benchmark_values[dates[0]]
        points = [
            {
                "time": day,
                "value": round((instrument[day] / first_instrument) / (benchmark_values[day] / first_benchmark) * 100, 6),
            }
            for day in dates
            if first_instrument and first_benchmark and benchmark_values[day]
        ]
        return {
            "series_id": f"relative_performance_vs_{benchmark}",
            "pane": "relative_strength",
            "kind": "line",
            "unit": "index",
            "benchmark": benchmark,
            "reference_value": 100,
            "points": points,
        }

    def _bar_quality(self, bars: list[dict[str, Any]], target_date: date) -> tuple[str, list[str]]:
        warnings: list[str] = []
        if not bars:
            return "missing", ["没有可用日 K。"]
        for index, bar in enumerate(bars):
            if bar["high"] < max(bar["open"], bar["close"]) or bar["low"] > min(bar["open"], bar["close"]):
                return "conflicted", [f"{bar['date']} 的 OHLC 关系非法。"]
            if bar["volume"] < 0:
                return "conflicted", [f"{bar['date']} 的成交量为负数。"]
            if index and bar["date"] == bars[index - 1]["date"]:
                return "conflicted", [f"{bar['date']} 存在重复日期。"]
        if len(bars) < self.minimum_history_bars:
            warnings.append(f"只有 {len(bars)} 根日线，低于要求的 {self.minimum_history_bars} 根。")
            status = "partial"
        else:
            status = "ok"
        if date.fromisoformat(str(bars[-1]["date"])) < target_date:
            warnings.append(f"最后日 K 为 {bars[-1]['date']}，早于目标交易日的数据缺失。")
            status = "stale" if status == "ok" else status
        dates = [date.fromisoformat(str(item["date"])) for item in bars]
        expected_dates = set(trading_session_dates(dates[0], min(dates[-1], target_date), self.calendar_name))
        actual_dates = set(dates)
        unexpected_dates = sorted(actual_dates - expected_dates)
        if unexpected_dates:
            return "conflicted", [
                f"包含非交易日 K 线：{', '.join(item.isoformat() for item in unexpected_dates[:5])}。"
            ]
        missing_dates = sorted(expected_dates - actual_dates)
        if missing_dates:
            warnings.append(
                "交易日 K 线不连续，缺失 "
                f"{len(missing_dates)} 天（示例：{', '.join(item.isoformat() for item in missing_dates[:5])}）。"
            )
            if status == "ok":
                status = "partial"
        return status, warnings

    @staticmethod
    def _overall_status(quality: dict[str, Any]) -> str:
        values = [
            str(item.get("status"))
            for item in quality.get("symbols", {}).values()
        ] if "symbols" in quality else []
        if not values:
            return "missing"
        if any(value == "conflicted" for value in values):
            return "conflicted"
        if all(value == "missing" for value in values):
            return "missing"
        if any(value in {"partial", "stale", "missing"} for value in values):
            return "partial"
        return "ok"
