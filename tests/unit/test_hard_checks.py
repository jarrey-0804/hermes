"""Tests for HardChecks module."""

from pathlib import Path

import pytest

from hermes.qc.hard_checks import (
    CheckResult,
    HardCheckReport,
    HardChecks,
)


class TestCheckResult:
    """Test CheckResult dataclass."""

    def test_create_check_result(self):
        result = CheckResult(name="test", passed=True, details="OK")
        assert result.name == "test"
        assert result.passed is True
        assert result.details == "OK"
        assert result.severity == "low"

    def test_check_result_with_severity(self):
        result = CheckResult(name="test", passed=False, details="Failed", severity="critical")
        assert result.severity == "critical"


class TestHardCheckReport:
    """Test HardCheckReport dataclass."""

    def test_empty_report(self):
        report = HardCheckReport()
        assert report.passed is True
        assert report.total_issues == 0
        assert len(report.checks) == 0

    def test_add_passing_check(self):
        report = HardCheckReport()
        report.add(CheckResult(name="test", passed=True))
        assert report.passed is True
        assert report.total_issues == 0
        assert len(report.checks) == 1

    def test_add_failing_check(self):
        report = HardCheckReport()
        report.add(CheckResult(name="test", passed=False))
        assert report.passed is False
        assert report.total_issues == 1
        assert len(report.checks) == 1

    def test_add_multiple_checks(self):
        report = HardCheckReport()
        report.add(CheckResult(name="test1", passed=True))
        report.add(CheckResult(name="test2", passed=False))
        report.add(CheckResult(name="test3", passed=True))
        assert report.passed is False
        assert report.total_issues == 1
        assert len(report.checks) == 3

    def test_summary(self):
        report = HardCheckReport()
        report.add(CheckResult(name="test1", passed=True))
        report.add(CheckResult(name="test2", passed=False))
        summary = report.summary()
        assert "1 passed" in summary
        assert "1 failed" in summary
        assert "1 issues" in summary


