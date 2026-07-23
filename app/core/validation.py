"""Shared request-body validation helpers (batch-2 input-defense lesson)."""

from __future__ import annotations


def reject_nul(value: object) -> None:
    """Recursively reject NUL bytes — PostgreSQL text/JSONB cannot store \\u0000.

    Without this, a string with NUL passes Pydantic but 500s at INSERT. Checks
    dict keys as well as values, and list items.
    """
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError("NUL bytes are not allowed")
    elif isinstance(value, dict):
        for k, v in value.items():
            reject_nul(k)
            reject_nul(v)
    elif isinstance(value, (list, tuple)):
        for item in value:
            reject_nul(item)
