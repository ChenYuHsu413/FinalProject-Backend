"""Audit integration tests — require a real PostgreSQL (triggers/REVOKE are
PG-specific). Gated on TEST_DATABASE_URL; skipped otherwise (CI provides it).

Covers the batch-2 acceptance items: append/chain linkage, SQL-level operator
self-filter, the append-only trigger blocking UPDATE/DELETE, worker re-verify +
tamper detection, denied-attempt auditing, privilege-escalation 403 audited,
service-only POST /audit/events, and migration DDL re-entrancy.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

PG_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="TEST_DATABASE_URL not set")

SERVICE_TOKEN = "test-service-token"
AUTH = {"Authorization": f"Bearer {SERVICE_TOKEN}"}


def _admin_headers() -> dict[str, str]:
    return {
        **AUTH,
        "X-User-ID": "admin-1",
        "X-User-Role": "admin",
        "X-Correlation-ID": str(uuid.uuid4()),
    }


def _operator_headers(user_id: str) -> dict[str, str]:
    return {
        **AUTH,
        "X-User-ID": user_id,
        "X-User-Role": "operator",
        "X-Correlation-ID": str(uuid.uuid4()),
    }


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Clean slate + real `alembic upgrade head` (also validates the migration)."""
    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import create_async_engine

    async def reset() -> None:
        engine = create_async_engine(PG_URL)
        async with engine.begin() as conn:
            await conn.execute(_text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(_text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(reset())
    env = {**os.environ, "DATABASE_URL": PG_URL, "SERVICE_TOKEN": SERVICE_TOKEN}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    yield


@pytest_asyncio.fixture
async def sessionmaker(_schema):
    """Point the global engine at the test DB and truncate audit tables.

    Cleanup disables the trigger to delete rows, then re-enables it — the trigger
    stays installed for the attack test.
    """
    from app.core import settings as settings_mod
    from app.core.db import dispose_engine, get_sessionmaker

    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = PG_URL
    settings_mod.get_settings.cache_clear()
    await dispose_engine()

    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(text("ALTER TABLE audit_events DISABLE TRIGGER USER"))
        await s.execute(text("DELETE FROM audit_events"))
        await s.execute(text("DELETE FROM audit_chain_verifications"))
        await s.execute(text("ALTER TABLE audit_events ENABLE TRIGGER USER"))
        await s.commit()

    yield sm

    await dispose_engine()
    if old is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = old
    settings_mod.get_settings.cache_clear()


@pytest_asyncio.fixture
async def iclient(sessionmaker):
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed(sm, **over):
    from app.services.audit_service import AuditService

    async with sm() as s:
        svc = AuditService(s)
        return await svc.record(**over)


# --- append + chain ----------------------------------------------------------
async def test_append_builds_linked_chain(sessionmaker):
    from app.repositories.pg.models import AuditEvent

    for i in range(3):
        await _seed(sessionmaker, action="cycle.start", user_id=f"u{i}", reason=str(i))

    async with sessionmaker() as s:
        rows = list(
            (await s.execute(select(AuditEvent).order_by(AuditEvent.id.asc()))).scalars().all()
        )
    assert len(rows) == 3
    assert rows[0].prev_hash == "0" * 64  # genesis
    assert rows[1].prev_hash == rows[0].entry_hash
    assert rows[2].prev_hash == rows[1].entry_hash

    from app.repositories.pg.audit_repo import AuditRepository

    async with sessionmaker() as s:
        result = await AuditRepository(s).verify_full_chain()
    assert result.verified is True
    assert result.entries == 3


# --- SQL-level operator self-filter ------------------------------------------
async def test_get_events_operator_restricted_to_self(sessionmaker, iclient):
    await _seed(sessionmaker, action="alarm.ack", user_id="alice")
    await _seed(sessionmaker, action="alarm.ack", user_id="bob")

    r_admin = await iclient.get("/api/v1/audit/events", headers=_admin_headers())
    assert r_admin.status_code == 200
    assert r_admin.json()["total"] == 2

    r_alice = await iclient.get("/api/v1/audit/events", headers=_operator_headers("alice"))
    assert r_alice.status_code == 200
    body = r_alice.json()
    assert body["total"] == 1
    assert all(e["user_id"] == "alice" for e in body["events"])


# --- append-only trigger (the only auto-verifiable protection layer) ----------
async def test_trigger_blocks_update(sessionmaker):
    await _seed(sessionmaker, action="cycle.start", user_id="u")
    with pytest.raises(Exception) as exc:  # noqa: PT011 — DBAPIError from PG RAISE
        async with sessionmaker() as s:
            await s.execute(text("UPDATE audit_events SET reason = 'tampered'"))
            await s.commit()
    assert "append-only" in str(exc.value)


async def test_trigger_blocks_delete(sessionmaker):
    await _seed(sessionmaker, action="cycle.start", user_id="u")
    with pytest.raises(Exception) as exc:  # noqa: PT011
        async with sessionmaker() as s:
            await s.execute(text("DELETE FROM audit_events"))
            await s.commit()
    assert "append-only" in str(exc.value)


async def test_trigger_blocks_truncate(sessionmaker):
    await _seed(sessionmaker, action="cycle.start", user_id="u")
    with pytest.raises(Exception) as exc:  # noqa: PT011
        async with sessionmaker() as s:
            await s.execute(text("TRUNCATE audit_events"))
            await s.commit()
    assert "append-only" in str(exc.value)


# --- worker re-verify + tamper detection -------------------------------------
async def test_worker_verify_and_endpoint(sessionmaker, iclient):
    from worker.tasks import reverify_audit_chain

    await _seed(sessionmaker, action="cycle.start", user_id="u")
    await _seed(sessionmaker, action="cycle.stop", user_id="u")

    out = await reverify_audit_chain()
    assert out["verified"] is True
    assert out["entries"] == 2

    r = await iclient.get("/api/v1/audit/chain/verify", headers=_admin_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["verified"] is True
    assert body["entries"] == 2
    assert body["checked_at"]


async def test_worker_detects_tamper(sessionmaker):
    from worker.tasks import reverify_audit_chain

    await _seed(sessionmaker, action="cycle.start", user_id="u")
    await _seed(sessionmaker, action="cycle.stop", user_id="u")

    # Tamper by bypassing the trigger (simulates a privileged DB attacker).
    tamper = "UPDATE audit_events SET reason = 'evil' WHERE id = (SELECT MIN(id) FROM audit_events)"
    async with sessionmaker() as s:
        await s.execute(text("ALTER TABLE audit_events DISABLE TRIGGER USER"))
        await s.execute(text(tamper))
        await s.execute(text("ALTER TABLE audit_events ENABLE TRIGGER USER"))
        await s.commit()

    out = await reverify_audit_chain()
    assert out["verified"] is False
    assert out["first_bad_position"] == 1


# --- denied-attempt auditing -------------------------------------------------
async def _count_denied(sm) -> int:
    from app.repositories.pg.models import AuditEvent
    from app.services.audit_service import ACTION_AUTHZ_DENIED

    async with sm() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == ACTION_AUTHZ_DENIED)
            )
        ).scalar_one()


