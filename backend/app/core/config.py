from __future__ import annotations

import json
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings for the framework.

    The integration flags are intentionally conservative.  The framework always
    uses local mock adapters; no real provider client is constructed here.
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
    moomoo_enabled: bool = False
    moomoo_host: str = "127.0.0.1"
    moomoo_port: int = 11111
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
