"""Tests for CLI module."""

import json
from pathlib import Path

from typer.testing import CliRunner

from hermes.cli import _find_latest_state, _print_task_status, app

runner = CliRunner()


class TestVersionCommand:
    """Test version command."""

    def test_version_output(self):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "hermes" in result.stdout
        assert "0.1.0" in result.stdout


class TestDoctorCommand:
    """Test doctor command."""

    def test_doctor_runs(self):
        result = runner.invoke(app, ["doctor"])
        # May exit with code 1 if some checks fail, but should not crash
        assert result.exit_code in [0, 1]
        assert "Hermes Doctor" in result.stdout

    def test_doctor_checks_python(self):
        result = runner.invoke(app, ["doctor"])
        assert "Python" in result.stdout

    def test_doctor_checks_packages(self):
        result = runner.invoke(app, ["doctor"])
        assert "Package:" in result.stdout


class TestConfigShowCommand:
    """Test config show command."""

    def test_config_show_default(self, tmp_path: Path):
        config_path = str(tmp_path / "nonexistent.yaml")
        result = runner.invoke(app, ["config", "show", "--config", config_path])
        assert result.exit_code == 0
        # Should show default config as JSON
        assert "general" in result.stdout or "project_dir" in result.stdout

    def test_config_show_custom(self, tmp_path: Path):
        config_file = tmp_path / "hermes.yaml"
        config_file.write_text(
            "general:\n  project_dir: /custom/path\n  data_dir: /custom/runs\n"
        )
        result = runner.invoke(app, ["config", "show", "--config", str(config_file)])
        assert result.exit_code == 0


class TestConfigValidateCommand:
    """Test config validate command."""

    def test_config_validate_valid(self, tmp_path: Path):
        config_file = tmp_path / "hermes.yaml"
        config_file.write_text(
            "general:\n  project_dir: /tmp\n  data_dir: /tmp/runs\n"
        )
        result = runner.invoke(app, ["config", "validate", "--config", str(config_file)])
        assert result.exit_code == 0
        assert "valid" in result.stdout.lower()

    def test_config_validate_missing_file(self, tmp_path: Path):
        result = runner.invoke(
            app,
            ["config", "validate", "--config", str(tmp_path / "missing.yaml")]
        )
        # Should succeed with defaults (logs warning)
        assert result.exit_code == 0


