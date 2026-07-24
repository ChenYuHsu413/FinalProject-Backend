"""Response classes.

Starlette's ``JSONResponse`` sends ``Content-Type: application/json`` with no
``charset``. The body is always UTF-8, but clients that fall back to the host's
ANSI code page when the charset is absent (.NET/PowerShell, and anything using
``locale.getpreferredencoding()``) decode the CJK strings in the engine mock
data as cp950 — which is how ``"Normal → Diagnosis"`` reaches a caller as
``"Normal \\udce2\\udc86\\udc92 Diagnosis"``. Stating the charset explicitly
removes the guess.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"
