"""Orchestrator finalization tests (F3.3/F3.4 修复验证)。

验证 run() 的 try/finally 保证收尾逻辑一定执行，
即使 OrchestratorError / BudgetExceededError 被抛出。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes.orchestrator.core import (
    Orchestrator,
    OrchestratorError,
)
from hermes.utils.budget import BudgetExceededError
from hermes.orchestrator.state_machine import Outcome, Phase
from hermes.utils.config import HermesConfig


@pytest.fixture
def mock_orchestrator(tmp_path: Path) -> Orchestrator:
    """创建一个带 mock 的 Orchestrator（不真正调用 Claude）。"""
    config = HermesConfig()
    config.general.data_dir = str(tmp_path / "runs")
    config.general.project_dir = str(tmp_path / "project")

    # 创建假的项目目录和 git
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".git").mkdir()

    orch = Orchestrator(
        task_description="Test task for finalization",
        project_dir=project_dir,
        config=config,
        run_id="test-fin",
    )
    return orch


class TestFinalizationOnError:
    """F3.3/F3.4：run() 在异常时仍执行收尾。"""

    def test_budget_exceeded_still_finalizes(self, mock_orchestrator: Orchestrator):
        """BudgetExceededError 触发后，WAL 和 state 仍被写入。"""
        orch = mock_orchestrator

        # Mock _run_stage 让第一阶段就超预算
        def mock_run_stage(phase):
            orch._budget.add_cost(999.0)  # 远超预算
            # 模拟 _budget.check() 会抛出 BudgetExceededError
            orch._budget.check()
            return Outcome.SUCCESS

        with patch.object(orch, "_run_stage", side_effect=mock_run_stage), \
             patch.object(orch, "_create_branch"):
            # run() 应该不抛出异常，而是返回 ESCALATE 或当前 phase
            final_phase = orch.run()

        # 验证 WAL 包含收尾记录
        wal_path = orch.task_dir / "wal.jsonl"
        assert wal_path.exists(), "WAL file should exist after finalization"

        wal_text = wal_path.read_text()
        assert "run_start" in wal_text
        # 应该有 run_failed（因为 BudgetExceededError）
        assert "run_failed" in wal_text or "run_complete" in wal_text

        # 验证状态文件被保存
        state_path = orch.task_dir / "orchestrator-state.json"
        assert state_path.exists(), "State file should exist after finalization"

        state = json.loads(state_path.read_text())
        assert state["run_id"] == "test-fin"

    def test_global_timeout_still_finalizes(self, mock_orchestrator: Orchestrator):
        """OrchestratorError（全局超时）触发后，WAL 和 state 仍被写入。"""
        orch = mock_orchestrator

        def mock_run_stage(phase):
            # 模拟全局超时
            orch._run_start = time.time() - 100 * 60  # 100 分钟前开始
            orch._check_global_timeout()
            return Outcome.SUCCESS

        with patch.object(orch, "_run_stage", side_effect=mock_run_stage), \
             patch.object(orch, "_create_branch"):
            final_phase = orch.run()

        # 验证收尾执行了
        wal_path = orch.task_dir / "wal.jsonl"
        assert wal_path.exists()

        state_path = orch.task_dir / "orchestrator-state.json"
        assert state_path.exists()

    def test_unexpected_exception_still_finalizes(self, mock_orchestrator: Orchestrator):
        """意外异常触发后，WAL 和 state 仍被写入，异常被重新抛出。"""
        orch = mock_orchestrator

        def mock_run_stage(phase):
            raise ValueError("Unexpected internal error")

        with patch.object(orch, "_run_stage", side_effect=mock_run_stage), \
             patch.object(orch, "_create_branch"):
            with pytest.raises(ValueError, match="Unexpected internal error"):
                orch.run()

        # 即使异常被重新抛出，收尾逻辑仍应执行
        wal_path = orch.task_dir / "wal.jsonl"
        assert wal_path.exists()

        state_path = orch.task_dir / "orchestrator-state.json"
        assert state_path.exists()

    def test_normal_completion_finalizes(self, mock_orchestrator: Orchestrator):
        """正常完成时，收尾逻辑也正确执行。"""
        orch = mock_orchestrator

        # 模拟直接到达终态
        orch._sm._phase = Phase.DONE

        with patch.object(orch, "_create_branch"):
            final_phase = orch.run()

        assert final_phase == Phase.DONE

        wal_path = orch.task_dir / "wal.jsonl"
        assert wal_path.exists()

        wal_text = wal_path.read_text()
        assert "run_complete" in wal_text
        # 不应有 run_failed
        lines = [json.loads(l) for l in wal_text.strip().split("\n") if l.strip()]
        events = [e["event"] for e in lines]
        assert "run_failed" not in events

    def test_wal_records_abort_reason(self, mock_orchestrator: Orchestrator):
        """异常中止时，WAL 记录包含原因。"""
        orch = mock_orchestrator

        def mock_run_stage(phase):
            raise BudgetExceededError("Cost $99.99 exceeds $5.00")

        with patch.object(orch, "_run_stage", side_effect=mock_run_stage), \
             patch.object(orch, "_create_branch"):
            orch.run()

        wal_path = orch.task_dir / "wal.jsonl"
        lines = [json.loads(l) for l in wal_path.read_text().strip().split("\n") if l.strip()]

        # 找到 run_failed 事件（至少有 1 条包含原因）
        failed_events = [e for e in lines if e["event"] == "run_failed"]
        assert len(failed_events) >= 1
        reasons = [e["data"].get("reason", "") for e in failed_events]
        assert any("budget_exceeded" in r for r in reasons), (
            f"Expected budget_exceeded in reasons, got: {reasons}"
        )