class TestStatusCommand:
    """Test status command."""

    def test_status_no_tasks(self, tmp_path: Path):
        result = runner.invoke(app, ["status", "--data-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "No task found" in result.stdout

    def test_status_with_task(self, tmp_path: Path):
        # Create a mock state file
        run_dir = tmp_path / "test-run-123"
        run_dir.mkdir()
        state_file = run_dir / "orchestrator-state.json"
        state = {
            "run_id": "test-run-123",
            "task": "Test task",
            "phase": "done",
            "total_cost_usd": 0.05,
            "qc_rounds": 1,
            "duration_sec": 30.5,
            "state_machine": {
                "history": [
                    {"from": "research", "outcome": "success", "to": "plan"},
                    {"from": "plan", "outcome": "success", "to": "execute"},
                ]
            }
        }
        state_file.write_text(json.dumps(state))

        result = runner.invoke(app, ["status", "test-run-123", "--data-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "test-run-123" in result.stdout
        assert "Test task" in result.stdout

    def test_status_json_output(self, tmp_path: Path):
        run_dir = tmp_path / "test-run"
        run_dir.mkdir()
        state_file = run_dir / "orchestrator-state.json"
        state = {"run_id": "test-run", "task": "Test", "phase": "done"}
        state_file.write_text(json.dumps(state))

        result = runner.invoke(
            app, ["status", "test-run", "--data-dir", str(tmp_path), "--output", "json"]
        )
        assert result.exit_code == 0
        # Should be valid JSON
        parsed = json.loads(result.stdout)
        assert parsed["run_id"] == "test-run"


class TestLogsCommand:
    """Test logs command."""

    def test_logs_not_found(self, tmp_path: Path):
        result = runner.invoke(app, ["logs", "nonexistent", "--data-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "WAL not found" in result.stdout

    def test_logs_with_data(self, tmp_path: Path):
        from hermes.orchestrator.wal import WALEvent, WriteAheadLog

        # Create a WAL file
        run_dir = tmp_path / "test-run"
        run_dir.mkdir()
        wal_path = run_dir / "wal.jsonl"
        wal = WriteAheadLog(wal_path)
        wal.append(WALEvent.RUN_START, {"task": "test"})
        wal.append(WALEvent.PHASE_START, {"phase": "research"})
        wal.append(WALEvent.PHASE_COMPLETE, {"phase": "research"})

        result = runner.invoke(app, ["logs", "test-run", "--data-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "run_start" in result.stdout
        assert "phase_start" in result.stdout

    def test_logs_with_event_filter(self, tmp_path: Path):
        from hermes.orchestrator.wal import WALEvent, WriteAheadLog

        run_dir = tmp_path / "test-run"
        run_dir.mkdir()
        wal_path = run_dir / "wal.jsonl"
        wal = WriteAheadLog(wal_path)
        wal.append(WALEvent.RUN_START, {"task": "test"})
        wal.append(WALEvent.PHASE_START, {"phase": "research"})

        result = runner.invoke(
            app, ["logs", "test-run", "--data-dir", str(tmp_path), "--event", "run_start"]
        )
        assert result.exit_code == 0
        assert "run_start" in result.stdout
        assert "phase_start" not in result.stdout


class TestRunCommand:
    """Test run command."""

    def test_run_dry_run(self, tmp_path: Path):
        config_file = tmp_path / "hermes.yaml"
        config_file.write_text(
            "general:\n  project_dir: /tmp\n  data_dir: /tmp/runs\n"
        )
        result = runner.invoke(
            app,
            ["run", "test task", "--config", str(config_file), "--dry-run"]
        )
        assert result.exit_code == 0
        assert "Dry run complete" in result.stdout

    def test_run_invalid_config(self, tmp_path: Path):
        # Create config with invalid heartbeat settings
        config_file = tmp_path / "hermes.yaml"
        config_file.write_text(
            "heartbeat:\n  interval_sec: 30\n  timeout_sec: 10\n"
        )
        result = runner.invoke(
            app,
            ["run", "test task", "--config", str(config_file), "--dry-run"]
        )
        assert result.exit_code == 1
        # Error may be in stdout or exception
        assert "error" in result.stdout.lower() or result.exception is not None


class TestHelperFunctions:
    """Test helper functions."""

    def test_find_latest_state_no_dir(self, tmp_path: Path):
        result = _find_latest_state(tmp_path / "nonexistent")
        assert result is None

    def test_find_latest_state_empty(self, tmp_path: Path):
        result = _find_latest_state(tmp_path)
        assert result is None

    def test_find_latest_state_single(self, tmp_path: Path):
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        state_file = run_dir / "orchestrator-state.json"
        state_file.write_text("{}")

        result = _find_latest_state(tmp_path)
        assert result == state_file

    def test_find_latest_state_multiple(self, tmp_path: Path):
        # Create two runs with different mtimes
        run1 = tmp_path / "run1"
        run1.mkdir()
        state1 = run1 / "orchestrator-state.json"
        state1.write_text("{}")

        run2 = tmp_path / "run2"
        run2.mkdir()
        state2 = run2 / "orchestrator-state.json"
        state2.write_text("{}")

        # Make run2 newer
        import time
        time.sleep(0.1)
        state2.touch()

        result = _find_latest_state(tmp_path)
        assert result == state2

    def test_print_task_status(self, capsys):
        state = {
            "run_id": "test-123",
            "task": "Fix bug",
            "phase": "done",
            "total_cost_usd": 0.1,
            "qc_rounds": 2,
            "duration_sec": 45.5,
            "state_machine": {
                "history": [
                    {"from": "research", "outcome": "success", "to": "plan"},
                ]
            }
        }
        _print_task_status(state)
        captured = capsys.readouterr()
        assert "test-123" in captured.out
        assert "Fix bug" in captured.out
        assert "Phase History" in captured.out
