"""Orchestrator — TCB 核心主循环。

设计参考：第8轮（完整主循环 ~280行）+ 第3轮（TCB 最小化）。

职责（TCB 三件事）：
1. 读取当前状态
2. 根据 Outcome 决定下一步
3. 执行状态转移

非 TCB 职责外移到：GitOps / PromptBuilder / BudgetTracker / ClaudeRunner / HardChecks。
F1.1 修复：将 Git 操作、Prompt 构建、预算检查提取到独立模块。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional

from hermes.executor.claude_runner import (
    ClaudeResult,
    ClaudeRunner,
)
from hermes.executor.context_bridge import ContextBridge
from hermes.executor.prompt_builder import PromptBuilder
from hermes.observability.logger import get_logger
from hermes.orchestrator.state_machine import (
    DEFAULT_PHASE_CONFIGS,
    MaxRetriesExceeded,
    Outcome,
    Phase,
    PhaseConfig,
    StateMachine,
    TransitionError,
)
from hermes.orchestrator.wal import WALEvent, WriteAheadLog
from hermes.qc.artifact import (
    ArtifactError,
    QCResultArtifact,
    load_artifact,
)
from hermes.qc.hard_checks import HardChecks
from hermes.utils.budget import BudgetExceededError, BudgetTracker
from hermes.utils.config import HermesConfig
from hermes.utils.git_ops import GitError, GitOps


class OrchestratorError(Exception):
    """Orchestrator 错误。"""


class Orchestrator:
    """四阶循环调度引擎。

    线性 pipeline + 有限回退 + QC 双通道。

    Usage:
        orch = Orchestrator(
            task_description="Fix the login bug in auth.py",
            project_dir=Path("/workspace"),
            config=HermesConfig.load(Path("config/hermes.yaml")),
        )
        final_phase = orch.run()
    """

    GLOBAL_TIMEOUT_SEC = 90 * 60  # 90 分钟全局兜底
    QC_MAX_ROUNDS = 3             # QC 最大循环次数

    def __init__(
        self,
        task_description: str,
        project_dir: Path,
        config: Optional[HermesConfig] = None,
        run_id: Optional[str] = None,
    ) -> None:
        self._run_id = run_id or str(uuid.uuid4())[:8]
        self._task_desc = task_description
        self._project_dir = project_dir.resolve()
        self._config = config or HermesConfig()
        self._task_dir = Path(self._config.general.data_dir) / self._run_id
        self._task_dir.mkdir(parents=True, exist_ok=True)

        # 核心组件（TCB）
        self._sm = StateMachine(phase_configs=self._build_phase_configs())
        self._wal = WriteAheadLog(self._task_dir / "wal.jsonl")
        self._log = get_logger("orchestrator", run_id=self._run_id)

        # 外部组件（非 TCB，F1.1 提取）
        self._git = GitOps(self._project_dir)
        self._bridge = ContextBridge(self._task_dir)
        self._runner = ClaudeRunner(work_dir=self._project_dir)
        prompts_dir = Path(__file__).parent.parent.parent.parent / "prompts" / "system"
        self._prompts = PromptBuilder(prompts_dir)
        self._budget = BudgetTracker(
            max_per_task_usd=self._config.budget.max_per_task_usd,
            max_daily_usd=self._config.budget.max_daily_usd,
            alert_threshold_pct=self._config.budget.alert_threshold_pct,
        )

        # 状态
        self._run_start: float = 0
        self._qc_rounds: int = 0

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def task_dir(self) -> Path:
        return self._task_dir

    def run(self) -> Phase:
        """执行完整的四阶循环。

        Returns:
            最终阶段（DONE 或 ESCALATE）
        """
        self._run_start = time.time()
        self._wal.append(WALEvent.RUN_START, {
            "task": self._task_desc[:200],
            "project": str(self._project_dir),
        })
        self._log.info("run_started", run_id=self._run_id, task=self._task_desc[:100])

        # 创建 git 分支
        self._create_branch()

        # 主循环 — try/finally 保证收尾逻辑一定执行（F3.3/F3.4 修复）
        abort_reason: str = ""
        try:
            while not self._sm.phase.is_terminal:
                self._check_global_timeout()

                phase = self._sm.phase

                if phase == Phase.QC:
                    outcome = self._run_qc_loop()
                else:
                    outcome = self._run_stage(phase)

                # 状态转移
                try:
                    next_phase = self._sm.transition(outcome)
                    self._wal.append(WALEvent.PHASE_COMPLETE, {
                        "phase": phase.value,
                        "outcome": outcome.value,
                        "next": next_phase.value,
                    })
                    self._log.info(
                        "phase_complete",
                        phase=phase.value,
                        outcome=outcome.value,
                        next=next_phase.value,
                    )
                except MaxRetriesExceeded:
                    self._log.error("max_retries_exceeded", phase=phase.value)
                    self._sm.transition(Outcome.HARD_FAIL)
                except TransitionError as e:
                    self._log.error("transition_error", error=str(e))
                    break

        except BudgetExceededError as e:
            abort_reason = f"budget_exceeded: {e}"
            self._log.error("run_aborted", reason=abort_reason)
        except OrchestratorError as e:
            abort_reason = f"orchestrator_error: {e}"
            self._log.error("run_aborted", reason=abort_reason)
        except Exception as e:
            abort_reason = f"unexpected_error: {type(e).__name__}: {e}"
            self._log.error("run_aborted", reason=abort_reason)
            raise
        finally:
            # 收尾逻辑 — 无论如何都执行
            self._finalize(abort_reason)

        return self._sm.phase

    def _finalize(self, abort_reason: str = "") -> None:
        """收尾：写 WAL RUN_COMPLETE/RUN_FAILED + 保存状态。

        通过 finally 调用，保证即使主循环抛异常也会执行。
        """
        final = self._sm.phase
        duration = time.time() - self._run_start

        event = WALEvent.RUN_FAILED if abort_reason else WALEvent.RUN_COMPLETE
        self._wal.append(event, {
            "final_phase": final.value,
            "duration_sec": round(duration, 1),
            "total_cost_usd": round(self._budget.task_total, 4),
            **({"reason": abort_reason} if abort_reason else {}),
        })
        self._log.info(
            "run_complete" if not abort_reason else "run_finalized_after_error",
            final_phase=final.value,
            duration_sec=round(duration, 1),
            total_cost=round(self._budget.task_total, 4),
            aborted=bool(abort_reason),
        )

        # 保存最终状态（即使异常也保存，便于恢复）
        try:
            self._save_state()
        except Exception as e:
            self._log.error("save_state_failed_in_finalize", error=str(e))

    def _run_stage(self, phase: Phase) -> Outcome:
        """执行单个阶段。"""
        config = self._sm.current_config()

        self._wal.append(WALEvent.PHASE_START, {
            "phase": phase.value,
            "model": config.model,
            "max_turns": config.max_turns,
            "budget_usd": config.budget_usd,
        })
        self._log.info("phase_started", phase=phase.value, model=config.model)

        # 构建 prompt（委托给 PromptBuilder）
        prompt = self._prompts.build_user_prompt(
            phase=phase,
            config=config,
            task_description=self._task_desc,
            context_bridge=self._bridge,
            task_dir=self._task_dir,
        )
        system_prompt = self._prompts.build_system_prompt(
            phase=phase,
            config=config,
            task_dir=self._task_dir,
        )

        # 执行 Claude
        result = self._runner.run_with_retry(
            prompt=prompt,
            system_prompt=system_prompt,
            allowed_tools=config.allowed_tools,
            max_turns=config.max_turns,
            timeout_sec=config.timeout_sec,
            budget_usd=config.budget_usd,
            model=config.model,
            permission_mode=config.permission_mode,
            max_network_retries=3,
        )

        # 追踪成本（委托给 BudgetTracker）
        self._budget.add_cost(result.cost_usd)
        self._budget.check()

        # 分析结果
        return self._analyze_result(phase, config, result)

    def _run_qc_loop(self) -> Outcome:
        """QC 双通道循环：硬检 + Claude 审查。"""
        for round_num in range(1, self.QC_MAX_ROUNDS + 1):
            self._qc_rounds = round_num
            self._log.info("qc_round_start", round=round_num)

            # 通道 1：硬检脚本
            hard_result = self._run_hard_checks()

            # 通道 2：Claude 审查
            qc_outcome = self._run_stage(Phase.QC)

            if qc_outcome != Outcome.SUCCESS:
                return qc_outcome

            # 读取 QC artifact
            qc_path = self._task_dir / "qc" / "qc-result.json"
            if not qc_path.exists():
                return Outcome.SOFT_FAIL

            try:
                qc_artifact = load_artifact(QCResultArtifact, qc_path)
            except ArtifactError as e:
                self._log.error("qc_artifact_load_failed", error=str(e))
                return Outcome.SOFT_FAIL
            # 更新硬检结果到 artifact
            qc_artifact.hard_check_passed = hard_result.passed
            qc_artifact.hard_check_details = hard_result.summary()

            self._wal.append(WALEvent.QC_RESULT, {
                "round": round_num,
                "verdict": qc_artifact.verdict.value,
                "hard_check": hard_result.passed,
                "issues": len(qc_artifact.issues_found),
            })

            if qc_artifact.is_pass and hard_result.passed:
                return Outcome.SUCCESS

            if round_num < self.QC_MAX_ROUNDS:
                # 回滚 EXECUTE 改动，重新执行
                self._rollback_execute()
                # 带 QC 反馈重新执行
                self._log.info("qc_failed_rerunning", round=round_num)
                exec_outcome = self._run_execute_with_feedback(
                    qc_path, round_num
                )
                if exec_outcome != Outcome.SUCCESS:
                    return exec_outcome

        # 所有 QC 轮次耗尽
        return Outcome.HARD_FAIL

    def _run_execute_with_feedback(
        self, qc_path: Path, attempt: int
    ) -> Outcome:
        """带 QC 反馈重新执行 EXECUTE 阶段。"""
        config = self._sm.current_config()
        feedback = self._bridge.build_qc_feedback_context(qc_path, attempt)
        prompt = self._prompts.build_user_prompt(
            phase=Phase.EXECUTE,
            config=config,
            task_description=self._task_desc,
            context_bridge=self._bridge,
            task_dir=self._task_dir,
            extra_context=feedback,
        )
        system_prompt = self._prompts.build_system_prompt(
            phase=Phase.EXECUTE,
            config=config,
            task_dir=self._task_dir,
        )

        result = self._runner.run_with_retry(
            prompt=prompt,
            system_prompt=system_prompt,
            allowed_tools=config.allowed_tools,
            max_turns=config.max_turns,
            timeout_sec=config.timeout_sec,
            budget_usd=config.budget_usd,
            model=config.model,
            max_network_retries=3,
        )

        self._budget.add_cost(result.cost_usd)
        return self._analyze_result(Phase.EXECUTE, config, result)

    def _analyze_result(
        self, phase: Phase, config: PhaseConfig, result: ClaudeResult
    ) -> Outcome:
        """分析 Claude 执行结果，返回 Outcome。"""
        if result.timed_out:
            return Outcome.TIMEOUT

        if result.refused:
            self._log.warn("claude_refused", phase=phase.value)
            return Outcome.REFUSED

        if not result.success:
            # 区分网络错误和其他硬失败（F3.2 修复）
            if result.stderr and "Network retries exhausted" in result.stderr:
                self._log.warn("network_error", phase=phase.value, stderr=result.stderr[:200])
                return Outcome.NETWORK_ERROR
            return Outcome.HARD_FAIL

        # 检查必需输出
        if config.required_output:
            output_path = self._task_dir / config.required_output
            if not output_path.exists():
                self._log.warn(
                    "missing_output",
                    phase=phase.value,
                    expected=config.required_output,
                )
                return Outcome.SOFT_FAIL

        return Outcome.SUCCESS

    # ─── 辅助方法（委托给外部模块）──────────────────────────

    def _run_hard_checks(self):
        """运行硬检脚本（委托给 GitOps + HardChecks）。"""
        try:
            diff_text = self._git.get_diff()
            changed_files = self._git.get_changed_files()
        except GitError as e:
            self._log.warn("git_ops_failed_in_hard_checks", error=str(e))
            diff_text = ""
            changed_files = []

        checks = HardChecks(
            project_dir=self._project_dir,
            max_diff_lines=self._config.qc_rules.max_diff_lines,
            protected_files=self._config.qc_rules.protected_files,
            exclude_patterns=self._config.qc_rules.exclude_patterns,
        )
        return checks.run_all(diff_text=diff_text, changed_files=changed_files)

    def _rollback_execute(self) -> None:
        """回滚 EXECUTE 阶段的代码改动（委托给 GitOps）。"""
        self._wal.append(WALEvent.ROLLBACK, {"reason": "qc_failed"})
        try:
            self._git.rollback()
        except GitError as e:
            self._log.error("rollback_failed", error=str(e))

    def _create_branch(self) -> None:
        """创建任务分支（委托给 GitOps）。"""
        branch = f"{self._config.git.branch_prefix}{self._run_id}"
        try:
            self._git.create_branch(branch)
            self._log.info("branch_created", branch=branch)
        except GitError as e:
            self._log.warn("branch_creation_failed", error=str(e))

    def _check_global_timeout(self) -> None:
        """检查全局超时。"""
        elapsed = time.time() - self._run_start
        if elapsed > self.GLOBAL_TIMEOUT_SEC:
            self._log.error("global_timeout", elapsed_sec=round(elapsed))
            self._wal.append(WALEvent.PHASE_TIMEOUT, {"type": "global"})
            raise OrchestratorError("Global timeout exceeded")

    def _save_state(self) -> None:
        """保存 Orchestrator 状态。"""
        state = {
            "run_id": self._run_id,
            "task": self._task_desc,
            "phase": self._sm.phase.value,
            "total_cost_usd": round(self._budget.task_total, 4),
            "qc_rounds": self._qc_rounds,
            "duration_sec": round(time.time() - self._run_start, 1),
            "state_machine": self._sm.to_dict(),
        }
        state_path = self._task_dir / "orchestrator-state.json"
        state_path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self._wal.append(WALEvent.STATE_SAVE, {"path": str(state_path)})

    def _build_phase_configs(self) -> dict[Phase, PhaseConfig]:
        """从配置构建阶段配置。"""
        configs: dict[Phase, PhaseConfig] = {}
        cfg = self._config

        phase_map = {
            Phase.RESEARCH: cfg.stages.research,
            Phase.PLAN: cfg.stages.plan,
            Phase.EXECUTE: cfg.stages.execute,
            Phase.QC: cfg.stages.qc,
        }

        for phase, stage_cfg in phase_map.items():
            default = DEFAULT_PHASE_CONFIGS.get(phase)
            configs[phase] = PhaseConfig(
                phase=phase,
                model=stage_cfg.model,
                max_turns=stage_cfg.max_turns,
                timeout_sec=stage_cfg.timeout_sec,
                max_retries=stage_cfg.max_retries,
                budget_usd=stage_cfg.budget_usd,
                permission_mode=stage_cfg.permission_mode,
                allowed_tools=stage_cfg.allowed_tools,
                required_output=stage_cfg.required_output,
                system_prompt_template=default.system_prompt_template if default else "",
            )

        # PROPOSE_SOP 使用默认配置
        configs[Phase.PROPOSE_SOP] = DEFAULT_PHASE_CONFIGS[Phase.PROPOSE_SOP]

        return configs
