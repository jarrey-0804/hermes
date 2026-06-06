"""WAL 单元测试。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from hermes.orchestrator.wal import (
    WALCorruptionError,
    WALEntry,
    WALEvent,
    WriteAheadLog,
)


@pytest.fixture
def wal_path(tmp_path: Path) -> Path:
    return tmp_path / "test_wal.jsonl"


@pytest.fixture
def wal(wal_path: Path) -> WriteAheadLog:
    return WriteAheadLog(wal_path)


class TestWALEntry:
    def test_to_json_and_back(self):
        entry = WALEntry(
            seq=0,
            event="test",
            data={"key": "value"},
            ts=time.time(),
            prev_hash="genesis",
            entry_hash="abc123",
        )
        json_str = entry.to_json()
        parsed = WALEntry.from_json(json_str)
        assert parsed.seq == 0
        assert parsed.event == "test"
        assert parsed.data == {"key": "value"}

    def test_compute_hash_deterministic(self):
        entry = WALEntry(
            seq=0,
            event="test",
            data={"key": "value"},
            ts=1000.0,
            prev_hash="genesis",
        )
        h1 = entry.compute_hash("secret")
        h2 = entry.compute_hash("secret")
        assert h1 == h2
        assert len(h1) == 16

    def test_hash_changes_with_secret(self):
        entry = WALEntry(
            seq=0,
            event="test",
            data={},
            ts=1000.0,
            prev_hash="genesis",
        )
        h1 = entry.compute_hash("secret1")
        h2 = entry.compute_hash("secret2")
        assert h1 != h2


class TestWriteAheadLog:
    def test_append_and_replay(self, wal: WriteAheadLog):
        wal.append(WALEvent.PHASE_START, {"phase": "research"})
        wal.append(WALEvent.PHASE_COMPLETE, {"phase": "research"})

        entries = wal.replay()
        assert len(entries) == 2
        assert entries[0].event == WALEvent.PHASE_START
        assert entries[1].event == WALEvent.PHASE_COMPLETE

    def test_seq_increments(self, wal: WriteAheadLog):
        wal.append("event1", {})
        wal.append("event2", {})
        wal.append("event3", {})

        entries = wal.replay()
        assert [e.seq for e in entries] == [0, 1, 2]
        assert wal.seq == 3

    def test_hash_chain_integrity(self, wal: WriteAheadLog):
        wal.append("e1", {"a": 1})
        wal.append("e2", {"b": 2})
        wal.append("e3", {"c": 3})

        entries = wal.replay()
        assert entries[0].prev_hash == "genesis"
        assert entries[1].prev_hash == entries[0].entry_hash
        assert entries[2].prev_hash == entries[1].entry_hash

    def test_replay_detects_tampering(self, wal_path: Path):
        wal = WriteAheadLog(wal_path)
        wal.append("e1", {"a": 1})
        wal.append("e2", {"b": 2})

        # 篡改第二条记录
        lines = wal_path.read_text().strip().split("\n")
        entry = json.loads(lines[1])
        entry["data"]["b"] = 999  # 篡改数据
        lines[1] = json.dumps(entry)
        wal_path.write_text("\n".join(lines) + "\n")

        with pytest.raises(WALCorruptionError, match="hash"):
            WriteAheadLog(wal_path).replay()

    def test_replay_detects_broken_chain(self, wal_path: Path):
        wal = WriteAheadLog(wal_path)
        wal.append("e1", {})
        wal.append("e2", {})
        wal.append("e3", {})

        # 删除第二条，破坏链
        lines = wal_path.read_text().strip().split("\n")
        del lines[1]
        wal_path.write_text("\n".join(lines) + "\n")

        with pytest.raises(WALCorruptionError, match="hash chain"):
            WriteAheadLog(wal_path).replay()

    def test_persistence_across_instances(self, wal_path: Path):
        wal1 = WriteAheadLog(wal_path)
        wal1.append("e1", {"x": 1})
        wal1.append("e2", {"x": 2})

        # 新实例应该能读取之前的记录
        wal2 = WriteAheadLog(wal_path)
        entries = wal2.replay()
        assert len(entries) == 2
        assert wal2.seq == 2

    def test_get_events(self, wal: WriteAheadLog):
        wal.append(WALEvent.PHASE_START, {"phase": "research"})
        wal.append(WALEvent.PHASE_COMPLETE, {"phase": "research"})
        wal.append(WALEvent.PHASE_START, {"phase": "plan"})

        starts = wal.get_events(WALEvent.PHASE_START)
        assert len(starts) == 2

        last_start = wal.get_last_event(WALEvent.PHASE_START)
        assert last_start is not None
        assert last_start.data["phase"] == "plan"

    def test_get_events_empty(self, wal: WriteAheadLog):
        assert wal.get_events("nonexistent") == []
        assert wal.get_last_event("nonexistent") is None

    def test_clear(self, wal: WriteAheadLog):
        wal.append("e1", {})
        wal.append("e2", {})
        wal.clear()

        assert wal.seq == 0
        assert wal.replay() == []

    def test_compact(self, wal_path: Path):
        wal = WriteAheadLog(wal_path, auto_compact=False)
        for i in range(200):
            wal.append(f"event_{i}", {"i": i})

        assert wal.seq == 200

        removed = wal.compact()
        assert removed > 0

        entries = wal.replay()
        assert len(entries) < 200
        assert len(entries) >= 50  # 至少保留 50 条

    def test_fsync_on_write(self, wal_path: Path):
        """验证写入后数据立即可读（fsync 效果）。"""
        wal = WriteAheadLog(wal_path)
        wal.append("critical", {"data": "important"})

        # 立即用新实例读取
        wal2 = WriteAheadLog(wal_path)
        entries = wal2.replay()
        assert len(entries) == 1
        assert entries[0].data["data"] == "important"
