"""App factory + lifespan (PROMPT §4).

Batch 1 wires the trust-boundary middleware, unified error handlers, and the
skeleton routers (health, authz). DB/Redis connections and the dev simulator are
introduced in later batches; the lifespan is the seam where they will attach.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.db import dispose_engine
from app.core.errors import register_exception_handlers
from app.core.security import TrustBoundaryMiddleware
from app.core.settings import get_settings
from app.routers import authz, health
from app.routers.governance import audit

API_PREFIX = "/api/v1"


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

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(authz.router, prefix=API_PREFIX)
    app.include_router(audit.router, prefix=API_PREFIX)

    return app


app = create_app()
