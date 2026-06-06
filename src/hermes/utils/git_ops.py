"""Git 操作封装。

将 Git 操作从 Orchestrator 中提取出来（F1.1 TCB 瘦身）。
提供分支创建、回滚、diff 获取等功能。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from hermes.observability.logger import get_logger


class GitError(Exception):
    """Git 操作错误。"""


class GitOps:
    """Git 操作封装器。

    Usage:
        git = GitOps(project_dir)
        git.create_branch("auto/run-123")
        diff = git.get_diff()
        git.rollback()
    """

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = project_dir
        self._log = get_logger("git_ops")

    def create_branch(self, branch_name: str) -> None:
        """创建新分支。

        Args:
            branch_name: 分支名称（如 "auto/run-123"）

        Raises:
            GitError: Git 命令执行失败
        """
        self._log.info("creating_branch", branch=branch_name)
        try:
            result = subprocess.run(
                ["git", "checkout", "-b", branch_name],
                cwd=self._project_dir,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                raise GitError(f"Failed to create branch: {result.stderr}")
        except subprocess.TimeoutExpired:
            raise GitError("Git branch creation timed out")
        except OSError as e:
            raise GitError(f"Git command failed: {e}")

    def rollback(self) -> None:
        """回滚工作区所有修改（git checkout -- .）。

        Raises:
            GitError: Git 命令执行失败
        """
        self._log.info("rolling_back_changes")
        try:
            result = subprocess.run(
                ["git", "checkout", "--", "."],
                cwd=self._project_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                raise GitError(f"Failed to rollback: {result.stderr}")
        except subprocess.TimeoutExpired:
            raise GitError("Git rollback timed out")
        except OSError as e:
            raise GitError(f"Git rollback failed: {e}")

    def get_diff(self, base: str = "HEAD") -> str:
        """获取 Git diff。

        Args:
            base: 基准提交（默认 HEAD）

        Returns:
            diff 文本

        Raises:
            GitError: Git 命令执行失败
        """
        try:
            result = subprocess.run(
                ["git", "diff", base],
                cwd=self._project_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                raise GitError(f"Failed to get diff: {result.stderr}")
            return result.stdout
        except subprocess.TimeoutExpired:
            raise GitError("Git diff timed out")
        except OSError as e:
            raise GitError(f"Git diff failed: {e}")

    def get_changed_files(self, base: str = "HEAD") -> list[str]:
        """获取变更文件列表。

        Args:
            base: 基准提交（默认 HEAD）

        Returns:
            变更文件路径列表

        Raises:
            GitError: Git 命令执行失败
        """
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", base],
                cwd=self._project_dir,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                raise GitError(f"Failed to list changed files: {result.stderr}")
            return [f for f in result.stdout.strip().split("\n") if f]
        except subprocess.TimeoutExpired:
            raise GitError("Git changed files query timed out")
        except OSError as e:
            raise GitError(f"Git changed files query failed: {e}")
