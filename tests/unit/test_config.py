"""Config 验证器测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes.utils.config import ConfigValidator, HermesConfig


@pytest.fixture
def valid_config_path(tmp_path: Path) -> Path:
    """创建一个有效的配置文件。"""
    config_path = tmp_path / "hermes.yaml"
    config_path.write_text(
        """
general:
  project_dir: /tmp/test
  data_dir: /tmp/runs
  log_level: INFO
  log_format: json

model:
  default: sonnet
  research: haiku
  plan: sonnet
  execute: sonnet
  qc: haiku

stages:
  research:
    timeout_sec: 900
    max_turns: 8
    max_retries: 1
    budget_usd: 1.0
    model: haiku
    permission_mode: default
    allowed_tools:
      - Read
      - Glob
      - Grep
      - Write
    required_output: research/findings.json

  plan:
    timeout_sec: 600
    max_turns: 6
    max_retries: 1
    budget_usd: 1.5
    model: sonnet
    permission_mode: default
    allowed_tools:
      - Read
      - Glob
      - Grep
      - Write
    required_output: plan/execution-plan.json

  execute:
    timeout_sec: 1800
    max_turns: 12
    max_retries: 2
    budget_usd: 3.0
    model: sonnet
    permission_mode: default
    allowed_tools:
      - Read
      - Glob
      - Grep
      - Write
      - Edit

  qc:
    timeout_sec: 600
    max_turns: 4
    max_retries: 0
    budget_usd: 0.5
    model: haiku
    permission_mode: plan
    allowed_tools:
      - Read
      - Glob
      - Grep
    required_output: qc/qc-result.json

qc_rules:
  max_diff_lines: 500
  protected_files:
    - .env
    - .git/config
  exclude_patterns:
    - node_modules/**

budget:
  max_per_task_usd: 5.0
  max_daily_usd: 50.0
  alert_threshold_pct: 80
  cost_model: anthropic

docker:
  enabled: false
  image: hermes:latest
  memory: 2g
  cpus: 1
  pids_limit: 100
  network: restricted
  read_only: true

security:
  webfetch_whitelist: []
  forbidden_git_ops:
    - checkout
    - add
    - commit
    - reset
  max_file_size_kb: 1024

sop:
  enabled: false
  ttl_days: 7
  approval_sla_hours: 48
  auto_expire: true

heartbeat:
  interval_sec: 10
  timeout_sec: 30

queue:
  max_concurrent: 1
  priorities:
    - SYSTEM
    - URGENT
    - NORMAL
    - LOW

notifications:
  enabled: false
  slack_webhook_env: SLACK_WEBHOOK_URL

dashboard:
  enabled: false
  port: 8080
  refresh_sec: 30

git:
  branch_prefix: auto/
  pre_receive_hook: false
  auto_commit: false
""",
        encoding="utf-8",
    )
    return config_path


class TestConfigLoad:
    """配置加载测试。"""

    def test_load_valid_config(self, valid_config_path: Path):
        """加载有效的配置文件。"""
        config = HermesConfig.load(valid_config_path)
        assert config.general.project_dir == "/tmp/test"
        assert config.budget.max_per_task_usd == 5.0

    def test_load_missing_config_warns(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """F5.3 修复验证：配置文件不存在时记录警告。"""
        missing_path = tmp_path / "nonexistent.yaml"
        config = HermesConfig.load(missing_path)

        # 应该返回默认配置
        assert config.general.project_dir == "/workspace"

    def test_load_empty_config(self, tmp_path: Path):
        """加载空配置文件。"""
        config_path = tmp_path / "empty.yaml"
        config_path.write_text("", encoding="utf-8")
        config = HermesConfig.load(config_path)

        # 应该返回默认配置
        assert config.general.project_dir == "/workspace"


class TestConfigValidator:
    """配置验证器测试。"""

    def test_validate_valid_config(self, valid_config_path: Path):
        """验证有效的配置。"""
        config = HermesConfig.load(valid_config_path)
        validator = ConfigValidator(config)
        result = validator.validate()

        assert result.is_valid
        assert len(result.errors) == 0

    def test_forbidden_git_ops_regex(self, tmp_path: Path):
        """F5.1 修复验证：Git 操作解析器使用 regex 正确提取操作名。"""
        config_path = tmp_path / "bad_git.yaml"
        config_path.write_text(
            """
stages:
  research:
    allowed_tools:
      - "Bash(git checkout:main)"
      - "Bash(git add:.)"
      - "Bash(git commit -m test)"

security:
  forbidden_git_ops:
    - checkout
    - add
""",
            encoding="utf-8",
        )

        config = HermesConfig.load(config_path)
        validator = ConfigValidator(config)
        result = validator.validate()

        # 应该检测到 forbidden git ops
        assert not result.is_valid
        error_messages = [e for e in result.errors if "forbidden git op" in e]
        assert len(error_messages) >= 1

        # 验证所有 forbidden ops 都被检测到
        error_text = " ".join(error_messages)
        assert "checkout" in error_text
        assert "add" in error_text

    def test_heartbeat_timeout_must_exceed_interval(self, tmp_path: Path):
        """心跳超时应该大于心跳间隔。"""
        config_path = tmp_path / "bad_heartbeat.yaml"
        config_path.write_text(
            """
heartbeat:
  interval_sec: 20
  timeout_sec: 15
""",
            encoding="utf-8",
        )

        config = HermesConfig.load(config_path)
        validator = ConfigValidator(config)
        result = validator.validate()

        assert not result.is_valid
        assert any("heartbeat" in e.lower() for e in result.errors)

    def test_daily_budget_must_exceed_per_task(self, tmp_path: Path):
        """每日预算应该大于等于单任务预算。"""
        config_path = tmp_path / "bad_budget.yaml"
        config_path.write_text(
            """
budget:
  max_per_task_usd: 50.0
  max_daily_usd: 10.0
""",
            encoding="utf-8",
        )

        config = HermesConfig.load(config_path)
        validator = ConfigValidator(config)
        result = validator.validate()

        assert not result.is_valid
        assert any("max_daily_usd" in e for e in result.errors)
