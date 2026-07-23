"""SHAP diagnosis layer (後端資料規格書 §2.4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.permissions import DASHBOARD_READ
from app.core.security import require_permission
from app.repositories.files.engine_repo import EngineFileRepository
from app.routers.engine.deps import NOT_FOUND_RESPONSES, get_engine_repo
from app.routers.engine.models import ShapDiagnosis, ShapSummary

router = APIRouter(tags=["engine:shap"], responses=NOT_FOUND_RESPONSES)


@router.get("/shap/diagnosis", response_model=ShapDiagnosis)
def shap_diagnosis(
    scenario_id: str = Query(...),
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(DASHBOARD_READ)),
) -> dict:
    return repo.shap_diagnosis(scenario_id)


@router.get("/shap/summary", response_model=ShapSummary)
def shap_summary(
    scenario_id: str = Query(...),
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(DASHBOARD_READ)),
) -> dict:
    return repo.shap_summary(scenario_id)
