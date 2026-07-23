"""Alarm orchestration (design-backend §4).

Every mutation writes an audit row (via AuditService, which commits the alarm
change and the audit row together) and best-effort publishes an `alarm:new` /
`alarm:updated` event on `ai_servo:alarm` (§11 envelope). A Redis outage never
fails the mutation.

Fallback escalation de-duplicates: an active alarm for the same device + rule is
updated, not re-opened, so a persisting escalation cannot flood the alarm centre
(DECISIONS D5.2).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.domain.alarms import ACK, RESOLVE, next_state
from app.events import channels
from app.events.publisher import EventPublisher
from app.repositories.pg.alarm_repo import AlarmRepository
from app.repositories.pg.models import Alarm
from app.services.audit_service import AuditService

logger = logging.getLogger("app.alarms")


def _now() -> datetime:
    return datetime.now(UTC)


def _new_alarm_id() -> str:
    return f"ALM-{uuid4().hex[:12]}"


def _to_event_payload(alarm: Alarm) -> dict:
    return {
        "alarm_id": alarm.alarm_id,
        "severity": alarm.severity,
        "device": alarm.device,
        "scenario_id": alarm.scenario_id,
        "rule": alarm.rule,
        "status": alarm.status,
        "raised_at": alarm.raised_at.isoformat() if alarm.raised_at else None,
        "ack_by": alarm.ack_by,
        "correlation_id": alarm.correlation_id,
    }


class AlarmService:
    def __init__(self, session: AsyncSession, publisher: EventPublisher | None = None) -> None:
        self.session = session
        self.repo = AlarmRepository(session)
        self.publisher = publisher

    async def _publish(self, event_type: str, alarm: Alarm) -> None:
        if self.publisher is None:
            return
        try:
            await self.publisher.publish(
                channel=channels.ALARM,
                event_type=event_type,
                payload=_to_event_payload(alarm),
                scenario_id=alarm.scenario_id,
                correlation_id=alarm.correlation_id,
            )
        except Exception:  # noqa: BLE001 — publishing is best-effort
            logger.warning("failed to publish %s for %s", event_type, alarm.alarm_id, exc_info=True)

    async def raise_from_fallback(
        self,
        *,
        device: str,
        rule: str,
        severity: str = "critical",
        scenario_id: str | None = None,
        correlation_id: str | None = None,
        root_cause_ref: str | None = None,
    ) -> tuple[Alarm, bool]:
        """Open an alarm, or update the existing active one (dedup). Returns (alarm, created)."""
        existing = await self.repo.find_active_by_device_rule(device, rule)
        if existing is not None:
            # Dedup: refresh correlation to the latest escalation; do NOT re-open.
            existing.correlation_id = correlation_id
            existing.updated_at = _now()
            await self.session.commit()
            await self._publish("alarm:updated", existing)
            return existing, False

        alarm = Alarm(
            alarm_id=_new_alarm_id(),
            severity=severity,
            device=device,
            scenario_id=scenario_id,
            rule=rule,
            status="active",
            raised_at=_now(),
            root_cause_ref=root_cause_ref,
            correlation_id=correlation_id,
        )
        await self.repo.add(alarm)
        await AuditService(self.session).record(
            action="alarm.raised",
            correlation_id=correlation_id,
            target_device=device,
            scenario_id=scenario_id,
            new_value={"alarm_id": alarm.alarm_id, "rule": rule, "severity": severity},
            result="active",
            reason="fallback_escalation",
        )
        await self._publish("alarm:new", alarm)
        return alarm, True

    async def _get_or_404(self, alarm_id: str) -> Alarm:
        alarm = await self.repo.get(alarm_id)
        if alarm is None:
            raise AppError(code="NOT_FOUND", message="alarm not found", status_code=404)
        return alarm

    async def ack(
        self,
        alarm_id: str,
        *,
        user_id: str | None,
        role: str | None,
        note: str | None,
        correlation_id: str | None,
    ) -> Alarm:
        alarm = await self._get_or_404(alarm_id)
        old = alarm.status
        alarm.status = next_state(alarm.status, ACK)  # raises InvalidAlarmTransition -> 409
        alarm.ack_by = user_id
        alarm.ack_at = _now()
        alarm.ack_note = note
        await AuditService(self.session).record(
            action="alarm.ack",
            user_id=user_id,
            role=role,
            correlation_id=correlation_id,
            target_device=alarm.device,
            scenario_id=alarm.scenario_id,
            old_value={"status": old},
            new_value={"status": alarm.status, "note": note},
            reason=note,
            result="acknowledged",
        )
        await self._publish("alarm:updated", alarm)
        return alarm

    async def resolve(
        self,
        alarm_id: str,
        *,
        user_id: str | None,
        role: str | None,
        maintenance_report_id: str | None,
        correlation_id: str | None,
    ) -> Alarm:
        alarm = await self._get_or_404(alarm_id)
        old = alarm.status
        alarm.status = next_state(alarm.status, RESOLVE)  # 409 if illegal
        alarm.resolved_at = _now()
        if maintenance_report_id:
            alarm.maintenance_report_id = maintenance_report_id
        await AuditService(self.session).record(
            action="alarm.resolve",
            user_id=user_id,
            role=role,
            correlation_id=correlation_id,
            target_device=alarm.device,
            scenario_id=alarm.scenario_id,
            old_value={"status": old},
            new_value={"status": alarm.status, "maintenance_report_id": maintenance_report_id},
            result="resolved",
        )
        await self._publish("alarm:updated", alarm)
        return alarm
