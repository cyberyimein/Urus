from __future__ import annotations

import json
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings for the framework skeleton.

    Provider-specific settings are intentionally not part of the framework
    baseline. Real data adapters are added on their own stage branches.
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
    options_target_symbols: str = "QQQ,INTC"
    options_target_dtes: str = "0,7,30,60,90"
    options_max_dte: int = 90
    options_strike_range_percent: float = 20.0
    options_snapshot_batch_size: int = 400
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
    def options_target_symbol_list(self) -> list[str]:
        return [
            item.strip().upper()
            for item in self.options_target_symbols.split(",")
            if item.strip()
        ]

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
