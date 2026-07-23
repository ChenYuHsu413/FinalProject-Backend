"""Control-mode state machine (後端資料規格書 §九)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.permissions import DASHBOARD_READ
from app.core.security import require_permission
from app.repositories.files.engine_repo import EngineFileRepository
from app.routers.engine.deps import NOT_FOUND_RESPONSES, get_engine_repo
from app.routers.engine.models import ControlModeStatus

router = APIRouter(tags=["engine:control-mode"], responses=NOT_FOUND_RESPONSES)


@router.get("/control-mode", response_model=ControlModeStatus)
def control_mode(
    scenario_id: str = Query(...),
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(DASHBOARD_READ)),
) -> dict:
    return repo.control_mode(scenario_id)
