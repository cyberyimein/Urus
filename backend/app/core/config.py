from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings for the framework.

    Provider flags are intentionally explicit.  Moomoo/OpenD is constructed
    only when ``MOOMOO_ENABLED`` is true; other adapters remain optional.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Urus"
    app_version: str = "0.1.0"
    api_schema_version: str = "v1"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    database_url: str = "sqlite:///./urus.db"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    enabled_symbols: str = "QQQ,INTC"
    # Stage 3A full technical universe: QQQ is added by the collector as the
    # relative-strength benchmark; these are the sector ETFs and public
    # watchlist names that receive the same snapshot + daily-history fields.
    instrument_validation_symbols: str = (
        "SPY,SMH,IGV,LITE,COHR,MRVL,NOK,AMD,INTC,NVDA,NBIS,ORCL,MSFT,NOW,RKLB,AMZN,AAPL,GOOG"
    )
    moomoo_enabled: bool = False
    moomoo_host: str = "127.0.0.1"
    moomoo_port: int = 11111
    options_target_symbols: str = "SPY,QQQ,SMH,IGV"
    options_watchlist_symbols: str = (
        "LITE,COHR,MRVL,NOK,AMD,INTC,NVDA,NBIS,ORCL,MSFT,NOW,RKLB,AMZN,AAPL,GOOG"
    )
    options_watchlist_excluded_symbols: str = "SPCX"
    options_target_dtes: str = "0,7,30,60,90"
    options_max_dte: int = 90
    options_strike_range_percent: float = 20.0
    options_snapshot_batch_size: int = 400
    options_snapshot_interval_seconds: float = 0.75
    options_chain_interval_seconds: float = 3.5
    options_gamma_profile_range_percent: float = 30.0
    options_gamma_profile_points: int = 121
    options_risk_free_rate_percent: float = 4.0
    options_dividend_yield_percent: float = 0.0
    moomoo_history_days: int = 260
    moomoo_sdk_home: str = "data/moomoo_home"
    moomoo_market_symbols: str = (
        "QQQ,SPY,IWM,DIA,RSP,SMH,SOXX,IGV,HYG,LQD,TLT,IEF,UUP,GLD,USO"
    )
    capital_flow_symbols: str = "SPY,QQQ,SMH,SOXX,IGV"
    capital_flow_cache_days: int = 30
    capital_flow_projection_days: int = 5
    market_timezone: str = "America/New_York"
    # Exchange-calendars identifier used by the in-process scheduler. XNYS
    # covers the NYSE/Nasdaq US equity session and its holiday/early-close
    # rules; deployments can override this for another supported exchange.
    market_calendar: str = "XNYS"
    fred_enabled: bool = False
    fred_base_url: str = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    fred_timeout_seconds: float = 10.0
    fred_lookback_days: int = 30
    yahoo_enabled: bool = False
    yahoo_base_url: str = "https://query2.finance.yahoo.com/v8/finance/chart"
    yahoo_timeout_seconds: float = 10.0
    yahoo_lookback_days: int = 30
    anomalo_enabled: bool = False
    anomalo_base_url: str | None = None
    # Agent web-search runs can take several minutes; keep connection failure
    # handling separate in the HTTP adapter while allowing a long read window.
    anomalo_timeout_seconds: float = 600.0
    # Scheduled events are opt-in during validation. The breaking/news agent
    # remains defined but disabled until its source policy is finalized.
    expected_events_enabled: bool = False
    breaking_events_enabled: bool = False
    # Select the 1B/3B research overlay. ``events`` preserves the scheduled
    # event workflow; ``cta`` runs deterministic ETF-proxy trend pressure.
    # This branch is the CTA research fork. Deployments can still opt back
    # into the scheduled-event overlay explicitly with `events`.
    workflow_research_variant: Literal["events", "cta"] = "cta"
    cta_proxy_symbols: str = "SPY,QQQ,IWM,IEF,TLT,UUP,GLD,USO,HYG,LQD,SMH,IGV"
    anomalo_scheduled_agent: str = "scheduled-event-investigator"
    anomalo_breaking_agent: str = "breaking-event-investigator"
    urus_agent_enabled: bool = False
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    urus_agent_model: str = "deepseek/deepseek-v4-flash-0731"
    urus_agent_temperature: float = 0.1
    # A full daily-cycle synthesis can be slower than a single small model
    # turn; keep this configurable while using the long-running local default.
    urus_agent_timeout_seconds: float = 1200.0
    # Optional provider-side completion cap.  Leave unset (or set to 0) to let
    # the model finish a large synthesis response without Urus truncating it.
    urus_agent_max_completion_tokens: int | None = None
    urus_agent_input_cost_per_million: float = 0.0
    urus_agent_output_cost_per_million: float = 0.0
    urus_agent_max_tool_iterations: int = 8
    urus_agent_max_tool_result_bytes: int = 100000
    urus_agent_max_total_tool_result_bytes: int = 500000
    urus_agent_max_context_bytes: int = 500000
    urus_agent_max_total_tool_calls: int = 24
    urus_agent_max_raw_response_bytes: int = 200000
    # Independent theme invocations run concurrently. Market and synthesis
    # remain dependency boundaries; option structure is deterministic context.
    urus_agent_theme_max_concurrency: int = 6
    urus_agent_enforce_stage_tools: bool = True
    urus_agent_event_limit: int = 10
    # Runtime scheduler defaults. They can be overridden from /settings and
    # are intentionally separate from the provider credentials above.
    scheduled_pre_market_enabled: bool = True
    scheduled_pre_market_skip_ai_decision: bool = False
    scheduled_pre_close_enabled: bool = True
    scheduled_pre_close_skip_ai_decision: bool = True
    scheduled_post_close_enabled: bool = True
    scheduled_post_close_skip_ai_decision: bool = False
    event_discovery_horizon_days: int = 120
    event_instrument_symbols: str = (
        "LITE,COHR,MRVL,NOK,AMD,INTC,NVDA,NBIS,ORCL,MSFT,NOW,RKLB,AMZN,AAPL,GOOG"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if not raw:
            return []
        if raw.startswith("["):
            parsed = json.loads(raw)
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in raw.split(",") if item.strip()]

    @property
    def enabled_symbol_list(self) -> list[str]:
        return [item.strip().upper() for item in self.enabled_symbols.split(",") if item.strip()]

    @property
    def instrument_validation_symbol_list(self) -> list[str]:
        return [
            item.strip().upper()
            for item in self.instrument_validation_symbols.split(",")
            if item.strip()
        ]

    @property
    def capital_flow_symbol_list(self) -> list[str]:
        return [
            item.strip().upper()
            for item in self.capital_flow_symbols.split(",")
            if item.strip()
        ]

    @property
    def event_instrument_symbol_list(self) -> list[str]:
        return [
            item.strip().upper()
            for item in self.event_instrument_symbols.split(",")
            if item.strip()
        ]

    @property
    def cta_proxy_symbol_list(self) -> list[str]:
        return [
            item.strip().upper()
            for item in self.cta_proxy_symbols.split(",")
            if item.strip()
        ]

    @property
    def options_target_symbol_list(self) -> list[str]:
        return [
            item.strip().upper()
            for item in self.options_target_symbols.split(",")
            if item.strip()
        ]

    @property
    def options_watchlist_symbol_list(self) -> list[str]:
        return [
            item.strip().upper()
            for item in self.options_watchlist_symbols.split(",")
            if item.strip()
        ]

    @property
    def options_watchlist_excluded_symbol_list(self) -> list[str]:
        return [
            item.strip().upper()
            for item in self.options_watchlist_excluded_symbols.split(",")
            if item.strip()
        ]

    @property
    def options_collection_symbol_list(self) -> list[str]:
        """Core option ETFs plus every public instrument in the option watchlist."""
        return list(
            dict.fromkeys(
                self.options_target_symbol_list
                + self.options_watchlist_symbol_list
                + self.enabled_symbol_list
            )
        )

    @property
    def options_target_dte_list(self) -> list[int]:
        return sorted(
            {
                int(item.strip())
                for item in self.options_target_dtes.split(",")
                if item.strip()
            }
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
