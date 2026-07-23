"""Liveness endpoint. Exempt from the trust boundary (container healthcheck)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.settings import get_settings

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    schema_version: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service="ai-servo-backend",
        version=settings.api_version,
        schema_version=settings.schema_version,
    )
