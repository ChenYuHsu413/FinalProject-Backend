"""Audit repository — append-only writes + queries (design-backend.md §5).

Layer 1 of the append-only protection: this class exposes **no** update/delete
method. Appends are serialized with a Postgres transaction-level advisory lock so
concurrent writers cannot race on ``prev_hash`` and fork the chain.

The single source of truth for "what bytes get hashed" is ``_business_dict`` — it
is applied to the pre-insert instance (to compute the hash) and to rows read back
(to re-verify), so the two can never drift.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.audit import GENESIS_HASH, ChainVerification, compute_entry_hash, verify_chain
from app.repositories.pg.models import AuditChainVerification, AuditEvent

# Fixed key for pg_advisory_xact_lock — serializes all audit appends.
_AUDIT_LOCK_KEY = 727_2026


def _to_iso(dt: datetime | None) -> str | None:
    """Deterministic UTC ISO8601 with fixed microsecond width (hash stability).

    Fixed-width strftime (always 6 fractional digits + 'Z') means a zero-
    microsecond time and the DB round-trip both render identically.
    """
    if dt is None:
        return None
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _business_dict(ev: AuditEvent) -> dict[str, Any]:
    """Canonical business view of a row/instance — used for hashing AND verify."""
    return {
        "event_id": ev.event_id,
        "ts": _to_iso(ev.ts),
        "correlation_id": ev.correlation_id,
        "command_id": ev.command_id,
        "user_id": ev.user_id,
        "role": ev.role,
        "source_ip": ev.source_ip,
        "action": ev.action,
        "target_device": ev.target_device,
        "scenario_id": ev.scenario_id,
        "old_value": ev.old_value,
        "new_value": ev.new_value,
        "reason": ev.reason,
        "proposed_at": _to_iso(ev.proposed_at),
        "approved_at": _to_iso(ev.approved_at),
        "executed_at": _to_iso(ev.executed_at),
        "result": ev.result,
        "model_version": ev.model_version,
        "mode": ev.mode,
    }


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(self, event: AuditEvent) -> AuditEvent:
        """Serialize, chain, and insert one audit row. Caller commits."""
        # Transaction-scoped lock: released automatically at COMMIT/ROLLBACK.
        await self.session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _AUDIT_LOCK_KEY})

        prev_hash = await self._current_head_hash()
        event.prev_hash = prev_hash
        event.entry_hash = compute_entry_hash(prev_hash, _business_dict(event))
        self.session.add(event)
        await self.session.flush()
        return event

    async def _current_head_hash(self) -> str:
        row = await self.session.execute(
            select(AuditEvent.entry_hash).order_by(AuditEvent.id.desc()).limit(1)
        )
        head = row.scalar_one_or_none()
        return head or GENESIS_HASH

    async def list_events(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        restrict_to_user: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuditEvent], int]:
        """List events with filters applied **in SQL**.

        ``restrict_to_user`` enforces the operator "own entries only" rule at the
        query layer (design-backend §5.2) — never fetch-all-then-filter.
        """
        stmt = select(AuditEvent)
        count_stmt = select(func.count()).select_from(AuditEvent)

        conditions = []
        if restrict_to_user is not None:
            conditions.append(AuditEvent.user_id == restrict_to_user)
        if actor is not None:
            conditions.append(AuditEvent.user_id == actor)
        if action is not None:
            conditions.append(AuditEvent.action == action)
        if date_from is not None:
            conditions.append(AuditEvent.ts >= date_from)
        if date_to is not None:
            conditions.append(AuditEvent.ts <= date_to)

        for cond in conditions:
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)

        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(AuditEvent.id.desc()).limit(limit).offset(offset)
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, total

    async def iter_all_ordered(self) -> list[AuditEvent]:
        """All events oldest→newest, for full chain re-verification (worker)."""
        rows = await self.session.execute(select(AuditEvent).order_by(AuditEvent.id.asc()))
        return list(rows.scalars().all())

    async def verify_full_chain(self) -> ChainVerification:
        events = await self.iter_all_ordered()
        payload = []
        for ev in events:
            d = _business_dict(ev)
            d["prev_hash"] = ev.prev_hash
            d["entry_hash"] = ev.entry_hash
            payload.append(d)
        return verify_chain(payload)

    async def save_verification(self, result: ChainVerification) -> AuditChainVerification:
        row = AuditChainVerification(
            verified=result.verified,
            entries=result.entries,
            first_bad_position=result.first_bad_position,
            head_hash=result.head_hash,
            reason=result.reason,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def latest_verification(self) -> AuditChainVerification | None:
        row = await self.session.execute(
            select(AuditChainVerification).order_by(AuditChainVerification.id.desc()).limit(1)
        )
        return row.scalar_one_or_none()
