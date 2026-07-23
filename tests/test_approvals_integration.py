"""Approval + training + integrations integration tests (require PostgreSQL; gated).

Covers batch-7 acceptance:
* 同人禁核 (`decided_by != proposed_by`) → 403; admin has no propose path (403).
* Approval state machine over HTTP: double-approve → 409 (terminal).
* model_promotion side effect: models.jsonl shadow→active rewrite + model:changed
  on ai_servo:l3_deploy; apply failure → approved + apply_failed + alarm (D7.3).
* param_tuning post-approval five-check (§11.3): pass → applied, fail → failed.
* /approvals/summary counts; /system/integrations mock_mode flag.
* Full demo chain: training job → passed → auto-proposed model_promotion →
  admin approve → models.jsonl rewrite → model:changed.
* Over-privilege: operator approve → 403 AND audited.
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
SCENARIO = "01_Pick_and_Place"


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
async def engine_dir(tmp_path):
    """Populate ENGINE_DATA_DIR with the mock model registry + a shadow candidate."""
    from app.core import settings as settings_mod
    from app.mock.simulator import MockSimulator
    from app.repositories.files.model_registry_repo import ModelRegistryFileRepository

    MockSimulator(str(tmp_path)).generate_all()
    # Add a shadow candidate v1.0.4 so a model_promotion has something to flip.
    ModelRegistryFileRepository(str(tmp_path)).add_shadow(
        scenario_id=SCENARIO, version="v1.0.4", file_hash="sha-cand", metrics={"RMSE": 0.0172}
    )
    old = os.environ.get("ENGINE_DATA_DIR")
    os.environ["ENGINE_DATA_DIR"] = str(tmp_path)
    settings_mod.get_settings.cache_clear()
    yield str(tmp_path)
    if old is None:
        os.environ.pop("ENGINE_DATA_DIR", None)
    else:
        os.environ["ENGINE_DATA_DIR"] = old
    settings_mod.get_settings.cache_clear()


@pytest_asyncio.fixture
async def sm(engine_dir):
    from app.core import settings as settings_mod
    from app.core.db import dispose_engine, get_sessionmaker

    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = PG_URL
    settings_mod.get_settings.cache_clear()
    await dispose_engine()

    smk = get_sessionmaker()
    async with smk() as s:
        await s.execute(text("DELETE FROM approvals"))
        await s.execute(text("DELETE FROM training_jobs"))
        await s.execute(text("DELETE FROM alarms"))
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


async def _drain(pubsub, channel, expected: int) -> list[dict]:
    out: list[dict] = []
    for _ in range(40):
        m = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.3)
        if m is not None:
            out.append(json.loads(m["data"]))
        if len(out) >= expected:
            break
    return out


# --- propose permissions (pre-check #1) --------------------------------------
async def test_engineer_can_propose_admin_cannot(sm, iclient):
    body = {
        "type": "model_promotion",
        "scenario_id": SCENARIO,
        "summary": {"from": "v1.0.3", "to": "v1.0.4", "rmse_improvement_pct": 5.2},
        "reason": "shadow passed",
    }
    r = await iclient.post("/api/v1/approvals", headers=_headers("engineer", "eng-1"), json=body)
    assert r.status_code == 201, r.text
    assert r.json()["state"] == "pending"

    # Admin holds no propose code → the propose path does not exist for admin.
    r2 = await iclient.post("/api/v1/approvals", headers=_headers("admin", "adm-1"), json=body)
    assert r2.status_code == 403
    assert r2.json()["error"]["details"]["required"] == "model.promote.propose"


# --- 同人禁核 (pre-check #1/#2) -----------------------------------------------
async def test_same_person_approval_forbidden(sm, fake_redis):
    from app.services.approval_service import ApprovalService

    # Construct an approval proposed by 'adm-self' directly (bypassing the propose
    # permission gate) so we can test the service-layer 同人禁核 in isolation.
    async with sm() as s:
        svc = ApprovalService(s)
        appr = await svc.propose(
            type="model_promotion",
            summary={"to": "v1.0.4"},
            reason="x",
            risk="low",
            scenario_id=SCENARIO,
            device=None,
            user_id="adm-self",
            role="admin",
            correlation_id="c",
        )
        aid = appr.approval_id
    # Same person approving their own proposal → 403.
    async with sm() as s:
        from app.core.errors import AppError

        with pytest.raises(AppError) as ei:
            await ApprovalService(s).approve(
                aid, note="self", user_id="adm-self", role="admin", correlation_id="c"
            )
    assert ei.value.status_code == 403


# --- double-approve is a terminal-state 409 (pre-check #2) --------------------
async def test_double_approve_is_409(sm, iclient):
    body = {"type": "model_promotion", "scenario_id": SCENARIO, "summary": {"to": "v1.0.4"}}
    r = await iclient.post("/api/v1/approvals", headers=_headers("engineer", "eng-1"), json=body)
    aid = r.json()["approval_id"]

    r1 = await iclient.post(
        f"/api/v1/approvals/{aid}/approve", headers=_headers("admin", "adm-1"), json={"note": "ok"}
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["state"] == "approved"
    r2 = await iclient.post(
        f"/api/v1/approvals/{aid}/approve",
        headers=_headers("admin", "adm-1"),
        json={"note": "again"},
    )
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "CONFLICT"


# --- model_promotion side effect: models.jsonl rewrite + model:changed --------
async def test_model_promotion_rewrites_registry_and_emits_model_changed(
    sm, fake_redis, engine_dir
):
    from app.events import channels
    from app.events.publisher import EventPublisher
    from app.repositories.files.model_registry_repo import ModelRegistryFileRepository
    from app.services.approval_service import ApprovalService

    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(channels.L3_DEPLOY)

    async with sm() as s:
        svc = ApprovalService(s, EventPublisher(fake_redis))
        appr = await svc.propose(
            type="model_promotion",
            summary={"from": "v1.0.3", "to": "v1.0.4", "rmse_improvement_pct": 5.2},
            reason="promote",
            risk="medium",
            scenario_id=SCENARIO,
            device=None,
            user_id="eng-1",
            role="engineer",
            correlation_id="c",
        )
        aid = appr.approval_id
    async with sm() as s:
        approved = await ApprovalService(s, EventPublisher(fake_redis)).approve(
            aid, note="ship it", user_id="adm-1", role="admin", correlation_id="c"
        )
    assert approved.state == "approved"
    assert approved.side_effect_status == "applied"

    # models.jsonl: v1.0.4 is now active, v1.0.3 demoted to archived.
    models = ModelRegistryFileRepository(engine_dir).list_for_scenario(SCENARIO)
    by_version = {m["version"]: m["status"] for m in models}
    assert by_version["v1.0.4"] == "active"
    assert by_version["v1.0.3"] == "archived"

    # model:changed emitted on ai_servo:l3_deploy (NOT governance).
    events = await _drain(pubsub, channels.L3_DEPLOY, 1)
    types = [e["event_type"] for e in events]
    assert "model:changed" in types
    changed = next(e for e in events if e["event_type"] == "model:changed")
    assert changed["payload"]["model_version"] == "v1.0.4"
    assert changed["payload"]["status"] == "active"
    await pubsub.unsubscribe(channels.L3_DEPLOY)


async def test_model_promotion_apply_failure_keeps_approved_and_alarms(sm, fake_redis, engine_dir):
    from app.events.publisher import EventPublisher
    from app.repositories.pg.models import Alarm
    from app.services.approval_service import ApprovalService

    # Propose a promotion to a version that does NOT exist in the registry.
    async with sm() as s:
        svc = ApprovalService(s, EventPublisher(fake_redis))
        appr = await svc.propose(
            type="model_promotion",
            summary={"to": "v9.9.9-missing"},
            reason="bad",
            risk="high",
            scenario_id=SCENARIO,
            device=None,
            user_id="eng-1",
            role="engineer",
            correlation_id="c",
        )
        aid = appr.approval_id
    async with sm() as s:
        approved = await ApprovalService(s, EventPublisher(fake_redis)).approve(
            aid, note="approve anyway", user_id="adm-1", role="admin", correlation_id="c"
        )
    # D7.3: approval stays approved; side effect apply_failed; alarm raised.
    assert approved.state == "approved"
    assert approved.side_effect_status == "apply_failed"
    async with sm() as s:
        alarms = (
            (await s.execute(select(Alarm).where(Alarm.rule == "model_promotion_apply_failed")))
            .scalars()
            .all()
        )
    assert len(alarms) == 1


# --- param_tuning five-check (pre-check #5) -----------------------------------
async def test_param_tuning_pass_applies(sm, fake_redis):
    from app.events.publisher import EventPublisher
    from app.services.approval_service import ApprovalService

    async with sm() as s:
        svc = ApprovalService(s, EventPublisher(fake_redis))
        appr = await svc.propose(
            type="param_tuning",
            summary={
                "device": "AXIS-04",
                "param": "Kp",
                "new": 12.75,
                "delta_pct": 2.8,
                "allowed_range": [10, 14],
            },
            reason="tune",
            risk="low",
            scenario_id=SCENARIO,
            device="AXIS-04",
            user_id="eng-1",
            role="engineer",
            correlation_id="c",
        )
        aid = appr.approval_id
    async with sm() as s:
        approved = await ApprovalService(s, EventPublisher(fake_redis)).approve(
            aid, note="ok", user_id="adm-1", role="admin", correlation_id="c"
        )
    assert approved.state == "approved"
    assert approved.side_effect_status == "applied"


async def test_param_tuning_out_of_range_fails_check(sm, fake_redis):
    from app.events.publisher import EventPublisher
    from app.services.approval_service import ApprovalService

    async with sm() as s:
        svc = ApprovalService(s, EventPublisher(fake_redis))
        appr = await svc.propose(
            type="param_tuning",
            summary={
                "device": "AXIS-04",
                "param": "Kp",
                "new": 99.0,
                "delta_pct": 2.8,
                "allowed_range": [10, 14],
            },
            reason="tune too high",
            risk="low",
            scenario_id=SCENARIO,
            device="AXIS-04",
            user_id="eng-1",
            role="engineer",
            correlation_id="c",
        )
        aid = appr.approval_id
    async with sm() as s:
        approved = await ApprovalService(s, EventPublisher(fake_redis)).approve(
            aid, note="ok", user_id="adm-1", role="admin", correlation_id="c"
        )
    # Approved by admin, but the five-check application failed (D7.3 + §11.3).
    assert approved.state == "approved"
    assert approved.side_effect_status == "failed"
    assert approved.side_effect_detail["failed_check"] == "bounds"


# --- reject requires note; withdraw is proposer-only --------------------------
async def test_reject_requires_note(sm, iclient):
    body = {"type": "model_promotion", "scenario_id": SCENARIO, "summary": {"to": "v1.0.4"}}
    r = await iclient.post("/api/v1/approvals", headers=_headers("engineer", "eng-1"), json=body)
    aid = r.json()["approval_id"]
    r2 = await iclient.post(
        f"/api/v1/approvals/{aid}/reject", headers=_headers("admin", "adm-1"), json={}
    )
    assert r2.status_code == 422  # note is required


async def test_withdraw_by_proposer_only(sm, iclient):
    body = {"type": "model_promotion", "scenario_id": SCENARIO, "summary": {"to": "v1.0.4"}}
    r = await iclient.post("/api/v1/approvals", headers=_headers("engineer", "eng-1"), json=body)
    aid = r.json()["approval_id"]

    # A different engineer cannot withdraw someone else's proposal.
    r2 = await iclient.post(
        f"/api/v1/approvals/{aid}/withdraw", headers=_headers("engineer", "eng-2")
    )
    assert r2.status_code == 403
    # The proposer can.
    r3 = await iclient.post(
        f"/api/v1/approvals/{aid}/withdraw", headers=_headers("engineer", "eng-1")
    )
    assert r3.status_code == 200
    assert r3.json()["state"] == "withdrawn"


# --- over-privilege reverse test (pre-check cross-cutting) --------------------
async def test_operator_approve_forbidden_and_audited(sm, iclient):
    body = {"type": "model_promotion", "scenario_id": SCENARIO, "summary": {"to": "v1.0.4"}}
    r = await iclient.post("/api/v1/approvals", headers=_headers("engineer", "eng-1"), json=body)
    aid = r.json()["approval_id"]

    r2 = await iclient.post(
        f"/api/v1/approvals/{aid}/approve", headers=_headers("operator", "op-1"), json={"note": "x"}
    )
    assert r2.status_code == 403  # operator lacks approval.read gate

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
    assert row.role == "operator"


# --- /approvals/summary (pre-check #6) ---------------------------------------
async def test_approvals_summary_counts(sm, iclient):
    for _ in range(2):
        await iclient.post(
            "/api/v1/approvals",
            headers=_headers("engineer", "eng-1"),
            json={"type": "model_promotion", "scenario_id": SCENARIO, "summary": {"to": "v1.0.4"}},
        )
    await iclient.post(
        "/api/v1/approvals",
        headers=_headers("engineer", "eng-1"),
        json={
            "type": "param_tuning",
            "device": "AXIS-04",
            "summary": {"param": "Kp", "new": 12.5, "delta_pct": 1.0, "allowed_range": [10, 14]},
        },
    )
    r = await iclient.get("/api/v1/approvals/summary", headers=_headers("admin", "adm-1"))
    assert r.status_code == 200
    body = r.json()
    assert body["by_type"]["model_promotion"] == 2
    assert body["by_type"]["param_tuning"] == 1
    assert body["total"] == 3
    assert body["oldest_wait_s"] >= 0


# --- /system/integrations (pre-check #7) -------------------------------------
async def test_system_integrations_mock_flag(sm, iclient):
    r = await iclient.get("/api/v1/system/integrations", headers=_headers("admin", "adm-1"))
    assert r.status_code == 200
    body = r.json()
    assert body["mock_mode"] is True  # PROMPT §7 honesty flag
    names = {s["name"] for s in body["services"]}
    assert {"fastapi", "redis", "postgresql", "ntp"} <= names
    assert body["version_consistency"]["verified"] is True
    # postgres probe succeeded (we have a live DB) → connected.
    pg = next(s for s in body["services"] if s["name"] == "postgresql")
    assert pg["status"] == "connected"


async def test_system_integrations_forbidden_for_operator(sm, iclient):
    r = await iclient.get("/api/v1/system/integrations", headers=_headers("operator", "op-1"))
    assert r.status_code == 403


# --- FULL DEMO CHAIN: train → propose → approve → model:changed (pre-check #8)-
async def test_full_governance_loop_train_to_model_changed(sm, fake_redis, engine_dir):
    from app.events import channels
    from app.events.publisher import EventPublisher
    from app.repositories.files.model_registry_repo import ModelRegistryFileRepository
    from app.repositories.pg.training_repo import TrainingRepository
    from app.services.approval_service import ApprovalService
    from app.services.training_service import TrainingService

    pub = EventPublisher(fake_redis)
    gov = fake_redis.pubsub()
    await gov.subscribe(channels.GOVERNANCE)
    deploy = fake_redis.pubsub()
    await deploy.subscribe(channels.L3_DEPLOY)

    # 1. Engineer triggers a full_retrain job.
    async with sm() as s:
        job = await TrainingService(s, pub).create(
            type="full_retrain",
            scenario_id=SCENARIO,
            reason="drift",
            data_window="24h",
            user_id="eng-1",
            role="engineer",
            correlation_id="chain-c",
        )
        await s.commit()
        jid = job.job_id

    # 2. Worker advances the job to `passed` (spawns the model_promotion approval).
    for _ in range(6):
        async with sm() as s:
            svc = TrainingService(s, pub)
            j = await TrainingRepository(s).get(jid)
            if j.status == "passed":
                break
            await svc.advance(j)
            await s.commit()

    async with sm() as s:
        j = await TrainingRepository(s).get(jid)
    assert j.status == "passed"
    assert j.approval_id is not None
    aid = j.approval_id

    # approval:new was published on governance for the auto-proposal.
    gov_events = await _drain(gov, channels.GOVERNANCE, 1)
    assert any(e["event_type"] == "approval:new" for e in gov_events)

    # 3. Admin approves → models.jsonl rewrite + model:changed.
    async with sm() as s:
        approved = await ApprovalService(s, pub).approve(
            aid, note="ship", user_id="adm-1", role="admin", correlation_id="chain-c"
        )
    assert approved.state == "approved"
    assert approved.side_effect_status == "applied"

    models = ModelRegistryFileRepository(engine_dir).list_for_scenario(SCENARIO)
    active = [m for m in models if m["status"] == "active"]
    assert len(active) == 1
    assert active[0]["version"] == j.result_model_version  # the trained candidate is now active

    deploy_events = await _drain(deploy, channels.L3_DEPLOY, 1)
    assert any(e["event_type"] == "model:changed" for e in deploy_events)
    await gov.unsubscribe(channels.GOVERNANCE)
    await deploy.unsubscribe(channels.L3_DEPLOY)


# --- training job REST + cancel + shadow comparisons -------------------------
async def test_training_job_rest_and_cancel(sm, iclient):
    r = await iclient.post(
        "/api/v1/training/jobs",
        headers=_headers("engineer", "eng-1"),
        json={"type": "finetune", "scenario_id": SCENARIO, "reason": "tune", "data_window": "8h"},
    )
    assert r.status_code == 202, r.text
    jid = r.json()["job_id"]
    assert r.json()["status"] == "queued"

    # operator lacks model.retrain → cannot trigger.
    r_op = await iclient.post(
        "/api/v1/training/jobs",
        headers=_headers("operator", "op-1"),
        json={"type": "finetune", "scenario_id": SCENARIO},
    )
    assert r_op.status_code == 403

    # cancel (engineer) → cancelled.
    rc = await iclient.post(
        f"/api/v1/training/jobs/{jid}/cancel", headers=_headers("engineer", "eng-1")
    )
    assert rc.status_code == 200
    assert rc.json()["status"] == "cancelled"
    # cancelling a terminal job again → 409.
    rc2 = await iclient.post(
        f"/api/v1/training/jobs/{jid}/cancel", headers=_headers("engineer", "eng-1")
    )
    assert rc2.status_code == 409
