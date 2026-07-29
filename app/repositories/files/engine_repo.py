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
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.domain.scenarios import is_wellformed_scenario

# param_verify report ids are the report filename stem ({device}_{approval_id}).
# Strict allow-list so an id can never traverse the filesystem (B3 path guard).
_REPORT_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


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

    # --- param_verify (executor digital-twin reports, B3) -------------------
    # Read-only over the JSON reports the bypass executor writes to
    # ENGINE_DATA_DIR/param_verify/ (one file per applied param_tuning approval).
    # A missing directory is honest emptiness (no verification has happened yet),
    # NOT a 404 — the list is a legitimate zero-length result.
    @staticmethod
    def _mtime_iso(path: Path) -> str:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()

    @staticmethod
    def _param_verify_summary(
        report_id: str, created_at: str, report: dict[str, Any]
    ) -> dict[str, Any]:
        # Curated summary for the list; the full report is served by the detail
        # endpoint. Outcome-specific fields (before/after/peak…) are absent on
        # skipped_stale reports, so every report-derived field is optional.
        return {
            "report_id": report_id,
            "device": report.get("device"),
            "approval_id": report.get("approval_id"),
            "created_at": created_at,
            "param": report.get("param"),
            "outcome": report.get("outcome"),
            "old": report.get("old"),
            "new": report.get("new"),
            "before": report.get("before"),
            "after": report.get("after"),
            "peak_current_delta_pct": report.get("peak_current_delta_pct"),
        }

    def param_verify_list(
        self, device: str | None = None, limit: int = 20
    ) -> tuple[list[dict[str, Any]], int]:
        """List verification report summaries, newest first (by file mtime).

        Missing ``param_verify/`` dir → ``([], 0)`` (not an error). ``total`` is the
        full filtered count; the returned list is capped at ``limit``.
        """
        d = self.base / "param_verify"
        if not d.is_dir():
            return [], 0
        collected: list[tuple[float, dict[str, Any]]] = []
        for path in d.glob("*.json"):
            # Skip executor-internal state (e.g. _executed.json), not a report.
            if path.name.startswith("_"):
                continue
            try:
                mtime = path.stat().st_mtime
                with path.open("r", encoding="utf-8") as fh:
                    report = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue  # skip unreadable/corrupt file rather than 500 the list
            if not isinstance(report, dict):
                continue
            if device is not None and report.get("device") != device:
                continue
            created_at = datetime.fromtimestamp(mtime, tz=UTC).isoformat()
            collected.append((mtime, self._param_verify_summary(path.stem, created_at, report)))
        # Newest first; report_id as a stable tiebreak for equal mtimes.
        collected.sort(key=lambda t: (t[0], t[1]["report_id"]), reverse=True)
        items = [summary for _, summary in collected]
        return items[:limit], len(items)

    def param_verify_get(self, report_id: str) -> dict[str, Any]:
        """Read one full report by id, wrapped with metadata.

        Malformed id (path-traversal guard) or missing file → ``EngineDataNotFound``
        (→ 404), consistent with the rest of the engine layer.
        """
        # Path-traversal guard + hide executor-internal state (_executed.json etc.):
        # a leading '_' is never a report, matching the listing skip.
        if report_id.startswith("_") or not _REPORT_ID_RE.match(report_id):
            raise EngineDataNotFound(f"invalid param_verify report id: {report_id!r}")
        path = self.base / "param_verify" / f"{report_id}.json"
        if not path.is_file():
            raise EngineDataNotFound(f"missing param_verify report: {report_id}")
        with path.open("r", encoding="utf-8") as fh:
            report = json.load(fh)
        if not isinstance(report, dict):
            # Not a report object (e.g. a stray array) → treat as absent, not a 500.
            raise EngineDataNotFound(f"not a param_verify report: {report_id}")
        return {
            "report_id": report_id,
            "device": report.get("device"),
            "approval_id": report.get("approval_id"),
            "created_at": self._mtime_iso(path),
            "report": report,
        }
