from __future__ import annotations

import json
from functools import lru_cache

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
    # Full SQLite integrity checks are expensive enough to keep out of the
    # regular liveness endpoint. The sidecar is independent from the DB so a
    # corrupt file can still expose its last check result to the UI.
    database_health_path: str = ""
    database_health_interval_hours: float = 24.0
    # Optional deployed Urus instance used as the source of truth for the
    # local observation Universe. Leave empty in production to use its own
    # active Universe; local workstations can point at the deployed API root.
    observation_universe_source_url: str = ""
    observation_universe_sync_timeout_seconds: float = 15.0
    # A stale local revision is a deliberate emergency mode.  Keep the
    # default fail-closed so a broken upstream cannot silently change the
    # observed universe.
    observation_allow_stale_universe: bool = False
    sidecar_capture_database_path: str = "/data/urus-moomoo/collector.sqlite"
    observation_scheduled_require_source_captures: bool = False
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    enabled_symbols: str = "QQQ,INTC"
    # Configured technical universe: QQQ is the default relative-strength
    # benchmark; these sector ETFs and public watchlist names receive the same
    # snapshot and daily-history fields.
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
    # Option underlying overview and expiration endpoints share the provider's
    # 60 requests / 30 seconds ceiling. Keep a separate bucket from contract
    # snapshots so repeated option runs cannot burst metadata requests.
    options_metadata_interval_seconds: float = 0.55
    options_gamma_profile_range_percent: float = 30.0
    options_gamma_profile_points: int = 121
    options_risk_free_rate_percent: float = 4.0
    options_dividend_yield_percent: float = 0.0
    moomoo_history_days: int = 260
    # Historical candlestick capacity is account-dependent. Keep a safety
    # reserve and fail closed when OpenD cannot report the live quota.
    moomoo_history_quota_reserve_ratio: float = 0.20
    moomoo_history_quota_reserve_absolute: int = 10
    moomoo_capacity_snapshot_ttl_seconds: float = 300.0
    moomoo_history_request_interval_seconds: float = 0.55
    moomoo_snapshot_request_interval_seconds: float = 0.55
    moomoo_trading_calendar_request_interval_seconds: float = 1.05
    # One host-wide lock serializes the personal collector across workflow
    # subprocesses. The lock file also remembers provider pacing timestamps.
    moomoo_collection_lock_path: str = "data/moomoo_collection.lock"
    # High-frequency option facts live in a dedicated store so multi-month
    # research growth cannot inflate or lock the operational Urus database.
    intraday_options_enabled: bool = False
    intraday_options_database_url: str = (
        "sqlite:///./data/intraday_options/intraday_options-v2.db"
    )
    intraday_options_raw_payload_dir: str = "data/intraday_options/raw"
    intraday_options_backup_dir: str = "data/intraday_options/backups"
    intraday_options_symbols: str = "SPY,QQQ,IWM"
    intraday_options_fast_band_percent: float = 3.0
    intraday_options_structure_band_percent: float = 10.0
    intraday_options_poll_seconds: float = 10.0
    intraday_options_min_quote_coverage: float = 0.80
    intraday_options_close_finalizer_delay_minutes: int = 5
    intraday_options_backup_retention_days: int = 14
    # Minimum completed daily bars required before a chart/evidence package is
    # considered ready for strategy evaluation.  Keep this independent from
    # the provider fetch window so another collector can satisfy the contract.
    daily_min_history_bars: int = 260
    moomoo_sdk_home: str = "data/moomoo_home"
    moomoo_market_symbols: str = (
        "QQQ,SPY,IWM,DIA,RSP,SMH,SOXX,IGV,HYG,LQD,TLT,IEF,UUP,GLD,USO"
    )
    capital_flow_symbols: str = "SPY,QQQ,SMH,SOXX,IGV"
    capital_flow_cache_days: int = 30
    capital_flow_projection_days: int = 5
    market_timezone: str = "America/New_York"
    # Exchange-calendars identifier used for session open/close timestamps by
    # the in-process scheduler. XNYS covers the NYSE/Nasdaq US equity session
    # and its holiday/early-close rules; deployments can override this for
    # another supported exchange. When Moomoo is enabled, its trading-days API
    # remains authoritative for session membership.
    market_calendar: str = "XNYS"
    fred_enabled: bool = False
    fred_base_url: str = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    fred_timeout_seconds: float = 10.0
    fred_lookback_days: int = 30
    yahoo_enabled: bool = False
    yahoo_base_url: str = "https://query2.finance.yahoo.com/v8/finance/chart"
    yahoo_timeout_seconds: float = 10.0
    yahoo_lookback_days: int = 30
    anomalo_base_url: str | None = None
    # Remote Workflow execution remains disabled until a published binding and
    # a scoped service token are configured.
    anomalo_workflow_enabled: bool = False
    anomalo_workflow_token: str | None = None
    anomalo_workflow_connect_timeout_seconds: float = 10.0
    anomalo_workflow_read_timeout_seconds: float = 1200.0
    anomalo_workflow_fake_adapter: bool = False
    remote_decision_max_input_bytes: int = 500000
    remote_decision_max_cross_section_rows: int = 1000
    remote_decision_max_notable_cards: int = 20
    remote_decision_event_poll_limit: int = 500
    # A 0.25s poll interval should cover the Workflow read timeout (20 min)
    # after a Urus restart, rather than failing a still-running remote Run
    # after a shorter timeout window.
    remote_decision_max_polls: int = 4800
    # Runtime scheduler default. It can be overridden from /settings.
    scheduled_post_close_enabled: bool = True

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

    @property
    def intraday_options_symbol_list(self) -> list[str]:
        return list(
            dict.fromkeys(
                item.strip().upper()
                for item in self.intraday_options_symbols.split(",")
                if item.strip()
            )
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
