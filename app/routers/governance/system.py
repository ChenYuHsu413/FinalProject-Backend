"""System status endpoints (design-backend §7).

GET /system/integrations — `system.settings` (admin). Reports dependency
connectivity/latency, `version_consistency`, and the PROMPT §7 `mock_mode` honesty
flag. Never 500s on a down dependency (degrades to `disconnected`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.permissions import SYSTEM_SETTINGS
from app.core.security import Principal, require_permission
from app.events.deps import get_publisher
from app.events.publisher import EventPublisher
from app.services.integrations_service import build_integrations

router = APIRouter(
    prefix="/system",
    tags=["system"],
    responses={403: {"description": "role lacks permission"}},
)


class ServiceStatus(BaseModel):
    name: str
    status: str
    latency_ms: int | None = None
    offset_ms: int | None = None


class VersionConsistency(BaseModel):
    verified: bool
    components: dict[str, str]


class IntegrationsOut(BaseModel):
    mock_mode: bool
    services: list[ServiceStatus]
    version_consistency: VersionConsistency
    checked_at: str


@router.get("/integrations", response_model=IntegrationsOut)
async def system_integrations(
    session: AsyncSession = Depends(get_session),
    publisher: EventPublisher = Depends(get_publisher),
    _: Principal = Depends(require_permission(SYSTEM_SETTINGS)),
) -> IntegrationsOut:
    data = await build_integrations(session, publisher)
    return IntegrationsOut(**data)
