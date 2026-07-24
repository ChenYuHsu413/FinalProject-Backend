"""Dashboard snapshot aggregation (design-backend.md §2).

Builds the one-shot first-screen / reconnect payload. Dynamic numbers (dv,
residual, and their backend-computed `delta_5min` / `sigma3_margin_pct`) come from
the deterministic time-series generator so the frontend sees a *moving* signal;
`pipeline` / `health_cards` are representative mock values; the `alarms` block is
a placeholder until the alarm subsystem lands in batch 5 (DECISIONS D4.2).

Scenario id is the long form (PROMPT §3 #5), overriding the `S01` in the §2
example. The snapshot never 404s on missing engine files — it must always render
the first screen — it degrades to computed/representative values.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import timeseries as ts
from app.domain.devices import get_device
from app.domain.servo_features import synth_features
from app.repositories.http import model_service
from app.repositories.pg.alarm_repo import AlarmRepository

logger = logging.getLogger("app.snapshot")

SCHEMA_VERSION = "1.0"

_DV_THRESHOLD = 0.35
_RESIDUAL_THRESHOLD = 0.035


def _status_for(value: float, threshold: float) -> str:
    ratio = value / threshold if threshold else 0.0
    if ratio < 0.6:
        return "normal"
    if ratio < 1.0:
        return "watch"
    if ratio < 1.2:
        return "warning"
    return "critical"


def _float_or(value: object, default: float) -> float:
    """Coerce an external-service field; a junk value must not 500 the snapshot."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def build_snapshot(session: AsyncSession, device_id: str) -> dict:
    device = get_device(device_id)  # raises DeviceNotFound -> 404

    # Real alarm counts (batch 5, replacing the batch-4 placeholder — DECISIONS D4.2).
    alarms = await AlarmRepository(session).counts(device.id)

    # dv prefers the external model (batch 8, DECISIONS D8.1); the deterministic
    # generator stays the fallback. The snapshot must always render the first
    # screen (module docstring), so a model outage degrades silently, never 5xx.
    dv_value = ts.current_value("dv", device.id)
    dv_source = "generated"
    try:
        pred = await model_service.predict(device.id, synth_features(device.id))
        dv_value = pred.dv
        dv_source = "model"
    except model_service.ModelServiceError:
        pass  # keep the generated value
    logger.debug("snapshot dv for %s from %s", device.id, dv_source)

    # Model metadata comes from the same service; representative values when it
    # is disabled or unreachable.
    model_info = await model_service.info()

    residual_value = ts.current_value("residual", device.id)
    residual_margin = max(0.0, (_RESIDUAL_THRESHOLD - residual_value) / _RESIDUAL_THRESHOLD * 100)

    return {
        "ts": _now_iso(),
        "schema_version": SCHEMA_VERSION,
        "device": {"id": device.id, "cell": device.cell, "line": device.line},
        "scenario": {"id": device.scenario_id, "name": device.scenario_name},
        "control_mode": "NORMAL",
        "system_status": "RUNNING",
        "health_pct": 92,
        "cycle": {"id": "C-08429", "state": "running", "started_at": _now_iso(), "elapsed_s": 258},
        "dv": {
            "value": dv_value,
            "threshold": _DV_THRESHOLD,
            "delta_5min": ts.delta_5min("dv", device.id),
            "status": _status_for(dv_value, _DV_THRESHOLD),
        },
        "residual": {
            "value": residual_value,
            "threshold": _RESIDUAL_THRESHOLD,
            "sigma3_margin_pct": round(residual_margin, 1),
            "status": "in_threshold" if residual_value <= _RESIDUAL_THRESHOLD else "exceeded",
        },
        "alarms": alarms,
        "model": {
            "active_version": str(model_info.get("model_version") or "v3.2.0"),
            "scenario": device.scenario_id,
        },
        "pipeline": {
            "stages": [
                {"name": "EtherCAT", "metric": "50kHz", "status": "ok"},
                {"name": "Features", "metric": "48 active", "status": "ok"},
                {"name": "Inference", "metric": "0.31ms", "status": "ok"},
                {"name": "Decision", "metric": "Normal", "status": "ok"},
            ],
            "e2e_latency_ms": 0.82,
            "sla_ms": 1.0,
        },
        "health_cards": {
            "comm": {"uptime_pct": 99.98, "packets_lost": 0, "status": "ok"},
            "data_quality": {"score_pct": 99.5, "nan_pct": 0.1, "status": "ok"},
            "model": {
                "r2": _float_or(model_info.get("reg_r2"), 0.94),
                "drift_pct": 12,
                "status": "watch",
            },
            "fallback": {"failed": 0, "chain_ready": "RF→PID", "status": "ok"},
            "latency": {"inference_ms": 0.31, "p99_ms": 0.45, "status": "ok"},
        },
    }
