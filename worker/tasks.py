"""Worker tasks. Kept as plain async functions so they are directly testable."""

from __future__ import annotations

from typing import Any

from app.core.db import get_sessionmaker
from app.core.settings import get_settings
from app.domain.scenarios import ACTIVE_SCENARIOS
from app.events.publisher import EventPublisher
from app.mock import simulator
from app.services.audit_service import AuditService


def _publisher(ctx: dict[str, Any]) -> EventPublisher:
    # arq provides its Redis pool at ctx["redis"] (a redis.asyncio subclass).
    return EventPublisher(ctx["redis"])


async def sim_l1_summary(ctx: dict[str, Any]) -> None:
    if not get_settings().mock_mode:
        return
    pub = _publisher(ctx)
    for scenario in ACTIVE_SCENARIOS:
        await simulator.publish_l1_summary(pub, scenario)


async def sim_l2_finetune(ctx: dict[str, Any]) -> None:
    if not get_settings().mock_mode:
        return
    pub = _publisher(ctx)
    for scenario in ACTIVE_SCENARIOS:
        await simulator.publish_l2_finetune(pub, scenario)


async def sim_fallback_event(ctx: dict[str, Any]) -> None:
    if not get_settings().mock_mode:
        return
    pub = _publisher(ctx)
    for scenario in ACTIVE_SCENARIOS:
        await simulator.publish_fallback_event(pub, scenario)


async def sim_shap_diagnosis(ctx: dict[str, Any]) -> None:
    if not get_settings().mock_mode:
        return
    pub = _publisher(ctx)
    for scenario in ACTIVE_SCENARIOS:
        await simulator.publish_shap_diagnosis(pub, scenario)


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
