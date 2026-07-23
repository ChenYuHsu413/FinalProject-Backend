"""Health endpoint is reachable without a service token (container healthcheck)."""

from __future__ import annotations


async def test_health_ok_without_token(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "ai-servo-backend"
    assert "version" in body
    assert "schema_version" in body


async def test_health_sets_correlation_id_header(client):
    resp = await client.get("/api/v1/health")
    assert resp.headers.get("X-Correlation-ID")
