"""Unified error format (design-backend.md §1.2).

Every error response — from raised ``AppError``, FastAPI validation, or an
uncaught exception — is rendered as::

    {
      "error": {
        "code": "FORBIDDEN | VALIDATION_ERROR | CONFLICT | NOT_FOUND | UPSTREAM_TIMEOUT",
        "message": "human readable",
        "correlation_id": "…",
        "details": {}
      }
    }
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Canonical error codes and their default HTTP status.
ERROR_STATUS: dict[str, int] = {
    "VALIDATION_ERROR": 400,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "UPSTREAM_TIMEOUT": 504,
    "INTERNAL_ERROR": 500,
}

# Map framework HTTP status codes onto our canonical codes.
_STATUS_TO_CODE: dict[int, str] = {
    400: "VALIDATION_ERROR",
    401: "FORBIDDEN",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    504: "UPSTREAM_TIMEOUT",
}


class AppError(Exception):
    """Raise anywhere to produce a unified error response."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code or ERROR_STATUS.get(code, 400)
        self.details = details or {}
        super().__init__(message)


def correlation_id_of(request: Request) -> str | None:
    """Best-effort correlation id: request.state (set by middleware) then header."""
    cid = getattr(request.state, "correlation_id", None)
    return cid or request.headers.get("X-Correlation-ID")


def build_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    correlation_id: str | None,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    payload = {
        "error": {
            "code": code,
            "message": message,
            "correlation_id": correlation_id,
            "details": details or {},
        }
    }
    response = JSONResponse(status_code=status_code, content=payload)
    if correlation_id:
        response.headers["X-Correlation-ID"] = correlation_id
    return response


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return build_error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        correlation_id=correlation_id_of(request),
        details=exc.details,
    )


async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return build_error_response(
        status_code=422,
        code="VALIDATION_ERROR",
        message="Request validation failed.",
        correlation_id=correlation_id_of(request),
        details={"errors": exc.errors()},
    )


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _STATUS_TO_CODE.get(exc.status_code, "INTERNAL_ERROR")
    return build_error_response(
        status_code=exc.status_code,
        code=code,
        message=str(exc.detail),
        correlation_id=correlation_id_of(request),
    )


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    # Do not leak internals (design-frontend.md §7.1 error pages).
    return build_error_response(
        status_code=500,
        code="INTERNAL_ERROR",
        message="Internal server error.",
        correlation_id=correlation_id_of(request),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
