"""Engine file repository — reads ML-pipeline output files under ENGINE_DATA_DIR.

Interface-first so "swap in the real pipeline output" later means changing only
this class, not the API layer (PROMPT §1). Key rules for batch 3:

* A **missing file is normal** (simulator hasn't produced it / scenario untrained)
  → raise ``EngineDataNotFound`` which routers map to a documented 404, never 500.
* ``scenario_id`` is validated for well-formedness **before** any path assembly,
  so an arbitrary string can never traverse the filesystem (acceptance #3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domain.scenarios import is_wellformed_scenario


class EngineDataNotFound(Exception):
    """Requested engine data file does not exist (→ HTTP 404)."""


class EngineFileRepository:
    def __init__(self, base_dir: str) -> None:
        self.base = Path(base_dir)

    # --- internals ----------------------------------------------------------
    def _require_scenario(self, scenario_id: str) -> str:
        # Path-traversal guard: reject anything not matching the strict pattern
        # before it is used to build a path.
        if not is_wellformed_scenario(scenario_id):
            raise EngineDataNotFound(f"unknown scenario: {scenario_id!r}")
        return scenario_id

    def _read_json(self, *parts: str) -> Any:
        path = self.base.joinpath(*parts)
        if not path.is_file():
            raise EngineDataNotFound(f"missing engine data file: {path.name}")
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def _read_jsonl(self, *parts: str) -> list[dict[str, Any]]:
        path = self.base.joinpath(*parts)
        if not path.is_file():
            raise EngineDataNotFound(f"missing engine data file: {path.name}")
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    # --- L1 -----------------------------------------------------------------
    def l1_realtime(self, scenario_id: str) -> dict[str, Any]:
        s = self._require_scenario(scenario_id)
        return self._read_json("L1", f"L1_{s}_realtime.json")

    def l1_latency(self, scenario_id: str) -> dict[str, Any]:
        s = self._require_scenario(scenario_id)
        return self._read_json("L1", f"L1_{s}_latency.json")

    def l1_model(self, scenario_id: str) -> dict[str, Any]:
        s = self._require_scenario(scenario_id)
        return self._read_json("L1", f"L1_{s}_model.json")

    # --- L2 -----------------------------------------------------------------
    def l2_latest(self, scenario_id: str) -> dict[str, Any]:
        s = self._require_scenario(scenario_id)
        return self._read_json("L2", f"L2_{s}.json")

    def l2_trend(self, scenario_id: str) -> dict[str, Any]:
        s = self._require_scenario(scenario_id)
        return self._read_json("L2", f"L2_{s}_trend.json")

    # --- L3 -----------------------------------------------------------------
    def l3_latest(self, scenario_id: str) -> dict[str, Any]:
        s = self._require_scenario(scenario_id)
        return self._read_json("Stage5_Output", "L3", f"L3_{s}_ranking.json")

    def l3_shadow(self, scenario_id: str) -> dict[str, Any]:
        s = self._require_scenario(scenario_id)
        return self._read_json("Stage6_Output", f"{s}_shadow_result.json")

    def l3_models(self, scenario_id: str, status: str | None = None) -> list[dict[str, Any]]:
        s = self._require_scenario(scenario_id)
        rows = self._read_jsonl("Stage5_Output", "models.jsonl")
        models = [r for r in rows if r.get("scenario_id") == s]
        if status is not None:
            models = [r for r in models if r.get("status") == status]
        return models

    # --- SHAP ---------------------------------------------------------------
    def shap_diagnosis(self, scenario_id: str) -> dict[str, Any]:
        s = self._require_scenario(scenario_id)
        return self._read_json("Stage7_Output", f"{s}_diagnosis.json")

    def shap_summary(self, scenario_id: str) -> dict[str, Any]:
        s = self._require_scenario(scenario_id)
        return self._read_json("Stage7_Output", f"{s}_shap_summary.json")

    # --- Fallback -----------------------------------------------------------
    def fallback_events(self, scenario_id: str | None = None) -> list[dict[str, Any]]:
        # Mock stores fallback events as JSONL (the SQLite hash-chain of
        # 資料規格書 §五 is deferred — see DECISIONS D3.3).
        rows = self._read_jsonl("fallback", "fallback_events.jsonl")
        if scenario_id is not None:
            s = self._require_scenario(scenario_id)
            rows = [r for r in rows if r.get("scenario_id") == s]
        return rows

    def fallback_stats(self, scenario_id: str) -> dict[str, Any]:
        s = self._require_scenario(scenario_id)
        return self._read_json("fallback", f"fallback_stats_{s}.json")

    # --- Scenario summaries / library / residual / ensemble / mode ----------
    def scenarios_status(self) -> dict[str, Any]:
        return self._read_json("Stage7_Output", "scenarios_status.json")

    def residual_status(self, scenario_id: str) -> dict[str, Any]:
        s = self._require_scenario(scenario_id)
        return self._read_json("residual", f"residual_{s}.json")

    def scenario_library(self) -> dict[str, Any]:
        return self._read_json("scenario_library", "scenario_library.json")

    def ensemble_status(self, scenario_id: str) -> dict[str, Any]:
        s = self._require_scenario(scenario_id)
        return self._read_json("ensemble", f"ensemble_{s}.json")

    def control_mode(self, scenario_id: str) -> dict[str, Any]:
        s = self._require_scenario(scenario_id)
        return self._read_json("control_mode", f"control_mode_{s}.json")

    def data_lifecycle(self) -> dict[str, Any]:
        return self._read_json("retention", "data_retention.json")
