"""Test fixtures. Sets the service token before the app is imported."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

# asyncpg + the Windows Proactor loop raise a spurious "'NoneType' has no
# attribute 'send'" during connection GC across tests. The Selector loop avoids
# it. No effect on Linux CI.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

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


@pytest.fixture(autouse=True)
def _reset_db_engine():
    """Force the async engine to rebuild on each test's own event loop.

    pytest-asyncio uses a function-scoped loop, but the engine is a module
    singleton — without this, the 2nd DB-backed test reuses an engine bound to the
    1st test's (now-closed) loop and raises RuntimeError. Nulling the globals
    (no await) makes the next get_engine() rebuild on the current loop.
    """
    import app.core.db as db

    db._engine = None
    db._sessionmaker = None
    yield
    db._engine = None
    db._sessionmaker = None


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
