"""Audit orchestration (design-backend.md §5).

`AuditService` wraps the repository with a session and is what every future
mutation calls to write its audit row. `record_denied_attempt` is the
recursion-safe path used by the trust-boundary middleware to log rejected
requests (bad token / missing headers / permission denied) for admin security
monitoring — it opens its own session and swallows all errors so an audit-write
failure can never break the response or trigger another audit write.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.repositories.pg.audit_repo import AuditRepository
from app.repositories.pg.models import AuditChainVerification, AuditEvent

logger = logging.getLogger("app.audit")

# Action taken when the trust boundary rejects a request.
ACTION_AUTHZ_DENIED = "authz.denied"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AuditRepository(session)

    async def record(
        self,
        *,
        action: str,
        user_id: str | None = None,
        role: str | None = None,
        correlation_id: str | None = None,
        source_ip: str | None = None,
        command_id: str | None = None,
        target_device: str | None = None,
        scenario_id: str | None = None,
        old_value: dict[str, Any] | None = None,
        new_value: dict[str, Any] | None = None,
        reason: str | None = None,
        result: str | None = None,
        model_version: str | None = None,
        mode: str | None = None,
        ts: datetime | None = None,
        proposed_at: datetime | None = None,
        approved_at: datetime | None = None,
        executed_at: datetime | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_id=str(uuid4()),
            ts=ts or _utcnow(),
            correlation_id=correlation_id,
            command_id=command_id,
            user_id=user_id,
            role=role,
            source_ip=source_ip,
            action=action,
            target_device=target_device,
            scenario_id=scenario_id,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            proposed_at=proposed_at,
            approved_at=approved_at,
            executed_at=executed_at,
            result=result,
            model_version=model_version,
            mode=mode,
        )
        await self.repo.append(event)
        await self.session.commit()
        return event

    async def list_events(self, **kwargs) -> tuple[list[AuditEvent], int]:
        return await self.repo.list_events(**kwargs)

    async def latest_verification(self) -> AuditChainVerification | None:
        return await self.repo.latest_verification()

    async def run_verification(self) -> AuditChainVerification:
        result = await self.repo.verify_full_chain()
        row = await self.repo.save_verification(result)
        await self.session.commit()
        return row


async def record_denied_attempt(
    *,
    reason: str,
    correlation_id: str | None,
    source_ip: str | None,
    method: str,
    path: str,
    user_id: str | None = None,
    role: str | None = None,
) -> None:
    """Best-effort audit of a rejected request. Never raises, never recurses.

    Opens an independent session (the middleware has no DI session) and swallows
    every error: an audit-write failure must not break the 4xx response nor
    trigger further auditing.
    """
    try:
        async with get_sessionmaker()() as session:
            service = AuditService(session)
            await service.record(
                action=ACTION_AUTHZ_DENIED,
                user_id=user_id,
                role=role,
                correlation_id=correlation_id,
                source_ip=source_ip,
                result="denied",
                reason=reason,
                new_value={"method": method, "path": path},
            )
    except Exception:  # noqa: BLE001 — deliberately swallow; do not re-audit.
        logger.warning("failed to record denied attempt for %s %s", method, path, exc_info=True)
