"""Trust-boundary middleware: service token + X-User-* validation (design-backend §1)."""

from __future__ import annotations

import uuid


async def test_missing_service_token_is_forbidden(client):
    resp = await client.get("/api/v1/authz/permissions")
    assert resp.status_code == 403
    err = resp.json()["error"]
    assert err["code"] == "FORBIDDEN"
    assert err["correlation_id"]


async def test_wrong_service_token_is_forbidden(client):
    resp = await client.get(
        "/api/v1/authz/permissions",
        headers={"Authorization": "Bearer nope"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


async def test_valid_token_allows_read(client, auth_headers):
    resp = await client.get("/api/v1/authz/permissions", headers=auth_headers)
    assert resp.status_code == 200


async def test_mutation_missing_identity_headers_is_400(client, auth_headers):
    # POST to any /api/v1 path: middleware runs before routing, so identity
    # headers are checked before the 405/404 for the (undefined) route.
    resp = await client.post("/api/v1/authz/permissions", headers=auth_headers)
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert set(err["details"]["missing_headers"]) == {
        "X-Correlation-ID",
        "X-User-ID",
        "X-User-Role",
    }


async def test_mutation_unknown_role_is_400(client, auth_headers):
    headers = {
        **auth_headers,
        "X-Correlation-ID": str(uuid.uuid4()),
        "X-User-ID": "user-1",
        "X-User-Role": "superuser",
    }
    resp = await client.post("/api/v1/authz/permissions", headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_mutation_valid_identity_passes_middleware(client, auth_headers):
    # Valid token + identity → middleware lets it through; route is GET-only so
    # FastAPI returns 405 (method not allowed), proving the boundary passed.
    headers = {
        **auth_headers,
        "X-Correlation-ID": str(uuid.uuid4()),
        "X-User-ID": "user-1",
        "X-User-Role": "operator",
    }
    resp = await client.post("/api/v1/authz/permissions", headers=headers)
    assert resp.status_code == 405


async def test_correlation_id_is_echoed(client, auth_headers):
    cid = str(uuid.uuid4())
    resp = await client.get(
        "/api/v1/authz/permissions",
        headers={**auth_headers, "X-Correlation-ID": cid},
    )
    assert resp.headers.get("X-Correlation-ID") == cid
