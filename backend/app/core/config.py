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
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    enabled_symbols: str = "QQQ,INTC"
    instrument_validation_symbols: str = "INTC,SMH"
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
    market_timezone: str = "America/New_York"
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
    anomalo_timeout_seconds: float = 10.0

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
