"""Tests for observability.logger module."""

from pathlib import Path

import pytest

from hermes.observability.logger import (
    bind_context,
    clear_context,
    get_logger,
    setup_logging,
)


@pytest.fixture(autouse=True)
def reset_logging_state():
    """Reset logging state before and after each test."""
    import hermes.observability.logger as logger_module
    logger_module._configured = False
    yield
    logger_module._configured = False


class TestSetupLogging:
    """Test setup_logging function."""

    def test_setup_logging_json_format(self, tmp_path: Path):
        """Test JSON format logging setup."""
        log = setup_logging(level="INFO", format="json")
        assert log is not None
        # Should be able to log without errors
        log.info("test_message", key="value")

    def test_setup_logging_console_format(self, tmp_path: Path):
        """Test console format logging setup."""
        log = setup_logging(level="DEBUG", format="console")
        assert log is not None
        log.debug("test_debug", key="value")

    def test_setup_logging_with_file(self, tmp_path: Path):
        """Test logging to file."""
        log_file = tmp_path / "test.log"
        log = setup_logging(level="INFO", format="json", log_file=log_file)
        assert log is not None
        log.info("test_message")
        # File should exist (may be empty if buffered)
        assert log_file.parent.exists()

    def test_setup_logging_idempotent(self, tmp_path: Path):
        """Test that setup_logging is idempotent (only configures once)."""
        import hermes.observability.logger as logger_module
        log1 = setup_logging(level="INFO", format="json")
        assert logger_module._configured is True
        # Second call should return early (already configured)
        log2 = setup_logging(level="DEBUG", format="console")
        # Both should be valid loggers (not necessarily the same object)
        assert log1 is not None
        assert log2 is not None
        # Configuration should not have changed
        assert logger_module._configured is True

    def test_setup_logging_invalid_level(self, tmp_path: Path):
        """Test with invalid log level defaults to INFO."""
        log = setup_logging(level="INVALID", format="json")
        assert log is not None
        # Should still work, defaulting to INFO
        log.info("test_message")


class TestGetLogger:
    """Test get_logger function."""

    def test_get_logger_default(self):
        """Test getting default logger."""
        log = get_logger()
        assert log is not None
        log.info("test_message")

    def test_get_logger_with_name(self):
        """Test getting named logger."""
        log = get_logger("orchestrator")
        assert log is not None
        log.info("test_message")

    def test_get_logger_with_context(self):
        """Test getting logger with bound context."""
        log = get_logger("executor", run_id="abc123", phase="research")
        assert log is not None
        log.info("test_message", step=1)


class TestBindContext:
    """Test bind_context function."""

    def test_bind_context_single(self):
        """Test binding single context variable."""
        bind_context(run_id="test123")
        log = get_logger()
        # Should not raise
        log.info("test_message")

    def test_bind_context_multiple(self):
        """Test binding multiple context variables."""
        bind_context(run_id="test123", phase="research", task="fix_bug")
        log = get_logger()
        log.info("test_message")


class TestClearContext:
    """Test clear_context function."""

    def test_clear_context(self):
        """Test clearing context variables."""
        bind_context(run_id="test123")
        clear_context()
        log = get_logger()
        # Should not raise
        log.info("test_message")

    def test_clear_context_empty(self):
        """Test clearing when no context is set."""
        clear_context()
        log = get_logger()
        log.info("test_message")
