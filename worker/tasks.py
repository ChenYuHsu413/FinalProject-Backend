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


async def scan_command_timeouts(ctx: dict[str, Any]) -> dict[str, Any]:
    """Mark commands whose confirm window elapsed as `timeout` (worker-only, §3.1).

    The API request path never decides timeout; only this scan does. Timeout is
    terminal — it presumes neither success nor failure.
    """
    from datetime import UTC, datetime

    from app.repositories.pg.command_repo import CommandRepository
    from app.services.command_service import CommandService

    pub = _publisher(ctx)
    marked = 0
    async with get_sessionmaker()() as session:
        due = await CommandRepository(session).scan_timeouts(datetime.now(UTC))
        service = CommandService(session, pub)
        for cmd in due:
            await service.mark_timeout(cmd)
            marked += 1
    return {"timed_out": marked}


def _should_leave_unconfirmed(command_id: str) -> bool:
    # Deterministic ~20%: leave some commands unconfirmed so the timeout path runs.
    try:
        return int(command_id.split("-")[1], 16) % 5 == 0
    except (IndexError, ValueError):
        return False


async def mock_confirm_commands(ctx: dict[str, Any]) -> dict[str, Any]:
    """Mock device confirmer (DECISIONS D6.4): submitted→accepted→completed with a
    two-tick delay, leaving ~20% unconfirmed so they reach `timeout`."""
    if not get_settings().mock_mode:
        return {"skipped": True}

    from app.domain.commands import ACCEPTED, SUBMITTED
    from app.repositories.pg.command_repo import CommandRepository
    from app.services.command_service import CommandService

    pub = _publisher(ctx)
    async with get_sessionmaker()() as session:
        repo = CommandRepository(session)
        service = CommandService(session, pub)
        # Finish ones already accepted.
        accepted, _ = await repo.list(status=ACCEPTED, limit=100)
        for cmd in accepted:
            await service.complete(cmd.command_id)
        # Accept freshly submitted (except the deliberately-unconfirmed fraction).
        submitted, _ = await repo.list(status=SUBMITTED, limit=100)
        for cmd in submitted:
            if _should_leave_unconfirmed(cmd.command_id):
                continue
            await service.accept(cmd.command_id)
    return {"ok": True}


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
