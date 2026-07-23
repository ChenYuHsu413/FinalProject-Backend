"""Shared dependencies for engine routers."""

from __future__ import annotations

from app.core.settings import get_settings
from app.repositories.files.engine_repo import EngineFileRepository

# Every engine endpoint can return 404 (missing data file / unknown scenario);
# declaring it keeps the OpenAPI honest so contract tests accept the 404.
NOT_FOUND_RESPONSES: dict = {404: {"description": "engine data or scenario not found"}}


def get_engine_repo() -> EngineFileRepository:
    return EngineFileRepository(get_settings().engine_data_dir)
