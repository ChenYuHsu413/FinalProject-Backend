"""Alarm + maintenance integration tests (require PostgreSQL; gated).

Covers batch-5 acceptance: ack/resolve lifecycle, fallback-escalation dedup,
admin-ack → 403 AND audited, input NUL/length rejection, alarm:new via fakeredis,
snapshot real alarm counts, maintenance report resolving an alarm.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

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

    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(text("DELETE FROM alarms"))
        await s.execute(text("DELETE FROM maintenance_reports"))
        await s.execute(text("ALTER TABLE audit_events DISABLE TRIGGER USER"))
        await s.execute(text("DELETE FROM audit_events"))
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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _seed_alarm(sm, *, device="AXIS-04", rule="residual_gt_3sigma", severity="critical"):
    from app.services.alarm_service import AlarmService

    async with sm() as s:
        alarm, created = await AlarmService(s).raise_from_fallback(
            device=device,
            rule=rule,
            severity=severity,
            scenario_id="01_Pick_and_Place",
            correlation_id="seed",
        )
        return alarm.alarm_id, created


# --- lifecycle ---------------------------------------------------------------
async def test_ack_then_resolve_lifecycle(sm, iclient):
    alarm_id, _ = await _seed_alarm(sm)
    r = await iclient.post(
        f"/api/v1/alarms/{alarm_id}/ack", headers=_headers("operator"), json={"note": "checking"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "acknowledged"
    assert body["ack_by"] == "u-1"
    assert body["ack_note"] == "checking"

    r2 = await iclient.post(
        f"/api/v1/alarms/{alarm_id}/resolve", headers=_headers("operator"), json={}
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "resolved"


async def test_ack_twice_is_409(sm, iclient):
    alarm_id, _ = await _seed_alarm(sm)
    await iclient.post(f"/api/v1/alarms/{alarm_id}/ack", headers=_headers("operator"), json={})
    r = await iclient.post(f"/api/v1/alarms/{alarm_id}/ack", headers=_headers("operator"), json={})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"


# --- dedup -------------------------------------------------------------------
async def test_fallback_escalation_dedup(sm):
    id1, created1 = await _seed_alarm(sm, rule="fallback_consecutive_3")
    id2, created2 = await _seed_alarm(sm, rule="fallback_consecutive_3")
    assert created1 is True
    assert created2 is False  # deduped — same device+rule active
    assert id1 == id2
    from app.repositories.pg.models import Alarm

    async with sm() as s:
        count = len((await s.execute(select(Alarm))).scalars().all())
    assert count == 1  # not flooded


# --- permission reverse test -------------------------------------------------
async def test_admin_ack_forbidden_and_audited(sm, iclient):
    alarm_id, _ = await _seed_alarm(sm)
    r = await iclient.post(
        f"/api/v1/alarms/{alarm_id}/ack", headers=_headers("admin"), json={"note": "x"}
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"

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
    assert row.role == "admin"
    assert "alarm.ack" in (row.reason or "")


# --- input defense -----------------------------------------------------------
async def test_ack_note_nul_rejected(sm, iclient):
    alarm_id, _ = await _seed_alarm(sm)
    r = await iclient.post(
        f"/api/v1/alarms/{alarm_id}/ack", headers=_headers("operator"), json={"note": "bad\x00"}
    )
    assert r.status_code == 422


async def test_maintenance_report_nul_rejected(sm, iclient):
    r = await iclient.post(
        "/api/v1/maintenance-reports",
        headers=_headers("operator"),
        json={"device": "AXIS-04", "actions_taken": ["clean\x00"], "result": "ok"},
    )
    assert r.status_code == 422


# --- events ------------------------------------------------------------------
async def test_alarm_new_event_published(sm, fake_redis):
    from app.events import channels
    from app.events.publisher import EventPublisher
    from app.services.alarm_service import AlarmService

    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(channels.ALARM)
    async with sm() as s:
        await AlarmService(s, EventPublisher(fake_redis)).raise_from_fallback(
            device="AXIS-04",
            rule="residual_gt_3sigma",
            scenario_id="01_Pick_and_Place",
            correlation_id="corr-1",
        )
    received = None
    for _ in range(10):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
        if msg is not None:
            received = msg
            break
    assert received is not None
    env = json.loads(received["data"])
    assert env["event_type"] == "alarm:new"
    assert env["correlation_id"] == "corr-1"
    assert {"event_id", "timestamp", "schema_version", "payload"} <= env.keys()
    assert env["payload"]["rule"] == "residual_gt_3sigma"
    await pubsub.unsubscribe(channels.ALARM)


# --- snapshot real counts + maintenance resolves alarm -----------------------
async def test_snapshot_reflects_real_alarms(sm, iclient):
    await _seed_alarm(sm, severity="critical")
    r = await iclient.get("/api/v1/ui/snapshot?device=AXIS-04", headers=_headers("operator"))
    assert r.status_code == 200
    alarms = r.json()["alarms"]
    assert alarms["active"] >= 1
    assert alarms["critical"] >= 1


async def test_maintenance_report_resolves_alarm(sm, iclient):
    alarm_id, _ = await _seed_alarm(sm)
    r = await iclient.post(
        "/api/v1/maintenance-reports",
        headers=_headers("operator"),
        json={
            "device": "AXIS-04",
            "actions_taken": ["replaced bearing"],
            "result": "fixed",
            "alarm_id": alarm_id,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["residual_recovery_status"] == "observing"

    r2 = await iclient.get(f"/api/v1/alarms/{alarm_id}", headers=_headers("operator"))
    assert r2.json()["status"] == "resolved"
