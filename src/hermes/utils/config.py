"""Config — Pydantic v2 配置模型 + ConfigValidator。

设计参考：第8轮（统一配置系统）+ 第13轮（交叉约束检查）。
- hermes.yaml 的完整 Pydantic 模型
- 启动时强制验证
- 环境变量校验
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hermes.observability.logger import get_logger


# ─── 子模型 ────────────────────────────────────────────────


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    default: str = "sonnet"
    research: str = "haiku"
    plan: str = "sonnet"
    execute: str = "sonnet"
    qc: str = "haiku"


class StageConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    timeout_sec: int = Field(default=900, ge=60, le=7200)
    max_turns: int = Field(default=8, ge=1, le=50)
    max_retries: int = Field(default=1, ge=0, le=5)
    budget_usd: float = Field(default=2.0, ge=0.01, le=50.0)
    model: str = "sonnet"
    permission_mode: str = "default"
    allowed_tools: list[str] = Field(default_factory=list)
    required_output: Optional[str] = None


class StagesConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    research: StageConfig = Field(default_factory=lambda: StageConfig(
        timeout_sec=900, max_turns=8, max_retries=1, budget_usd=1.0,
        model="haiku",
        allowed_tools=["Read", "Glob", "Grep", "Write",
                        "Bash(git log:*)", "Bash(git diff HEAD:*)"],
        required_output="research/findings.json",
    ))
    plan: StageConfig = Field(default_factory=lambda: StageConfig(
        timeout_sec=600, max_turns=6, max_retries=1, budget_usd=1.5,
        model="sonnet",
        allowed_tools=["Read", "Glob", "Grep", "Write",
                        "Bash(git log:*)", "Bash(git diff HEAD:*)"],
        required_output="plan/execution-plan.json",
    ))
    execute: StageConfig = Field(default_factory=lambda: StageConfig(
        timeout_sec=1800, max_turns=12, max_retries=2, budget_usd=3.0,
        model="sonnet",
        allowed_tools=["Read", "Glob", "Grep", "Write", "Edit",
                        "Bash(npm run:*)", "Bash(python src/:*)",
                        "Bash(pytest:*)", "Bash(git diff HEAD:*)"],
    ))
    qc: StageConfig = Field(default_factory=lambda: StageConfig(
        timeout_sec=600, max_turns=4, max_retries=0, budget_usd=0.5,
        model="haiku", permission_mode="plan",
        allowed_tools=["Read", "Glob", "Grep", "Write",
                        "Bash(git diff HEAD:*)", "Bash(pytest:*)"],
        required_output="qc/qc-result.json",
    ))


class QCRulesConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    max_diff_lines: int = Field(default=500, ge=50, le=5000)
    check_secrets: bool = True
    check_binary_files: bool = True
    check_todo_fixme: bool = True
    protected_files: list[str] = Field(default_factory=lambda: [
        ".env", ".env.*", "**/credentials*", "**/*.pem", "**/*.key"
    ])
    exclude_patterns: list[str] = Field(default_factory=lambda: [
        "node_modules/**", ".git/**", "__pycache__/**", "*.pyc",
        "dist/**", "build/**",
    ])


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    max_per_task_usd: float = Field(default=5.0, ge=0.5, le=100.0)
    max_daily_usd: float = Field(default=50.0, ge=5.0, le=1000.0)
    alert_threshold_pct: int = Field(default=80, ge=50, le=100)
    cost_model: str = "anthropic"


class DockerConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    enabled: bool = False
    image: str = "hermes-agent:latest"
    memory: str = "4g"
    cpus: int = Field(default=2, ge=1, le=16)
    pids_limit: int = Field(default=100, ge=10, le=1000)
    network: str = "restricted"
    read_only: bool = True


class SecurityConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    webfetch_whitelist: list[str] = Field(default_factory=list)
    forbidden_git_ops: list[str] = Field(default_factory=lambda: [
        "checkout", "add", "commit", "reset", "stash",
        "merge", "rebase", "push", "fetch",
    ])
    max_file_size_kb: int = Field(default=1024, ge=64, le=10240)


class SOPConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    enabled: bool = False
    ttl_days: int = Field(default=7, ge=1, le=90)
    approval_sla_hours: int = Field(default=48, ge=1, le=168)
    auto_expire: bool = True


class HeartbeatConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    interval_sec: int = Field(default=10, ge=5, le=60)
    timeout_sec: int = Field(default=30, ge=15, le=120)


class QueueConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    max_concurrent: int = Field(default=1, ge=1, le=32)
    priorities: list[str] = Field(
        default_factory=lambda: ["SYSTEM", "URGENT", "NORMAL", "LOW"]
    )


class NotificationsConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    enabled: bool = False
    slack_webhook_env: str = "SLACK_WEBHOOK_URL"


class DashboardConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    enabled: bool = False
    port: int = Field(default=8080, ge=1024, le=65535)
    refresh_sec: int = Field(default=30, ge=5, le=300)


class GitConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    branch_prefix: str = "auto/"
    pre_receive_hook: bool = False
    auto_commit: bool = False


class GeneralConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    project_dir: str = "/workspace"
    data_dir: str = "./runs"
    log_level: str = "INFO"
    log_format: str = "json"


# ─── 顶层配置 ──────────────────────────────────────────────


class HermesConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    """Hermes 统一配置（hermes.yaml）。"""

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    stages: StagesConfig = Field(default_factory=StagesConfig)
    qc_rules: QCRulesConfig = Field(default_factory=QCRulesConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    docker: DockerConfig = Field(default_factory=DockerConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    sop: SOPConfig = Field(default_factory=SOPConfig)
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    git: GitConfig = Field(default_factory=GitConfig)

    @classmethod
    def load(cls, path: Path) -> HermesConfig:
        """从 YAML 文件加载配置。"""
        if not path.exists():
            log = get_logger("config")
            log.warning(
                "config_file_not_found_using_defaults",
                path=str(path),
            )
            return cls()

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not raw:
            return cls()
        return cls.model_validate(raw)

    def save(self, path: Path) -> None:
        """保存配置到 YAML 文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump(mode="json")
        path.write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )


