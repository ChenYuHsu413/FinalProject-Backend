"""Test fixtures. Sets the service token before the app is imported."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

SERVICE_TOKEN = "test-service-token"

# Must be set before app.core.settings is first imported/cached.
os.environ.setdefault("SERVICE_TOKEN", SERVICE_TOKEN)
os.environ.setdefault("APP_ENV", "dev")


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
