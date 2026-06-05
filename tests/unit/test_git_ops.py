"""GitOps 测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hermes.utils.git_ops import GitError, GitOps


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """创建一个 Git 仓库。"""
    subprocess.run(
        ["git", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    # 创建初始提交
    (tmp_path / "README.md").write_text("# Test", encoding="utf-8")
    subprocess.run(
        ["git", "add", "."],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    return tmp_path


class TestGitOps:
    """GitOps 测试。"""

    def test_create_branch(self, git_repo: Path):
        """创建新分支。"""
        git = GitOps(git_repo)
        git.create_branch("test-branch")

        # 验证分支已创建
        result = subprocess.run(
            ["git", "branch", "--list", "test-branch"],
            cwd=git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "test-branch" in result.stdout

    def test_create_branch_already_exists(self, git_repo: Path):
        """创建已存在的分支应该失败。"""
        git = GitOps(git_repo)
        git.create_branch("test-branch")

        with pytest.raises(GitError, match="Failed to create branch"):
            git.create_branch("test-branch")

    def test_rollback(self, git_repo: Path):
        """回滚工作区修改。"""
        git = GitOps(git_repo)

        # 修改已跟踪的文件（README.md 在 fixture 中已提交）
        (git_repo / "README.md").write_text("modified content", encoding="utf-8")

        # 回滚
        git.rollback()

        # 验证修改已撤销
        content = (git_repo / "README.md").read_text(encoding="utf-8")
        assert content == "# Test"

    def test_get_diff(self, git_repo: Path):
        """获取 Git diff。"""
        git = GitOps(git_repo)

        # 创建修改
        (git_repo / "test.txt").write_text("new file", encoding="utf-8")
        subprocess.run(
            ["git", "add", "test.txt"],
            cwd=git_repo,
            capture_output=True,
            check=True,
        )

        diff = git.get_diff()
        assert "new file" in diff

    def test_get_diff_empty(self, git_repo: Path):
        """获取空 diff。"""
        git = GitOps(git_repo)
        diff = git.get_diff()
        assert diff == ""

    def test_get_changed_files(self, git_repo: Path):
        """获取变更文件列表。"""
        git = GitOps(git_repo)

        # 创建修改
        (git_repo / "test1.txt").write_text("file 1", encoding="utf-8")
        (git_repo / "test2.txt").write_text("file 2", encoding="utf-8")
        subprocess.run(
            ["git", "add", "."],
            cwd=git_repo,
            capture_output=True,
            check=True,
        )

        files = git.get_changed_files()
        assert "test1.txt" in files
        assert "test2.txt" in files

    def test_get_changed_files_empty(self, git_repo: Path):
        """获取空变更文件列表。"""
        git = GitOps(git_repo)
        files = git.get_changed_files()
        assert files == []

    def test_invalid_git_repo(self, tmp_path: Path):
        """无效的 Git 仓库应该抛出异常。"""
        git = GitOps(tmp_path)

        with pytest.raises(GitError):
            git.get_diff()
