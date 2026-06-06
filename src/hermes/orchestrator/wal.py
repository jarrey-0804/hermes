"""WAL — Write-Ahead Log（预写日志）。

设计参考：第5轮（WAL + 崩溃恢复）+ 第9轮（HMAC 增强）。
- JSONL 格式，每行一条记录
- fsync 保证 crash-safe
- replay 恢复崩溃前状态
- compaction 防止无限增长
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WALEntry:
    """WAL 单条记录。"""

    seq: int  # 单调递增序列号
    event: str  # 事件类型
    data: dict[str, Any]  # 事件数据
    ts: float  # Unix 时间戳
    prev_hash: str  # 前一条记录的 hash（链式完整性）
    entry_hash: str = ""  # 本条记录的 hash

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, line: str) -> WALEntry:
        d = json.loads(line)
        return cls(**d)

    def compute_hash(self, secret: str = "") -> str:
        """计算本条记录的 HMAC hash。"""
        payload = (
            f"{self.seq}:{self.event}:{json.dumps(self.data, sort_keys=True)}"
            f":{self.ts}:{self.prev_hash}:{secret}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ─── WAL 事件类型 ──────────────────────────────────────────

class WALEvent:
    """标准事件名称。"""

    RUN_START = "run_start"
    RUN_COMPLETE = "run_complete"
    RUN_FAILED = "run_failed"
    PHASE_START = "phase_start"
    PHASE_COMPLETE = "phase_complete"
    PHASE_RETRY = "phase_retry"
    PHASE_TIMEOUT = "phase_timeout"
    PHASE_ERROR = "phase_error"
    QC_RESULT = "qc_result"
    STATE_SAVE = "state_save"
    STATE_RESTORE = "state_restore"
    BUDGET_RESERVE = "budget_reserve"
    BUDGET_SETTLE = "budget_settle"
    ARTIFACT_WRITTEN = "artifact_written"
    HEARTBEAT = "heartbeat"
    NETWORK_RETRY = "network_retry"
    ROLLBACK = "rollback"


class WALError(Exception):
    """WAL 操作错误。"""


class WALCorruptionError(WALError):
    """WAL 完整性校验失败。"""


class WriteAheadLog:
    """Write-Ahead Log 实现。

    Usage:
        wal = WriteAheadLog(Path("runs/abc123/wal.jsonl"))
        wal.append(WALEvent.PHASE_START, {"phase": "research"})
        entries = wal.replay()
    """

    MAX_ENTRIES = 1000  # 硬性上限（第11轮：WAL 无限增长问题）
    MAX_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB

    def __init__(
        self,
        path: Path,
        secret: str = "",
        auto_compact: bool = True,
    ) -> None:
        self._path = path
        self._secret = secret
        self._auto_compact = auto_compact
        self._seq = 0
        self._last_hash = "genesis"

        # 确保目录存在
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # 如果文件已存在，加载最新状态
        if self._path.exists():
            self._load_tail()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def seq(self) -> int:
        return self._seq

    def append(self, event: str, data: dict[str, Any] | None = None) -> WALEntry:
        """追加一条 WAL 记录（fsync 保证持久化）。

        Raises:
            WALError: 写入失败
        """
        entry = WALEntry(
            seq=self._seq,
            event=event,
            data=data or {},
            ts=time.time(),
            prev_hash=self._last_hash,
        )
        # 计算 hash
        entry = WALEntry(
            seq=entry.seq,
            event=entry.event,
            data=entry.data,
            ts=entry.ts,
            prev_hash=entry.prev_hash,
            entry_hash=entry.compute_hash(self._secret),
        )

        self._write_entry(entry)

        self._seq += 1
        self._last_hash = entry.entry_hash

        # 自动压缩
        if self._auto_compact and self._seq >= self.MAX_ENTRIES:
            self.compact()

        return entry

    def replay(self) -> list[WALEntry]:
        """重放所有 WAL 记录，验证完整性。

        Raises:
            WALCorruptionError: hash 链断裂
        """
        entries: list[WALEntry] = []
        expected_hash = "genesis"

        if not self._path.exists():
            return entries

        with open(self._path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = WALEntry.from_json(line)
                except (json.JSONDecodeError, TypeError) as e:
                    raise WALCorruptionError(
                        f"Line {line_no}: invalid JSON: {e}"
                    ) from e

                # 验证 hash 链
                if entry.prev_hash != expected_hash:
                    raise WALCorruptionError(
                        f"Line {line_no}: hash chain broken. "
                        f"Expected prev_hash={expected_hash}, got {entry.prev_hash}"
                    )

                # 验证本条 hash
                computed = entry.compute_hash(self._secret)
                if computed != entry.entry_hash:
                    raise WALCorruptionError(
                        f"Line {line_no}: entry hash mismatch. "
                        f"Expected {computed}, got {entry.entry_hash}"
                    )

                expected_hash = entry.entry_hash
                entries.append(entry)

        return entries

    def compact(self) -> int:
        """压缩 WAL：保留最新 20% 的记录，写入 compacted 文件。

        Returns:
            被移除的记录数
        """
        entries = self.replay()
        if len(entries) < 100:
            return 0

        # 保留最新 20%
        keep_count = max(50, len(entries) // 5)
        removed = len(entries) - keep_count
        kept = entries[-keep_count:]

        # 重建 hash 链（从 genesis 开始）
        rehashed: list[WALEntry] = []
        prev_hash = "genesis"
        for entry in kept:
            new_entry = WALEntry(
                seq=entry.seq,
                event=entry.event,
                data=entry.data,
                ts=entry.ts,
                prev_hash=prev_hash,
            )
            new_entry = WALEntry(
                seq=new_entry.seq,
                event=new_entry.event,
                data=new_entry.data,
                ts=new_entry.ts,
                prev_hash=new_entry.prev_hash,
                entry_hash=new_entry.compute_hash(self._secret),
            )
            rehashed.append(new_entry)
            prev_hash = new_entry.entry_hash

        # 写入压缩文件
        compact_path = self._path.with_suffix(".compacted.jsonl")
        with open(compact_path, "w", encoding="utf-8") as f:
            for entry in rehashed:
                f.write(entry.to_json() + "\n")
            f.flush()
            os.fsync(f.fileno())

        # 原子替换
        compact_path.replace(self._path)

        # 重建索引
        self._load_tail()
        return removed

    def get_events(self, event_type: str) -> list[WALEntry]:
        """获取指定类型的所有事件。"""
        return [e for e in self.replay() if e.event == event_type]

    def get_last_event(self, event_type: str) -> WALEntry | None:
        """获取指定类型的最后一个事件。"""
        events = self.get_events(event_type)
        return events[-1] if events else None

    def _write_entry(self, entry: WALEntry) -> None:
        """原子写入单条记录（append + fsync）。"""
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(entry.to_json() + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            raise WALError(f"Failed to write WAL entry: {e}") from e

    def _load_tail(self) -> None:
        """从现有 WAL 文件加载最后一条记录的状态。"""
        entries = self.replay()
        if entries:
            last = entries[-1]
            self._seq = last.seq + 1
            self._last_hash = last.entry_hash
        else:
            self._seq = 0
            self._last_hash = "genesis"

    def clear(self) -> None:
        """清空 WAL（仅用于测试）。"""
        if self._path.exists():
            self._path.unlink()
        self._seq = 0
        self._last_hash = "genesis"
