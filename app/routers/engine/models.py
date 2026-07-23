"""Engine response models — field-for-field with 後端資料規格書 §二/§七/§八/§九/§十.

Field names are the contract the Flask normalizer depends on (batch-3 acceptance
#1), so they mirror the spec examples exactly and case-sensitively. Deeply nested
/ variable structures (e.g. L3 pools, SHAP force plots) are typed as dict/list to
stay faithful to whatever the pipeline emits while keeping the container field
names exact.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ControlMode(BaseModel):
    code: int
    name: str
    hmi_color: str


# --- §2.1 L1 -----------------------------------------------------------------
class L1Predictions(BaseModel):
    DV_mean: float
    DV_std: float
    DV_min: float
    DV_max: float
    ylabel_mode: str
    ylabel_distribution: dict[str, float]


class L1LatencyBlock(BaseModel):
    mean_ms: float
    p99_ms: float
    max_ms: float
    within_1ms_ratio: float


class L1Realtime(BaseModel):
    level: str
    type: str
    timestamp: str
    scenario_id: str
    samples_in_second: int
    predictions: L1Predictions
    real_time_RMSE: float
    latency: L1LatencyBlock
    fallback_count: int
    control_mode: ControlMode


class L1LatencyStats(BaseModel):
    scenario_id: str
    window_seconds: int
    mean_ms: float
    p99_ms: float
    max_ms: float
    within_1ms_ratio: float
    total_inferences: int


class L1ModelInfo(BaseModel):
    version: str
    algorithm: str
    parameters: dict[str, Any]
    input_features: list[str]
    output_targets: list[str]
    file_hash_sha256: str
    model_size_mb: float
    trained_at: str
    shadow_pass_at: str
    status: str


class L1Model(BaseModel):
    scenario_id: str
    model: L1ModelInfo


# --- §2.2 L2 -----------------------------------------------------------------
class L2Latest(BaseModel):
    level: str
    type: str
    timestamp: str
    scenario_id: str
    buffer_info: dict[str, Any]
    finetune: dict[str, Any]
    rollback: dict[str, Any]
    new_model_hash: str


class L2Trend(BaseModel):
    scenario_id: str
    period_hours: int
    finetune_history: list[dict[str, Any]]
    summary: dict[str, Any]


# --- §2.3 L3 -----------------------------------------------------------------
class L3Latest(BaseModel):
    scenario: str
    trained_at: str
    n_features: int
    n_train: int
    n_test: int
    champion: dict[str, Any]
    selected_model: dict[str, Any]
    ml_pool: dict[str, Any]
    dl_pool: dict[str, Any]
    feature_importance_global: list[dict[str, Any]]


class L3Shadow(BaseModel):
    scenario: str
    test_windows: int
    tested_at: str
    new_model: dict[str, Any]
    old_model: dict[str, Any]
    naive_mean_rmse: float
    absolute_gates: dict[str, Any]
    comparison: dict[str, Any]
    decision: str


class L3Models(BaseModel):
    scenario_id: str
    models: list[dict[str, Any]]


# --- §2.4 SHAP ---------------------------------------------------------------
class ShapDiagnosis(BaseModel):
    scenario: str
    timestamp: str
    trigger: dict[str, Any]
    shap_values: dict[str, Any]
    global_feature_importance: list[dict[str, Any]]
    lightgbm_importance: list[dict[str, Any]]
    root_cause_rank: list[dict[str, Any]]
    device_suggestions: list[dict[str, Any]]
    control_mode: dict[str, Any]
    recommended_mode: str
    worst_windows: list[dict[str, Any]]


class ShapSummary(BaseModel):
    level: str
    type: str
    scenario_id: str
    beeswarm: dict[str, Any]
    feature_importance_mean_abs: list[dict[str, Any]]


# --- §2.5 Fallback -----------------------------------------------------------
class FallbackEventsPage(BaseModel):
    events: list[dict[str, Any]]
    total: int
    page: int
    limit: int


class FallbackStats(BaseModel):
    scenario_id: str
    period_hours: int
    total_events: int
    by_reason: dict[str, int]
    by_level: dict[str, int]
    current_status: str
    last_event_at: str | None
    consecutive_normal_hours: float


# --- §2.6 Scenarios ----------------------------------------------------------
class ScenariosSummary(BaseModel):
    scenarios: dict[str, Any]


# --- §七 Residual / scenario library ----------------------------------------
class ResidualStatus(BaseModel):
    scenario_id: str
    residual: dict[str, Any]
    scheduler: dict[str, Any]


class ScenarioLibrary(BaseModel):
    total_scenarios: int
    active_scenarios: int
    scenarios: list[dict[str, Any]]
    new_scenario_pending: bool
    new_scenario_note: str | None


# --- §八 Ensemble ------------------------------------------------------------
class EnsembleStatus(BaseModel):
    scenario_id: str
    ensemble_mode: str
    single_model: dict[str, Any]
    ensemble_candidates: list[dict[str, Any]]
    last_evaluation_at: str
    evaluation_cycle_hours: int
    rule: str


# --- §九 Control mode --------------------------------------------------------
class ControlModeStatus(BaseModel):
    scenario_id: str
    current_mode: ControlMode
    mode_history: list[dict[str, Any]]
    state_machine: dict[str, Any]


# --- §十 Data lifecycle ------------------------------------------------------
class DataLifecycle(BaseModel):
    retention_policy: dict[str, Any]
    current_usage: dict[str, Any]
    next_cleanup_at: str
    sampling_config: dict[str, Any]
