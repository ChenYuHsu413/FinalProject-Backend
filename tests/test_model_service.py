"""External model service adapter tests (batch 8, SEAM B).

Everything runs against ``httpx.MockTransport`` — the suite never touches the
real Space. The contract under test is the one the snapshot depends on: map the
external field names, clamp the DV, cache within the TTL, and **fail loudly here
so the caller can fall back silently**.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest
from app.core.settings import get_settings
from app.repositories.http import model_service

# The snapshot endpoint resolves a DB session (real alarm counts, batch 5).
_needs_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="snapshot needs a database (alarm counts)"
)

HEADERS = {
    "Authorization": "Bearer test-service-token",
    "X-User-ID": "op-1",
    "X-User-Role": "operator",
    "X-Correlation-ID": "cid-model",
}

_OK_BODY = {
    "degradation_score": 0.42,
    "predicted_health_state": "MED",
    "health_state_proba": {"LN": 0.1, "LO": 0.2, "MED": 0.6, "HI": 0.1},
}
_INFO_BODY = {"model_version": "v1", "reg_r2": 0.9444}


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    monkeypatch.setenv("MODEL_SOURCE", "http")
    monkeypatch.setenv("MODEL_SERVICE_URL", "http://model.test")
    get_settings.cache_clear()
    model_service.reset_cache()
    yield
    get_settings.cache_clear()
    model_service.reset_cache()


def _install(monkeypatch, handler) -> list[httpx.Request]:
    """Route the adapter's outbound calls to a MockTransport; return the log.

    ``setdefault`` (not a plain assignment) so the ASGI test client, which passes
    its own transport, is unaffected.
    """
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(_record)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.setdefault("transport", transport)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return seen


def _route(predict_response: httpx.Response):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/servo/model_info":
            return httpx.Response(200, json=_INFO_BODY)
        return predict_response

    return handler


# --- mapping -----------------------------------------------------------------
async def test_predict_maps_fields(monkeypatch):
    _install(monkeypatch, _route(httpx.Response(200, json=_OK_BODY)))

    pred = await model_service.predict("AXIS-04", {"rotor_speed_mean": 1.0})

    assert pred.dv == 0.42
    assert pred.state == "MED"
    assert pred.proba["MED"] == 0.6
    assert pred.version == "v1"


@pytest.mark.parametrize(("score", "expected"), [(1.7, 1.0), (-0.5, 0.0)])
async def test_predict_clamps_dv(monkeypatch, score, expected):
    body = {**_OK_BODY, "degradation_score": score}
    _install(monkeypatch, _route(httpx.Response(200, json=body)))

    pred = await model_service.predict("AXIS-04", {})

    assert pred.dv == expected


async def test_missing_field_raises(monkeypatch):
    body = {k: v for k, v in _OK_BODY.items() if k != "predicted_health_state"}
    _install(monkeypatch, _route(httpx.Response(200, json=body)))

    with pytest.raises(model_service.ModelServiceError):
        await model_service.predict("AXIS-04", {})


# --- transport failures ------------------------------------------------------
async def test_non_dict_body_is_rejected(monkeypatch):
    """A JSON array is unusable. `info` must still hand back a dict — the caller
    does `.get()` on it, and an AttributeError there would 500 the snapshot."""
    _install(monkeypatch, lambda _r: httpx.Response(200, json=[1, 2, 3]))

    with pytest.raises(model_service.ModelServiceError):
        await model_service.predict("AXIS-04", {})
    assert await model_service.info() == {}


async def test_http_500_raises(monkeypatch):
    _install(monkeypatch, _route(httpx.Response(500, json={"detail": "boom"})))

    with pytest.raises(model_service.ModelServiceError):
        await model_service.predict("AXIS-04", {})


async def test_timeout_raises(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow", request=request)

    _install(monkeypatch, handler)

    with pytest.raises(model_service.ModelServiceError):
        await model_service.predict("AXIS-04", {})


async def test_info_returns_empty_on_failure(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _install(monkeypatch, handler)

    assert await model_service.info() == {}


# --- caching -----------------------------------------------------------------
async def test_second_call_is_cached(monkeypatch):
    seen = _install(monkeypatch, _route(httpx.Response(200, json=_OK_BODY)))

    first = await model_service.predict("AXIS-04", {})
    second = await model_service.predict("AXIS-04", {})

    assert first == second
    assert [r.url.path for r in seen].count("/servo/predict") == 1


# Run twice on purpose: pytest-asyncio gives each case its own event loop, so the
# second case is the regression guard for the herd lock binding to the first
# loop (a module-level asyncio.Lock does, once contended — hence _get_lock).
@pytest.mark.parametrize("_round", [1, 2])
async def test_concurrent_callers_collapse_to_one_call(monkeypatch, _round):
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)  # hold the lock long enough to force overlap
        if request.url.path == "/servo/model_info":
            return httpx.Response(200, json=_INFO_BODY)
        return httpx.Response(200, json=_OK_BODY)

    seen = _install(monkeypatch, handler)

    results = await asyncio.gather(*(model_service.predict("AXIS-04", {}) for _ in range(8)))

    assert {r.dv for r in results} == {0.42}
    assert [r.url.path for r in seen].count("/servo/predict") == 1


# --- disabled by default -----------------------------------------------------
async def test_mock_mode_never_calls_out(monkeypatch):
    seen = _install(monkeypatch, _route(httpx.Response(200, json=_OK_BODY)))
    monkeypatch.setenv("MODEL_SOURCE", "mock")
    get_settings.cache_clear()

    with pytest.raises(model_service.ModelServiceError):
        await model_service.predict("AXIS-04", {})

    assert await model_service.info() == {}
    assert seen == []


# --- the rule that matters: a model outage must not break the first screen ---
@_needs_db
async def test_snapshot_still_200_when_model_is_down(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nothing listening", request=request)

    _install(monkeypatch, handler)

    resp = await client.get("/api/v1/ui/snapshot?device=AXIS-04", headers=HEADERS)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["dv"]["value"], float)
    # fell back to the representative values, not the service's
    assert body["model"]["active_version"] == "v3.2.0"
    assert body["health_cards"]["model"]["r2"] == 0.94


@_needs_db
async def test_snapshot_survives_junk_metadata(client, monkeypatch):
    """A non-numeric reg_r2 must not 500 the first screen (snapshot._float_or)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/servo/model_info":
            return httpx.Response(200, json={"model_version": "v1", "reg_r2": "n/a"})
        return httpx.Response(200, json=_OK_BODY)

    _install(monkeypatch, handler)

    resp = await client.get("/api/v1/ui/snapshot?device=AXIS-04", headers=HEADERS)

    assert resp.status_code == 200, resp.text
    assert resp.json()["health_cards"]["model"]["r2"] == 0.94


@_needs_db
async def test_snapshot_uses_model_values_when_up(client, monkeypatch):
    _install(monkeypatch, _route(httpx.Response(200, json=_OK_BODY)))

    resp = await client.get("/api/v1/ui/snapshot?device=AXIS-04", headers=HEADERS)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dv"]["value"] == 0.42
    assert body["model"]["active_version"] == "v1"
    assert body["health_cards"]["model"]["r2"] == 0.9444
