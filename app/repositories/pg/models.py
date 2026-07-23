"""Governance ORM models.

batch 2: `audit_events` — append-only, hash-chained (design-backend.md §5.1);
DB-generated `id`/`created_at` are NOT hashed (see app/domain/audit.py + D2.2).
batch 5: `alarms` + `maintenance_reports` — mutable governance state (ack/resolve
update rows), so no append-only triggers here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
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


class Alarm(Base):
    """Alarm lifecycle record (design-backend §4.1). Mutable: ack/resolve update."""

    __tablename__ = "alarms"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alarm_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # critical/warning/info
    device: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_id: Mapped[str | None] = mapped_column(String(64))
    rule: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. residual_gt_3sigma
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    raised_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ack_by: Mapped[str | None] = mapped_column(String(128))
    ack_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ack_note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    root_cause_ref: Mapped[str | None] = mapped_column(String(128))  # SHAP diagnosis ref
    maintenance_report_id: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MaintenanceReport(Base):
    """Maintenance report (design-backend §8)."""

    __tablename__ = "maintenance_reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    alarm_id: Mapped[str | None] = mapped_column(String(64))
    device: Mapped[str] = mapped_column(String(64), nullable=False)
    actions_taken: Mapped[list] = mapped_column(JSONB, nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    attachments: Mapped[list | None] = mapped_column(JSONB)
    residual_recovery_status: Mapped[str | None] = mapped_column(String(32))
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Command(Base):
    """Command lifecycle record (design-backend §3). Mutable state."""

    __tablename__ = "commands"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    command_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    command_type: Mapped[str] = mapped_column(String(32), nullable=False)
    device: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_id: Mapped[str | None] = mapped_column(String(64))
    target_mode: Mapped[str | None] = mapped_column(String(32))  # for mode.switch
    reason: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict | None] = mapped_column(JSONB)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="submitted")
    confirm_timeout_s: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    high_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    operator: Mapped[str | None] = mapped_column(String(128))
    role: Mapped[str | None] = mapped_column(String(32))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[str | None] = mapped_column(String(32))
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "command_type", "device", "idempotency_key", name="uq_commands_idempotency"
        ),
    )


class Approval(Base):
    """Governance approval record (design-backend §6.1). Mutable: decided updates row.

    `state` (not `status`) mirrors the §6.1 field name the admin frontend renders.
    `summary` is the type-specific §6.1 payload (JSONB). `side_effect_status` /
    `side_effect_detail` capture the post-decision application outcome separately
    from the decision itself (model promotion rewrite / param five-check — D7.3).
    """

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    approval_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # §6.1 approval type
    risk: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # Target context (scenario_id / device are not always in `summary`, §6.1).
    scenario_id: Mapped[str | None] = mapped_column(String(64))
    device: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)  # proposal reason
    proposed_by: Mapped[str | None] = mapped_column(String(128))
    proposed_role: Mapped[str | None] = mapped_column(String(32))
    proposed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_by: Mapped[str | None] = mapped_column(String(128))
    decided_role: Mapped[str | None] = mapped_column(String(32))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    # Post-decision application (D7.3): applied | apply_failed | failed.
    side_effect_status: Mapped[str | None] = mapped_column(String(24))
    side_effect_detail: Mapped[dict | None] = mapped_column(JSONB)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TrainingJob(Base):
    """Training job (design-backend §9). Mutable: worker advances state."""

    __tablename__ = "training_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # finetune | full_retrain
    scenario_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    data_window: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rmse: Mapped[float | None] = mapped_column()  # current metric (Float via type affinity)
    shadow_comparison: Mapped[dict | None] = mapped_column(JSONB)  # §9 new/old RMSE etc.
    result_model_version: Mapped[str | None] = mapped_column(String(64))
    approval_id: Mapped[str | None] = mapped_column(String(64))  # spawned on `passed`
    requested_by: Mapped[str | None] = mapped_column(String(128))
    requested_role: Mapped[str | None] = mapped_column(String(32))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
