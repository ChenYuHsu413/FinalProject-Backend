"""Aggregated servo feature row for the model service (batch 8).

SEAM A — replaced when the data pipeline delivers real aggregated features.
Until then this synthesises a deterministic row per (device, 10-second bucket)
using the same technique as ``domain/timeseries`` (hashlib seed → random.Random),
so the value moves over time but is reproducible for a given input.

The column names are the model's contract, NOT ours: they come from the model
service's ``GET /servo/model_info`` → ``feature_columns``. If the model team
changes them, this file and the adapter change together.

Pure logic, no IO.
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import UTC, datetime

FEATURE_COLUMNS: tuple[str, ...] = (
    "rotor_speed_mean", "rotor_speed_std", "rotor_speed_rms",
    "torque_mean", "torque_std", "torque_rms",
    "del_pos_mean", "del_pos_std", "del_pos_rms",
    "i_3p_a_rms", "i_3p_b_rms", "i_3p_c_rms",
    "direct_rms", "direct_std",
    "quadrature_rms", "quadrature_std",
    "rod_demand_pos_mean", "rod_actual_pos_mean",
    "position_error_mean", "position_error_max", "position_error_std",
)

# Representative centres (base, spread). Values are in the same ballpark as the
# model's training distribution — taken from the service's GET /servo/samples —
# so the returned DV stays in a plausible range rather than pinning to an
# extreme. If the model changes hands, this table changes with FEATURE_COLUMNS.
_CENTRES: dict[str, tuple[float, float]] = {
    "rotor_speed_mean": (8.9, 2.0),      "rotor_speed_std": (56.5, 4.0),
    "rotor_speed_rms": (57.2, 4.0),      "torque_mean": (-0.02, 0.3),
    "torque_std": (6.69, 0.4),           "torque_rms": (6.69, 0.4),
    "del_pos_mean": (19.4, 1.5),         "del_pos_std": (9.30, 0.8),
    "del_pos_rms": (21.5, 1.5),          "i_3p_a_rms": (4.69, 0.3),
    "i_3p_b_rms": (4.69, 0.3),           "i_3p_c_rms": (4.69, 0.3),
    "direct_rms": (0.034, 0.004),        "direct_std": (0.002, 0.0005),
    "quadrature_rms": (2.70, 0.2),       "quadrature_std": (0.08, 0.01),
    "rod_demand_pos_mean": (120.0, 3.0), "rod_actual_pos_mean": (119.9, 3.0),
    "position_error_mean": (0.06, 0.01), "position_error_max": (0.31, 0.03),
    "position_error_std": (0.02, 0.004),
}


def _seed(device: str, bucket: int) -> int:
    # Stable across processes — builtin hash() is PYTHONHASHSEED-salted.
    digest = hashlib.sha256(f"{device}|{bucket}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def synth_features(device: str, *, now: datetime | None = None) -> dict[str, float]:
    """One aggregated feature row. Deterministic per (device, 10-second bucket)."""
    now = now or datetime.now(UTC)
    bucket = int(now.timestamp()) // 10
    rng = random.Random(_seed(device, bucket))
    phase = now.timestamp() / 600.0
    row: dict[str, float] = {}
    for i, col in enumerate(FEATURE_COLUMNS):
        base, spread = _CENTRES[col]
        wave = math.sin(phase + i * 0.7)
        row[col] = round(base + spread * (0.6 * wave + 0.4 * rng.uniform(-1, 1)), 6)
    return row
