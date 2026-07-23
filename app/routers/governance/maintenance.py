"""Maintenance report endpoints (design-backend §8).

* POST /maintenance-reports  — `maintenance.report`
* GET  /maintenance-reports  — `alarm.read`

Body is length-capped + NUL-checked (batch-2 input defense). Creating a report
with `alarm_id` also resolves that alarm.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.permissions import ALARM_READ, MAINTENANCE_REPORT
from app.core.security import Principal, require_permission
from app.core.validation import reject_nul
from app.events.deps import get_publisher
from app.events.publisher import EventPublisher
from app.repositories.pg.alarm_repo import MaintenanceRepository
from app.services.maintenance_service import MaintenanceService

router = APIRouter(
    tags=["maintenance"],
    responses={403: {"description": "role lacks permission"}, 404: {"description": "not found"}},
)


class MaintenanceReportIn(BaseModel):
    device: str = Field(max_length=64)
    actions_taken: list[str] = Field(min_length=1)
    result: str = Field(max_length=32)
    alarm_id: str | None = Field(default=None, max_length=64)
    attachments: list[str] | None = None

    @model_validator(mode="after")
    def _no_nul(self) -> MaintenanceReportIn:
        reject_nul(self.device)
        reject_nul(self.result)
        reject_nul(self.alarm_id)
        reject_nul(self.actions_taken)
        reject_nul(self.attachments)
        return self


class MaintenanceReportOut(BaseModel):
    report_id: str
    alarm_id: str | None
    device: str
    actions_taken: list
    result: str
    attachments: list | None
    residual_recovery_status: str | None
    created_by: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MaintenanceReportsPage(BaseModel):
    reports: list[MaintenanceReportOut]
    total: int
    page: int
    page_size: int


@router.post("/maintenance-reports", response_model=MaintenanceReportOut, status_code=201)
async def create_report(
    body: MaintenanceReportIn,
    session: AsyncSession = Depends(get_session),
    publisher: EventPublisher = Depends(get_publisher),
    principal: Principal = Depends(require_permission(MAINTENANCE_REPORT)),
) -> MaintenanceReportOut:
    report = await MaintenanceService(session, publisher).create(
        device=body.device,
        actions_taken=body.actions_taken,
        result=body.result,
        alarm_id=body.alarm_id,
        attachments=body.attachments,
        user_id=principal.user_id,
        role=principal.role,
        correlation_id=principal.correlation_id,
    )
    return MaintenanceReportOut.model_validate(report)


@router.get("/maintenance-reports", response_model=MaintenanceReportsPage)
async def list_reports(
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_permission(ALARM_READ)),
    device: str | None = None,
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=50, ge=1, le=500),
) -> MaintenanceReportsPage:
    rows, total = await MaintenanceRepository(session).list(
        device=device,
        date_from=date_from,
        date_to=date_to,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return MaintenanceReportsPage(
        reports=[MaintenanceReportOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
