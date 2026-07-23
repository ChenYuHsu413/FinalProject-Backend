"""Unit tests for the permission table and the require_permission dependency."""

from __future__ import annotations

import pytest
from app.core.errors import AppError
from app.core.permissions import (
    ALL_PERMISSIONS,
    ROLE_PERMISSIONS,
    has_permission,
    permissions_for,
)
from app.core.security import Principal, require_permission


def test_every_granted_permission_is_declared():
    known = set(ALL_PERMISSIONS)
    for role, perms in ROLE_PERMISSIONS.items():
        unknown = perms - known
        assert not unknown, f"{role} grants undeclared permissions: {unknown}"


def test_no_duplicate_permission_codes():
    assert len(ALL_PERMISSIONS) == len(set(ALL_PERMISSIONS))


def test_has_permission_and_permissions_for():
    assert has_permission("operator", "cycle.start")
    assert not has_permission("operator", "system.settings")
    assert not has_permission("nobody", "cycle.start")
    assert permissions_for("engineer") == sorted(ROLE_PERMISSIONS["engineer"])


class _FakeRequest:
    def __init__(self, role, user_id="u-1", cid="cid-1"):
        self.state = type("S", (), {})()
        self.state.user_role = role
        self.state.user_id = user_id
        self.state.correlation_id = cid


def test_require_permission_allows_authorized_role():
    dep = require_permission("cycle.start")
    principal = dep(_FakeRequest("operator"))
    assert isinstance(principal, Principal)
    assert principal.role == "operator"


def test_require_permission_forbids_unauthorized_role():
    dep = require_permission("system.settings")
    with pytest.raises(AppError) as exc:
        dep(_FakeRequest("operator"))
    assert exc.value.code == "FORBIDDEN"
    assert exc.value.status_code == 403


def test_require_permission_rejects_missing_role():
    dep = require_permission("cycle.start")
    with pytest.raises(AppError) as exc:
        dep(_FakeRequest(None))
    assert exc.value.status_code == 400
