"""L3 AutoML layer (後端資料規格書 §2.3)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.permissions import MODEL_READ
from app.core.security import require_permission
from app.repositories.files.engine_repo import EngineFileRepository
from app.routers.engine.deps import NOT_FOUND_RESPONSES, get_engine_repo
from app.routers.engine.models import L3Latest, L3Models, L3Shadow

router = APIRouter(tags=["engine:l3"], responses=NOT_FOUND_RESPONSES)


@router.get("/l3/latest", response_model=L3Latest)
def l3_latest(
    scenario_id: str = Query(...),
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(MODEL_READ)),
) -> dict:
    return repo.l3_latest(scenario_id)


@router.get("/l3/shadow", response_model=L3Shadow)
def l3_shadow(
    scenario_id: str = Query(...),
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(MODEL_READ)),
) -> dict:
    return repo.l3_shadow(scenario_id)


@router.get("/l3/models", response_model=L3Models)
def l3_models(
    scenario_id: str = Query(...),
    status: str | None = Query(default=None),
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(MODEL_READ)),
) -> dict:
    return {"scenario_id": scenario_id, "models": repo.l3_models(scenario_id, status)}
