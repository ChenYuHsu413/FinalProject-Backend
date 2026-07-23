"""Residual monitoring / scheduling (後端資料規格書 §七)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.permissions import DASHBOARD_READ
from app.core.security import require_permission
from app.repositories.files.engine_repo import EngineFileRepository
from app.routers.engine.deps import NOT_FOUND_RESPONSES, get_engine_repo
from app.routers.engine.models import ResidualStatus

router = APIRouter(tags=["engine:residual"], responses=NOT_FOUND_RESPONSES)


@router.get("/residual/status", response_model=ResidualStatus)
def residual_status(
    scenario_id: str = Query(...),
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(DASHBOARD_READ)),
) -> dict:
    return repo.residual_status(scenario_id)
