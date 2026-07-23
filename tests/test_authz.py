"""authz/permissions endpoint returns the authoritative role→permission table."""

from __future__ import annotations

from app.core.permissions import ALL_PERMISSIONS, VALID_ROLES


async def test_permissions_shape(client, auth_headers):
    resp = await client.get("/api/v1/authz/permissions", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()

    assert body["schema_version"]
    assert set(body["permissions"]) == set(ALL_PERMISSIONS)
    assert set(body["roles"].keys()) == set(VALID_ROLES)


async def test_admin_is_not_a_super_operator(client, auth_headers):
    # design-frontend.md §6.1: admin has read-only device control.
    resp = await client.get("/api/v1/authz/permissions", headers=auth_headers)
    admin = set(resp.json()["roles"]["admin"])
    assert "cycle.start" not in admin
    assert "cycle.stop" not in admin
    assert "mode.switch" not in admin
    assert "alarm.ack" not in admin
    # But admin owns governance/approve codes.
    assert "model.promote.approve" in admin
    assert "system.settings" in admin


async def test_operator_owns_console_actions(client, auth_headers):
    resp = await client.get("/api/v1/authz/permissions", headers=auth_headers)
    operator = set(resp.json()["roles"]["operator"])
    assert {"cycle.start", "cycle.stop", "safety.stop_request", "alarm.ack"} <= operator


async def test_approval_codes_are_split_propose_vs_approve(client, auth_headers):
    # D1.5a: engineer proposes, admin approves — never the same code, so 同人禁核
    # is enforceable at the permission layer (design-backend §6.2).
    resp = await client.get("/api/v1/authz/permissions", headers=auth_headers)
    roles = resp.json()["roles"]
    engineer, admin = set(roles["engineer"]), set(roles["admin"])
    for kind in ("model.promote", "scenario.activate", "param.tune"):
        assert f"{kind}.propose" in engineer
        assert f"{kind}.propose" not in admin
        assert f"{kind}.approve" in admin
        assert f"{kind}.approve" not in engineer


async def test_safety_stop_request_granted_to_all_roles(client, auth_headers):
    # D1.5b: E-Stop request is a safety action, exempt from least-privilege.
    resp = await client.get("/api/v1/authz/permissions", headers=auth_headers)
    roles = resp.json()["roles"]
    for role in ("operator", "engineer", "admin"):
        assert "safety.stop_request" in roles[role]
