"""Test fixtures. Sets the service token before the app is imported."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

SERVICE_TOKEN = "test-service-token"

# FORCE (not setdefault): CI sets SERVICE_TOKEN=ci-test-token in the job env, and
# a setdefault would leave that in place — then the app expects one token while
# the tests send another, and every request 403s. Tests must be hermetic and own
# their token regardless of ambient env. Clear the settings cache in case
# something imported settings before this ran.
os.environ["SERVICE_TOKEN"] = SERVICE_TOKEN
os.environ["APP_ENV"] = "dev"

from app.core.settings import get_settings  # noqa: E402 — after env is forced

get_settings.cache_clear()


@pytest.fixture(scope="session")
def service_token() -> str:
    return SERVICE_TOKEN


@pytest.fixture(scope="session")
def auth_headers(service_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {service_token}"}


@pytest_asyncio.fixture
async def client() -> AsyncIterator[Any]:
    from app.main import app
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