class TestHardChecks:
    """Test HardChecks class."""

    def test_init(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path)
        assert checks._project_dir == tmp_path
        assert checks._max_diff_lines == 500
        assert checks._protected == []
        assert checks._exclude == []

    def test_init_with_custom_params(self, tmp_path: Path):
        checks = HardChecks(
            project_dir=tmp_path,
            max_diff_lines=1000,
            protected_files=["*.key", "*.pem"],
            exclude_patterns=["test/**", "*.log"],
        )
        assert checks._max_diff_lines == 1000
        assert checks._protected == ["*.key", "*.pem"]
        assert checks._exclude == ["test/**", "*.log"]

    def test_check_diff_size_empty(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path)
        result = checks.check_diff_size("")
        assert result.passed is True
        assert result.name == "diff_size"
        assert "No diff" in result.details

    def test_check_diff_size_within_limit(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path, max_diff_lines=100)
        diff = "\n".join([f"line {i}" for i in range(50)])
        result = checks.check_diff_size(diff)
        assert result.passed is True
        assert "50 lines" in result.details

    def test_check_diff_size_exceeds_limit(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path, max_diff_lines=100)
        diff = "\n".join([f"line {i}" for i in range(150)])
        result = checks.check_diff_size(diff)
        assert result.passed is False
        assert result.severity == "medium"
        assert "150 lines" in result.details
        assert "max: 100" in result.details

    def test_check_todo_fixme_no_issues(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path)
        # Create a clean file
        test_file = tmp_path / "clean.py"
        test_file.write_text("def hello():\n    return 'world'\n")
        result = checks.check_todo_fixme(["clean.py"])
        assert result.passed is True
        assert result.name == "todo_fixme"

    def test_check_todo_fixme_with_todo(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path)
        test_file = tmp_path / "todo.py"
        test_file.write_text("def hello():\n    # TODO: implement this\n    pass\n")
        result = checks.check_todo_fixme(["todo.py"])
        assert result.passed is False
        assert result.severity == "low"
        assert "TODO" in result.details or "FIXME" in result.details

    def test_check_todo_fixme_with_fixme(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path)
        test_file = tmp_path / "fixme.py"
        test_file.write_text("def hello():\n    # FIXME: broken\n    pass\n")
        result = checks.check_todo_fixme(["fixme.py"])
        assert result.passed is False

    def test_check_todo_fixme_excludes_pattern(self, tmp_path: Path):
        checks = HardChecks(
            project_dir=tmp_path,
            exclude_patterns=["test/**"],
        )
        test_dir = tmp_path / "test"
        test_dir.mkdir()
        test_file = test_dir / "todo.py"
        test_file.write_text("# TODO: this should be excluded\n")
        result = checks.check_todo_fixme(["test/todo.py"])
        assert result.passed is True

    def test_check_secrets_no_issues(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path)
        test_file = tmp_path / "clean.py"
        test_file.write_text("api_key = 'example_key'\n")
        result = checks.check_secrets(["clean.py"])
        assert result.passed is True
        assert result.name == "secrets"

    def test_check_secrets_aws_key(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path)
        test_file = tmp_path / "secret.py"
        test_file.write_text("aws_key = 'AKIAIOSFODNN7EXAMPLE'\n")
        result = checks.check_secrets(["secret.py"])
        assert result.passed is False
        assert result.severity == "critical"
        assert "AWS Access Key" in result.details

    def test_check_secrets_password(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path)
        test_file = tmp_path / "secret.py"
        test_file.write_text("password = 'supersecretpassword123'\n")
        result = checks.check_secrets(["secret.py"])
        assert result.passed is False
        assert "Password" in result.details

    def test_check_secrets_private_key(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path)
        test_file = tmp_path / "secret.py"
        test_file.write_text("-----BEGIN RSA PRIVATE KEY-----\n")
        result = checks.check_secrets(["secret.py"])
        assert result.passed is False
        assert "Private Key" in result.details

    def test_check_binary_files_no_issues(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path)
        result = checks.check_binary_files(["clean.py", "readme.md"])
        assert result.passed is True
        assert result.name == "binary_files"

    def test_check_binary_files_with_exe(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path)
        result = checks.check_binary_files(["program.exe"])
        assert result.passed is False
        assert result.severity == "medium"
        assert "Binary files" in result.details

    def test_check_binary_files_with_image(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path)
        result = checks.check_binary_files(["image.png", "photo.jpg"])
        assert result.passed is False
        assert "image.png" in result.details

    def test_check_protected_files_no_issues(self, tmp_path: Path):
        checks = HardChecks(
            project_dir=tmp_path,
            protected_files=["*.key", "*.pem"],
        )
        result = checks.check_protected_files(["clean.py"])
        assert result.passed is True
        assert result.name == "protected_files"

    def test_check_protected_files_with_key(self, tmp_path: Path):
        checks = HardChecks(
            project_dir=tmp_path,
            protected_files=["*.key", "*.pem"],
        )
        result = checks.check_protected_files(["private.key"])
        assert result.passed is False
        assert result.severity == "high"
        assert "Protected files" in result.details

    def test_check_file_sizes_no_issues(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path)
        test_file = tmp_path / "small.py"
        test_file.write_text("x = 1\n")
        result = checks.check_file_sizes(["small.py"])
        assert result.passed is True
        assert result.name == "file_sizes"

    def test_check_file_sizes_oversized(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path)
        test_file = tmp_path / "large.py"
        # Create a 2MB file
        test_file.write_text("x" * (2 * 1024 * 1024))
        result = checks.check_file_sizes(["large.py"], max_size_kb=1024)
        assert result.passed is False
        assert result.severity == "medium"
        assert "Oversized" in result.details

    def test_check_file_sizes_nonexistent(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path)
        result = checks.check_file_sizes(["nonexistent.py"])
        assert result.passed is True

    def test_is_excluded(self, tmp_path: Path):
        checks = HardChecks(
            project_dir=tmp_path,
            exclude_patterns=["test/**", "*.log", "node_modules/**"],
        )
        assert checks._is_excluded("test/file.py") is True
        assert checks._is_excluded("debug.log") is True
        assert checks._is_excluded("node_modules/package.json") is True
        assert checks._is_excluded("src/main.py") is False
        assert checks._is_excluded("README.md") is False

    def test_run_all_clean_project(self, tmp_path: Path):
        checks = HardChecks(project_dir=tmp_path)
        test_file = tmp_path / "clean.py"
        test_file.write_text("def hello():\n    return 'world'\n")
        report = checks.run_all(
            diff_text="line1\nline2\n",
            changed_files=["clean.py"],
        )
        assert report.passed is True
        assert len(report.checks) == 6

    def test_run_all_with_issues(self, tmp_path: Path):
        checks = HardChecks(
            project_dir=tmp_path,
            max_diff_lines=10,
            protected_files=["*.key"],
        )
        # Create files with issues
        todo_file = tmp_path / "todo.py"
        todo_file.write_text("# TODO: fix this\n")
        secret_file = tmp_path / "secret.py"
        secret_file.write_text("AKIAIOSFODNN7EXAMPLE\n")

        report = checks.run_all(
            diff_text="\n".join([f"line {i}" for i in range(20)]),
            changed_files=["todo.py", "secret.py", "private.key"],
        )
        assert report.passed is False
        assert report.total_issues >= 3  # diff size, todo, secrets, protected file
