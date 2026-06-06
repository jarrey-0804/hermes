"""Hard Checks — 确定性质检脚本。

设计参考：第4轮（QC 硬检脚本）+ 第7轮（检查项扩展）。
- diff 大小限制
- TODO/FIXME 标记扫描
- Secrets 检测
- 二进制文件检测
- 受保护文件检查
- 配置驱动（hermes.qc.yaml）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from hermes.observability.logger import get_logger


@dataclass
class CheckResult:
    """单项检查结果。"""

    name: str
    passed: bool
    details: str = ""
    severity: str = "low"  # low | medium | high | critical


@dataclass
class HardCheckReport:
    """硬检总报告。"""

    passed: bool = True
    checks: list[CheckResult] = field(default_factory=list)
    total_issues: int = 0

    def add(self, result: CheckResult) -> None:
        self.checks.append(result)
        if not result.passed:
            self.passed = False
            self.total_issues += 1

    def summary(self) -> str:
        passed = sum(1 for c in self.checks if c.passed)
        failed = len(self.checks) - passed
        return f"Hard checks: {passed} passed, {failed} failed, {self.total_issues} issues"


# ─── Secrets 检测模式 ──────────────────────────────────────

_SECRET_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key"),
    (r'(?:password|passwd|pwd)\s*[=:]\s*["\']?[^\s"\']{8,}', "Password in code"),
    (r'(?:api[_-]?key|apikey)\s*[=:]\s*["\']?[^\s"\']{16,}', "API Key"),
    (r'(?:secret|token)\s*[=:]\s*["\']?[^\s"\']{16,}', "Secret/Token"),
    (r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", "Private Key"),
    (r"ghp_[0-9a-zA-Z]{36}", "GitHub Personal Token"),
    (r"sk-[0-9a-zA-Z]{48}", "OpenAI API Key"),
    (r"xox[bpsar]-[0-9a-zA-Z-]{10,}", "Slack Token"),
]


class HardChecks:
    """确定性质检执行器。

    Usage:
        checks = HardChecks(project_dir=Path("/workspace"))
        report = checks.run_all(diff_text=diff, changed_files=files)
    """

    def __init__(
        self,
        project_dir: Path,
        max_diff_lines: int = 500,
        protected_files: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
    ) -> None:
        self._project_dir = project_dir
        self._max_diff_lines = max_diff_lines
        self._protected = protected_files or []
        self._exclude = exclude_patterns or []
        self._log = get_logger("hard_checks")

    def run_all(
        self,
        diff_text: str = "",
        changed_files: list[str] | None = None,
    ) -> HardCheckReport:
        """执行所有硬检。"""
        report = HardCheckReport()
        changed = changed_files or []

        report.add(self.check_diff_size(diff_text))
        report.add(self.check_todo_fixme(changed))
        report.add(self.check_secrets(changed))
        report.add(self.check_binary_files(changed))
        report.add(self.check_protected_files(changed))
        report.add(self.check_file_sizes(changed))

        self._log.info(
            "hard_checks_complete",
            passed=report.passed,
            total_issues=report.total_issues,
            checks=len(report.checks),
        )

        return report

    def check_diff_size(self, diff_text: str) -> CheckResult:
        """检查 diff 行数是否超限。"""
        if not diff_text:
            return CheckResult(name="diff_size", passed=True, details="No diff")

        lines = diff_text.strip().split("\n")
        count = len(lines)

        if count > self._max_diff_lines:
            return CheckResult(
                name="diff_size",
                passed=False,
                details=f"Diff has {count} lines (max: {self._max_diff_lines})",
                severity="medium",
            )
        return CheckResult(
            name="diff_size",
            passed=True,
            details=f"Diff: {count} lines",
        )

    def check_todo_fixme(self, changed_files: list[str]) -> CheckResult:
        """扫描新增的 TODO/FIXME 标记。"""
        todos_found: list[str] = []
        pattern = re.compile(r"\b(TODO|FIXME|HACK|XXX|BUG)\b", re.IGNORECASE)

        for file_path in changed_files:
            full_path = self._project_dir / file_path
            if not full_path.exists() or not full_path.is_file():
                continue
            if self._is_excluded(file_path):
                continue
            try:
                text = full_path.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(text.split("\n"), 1):
                    if pattern.search(line):
                        todos_found.append(f"{file_path}:{i}")
                        if len(todos_found) >= 20:
                            break
            except (OSError, PermissionError):
                continue

        if todos_found:
            markers = ", ".join(todos_found[:5])
            return CheckResult(
                name="todo_fixme",
                passed=False,
                details=f"Found {len(todos_found)} TODO/FIXME markers: {markers}",
                severity="low",
            )
        return CheckResult(name="todo_fixme", passed=True)

    def check_secrets(self, changed_files: list[str]) -> CheckResult:
        """检测硬编码的 secrets。"""
        secrets_found: list[str] = []
        compiled = [(re.compile(p), name) for p, name in _SECRET_PATTERNS]

        for file_path in changed_files:
            full_path = self._project_dir / file_path
            if not full_path.exists() or not full_path.is_file():
                continue
            if self._is_excluded(file_path):
                continue
            try:
                text = full_path.read_text(encoding="utf-8", errors="replace")
                for pattern, name in compiled:
                    if pattern.search(text):
                        secrets_found.append(f"{file_path}: {name}")
            except (OSError, PermissionError):
                continue

        if secrets_found:
            return CheckResult(
                name="secrets",
                passed=False,
                details=f"Potential secrets found: {'; '.join(secrets_found[:5])}",
                severity="critical",
            )
        return CheckResult(name="secrets", passed=True)

    def check_binary_files(self, changed_files: list[str]) -> CheckResult:
        """检测新增的二进制文件。"""
        binary_extensions = {
            ".exe",
            ".dll",
            ".so",
            ".dylib",
            ".a",
            ".o",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".bmp",
            ".ico",
            ".zip",
            ".tar",
            ".gz",
            ".bz2",
            ".xz",
            ".pdf",
            ".doc",
            ".docx",
            ".pyc",
            ".pyo",
            ".class",
        }
        binaries: list[str] = []

        for file_path in changed_files:
            ext = Path(file_path).suffix.lower()
            if ext in binary_extensions:
                binaries.append(file_path)

        if binaries:
            return CheckResult(
                name="binary_files",
                passed=False,
                details=f"Binary files detected: {', '.join(binaries[:5])}",
                severity="medium",
            )
        return CheckResult(name="binary_files", passed=True)

    def check_protected_files(self, changed_files: list[str]) -> CheckResult:
        """检查受保护文件是否被修改。"""
        import fnmatch

        modified_protected: list[str] = []
        for file_path in changed_files:
            for pattern in self._protected:
                if fnmatch.fnmatch(file_path, pattern):
                    modified_protected.append(file_path)
                    break

        if modified_protected:
            return CheckResult(
                name="protected_files",
                passed=False,
                details=f"Protected files modified: {', '.join(modified_protected)}",
                severity="high",
            )
        return CheckResult(name="protected_files", passed=True)

    def check_file_sizes(self, changed_files: list[str], max_size_kb: int = 1024) -> CheckResult:
        """检查文件大小是否超限。"""
        oversized: list[str] = []

        for file_path in changed_files:
            full_path = self._project_dir / file_path
            if not full_path.exists():
                continue
            size_kb = full_path.stat().st_size / 1024
            if size_kb > max_size_kb:
                oversized.append(f"{file_path} ({size_kb:.0f}KB)")

        if oversized:
            return CheckResult(
                name="file_sizes",
                passed=False,
                details=f"Oversized files: {', '.join(oversized[:5])}",
                severity="medium",
            )
        return CheckResult(name="file_sizes", passed=True)

    def _is_excluded(self, file_path: str) -> bool:
        """检查文件是否在排除列表中。"""
        import fnmatch

        return any(fnmatch.fnmatch(file_path, p) for p in self._exclude)
