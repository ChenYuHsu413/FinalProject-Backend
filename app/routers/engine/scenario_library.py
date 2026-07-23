"""Scenario model library (後端資料規格書 §七)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.permissions import DASHBOARD_READ
from app.core.security import require_permission
from app.repositories.files.engine_repo import EngineFileRepository
from app.routers.engine.deps import NOT_FOUND_RESPONSES, get_engine_repo
from app.routers.engine.models import ScenarioLibrary

router = APIRouter(tags=["engine:scenario-library"], responses=NOT_FOUND_RESPONSES)


@router.get("/scenario-library", response_model=ScenarioLibrary)
def scenario_library(
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(DASHBOARD_READ)),
) -> dict:
    return repo.scenario_library()
