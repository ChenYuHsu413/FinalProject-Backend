"""Alarm + maintenance-report repositories (design-backend §4/§8)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.alarms import ACTIVE
from app.repositories.pg.models import Alarm, MaintenanceReport


class AlarmRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, alarm: Alarm) -> Alarm:
        self.session.add(alarm)
        await self.session.flush()
        return alarm

    async def get(self, alarm_id: str) -> Alarm | None:
        row = await self.session.execute(select(Alarm).where(Alarm.alarm_id == alarm_id))
        return row.scalar_one_or_none()

    async def find_active_by_device_rule(self, device: str, rule: str) -> Alarm | None:
        """Dedup lookup: an existing active alarm for the same device + rule."""
        row = await self.session.execute(
            select(Alarm)
            .where(Alarm.device == device, Alarm.rule == rule, Alarm.status == ACTIVE)
            .order_by(Alarm.id.desc())
            .limit(1)
        )
        return row.scalar_one_or_none()

    async def list(
        self,
        *,
        status: str | None = None,
        severity: str | None = None,
        device: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Alarm], int]:
        stmt = select(Alarm)
        count_stmt = select(func.count()).select_from(Alarm)
        conds = []
        if status is not None:
            conds.append(Alarm.status == status)
        if severity is not None:
            conds.append(Alarm.severity == severity)
        if device is not None:
            conds.append(Alarm.device == device)
        if date_from is not None:
            conds.append(Alarm.raised_at >= date_from)
        if date_to is not None:
            conds.append(Alarm.raised_at <= date_to)
        for c in conds:
            stmt = stmt.where(c)
            count_stmt = count_stmt.where(c)
        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(Alarm.id.desc()).limit(limit).offset(offset)
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, total

    async def counts(self, device: str) -> dict:
        """Active-alarm counts + oldest pending age for the snapshot (§2)."""
        rows = (
            await self.session.execute(
                select(Alarm.severity, Alarm.raised_at).where(
                    Alarm.device == device, Alarm.status == ACTIVE
                )
            )
        ).all()
        active = len(rows)
        critical = sum(1 for r in rows if r.severity == "critical")
        warning = sum(1 for r in rows if r.severity == "warning")
        oldest = None
        if rows:
            oldest_raised = min(r.raised_at for r in rows)
            now = datetime.now(oldest_raised.tzinfo)
            oldest = int((now - oldest_raised).total_seconds())
        return {
            "active": active,
            "critical": critical,
            "warning": warning,
            "oldest_pending_s": oldest or 0,
        }


class MaintenanceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, report: MaintenanceReport) -> MaintenanceReport:
        self.session.add(report)
        await self.session.flush()
        return report

    async def list(
        self,
        *,
        device: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[MaintenanceReport], int]:
        stmt = select(MaintenanceReport)
        count_stmt = select(func.count()).select_from(MaintenanceReport)
        conds = []
        if device is not None:
            conds.append(MaintenanceReport.device == device)
        if date_from is not None:
            conds.append(MaintenanceReport.created_at >= date_from)
        if date_to is not None:
            conds.append(MaintenanceReport.created_at <= date_to)
        for c in conds:
            stmt = stmt.where(c)
            count_stmt = count_stmt.where(c)
        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(MaintenanceReport.id.desc()).limit(limit).offset(offset)
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, total
