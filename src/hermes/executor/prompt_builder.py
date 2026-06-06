"""Prompt 构建器。

将 Prompt 构建逻辑从 Orchestrator 中提取出来（F1.1 TCB 瘦身）。
使用 Jinja2 模板引擎渲染 system prompt 和 user prompt。
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from hermes.executor.context_bridge import ContextBridge
from hermes.observability.logger import get_logger
from hermes.orchestrator.state_machine import Phase, PhaseConfig


class PromptBuilderError(Exception):
    """Prompt 构建错误。"""


class PromptBuilder:
    """Prompt 构建器。

    负责根据阶段和配置生成 system prompt 和 user prompt。

    Usage:
        builder = PromptBuilder(templates_dir)
        system_prompt = builder.build_system_prompt(phase, config, task_dir)
        user_prompt = builder.build_user_prompt(phase, config, task_desc, context)
    """

    def __init__(self, templates_dir: Path) -> None:
        self._templates_dir = templates_dir
        self._log = get_logger("prompt_builder")
        self._jinja: Environment | None = None

    def _get_jinja(self) -> Environment:
        """懒加载 Jinja2 环境。"""
        if self._jinja is None:
            self._jinja = Environment(
                loader=FileSystemLoader(str(self._templates_dir)),
                autoescape=False,
            )
        return self._jinja

    def build_system_prompt(
        self, phase: Phase, config: PhaseConfig, task_dir: Path
    ) -> str:
        """构建 system prompt。

        Args:
            phase: 当前阶段
            config: 阶段配置
            task_dir: 任务目录

        Returns:
            渲染后的 system prompt

        Raises:
            PromptBuilderError: 模板加载或渲染失败
        """
        template_name = f"{phase.value}_system.j2"

        try:
            jinja = self._get_jinja()
            template = jinja.get_template(template_name)
            return template.render(
                task_dir=str(task_dir),
                timeout_sec=config.timeout_sec,
                max_turns=config.max_turns,
                model=config.model,
            )
        except Exception as e:
            self._log.warn(
                "template_render_failed",
                template=template_name,
                error=str(e),
            )
            # 回退到默认 system prompt
            return f"You are Hermes {phase.value.upper()} agent."

    def build_user_prompt(
        self,
        phase: Phase,
        config: PhaseConfig,
        task_description: str,
        context_bridge: ContextBridge,
        task_dir: Path,
        extra_context: str = "",
    ) -> str:
        """构建 user prompt。

        Args:
            phase: 当前阶段
            config: 阶段配置
            task_description: 任务描述
            context_bridge: 上下文桥接器
            task_dir: 任务目录
            extra_context: 额外上下文（如 QC 反馈）

        Returns:
            完整的 user prompt
        """
        parts: list[str] = []

        # 任务描述
        parts.append(f"## Task\n{task_description}")

        # 上下文（上一阶段产出）
        prev_phase = self._get_previous_phase(phase)
        if prev_phase:
            context = context_bridge.build_context(
                from_phase=prev_phase.value,
                to_phase=phase.value,
            )
            parts.append(context)

        # 额外上下文（QC 反馈等）
        if extra_context:
            parts.append(extra_context)

        # 输出指令
        if config.required_output:
            output_path = task_dir / config.required_output
            parts.append(f"## Output\nWrite your output to: {output_path}")

        return "\n\n".join(parts)

    def _get_previous_phase(self, phase: Phase) -> Phase | None:
        """获取上一阶段。"""
        order = [Phase.RESEARCH, Phase.PLAN, Phase.EXECUTE, Phase.QC]
        try:
            idx = order.index(phase)
            return order[idx - 1] if idx > 0 else None
        except ValueError:
            return None
