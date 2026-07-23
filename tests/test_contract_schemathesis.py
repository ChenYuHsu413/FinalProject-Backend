"""Schemathesis contract test against the live OpenAPI schema (batch DoD).

Fuzzes every operation and asserts responses conform to the documented schema /
status codes (no undocumented 500s). Public endpoints (health, authz) run
everywhere; audit endpoints need a DB, so those cases self-skip unless
DATABASE_URL is configured (CI sets it after applying migrations).
"""

from __future__ import annotations

import os
import uuid

import pytest
import schemathesis
from hypothesis import HealthCheck, settings

# FastAPI emits OpenAPI 3.1; enable schemathesis' 3.1 support.
schemathesis.experimental.OPEN_API_3_1.enable()

os.environ.setdefault("SERVICE_TOKEN", "test-service-token")
SERVICE_TOKEN = os.environ["SERVICE_TOKEN"]

from app.main import app  # noqa: E402 — after env/experimental setup

schema = schemathesis.from_asgi("/openapi.json", app)

_HEADERS = {
    "Authorization": f"Bearer {SERVICE_TOKEN}",
    "X-User-ID": "admin-1",
    "X-User-Role": "admin",
    "X-Correlation-ID": str(uuid.uuid4()),
}


@schema.parametrize()
@settings(max_examples=10, deadline=None, suppress_health_check=list(HealthCheck))
def test_openapi_contract(case):
    if case.path.startswith("/api/v1/audit") and not os.environ.get("DATABASE_URL"):
        pytest.skip("audit endpoints require a database")
    case.call_and_validate(headers=_HEADERS)
