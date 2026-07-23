"""Ensemble decision status (後端資料規格書 §八)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.permissions import DASHBOARD_READ
from app.core.security import require_permission
from app.domain.scenarios import ACTIVE_SCENARIOS
from app.repositories.files.engine_repo import EngineFileRepository
from app.routers.engine.deps import NOT_FOUND_RESPONSES, get_engine_repo
from app.routers.engine.models import EnsembleStatus

router = APIRouter(tags=["engine:ensemble"], responses=NOT_FOUND_RESPONSES)


@router.get("/ensemble/status", response_model=EnsembleStatus)
def ensemble_status(
    scenario_id: str = Query(default=ACTIVE_SCENARIOS[0]),
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(DASHBOARD_READ)),
) -> dict:
    return repo.ensemble_status(scenario_id)
