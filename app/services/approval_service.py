"""Approval orchestration — the governance closed loop (design-backend §6).

The batch-7 crux: an approval is not just a DB state flip — approving a
`model_promotion` **writes into the engine layer** (rewrites `models.jsonl`) and
publishes `model:changed`. The rulings that shape this service:

* **同人禁核** (§6.2): ``decided_by != proposed_by`` → **403**. Enforced here at
  the service layer *and* at the permission layer (split propose/approve codes,
  D1.5a) — belt-and-suspenders so a future permission-table change cannot quietly
  remove the guard.
* **Decision ≠ application** (D7.3): the state machine decision (`approved`) and
  the side effect (models.jsonl rewrite / param five-check) are separate. If the
  side effect fails, the approval **stays `approved`** and the side effect is
  recorded `apply_failed` / `failed` + an alarm — we never roll back an approval
  to pretend it did not happen (audit truth).
* **Events** (§6.2, D6.6): `approval:new` / `approval:decided` on
  `ai_servo:governance`; `model:changed` on `ai_servo:l3_deploy` (NOT governance)
  — all best-effort §11 envelopes; a Redis outage never fails the mutation.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.permissions import APPROVE_CODE, has_permission
from app.core.settings import get_settings
from app.domain.approvals import (
    APPROVE,
    MODEL_PROMOTION,
    PARAM_TUNING,
    REJECT,
    SCENARIO_ACTIVATION,
    WITHDRAW,
    next_state,
)
from app.domain.param_tuning import check_param_tuning
from app.events import channels
from app.events.publisher import EventPublisher
from app.repositories.files.model_registry_repo import (
    ModelRegistryError,
    ModelRegistryFileRepository,
)
from app.repositories.pg.approval_repo import ApprovalRepository
from app.repositories.pg.models import Approval
from app.services.alarm_service import AlarmService
from app.services.audit_service import AuditService

logger = logging.getLogger("app.approvals")


def _now() -> datetime:
    return datetime.now(UTC)


def _new_approval_id() -> str:
    return f"APR-{uuid4().hex[:12]}"


def _to_event_payload(a: Approval) -> dict:
    return {
        "approval_id": a.approval_id,
        "type": a.type,
        "risk": a.risk,
        "state": a.state,
        "scenario_id": a.scenario_id,
        "device": a.device,
        "summary": a.summary,
        "proposed_by": a.proposed_by,
        "decided_by": a.decided_by,
        "side_effect_status": a.side_effect_status,
        "correlation_id": a.correlation_id,
    }


class ApprovalService:
    def __init__(self, session: AsyncSession, publisher: EventPublisher | None = None) -> None:
        self.session = session
        self.repo = ApprovalRepository(session)
        self.publisher = publisher

    async def _publish(self, channel: str, event_type: str, payload: dict, a: Approval) -> None:
        if self.publisher is None:
            return
        try:
            await self.publisher.publish(
                channel=channel,
                event_type=event_type,
                payload=payload,
                scenario_id=a.scenario_id,
                correlation_id=a.correlation_id,
            )
        except Exception:  # noqa: BLE001 — best-effort
            logger.warning("failed to publish %s for %s", event_type, a.approval_id, exc_info=True)

    # --- propose ------------------------------------------------------------
    async def propose(
        self,
        *,
        type: str,
        summary: dict | None,
        reason: str | None,
        risk: str,
        scenario_id: str | None,
        device: str | None,
        user_id: str | None,
        role: str | None,
        correlation_id: str | None,
    ) -> Approval:
        approval = Approval(
            approval_id=_new_approval_id(),
            type=type,
            risk=risk,
            state="pending",
            scenario_id=scenario_id,
            device=device,
            summary=summary,
            reason=reason,
            proposed_by=user_id,
            proposed_role=role,
            proposed_at=_now(),
            correlation_id=correlation_id,
        )
        await self.repo.add(approval)
        await AuditService(self.session).record(
            action=f"approval.propose:{type}",
            user_id=user_id,
            role=role,
            correlation_id=correlation_id,
            target_device=device,
            scenario_id=scenario_id,
            new_value={"approval_id": approval.approval_id, "risk": risk, "summary": summary},
            reason=reason,
            result="pending",
            proposed_at=approval.proposed_at,
        )
        await self._publish(
            channels.GOVERNANCE, "approval:new", _to_event_payload(approval), approval
        )
        return approval

    async def _get_or_404(self, approval_id: str) -> Approval:
        approval = await self.repo.get(approval_id)
        if approval is None:
            raise AppError(code="NOT_FOUND", message="approval not found", status_code=404)
        return approval

    def _check_can_decide(
        self, approval: Approval, *, user_id: str | None, role: str | None
    ) -> None:
        """同人禁核 (§6.2) + per-type approve-code defence (D1.5a). Both raise 403."""
        # 同人禁核: the decider must not be the proposer.
        if user_id is not None and approval.proposed_by == user_id:
            raise AppError(
                code="FORBIDDEN",
                message="same-person approval is not allowed (提出者不可自核)",
                status_code=403,
                details={"proposed_by": approval.proposed_by, "decided_by": user_id},
            )
        # Defence in depth: the role must hold the per-type approve code even though
        # the router already gated on approval.read (admin-only). Guards against a
        # future permission-table change silently widening who can decide.
        required = APPROVE_CODE.get(approval.type)
        if required is None or not has_permission(role or "", required):
            raise AppError(
                code="FORBIDDEN",
                message="role lacks the approve code for this approval type",
                status_code=403,
                details={"role": role, "required": required},
            )

    # --- approve ------------------------------------------------------------
    async def approve(
        self,
        approval_id: str,
        *,
        note: str | None,
        user_id: str | None,
        role: str | None,
        correlation_id: str | None,
    ) -> Approval:
        approval = await self._get_or_404(approval_id)
        self._check_can_decide(approval, user_id=user_id, role=role)
        # State machine: pending → approved; a double-approve (terminal) → 409.
        approval.state = next_state(approval.state, APPROVE)
        approval.decided_by = user_id
        approval.decided_role = role
        approval.decided_at = _now()
        approval.decision_note = note
        await AuditService(self.session).record(
            action=f"approval.approve:{approval.type}",
            user_id=user_id,
            role=role,
            correlation_id=correlation_id or approval.correlation_id,
            target_device=approval.device,
            scenario_id=approval.scenario_id,
            old_value={"state": "pending"},
            new_value={"state": approval.state, "approval_id": approval.approval_id},
            reason=note,
            result="approved",
            proposed_at=approval.proposed_at,
            approved_at=approval.decided_at,
        )
        await self._publish(
            channels.GOVERNANCE, "approval:decided", _to_event_payload(approval), approval
        )
        # Post-decision side effect (D7.3) — decision already committed above.
        await self._apply_side_effect(approval, user_id=user_id, role=role)
        return approval

    # --- reject / withdraw --------------------------------------------------
    async def reject(
        self,
        approval_id: str,
        *,
        note: str,
        user_id: str | None,
        role: str | None,
        correlation_id: str | None,
    ) -> Approval:
        approval = await self._get_or_404(approval_id)
        self._check_can_decide(approval, user_id=user_id, role=role)
        approval.state = next_state(approval.state, REJECT)  # 409 if already terminal
        approval.decided_by = user_id
        approval.decided_role = role
        approval.decided_at = _now()
        approval.decision_note = note
        await AuditService(self.session).record(
            action=f"approval.reject:{approval.type}",
            user_id=user_id,
            role=role,
            correlation_id=correlation_id or approval.correlation_id,
            target_device=approval.device,
            scenario_id=approval.scenario_id,
            old_value={"state": "pending"},
            new_value={"state": approval.state, "approval_id": approval.approval_id},
            reason=note,
            result="rejected",
        )
        await self._publish(
            channels.GOVERNANCE, "approval:decided", _to_event_payload(approval), approval
        )
        return approval

    async def withdraw(
        self,
        approval_id: str,
        *,
        user_id: str | None,
        role: str | None,
        correlation_id: str | None,
    ) -> Approval:
        """Proposer retracts a pending approval (D7.7 — spec has the state, not the endpoint)."""
        approval = await self._get_or_404(approval_id)
        # Only the proposer may withdraw their own proposal.
        if user_id is None or approval.proposed_by != user_id:
            raise AppError(
                code="FORBIDDEN",
                message="only the proposer may withdraw this approval",
                status_code=403,
                details={"proposed_by": approval.proposed_by},
            )
        approval.state = next_state(approval.state, WITHDRAW)  # 409 if already terminal
        approval.decided_by = user_id
        approval.decided_role = role
        approval.decided_at = _now()
        await AuditService(self.session).record(
            action=f"approval.withdraw:{approval.type}",
            user_id=user_id,
            role=role,
            correlation_id=correlation_id or approval.correlation_id,
            target_device=approval.device,
            scenario_id=approval.scenario_id,
            old_value={"state": "pending"},
            new_value={"state": approval.state, "approval_id": approval.approval_id},
            result="withdrawn",
        )
        await self._publish(
            channels.GOVERNANCE, "approval:decided", _to_event_payload(approval), approval
        )
        return approval

    # --- side effects -------------------------------------------------------
    async def _apply_side_effect(
        self, approval: Approval, *, user_id: str | None, role: str | None
    ) -> None:
        if approval.type == MODEL_PROMOTION:
            await self._apply_model_promotion(approval)
        elif approval.type == PARAM_TUNING:
            await self._apply_param_tuning(approval)
        elif approval.type == SCENARIO_ACTIVATION:
            # §6.2: scenario_activation enters Shadow, it does NOT go active directly.
            await self._set_side_effect(
                approval, "shadow", {"note": "entered shadow, not active (design-backend §6.2)"}
            )

    async def _set_side_effect(self, approval: Approval, status: str, detail: dict | None) -> None:
        approval.side_effect_status = status
        approval.side_effect_detail = detail
        await AuditService(self.session).record(
            action=f"approval.apply:{approval.type}",
            user_id=approval.decided_by,
            role=approval.decided_role,
            correlation_id=approval.correlation_id,
            target_device=approval.device,
            scenario_id=approval.scenario_id,
            new_value={
                "approval_id": approval.approval_id,
                "side_effect": status,
                **(detail or {}),
            },
            result=status,
            executed_at=_now(),
        )

    async def _apply_model_promotion(self, approval: Approval) -> None:
        """Rewrite models.jsonl (shadow→active) + publish model:changed (§6.2)."""
        summary = approval.summary or {}
        to_version = summary.get("to")
        scenario_id = approval.scenario_id
        registry = ModelRegistryFileRepository(get_settings().engine_data_dir)
        try:
            if not scenario_id or not to_version:
                raise ModelRegistryError("model_promotion summary missing scenario_id/to version")
            promoted = registry.promote(scenario_id=scenario_id, to_version=to_version)
        except ModelRegistryError as exc:
            # D7.3: approval stays `approved`; record apply_failed + raise an alarm.
            await self._set_side_effect(approval, "apply_failed", {"error": str(exc)})
            await AlarmService(self.session, self.publisher).raise_governance_alarm(
                device=approval.device or "AXIS-04",
                rule="model_promotion_apply_failed",
                severity="critical",
                scenario_id=scenario_id,
                correlation_id=approval.correlation_id,
                detail={"approval_id": approval.approval_id, "error": str(exc)},
            )
            return

        await self._set_side_effect(
            approval, "applied", {"active_version": to_version, "scenario": scenario_id}
        )
        # model:changed lives on ai_servo:l3_deploy, NOT governance (§6.2 / §9.3).
        await self._publish(
            channels.L3_DEPLOY,
            "model:changed",
            {
                "model_version": to_version,
                "scenario": scenario_id,
                "status": "active",
                "hash": promoted.get("file_hash_sha256"),
            },
            approval,
        )

    async def _apply_param_tuning(self, approval: Approval) -> None:
        """Post-approval five-check chain (design-frontend §11.3 / §6.2)."""
        summary = approval.summary or {}
        # device state: mock — the device is safe if known and not mid-cycle. Here
        # we treat a registered device as 'idle' (the mock has no live cycle state
        # tied to the approval); a real integration resolves the live drive state.
        device_state = "idle" if approval.device else None
        result = check_param_tuning(
            param=summary.get("param"),
            new_value=summary.get("new"),
            allowed_range=summary.get("allowed_range"),
            delta_pct=summary.get("delta_pct"),
            device_state=device_state,
        )
        if not result.ok:
            await self._set_side_effect(
                approval, "failed", {"failed_check": result.failed_check, "reason": result.reason}
            )
            await AlarmService(self.session, self.publisher).raise_governance_alarm(
                device=approval.device or "AXIS-04",
                rule="param_tuning_check_failed",
                severity="warning",
                scenario_id=approval.scenario_id,
                correlation_id=approval.correlation_id,
                detail={"approval_id": approval.approval_id, "failed_check": result.failed_check},
            )
            return
        await self._set_side_effect(
            approval, "applied", {"param": summary.get("param"), "new": summary.get("new")}
        )
