"""L1 real-time layer (後端資料規格書 §2.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.permissions import DASHBOARD_READ
from app.core.security import require_permission
from app.domain.scenarios import ACTIVE_SCENARIOS
from app.repositories.files.engine_repo import EngineFileRepository
from app.routers.engine.deps import NOT_FOUND_RESPONSES, get_engine_repo
from app.routers.engine.models import L1LatencyStats, L1Model, L1Realtime

router = APIRouter(tags=["engine:l1"], responses=NOT_FOUND_RESPONSES)

_DEFAULT = ACTIVE_SCENARIOS[0]


@router.get("/l1/realtime", response_model=L1Realtime)
def l1_realtime(
    scenario_id: str = Query(default=_DEFAULT),
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(DASHBOARD_READ)),
) -> dict:
    return repo.l1_realtime(scenario_id)


@router.get("/l1/latency", response_model=L1LatencyStats)
def l1_latency(
    scenario_id: str = Query(default=_DEFAULT),
    window_seconds: int = Query(default=60, ge=1),
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(DASHBOARD_READ)),
) -> dict:
    return repo.l1_latency(scenario_id)


@router.get("/l1/model", response_model=L1Model)
def l1_model(
    scenario_id: str = Query(...),
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(DASHBOARD_READ)),
) -> dict:
    return repo.l1_model(scenario_id)
