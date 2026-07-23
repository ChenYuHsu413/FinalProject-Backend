"""Unified event envelope (design-backend.md §11).

Every Redis event — engine or governance — is wrapped in this envelope; the
source-specific payload (e.g. 後端資料規格書 §3.2 shapes) goes in ``payload``.
``schema_version`` only ever gains fields, never loses them (PROMPT §7).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"


class EventEnvelope(BaseModel):
    event_id: str
    event_type: str
    timestamp: str  # UTC ISO8601
    scenario_id: str | None = None
    schema_version: str = SCHEMA_VERSION
    correlation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


def make_envelope(
    *,
    event_type: str,
    payload: dict[str, Any],
    scenario_id: str | None = None,
    correlation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_id=str(uuid4()),
        event_type=event_type,
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        scenario_id=scenario_id,
        schema_version=SCHEMA_VERSION,
        correlation_id=correlation_id,
        payload=payload,
    )
