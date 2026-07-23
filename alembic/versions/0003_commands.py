"""commands (batch 6)

Revision ID: 0003_commands
Revises: 0002_alarms
Create Date: 2026-07-23

Mutable command state. Unique (command_type, device, idempotency_key) implements
idempotency at the DB layer (concurrent duplicates blocked — design-backend §3.3).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_commands"
down_revision: str | None = "0002_alarms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "commands",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("command_id", sa.String(length=64), nullable=False),
        sa.Column("command_type", sa.String(length=32), nullable=False),
        sa.Column("device", sa.String(length=64), nullable=False),
        sa.Column("scenario_id", sa.String(length=64)),
        sa.Column("target_mode", sa.String(length=32)),
        sa.Column("reason", sa.Text()),
        sa.Column("params", postgresql.JSONB()),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="submitted"),
        sa.Column("confirm_timeout_s", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("high_risk", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("operator", sa.String(length=128)),
        sa.Column("role", sa.String(length=32)),
        sa.Column("correlation_id", sa.String(length=64)),
        sa.Column("result", sa.String(length=32)),
        sa.Column(
            "submitted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("command_id", name="uq_commands_command_id"),
        sa.UniqueConstraint(
            "command_type", "device", "idempotency_key", name="uq_commands_idempotency"
        ),
    )
    op.create_index("ix_commands_device", "commands", ["device"])
    op.create_index("ix_commands_status", "commands", ["status"])


def downgrade() -> None:
    op.drop_table("commands")
