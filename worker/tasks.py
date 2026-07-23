"""Worker tasks. Kept as plain async functions so they are directly testable."""

from __future__ import annotations

from typing import Any

from app.core.db import get_sessionmaker
from app.services.audit_service import AuditService


async def reverify_audit_chain(ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """Recompute the whole audit chain and persist the result.

    This is the ONLY place a full recompute happens; ``GET /audit/chain/verify``
    just reads the latest persisted result (design-backend §5.1). Runs hourly and
    once on worker startup.
    """
    async with get_sessionmaker()() as session:
        service = AuditService(session)
        row = await service.run_verification()
        return {
            "verified": row.verified,
            "entries": row.entries,
            "first_bad_position": row.first_bad_position,
            "checked_at": row.checked_at.isoformat() if row.checked_at else None,
        }
