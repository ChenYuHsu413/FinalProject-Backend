"""Command orchestration (design-backend §3 + PROMPT §3 ruling #1).

Key rules:
* **202 = submitted only.** The submit path never presumes acceptance/completion.
* **Idempotency** is enforced at the DB layer on (command_type, device,
  idempotency_key): a duplicate returns the ORIGINAL command (HTTP 200), and a
  concurrent duplicate that loses the unique-constraint race is caught and also
  returns the original — NOT a 409 (D6.2). This is distinct from an in-progress
  **conflict** (cycle already running + start → 409).
* **timeout is decided only by the worker** (`mark_timeout`), never here.
* `command:status` publishes on every transition; `mode:changed` only when a
  `mode.switch` command reaches `completed` (ruling #1).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.domain.commands import (
    ACCEPTED,
    COMPLETED,
    CYCLE_START,
    FAILED,
    MODE_SWITCH,
    SAFETY_STOP_REQUEST,
    TIMEOUT,
    transition,
)
from app.events import channels
from app.events.publisher import EventPublisher
from app.repositories.pg.command_repo import CommandRepository
from app.repositories.pg.models import Command
from app.services.audit_service import AuditService

logger = logging.getLogger("app.commands")

_DEFAULT_TIMEOUT_S = 10
_ESTOP_TIMEOUT_S = 5  # E-Stop request: shorter confirm window (design-backend §3.1)


def _now() -> datetime:
    return datetime.now(UTC)


def _new_command_id() -> str:
    return f"CMD-{uuid4().hex[:12]}"


def _status_payload(cmd: Command) -> dict:
    return {
        "command_id": cmd.command_id,
        "command_type": cmd.command_type,
        "device": cmd.device,
        "status": cmd.status,
        "operator": cmd.operator,
        "reason": cmd.reason,
        "high_risk": cmd.high_risk,
        "correlation_id": cmd.correlation_id,
    }


class CommandService:
    def __init__(self, session: AsyncSession, publisher: EventPublisher | None = None) -> None:
        self.session = session
        self.repo = CommandRepository(session)
        self.publisher = publisher

    async def _publish(self, event_type: str, payload: dict, cmd: Command) -> None:
        if self.publisher is None:
            return
        try:
            await self.publisher.publish(
                channel=channels.COMMAND,
                event_type=event_type,
                payload=payload,
                scenario_id=cmd.scenario_id,
                correlation_id=cmd.correlation_id,
            )
        except Exception:  # noqa: BLE001 — best-effort
            logger.warning("failed to publish %s for %s", event_type, cmd.command_id, exc_info=True)

    async def submit(
        self,
        *,
        command_type: str,
        device: str,
        idempotency_key: str,
        operator: str | None,
        role: str | None,
        correlation_id: str | None,
        reason: str | None = None,
        params: dict | None = None,
        scenario_id: str | None = None,
        target_mode: str | None = None,
    ) -> tuple[Command, bool]:
        """Submit a command. Returns (command, created). created=False for a duplicate."""
        existing = await self.repo.get_by_idempotency(command_type, device, idempotency_key)
        if existing is not None:
            return existing, False  # idempotent replay → original current state, HTTP 200

        # In-progress conflict is distinct from idempotency (D6.2).
        if command_type == CYCLE_START and await self.repo.is_cycle_running(device):
            raise AppError(
                code="CONFLICT",
                message="a cycle is already running on this device",
                status_code=409,
            )

        high_risk = command_type == SAFETY_STOP_REQUEST
        cmd = Command(
            command_id=_new_command_id(),
            command_type=command_type,
            device=device,
            scenario_id=scenario_id,
            target_mode=target_mode,
            reason=reason,
            params=params,
            idempotency_key=idempotency_key,
            status="submitted",
            confirm_timeout_s=_ESTOP_TIMEOUT_S if high_risk else _DEFAULT_TIMEOUT_S,
            high_risk=high_risk,
            operator=operator,
            role=role,
            correlation_id=correlation_id,
            submitted_at=_now(),
        )
        try:
            await self.repo.add(cmd)
        except IntegrityError:
            # Concurrent duplicate lost the unique-constraint race → return original.
            await self.session.rollback()
            existing = await self.repo.get_by_idempotency(command_type, device, idempotency_key)
            if existing is not None:
                return existing, False
            raise

        await AuditService(self.session).record(
            action=command_type,
            command_id=cmd.command_id,
            user_id=operator,
            role=role,
            correlation_id=correlation_id,
            target_device=device,
            scenario_id=scenario_id,
            reason=reason,
            result="submitted",
            new_value={"high_risk": high_risk, "target_mode": target_mode},
        )
        await self._publish("command:status", _status_payload(cmd), cmd)
        return cmd, True

    async def _get_or_404(self, command_id: str) -> Command:
        cmd = await self.repo.get(command_id)
        if cmd is None:
            raise AppError(code="NOT_FOUND", message="command not found", status_code=404)
        return cmd

    async def _transition(self, cmd: Command, target: str, *, result: str | None = None) -> Command:
        cmd.status = transition(cmd.status, target)  # raises InvalidCommandTransition -> 409
        if target == ACCEPTED:
            cmd.accepted_at = _now()
        elif target in (COMPLETED, FAILED, TIMEOUT):
            cmd.completed_at = _now()
        if result is not None:
            cmd.result = result
        await AuditService(self.session).record(
            action=f"{cmd.command_type}:{target}",
            command_id=cmd.command_id,
            user_id=cmd.operator,
            role=cmd.role,
            correlation_id=cmd.correlation_id,
            target_device=cmd.device,
            scenario_id=cmd.scenario_id,
            result=target,
        )
        await self._publish("command:status", _status_payload(cmd), cmd)
        # mode:changed only when a mode command completes (ruling #1).
        if target == COMPLETED and cmd.command_type == MODE_SWITCH:
            await self._publish(
                "mode:changed",
                {
                    "device": cmd.device,
                    "to_mode": cmd.target_mode,
                    "operator": cmd.operator,
                    "reason": cmd.reason,
                    "correlation_id": cmd.correlation_id,
                },
                cmd,
            )
        return cmd

    async def accept(self, command_id: str) -> Command:
        return await self._transition(await self._get_or_404(command_id), ACCEPTED)

    async def complete(self, command_id: str, *, result: str = "ok") -> Command:
        return await self._transition(await self._get_or_404(command_id), COMPLETED, result=result)

    async def fail(self, command_id: str, *, result: str = "failed") -> Command:
        return await self._transition(await self._get_or_404(command_id), FAILED, result=result)

    async def mark_timeout(self, cmd: Command) -> Command:
        """Worker-only: mark a command timed out (terminal, no success/failure presumed)."""
        return await self._transition(cmd, TIMEOUT, result="timeout")