# ─── ConfigValidator ───────────────────────────────────────


class ValidationResult:
    """配置验证结果。"""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        return (
            f"Validation: {len(self.errors)} errors, {len(self.warnings)} warnings"
        )


class ConfigValidator:
    """配置验证器（启动时运行）。

    检查：
    1. Pydantic schema 验证（自动）
    2. 交叉约束检查
    3. 环境变量存在性
    4. 路径可达性
    """

    def __init__(self, config: HermesConfig) -> None:
        self._config = config
        self._log = get_logger("config_validator")

    def validate(self) -> ValidationResult:
        """执行完整验证。"""
        result = ValidationResult()

        self._check_cross_constraints(result)
        self._check_env_vars(result)
        self._check_paths(result)
        self._check_tool_whitelist(result)
        self._check_budget_sanity(result)

        if result.is_valid:
            self._log.info("config_valid", errors=0, warnings=len(result.warnings))
        else:
            self._log.error("config_invalid", errors=len(result.errors),
                           warnings=len(result.warnings))

        return result

    def _check_cross_constraints(self, result: ValidationResult) -> None:
        """交叉约束检查。"""
        cfg = self._config

        # EXECUTE 阶段超时不应小于 RESEARCH
        if cfg.stages.execute.timeout_sec < cfg.stages.research.timeout_sec:
            result.add_warning(
                "EXECUTE timeout < RESEARCH timeout. "
                "Consider increasing EXECUTE timeout."
            )

        # 预算合理性
        total_stage_budget = (
            cfg.stages.research.budget_usd
            + cfg.stages.plan.budget_usd
            + cfg.stages.execute.budget_usd
            + cfg.stages.qc.budget_usd
        )
        if total_stage_budget > cfg.budget.max_per_task_usd * 1.5:
            result.add_warning(
                f"Sum of stage budgets (${total_stage_budget:.2f}) exceeds "
                f"max_per_task_usd (${cfg.budget.max_per_task_usd:.2f}) * 1.5"
            )

        # QC 阶段应该是只读的
        if cfg.stages.qc.permission_mode != "plan":
            result.add_warning(
                "QC stage permission_mode should be 'plan' (read-only)"
            )

        # 心跳超时应大于间隔
        if cfg.heartbeat.timeout_sec <= cfg.heartbeat.interval_sec:
            result.add_error(
                "heartbeat.timeout_sec must be > heartbeat.interval_sec"
            )

    def _check_env_vars(self, result: ValidationResult) -> None:
        """检查环境变量。"""
        # ANTHROPIC_API_KEY 必须存在
        if not os.environ.get("ANTHROPIC_API_KEY"):
            result.add_warning(
                "ANTHROPIC_API_KEY not set. Claude CLI calls will fail."
            )

        # 通知相关
        if self._config.notifications.enabled:
            env_name = self._config.notifications.slack_webhook_env
            if not os.environ.get(env_name):
                result.add_warning(
                    f"Notifications enabled but {env_name} not set"
                )

    def _check_paths(self, result: ValidationResult) -> None:
        """检查路径可达性。"""
        project_dir = Path(self._config.general.project_dir)
        if not project_dir.exists():
            result.add_warning(
                f"project_dir does not exist: {project_dir}"
            )

    def _check_tool_whitelist(self, result: ValidationResult) -> None:
        """检查工具白名单合理性。"""
        # RESEARCH 不应包含 Write/Edit（除了 artifact 输出）
        research_tools = self._config.stages.research.allowed_tools
        if "Edit" in research_tools:
            result.add_warning(
                "RESEARCH stage has 'Edit' tool. RESEARCH should be read-only."
            )

        # 检查危险 git 操作不在白名单中
        forbidden = set(self._config.security.forbidden_git_ops)
        for stage_name in ["research", "plan", "execute", "qc"]:
            stage = getattr(self._config.stages, stage_name)
            for tool in stage.allowed_tools:
                # 用 regex 解析 git 操作（F5.1 修复）
                # 匹配 "Bash(git <op>" 格式，提取 <op> 部分
                match = re.match(r"Bash\(git\s+(\w+)", tool)
                if match:
                    op = match.group(1)
                    if op in forbidden:
                        result.add_error(
                            f"{stage_name} stage has forbidden git op: {tool}"
                        )

    def _check_budget_sanity(self, result: ValidationResult) -> None:
        """检查预算配置合理性。"""
        cfg = self._config
        if cfg.budget.max_daily_usd < cfg.budget.max_per_task_usd:
            result.add_error(
                "max_daily_usd must be >= max_per_task_usd"
            )
