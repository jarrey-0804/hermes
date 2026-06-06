"""State machine — Phase/Outcome enums, transition table, validation.

TCB 核心：状态机定义与转移验证。
设计参考：第3轮（PhaseConfig）+ 第8轮（状态转移表）+ 第11轮（边界案例）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Phase(StrEnum):
    """四阶循环 + 终态。"""

    RESEARCH = "research"
    PLAN = "plan"
    EXECUTE = "execute"
    QC = "qc"
    PROPOSE_SOP = "propose_sop"
    DONE = "done"
    ESCALATE = "escalate"

    @property
    def is_terminal(self) -> bool:
        return self in (Phase.DONE, Phase.ESCALATE)

    @property
    def is_executable(self) -> bool:
        """是否为需要 Claude 执行的阶段。"""
        return self in (Phase.RESEARCH, Phase.PLAN, Phase.EXECUTE, Phase.QC, Phase.PROPOSE_SOP)


class Outcome(StrEnum):
    """阶段执行结果。"""

    SUCCESS = "success"
    SOFT_FAIL = "soft_fail"
    HARD_FAIL = "hard_fail"
    TIMEOUT = "timeout"
    REFUSED = "refused"  # Claude 拒绝执行（第11轮边界案例）
    NETWORK_ERROR = "network_error"  # 网络/API 错误（重试耗尽后）


@dataclass(frozen=True)
class PhaseConfig:
    """单个阶段的执行配置。"""

    phase: Phase
    model: str = "sonnet"
    max_turns: int = 8
    timeout_sec: int = 900
    max_retries: int = 1
    budget_usd: float = 2.0
    permission_mode: str = "default"  # default | plan | acceptEdits
    allowed_tools: list[str] = field(default_factory=list)
    required_output: str | None = None
    system_prompt_template: str = ""
    context_template: str = ""
    output_schema: dict | None = None

    @property
    def is_readonly(self) -> bool:
        """是否为只读阶段（RESEARCH / PLAN / QC）。"""
        return self.phase in (Phase.RESEARCH, Phase.PLAN, Phase.QC)


# ─── 状态转移表 ────────────────────────────────────────────
# 线性 pipeline + 有限回退（第2轮共识）
# QC 失败 → 回退到 PLAN（SOFT_FAIL）或 PROPOSE_SOP（HARD_FAIL）

_TRANSITIONS: dict[Phase, dict[Outcome, Phase]] = {
    Phase.RESEARCH: {
        Outcome.SUCCESS: Phase.PLAN,
        Outcome.SOFT_FAIL: Phase.RESEARCH,  # 重试自身
        Outcome.HARD_FAIL: Phase.PLAN,  # 跳过 RESEARCH（第3轮 ErrorPolicy）
        Outcome.TIMEOUT: Phase.PLAN,
        Outcome.NETWORK_ERROR: Phase.PLAN,
    },
    Phase.PLAN: {
        Outcome.SUCCESS: Phase.EXECUTE,
        Outcome.SOFT_FAIL: Phase.PLAN,
        Outcome.HARD_FAIL: Phase.ESCALATE,
        Outcome.TIMEOUT: Phase.ESCALATE,
        Outcome.NETWORK_ERROR: Phase.ESCALATE,
    },
    Phase.EXECUTE: {
        Outcome.SUCCESS: Phase.QC,
        Outcome.SOFT_FAIL: Phase.EXECUTE,
        Outcome.HARD_FAIL: Phase.ESCALATE,
        Outcome.TIMEOUT: Phase.ESCALATE,
        Outcome.NETWORK_ERROR: Phase.ESCALATE,
    },
    Phase.QC: {
        Outcome.SUCCESS: Phase.DONE,
        Outcome.SOFT_FAIL: Phase.PLAN,  # QC 不过回退到 PLAN 重新规划
        Outcome.HARD_FAIL: Phase.PROPOSE_SOP,
        Outcome.TIMEOUT: Phase.PROPOSE_SOP,
        Outcome.NETWORK_ERROR: Phase.PROPOSE_SOP,
    },
    Phase.PROPOSE_SOP: {
        Outcome.SUCCESS: Phase.ESCALATE,
        Outcome.SOFT_FAIL: Phase.ESCALATE,
        Outcome.HARD_FAIL: Phase.ESCALATE,
        Outcome.TIMEOUT: Phase.ESCALATE,
        Outcome.NETWORK_ERROR: Phase.ESCALATE,
    },
}

# REFUSED 一律进入 ESCALATE（Claude 拒绝执行，不重试）
for _phase in list(_TRANSITIONS.keys()):
    _TRANSITIONS[_phase][Outcome.REFUSED] = Phase.ESCALATE


# ─── 默认阶段配置 ──────────────────────────────────────────

DEFAULT_PHASE_CONFIGS: dict[Phase, PhaseConfig] = {
    Phase.RESEARCH: PhaseConfig(
        phase=Phase.RESEARCH,
        model="haiku",
        max_turns=8,
        timeout_sec=900,
        max_retries=1,
        budget_usd=1.0,
        allowed_tools=[
            "Read",
            "Glob",
            "Grep",
            "Write",
            "Bash(git log:*)",
            "Bash(git diff HEAD:*)",
        ],
        required_output="research/findings.json",
        system_prompt_template="research_system.j2",
        output_schema=None,  # 由 artifact.py 提供
    ),
    Phase.PLAN: PhaseConfig(
        phase=Phase.PLAN,
        model="sonnet",
        max_turns=6,
        timeout_sec=600,
        max_retries=1,
        budget_usd=1.5,
        allowed_tools=[
            "Read",
            "Glob",
            "Grep",
            "Write",
            "Bash(git log:*)",
            "Bash(git diff HEAD:*)",
        ],
        required_output="plan/execution-plan.json",
        system_prompt_template="plan_system.j2",
    ),
    Phase.EXECUTE: PhaseConfig(
        phase=Phase.EXECUTE,
        model="sonnet",
        max_turns=12,
        timeout_sec=1800,
        max_retries=2,
        budget_usd=3.0,
        allowed_tools=[
            "Read",
            "Glob",
            "Grep",
            "Write",
            "Edit",
            "Bash(npm run:*)",
            "Bash(python src/:*)",
            "Bash(pytest:*)",
            "Bash(git diff HEAD:*)",
        ],
        required_output=None,  # 直接修改源码
        system_prompt_template="execute_system.j2",
    ),
    Phase.QC: PhaseConfig(
        phase=Phase.QC,
        model="haiku",
        max_turns=4,
        timeout_sec=600,
        max_retries=0,
        budget_usd=0.5,
        permission_mode="plan",  # 只读审查
        allowed_tools=[
            "Read",
            "Glob",
            "Grep",
            "Write",
            "Bash(git diff HEAD:*)",
            "Bash(pytest:*)",
        ],
        required_output="qc/qc-result.json",
        system_prompt_template="qc_system.j2",
    ),
    Phase.PROPOSE_SOP: PhaseConfig(
        phase=Phase.PROPOSE_SOP,
        model="haiku",
        max_turns=4,
        timeout_sec=600,
        max_retries=0,
        budget_usd=0.5,
        allowed_tools=["Read", "Glob", "Grep", "Write"],
        required_output="sop/proposal.json",
        system_prompt_template="sop_system.j2",
    ),
}


# ─── 状态机引擎 ────────────────────────────────────────────


class TransitionError(Exception):
    """非法状态转移。"""


class MaxRetriesExceeded(Exception):
    """超过最大重试次数。"""


class StateMachine:
    """线性 pipeline + 有限回退的状态机。

    职责（TCB 三件事，第3轮）：
    1. 读取当前状态
    2. 根据 Outcome 决定下一步
    3. 执行状态转移
    """

    def __init__(
        self,
        initial_phase: Phase = Phase.RESEARCH,
        phase_configs: dict[Phase, PhaseConfig] | None = None,
    ) -> None:
        self._phase = initial_phase
        self._configs = phase_configs or DEFAULT_PHASE_CONFIGS
        self._retry_counts: dict[Phase, int] = {}
        self._history: list[tuple[Phase, Outcome, Phase]] = []

    @property
    def phase(self) -> Phase:
        return self._phase

    @property
    def history(self) -> list[tuple[Phase, Outcome, Phase]]:
        return list(self._history)

    def current_config(self) -> PhaseConfig:
        """获取当前阶段的配置。"""
        return self._configs[self._phase]

    def transition(self, outcome: Outcome) -> Phase:
        """根据 outcome 计算下一阶段并执行转移。

        Raises:
            TransitionError: 非法转移（终态无法继续）
            MaxRetriesExceeded: 重试次数耗尽
        """
        if self._phase.is_terminal:
            raise TransitionError(
                f"Cannot transition from terminal phase {self._phase.value}"
            )

        phase_transitions = _TRANSITIONS.get(self._phase)
        if not phase_transitions or outcome not in phase_transitions:
            raise TransitionError(
                f"No transition defined: {self._phase.value} + {outcome.value}"
            )

        next_phase = phase_transitions[outcome]

        # 检查重试次数（回退到自身时）
        if next_phase == self._phase and outcome != Outcome.SUCCESS:
            retries = self._retry_counts.get(self._phase, 0)
            max_retries = self._configs[self._phase].max_retries
            if retries >= max_retries:
                raise MaxRetriesExceeded(
                    f"Phase {self._phase.value}: {retries}/{max_retries} retries exceeded"
                )
            self._retry_counts[self._phase] = retries + 1

        # 成功前进时重置该阶段的重试计数
        if outcome == Outcome.SUCCESS:
            self._retry_counts[self._phase] = 0

        self._history.append((self._phase, outcome, next_phase))
        self._phase = next_phase
        return next_phase

    def get_retry_count(self, phase: Phase | None = None) -> int:
        """获取指定阶段的重试次数。"""
        return self._retry_counts.get(phase or self._phase, 0)

    def reset(self) -> None:
        """重置状态机到初始状态。"""
        self._phase = Phase.RESEARCH
        self._retry_counts.clear()
        self._history.clear()

    def to_dict(self) -> dict:
        """序列化状态（用于 WAL / 持久化）。"""
        return {
            "phase": self._phase.value,
            "retry_counts": {k.value: v for k, v in self._retry_counts.items()},
            "history": [
                {"from": f.value, "outcome": o.value, "to": t.value}
                for f, o, t in self._history
            ],
        }

    @classmethod
    def from_dict(
        cls, data: dict, phase_configs: dict[Phase, PhaseConfig] | None = None
    ) -> StateMachine:
        """从序列化数据恢复。"""
        sm = cls(
            initial_phase=Phase(data["phase"]),
            phase_configs=phase_configs,
        )
        sm._retry_counts = {
            Phase(k): v for k, v in data.get("retry_counts", {}).items()
        }
        sm._history = [
            (Phase(h["from"]), Outcome(h["outcome"]), Phase(h["to"]))
            for h in data.get("history", [])
        ]
        return sm
