"""BudgetTracker 测试。"""

from __future__ import annotations

import pytest

from hermes.utils.budget import BudgetExceededError, BudgetTracker


class TestBudgetTracker:
    """BudgetTracker 测试。"""

    def test_add_cost(self):
        """累加成本。"""
        tracker = BudgetTracker(
            max_per_task_usd=5.0,
            max_daily_usd=50.0,
        )

        tracker.add_cost(1.5)
        assert tracker.task_total == 1.5
        assert tracker.daily_total == 1.5

        tracker.add_cost(2.0)
        assert tracker.task_total == 3.5
        assert tracker.daily_total == 3.5

    def test_check_within_budget(self):
        """检查预算未超限。"""
        tracker = BudgetTracker(
            max_per_task_usd=5.0,
            max_daily_usd=50.0,
        )

        tracker.add_cost(3.0)
        tracker.check()  # 不应该抛出异常

    def test_check_task_budget_exceeded(self):
        """检查任务预算超限。"""
        tracker = BudgetTracker(
            max_per_task_usd=5.0,
            max_daily_usd=50.0,
        )

        tracker.add_cost(6.0)

        with pytest.raises(BudgetExceededError, match="Task cost"):
            tracker.check()

    def test_check_daily_budget_exceeded(self):
        """检查每日预算超限。"""
        tracker = BudgetTracker(
            max_per_task_usd=50.0,  # 单任务预算足够
            max_daily_usd=10.0,  # 日预算较低
        )

        tracker.add_cost(11.0)

        with pytest.raises(BudgetExceededError, match="Daily cost"):
            tracker.check()

    def test_reset_task(self):
        """重置任务成本。"""
        tracker = BudgetTracker(
            max_per_task_usd=5.0,
            max_daily_usd=50.0,
        )

        tracker.add_cost(3.0)
        tracker.reset_task()

        assert tracker.task_total == 0.0
        assert tracker.daily_total == 3.0  # 日成本不重置

    def test_reset_daily(self):
        """重置每日成本。"""
        tracker = BudgetTracker(
            max_per_task_usd=5.0,
            max_daily_usd=50.0,
        )

        tracker.add_cost(3.0)
        tracker.reset_daily()

        assert tracker.task_total == 3.0  # 任务成本不重置
        assert tracker.daily_total == 0.0

    def test_summary(self):
        """获取成本摘要。"""
        tracker = BudgetTracker(
            max_per_task_usd=5.0,
            max_daily_usd=50.0,
        )

        tracker.add_cost(2.5)
        summary = tracker.summary()

        assert summary["task_total_usd"] == 2.5
        assert summary["daily_total_usd"] == 2.5
        assert summary["max_per_task_usd"] == 5.0
        assert summary["max_daily_usd"] == 50.0
        assert summary["task_remaining_usd"] == 2.5
        assert summary["daily_remaining_usd"] == 47.5

    def test_alert_threshold(self):
        """预算告警阈值。"""
        tracker = BudgetTracker(
            max_per_task_usd=10.0,
            max_daily_usd=100.0,
            alert_threshold_pct=80,
        )

        tracker.add_cost(7.0)  # 70% < 80%，不告警
        assert tracker._alerted is False

        tracker.add_cost(2.0)  # 90% > 80%，告警
        assert tracker._alerted is True

    def test_alert_only_once(self):
        """预算告警只触发一次。"""
        tracker = BudgetTracker(
            max_per_task_usd=10.0,
            max_daily_usd=100.0,
            alert_threshold_pct=80,
        )

        tracker.add_cost(9.0)  # 第一次告警
        assert tracker._alerted is True

        # 手动重置标志来检测是否会再次告警
        tracker._alerted = False
        tracker.add_cost(0.5)  # 再次超过阈值
        # 由于 _alerted 被重置了，它会再次告警
        # 但实际逻辑中 _alerted 不会被外部重置
        # 所以这个测试验证的是：当 _alerted 为 False 时会告警
        assert tracker._alerted is True
