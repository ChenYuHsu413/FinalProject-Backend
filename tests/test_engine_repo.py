"""Unit tests for the engine file repository (missing=NotFound, path-safety)."""

from __future__ import annotations

import pytest
from app.mock.simulator import MockSimulator
from app.repositories.files.engine_repo import EngineDataNotFound, EngineFileRepository


def test_missing_file_raises_not_found(tmp_path):
    repo = EngineFileRepository(str(tmp_path))  # empty dir
    with pytest.raises(EngineDataNotFound):
        repo.l1_realtime("01_Pick_and_Place")


def test_unknown_scenario_raises_not_found_without_touching_fs(tmp_path):
    repo = EngineFileRepository(str(tmp_path))
    with pytest.raises(EngineDataNotFound):
        repo.l1_realtime("../../etc/passwd")


def test_reads_generated_files(tmp_path):
    MockSimulator(str(tmp_path)).generate_all()
    repo = EngineFileRepository(str(tmp_path))
    rt = repo.l1_realtime("01_Pick_and_Place")
    assert isinstance(rt["predictions"]["DV_mean"], float)  # moving value (batch 4)
    assert rt["latency"]["within_1ms_ratio"] == 1.0
    models = repo.l3_models("01_Pick_and_Place", status="active")
    assert all(m["status"] == "active" for m in models)
    assert models  # at least one active model


def test_scenarios_status_has_active_keys(tmp_path):
    MockSimulator(str(tmp_path)).generate_all()
    repo = EngineFileRepository(str(tmp_path))
    status = repo.scenarios_status()
    assert set(status["scenarios"]) == {
        "01_Pick_and_Place",
        "18_Ball_Screw",
        "34_Rotor_Demag",
    }
