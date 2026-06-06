"""PathGuard — 路径安全检查 + Prompt Injection 扫描。

设计参考：第9轮（STRIDE 威胁模型）+ 第13轮（prompt injection scanner）。
- 阻止 Claude 读写受保护路径
- 检测仓库文件中的 Prompt Injection 模式
- 输出内容扫描（防信息泄露）
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GuardResult:
    """路径检查结果。"""

    allowed: bool
    reason: str = ""
    path: str = ""


@dataclass
class InjectionMatch:
    """注入检测结果。"""

    pattern_name: str
    matched_text: str
    severity: str  # critical | high | medium | low
    line_number: int = 0


class PathGuard:
    """文件系统路径守卫。

    检查 Claude 的文件操作是否在允许范围内。
    """

    # 默认可疑路径模式
    SUSPICIOUS_PATTERNS = [
        "**/.env",
        "**/.env.*",
        "**/credentials*",
        "**/*.pem",
        "**/*.key",
        "**/*.p12",
        "**/*.pfx",
        "**/id_rsa",
        "**/id_ed25519",
        "**/.ssh/**",
        "**/.aws/**",
        "**/.kube/**",
        "**/secrets*",
        "**/password*",
        # Git 内部文件——防止 hook 注入 / remote 篡改 / 配置篡改
        "**/.git/hooks/**",
        "**/.git/config",
        "**/.git/HEAD",
        "**/.git/refs/**",
        "**/.git/objects/**",
    ]

    # 系统级保护路径
    SYSTEM_PATHS = [
        "/etc/**",
        "/usr/**",
        "/var/**",
        "/root/**",
        "/proc/**",
        "/sys/**",
    ]

    def __init__(
        self,
        project_dir: Path,
        protected_files: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
    ) -> None:
        self._project_dir = project_dir.resolve()
        self._protected = protected_files or self.SUSPICIOUS_PATTERNS
        self._exclude = exclude_patterns or []

    def check_read(self, file_path: str | Path) -> GuardResult:
        """检查读取操作是否允许。"""
        return self._check(file_path, operation="read")

    def check_write(self, file_path: str | Path) -> GuardResult:
        """检查写入操作是否允许。"""
        return self._check(file_path, operation="write")

    def _check(self, file_path: str | Path, operation: str) -> GuardResult:
        """通用路径检查。"""
        path = Path(file_path)

        # 解析为绝对路径
        if not path.is_absolute():
            path = (self._project_dir / path).resolve()
        else:
            path = path.resolve()

        path_str = str(path)

        # 检查系统路径
        for pattern in self.SYSTEM_PATHS:
            if fnmatch.fnmatch(path_str, pattern):
                return GuardResult(
                    allowed=False,
                    reason=f"System path blocked: {pattern}",
                    path=path_str,
                )

        # 检查项目外路径
        try:
            path.relative_to(self._project_dir)
        except ValueError:
            return GuardResult(
                allowed=False,
                reason=f"Path outside project directory: {path_str}",
                path=path_str,
            )

        # 检查受保护文件
        rel_path = str(path.relative_to(self._project_dir))
        for pattern in self._protected:
            if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(path_str, pattern):
                return GuardResult(
                    allowed=False,
                    reason=f"Protected file: {pattern}",
                    path=rel_path,
                )

        # 检查排除模式
        for pattern in self._exclude:
            if fnmatch.fnmatch(rel_path, pattern):
                return GuardResult(
                    allowed=False,
                    reason=f"Excluded pattern: {pattern}",
                    path=rel_path,
                )

        return GuardResult(allowed=True, path=rel_path)


# ─── Prompt Injection Scanner ──────────────────────────────


# 已知注入模式（第9轮 + 第13轮）
INJECTION_PATTERNS: list[tuple[str, str, str]] = [
    # (pattern_name, regex, severity)
    (
        "role_override",
        r"(?i)(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)",
        "critical",
    ),
    (
        "system_prompt_leak",
        r"(?i)(show|reveal|print|output)\s+(me\s+)?(your|the)\s+(system\s+prompt|instructions?|rules?)",
        "high",
    ),
    (
        "jailbreak_attempt",
        r"(?i)(you\s+are\s+now|act\s+as|pretend\s+you\s+are|DAN\s+mode|developer\s+mode)",
        "critical",
    ),
    (
        "tool_abuse",
        r"(?i)(execute|run|eval)\s*(this\s+)?(shell|bash|cmd|command)\s*[:\-]",
        "high",
    ),
    (
        "data_exfil",
        r"(?i)(curl|wget|fetch)\s+https?://[^\s]+\s*.*?(POST|PUT|data=|@)",
        "high",
    ),
    (
        "hidden_instruction",
        r"(?i)\[INST\]|\[SYSTEM\]|<\|im_start\|>|<\|im_end\|>|<<SYS>>",
        "critical",
    ),
    (
        "credential_request",
        r"(?i)(show|give|print|display)\s+(me\s+)?(api[_\s]?key|password|secret|token|credentials?)",
        "high",
    ),
    (
        "escape_sandbox",
        r"(?i)(\/proc\/self|\/dev\/fd|proc\/\d+|chmod\s+[47]|sudo\s+)",
        "critical",
    ),
]


class InjectionScanner:
    """Prompt Injection 检测器。

    扫描文本内容（仓库文件、web 内容等）中的注入模式。
    """

    def __init__(
        self,
        custom_patterns: list[tuple[str, str, str]] | None = None,
    ) -> None:
        self._patterns = INJECTION_PATTERNS + (custom_patterns or [])
        self._compiled = [
            (name, re.compile(pattern), severity)
            for name, pattern, severity in self._patterns
        ]

    def scan(self, text: str) -> list[InjectionMatch]:
        """扫描文本中的注入模式。

        Returns:
            匹配列表，空列表表示安全
        """
        matches: list[InjectionMatch] = []

        for line_no, line in enumerate(text.split("\n"), 1):
            for name, pattern, severity in self._compiled:
                m = pattern.search(line)
                if m:
                    matches.append(
                        InjectionMatch(
                            pattern_name=name,
                            matched_text=m.group()[:200],
                            severity=severity,
                            line_number=line_no,
                        )
                    )

        return matches

    def scan_file(self, file_path: Path) -> list[InjectionMatch]:
        """扫描文件内容。"""
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
            return self.scan(text)
        except (OSError, PermissionError):
            return []

    def is_safe(self, text: str, max_severity: str = "high") -> bool:
        """快速检查文本是否安全（无超过 max_severity 的匹配）。

        Args:
            text: 要检查的文本
            max_severity: 允许的最高严重度（"low", "medium", "high", "critical"）
                         例如 max_severity="high" 表示允许 low/medium/high，
                         但 critical 会被认为不安全

        Returns:
            True if all matches have severity <= max_severity
        """
        severity_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        threshold = severity_order.get(max_severity, 2)
        matches = self.scan(text)
        return all(
            severity_order.get(m.severity, 0) <= threshold for m in matches
        )