async def test_bad_token_attempt_is_audited(sessionmaker, iclient):
    r = await iclient.get("/api/v1/audit/events", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403
    assert await _count_denied(sessionmaker) == 1


async def test_privilege_escalation_is_403_and_audited(sessionmaker, iclient):
    # Operator lacks audit.export (admin-only) → 403, and it is audited.
    r = await iclient.get("/api/v1/audit/export", headers=_operator_headers("mallory"))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"

    from app.repositories.pg.models import AuditEvent
    from app.services.audit_service import ACTION_AUTHZ_DENIED

    async with sessionmaker() as s:
        row = (
            await s.execute(
                select(AuditEvent)
                .where(AuditEvent.action == ACTION_AUTHZ_DENIED)
                .order_by(AuditEvent.id.desc())
                .limit(1)
            )
        ).scalar_one()
    assert row.user_id == "mallory"
    assert row.role == "operator"
    assert "audit.export" in (row.reason or "")


# --- POST /audit/events is service-only (no X-User-* required) ----------------
async def test_post_audit_events_needs_no_user_headers(sessionmaker, iclient):
    r = await iclient.post(
        "/api/v1/audit/events",
        headers=AUTH,  # service token only, no X-User-*
        json={"action": "auth.login", "user_id": "someone", "role": "operator"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["event_id"]
    assert body["entry_hash"]


# --- migration DDL re-entrancy -----------------------------------------------
async def test_trigger_ddl_is_reentrant(sessionmaker):
    # Re-running the idempotent trigger/function DDL must not error.
    ddl = [
        # mirrors the migration's idempotent statements
        """
        CREATE OR REPLACE FUNCTION audit_events_block_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only: %', TG_OP
                USING ERRCODE = 'raise_exception';
        END;
        $$ LANGUAGE plpgsql;
        """,
        "DROP TRIGGER IF EXISTS trg_audit_events_no_mutation ON audit_events;",
        """
        CREATE TRIGGER trg_audit_events_no_mutation
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_block_mutation();
        """,
    ]
    async with sessionmaker() as s:
        for stmt in ddl:
            await s.execute(text(stmt))
        await s.commit()
    # trigger still blocks after re-create
    await _seed(sessionmaker, action="cycle.start", user_id="u")
    with pytest.raises(Exception):  # noqa: PT011, B017
        async with sessionmaker() as s:
            await s.execute(text("DELETE FROM audit_events"))
            await s.commit()
