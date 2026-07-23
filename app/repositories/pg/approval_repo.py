"""Approval repository (design-backend §6)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.approvals import PENDING
from app.repositories.pg.models import Approval


class ApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, approval: Approval) -> Approval:
        self.session.add(approval)
        await self.session.flush()
        return approval

    async def get(self, approval_id: str) -> Approval | None:
        row = await self.session.execute(
            select(Approval).where(Approval.approval_id == approval_id)
        )
        return row.scalar_one_or_none()

    async def list(
        self,
        *,
        state: str | None = None,
        type: str | None = None,
        risk: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Approval], int]:
        stmt = select(Approval)
        count_stmt = select(func.count()).select_from(Approval)
        conds = []
        if state is not None:
            conds.append(Approval.state == state)
        if type is not None:
            conds.append(Approval.type == type)
        if risk is not None:
            conds.append(Approval.risk == risk)
        for c in conds:
            stmt = stmt.where(c)
            count_stmt = count_stmt.where(c)
        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(Approval.id.desc()).limit(limit).offset(offset)
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, total

    async def summary(self) -> dict:
        """Pending count per type + oldest-waiting age (design-backend §6.2 /summary).

        Feeds the admin first-screen 待辦計數卡 (design-frontend §7.5): a count per
        approval type and the age of the longest-waiting pending item.
        """
        rows = (
            await self.session.execute(
                select(Approval.type, Approval.proposed_at).where(Approval.state == PENDING)
            )
        ).all()
        by_type: dict[str, int] = {}
        oldest_at: datetime | None = None
        for typ, proposed_at in rows:
            by_type[typ] = by_type.get(typ, 0) + 1
            if oldest_at is None or proposed_at < oldest_at:
                oldest_at = proposed_at
        oldest_wait_s = 0
        if oldest_at is not None:
            now = datetime.now(oldest_at.tzinfo)
            oldest_wait_s = int((now - oldest_at).total_seconds())
        return {"by_type": by_type, "total": len(rows), "oldest_wait_s": oldest_wait_s}
