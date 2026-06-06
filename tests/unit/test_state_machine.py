"""State Machine 单元测试。"""

from __future__ import annotations

import pytest

from hermes.orchestrator.state_machine import (
    DEFAULT_PHASE_CONFIGS,
    MaxRetriesExceeded,
    Outcome,
    Phase,
    StateMachine,
    TransitionError,
)


class TestPhase:
    def test_terminal_phases(self):
        assert Phase.DONE.is_terminal
        assert Phase.ESCALATE.is_terminal
        assert not Phase.RESEARCH.is_terminal
        assert not Phase.EXECUTE.is_terminal

    def test_executable_phases(self):
        assert Phase.RESEARCH.is_executable
        assert Phase.PLAN.is_executable
        assert Phase.EXECUTE.is_executable
        assert Phase.QC.is_executable
        assert not Phase.DONE.is_executable
        assert not Phase.ESCALATE.is_executable


class TestStateMachineTransitions:
    """测试合法状态转移。"""

    def test_happy_path(self):
        """正常流程：RESEARCH → PLAN → EXECUTE → QC → DONE"""
        sm = StateMachine()
        assert sm.phase == Phase.RESEARCH

        sm.transition(Outcome.SUCCESS)
        assert sm.phase == Phase.PLAN

        sm.transition(Outcome.SUCCESS)
        assert sm.phase == Phase.EXECUTE

        sm.transition(Outcome.SUCCESS)
        assert sm.phase == Phase.QC

        sm.transition(Outcome.SUCCESS)
        assert sm.phase == Phase.DONE

    def test_research_soft_fail_retries(self):
        """RESEARCH 失败可重试自身。"""
        sm = StateMachine()
        sm.transition(Outcome.SOFT_FAIL)
        assert sm.phase == Phase.RESEARCH  # 重试
        assert sm.get_retry_count() == 1

    def test_research_hard_fail_skips_to_plan(self):
        """RESEARCH 硬失败跳到 PLAN。"""
        sm = StateMachine()
        sm.transition(Outcome.HARD_FAIL)
        assert sm.phase == Phase.PLAN

    def test_qc_soft_fail_goes_to_plan(self):
        """QC 不过回退到 PLAN。"""
        sm = StateMachine()
        sm.transition(Outcome.SUCCESS)  # → PLAN
        sm.transition(Outcome.SUCCESS)  # → EXECUTE
        sm.transition(Outcome.SUCCESS)  # → QC
        sm.transition(Outcome.SOFT_FAIL)  # → PLAN (回退)
        assert sm.phase == Phase.PLAN

    def test_qc_hard_fail_goes_to_sop(self):
        """QC 硬失败到 PROPOSE_SOP。"""
        sm = StateMachine()
        sm.transition(Outcome.SUCCESS)  # → PLAN
        sm.transition(Outcome.SUCCESS)  # → EXECUTE
        sm.transition(Outcome.SUCCESS)  # → QC
        sm.transition(Outcome.HARD_FAIL)  # → PROPOSE_SOP
        assert sm.phase == Phase.PROPOSE_SOP

    def test_propose_sop_goes_to_escalate(self):
        sm = StateMachine()
        sm.transition(Outcome.SUCCESS)  # → PLAN
        sm.transition(Outcome.SUCCESS)  # → EXECUTE
        sm.transition(Outcome.SUCCESS)  # → QC
        sm.transition(Outcome.HARD_FAIL)  # → PROPOSE_SOP
        sm.transition(Outcome.SUCCESS)  # → ESCALATE
        assert sm.phase == Phase.ESCALATE

    def test_network_error_always_escalates(self):
        """NETWORK_ERROR 应该像 HARD_FAIL 一样转移（但可区分）。"""
        for phase in [Phase.RESEARCH, Phase.PLAN, Phase.EXECUTE, Phase.QC]:
            sm = StateMachine(initial_phase=phase)
            sm.transition(Outcome.NETWORK_ERROR)
            # RESEARCH skips to PLAN, others escalate
            if phase == Phase.RESEARCH:
                assert sm.phase == Phase.PLAN
            elif phase == Phase.QC:
                assert sm.phase == Phase.PROPOSE_SOP
            else:
                assert sm.phase == Phase.ESCALATE

    def test_refused_always_escalates(self):
        """任何阶段的 REFUSED 都进入 ESCALATE。"""
        for phase in [Phase.RESEARCH, Phase.PLAN, Phase.EXECUTE, Phase.QC]:
            sm = StateMachine(initial_phase=phase)
            sm.transition(Outcome.REFUSED)
            assert sm.phase == Phase.ESCALATE


