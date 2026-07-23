"""Scenario summaries (後端資料規格書 §2.6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.permissions import DASHBOARD_READ
from app.core.security import require_permission
from app.repositories.files.engine_repo import EngineFileRepository
from app.routers.engine.deps import NOT_FOUND_RESPONSES, get_engine_repo
from app.routers.engine.models import ScenariosSummary

router = APIRouter(tags=["engine:scenarios"], responses=NOT_FOUND_RESPONSES)


@router.get("/scenarios", response_model=ScenariosSummary)
def scenarios(
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(DASHBOARD_READ)),
) -> dict:
    return repo.scenarios_status()
