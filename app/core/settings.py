"""Application settings (pydantic-settings).

Reads from environment / `.env`. The secret-loading interface is deliberately a
single class so a future secret backend (Vault, GCP Secret Manager) can be
swapped in without touching call sites (PROMPT §6.5).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Deployment topology switch (DEPLOY_MODE). `full` is the current admin-only
# governance model; `lite` additionally grants engineers the approve authority for
# model_promotion / param_tuning to support a single-role workbench. See
# docs/DECISIONS.md D1.8 and app/core/permissions.py.
DEPLOY_MODES: frozenset[str] = frozenset({"full", "lite"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # The batch-8 `model_*` fields collide with pydantic's default protected
        # namespace; this class has no BaseModel API to shadow, so drop it.
        protected_namespaces=(),
    )

    # Application
    app_env: str = "dev"  # dev | prod
    api_version: str = "0.1.0"
    schema_version: str = "1.0"

    # Deployment topology (DEPLOY_MODE): full | lite. Drives the permission matrix
    # (app/core/permissions.py) — `lite` widens engineer's approval rights. Invalid
    # values fail fast at startup (validator below).
    deploy_mode: str = "full"

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

    # Model service (batch 8) — the external inference service (SEAM B). When the
    # model team delivers, only `model_service_url` and the adapter's field
    # mapping change, never the call sites. Default "mock" so tests / CI never
    # touch the network.
    model_source: str = "mock"  # mock | http
    model_service_url: str = ""  # e.g. https://icefeather-aifinalproject.hf.space
    model_service_timeout_s: float = 3.0
    model_cache_ttl_s: float = 5.0

    @field_validator("deploy_mode", mode="before")
    @classmethod
    def _validate_deploy_mode(cls, v: object) -> str:
        # Normalize case/whitespace, then reject anything outside full|lite so a
        # typo'd DEPLOY_MODE fails at startup (Settings construction) rather than
        # silently defaulting and mis-granting permissions.
        s = str(v).strip().lower()
        if s not in DEPLOY_MODES:
            raise ValueError(f"DEPLOY_MODE must be one of {sorted(DEPLOY_MODES)}, got {v!r}")
        return s

    @property
    def is_prod(self) -> bool:
        return self.app_env.lower() == "prod"

    @property
    def model_enabled(self) -> bool:
        return self.model_source == "http" and bool(self.model_service_url)


@lru_cache
def get_settings() -> Settings:
    """Cached accessor. Tests clear the cache via `get_settings.cache_clear()`."""
    return Settings()
