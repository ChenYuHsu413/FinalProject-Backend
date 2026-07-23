"""Maintenance report orchestration (design-backend §8)."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.events.publisher import EventPublisher
from app.repositories.pg.alarm_repo import MaintenanceRepository
from app.repositories.pg.models import MaintenanceReport
from app.services.alarm_service import AlarmService
from app.services.audit_service import AuditService


def _new_report_id() -> str:
    return f"MNT-{uuid4().hex[:12]}"


class MaintenanceService:
    def __init__(self, session: AsyncSession, publisher: EventPublisher | None = None) -> None:
        self.session = session
        self.repo = MaintenanceRepository(session)
        self.publisher = publisher

    async def create(
        self,
        *,
        device: str,
        actions_taken: list,
        result: str,
        alarm_id: str | None = None,
        attachments: list | None = None,
        user_id: str | None,
        role: str | None,
        correlation_id: str | None,
    ) -> MaintenanceReport:
        report = MaintenanceReport(
            report_id=_new_report_id(),
            alarm_id=alarm_id,
            device=device,
            actions_taken=actions_taken,
            result=result,
            attachments=attachments,
            # "residual recovery observation" — simulator-filled for now (DECISIONS D5.7).
            residual_recovery_status="observing",
            created_by=user_id,
        )
        await self.repo.add(report)
        await AuditService(self.session).record(
            action="maintenance.report",
            user_id=user_id,
            role=role,
            correlation_id=correlation_id,
            target_device=device,
            new_value={"report_id": report.report_id, "alarm_id": alarm_id, "result": result},
            result="recorded",
        )
        # Linked alarm resolution (design-backend §8 — report can resolve an alarm).
        if alarm_id:
            await AlarmService(self.session, self.publisher).resolve(
                alarm_id,
                user_id=user_id,
                role=role,
                maintenance_report_id=report.report_id,
                correlation_id=correlation_id,
            )
        return report
