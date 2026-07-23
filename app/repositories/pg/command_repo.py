"""Command repository (design-backend §3)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.commands import ACCEPTED, COMPLETED, CYCLE_START, CYCLE_STOP, SUBMITTED
from app.repositories.pg.models import Command

# States that mean "in flight or in effect" for the cycle-running check.
_LIVE = (SUBMITTED, ACCEPTED, COMPLETED)


class CommandRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, command: Command) -> Command:
        self.session.add(command)
        await self.session.flush()  # may raise IntegrityError on idempotency clash
        return command

    async def get(self, command_id: str) -> Command | None:
        row = await self.session.execute(select(Command).where(Command.command_id == command_id))
        return row.scalar_one_or_none()

    async def get_by_idempotency(
        self, command_type: str, device: str, idempotency_key: str
    ) -> Command | None:
        row = await self.session.execute(
            select(Command).where(
                Command.command_type == command_type,
                Command.device == device,
                Command.idempotency_key == idempotency_key,
            )
        )
        return row.scalar_one_or_none()

    async def is_cycle_running(self, device: str) -> bool:
        """True if the most recent live cycle command for the device is a start."""
        row = await self.session.execute(
            select(Command.command_type)
            .where(
                Command.device == device,
                Command.command_type.in_((CYCLE_START, CYCLE_STOP)),
                Command.status.in_(_LIVE),
            )
            .order_by(Command.id.desc())
            .limit(1)
        )
        latest = row.scalar_one_or_none()
        return latest == CYCLE_START

    async def scan_timeouts(self, now: datetime) -> list[Command]:
        """Non-terminal commands whose confirm window has elapsed (worker use)."""
        rows = await self.session.execute(
            select(Command).where(Command.status.in_((SUBMITTED, ACCEPTED)))
        )
        due: list[Command] = []
        for cmd in rows.scalars().all():
            age = (now - cmd.submitted_at).total_seconds()
            if age >= cmd.confirm_timeout_s:
                due.append(cmd)
        return due

    async def list(
        self,
        *,
        device: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Command], int]:
        stmt = select(Command)
        count_stmt = select(func.count()).select_from(Command)
        conds = []
        if device is not None:
            conds.append(Command.device == device)
        if status is not None:
            conds.append(Command.status == status)
        for c in conds:
            stmt = stmt.where(c)
            count_stmt = count_stmt.where(c)
        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(Command.id.desc()).limit(limit).offset(offset)
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, total
