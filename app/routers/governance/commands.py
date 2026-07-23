"""Command endpoints (design-backend §3.2 + PROMPT §3 ruling #1).

POST returns **202** for a new command (body = submitted-semantics only, no
"completed" fields), or **200** for an idempotent replay (original command's
current state). In-progress conflict → 409. Timeout is never decided here.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.permissions import (
    CYCLE_START,
    CYCLE_STOP,
    DASHBOARD_READ,
    MODE_SWITCH,
    SAFETY_STOP_REQUEST,
)
from app.core.security import Principal, require_permission
from app.core.validation import reject_nul
from app.domain import commands as cmd_domain
from app.events.deps import get_publisher
from app.events.publisher import EventPublisher
from app.repositories.pg.command_repo import CommandRepository
from app.services.command_service import CommandService

router = APIRouter(
    tags=["commands"],
    responses={
        200: {"description": "idempotent replay — original command's current state"},
        403: {"description": "role lacks permission"},
        404: {"description": "command not found"},
        409: {"description": "in-progress conflict"},
    },
)


# --- request bodies (input-hardened) ----------------------------------------
class _CommandBody(BaseModel):
    device: str = Field(max_length=64)
    idempotency_key: str = Field(max_length=64)
    reason: str | None = Field(default=None, max_length=1000)
    params: dict | None = None

    @model_validator(mode="after")
    def _no_nul(self):
        reject_nul(self.device)
        reject_nul(self.idempotency_key)
        reject_nul(self.reason)
        reject_nul(self.params)
        return self


class CycleStartIn(_CommandBody):
    scenario_id: str | None = Field(default=None, max_length=64)


class CycleStopIn(_CommandBody):
    pass


class ModeIn(_CommandBody):
    target_mode: str = Field(max_length=32)

    @model_validator(mode="after")
    def _no_nul_mode(self):
        reject_nul(self.target_mode)
        return self


class EstopIn(_CommandBody):
    pass


# --- responses ---------------------------------------------------------------
class CommandSubmitOut(BaseModel):
    """202 body — submitted semantics ONLY. Deliberately no result/completed_at."""

    command_id: str
    status: str
    submitted_at: datetime
    confirm_timeout_s: int


class CommandOut(BaseModel):
    command_id: str
    command_type: str
    device: str
    scenario_id: str | None
    target_mode: str | None
    reason: str | None
    status: str
    confirm_timeout_s: int
    high_risk: bool
    operator: str | None
    result: str | None
    submitted_at: datetime
    accepted_at: datetime | None
    completed_at: datetime | None
    correlation_id: str | None

    model_config = {"from_attributes": True}


class CommandsPage(BaseModel):
    commands: list[CommandOut]
    total: int
    page: int
    page_size: int


async def _submit(
    *, service: CommandService, response: Response, principal: Principal, **kwargs
) -> CommandSubmitOut:
    cmd, created = await service.submit(
        operator=principal.user_id,
        role=principal.role,
        correlation_id=principal.correlation_id,
        **kwargs,
    )
    # 202 for a freshly submitted command; 200 for an idempotent replay.
    response.status_code = 202 if created else 200
    return CommandSubmitOut(
        command_id=cmd.command_id,
        status=cmd.status,
        submitted_at=cmd.submitted_at,
        confirm_timeout_s=cmd.confirm_timeout_s,
    )


@router.post("/commands/cycle/start", response_model=CommandSubmitOut, status_code=202)
async def cycle_start(
    body: CycleStartIn,
    response: Response,
    session: AsyncSession = Depends(get_session),
    publisher: EventPublisher = Depends(get_publisher),
    principal: Principal = Depends(require_permission(CYCLE_START)),
) -> CommandSubmitOut:
    return await _submit(
        service=CommandService(session, publisher),
        response=response,
        principal=principal,
        command_type=cmd_domain.CYCLE_START,
        device=body.device,
        idempotency_key=body.idempotency_key,
        reason=body.reason,
        params=body.params,
        scenario_id=body.scenario_id,
    )


@router.post("/commands/cycle/stop", response_model=CommandSubmitOut, status_code=202)
async def cycle_stop(
    body: CycleStopIn,
    response: Response,
    session: AsyncSession = Depends(get_session),
    publisher: EventPublisher = Depends(get_publisher),
    principal: Principal = Depends(require_permission(CYCLE_STOP)),
) -> CommandSubmitOut:
    return await _submit(
        service=CommandService(session, publisher),
        response=response,
        principal=principal,
        command_type=cmd_domain.CYCLE_STOP,
        device=body.device,
        idempotency_key=body.idempotency_key,
        reason=body.reason,
        params=body.params,
    )


@router.post("/commands/mode", response_model=CommandSubmitOut, status_code=202)
async def switch_mode(
    body: ModeIn,
    response: Response,
    session: AsyncSession = Depends(get_session),
    publisher: EventPublisher = Depends(get_publisher),
    principal: Principal = Depends(require_permission(MODE_SWITCH)),
) -> CommandSubmitOut:
    return await _submit(
        service=CommandService(session, publisher),
        response=response,
        principal=principal,
        command_type=cmd_domain.MODE_SWITCH,
        device=body.device,
        idempotency_key=body.idempotency_key,
        reason=body.reason,
        params=body.params,
        target_mode=body.target_mode,
    )


@router.post("/commands/estop-request", response_model=CommandSubmitOut, status_code=202)
async def estop_request(
    body: EstopIn,
    response: Response,
    session: AsyncSession = Depends(get_session),
    publisher: EventPublisher = Depends(get_publisher),
    principal: Principal = Depends(require_permission(SAFETY_STOP_REQUEST)),
) -> CommandSubmitOut:
    return await _submit(
        service=CommandService(session, publisher),
        response=response,
        principal=principal,
        command_type=cmd_domain.SAFETY_STOP_REQUEST,
        device=body.device,
        idempotency_key=body.idempotency_key,
        reason=body.reason,
        params=body.params,
    )


@router.get("/commands/{command_id}", response_model=CommandOut)
async def get_command(
    command_id: str,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_permission(DASHBOARD_READ)),
) -> CommandOut:
    cmd = await CommandRepository(session).get(command_id)
    if cmd is None:
        from app.core.errors import AppError

        raise AppError(code="NOT_FOUND", message="command not found", status_code=404)
    return CommandOut.model_validate(cmd)


@router.get("/commands", response_model=CommandsPage)
async def list_commands(
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_permission(DASHBOARD_READ)),
    device: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=50, ge=1, le=500),
) -> CommandsPage:
    rows, total = await CommandRepository(session).list(
        device=device, status=status, limit=page_size, offset=(page - 1) * page_size
    )
    return CommandsPage(
        commands=[CommandOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
