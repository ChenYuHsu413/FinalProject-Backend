"""Mock simulator: writes engine files + builds Redis event payloads.

File shapes mirror 後端資料規格書 §二/§七/§八/§九/§十 field-for-field so the engine
endpoints (which validate against the response models) return spec-faithful data.
Event payloads follow §3.2 and are wrapped in the §11 envelope by the publisher.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.domain import timeseries as ts
from app.domain.scenarios import ACTIVE_SCENARIOS, SCENARIO_LIBRARY_SIZE
from app.events import channels
from app.events.publisher import EventPublisher


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- per-scenario file builders ---------------------------------------------
def _l1_realtime(s: str) -> dict[str, Any]:
    dv = ts.current_value("dv", s)  # moving value (batch-4 observation #1)
    return {
        "level": "L1",
        "type": "summary_1s",
        "timestamp": _now_iso(),
        "scenario_id": s,
        "samples_in_second": 50000,
        "predictions": {
            "DV_mean": dv,
            "DV_std": 0.02,
            "DV_min": 0.08,
            "DV_max": 0.21,
            "ylabel_mode": "LN",
            "ylabel_distribution": {"LN": 0.92, "LO": 0.06, "MED": 0.02, "HI": 0.0},
        },
        "real_time_RMSE": 0.015,
        "latency": {"mean_ms": 0.20, "p99_ms": 0.35, "max_ms": 0.55, "within_1ms_ratio": 1.0},
        "fallback_count": 0,
        "control_mode": {"code": 0, "name": "Normal", "hmi_color": "green"},
    }


def _l1_latency(s: str) -> dict[str, Any]:
    return {
        "scenario_id": s,
        "window_seconds": 60,
        "mean_ms": 0.21,
        "p99_ms": 0.38,
        "max_ms": 0.62,
        "within_1ms_ratio": 1.0,
        "total_inferences": 3000000,
    }


def _l1_model(s: str) -> dict[str, Any]:
    return {
        "scenario_id": s,
        "model": {
            "version": "v1.0.3",
            "algorithm": "LightGBM",
            "parameters": {
                "num_leaves": 31,
                "max_depth": 8,
                "min_data_in_leaf": 50,
                "subsample": 0.8,
                "feature_fraction": 0.8,
                "n_estimators": 100,
            },
            "input_features": ["FE", "Vel", "Acc", "torque", "Id", "Iq"],
            "output_targets": ["DV", "ylabel"],
            "file_hash_sha256": "a1b2c3d4e5f6",
            "model_size_mb": 3.8,
            "trained_at": "2026-07-22T08:00:00Z",
            "shadow_pass_at": "2026-07-22T08:30:00Z",
            "status": "active",
        },
    }


def _l2_latest(s: str) -> dict[str, Any]:
    return {
        "level": "L2",
        "type": "finetune_result",
        "timestamp": _now_iso(),
        "scenario_id": s,
        "buffer_info": {
            "buffer_minutes": 5,
            "buffer_samples": 15000000,
            "windows_extracted": 5700,
            "window_features_count": 17,
        },
        "finetune": {
            "epochs": 5,
            "learning_rate": 0.001,
            "rmse_before": 0.042,
            "rmse_after": 0.038,
            "improvement_pct": 9.5,
        },
        "rollback": {"triggered": False, "reason": None},
        "new_model_hash": "e5f6g7h8",
    }


def _l2_trend(s: str) -> dict[str, Any]:
    return {
        "scenario_id": s,
        "period_hours": 1,
        "finetune_history": [
            {
                "time": "01:46",
                "rmse_before": 0.042,
                "rmse_after": 0.038,
                "improvement": 9.5,
                "rollback": False,
            },
            {
                "time": "01:47",
                "rmse_before": 0.038,
                "rmse_after": 0.036,
                "improvement": 5.3,
                "rollback": False,
            },
        ],
        "summary": {
            "total_finetunes": 60,
            "avg_improvement_pct": 4.2,
            "rollback_count": 2,
            "current_rmse": 0.033,
            "rmse_trend": "improving",
        },
    }


def _l3_latest(s: str) -> dict[str, Any]:
    return {
        "scenario": s,
        "trained_at": "2026-07-23T05:00:00Z",
        "n_features": 17,
        "n_train": 760,
        "n_test": 3800,
        "champion": {
            "name": "LightGBM_HighAcc",
            "CV_RMSE": 0.0185,
            "TEST_RMSE": 0.0172,
            "TEST_R2": 0.94,
        },
        "selected_model": {
            "algorithm": "LightGBM_HighAcc",
            "version": "v1.0.3",
            "reason": "Best CV RMSE + LightGBM priority (2% tie threshold)",
            "params": {
                "num_leaves": 127,
                "max_depth": 15,
                "learning_rate": 0.1,
                "subsample": 0.8,
                "feature_fraction": 0.8,
            },
        },
        "ml_pool": {
            "regression": [
                {
                    "rank": 1,
                    "name": "LightGBM_HighAcc",
                    "CV_RMSE": 0.0185,
                    "TEST_RMSE": 0.0172,
                    "TEST_R2": 0.94,
                    "train_time_s": 45.2,
                },
                {
                    "rank": 2,
                    "name": "XGBoost",
                    "CV_RMSE": 0.0195,
                    "TEST_RMSE": 0.0188,
                    "TEST_R2": 0.92,
                    "train_time_s": 120.5,
                },
            ],
            "classification": [
                {
                    "rank": 1,
                    "name": "LightGBM_HighAcc",
                    "CV_F1": 0.96,
                    "Accuracy": 0.97,
                    "F1": 0.96,
                    "AUC": 0.99,
                }
            ],
            "clustering": [
                {"rank": 1, "name": "KMeans", "Silhouette": 0.45, "Davies_Bouldin": 1.2}
            ],
        },
        "dl_pool": {
            "models": [
                {
                    "rank": 1,
                    "name": "MLP_64_32_16",
                    "Loss": 0.006,
                    "Val_Loss": 0.008,
                    "RMSE": 0.020,
                    "Latency_ms": 0.15,
                }
            ]
        },
        "feature_importance_global": [
            {
                "feature": "FE_RMS",
                "importance": 0.31,
                "split_importance": 42,
                "gain_importance": 0.31,
            },
            {
                "feature": "Vel_RMS",
                "importance": 0.15,
                "split_importance": 25,
                "gain_importance": 0.15,
            },
        ],
    }


def _l3_shadow(s: str) -> dict[str, Any]:
    return {
        "scenario": s,
        "test_windows": 3800,
        "tested_at": "2026-07-23T05:30:00Z",
        "new_model": {
            "RMSE": 0.0172,
            "MAE": 0.011,
            "R2": 0.94,
            "latency_ms": 0.20,
            "model_hash": "a1b2",
        },
        "old_model": {"RMSE": 0.0220, "MAE": 0.014, "R2": 0.91, "model_hash": "c3d4"},
        "naive_mean_rmse": 0.15,
        "absolute_gates": {"r2_positive": True, "beats_naive_mean": True, "latency_ok": True},
        "comparison": {"rmse_improvement_pct": 21.8, "threshold_met": True, "abs_gate_ok": True},
        "decision": "DEPLOY",
    }


def _shap_diagnosis(s: str) -> dict[str, Any]:
    return {
        "scenario": s,
        "timestamp": _now_iso(),
        "trigger": {
            "reason": "max_prediction_error",
            "window_index": 423,
            "DV_actual": 0.72,
            "DV_predicted": 0.45,
            "error": 0.27,
            "severity": "high",
            "severity_thresholds": {
                "medium": "error > TEST_RMSE (0.017)",
                "high": "error > 2x TEST_RMSE (0.034)",
            },
        },
        "shap_values": {
            "expected_value": 0.12,
            "current_prediction": 0.45,
            "force_plot_data": [
                {"feature": "FE_RMS", "value": 0.032, "shap": 0.18, "contribution_pct": 50.0},
                {"feature": "FE_Peak", "value": 0.085, "shap": 0.11, "contribution_pct": 30.6},
            ],
            "waterfall": {
                "base": 0.12,
                "steps": [
                    {"feature": "FE_RMS", "effect": 0.18, "cumulative": 0.30},
                    {"feature": "FE_Peak", "effect": 0.11, "cumulative": 0.41},
                ],
            },
        },
        "global_feature_importance": [
            {"feature": "FE_RMS", "mean_abs_shap": 0.042},
            {"feature": "FE_Peak", "mean_abs_shap": 0.035},
        ],
        "lightgbm_importance": [
            {"feature": "FE_RMS", "gain_importance": 0.31, "split_importance": 42}
        ],
        "root_cause_rank": [{"feature": "FE_RMS", "mean_contribution_pct": 50.0, "value": 0.032}],
        "device_suggestions": [
            {
                "feature": "FE_RMS",
                "shap_contribution_pct": 50.0,
                "parameter": "P1-40 Position Loop Gain",
                "suggested_action": "increase 10%",
                "current_value": 0.032,
            }
        ],
        "control_mode": {
            "code": 2,
            "name": "FineTune",
            "hmi_color": "red",
            "dv_norm_predicted": 0.72,
        },
        "recommended_mode": "FineTune",
        "worst_windows": [
            {"window_index": 423, "DV_actual": 0.72, "DV_predicted": 0.45, "error": 0.27}
        ],
    }


def _shap_summary(s: str) -> dict[str, Any]:
    return {
        "level": "SHAP",
        "type": "summary_plot",
        "scenario_id": s,
        "beeswarm": {
            "features": ["FE_RMS", "FE_Peak", "SettlingTime"],
            "data": [
                {
                    "feature": "FE_RMS",
                    "shap_values": [0.18, -0.05, 0.12],
                    "feature_values": [0.032, 0.008, 0.025],
                },
                {
                    "feature": "FE_Peak",
                    "shap_values": [0.11, -0.03, 0.08],
                    "feature_values": [0.085, 0.020, 0.060],
                },
            ],
        },
        "feature_importance_mean_abs": [
            {"feature": "FE_RMS", "mean_abs_shap": 0.042},
            {"feature": "FE_Peak", "mean_abs_shap": 0.035},
        ],
    }


def _fallback_stats(s: str) -> dict[str, Any]:
    return {
        "scenario_id": s,
        "period_hours": 24,
        "total_events": 15,
        "by_reason": {
            "NaN_output": 8,
            "Inf_output": 3,
            "latency_exceeded": 2,
            "model_confidence_low": 2,
        },
        "by_level": {
            "level_1_use_previous": 12,
            "level_2_switch_PID": 2,
            "level_3_notify_expert": 1,
        },
        "current_status": "normal",
        "last_event_at": "2026-07-23T01:45:05Z",
        "consecutive_normal_hours": 2.5,
    }


def _residual(s: str) -> dict[str, Any]:
    return {
        "scenario_id": s,
        "residual": {
            "current": 0.032,
            "baseline_mean": 0.018,
            "baseline_std": 0.005,
            "threshold_3sigma": 0.033,
            "exceed_count": 0,
        },
        "scheduler": {
            "current_mode": "inference_only",
            "last_retrain_at": "2026-07-23T05:00:00Z",
            "next_retrain_at": "2026-07-23T09:00:00Z",
            "cycle_count": 42,
        },
    }


def _ensemble(s: str) -> dict[str, Any]:
    return {
        "scenario_id": s,
        "ensemble_mode": "single",
        "single_model": {
            "algorithm": "LightGBM_HighAcc",
            "CV_RMSE": 0.0185,
            "reason": "LightGBM 優先策略 (2% tie threshold)",
        },
        "ensemble_candidates": [],
        "last_evaluation_at": "2026-07-23T05:00:00Z",
        "evaluation_cycle_hours": 4,
        "rule": "優先採用內建模型 (不做 Ensemble)，誤差持續增大時啟動 Ensemble Learning",
    }


def _control_mode(s: str) -> dict[str, Any]:
    return {
        "scenario_id": s,
        "current_mode": {"code": 0, "name": "Normal", "hmi_color": "green"},
        "mode_history": [
            {"from": None, "to": "Normal", "at": "2026-07-23T00:00:00Z", "trigger": "system_start"},
        ],
        "state_machine": {
            "Normal → Diagnosis": "殘差連續 > 閾值 × 3 個週期 (自動觸發)",
            "Diagnosis → Normal": "操作員確認維修或忽略後殘差恢復正常",
            "Normal → FineTune": "操作員手動切換 (性能優化需求)",
            "FineTune → Normal": "優化完成或超時",
            "any → Safe": "Fallback 連續 3 次或 DV > 0.8",
        },
    }


# --- global file builders ----------------------------------------------------
def _models_jsonl() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for s in ACTIVE_SCENARIOS:
        rows.append(
            {
                "scenario_id": s,
                "version": "v1.0.3",
                "pool": "ml_reg",
                "status": "active",
                "file_hash_sha256": "a1b2",
                "metrics": {"RMSE": 0.0172},
                "trained_at": "2026-07-23T05:00:00Z",
            }
        )
        rows.append(
            {
                "scenario_id": s,
                "version": "v1.0.2",
                "pool": "ml_reg",
                "status": "rolled_back",
                "file_hash_sha256": "c3d4",
                "metrics": {"RMSE": 0.0220},
                "trained_at": "2026-07-22T05:00:00Z",
            }
        )
    return rows


def _fallback_events() -> list[dict[str, Any]]:
    return [
        {
            "event_id": f"fb-{s[:2]}-001",
            "timestamp": "2026-07-23T01:45:05.000Z",
            "scenario_id": s,
            "fallback_level": 1,
            "reason": "NaN_output_from_model",
            "model_output_before": {"DV": None, "ylabel": None, "confidence": None},
            "action_taken": {"use_previous_value": True, "previous_DV": 0.13},
            "consecutive_falls": 1,
        }
        for s in ACTIVE_SCENARIOS
    ]


def _scenarios_status() -> dict[str, Any]:
    base = {
        "severity": "low",
        "control_mode": {"code": 0, "name": "Normal", "hmi_color": "green"},
        "top_cause": None,
        "DV_predicted": 0.13,
        "device_suggestions_count": 0,
        "n_features": 9,
        "n_train_rows": 19309275,
        "n_test_rows": 59990200,
    }
    return {"scenarios": {s: dict(base) for s in ACTIVE_SCENARIOS}}


def _scenario_library() -> dict[str, Any]:
    scenarios = []
    for i in range(1, SCENARIO_LIBRARY_SIZE + 1):
        name = next((a for a in ACTIVE_SCENARIOS if a.startswith(f"{i:02d}_")), None)
        active = name is not None
        scenarios.append(
            {
                "id": i,
                "name": name or f"{i:02d}_Reserved",
                "status": "active" if active else "inactive",
                "model_version": "v1.0.3" if active else None,
                "similarity_score": 0.95 if active else None,
            }
        )
    return {
        "total_scenarios": SCENARIO_LIBRARY_SIZE,
        "active_scenarios": len(ACTIVE_SCENARIOS),
        "scenarios": scenarios,
        "new_scenario_pending": False,
        "new_scenario_note": None,
    }


def _data_retention() -> dict[str, Any]:
    return {
        "retention_policy": {
            "full_resolution_days": 7,
            "sampled_days": 30,
            "stats_only_after_days": 30,
        },
        "current_usage": {
            "total_data_gb": 45.2,
            "full_resolution_gb": 42.0,
            "sampled_gb": 3.2,
            "stats_only_gb": 0.5,
        },
        "next_cleanup_at": "2026-07-30T00:00:00Z",
        "sampling_config": {"interval_hours": 1, "samples_per_hour": 1},
    }


class MockSimulator:
    """Generates the engine file tree under ENGINE_DATA_DIR."""

    def __init__(self, base_dir: str) -> None:
        self.base = Path(base_dir)

    def _write(self, obj: Any, *parts: str) -> None:
        path = self.base.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)

    def _write_jsonl(self, rows: list[dict[str, Any]], *parts: str) -> None:
        path = self.base.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def write_l1_realtime(self, scenario_id: str) -> None:
        """Refresh just the L1 realtime file (called each tick so /l1/realtime moves)."""
        self._write(_l1_realtime(scenario_id), "L1", f"L1_{scenario_id}_realtime.json")

    def generate_all(self) -> None:
        for s in ACTIVE_SCENARIOS:
            self._write(_l1_realtime(s), "L1", f"L1_{s}_realtime.json")
            self._write(_l1_latency(s), "L1", f"L1_{s}_latency.json")
            self._write(_l1_model(s), "L1", f"L1_{s}_model.json")
            self._write(_l2_latest(s), "L2", f"L2_{s}.json")
            self._write(_l2_trend(s), "L2", f"L2_{s}_trend.json")
            self._write(_l3_latest(s), "Stage5_Output", "L3", f"L3_{s}_ranking.json")
            self._write(_l3_shadow(s), "Stage6_Output", f"{s}_shadow_result.json")
            self._write(_shap_diagnosis(s), "Stage7_Output", f"{s}_diagnosis.json")
            self._write(_shap_summary(s), "Stage7_Output", f"{s}_shap_summary.json")
            self._write(_fallback_stats(s), "fallback", f"fallback_stats_{s}.json")
            self._write(_residual(s), "residual", f"residual_{s}.json")
            self._write(_ensemble(s), "ensemble", f"ensemble_{s}.json")
            self._write(_control_mode(s), "control_mode", f"control_mode_{s}.json")
        self._write_jsonl(_models_jsonl(), "Stage5_Output", "models.jsonl")
        self._write_jsonl(_fallback_events(), "fallback", "fallback_events.jsonl")
        self._write(_scenarios_status(), "Stage7_Output", "scenarios_status.json")
        self._write(_scenario_library(), "scenario_library", "scenario_library.json")
        self._write(_data_retention(), "retention", "data_retention.json")


# --- event payload builders + publish helpers (§3.2 / §十三) -----------------
def l1_summary_payload(s: str) -> dict[str, Any]:
    return {
        "DV_mean": ts.current_value("dv", s),  # moving value each tick
        "ylabel_mode": "LN",
        "latency": {"mean_ms": 0.20, "p99_ms": 0.35},
        "fallback_count": 0,
    }


def l2_finetune_payload(s: str) -> dict[str, Any]:
    return {"rmse_before": 0.042, "rmse_after": 0.038, "improvement_pct": 9.5, "rollback": False}


def fallback_event_payload(s: str) -> dict[str, Any]:
    return {"reason": "NaN_output", "level": 1, "action": "use_previous"}


def shap_diagnosis_payload(s: str) -> dict[str, Any]:
    return {"severity": "high", "top_cause": "FE_RMS", "recommended_mode": "FineTune"}


async def publish_l1_summary(pub: EventPublisher, scenario_id: str) -> None:
    await pub.publish(
        channel=channels.L1_SUMMARY,
        event_type="l1:summary",
        payload=l1_summary_payload(scenario_id),
        scenario_id=scenario_id,
    )


async def publish_l2_finetune(pub: EventPublisher, scenario_id: str) -> None:
    await pub.publish(
        channel=channels.L2_FINETUNE,
        event_type="l2:finetune",
        payload=l2_finetune_payload(scenario_id),
        scenario_id=scenario_id,
    )


async def publish_fallback_event(pub: EventPublisher, scenario_id: str) -> None:
    await pub.publish(
        channel=channels.FALLBACK_EVENT,
        event_type="fallback:event",
        payload=fallback_event_payload(scenario_id),
        scenario_id=scenario_id,
    )


async def publish_shap_diagnosis(pub: EventPublisher, scenario_id: str) -> None:
    await pub.publish(
        channel=channels.SHAP_DIAGNOSIS,
        event_type="shap:diagnosis",
        payload=shap_diagnosis_payload(scenario_id),
        scenario_id=scenario_id,
    )
