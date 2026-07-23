"""Trust boundary: service-token auth + X-User-* validation (design-backend.md §1).

Enforced as ASGI middleware so it runs before routing:

* Every ``/api/v1/*`` request (except infra endpoints) must carry
  ``Authorization: Bearer <service_token>`` — the API only trusts the Flask BFF.
* Every **mutation** (POST/PUT/PATCH/DELETE) must carry ``X-Correlation-ID``,
  ``X-User-ID`` and ``X-User-Role``; missing/invalid → 400 (PROMPT §7).
* The correlation id is stashed on ``request.state`` and echoed back as a
  response header so it can flow Flask → FastAPI → Redis → audit.

Route handlers get the caller via the ``Principal`` dependency and enforce the
second-layer permission check with ``require_permission`` (used from batch 2).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.errors import AppError, build_error_response
from app.core.permissions import VALID_ROLES, has_permission
from app.core.settings import get_settings
from app.services.audit_service import record_denied_attempt

# Paths that bypass the trust boundary entirely (infra / self-description).
# Health must be reachable by the container healthcheck, which has no token.
_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/v1/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)

# Service-to-service mutation endpoints: still require the service token, but are
# exempt from the X-User-* requirement because the acting identity lives in the
# request body, not the caller's session (design-backend §5.2, DECISIONS D2.4).
_SERVICE_MUTATION_PATHS: frozenset[str] = frozenset({"/api/v1/audit/events"})

_MUTATION_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_BEARER_PREFIX = "Bearer "


def _is_exempt(path: str) -> bool:
    if path == "/":
        return True
    return any(path.startswith(p) for p in _EXEMPT_PREFIXES)


def _source_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


class TrustBoundaryMiddleware(BaseHTTPMiddleware):
    """Service-token + header validation for the API surface."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Always have a correlation id available for error rendering.
        incoming_cid = request.headers.get("X-Correlation-ID")
        request.state.correlation_id = incoming_cid or str(uuid.uuid4())
        request.state.user_id = None
        request.state.user_role = None

        # Reject NUL bytes in path/query before they reach a Postgres query
        # (asyncpg raises CharacterNotInRepertoireError → 500). 422 is documented
        # on any endpoint with a path/query param. Body NUL is handled by the
        # per-model validators. (Found by schemathesis fuzzing a path param.)
        if "\x00" in path or any("\x00" in v for v in request.query_params.values()):
            return build_error_response(
                status_code=422,
                code="VALIDATION_ERROR",
                message="NUL bytes are not allowed in the request path/query.",
                correlation_id=request.state.correlation_id,
            )

        if _is_exempt(path):
            return await self._call(request, call_next)

        settings = get_settings()

        # --- Layer 1: source authentication (service token) -----------------
        auth = request.headers.get("Authorization", "")
        token = auth[len(_BEARER_PREFIX) :] if auth.startswith(_BEARER_PREFIX) else ""
        if not settings.service_token or token != settings.service_token:
            return await self._deny(
                request,
                status_code=403,
                code="FORBIDDEN",
                message="Invalid or missing service token.",
                reason="invalid_service_token",
            )

        # --- Layer 2: identity headers, required on mutations ---------------
        # Service-to-service mutations carry identity in the body, not headers.
        header_required = (
            request.method in _MUTATION_METHODS and path not in _SERVICE_MUTATION_PATHS
        )
        if header_required:
            missing = [
                h
                for h in ("X-Correlation-ID", "X-User-ID", "X-User-Role")
                if not request.headers.get(h)
            ]
            if missing:
                return await self._deny(
                    request,
                    status_code=400,
                    code="VALIDATION_ERROR",
                    message="Missing required identity headers.",
                    reason="missing_identity_headers",
                    details={"missing_headers": missing},
                )

            role = request.headers.get("X-User-Role", "")
            if role not in VALID_ROLES:
                return await self._deny(
                    request,
                    status_code=400,
                    code="VALIDATION_ERROR",
                    message="Unknown X-User-Role.",
                    reason="unknown_role",
                    details={"role": role, "valid_roles": sorted(VALID_ROLES)},
                    user_id=request.headers.get("X-User-ID"),
                )

            request.state.user_id = request.headers.get("X-User-ID")
            request.state.user_role = role
        else:
            # Reads (and service mutations) may still carry identity headers.
            role = request.headers.get("X-User-Role")
            request.state.user_id = request.headers.get("X-User-ID")
            request.state.user_role = role if role in VALID_ROLES else None

        return await self._call(request, call_next)

    async def _deny(
        self,
        request: Request,
        *,
        status_code: int,
        code: str,
        message: str,
        reason: str,
        details: dict | None = None,
        user_id: str | None = None,
    ) -> Response:
        """Return a unified error AND record the rejected attempt (§5.2)."""
        cid = request.state.correlation_id
        await record_denied_attempt(
            reason=reason,
            correlation_id=cid,
            source_ip=_source_ip(request),
            method=request.method,
            path=request.url.path,
            user_id=user_id,
        )
        return build_error_response(
            status_code=status_code,
            code=code,
            message=message,
            correlation_id=cid,
            details=details,
        )

    @staticmethod
    async def _call(request: Request, call_next) -> Response:
        response = await call_next(request)
        cid = getattr(request.state, "correlation_id", None)
        if cid:
            response.headers["X-Correlation-ID"] = cid
        return response


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, as forwarded by the Flask BFF."""

    user_id: str | None
    role: str | None
    correlation_id: str


def get_principal(request: Request) -> Principal:
    """FastAPI dependency exposing the caller identity set by the middleware."""
    return Principal(
        user_id=getattr(request.state, "user_id", None),
        role=getattr(request.state, "user_role", None),
        correlation_id=getattr(request.state, "correlation_id", ""),
    )


def require_permission(permission: str):
    """Dependency factory for the second-layer permission check.

    Raises 403 in the unified error format when the caller's role lacks the
    permission — this is what makes an operator token hitting an engineer/admin
    endpoint fail with 403 (PROMPT §8 acceptance) — and records the denied
    attempt so it lands in the audit table (design-backend §5.2).
    """

    async def _dependency(request: Request) -> Principal:
        principal = get_principal(request)
        if principal.role is None:
            await record_denied_attempt(
                reason="missing_role",
                correlation_id=principal.correlation_id,
                source_ip=_source_ip(request),
                method=request.method,
                path=request.url.path,
                user_id=principal.user_id,
            )
            raise AppError(
                code="VALIDATION_ERROR",
                message="Missing or invalid X-User-Role.",
                status_code=400,
            )
        if not has_permission(principal.role, permission):
            await record_denied_attempt(
                reason=f"missing_permission:{permission}",
                correlation_id=principal.correlation_id,
                source_ip=_source_ip(request),
                method=request.method,
                path=request.url.path,
                user_id=principal.user_id,
                role=principal.role,
            )
            raise AppError(
                code="FORBIDDEN",
                message="Role lacks required permission.",
                status_code=403,
                details={"role": principal.role, "required": permission},
            )
        return principal

    return _dependency
