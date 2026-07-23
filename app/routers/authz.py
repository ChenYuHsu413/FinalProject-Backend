"""Authorization sync endpoint.

Exposes the authoritative role → permission table so the Flask BFF can align its
own UI gating and never drift from the backend's enforcement (design-backend.md
§1.1). Requires only the service token (like other infra sync endpoints); it is
not user-role gated.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.permissions import ALL_PERMISSIONS, permissions_table
from app.core.settings import get_settings

router = APIRouter(prefix="/authz", tags=["authz"])


class PermissionsResponse(BaseModel):
    schema_version: str
    permissions: list[str]
    roles: dict[str, list[str]]


@router.get("/permissions", response_model=PermissionsResponse)
def get_permissions() -> PermissionsResponse:
    return PermissionsResponse(
        schema_version=get_settings().schema_version,
        permissions=list(ALL_PERMISSIONS),
        roles=permissions_table(),
    )
