"""ContextBridge — 阶段间上下文传递。

设计参考：第4轮（优先级填充替代硬截断）。
- Tier 1（必传，~800字符）：summary + scope_decision
- Tier 2（高优，~2000字符）：findings/steps 详情
- Tier 3（补充，~1200字符）：risk + open_questions
- 总预算 4000 字符（基础），可扩展到 12K/30K（第11轮）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from hermes.qc.artifact import (
    ExecutionPlanArtifact,
    FindingsArtifact,
    QCResultArtifact,
    load_artifact,
)


class ContextBudget(str):
    """上下文字符预算等级。"""

    COMPACT = 4000    # 基础
    EXTENDED = 12000  # 扩展
    FULL = 30000      # 完整


DEFAULT_BUDGET = 4000


class ContextBridge:
    """管理阶段间的结构化上下文传递。

    Usage:
        bridge = ContextBridge(task_dir=Path("runs/abc123"))
        context = bridge.build_context(
            from_phase="research",
            to_phase="plan",
            budget=4000,
        )
    """

    def __init__(self, task_dir: Path) -> None:
        self._task_dir = task_dir

    def build_context(
        self,
        from_phase: str,
        to_phase: str,
        budget: int = DEFAULT_BUDGET,
    ) -> str:
        """为下一阶段构建上下文 preamble。

        按 Tier 1 → 2 → 3 顺序填充，直到预算用尽。
        """
        artifact = self._load_previous_artifact(from_phase)
        if artifact is None:
            return f"[WARNING] No artifact found from {from_phase} phase."

        sections: list[str] = []
        used = 0

        # Header
        header = f"## Context from {from_phase.upper()} phase\n"
        used += len(header)

        # Tier 1: Summary（必传）
        tier1 = self._get_tier1(artifact)
        if tier1:
            sections.append(f"### Summary (Tier 1)\n{tier1}")
            used += len(tier1) + 30

        # Tier 2: Details（高优）
        if used < budget:
            tier2 = self._get_tier2(artifact)
            if tier2:
                remaining = budget - used - 30
                truncated = tier2[:remaining] if len(tier2) > remaining else tier2
                sections.append(f"### Details (Tier 2)\n{truncated}")
                used += len(truncated) + 30

        # Tier 3: Extras（补充）
        if used < budget:
            tier3 = self._get_tier3(artifact)
            if tier3:
                remaining = budget - used - 30
                truncated = tier3[:remaining] if len(tier3) > remaining else tier3
                sections.append(f"### Additional (Tier 3)\n{truncated}")
                used += len(truncated) + 30

        return header + "\n\n".join(sections)

    def build_qc_feedback_context(self, qc_artifact_path: Path, attempt: int) -> str:
        """为 EXECUTE 重试构建 QC 反馈上下文。"""
        if not qc_artifact_path.exists():
            return ""

        qc = load_artifact(QCResultArtifact, qc_artifact_path)
        issues = "\n".join(f"  - {issue}" for issue in qc.issues_found[:10])
        return (
            f"## QC Feedback (Attempt {attempt})\n"
            f"Verdict: {qc.verdict.value}\n"
            f"Issues found ({len(qc.issues_found)}):\n{issues}\n"
            f"Security concerns: {', '.join(qc.security_concerns[:3])}\n"
            f"Please address these issues in this attempt."
        )

    def _load_previous_artifact(self, from_phase: str):
        """加载上一阶段的 artifact。"""
        if from_phase == "research":
            path = self._task_dir / "research" / "findings.json"
            if path.exists():
                return load_artifact(FindingsArtifact, path)
        elif from_phase == "plan":
            path = self._task_dir / "plan" / "execution-plan.json"
            if path.exists():
                return load_artifact(ExecutionPlanArtifact, path)
        elif from_phase == "qc":
            path = self._task_dir / "qc" / "qc-result.json"
            if path.exists():
                return load_artifact(QCResultArtifact, path)
        return None

    def _get_tier1(self, artifact) -> str:
        if hasattr(artifact, "tier1_summary"):
            return artifact.tier1_summary()
        return ""

    def _get_tier2(self, artifact) -> str:
        if hasattr(artifact, "tier2_details"):
            return artifact.tier2_details()
        return ""

    def _get_tier3(self, artifact) -> str:
        if hasattr(artifact, "tier3_extras"):
            return artifact.tier3_extras()
        return ""
