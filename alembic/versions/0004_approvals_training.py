"""approvals + training jobs (batch 7)

Revision ID: 0004_approvals
Revises: 0003_commands
Create Date: 2026-07-23

Governance approvals (design-backend §6) and training jobs (§9). Both are mutable
governance state (decision / worker progression update rows), so no append-only
triggers here — same treatment as alarms/commands.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_approvals"
down_revision: str | None = "0003_commands"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("approval_id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("risk", sa.String(length=16), nullable=False, server_default="low"),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("scenario_id", sa.String(length=64)),
        sa.Column("device", sa.String(length=64)),
        sa.Column("summary", postgresql.JSONB()),
        sa.Column("reason", sa.Text()),
        sa.Column("proposed_by", sa.String(length=128)),
        sa.Column("proposed_role", sa.String(length=32)),
        sa.Column(
            "proposed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("decided_by", sa.String(length=128)),
        sa.Column("decided_role", sa.String(length=32)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_note", sa.Text()),
        sa.Column("side_effect_status", sa.String(length=24)),
        sa.Column("side_effect_detail", postgresql.JSONB()),
        sa.Column("correlation_id", sa.String(length=64)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("approval_id", name="uq_approvals_approval_id"),
    )
    op.create_index("ix_approvals_state", "approvals", ["state"])
    op.create_index("ix_approvals_type", "approvals", ["type"])

    op.create_table(
        "training_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("scenario_id", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("data_window", sa.String(length=32)),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("progress_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rmse", sa.Float()),
        sa.Column("shadow_comparison", postgresql.JSONB()),
        sa.Column("result_model_version", sa.String(length=64)),
        sa.Column("approval_id", sa.String(length=64)),
        sa.Column("requested_by", sa.String(length=128)),
        sa.Column("requested_role", sa.String(length=32)),
        sa.Column("correlation_id", sa.String(length=64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("job_id", name="uq_training_jobs_job_id"),
    )
    op.create_index("ix_training_jobs_status", "training_jobs", ["status"])
    op.create_index("ix_training_jobs_scenario", "training_jobs", ["scenario_id"])


def downgrade() -> None:
    op.drop_table("training_jobs")
    op.drop_table("approvals")
