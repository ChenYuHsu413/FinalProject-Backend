"""Unit tests for batch-2 input hardening (no DB needed).

Covers the schemathesis-found 500s at the model/handler layer: NUL bytes,
overlong strings, and JSON-unsafe error details.
"""

from __future__ import annotations

import pytest
from app.core.errors import _json_safe
from app.routers.governance.audit import AuditEventIn
from pydantic import ValidationError


def test_valid_event_ok():
    ev = AuditEventIn(action="auth.login", user_id="u1", new_value={"ip": "10.0.0.1"})
    assert ev.action == "auth.login"


def test_rejects_nul_in_scalar():
    with pytest.raises(ValidationError):
        AuditEventIn(action="auth.\x00login")


def test_rejects_nul_in_nested_value():
    with pytest.raises(ValidationError):
        AuditEventIn(action="x", new_value={"note": "bad\x00byte"})


def test_rejects_nul_in_dict_key():
    with pytest.raises(ValidationError):
        AuditEventIn(action="x", old_value={"k\x00": "v"})


def test_rejects_overlong_action():
    with pytest.raises(ValidationError):
        AuditEventIn(action="a" * 65)  # column is VARCHAR(64)


def test_rejects_overlong_role():
    with pytest.raises(ValidationError):
        AuditEventIn(action="x", role="r" * 33)  # column is VARCHAR(32)


def test_json_safe_stringifies_exceptions():
    payload = {"errors": [{"ctx": {"error": ValueError("boom")}}]}
    safe = _json_safe(payload)
    assert safe["errors"][0]["ctx"]["error"] == "boom"


def test_json_safe_passes_primitives_and_recurses():
    assert _json_safe({"a": [1, "x", True, None]}) == {"a": [1, "x", True, None]}
    assert _json_safe({1: "int-key"}) == {"1": "int-key"}
