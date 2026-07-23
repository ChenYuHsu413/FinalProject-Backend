"""Alarm endpoints (design-backend §4.2).

* GET  /alarms, /alarms/{id}      — `alarm.read`
* POST /alarms/{id}/ack           — `alarm.ack` (operator + engineer; admin is
                                     read-only per frontend §6.3 → 403 + audited)
* POST /alarms/{id}/resolve       — `alarm.ack`

ack `note` is length-capped + NUL-checked (batch-2 input defense). ack/resolve on
an illegal state → 409 (InvalidAlarmTransition handler).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.permissions import ALARM_ACK, ALARM_READ
from app.core.security import Principal, require_permission
from app.core.validation import reject_nul
from app.events.deps import get_publisher
from app.events.publisher import EventPublisher
from app.repositories.pg.alarm_repo import AlarmRepository
from app.services.alarm_service import AlarmService

router = APIRouter(
    prefix="/alarms",
    tags=["alarms"],
    responses={
        403: {"description": "role lacks permission"},
        404: {"description": "alarm not found"},
        409: {"description": "illegal transition"},
    },
)


class AckBody(BaseModel):
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _no_nul(self) -> AckBody:
        reject_nul(self.note)
        return self


class ResolveBody(BaseModel):
    maintenance_report_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _no_nul(self) -> ResolveBody:
        reject_nul(self.maintenance_report_id)
        return self


class AlarmOut(BaseModel):
    alarm_id: str
    severity: str
    device: str
    scenario_id: str | None
    rule: str
    status: str
    raised_at: datetime
    ack_by: str | None
    ack_at: datetime | None
    ack_note: str | None
    resolved_at: datetime | None
    root_cause_ref: str | None
    maintenance_report_id: str | None
    correlation_id: str | None

    model_config = {"from_attributes": True}


class AlarmsPage(BaseModel):
    alarms: list[AlarmOut]
    total: int
    page: int
    page_size: int


@router.get("", response_model=AlarmsPage)
async def list_alarms(
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_permission(ALARM_READ)),
    status: str | None = None,
    severity: str | None = None,
    device: str | None = None,
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=50, ge=1, le=500),
) -> AlarmsPage:
    rows, total = await AlarmRepository(session).list(
        status=status,
        severity=severity,
        device=device,
        date_from=date_from,
        date_to=date_to,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return AlarmsPage(
        alarms=[AlarmOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{alarm_id}", response_model=AlarmOut)
async def get_alarm(
    alarm_id: str,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_permission(ALARM_READ)),
) -> AlarmOut:
    alarm = await AlarmRepository(session).get(alarm_id)
    if alarm is None:
        from app.core.errors import AppError

        raise AppError(code="NOT_FOUND", message="alarm not found", status_code=404)
    return AlarmOut.model_validate(alarm)


@router.post("/{alarm_id}/ack", response_model=AlarmOut)
async def ack_alarm(
    alarm_id: str,
    body: AckBody,
    session: AsyncSession = Depends(get_session),
    publisher: EventPublisher = Depends(get_publisher),
    principal: Principal = Depends(require_permission(ALARM_ACK)),
) -> AlarmOut:
    alarm = await AlarmService(session, publisher).ack(
        alarm_id,
        user_id=principal.user_id,
        role=principal.role,
        note=body.note,
        correlation_id=principal.correlation_id,
    )
    return AlarmOut.model_validate(alarm)


@router.post("/{alarm_id}/resolve", response_model=AlarmOut)
async def resolve_alarm(
    alarm_id: str,
    body: ResolveBody,
    session: AsyncSession = Depends(get_session),
    publisher: EventPublisher = Depends(get_publisher),
    principal: Principal = Depends(require_permission(ALARM_ACK)),
) -> AlarmOut:
    alarm = await AlarmService(session, publisher).resolve(
        alarm_id,
        user_id=principal.user_id,
        role=principal.role,
        maintenance_report_id=body.maintenance_report_id,
        correlation_id=principal.correlation_id,
    )
    return AlarmOut.model_validate(alarm)
