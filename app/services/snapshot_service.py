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

from datetime import UTC, datetime

from app.domain import timeseries as ts
from app.domain.devices import get_device

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


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_snapshot(device_id: str) -> dict:
    device = get_device(device_id)  # raises DeviceNotFound -> 404

    dv_value = ts.current_value("dv", device.id)
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
        # Placeholder until the alarm subsystem (batch 5); DECISIONS D4.2.
        "alarms": {"active": 2, "critical": 1, "warning": 1, "oldest_pending_s": 262},
        "model": {"active_version": "v3.2.0", "scenario": device.scenario_id},
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
            "model": {"r2": 0.94, "drift_pct": 12, "status": "watch"},
            "fallback": {"failed": 0, "chain_ready": "RF→PID", "status": "ok"},
            "latency": {"inference_ms": 0.31, "p99_ms": 0.45, "status": "ok"},
        },
    }
