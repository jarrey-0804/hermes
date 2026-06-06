"""ClaudeRunner — Claude Code CLI 调用器。

设计参考：第6轮（Claude CLI 实测验证）+ 第7轮（Prompt 工程）。
- 构建 claude -p 命令（--allowedTools, --system-prompt, --json-schema 等）
- 超时管理 + 网络重试
- 输出解析（JSON / text）
- 成本追踪
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hermes.observability.logger import get_logger


@dataclass
class ClaudeResult:
    """Claude CLI 执行结果。"""

    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    cost_usd: float = 0.0
    tokens_used: dict[str, int] = field(default_factory=dict)
    duration_sec: float = 0.0
    timed_out: bool = False
    refused: bool = False
    json_output: dict[str, Any] | None = None


class ClaudeRunnerError(Exception):
    """ClaudeRunner 错误。"""


class ClaudeTimeoutError(ClaudeRunnerError):
    """超时。"""


class ClaudeNetworkError(ClaudeRunnerError):
    """网络错误。"""


class ClaudeRefusedError(ClaudeRunnerError):
    """Claude 拒绝执行。"""


class ClaudeRunner:
    """Claude Code CLI 调用封装。

    Usage:
        runner = ClaudeRunner(work_dir=Path("/workspace"))
        result = runner.run(
            prompt="Analyze this codebase...",
            system_prompt="You are a code analyst...",
            allowed_tools=["Read", "Glob", "Grep"],
            timeout_sec=900,
            budget_usd=2.0,
        )
    """

    CLAUDE_BIN = "claude"

    def __init__(
        self,
        work_dir: Path,
        env: dict[str, str] | None = None,
        no_session: bool = True,
    ) -> None:
        self._work_dir = work_dir
        self._env = env or {}
        self._no_session = no_session
        self._log = get_logger("claude_runner")

    def run(
        self,
        prompt: str,
        system_prompt: str = "",
        append_system_prompt: str = "",
        allowed_tools: list[str] | None = None,
        json_schema: dict | None = None,
        max_turns: int = 8,
        timeout_sec: int = 900,
        budget_usd: float = 5.0,
        model: str = "sonnet",
        permission_mode: str = "default",
    ) -> ClaudeResult:
        """执行单次 Claude CLI 调用。

        Args:
            prompt: 用户 prompt
            system_prompt: 系统 prompt 覆盖
            append_system_prompt: 追加到系统 prompt
            allowed_tools: 工具白名单
            json_schema: 输出 JSON Schema
            max_turns: 最大对话轮次
            timeout_sec: 超时秒数
            budget_usd: 成本上限
            model: 模型名称
            permission_mode: 权限模式
        """
        cmd = self._build_command(
            prompt=prompt,
            system_prompt=system_prompt,
            append_system_prompt=append_system_prompt,
            allowed_tools=allowed_tools,
            json_schema=json_schema,
            max_turns=max_turns,
            budget_usd=budget_usd,
            model=model,
            permission_mode=permission_mode,
        )

        self._log.info(
            "claude_invoke",
            model=model,
            max_turns=max_turns,
            timeout_sec=timeout_sec,
            budget_usd=budget_usd,
            tools_count=len(allowed_tools or []),
        )

        start = time.time()
        result = self._execute(cmd, timeout_sec)
        result.duration_sec = time.time() - start

        # 解析成本
        self._parse_cost(result)

        self._log.info(
            "claude_complete",
            success=result.success,
            exit_code=result.exit_code,
            cost_usd=result.cost_usd,
            duration_sec=round(result.duration_sec, 1),
            timed_out=result.timed_out,
        )

        return result

    def run_with_retry(
        self,
        prompt: str,
        max_network_retries: int = 3,
        backoff_base: float = 10.0,
        **kwargs: Any,
    ) -> ClaudeResult:
        """带网络重试的 Claude 调用。

        只对网络/环境错误重试，逻辑错误不重试。
        """
        last_error: Exception | None = None

        for attempt in range(max_network_retries + 1):
            try:
                return self.run(prompt=prompt, **kwargs)
            except ClaudeNetworkError as e:
                last_error = e
                if attempt < max_network_retries:
                    backoff = backoff_base * (2**attempt)
                    self._log.warn(
                        "network_retry",
                        attempt=attempt + 1,
                        backoff_sec=backoff,
                        error=str(e),
                    )
                    time.sleep(backoff)

        # 所有重试失败
        return ClaudeResult(
            success=False,
            stderr=f"Network retries exhausted: {last_error}",
            exit_code=-1,
        )

    def _build_command(
        self,
        prompt: str,
        system_prompt: str,
        append_system_prompt: str,
        allowed_tools: list[str] | None,
        json_schema: dict | None,
        max_turns: int,
        budget_usd: float,
        model: str,
        permission_mode: str,
    ) -> list[str]:
        """构建 claude CLI 命令。"""
        cmd = [self.CLAUDE_BIN, "-p", prompt]

        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        if append_system_prompt:
            cmd.extend(["--append-system-prompt", append_system_prompt])

        if allowed_tools:
            cmd.extend(["--allowedTools", *allowed_tools])

        if json_schema:
            cmd.extend(["--json-schema", json.dumps(json_schema)])

        if max_turns:
            cmd.extend(["--max-turns", str(max_turns)])

        if budget_usd > 0:
            cmd.extend(["--max-budget-usd", str(budget_usd)])

        if model:
            cmd.extend(["--model", model])

        if permission_mode and permission_mode != "default":
            cmd.extend(["--permission-mode", permission_mode])

        # 始终使用 JSON 输出 + 无会话持久化
        cmd.extend(["--output-format", "json"])
        if self._no_session:
            cmd.append("--no-session-persistence")

        return cmd

    def _execute(self, cmd: list[str], timeout_sec: int) -> ClaudeResult:
        """执行 CLI 命令，处理超时和进程管理。"""
        env = {**self._get_env(), **self._env}

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self._work_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError:
            raise ClaudeRunnerError(
                f"Claude CLI not found at '{self.CLAUDE_BIN}'. "
                "Install with: npm install -g @anthropic-ai/claude-code"
            )
        except OSError as e:
            raise ClaudeNetworkError(f"Failed to start Claude: {e}") from e

        try:
            stdout, stderr = proc.communicate(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            # 优雅关闭：SIGTERM → 30s grace → SIGKILL
            proc.terminate()
            try:
                proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            return ClaudeResult(
                success=False,
                exit_code=-1,
                timed_out=True,
                stderr=f"Timed out after {timeout_sec}s",
            )

        # 检查拒绝执行
        refused = self._detect_refusal(stdout, stderr, proc.returncode)

        result = ClaudeResult(
            success=proc.returncode == 0 and not refused,
            stdout=stdout or "",
            stderr=stderr or "",
            exit_code=proc.returncode,
            refused=refused,
        )

        # 尝试解析 JSON 输出
        if result.stdout:
            result.json_output = self._try_parse_json(result.stdout)

        return result

    def _get_env(self) -> dict[str, str]:
        """获取环境变量。"""
        import os

        env = dict(os.environ)
        # 确保不继承交互模式设置
        env.pop("CLAUDE_CODE_ENTRY", None)
        return env

    def _parse_cost(self, result: ClaudeResult) -> None:
        """从 JSON 输出中解析成本数据。"""
        if result.json_output:
            result.cost_usd = result.json_output.get("total_cost_usd", 0.0)
            usage = result.json_output.get("modelUsage", {})
            if usage:
                for model_name, model_data in usage.items():
                    if isinstance(model_data, dict):
                        result.tokens_used[model_name] = model_data.get(
                            "inputTokens", 0
                        ) + model_data.get("outputTokens", 0)

    def _detect_refusal(self, stdout: str, stderr: str, exit_code: int) -> bool:
        """检测 Claude 是否拒绝执行（第11轮边界案例）。"""
        refusal_markers = [
            "I cannot",
            "I can't",
            "I'm not able to",
            "I will not",
            "I won't",
            "unsafe",
            "permission denied",
        ]
        combined = (stdout + stderr).lower()
        return any(marker.lower() in combined for marker in refusal_markers) and exit_code != 0

    def _try_parse_json(self, text: str) -> dict[str, Any] | None:
        """尝试解析 JSON，三层容错（第11轮）。"""
        # Layer 1: 直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Layer 2: 提取 JSON 块
        import re

        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # Layer 3: 尝试补全括号
        open_braces = text.count("{") - text.count("}")
        if open_braces > 0:
            try:
                return json.loads(text + "}" * open_braces)
            except json.JSONDecodeError:
                pass

        return None
