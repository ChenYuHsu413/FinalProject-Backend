"""Fallback layer (後端資料規格書 §2.5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.permissions import DASHBOARD_READ
from app.core.security import require_permission
from app.domain.scenarios import ACTIVE_SCENARIOS
from app.repositories.files.engine_repo import EngineFileRepository
from app.routers.engine.deps import NOT_FOUND_RESPONSES, get_engine_repo
from app.routers.engine.models import FallbackEventsPage, FallbackStats

router = APIRouter(tags=["engine:fallback"], responses=NOT_FOUND_RESPONSES)


@router.get("/fallback/events", response_model=FallbackEventsPage)
def fallback_events(
    page: int = Query(default=1, ge=1, le=1_000_000),
    limit: int = Query(default=20, ge=1, le=500),
    scenario_id: str | None = Query(default=None),
    level: int | None = Query(default=None, ge=1, le=3),
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(DASHBOARD_READ)),
) -> dict:
    events = repo.fallback_events(scenario_id)
    if level is not None:
        events = [e for e in events if e.get("fallback_level") == level]
    total = len(events)
    start = (page - 1) * limit
    return {
        "events": events[start : start + limit],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.get("/fallback/stats", response_model=FallbackStats)
def fallback_stats(
    scenario_id: str = Query(default=ACTIVE_SCENARIOS[0]),
    hours: int = Query(default=24, ge=1),
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(DASHBOARD_READ)),
) -> dict:
    return repo.fallback_stats(scenario_id)
