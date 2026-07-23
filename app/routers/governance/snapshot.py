"""Dashboard snapshot endpoint (design-backend.md §2).

Field names mirror §2 exactly (the Flask normalizer depends on them). `device`
unknown → 404 (DeviceNotFound handler). Requires `dashboard.read`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.permissions import DASHBOARD_READ
from app.core.security import require_permission
from app.domain.devices import DEFAULT_DEVICE
from app.services.snapshot_service import build_snapshot

router = APIRouter(tags=["snapshot"], responses={404: {"description": "unknown device"}})


class DeviceBlock(BaseModel):
    id: str
    cell: str
    line: str


class ScenarioBlock(BaseModel):
    id: str
    name: str


class CycleBlock(BaseModel):
    id: str
    state: str
    started_at: str
    elapsed_s: int


class DvBlock(BaseModel):
    value: float
    threshold: float
    delta_5min: float
    status: str


class ResidualBlock(BaseModel):
    value: float
    threshold: float
    sigma3_margin_pct: float
    status: str


class AlarmsBlock(BaseModel):
    active: int
    critical: int
    warning: int
    oldest_pending_s: int


class ModelBlock(BaseModel):
    active_version: str
    scenario: str


class PipelineBlock(BaseModel):
    stages: list[dict]
    e2e_latency_ms: float
    sla_ms: float


class Snapshot(BaseModel):
    ts: str
    schema_version: str
    device: DeviceBlock
    scenario: ScenarioBlock
    control_mode: str
    system_status: str
    health_pct: int
    cycle: CycleBlock
    dv: DvBlock
    residual: ResidualBlock
    alarms: AlarmsBlock
    model: ModelBlock
    pipeline: PipelineBlock
    health_cards: dict[str, dict]


@router.get("/ui/snapshot", response_model=Snapshot)
async def ui_snapshot(
    session: AsyncSession = Depends(get_session),
    device: str = Query(default=DEFAULT_DEVICE),
    _=Depends(require_permission(DASHBOARD_READ)),
) -> dict:
    return await build_snapshot(session, device)
