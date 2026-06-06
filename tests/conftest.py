"""pytest conftest — 全局 fixtures 和安全防护。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _block_real_api_calls(monkeypatch: pytest.MonkeyPatch):
    """阻止测试中意外调用真实 Claude API。

    显式设置 HERMES_ALLOW_REAL_API=1 可跳过此防护。
    """
    if os.environ.get("HERMES_ALLOW_REAL_API") == "1":
        return

    import subprocess

    original_run = subprocess.run
    original_popen = subprocess.Popen

    def blocked_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd:
            cmd_str = str(cmd[0])
            if "claude" in cmd_str:
                raise RuntimeError(
                    "Blocked: real Claude API call in test. Set HERMES_ALLOW_REAL_API=1 to allow."
                )
            # 允许 git 命令（用于 git_ops 测试）
            if "git" in cmd_str:
                return original_run(cmd, *args, **kwargs)
        return original_run(cmd, *args, **kwargs)

    class BlockedPopen:
        def __init__(self, cmd, *args, **kwargs):
            if isinstance(cmd, (list, tuple)) and cmd:
                cmd_str = str(cmd[0])
                if "claude" in cmd_str:
                    raise RuntimeError(
                        "Blocked: real Claude API call in test. "
                        "Set HERMES_ALLOW_REAL_API=1 to allow."
                    )
            self._proc = original_popen(cmd, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._proc, name)

        def __enter__(self):
            return self._proc.__enter__()

        def __exit__(self, *args):
            return self._proc.__exit__(*args)

    monkeypatch.setattr(subprocess, "run", blocked_run)
    monkeypatch.setattr(subprocess, "Popen", BlockedPopen)


@pytest.fixture
def sample_config_path() -> Path:
    """返回测试配置路径。"""
    return Path(__file__).parent.parent / "config" / "hermes.yaml"
