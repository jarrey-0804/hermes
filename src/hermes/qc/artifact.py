"""Artifact — 阶段产出的结构化数据模型（Pydantic v2）。

设计参考：第4轮（Artifact Schema + 双层验证）+ 第11轮（分级上限）。
- RESEARCH → FindingsArtifact
- PLAN → ExecutionPlanArtifact
- QC → QCResultArtifact
- SOP → SOPProposalArtifact
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ─── 通用基础 ──────────────────────────────────────────────


class ArtifactVersion(StrEnum):
    V1_0 = "1.0"


class ConfidenceLevel(StrEnum):
    HIGH = "high"  # > 0.8
    MEDIUM = "medium"  # 0.5 - 0.8
    LOW = "low"  # < 0.5


class RiskSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TaskComplexity(StrEnum):
    TRIVIAL = "trivial"
    SIMPLE = "simple"
    MEDIUM = "medium"
    LARGE = "large"
    CRITICAL = "critical"


class StepType(StrEnum):
    ADD = "ADD"
    MODIFY = "MODIFY"
    DELETE = "DELETE"
    TEST = "TEST"
    CONFIG = "CONFIG"


class QCVerdict(StrEnum):
    PASS = "PASS"
    CONDITIONAL_PASS = "CONDITIONAL_PASS"
    FAIL = "FAIL"


# ─── RESEARCH Artifact ─────────────────────────────────────


class ResearchFinding(BaseModel):
    """单条研究发现。"""

    finding: str = Field(..., min_length=10, description="发现描述")
    evidence: str = Field(..., min_length=5, description="证据（文件路径 + 行号）")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度 0-1")

    @field_validator("confidence")
    @classmethod
    def round_confidence(cls, v: float) -> float:
        return round(v, 2)


class RiskItem(BaseModel):
    """风险项。"""

    risk: str = Field(..., min_length=5)
    severity: RiskSeverity = RiskSeverity.MEDIUM
    mitigation: str = ""


class FindingsArtifact(BaseModel):
    """RESEARCH 阶段产出。"""

    schema_version: str = Field(default="1.0")
    key_findings: list[ResearchFinding] = Field(
        ..., min_length=1, max_length=15, description="核心发现（上限15条，第11轮分级策略）"
    )
    files_in_scope: list[str] = Field(default_factory=list, description="影响范围内的文件列表")
    tech_stack: list[str] = Field(default_factory=list)
    risk_items: list[RiskItem] = Field(default_factory=list, max_length=10)
    scope_decision: str = Field(..., min_length=10, description="范围决策说明")
    open_questions: list[str] = Field(default_factory=list, max_length=5)
    task_complexity: TaskComplexity = TaskComplexity.MEDIUM
    languages_detected: list[str] = Field(default_factory=list)

    def tier1_summary(self) -> str:
        """Tier 1 上下文（必传，~800字符）：summary + scope_decision。"""
        findings_brief = "; ".join(f.finding[:80] for f in self.key_findings[:3])
        return (
            f"Scope: {self.scope_decision[:200]}\n"
            f"Key findings: {findings_brief[:400]}\n"
            f"Tech stack: {', '.join(self.tech_stack[:10])}\n"
            f"Complexity: {self.task_complexity.value}\n"
            f"Files in scope: {len(self.files_in_scope)}"
        )

    def tier2_details(self) -> str:
        """Tier 2 上下文（高优，~2000字符）：findings 详情。"""
        lines = []
        for i, f in enumerate(self.key_findings[:5], 1):
            lines.append(f"  {i}. [{f.confidence:.0%}] {f.finding}")
            lines.append(f"     Evidence: {f.evidence[:120]}")
        return "\n".join(lines)

    def tier3_extras(self) -> str:
        """Tier 3 上下文（补充，~1200字符）：风险 + 开放问题。"""
        lines = []
        if self.risk_items:
            lines.append("Risks:")
            for r in self.risk_items[:5]:
                lines.append(f"  - [{r.severity.value}] {r.risk[:100]}")
        if self.open_questions:
            lines.append("Open questions:")
            for q in self.open_questions[:3]:
                lines.append(f"  - {q[:120]}")
        return "\n".join(lines)


# ─── PLAN Artifact ─────────────────────────────────────────


class ExecutionStep(BaseModel):
    """执行计划中的单步。"""

    id: int = Field(..., ge=1)
    action: str = Field(..., min_length=10, description="步骤描述")
    files_affected: list[str] = Field(default_factory=list, max_length=10)
    depends_on: list[int] = Field(default_factory=list)
    step_type: StepType = StepType.MODIFY
    rollback: str = Field(default="", description="回滚方案")
    risk: str = Field(default="low")

    @field_validator("depends_on")
    @classmethod
    def no_self_dependency(cls, v: list[int], info: Any) -> list[int]:
        return v  # 拓扑排序在外部验证


class ExecutionPlanArtifact(BaseModel):
    """PLAN 阶段产出。"""

    schema_version: str = Field(default="1.0")
    steps: list[ExecutionStep] = Field(
        ..., min_length=1, max_length=12, description="执行步骤（≤12步，每步≤3文件）"
    )
    acceptance_criteria: list[str] = Field(..., min_length=1, max_length=10)
    estimated_complexity: TaskComplexity = TaskComplexity.MEDIUM
    checkpoint: bool = Field(default=False, description="安全断点标记")
    estimated_tokens: int = Field(default=0, ge=0)
    total_files_affected: int = Field(default=0, ge=0)

    def tier1_summary(self) -> str:
        """Tier 1：步骤概览。"""
        steps_brief = "; ".join(f"Step{s.id}:{s.step_type.value}" for s in self.steps[:6])
        return (
            f"Plan: {len(self.steps)} steps, complexity={self.estimated_complexity.value}\n"
            f"Steps: {steps_brief[:400]}\n"
            f"Files affected: {self.total_files_affected}\n"
            f"Checkpoint: {self.checkpoint}"
        )

    def tier2_details(self) -> str:
        """Tier 2：步骤详情。"""
        lines = []
        for s in self.steps:
            deps = f" (deps: {s.depends_on})" if s.depends_on else ""
            lines.append(f"  Step {s.id} [{s.step_type.value}]{deps}: {s.action[:100]}")
            for f in s.files_affected[:3]:
                lines.append(f"    → {f}")
        return "\n".join(lines)

    def topological_order(self) -> list[ExecutionStep]:
        """返回拓扑排序后的步骤列表。"""
        in_degree: dict[int, int] = {s.id: 0 for s in self.steps}
        adj: dict[int, list[int]] = {s.id: [] for s in self.steps}

        for s in self.steps:
            for dep in s.depends_on:
                if dep in adj:
                    adj[dep].append(s.id)
                    in_degree[s.id] += 1

        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        result: list[ExecutionStep] = []
        step_map = {s.id: s for s in self.steps}

        while queue:
            queue.sort()
            sid = queue.pop(0)
            result.append(step_map[sid])
            for neighbor in adj[sid]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return result


# ─── QC Artifact ───────────────────────────────────────────


class QCCheckItem(BaseModel):
    """QC 单项检查结果。"""

    check_name: str
    passed: bool
    details: str = ""
    severity: RiskSeverity = RiskSeverity.LOW


class QCResultArtifact(BaseModel):
    """QC 阶段产出。"""

    schema_version: str = Field(default="1.0")
    verdict: QCVerdict = QCVerdict.FAIL
    checklist: list[QCCheckItem] = Field(default_factory=list)
    hard_check_passed: bool = Field(default=False, description="硬检脚本是否通过")
    hard_check_details: str = ""
    issues_found: list[str] = Field(default_factory=list, max_length=20)
    issues_fixed: list[str] = Field(default_factory=list)
    test_results: str = ""
    diff_summary: str = ""
    security_concerns: list[str] = Field(default_factory=list)
    failure_attribution: str = Field(default="", description="失败归因（供 SOP 生成使用）")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @property
    def is_pass(self) -> bool:
        return self.verdict == QCVerdict.PASS

    @property
    def is_conditional(self) -> bool:
        return self.verdict == QCVerdict.CONDITIONAL_PASS


# ─── SOP Proposal Artifact ─────────────────────────────────


class SOPProposalArtifact(BaseModel):
    """SOP 提案（由 Orchestrator 生成，非 Claude）。"""

    schema_version: str = Field(default="1.0")
    proposal_id: str = ""
    trigger_rule: str = ""  # REPEATED_FAILURE, TIMEOUT_PATTERN, etc.
    description: str = ""
    suggested_config_change: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.3, ge=0.0, le=1.0)
    sample_count: int = Field(default=0, ge=0)
    affected_phases: list[str] = Field(default_factory=list)
    risk_assessment: str = ""


# ─── Artifact I/O ──────────────────────────────────────────


class ArtifactError(Exception):
    """Artifact I/O 错误。"""


class ArtifactNotFoundError(ArtifactError):
    """Artifact 文件不存在。"""


class ArtifactCorruptError(ArtifactError):
    """Artifact 文件损坏（JSON 或 schema 错误）。"""


def save_artifact(artifact: BaseModel, path: Path) -> None:
    """保存 artifact 到 JSON 文件（原子写入）。

    Raises:
        ArtifactError: 写入失败（权限、磁盘满等）
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(
            artifact.model_dump_json(indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)
    except OSError as e:
        raise ArtifactError(f"Failed to save artifact to {path}: {e}") from e


def load_artifact(cls: type[BaseModel], path: Path) -> BaseModel:
    """从 JSON 文件加载 artifact。

    Raises:
        ArtifactNotFoundError: 文件不存在
        ArtifactCorruptError: JSON 解析或 schema 验证失败
    """
    if not path.exists():
        raise ArtifactNotFoundError(f"Artifact file not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ArtifactCorruptError(f"Invalid JSON in {path}: {e}") from e

    try:
        return cls.model_validate(data)
    except Exception as e:
        raise ArtifactCorruptError(f"Schema validation failed for {path}: {e}") from e


def validate_artifact(cls: type[BaseModel], data: dict[str, Any]) -> tuple[bool, list[str]]:
    """双层验证（第4轮）：
    Layer 1: jsonschema 硬校验（Pydantic）
    Layer 2: 语义校验

    Returns:
        (is_valid, errors)
    """
    errors: list[str] = []

    # Layer 1: Pydantic schema validation
    try:
        cls.model_validate(data)
    except Exception as e:
        errors.append(f"Schema validation failed: {e}")
        return False, errors

    # Layer 2: Semantic validation
    try:
        artifact = cls.model_validate(data)
        if isinstance(artifact, FindingsArtifact):
            if not artifact.scope_decision.strip():
                errors.append("scope_decision is empty")
            if len(artifact.key_findings) == 0:
                errors.append("No findings provided")
        elif isinstance(artifact, ExecutionPlanArtifact):
            if not artifact.acceptance_criteria:
                errors.append("No acceptance criteria defined")
            # 检查步骤依赖是否合法
            step_ids = {s.id for s in artifact.steps}
            for s in artifact.steps:
                for dep in s.depends_on:
                    if dep not in step_ids:
                        errors.append(f"Step {s.id} depends on non-existent step {dep}")
    except Exception as e:
        errors.append(f"Semantic validation error: {e}")

    return len(errors) == 0, errors
