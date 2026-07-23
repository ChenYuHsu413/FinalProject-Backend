"""Trend aggregation (design-backend.md §10).

Returns **backend-downsampled** series (≤500 points/series — the browser must not
accumulate; §10.3). Windows: 1h / 8h / 24h. Requires `trend.read`. Unknown device
→ 404; unknown metric → 400; bad window → 422 (pattern).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.errors import AppError
from app.core.permissions import TREND_READ
from app.core.security import require_permission
from app.domain import timeseries as ts
from app.domain.devices import DEFAULT_DEVICE, get_device

router = APIRouter(
    tags=["trends"],
    responses={
        400: {"description": "unknown metric"},
        404: {"description": "unknown device"},
    },
)


class SeriesBlock(BaseModel):
    points: list[dict]
    threshold: float


class TrendsResponse(BaseModel):
    device: str
    window: str
    generated_at: str
    series: dict[str, SeriesBlock]


@router.get("/trends", response_model=TrendsResponse)
def trends(
    metrics: str = Query(default="dv,residual"),
    window: str = Query(default="1h", pattern="^(1h|8h|24h)$"),
    device: str = Query(default=DEFAULT_DEVICE),
    _=Depends(require_permission(TREND_READ)),
) -> dict:
    get_device(device)  # raises DeviceNotFound -> 404

    requested = [m.strip() for m in metrics.split(",") if m.strip()]
    unknown = [m for m in requested if not ts.is_known_metric(m)]
    if unknown:
        raise AppError(
            code="VALIDATION_ERROR",
            message="Unknown metric(s).",
            status_code=400,
            details={"unknown": unknown, "known": sorted(ts.METRIC_PROFILES)},
        )

    now = datetime.now(UTC)
    series = {
        m: {
            "points": ts.series(metric=m, device=device, window=window, end=now),
            "threshold": ts.METRIC_PROFILES[m].threshold,
        }
        for m in requested
    }
    return {
        "device": device,
        "window": window,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "series": series,
    }
