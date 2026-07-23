"""Audit endpoints (design-backend.md §5.2).

* ``POST /audit/events`` — service-token only; Flask deposits its own events
  (login/logout/lockout). Identity is in the body, so this path is exempt from
  the X-User-* mutation requirement (see security.py + DECISIONS D2.4).
* ``GET /audit/events`` — ``audit.read``; operators are restricted to their own
  entries **in SQL** (design-backend §5.2).
* ``GET /audit/chain/verify`` — ``audit.read``; returns the worker's latest
  re-verification result (never a live full recompute — DECISIONS D2.3).
* ``GET /audit/export`` — ``audit.export`` (admin only); CSV dump.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.permissions import AUDIT_EXPORT, AUDIT_READ, OPERATOR
from app.core.security import Principal, get_principal, require_permission
from app.services.audit_service import AuditService

router = APIRouter(prefix="/audit", tags=["audit"])


def _reject_nul(value: object) -> None:
    """Recursively reject NUL bytes — PostgreSQL text/JSONB cannot store \\u0000.

    Without this, a string containing NUL passes Pydantic but blows up at INSERT
    with an undocumented 500 (found by schemathesis fuzzing). Checks dict keys as
    well as values.
    """
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError("NUL bytes are not allowed")
    elif isinstance(value, dict):
        for k, v in value.items():
            _reject_nul(k)
            _reject_nul(v)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_nul(item)


# --- Schemas -----------------------------------------------------------------
class AuditEventIn(BaseModel):
    # max_length mirrors the DB column widths (app/repositories/pg/models.py), so
    # an overlong value fails with 422 instead of a 500 from a varchar overflow.
    action: str = Field(max_length=64)
    user_id: str | None = Field(default=None, max_length=128)
    role: str | None = Field(default=None, max_length=32)
    source_ip: str | None = Field(default=None, max_length=64)
    command_id: str | None = Field(default=None, max_length=64)
    target_device: str | None = Field(default=None, max_length=64)
    scenario_id: str | None = Field(default=None, max_length=64)
    old_value: dict | None = None
    new_value: dict | None = None
    reason: str | None = None  # maps to TEXT (no length cap)
    result: str | None = Field(default=None, max_length=32)
    model_version: str | None = Field(default=None, max_length=64)
    mode: str | None = Field(default=None, max_length=32)
    ts: datetime | None = None
    proposed_at: datetime | None = None
    approved_at: datetime | None = None
    executed_at: datetime | None = None

    @model_validator(mode="after")
    def _no_nul(self) -> AuditEventIn:
        for value in (
            self.action,
            self.user_id,
            self.role,
            self.source_ip,
            self.command_id,
            self.target_device,
            self.scenario_id,
            self.result,
            self.model_version,
            self.mode,
            self.reason,
            self.old_value,
            self.new_value,
        ):
            _reject_nul(value)
        return self


class AuditEventOut(BaseModel):
    event_id: str
    entry_hash: str
    prev_hash: str
    action: str
    ts: datetime


class AuditEventRecord(BaseModel):
    id: int
    event_id: str
    ts: datetime
    created_at: datetime
    correlation_id: str | None
    command_id: str | None
    user_id: str | None
    role: str | None
    source_ip: str | None
    action: str
    target_device: str | None
    scenario_id: str | None
    old_value: dict | None
    new_value: dict | None
    reason: str | None
    result: str | None
    model_version: str | None
    mode: str | None
    prev_hash: str
    entry_hash: str

    model_config = {"from_attributes": True}


class AuditEventsPage(BaseModel):
    events: list[AuditEventRecord]
    total: int
    page: int
    page_size: int


class ChainVerifyOut(BaseModel):
    verified: bool | None = Field(description="null until the worker has run once")
    checked_at: datetime | None = None
    entries: int | None = None
    first_bad_position: int | None = None
    reason: str | None = None


# --- Endpoints ---------------------------------------------------------------
@router.post("/events", response_model=AuditEventOut, status_code=201)
async def post_event(
    payload: AuditEventIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> AuditEventOut:
    service = AuditService(session)
    source_ip = payload.source_ip or (request.client.host if request.client else None)
    event = await service.record(
        action=payload.action,
        user_id=payload.user_id,
        role=payload.role,
        correlation_id=principal.correlation_id,
        source_ip=source_ip,
        command_id=payload.command_id,
        target_device=payload.target_device,
        scenario_id=payload.scenario_id,
        old_value=payload.old_value,
        new_value=payload.new_value,
        reason=payload.reason,
        result=payload.result,
        model_version=payload.model_version,
        mode=payload.mode,
        ts=payload.ts,
        proposed_at=payload.proposed_at,
        approved_at=payload.approved_at,
        executed_at=payload.executed_at,
    )
    return AuditEventOut(
        event_id=event.event_id,
        entry_hash=event.entry_hash,
        prev_hash=event.prev_hash,
        action=event.action,
        ts=event.ts,
    )


@router.get("/events", response_model=AuditEventsPage)
async def list_events(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_permission(AUDIT_READ)),
    actor: str | None = None,
    action: str | None = None,
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
    # Cap page so OFFSET = (page-1)*page_size cannot overflow int64 → DB 500.
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=50, ge=1, le=500),
) -> AuditEventsPage:
    # Operators may only see their own entries — enforced in SQL (§5.2).
    restrict_to_user = principal.user_id if principal.role == OPERATOR else None
    rows, total = await AuditService(session).list_events(
        actor=actor,
        action=action,
        date_from=date_from,
        date_to=date_to,
        restrict_to_user=restrict_to_user,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return AuditEventsPage(
        events=[AuditEventRecord.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/chain/verify", response_model=ChainVerifyOut)
async def verify_chain(
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_permission(AUDIT_READ)),
) -> ChainVerifyOut:
    latest = await AuditService(session).latest_verification()
    if latest is None:
        return ChainVerifyOut(verified=None, reason="pending first verification")
    return ChainVerifyOut(
        verified=latest.verified,
        checked_at=latest.checked_at,
        entries=latest.entries,
        first_bad_position=latest.first_bad_position,
        reason=latest.reason,
    )


@router.get(
    "/export",
    # Declare the CSV media type so schemathesis' content-type conformance check
    # matches what we actually return (the default response_model would imply JSON).
    responses={200: {"content": {"text/csv": {}}, "description": "CSV export"}},
)
async def export_events(
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_permission(AUDIT_EXPORT)),
    format: str = Query(default="csv"),
) -> Response:
    rows, _total = await AuditService(session).list_events(limit=100_000, offset=0)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "event_id",
            "ts",
            "user_id",
            "role",
            "action",
            "target_device",
            "scenario_id",
            "result",
            "correlation_id",
            "prev_hash",
            "entry_hash",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.id,
                r.event_id,
                r.ts.isoformat() if r.ts else "",
                r.user_id or "",
                r.role or "",
                r.action,
                r.target_device or "",
                r.scenario_id or "",
                r.result or "",
                r.correlation_id or "",
                r.prev_hash,
                r.entry_hash,
            ]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_events.csv"},
    )
