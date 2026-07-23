"""App factory + lifespan (PROMPT §4).

Wires the trust-boundary middleware, unified error handlers, and the router
groups (health, authz, governance/audit, engine/*). The mock simulator runs in
the worker, not here (DECISIONS D3.4), so the API stays stateless.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.db import dispose_engine
from app.core.errors import build_error_response, correlation_id_of, register_exception_handlers
from app.core.security import TrustBoundaryMiddleware
from app.core.settings import get_settings
from app.repositories.files.engine_repo import EngineDataNotFound
from app.routers import authz, health
from app.routers.engine import (
    control_mode,
    data_lifecycle,
    ensemble,
    fallback,
    l1,
    l2,
    l3,
    residual,
    scenario_library,
    scenarios,
    shap,
)
from app.routers.governance import audit

API_PREFIX = "/api/v1"

_ENGINE_ROUTERS = (
    l1.router,
    l2.router,
    l3.router,
    shap.router,
    fallback.router,
    scenarios.router,
    scenario_library.router,
    residual.router,
    ensemble.router,
    control_mode.router,
    data_lifecycle.router,
)


async def _engine_not_found_handler(request: Request, exc: EngineDataNotFound) -> JSONResponse:
    # Missing engine data / unknown scenario is a documented 404, never a 500
    # (batch-3 acceptance #2/#3).
    return build_error_response(
        status_code=404,
        code="NOT_FOUND",
        message=str(exc),
        correlation_id=correlation_id_of(request),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Batch 3+: open Redis / start mock simulator here.
    yield
    # The DB engine is lazily created on first request; dispose it on shutdown.
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="AI SERVO PLATFORM Backend",
        version=settings.api_version,
        lifespan=lifespan,
    )

    # Trust boundary runs before routing.
    app.add_middleware(TrustBoundaryMiddleware)

    register_exception_handlers(app)
    app.add_exception_handler(EngineDataNotFound, _engine_not_found_handler)

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(authz.router, prefix=API_PREFIX)
    app.include_router(audit.router, prefix=API_PREFIX)
    for engine_router in _ENGINE_ROUTERS:
        app.include_router(engine_router, prefix=API_PREFIX)

    return app


app = create_app()
