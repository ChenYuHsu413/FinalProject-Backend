"""Training-job repository (design-backend §9)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.training import TERMINAL_STATES
from app.repositories.pg.models import TrainingJob


class TrainingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, job: TrainingJob) -> TrainingJob:
        self.session.add(job)
        await self.session.flush()
        return job

    async def get(self, job_id: str) -> TrainingJob | None:
        row = await self.session.execute(select(TrainingJob).where(TrainingJob.job_id == job_id))
        return row.scalar_one_or_none()

    async def list(
        self,
        *,
        status: str | None = None,
        scenario_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[TrainingJob], int]:
        stmt = select(TrainingJob)
        count_stmt = select(func.count()).select_from(TrainingJob)
        conds = []
        if status is not None:
            conds.append(TrainingJob.status == status)
        if scenario_id is not None:
            conds.append(TrainingJob.scenario_id == scenario_id)
        for c in conds:
            stmt = stmt.where(c)
            count_stmt = count_stmt.where(c)
        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(TrainingJob.id.desc()).limit(limit).offset(offset)
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, total

    async def active_jobs(self, limit: int = 100) -> list[TrainingJob]:
        """Non-terminal jobs (worker progression use)."""
        stmt = (
            select(TrainingJob)
            .where(TrainingJob.status.notin_(tuple(TERMINAL_STATES)))
            .order_by(TrainingJob.id.asc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def shadow_comparisons(self, scenario_id: str | None = None) -> list[TrainingJob]:
        """Jobs that have a shadow comparison (design-backend §9 /shadow/comparisons)."""
        stmt = select(TrainingJob).where(TrainingJob.shadow_comparison.isnot(None))
        if scenario_id is not None:
            stmt = stmt.where(TrainingJob.scenario_id == scenario_id)
        stmt = stmt.order_by(TrainingJob.id.desc()).limit(200)
        return list((await self.session.execute(stmt)).scalars().all())
