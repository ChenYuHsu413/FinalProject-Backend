"""alarms + maintenance_reports (batch 5)

Revision ID: 0002_alarms
Revises: 0001_audit
Create Date: 2026-07-23

Mutable governance state (ack/resolve update rows), so — unlike audit_events —
there are no append-only triggers and downgrade may drop the tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_alarms"
down_revision: str | None = "0001_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alarms",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("alarm_id", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("device", sa.String(length=64), nullable=False),
        sa.Column("scenario_id", sa.String(length=64)),
        sa.Column("rule", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column(
            "raised_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("ack_by", sa.String(length=128)),
        sa.Column("ack_at", sa.DateTime(timezone=True)),
        sa.Column("ack_note", sa.Text()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("root_cause_ref", sa.String(length=128)),
        sa.Column("maintenance_report_id", sa.String(length=64)),
        sa.Column("correlation_id", sa.String(length=64)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("alarm_id", name="uq_alarms_alarm_id"),
    )
    op.create_index("ix_alarms_device", "alarms", ["device"])
    op.create_index("ix_alarms_status", "alarms", ["status"])
    op.create_index("ix_alarms_rule", "alarms", ["rule"])
    # Dedup lookups target (device, rule, status='active').
    op.create_index("ix_alarms_device_rule_status", "alarms", ["device", "rule", "status"])

    op.create_table(
        "maintenance_reports",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.String(length=64), nullable=False),
        sa.Column("alarm_id", sa.String(length=64)),
        sa.Column("device", sa.String(length=64), nullable=False),
        sa.Column("actions_taken", postgresql.JSONB(), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("attachments", postgresql.JSONB()),
        sa.Column("residual_recovery_status", sa.String(length=32)),
        sa.Column("created_by", sa.String(length=128)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("report_id", name="uq_maintenance_reports_report_id"),
    )
    op.create_index("ix_maintenance_reports_device", "maintenance_reports", ["device"])


def downgrade() -> None:
    op.drop_table("maintenance_reports")
    op.drop_table("alarms")
