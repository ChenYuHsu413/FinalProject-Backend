"""Application settings (pydantic-settings).

Reads from environment / `.env`. The secret-loading interface is deliberately a
single class so a future secret backend (Vault, GCP Secret Manager) can be
swapped in without touching call sites (PROMPT §6.5).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_env: str = "dev"  # dev | prod
    api_version: str = "0.1.0"
    schema_version: str = "1.0"

    # Trust boundary — shared secret with the Flask BFF.
    service_token: str = ""

    # Engine data (file-backed ML pipeline outputs).
    engine_data_dir: str = "/srv/data/engine"

    # PostgreSQL (governance) — used from batch 2 onward.
    database_url: str = ""

    # Redis (events) — used from batch 3 onward.
    redis_url: str = ""

    # Mock simulator — used from batch 3 onward.
    mock_mode: bool = True

    @property
    def is_prod(self) -> bool:
        return self.app_env.lower() == "prod"


@lru_cache
def get_settings() -> Settings:
    """Cached accessor. Tests clear the cache via `get_settings.cache_clear()`."""
    return Settings()
