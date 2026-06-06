"""Tests for executor.claude_runner module."""

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from hermes.executor.claude_runner import (
    ClaudeNetworkError,
    ClaudeResult,
    ClaudeRunner,
    ClaudeRunnerError,
)


class TestClaudeResult:
    """Test ClaudeResult dataclass."""

    def test_create_result(self):
        result = ClaudeResult(success=True)
        assert result.success is True
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.exit_code == 0
        assert result.cost_usd == 0.0
        assert result.tokens_used == {}
        assert result.duration_sec == 0.0
        assert result.timed_out is False
        assert result.refused is False
        assert result.json_output is None

    def test_create_result_with_data(self):
        result = ClaudeResult(
            success=False,
            stdout="output",
            stderr="error",
            exit_code=1,
            cost_usd=0.05,
            tokens_used={"sonnet": 1000},
            duration_sec=5.5,
            timed_out=True,
            refused=False,
            json_output={"key": "value"},
        )
        assert result.success is False
        assert result.stdout == "output"
        assert result.exit_code == 1
        assert result.cost_usd == 0.05
        assert result.timed_out is True


class TestClaudeRunner:
    """Test ClaudeRunner class."""

    def test_init(self, tmp_path: Path):
        runner = ClaudeRunner(work_dir=tmp_path)
        assert runner._work_dir == tmp_path
        assert runner._env == {}
        assert runner._no_session is True

    def test_init_with_custom_env(self, tmp_path: Path):
        custom_env = {"ANTHROPIC_API_KEY": "test_key"}
        runner = ClaudeRunner(work_dir=tmp_path, env=custom_env, no_session=False)
        assert runner._env == custom_env
        assert runner._no_session is False

    def test_build_command_basic(self, tmp_path: Path):
        runner = ClaudeRunner(work_dir=tmp_path)
        cmd = runner._build_command(
            prompt="test prompt",
            system_prompt="",
            append_system_prompt="",
            allowed_tools=None,
            json_schema=None,
            max_turns=8,
            budget_usd=5.0,
            model="sonnet",
            permission_mode="default",
        )
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "test prompt" in cmd
        assert "--output-format" in cmd
        assert "json" in cmd
        assert "--no-session-persistence" in cmd

    def test_build_command_with_system_prompt(self, tmp_path: Path):
        runner = ClaudeRunner(work_dir=tmp_path)
        cmd = runner._build_command(
            prompt="test",
            system_prompt="You are helpful",
            append_system_prompt="",
            allowed_tools=None,
            json_schema=None,
            max_turns=8,
            budget_usd=5.0,
            model="sonnet",
            permission_mode="default",
        )
        assert "--system-prompt" in cmd
        assert "You are helpful" in cmd

    def test_build_command_with_allowed_tools(self, tmp_path: Path):
        runner = ClaudeRunner(work_dir=tmp_path)
        cmd = runner._build_command(
            prompt="test",
            system_prompt="",
            append_system_prompt="",
            allowed_tools=["Read", "Write", "Glob"],
            json_schema=None,
            max_turns=8,
            budget_usd=5.0,
            model="sonnet",
            permission_mode="default",
        )
        assert "--allowedTools" in cmd
        assert "Read" in cmd
        assert "Write" in cmd
        assert "Glob" in cmd

    def test_build_command_with_permission_mode(self, tmp_path: Path):
        runner = ClaudeRunner(work_dir=tmp_path)
        cmd = runner._build_command(
            prompt="test",
            system_prompt="",
            append_system_prompt="",
            allowed_tools=None,
            json_schema=None,
            max_turns=8,
            budget_usd=5.0,
            model="sonnet",
            permission_mode="plan",
        )
        assert "--permission-mode" in cmd
        assert "plan" in cmd

    def test_get_env(self, tmp_path: Path):
        runner = ClaudeRunner(work_dir=tmp_path)
        env = runner._get_env()
        assert isinstance(env, dict)
        assert "CLAUDE_CODE_ENTRY" not in env

    def test_parse_cost_with_valid_json(self, tmp_path: Path):
        runner = ClaudeRunner(work_dir=tmp_path)
        result = ClaudeResult(
            success=True,
            json_output={
                "total_cost_usd": 0.123,
                "modelUsage": {
                    "sonnet": {
                        "inputTokens": 1000,
                        "outputTokens": 500,
                    },
                    "haiku": {
                        "inputTokens": 200,
                        "outputTokens": 100,
                    },
                },
            },
        )
        runner._parse_cost(result)
        assert result.cost_usd == 0.123
        assert result.tokens_used["sonnet"] == 1500
        assert result.tokens_used["haiku"] == 300

    def test_parse_cost_without_json(self, tmp_path: Path):
        runner = ClaudeRunner(work_dir=tmp_path)
        result = ClaudeResult(success=True)
        runner._parse_cost(result)
        assert result.cost_usd == 0.0
        assert result.tokens_used == {}

    def test_detect_refusal_with_refusal(self, tmp_path: Path):
        runner = ClaudeRunner(work_dir=tmp_path)
        stdout = "I cannot help with that request"
        stderr = ""
        assert runner._detect_refusal(stdout, stderr, exit_code=1) is True

    def test_detect_refusal_with_cannot(self, tmp_path: Path):
        runner = ClaudeRunner(work_dir=tmp_path)
        stdout = "I can't do that"
        stderr = ""
        assert runner._detect_refusal(stdout, stderr, exit_code=1) is True

    def test_detect_refusal_with_unsafe(self, tmp_path: Path):
        runner = ClaudeRunner(work_dir=tmp_path)
        stdout = "This operation is unsafe"
        stderr = ""
        assert runner._detect_refusal(stdout, stderr, exit_code=1) is True

    def test_detect_refusal_no_refusal(self, tmp_path: Path):
        runner = ClaudeRunner(work_dir=tmp_path)
        stdout = "Task completed successfully"
        stderr = ""
        assert runner._detect_refusal(stdout, stderr, exit_code=0) is False

    def test_detect_refusal_exit_code_zero(self, tmp_path: Path):
        runner = ClaudeRunner(work_dir=tmp_path)
        stdout = "I cannot help"
        stderr = ""
        # Should not detect as refusal if exit code is 0
        assert runner._detect_refusal(stdout, stderr, exit_code=0) is False

    def test_try_parse_json_valid(self, tmp_path: Path):
        runner = ClaudeRunner(work_dir=tmp_path)
        text = '{"key": "value"}'
        result = runner._try_parse_json(text)
        assert result == {"key": "value"}

    def test_try_parse_json_with_text(self, tmp_path: Path):
        runner = ClaudeRunner(work_dir=tmp_path)
        text = 'Some text before {"key": "value"} and after'
        result = runner._try_parse_json(text)
        assert result == {"key": "value"}

    def test_try_parse_json_incomplete(self, tmp_path: Path):
        runner = ClaudeRunner(work_dir=tmp_path)
        text = '{"key": "value"'
        result = runner._try_parse_json(text)
        assert result == {"key": "value"}

    def test_try_parse_json_invalid(self, tmp_path: Path):
        runner = ClaudeRunner(work_dir=tmp_path)
        text = "not json at all"
        result = runner._try_parse_json(text)
        assert result is None

    @patch("subprocess.Popen")
    def test_execute_success(self, mock_popen, tmp_path: Path):
        mock_proc = Mock()
        mock_proc.communicate.return_value = ('{"result": "ok"}', "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        runner = ClaudeRunner(work_dir=tmp_path)
        result = runner._execute(["claude", "-p", "test"], timeout_sec=900)

        assert result.success is True
        assert result.exit_code == 0
        assert result.stdout == '{"result": "ok"}'
        assert result.json_output == {"result": "ok"}

    @patch("subprocess.Popen")
    def test_execute_timeout(self, mock_popen, tmp_path: Path):
        mock_proc = Mock()
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=900)
        mock_proc.terminate = Mock()
        mock_proc.kill = Mock()
        mock_proc.wait = Mock()
        mock_popen.return_value = mock_proc

        runner = ClaudeRunner(work_dir=tmp_path)
        result = runner._execute(["claude", "-p", "test"], timeout_sec=900)

        assert result.success is False
        assert result.timed_out is True
        assert result.exit_code == -1
        mock_proc.terminate.assert_called_once()

    @patch("subprocess.Popen")
    def test_execute_file_not_found(self, mock_popen, tmp_path: Path):
        mock_popen.side_effect = FileNotFoundError("claude not found")

        runner = ClaudeRunner(work_dir=tmp_path)
        with pytest.raises(ClaudeRunnerError, match="Claude CLI not found"):
            runner._execute(["claude", "-p", "test"], timeout_sec=900)

    @patch("subprocess.Popen")
    def test_execute_os_error(self, mock_popen, tmp_path: Path):
        mock_popen.side_effect = OSError("Permission denied")

        runner = ClaudeRunner(work_dir=tmp_path)
        with pytest.raises(ClaudeNetworkError, match="Failed to start Claude"):
            runner._execute(["claude", "-p", "test"], timeout_sec=900)

    @patch("subprocess.Popen")
    def test_run_success(self, mock_popen, tmp_path: Path):
        mock_proc = Mock()
        mock_proc.communicate.return_value = (
            json.dumps({"total_cost_usd": 0.05, "result": "ok"}),
            "",
        )
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        runner = ClaudeRunner(work_dir=tmp_path)
        result = runner.run(prompt="test prompt")

        assert result.success is True
        assert result.cost_usd == 0.05
        assert result.duration_sec > 0

    @patch("subprocess.Popen")
    def test_run_with_retry_success_first_attempt(self, mock_popen, tmp_path: Path):
        mock_proc = Mock()
        mock_proc.communicate.return_value = ('{"ok": true}', "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        runner = ClaudeRunner(work_dir=tmp_path)
        result = runner.run_with_retry(prompt="test", max_network_retries=3)

        assert result.success is True
        assert mock_popen.call_count == 1

    @patch("subprocess.Popen")
    @patch("time.sleep")
    def test_run_with_retry_success_after_retry(self, mock_sleep, mock_popen, tmp_path: Path):
        # First call raises network error, second succeeds
        mock_proc_fail = Mock()
        mock_proc_fail.communicate.side_effect = OSError("Network error")

        mock_proc_success = Mock()
        mock_proc_success.communicate.return_value = ('{"ok": true}', "")
        mock_proc_success.returncode = 0

        mock_popen.side_effect = [OSError("Network error"), mock_proc_success]

        runner = ClaudeRunner(work_dir=tmp_path)
        result = runner.run_with_retry(prompt="test", max_network_retries=3, backoff_base=0.01)

        assert result.success is True
        assert mock_popen.call_count == 2
        assert mock_sleep.call_count == 1

    @patch("subprocess.Popen")
    @patch("time.sleep")
    def test_run_with_retry_all_fail(self, mock_sleep, mock_popen, tmp_path: Path):
        mock_popen.side_effect = OSError("Network error")

        runner = ClaudeRunner(work_dir=tmp_path)
        result = runner.run_with_retry(prompt="test", max_network_retries=2, backoff_base=0.01)

        assert result.success is False
        assert "Network retries exhausted" in result.stderr
        assert mock_popen.call_count == 3  # Initial + 2 retries
        assert mock_sleep.call_count == 2
