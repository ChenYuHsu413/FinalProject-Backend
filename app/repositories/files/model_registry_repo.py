"""Model registry file repository — reads/rewrites ``Stage5_Output/models.jsonl``.

This is the **first time the governance layer writes into an engine-layer file**
(design-backend §6.2: approving a `model_promotion` triggers the backend's atomic
version switch). Kept behind this repo so "swap in the real dispatcher/registry"
later touches only this class, mirroring ``EngineFileRepository`` (batch 3).

Two properties matter:

* **Atomicity** — the rewrite is written to a temp file in the same directory and
  ``os.replace``d over the original, so a crash mid-write can never leave a
  half-written / truncated ``models.jsonl`` (a corrupt registry would break every
  ``/l1/model`` and ``/l3/models`` read).
* **Fallibility surfaced, not swallowed** — a missing file, an absent target
  version, etc. raise ``ModelRegistryError``. The approval service catches it and
  records the approval as `apply_failed` **without rolling back the approval**
  (D7.3) — an audit-truthful "the approval happened, the apply did not".

`status` values are the spec enum (資料規格書 §四 `/l3/models`): ``active /
shadow / rolled_back / archived`` — there is no `candidate` (D7.8). A promotion
sets the target ``shadow`` → ``active`` and demotes the prior ``active`` →
``archived``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.domain.scenarios import is_wellformed_scenario

ACTIVE = "active"
SHADOW = "shadow"
ROLLED_BACK = "rolled_back"
ARCHIVED = "archived"

_MODELS_PARTS = ("Stage5_Output", "models.jsonl")


class ModelRegistryError(Exception):
    """A model-registry operation could not be applied (missing file / version)."""


class ModelRegistryFileRepository:
    def __init__(self, base_dir: str) -> None:
        self.base = Path(base_dir)

    @property
    def _path(self) -> Path:
        return self.base.joinpath(*_MODELS_PARTS)

    def _read_all(self) -> list[dict[str, Any]]:
        path = self._path
        if not path.is_file():
            raise ModelRegistryError(f"models.jsonl not found at {path}")
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def _write_all(self, rows: list[dict[str, Any]]) -> None:
        path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic: write a sibling temp file, then replace. Same dir → same fs, so
        # os.replace is atomic and never leaves a half-written models.jsonl.
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp, path)

    def list_for_scenario(self, scenario_id: str) -> list[dict[str, Any]]:
        if not is_wellformed_scenario(scenario_id):
            raise ModelRegistryError(f"unknown scenario: {scenario_id!r}")
        return [r for r in self._read_all() if r.get("scenario_id") == scenario_id]

    def add_shadow(
        self,
        *,
        scenario_id: str,
        version: str,
        file_hash: str,
        metrics: dict[str, Any] | None = None,
        trained_at: str | None = None,
        pool: str = "ml_reg",
    ) -> dict[str, Any]:
        """Append a new ``shadow`` candidate record (training worker use)."""
        if not is_wellformed_scenario(scenario_id):
            raise ModelRegistryError(f"unknown scenario: {scenario_id!r}")
        rows = self._read_all()
        record = {
            "scenario_id": scenario_id,
            "version": version,
            "pool": pool,
            "status": SHADOW,
            "file_hash_sha256": file_hash,
            "metrics": metrics or {},
            "trained_at": trained_at,
        }
        rows.append(record)
        self._write_all(rows)
        return record

    def promote(self, *, scenario_id: str, to_version: str) -> dict[str, Any]:
        """Atomically switch ``to_version`` (a shadow) → active; demote prior active.

        Returns the promoted (now active) record. Raises ``ModelRegistryError`` if
        the file is missing or the target version is absent for the scenario — the
        caller marks the approval `apply_failed` on that error (D7.3).
        """
        if not is_wellformed_scenario(scenario_id):
            raise ModelRegistryError(f"unknown scenario: {scenario_id!r}")
        rows = self._read_all()

        target = next(
            (
                r
                for r in rows
                if r.get("scenario_id") == scenario_id and r.get("version") == to_version
            ),
            None,
        )
        if target is None:
            raise ModelRegistryError(f"model {to_version!r} not found for scenario {scenario_id!r}")

        for r in rows:
            if r.get("scenario_id") != scenario_id:
                continue
            if r.get("status") == ACTIVE and r is not target:
                r["status"] = ARCHIVED  # demote the outgoing active version
        target["status"] = ACTIVE

        self._write_all(rows)
        return target