class TestStateMachineRetries:
    """测试重试限制。"""

    def test_max_retries_exceeded(self):
        """超过最大重试次数抛出异常。"""
        sm = StateMachine()
        # RESEARCH max_retries = 1
        sm.transition(Outcome.SOFT_FAIL)  # retry 1
        with pytest.raises(MaxRetriesExceeded):
            sm.transition(Outcome.SOFT_FAIL)  # retry 2 → exceeded

    def test_success_resets_retry_count(self):
        """成功后重置重试计数。"""
        sm = StateMachine()
        sm.transition(Outcome.SOFT_FAIL)  # retry 1
        assert sm.get_retry_count(Phase.RESEARCH) == 1
        sm.transition(Outcome.SUCCESS)  # → PLAN, reset RESEARCH retries
        assert sm.get_retry_count(Phase.RESEARCH) == 0

    def test_terminal_phase_cannot_transition(self):
        """终态无法继续转移。"""
        sm = StateMachine(initial_phase=Phase.DONE)
        with pytest.raises(TransitionError):
            sm.transition(Outcome.SUCCESS)

    def test_illegal_transition(self):
        """未定义的转移抛出 TransitionError。"""
        # 构造一个不可能的转移
        sm = StateMachine()
        # RESEARCH + SUCCESS = PLAN，这是合法的
        # 但让我们测试自定义的非法情况
        # 先让 RESEARCH 成功到 PLAN
        sm.transition(Outcome.SUCCESS)
        assert sm.phase == Phase.PLAN
        # PLAN 没有 REFUSED... 等等，我们加了 REFUSED → ESCALATE
        # 所以 PLAN + REFUSED = ESCALATE（合法）
        # 测试从终态
        sm2 = StateMachine(initial_phase=Phase.ESCALATE)
        with pytest.raises(TransitionError):
            sm2.transition(Outcome.SUCCESS)


class TestStateMachineSerialization:
    """测试序列化 / 反序列化。"""

    def test_to_dict_and_back(self):
        sm = StateMachine()
        sm.transition(Outcome.SUCCESS)  # → PLAN
        sm.transition(Outcome.SUCCESS)  # → EXECUTE

        data = sm.to_dict()
        assert data["phase"] == "execute"
        assert len(data["history"]) == 2

        sm2 = StateMachine.from_dict(data)
        assert sm2.phase == Phase.EXECUTE
        assert len(sm2.history) == 2

    def test_retry_counts_preserved(self):
        sm = StateMachine()
        sm.transition(Outcome.SOFT_FAIL)  # retry 1
        sm.transition(Outcome.SUCCESS)    # → PLAN
        sm.transition(Outcome.SOFT_FAIL)  # retry 1 for PLAN

        data = sm.to_dict()
        sm2 = StateMachine.from_dict(data)
        assert sm2.get_retry_count(Phase.PLAN) == 1

    def test_reset(self):
        sm = StateMachine()
        sm.transition(Outcome.SUCCESS)
        sm.transition(Outcome.SUCCESS)
        sm.reset()
        assert sm.phase == Phase.RESEARCH
        assert sm.history == []


class TestPhaseConfig:
    def test_default_configs_exist(self):
        """所有可执行阶段都有默认配置。"""
        for phase in [Phase.RESEARCH, Phase.PLAN, Phase.EXECUTE, Phase.QC, Phase.PROPOSE_SOP]:
            assert phase in DEFAULT_PHASE_CONFIGS

    def test_readonly_phases(self):
        assert DEFAULT_PHASE_CONFIGS[Phase.RESEARCH].is_readonly
        assert DEFAULT_PHASE_CONFIGS[Phase.PLAN].is_readonly
        assert DEFAULT_PHASE_CONFIGS[Phase.QC].is_readonly
        assert not DEFAULT_PHASE_CONFIGS[Phase.EXECUTE].is_readonly
