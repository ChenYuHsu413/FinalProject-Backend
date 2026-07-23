"""Command subsystem integration tests (require PostgreSQL; gated).

Covers batch-6 acceptance: 202-submitted semantics, idempotency (dup→200 same id +
concurrency via DB constraint), in-progress conflict (409, distinct from
idempotency), worker-only timeout, mode:changed only on complete, E-Stop high_risk
+ all roles, engineer cycle.start → 403 AND audited, full event sequence.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

PG_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="TEST_DATABASE_URL not set")

SERVICE_TOKEN = "test-service-token"
AUTH = {"Authorization": f"Bearer {SERVICE_TOKEN}"}


def _headers(role: str, user: str = "u-1") -> dict[str, str]:
    return {**AUTH, "X-User-ID": user, "X-User-Role": role, "X-Correlation-ID": str(uuid.uuid4())}


@pytest.fixture(scope="session", autouse=True)
def _schema():
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
async def sm():
    from app.core import settings as settings_mod
    from app.core.db import dispose_engine, get_sessionmaker

    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = PG_URL
    settings_mod.get_settings.cache_clear()
    await dispose_engine()

    smk = get_sessionmaker()
    async with smk() as s:
        await s.execute(text("DELETE FROM commands"))
        await s.execute(text("ALTER TABLE audit_events DISABLE TRIGGER USER"))
        await s.execute(text("DELETE FROM audit_events"))
        await s.execute(text("ALTER TABLE audit_events ENABLE TRIGGER USER"))
        await s.commit()
    yield smk

    await dispose_engine()
    if old is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = old
    settings_mod.get_settings.cache_clear()


@pytest_asyncio.fixture
async def fake_redis():
    r = fakeredis.aioredis.FakeRedis()
    yield r
    await r.aclose()


@pytest_asyncio.fixture
async def iclient(sm, fake_redis):
    from app.events.deps import get_publisher
    from app.events.publisher import EventPublisher
    from app.main import app

    app.dependency_overrides[get_publisher] = lambda: EventPublisher(fake_redis)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# --- 202 semantics -----------------------------------------------------------
async def test_submit_202_submitted_semantics_only(sm, iclient):
    r = await iclient.post(
        "/api/v1/commands/cycle/start",
        headers=_headers("operator"),
        json={"device": "AXIS-04", "idempotency_key": "k-1", "reason": "start"},
    )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "submitted"
    # No "completed"-semantics fields in the 202 body.
    assert set(body) == {"command_id", "status", "submitted_at", "confirm_timeout_s"}
    assert "result" not in body and "completed_at" not in body


# --- idempotency vs conflict -------------------------------------------------
async def test_idempotent_duplicate_returns_200_same_id(sm, iclient):
    payload = {"device": "AXIS-04", "idempotency_key": "same-key", "reason": "x"}
    r1 = await iclient.post(
        "/api/v1/commands/cycle/start", headers=_headers("operator"), json=payload
    )
    r2 = await iclient.post(
        "/api/v1/commands/cycle/start", headers=_headers("operator"), json=payload
    )
    assert r1.status_code == 202
    assert r2.status_code == 200  # replay, NOT 409
    assert r1.json()["command_id"] == r2.json()["command_id"]


async def test_concurrent_duplicate_single_row(sm):
    # Use mode.switch (no cycle-conflict) to isolate idempotency-via-DB-constraint.
    from app.services.command_service import CommandService

    async def one():
        async with sm() as s:
            return await CommandService(s).submit(
                command_type="mode.switch",
                device="AXIS-04",
                idempotency_key="race-key",
                operator="op",
                role="operator",
                correlation_id="c",
                target_mode="FineTune",
            )

    r1, r2 = await asyncio.gather(one(), one())
    assert r1[0].command_id == r2[0].command_id  # same command
    assert sorted([r1[1], r2[1]]) == [False, True]  # exactly one created

    from app.repositories.pg.models import Command

    async with sm() as s:
        n = (await s.execute(select(func.count()).select_from(Command))).scalar_one()
    assert n == 1


async def test_cycle_conflict_is_409(sm, iclient):
    from app.services.command_service import CommandService

    # Start + drive to completed → cycle running.
    async with sm() as s:
        cmd, _ = await CommandService(s).submit(
            command_type="cycle.start",
            device="AXIS-04",
            idempotency_key="run-1",
            operator="op",
            role="operator",
            correlation_id="c",
        )
        cid = cmd.command_id
    async with sm() as s:
        svc = CommandService(s)
        await svc.accept(cid)
        await svc.complete(cid)

    # A new cycle.start (different key) while running → 409, not idempotency.
    r = await iclient.post(
        "/api/v1/commands/cycle/start",
        headers=_headers("operator"),
        json={"device": "AXIS-04", "idempotency_key": "run-2"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"


# --- worker-only timeout -----------------------------------------------------
async def test_timeout_is_worker_decided_and_terminal(sm):
    from app.repositories.pg.command_repo import CommandRepository
    from app.services.command_service import CommandService

    async with sm() as s:
        cmd, _ = await CommandService(s).submit(
            command_type="cycle.start",
            device="AXIS-04",
            idempotency_key="to-1",
            operator="op",
            role="operator",
            correlation_id="c",
        )
        cid = cmd.command_id
    # Backdate beyond the confirm window (simulates elapsed time).
    async with sm() as s:
        await s.execute(
            text("UPDATE commands SET submitted_at = submitted_at - interval '60 seconds'")
        )
        await s.commit()
    # Worker scan marks it timeout.
    async with sm() as s:
        repo, svc = CommandRepository(s), CommandService(s)
        due = await repo.scan_timeouts(datetime.now(UTC))
        assert any(c.command_id == cid for c in due)
        for c in due:
            await svc.mark_timeout(c)
    async with sm() as s:
        c = await CommandRepository(s).get(cid)
    assert c.status == "timeout"  # terminal, not presumed success/failure


# --- mode:changed only on complete ------------------------------------------
async def test_mode_changed_only_on_complete(sm, fake_redis):
    from app.events import channels
    from app.events.publisher import EventPublisher
    from app.services.command_service import CommandService

    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(channels.COMMAND)

    async def drain(expected: int) -> list[dict]:
        out: list[dict] = []
        for _ in range(30):  # don't break on a transient None (fakeredis subscribe frame)
            m = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.3)
            if m is not None:
                out.append(json.loads(m["data"]))
            if len(out) >= expected:
                break
        return out

    async with sm() as s:
        svc = CommandService(s, EventPublisher(fake_redis))
        cmd, _ = await svc.submit(
            command_type="mode.switch",
            device="AXIS-04",
            idempotency_key="m-1",
            operator="op",
            role="operator",
            correlation_id="c",
            target_mode="FineTune",
        )
        cid = cmd.command_id
        await svc.accept(cid)
    types_before = [e["event_type"] for e in await drain(2)]  # submitted + accepted
    assert "mode:changed" not in types_before  # not before completion

    async with sm() as s:
        await CommandService(s, EventPublisher(fake_redis)).complete(cid)
    types_after = [e["event_type"] for e in await drain(2)]  # completed + mode:changed
    assert "mode:changed" in types_after
    await pubsub.unsubscribe(channels.COMMAND)


# --- E-Stop ------------------------------------------------------------------
@pytest.mark.parametrize("role", ["operator", "engineer", "admin"])
async def test_estop_all_roles_high_risk_short_timeout(sm, iclient, role):
    r = await iclient.post(
        "/api/v1/commands/estop-request",
        headers=_headers(role),
        json={"device": "AXIS-04", "idempotency_key": f"es-{role}", "reason": "hazard"},
    )
    assert r.status_code == 202, r.text
    assert r.json()["confirm_timeout_s"] == 5  # shorter than the default 10

    from app.repositories.pg.models import Command

    async with sm() as s:
        cmd = (
            await s.execute(select(Command).where(Command.command_id == r.json()["command_id"]))
        ).scalar_one()
    assert cmd.high_risk is True


# --- permission reverse test -------------------------------------------------
async def test_engineer_cycle_start_forbidden_and_audited(sm, iclient):
    r = await iclient.post(
        "/api/v1/commands/cycle/start",
        headers=_headers("engineer"),
        json={"device": "AXIS-04", "idempotency_key": "eng-1"},
    )
    assert r.status_code == 403  # engineer lacks cycle.start (D1.5)

    from app.repositories.pg.models import AuditEvent
    from app.services.audit_service import ACTION_AUTHZ_DENIED

    async with sm() as s:
        row = (
            await s.execute(
                select(AuditEvent)
                .where(AuditEvent.action == ACTION_AUTHZ_DENIED)
                .order_by(AuditEvent.id.desc())
                .limit(1)
            )
        ).scalar_one()
    assert row.role == "engineer"
    assert "cycle.start" in (row.reason or "")


# --- full lifecycle event sequence ------------------------------------------
async def test_full_lifecycle_status_sequence(sm, fake_redis):
    from app.events import channels
    from app.events.publisher import EventPublisher
    from app.services.command_service import CommandService

    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(channels.COMMAND)
    async with sm() as s:
        svc = CommandService(s, EventPublisher(fake_redis))
        cmd, _ = await svc.submit(
            command_type="cycle.start",
            device="AXIS-04",
            idempotency_key="life-1",
            operator="op",
            role="operator",
            correlation_id="c",
        )
        await svc.accept(cmd.command_id)
        await svc.complete(cmd.command_id)

    statuses = []
    for _ in range(30):  # don't break on a transient None (fakeredis subscribe frame)
        m = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.3)
        if m is not None:
            e = json.loads(m["data"])
            if e["event_type"] == "command:status":
                statuses.append(e["payload"]["status"])
        if len(statuses) >= 3:
            break
    assert statuses == ["submitted", "accepted", "completed"]
    await pubsub.unsubscribe(channels.COMMAND)
