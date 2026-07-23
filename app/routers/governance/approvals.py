"""Approval endpoints (design-backend §6.2).

* GET  /approvals?state=&type=&risk=   — `approval.read` (admin)
* GET  /approvals/summary              — `approval.read` (admin 待辦計數卡)
* GET  /approvals/{id}                 — `approval.read` (detail; gap-fill D7.7)
* POST /approvals                      — propose; per-type propose code (engineer)
* POST /approvals/{id}/approve         — approve; admin + 同人禁核 (§6.2)
* POST /approvals/{id}/reject          — reject (note required)
* POST /approvals/{id}/withdraw        — proposer withdraws (gap-fill D7.7)

Propose requires the per-type **propose** code (`model.promote.propose` etc.,
D1.5a) — admin holds none, so an admin propose is a 403 and the path effectively
does not exist for admin (pre-check #1). Approve/reject are coarse-gated on
`approval.read` (admin-only) and the service enforces `decided_by != proposed_by`
(§6.2 → 403) plus the per-type approve code (defence in depth).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import AppError
from app.core.permissions import APPROVAL_READ, PROPOSE_CODE
from app.core.security import Principal, enforce_permission, require_permission, require_role
from app.core.validation import reject_nul
from app.domain.approvals import (
    APPROVAL_TYPES,
    MODEL_PROMOTION,
    PARAM_TUNING,
)
from app.events.deps import get_publisher
from app.events.publisher import EventPublisher
from app.repositories.pg.approval_repo import ApprovalRepository
from app.services.approval_service import ApprovalService

router = APIRouter(
    tags=["approvals"],
    responses={
        403: {"description": "role lacks permission / same-person approval"},
        404: {"description": "approval not found"},
        409: {"description": "illegal state transition (already decided)"},
    },
)

_RISKS = {"low", "medium", "high"}

# Minimum summary keys per type so the stored/rendered summary is §6.1-faithful.
_REQUIRED_SUMMARY_KEYS: dict[str, set[str]] = {
    MODEL_PROMOTION: {"to"},
    PARAM_TUNING: {"param", "new", "allowed_range", "delta_pct"},
}


class ProposeIn(BaseModel):
    type: str = Field(max_length=32)
    risk: str = "low"
    scenario_id: str | None = Field(default=None, max_length=64)
    device: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=1000)
    summary: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> ProposeIn:
        reject_nul(self.scenario_id)
        reject_nul(self.device)
        reject_nul(self.reason)
        reject_nul(self.summary)
        if self.type not in APPROVAL_TYPES:
            raise ValueError(f"unknown approval type: {self.type!r}")
        if self.risk not in _RISKS:
            raise ValueError(f"risk must be one of {sorted(_RISKS)}")
        missing = _REQUIRED_SUMMARY_KEYS.get(self.type, set()) - set(self.summary)
        if missing:
            raise ValueError(f"summary missing keys for {self.type}: {sorted(missing)}")
        # Targets the side effect needs (§6.2): promotion needs a scenario, param a device.
        if self.type == MODEL_PROMOTION and not self.scenario_id:
            raise ValueError("model_promotion requires scenario_id")
        if self.type == PARAM_TUNING and not self.device:
            raise ValueError("param_tuning requires device")
        return self


class DecisionBody(BaseModel):
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _no_nul(self) -> DecisionBody:
        reject_nul(self.note)
        return self


class RejectBody(BaseModel):
    note: str = Field(max_length=1000)  # required on reject (§6.2)

    @model_validator(mode="after")
    def _no_nul(self) -> RejectBody:
        reject_nul(self.note)
        return self


class ApprovalOut(BaseModel):
    approval_id: str
    type: str
    risk: str
    state: str
    scenario_id: str | None
    device: str | None
    summary: dict | None
    reason: str | None
    proposed_by: str | None
    proposed_role: str | None
    proposed_at: datetime
    decided_by: str | None
    decided_role: str | None
    decided_at: datetime | None
    decision_note: str | None
    side_effect_status: str | None
    side_effect_detail: dict | None
    correlation_id: str | None

    model_config = {"from_attributes": True}


class ApprovalsPage(BaseModel):
    approvals: list[ApprovalOut]
    total: int
    page: int
    page_size: int


class ApprovalSummaryOut(BaseModel):
    by_type: dict[str, int]
    total: int
    oldest_wait_s: int


@router.get("/approvals", response_model=ApprovalsPage)
async def list_approvals(
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_permission(APPROVAL_READ)),
    state: str | None = None,
    type: str | None = None,
    risk: str | None = None,
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=50, ge=1, le=500),
) -> ApprovalsPage:
    rows, total = await ApprovalRepository(session).list(
        state=state, type=type, risk=risk, limit=page_size, offset=(page - 1) * page_size
    )
    return ApprovalsPage(
        approvals=[ApprovalOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/approvals/summary", response_model=ApprovalSummaryOut)
async def approvals_summary(
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_permission(APPROVAL_READ)),
) -> ApprovalSummaryOut:
    return ApprovalSummaryOut(**await ApprovalRepository(session).summary())


@router.get("/approvals/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    approval_id: str,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_permission(APPROVAL_READ)),
) -> ApprovalOut:
    approval = await ApprovalRepository(session).get(approval_id)
    if approval is None:
        raise AppError(code="NOT_FOUND", message="approval not found", status_code=404)
    return ApprovalOut.model_validate(approval)


@router.post("/approvals", response_model=ApprovalOut, status_code=201)
async def propose_approval(
    body: ProposeIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    publisher: EventPublisher = Depends(get_publisher),
    principal: Principal = Depends(require_role()),
) -> ApprovalOut:
    # Per-type propose code (D1.5a). Admin holds none → 403 (pre-check #1).
    await enforce_permission(request, principal, PROPOSE_CODE[body.type])
    approval = await ApprovalService(session, publisher).propose(
        type=body.type,
        summary=body.summary,
        reason=body.reason,
        risk=body.risk,
        scenario_id=body.scenario_id,
        device=body.device,
        user_id=principal.user_id,
        role=principal.role,
        correlation_id=principal.correlation_id,
    )
    return ApprovalOut.model_validate(approval)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalOut)
async def approve_approval(
    approval_id: str,
    body: DecisionBody,
    session: AsyncSession = Depends(get_session),
    publisher: EventPublisher = Depends(get_publisher),
    principal: Principal = Depends(require_permission(APPROVAL_READ)),
) -> ApprovalOut:
    approval = await ApprovalService(session, publisher).approve(
        approval_id,
        note=body.note,
        user_id=principal.user_id,
        role=principal.role,
        correlation_id=principal.correlation_id,
    )
    return ApprovalOut.model_validate(approval)


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalOut)
async def reject_approval(
    approval_id: str,
    body: RejectBody,
    session: AsyncSession = Depends(get_session),
    publisher: EventPublisher = Depends(get_publisher),
    principal: Principal = Depends(require_permission(APPROVAL_READ)),
) -> ApprovalOut:
    approval = await ApprovalService(session, publisher).reject(
        approval_id,
        note=body.note,
        user_id=principal.user_id,
        role=principal.role,
        correlation_id=principal.correlation_id,
    )
    return ApprovalOut.model_validate(approval)


@router.post("/approvals/{approval_id}/withdraw", response_model=ApprovalOut)
async def withdraw_approval(
    approval_id: str,
    session: AsyncSession = Depends(get_session),
    publisher: EventPublisher = Depends(get_publisher),
    principal: Principal = Depends(require_role()),
) -> ApprovalOut:
    approval = await ApprovalService(session, publisher).withdraw(
        approval_id,
        user_id=principal.user_id,
        role=principal.role,
        correlation_id=principal.correlation_id,
    )
    return ApprovalOut.model_validate(approval)
