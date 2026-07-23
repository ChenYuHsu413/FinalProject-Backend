"""L2 fine-tune layer (後端資料規格書 §2.2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.permissions import DASHBOARD_READ
from app.core.security import require_permission
from app.repositories.files.engine_repo import EngineFileRepository
from app.routers.engine.deps import NOT_FOUND_RESPONSES, get_engine_repo
from app.routers.engine.models import L2Latest, L2Trend

router = APIRouter(tags=["engine:l2"], responses=NOT_FOUND_RESPONSES)


@router.get("/l2/latest", response_model=L2Latest)
def l2_latest(
    scenario_id: str = Query(...),
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(DASHBOARD_READ)),
) -> dict:
    return repo.l2_latest(scenario_id)


@router.get("/l2/trend", response_model=L2Trend)
def l2_trend(
    scenario_id: str = Query(...),
    hours: int = Query(default=1, ge=1),
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(DASHBOARD_READ)),
) -> dict:
    return repo.l2_trend(scenario_id)
