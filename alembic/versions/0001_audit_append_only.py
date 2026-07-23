"""audit subsystem: append-only hash-chained tables

Revision ID: 0001_audit
Revises:
Create Date: 2026-07-23

Creates ``audit_events`` (append-only, hash-chained) and
``audit_chain_verifications`` (worker re-verify results), then installs the
3-layer append-only protection's DB layers:

* REVOKE UPDATE/DELETE from PUBLIC (documents intent; owner still bypasses it),
* BEFORE UPDATE/DELETE row trigger that RAISEs (the real backstop — blocks even
  the table owner, and is the layer the integration test attacks directly),
* BEFORE TRUNCATE statement trigger (TRUNCATE bypasses row triggers).

The raw DDL is written idempotently (CREATE OR REPLACE / DROP IF EXISTS) so it
runs on both a clean and an already-migrated DB (batch-2 re-entrancy check).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_audit"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BLOCK_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_events_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only: % is not permitted', TG_OP
        USING ERRCODE = 'raise_exception';
END;
$$ LANGUAGE plpgsql;
"""

_ROW_TRIGGER = """
DROP TRIGGER IF EXISTS trg_audit_events_no_mutation ON audit_events;
CREATE TRIGGER trg_audit_events_no_mutation
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_block_mutation();
"""

_TRUNCATE_TRIGGER = """
DROP TRIGGER IF EXISTS trg_audit_events_no_truncate ON audit_events;
CREATE TRIGGER trg_audit_events_no_truncate
    BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_block_mutation();
"""


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(length=64)),
        sa.Column("command_id", sa.String(length=64)),
        sa.Column("user_id", sa.String(length=128)),
        sa.Column("role", sa.String(length=32)),
        sa.Column("source_ip", sa.String(length=64)),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_device", sa.String(length=64)),
        sa.Column("scenario_id", sa.String(length=64)),
        sa.Column("old_value", postgresql.JSONB()),
        sa.Column("new_value", postgresql.JSONB()),
        sa.Column("reason", sa.Text()),
        sa.Column("proposed_at", sa.DateTime(timezone=True)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
        sa.Column("result", sa.String(length=32)),
        sa.Column("model_version", sa.String(length=64)),
        sa.Column("mode", sa.String(length=32)),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_audit_events_event_id"),
        sa.UniqueConstraint("entry_hash", name="uq_audit_events_entry_hash"),
    )
    op.create_index("ix_audit_events_user_id", "audit_events", ["user_id"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_ts", "audit_events", ["ts"])

    op.create_table(
        "audit_chain_verifications",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("entries", sa.Integer(), nullable=False),
        sa.Column("first_bad_position", sa.Integer()),
        sa.Column("head_hash", sa.String(length=64)),
        sa.Column("reason", sa.String(length=128)),
    )

    # --- 3-layer append-only protection (DB layers) -------------------------
    op.execute(_BLOCK_FUNCTION)
    op.execute(_ROW_TRIGGER)
    op.execute(_TRUNCATE_TRIGGER)
    op.execute("REVOKE UPDATE, DELETE ON audit_events FROM PUBLIC")


def downgrade() -> None:
    # Tearing down the audit table would destroy the tamper-evident record.
    # Refuse rather than silently drop it (batch-2 acceptance: explicit-raise).
    raise RuntimeError(
        "Downgrade of the append-only audit subsystem is not supported: "
        "dropping audit_events would destroy the tamper-evident record."
    )
