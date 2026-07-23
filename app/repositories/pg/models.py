"""Governance ORM models (batch 2: audit).

`audit_events` is append-only and hash-chained (design-backend.md §5.1). The
DB-generated columns ``id`` and ``created_at`` are deliberately NOT part of the
hash (see app/domain/audit.py + DECISIONS D2.2); ``ts`` is the app-set business
event time that *is* hashed.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.repositories.pg.base import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"

    # DB-generated — excluded from the hash.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # --- Hashed business fields (design-backend §5.1 / frontend §11.2) -------
    event_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    command_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str | None] = mapped_column(String(128))
    role: Mapped[str | None] = mapped_column(String(32))
    source_ip: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_device: Mapped[str | None] = mapped_column(String(64))
    scenario_id: Mapped[str | None] = mapped_column(String(64))
    old_value: Mapped[dict | None] = mapped_column(JSONB)
    new_value: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    proposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(String(32))
    model_version: Mapped[str | None] = mapped_column(String(64))
    mode: Mapped[str | None] = mapped_column(String(32))

    # --- Chain columns ------------------------------------------------------
    # prev_hash is mixed into entry_hash by concatenation (not JSON-embedded).
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class AuditChainVerification(Base):
    """Result of a worker chain re-verification run (design-backend §5.1).

    ``GET /audit/chain/verify`` returns the latest row here — the VERIFIED badge
    must come from periodic re-verification, never a live full-table recompute.
    """

    __tablename__ = "audit_chain_verifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    entries: Mapped[int] = mapped_column(Integer, nullable=False)
    first_bad_position: Mapped[int | None] = mapped_column(Integer)
    head_hash: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(String(128))
