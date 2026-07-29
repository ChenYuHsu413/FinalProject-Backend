# -*- coding: utf-8 -*-
"""ExecutorLoop.poll_once 回歸測試 — D7.3「決策 ≠ 套用」短窗不得 blackhole。

用 stdlib unittest(執行器無 pytest 依賴,但 pytest 亦可收集此檔),以 stub
executor 取代 SimExecutor,避免拉進 numpy / pmsm_sim。只驗 poll_once 的
去重/重試判定,staleness 與回滾邏輯不在此測試範圍。

執行:
    .venv\\Scripts\\python.exe -m unittest executor.test_sim_executor
    或  python executor/test_sim_executor.py
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

import sim_executor


class _FakeResponse:
    def __init__(self, approvals):
        self._approvals = approvals

    def raise_for_status(self):
        pass

    def json(self):
        return {"approvals": self._approvals}


class _FakeRequests:
    """替換 sim_executor.requests;回傳目前設定的 approvals 清單。"""

    def __init__(self):
        self.approvals = []

    def get(self, *args, **kwargs):
        return _FakeResponse(self.approvals)


class _StubExecutor:
    """記錄被套用的提案,回傳固定 verified 報告(不觸及數位孿生)。"""

    def __init__(self):
        self.applied = []

    def apply_and_verify(self, device, param, new_value, dv_estimate,
                         proposal_current=None):
        self.applied.append((device, param, new_value))
        return {"device": device, "param": param, "old": 600.0,
                "new": float(new_value), "outcome": "verified",
                "executed_on": "simulated_device"}


def _approval(status):
    return {"approval_id": "APR-1", "device": "M07", "decided_by": "eng-1",
            "side_effect_status": status,
            "summary": {"param": "Acc", "new": 540.0, "current": 600.0}}


class PollOnceRetryWindow(unittest.TestCase):
    def setUp(self):
        self._orig_requests = sim_executor.requests
        self.fake = _FakeRequests()
        sim_executor.requests = self.fake
        self.tmp = tempfile.TemporaryDirectory()
        self.stub = _StubExecutor()
        self.loop = sim_executor.ExecutorLoop(
            base_url="http://test", token="t", executor=self.stub,
            engine_data_dir=self.tmp.name)

    def tearDown(self):
        sim_executor.requests = self._orig_requests
        self.tmp.cleanup()

    def _report_files(self):
        return [f for f in os.listdir(self.loop.out_dir)
                if f.startswith("M07_")]

    def test_apply_window_none_retries_not_blackholed(self):
        # 第一輪:side_effect_status=None(核准已落定、套用尚未 commit 的短窗)
        self.fake.approvals = [_approval(None)]
        reports = self.loop.poll_once()
        self.assertEqual(reports, [], "None 狀態本輪不應執行")
        self.assertNotIn("APR-1", self.loop.done, "None 狀態不得進去重集")
        self.assertEqual(self.stub.applied, [])
        self.assertEqual(self._report_files(), [])

        # 第二輪:套用已落定,side_effect_status=applied → 正常執行並產出報告
        self.fake.approvals = [_approval("applied")]
        reports = self.loop.poll_once()
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["outcome"], "verified")
        self.assertIn("APR-1", self.loop.done)
        self.assertEqual(self.stub.applied, [("M07", "Acc", 540.0)])
        self.assertEqual(len(self._report_files()), 1)

    def test_terminal_failure_excluded_and_deduped(self):
        # 排除法:非 None 且非 applied 的終態(failed / apply_failed …)→ 去重、不執行
        for status in ("failed", "apply_failed"):
            with self.subTest(status=status):
                loop = sim_executor.ExecutorLoop(
                    base_url="http://test", token="t", executor=_StubExecutor(),
                    engine_data_dir=self.tmp.name)
                self.fake.approvals = [_approval(status)]
                reports = loop.poll_once()
                self.assertEqual(reports, [])
                self.assertIn("APR-1", loop.done, f"{status} 應進去重集(終態略過)")

    def test_done_persisted_across_reload(self):
        self.fake.approvals = [_approval("applied")]
        self.loop.poll_once()
        # 重新載入 loop:_executed.json 應已含 APR-1,不再重跑
        loop2 = sim_executor.ExecutorLoop(
            base_url="http://test", token="t", executor=_StubExecutor(),
            engine_data_dir=self.tmp.name)
        self.assertIn("APR-1", loop2.done)
        reports = loop2.poll_once()
        self.assertEqual(reports, [], "已在去重集者不應重跑")


if __name__ == "__main__":
    unittest.main()
