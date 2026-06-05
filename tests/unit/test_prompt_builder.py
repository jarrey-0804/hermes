"""PromptBuilder 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes.executor.context_bridge import ContextBridge
from hermes.executor.prompt_builder import PromptBuilder
from hermes.orchestrator.state_machine import Phase, PhaseConfig


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    """创建 prompt 模板目录。"""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    # 创建 RESEARCH 模板
    (prompts_dir / "research_system.j2").write_text(
        """You are a research agent.
Task directory: {{ task_dir }}
Timeout: {{ timeout_sec }} seconds
Model: {{ model }}""",
        encoding="utf-8",
    )

    # 创建 PLAN 模板
    (prompts_dir / "plan_system.j2").write_text(
        "You are a planning agent.",
        encoding="utf-8",
    )

    return prompts_dir


@pytest.fixture
def builder(prompts_dir: Path) -> PromptBuilder:
    """创建 PromptBuilder。"""
    return PromptBuilder(prompts_dir)


class TestPromptBuilder:
    """PromptBuilder 测试。"""

    def test_build_system_prompt_research(self, builder: PromptBuilder, tmp_path: Path):
        """构建 RESEARCH 阶段的 system prompt。"""
        config = PhaseConfig(
            phase=Phase.RESEARCH,
            model="haiku",
            timeout_sec=900,
            max_turns=8,
        )

        prompt = builder.build_system_prompt(
            phase=Phase.RESEARCH,
            config=config,
            task_dir=tmp_path,
        )

        assert "research agent" in prompt
        assert str(tmp_path) in prompt
        assert "900" in prompt
        assert "haiku" in prompt

    def test_build_system_prompt_missing_template(self, builder: PromptBuilder, tmp_path: Path):
        """模板不存在时使用默认 prompt。"""
        config = PhaseConfig(
            phase=Phase.EXECUTE,
            model="sonnet",
        )

        prompt = builder.build_system_prompt(
            phase=Phase.EXECUTE,
            config=config,
            task_dir=tmp_path,
        )

        assert "EXECUTE" in prompt
        assert "Hermes" in prompt

    def test_build_user_prompt(self, builder: PromptBuilder, tmp_path: Path):
        """构建 user prompt。"""
        config = PhaseConfig(
            phase=Phase.RESEARCH,
            required_output="research/findings.json",
        )

        bridge = ContextBridge(tmp_path)

        prompt = builder.build_user_prompt(
            phase=Phase.RESEARCH,
            config=config,
            task_description="Fix the login bug",
            context_bridge=bridge,
            task_dir=tmp_path,
        )

        assert "Fix the login bug" in prompt
        assert "research/findings.json" in prompt

    def test_build_user_prompt_with_extra_context(self, builder: PromptBuilder, tmp_path: Path):
        """构建带额外上下文的 user prompt。"""
        config = PhaseConfig(phase=Phase.EXECUTE)
        bridge = ContextBridge(tmp_path)

        prompt = builder.build_user_prompt(
            phase=Phase.EXECUTE,
            config=config,
            task_description="Implement feature X",
            context_bridge=bridge,
            task_dir=tmp_path,
            extra_context="## QC Feedback\nFix the test failures",
        )

        assert "Implement feature X" in prompt
        assert "QC Feedback" in prompt
        assert "test failures" in prompt
