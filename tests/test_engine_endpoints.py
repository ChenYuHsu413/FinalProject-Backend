"""Engine endpoint tests — populated vs missing data dir (no Postgres needed).

Success responses need only a populated ENGINE_DATA_DIR; the trust boundary's DB
write only happens on denial, which these tests do not trigger.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

SERVICE_TOKEN = "test-service-token"
HEADERS = {
    "Authorization": f"Bearer {SERVICE_TOKEN}",
    "X-User-ID": "admin-1",
    "X-User-Role": "admin",
    "X-Correlation-ID": "cid-engine",
}
S = "01_Pick_and_Place"

# (url, needs_scenario_query)
ENDPOINTS = [
    (f"/api/v1/l1/realtime?scenario_id={S}", True),
    (f"/api/v1/l1/latency?scenario_id={S}", True),
    (f"/api/v1/l1/model?scenario_id={S}", True),
    (f"/api/v1/l2/latest?scenario_id={S}", True),
    (f"/api/v1/l2/trend?scenario_id={S}", True),
    (f"/api/v1/l3/latest?scenario_id={S}", True),
    (f"/api/v1/l3/shadow?scenario_id={S}", True),
    (f"/api/v1/l3/models?scenario_id={S}", True),
    (f"/api/v1/shap/diagnosis?scenario_id={S}", True),
    (f"/api/v1/shap/summary?scenario_id={S}", True),
    ("/api/v1/fallback/events", False),
    (f"/api/v1/fallback/stats?scenario_id={S}", True),
    ("/api/v1/scenarios", False),
    ("/api/v1/scenario-library", False),
    (f"/api/v1/residual/status?scenario_id={S}", True),
    (f"/api/v1/ensemble/status?scenario_id={S}", True),
    (f"/api/v1/control-mode?scenario_id={S}", True),
    ("/api/v1/data-lifecycle", False),
]


def _set_engine_dir(path: str | None) -> str | None:
    from app.core import settings as settings_mod

    old = os.environ.get("ENGINE_DATA_DIR")
    if path is None:
        os.environ.pop("ENGINE_DATA_DIR", None)
    else:
        os.environ["ENGINE_DATA_DIR"] = path
    settings_mod.get_settings.cache_clear()
    return old


@pytest_asyncio.fixture
async def engine_client(tmp_path):
    from app.mock.simulator import MockSimulator

    MockSimulator(str(tmp_path)).generate_all()
    old = _set_engine_dir(str(tmp_path))
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    _set_engine_dir(old)


@pytest_asyncio.fixture
async def empty_engine_client(tmp_path):
    old = _set_engine_dir(str(tmp_path / "empty"))
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    _set_engine_dir(old)


@pytest.mark.parametrize("url,_needs", ENDPOINTS)
async def test_endpoint_returns_200_with_data(engine_client, url, _needs):
    resp = await engine_client.get(url, headers=HEADERS)
    assert resp.status_code == 200, (url, resp.text)


async def test_field_fidelity_spot_checks(engine_client):
    rt = (await engine_client.get(f"/api/v1/l1/realtime?scenario_id={S}", headers=HEADERS)).json()
    assert rt["predictions"]["DV_mean"] == 0.13
    assert rt["predictions"]["ylabel_mode"] == "LN"
    assert rt["latency"]["within_1ms_ratio"] == 1.0

    lat = (await engine_client.get(f"/api/v1/l1/latency?scenario_id={S}", headers=HEADERS)).json()
    assert "within_1ms_ratio" in lat and "total_inferences" in lat

    sc = (await engine_client.get("/api/v1/scenarios", headers=HEADERS)).json()
    assert set(sc["scenarios"]) == {"01_Pick_and_Place", "18_Ball_Screw", "34_Rotor_Demag"}

    lib = (await engine_client.get("/api/v1/scenario-library", headers=HEADERS)).json()
    assert lib["total_scenarios"] == 40
    assert lib["active_scenarios"] == 3


async def test_unknown_scenario_is_404(engine_client):
    resp = await engine_client.get(
        "/api/v1/l1/realtime?scenario_id=99_Nonexistent", headers=HEADERS
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_path_traversal_scenario_is_404(engine_client):
    resp = await engine_client.get("/api/v1/l1/model?scenario_id=../../secret", headers=HEADERS)
    assert resp.status_code == 404


async def test_missing_data_is_404_not_500(empty_engine_client):
    resp = await empty_engine_client.get(f"/api/v1/l1/realtime?scenario_id={S}", headers=HEADERS)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_fallback_events_pagination(engine_client):
    resp = await engine_client.get("/api/v1/fallback/events?page=1&limit=2", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 1 and body["limit"] == 2
    assert len(body["events"]) <= 2
