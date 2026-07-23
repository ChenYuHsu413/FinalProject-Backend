"""Worker tasks. Kept as plain async functions so they are directly testable."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.db import get_sessionmaker
from app.core.settings import get_settings
from app.domain.devices import DEFAULT_DEVICE, get_device
from app.domain.scenarios import ACTIVE_SCENARIOS
from app.events import channels
from app.events.publisher import EventPublisher
from app.mock import simulator
from app.services.alarm_service import AlarmService
from app.services.audit_service import AuditService


def _publisher(ctx: dict[str, Any]) -> EventPublisher:
    # arq provides its Redis pool at ctx["redis"] (a redis.asyncio subclass).
    return EventPublisher(ctx["redis"])


async def sim_fallback_escalation(ctx: dict[str, Any]) -> None:
    """Mock escalation: publish fallback:escalation AND open a governance alarm.

    The alarm is de-duplicated by (device, rule) so a persisting escalation does
    not flood the alarm centre (DECISIONS D5.2); the fallback event and the alarm
    share one correlation_id.
    """
    if not get_settings().mock_mode:
        return
    pub = _publisher(ctx)
    device = get_device(DEFAULT_DEVICE)
    rule = "fallback_consecutive_3"
    correlation_id = str(uuid4())
    await pub.publish(
        channel=channels.FALLBACK_ESCALATION,
        event_type="fallback:escalation",
        payload={"trigger": "consecutive_3", "control_mode": "PID_BACKUP"},
        scenario_id=device.scenario_id,
        correlation_id=correlation_id,
    )
    async with get_sessionmaker()() as session:
        await AlarmService(session, pub).raise_from_fallback(
            device=device.id,
            rule=rule,
            severity="critical",
            scenario_id=device.scenario_id,
            correlation_id=correlation_id,
        )


async def sim_l1_summary(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    if not settings.mock_mode:
        return
    pub = _publisher(ctx)
    sim = simulator.MockSimulator(settings.engine_data_dir)
    for scenario in ACTIVE_SCENARIOS:
        await simulator.publish_l1_summary(pub, scenario)
        # Refresh the polling file so GET /l1/realtime also moves (not just the WS event).
        sim.write_l1_realtime(scenario)


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
