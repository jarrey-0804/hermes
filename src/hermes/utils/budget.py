"""预算追踪器。

将预算检查逻辑从 Orchestrator 中提取出来（F1.1 TCB 瘦身）。
提供成本累加和预算超限检查功能。
"""

from __future__ import annotations

from hermes.observability.logger import get_logger


class BudgetExceededError(Exception):
    """预算超限。"""


class BudgetTracker:
    """预算追踪器。

    Usage:
        tracker = BudgetTracker(max_per_task=5.0, max_daily=50.0)
        tracker.add_cost(1.5)
        tracker.check()  # 不抛异常
        tracker.add_cost(4.0)
        tracker.check()  # 抛出 BudgetExceededError
    """

    def __init__(
        self,
        max_per_task_usd: float,
        max_daily_usd: float,
        alert_threshold_pct: int = 80,
    ) -> None:
        self._max_per_task = max_per_task_usd
        self._max_daily = max_daily_usd
        self._alert_threshold_pct = alert_threshold_pct
        self._task_total = 0.0
        self._daily_total = 0.0
        self._log = get_logger("budget_tracker")
        self._alerted = False

    @property
    def task_total(self) -> float:
        """当前任务累计成本。"""
        return self._task_total

    @property
    def daily_total(self) -> float:
        """当日累计成本。"""
        return self._daily_total

    @property
    def max_per_task(self) -> float:
        return self._max_per_task

    @property
    def max_daily(self) -> float:
        return self._max_daily

    def add_cost(self, amount_usd: float) -> None:
        """累加成本。

        Args:
            amount_usd: 本次调用成本（USD）
        """
        self._task_total += amount_usd
        self._daily_total += amount_usd

        # 检查告警阈值
        if not self._alerted:
            threshold = self._max_per_task * (self._alert_threshold_pct / 100.0)
            if self._task_total >= threshold:
                self._log.warn(
                    "budget_threshold_reached",
                    task_total=round(self._task_total, 4),
                    max_per_task=self._max_per_task,
                    threshold_pct=self._alert_threshold_pct,
                )
                self._alerted = True

    def check(self) -> None:
        """检查预算是否超限。

        Raises:
            BudgetExceededError: 任务成本或日成本超限
        """
        if self._task_total > self._max_per_task:
            raise BudgetExceededError(
                f"Task cost ${self._task_total:.2f} exceeds "
                f"${self._max_per_task:.2f} per-task limit"
            )

        if self._daily_total > self._max_daily:
            raise BudgetExceededError(
                f"Daily cost ${self._daily_total:.2f} exceeds "
                f"${self._max_daily:.2f} daily limit"
            )

    def reset_task(self) -> None:
        """重置任务成本（新任务开始时调用）。"""
        self._task_total = 0.0
        self._alerted = False

    def reset_daily(self) -> None:
        """重置日成本（新一天开始时调用）。"""
        self._daily_total = 0.0

    def summary(self) -> dict[str, float]:
        """返回成本摘要。"""
        return {
            "task_total_usd": round(self._task_total, 4),
            "daily_total_usd": round(self._daily_total, 4),
            "max_per_task_usd": self._max_per_task,
            "max_daily_usd": self._max_daily,
            "task_remaining_usd": round(max(0, self._max_per_task - self._task_total), 4),
            "daily_remaining_usd": round(max(0, self._max_daily - self._daily_total), 4),
        }
