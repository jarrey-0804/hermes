"""Logger — structlog 结构化日志配置。

设计参考：第5轮（可观测性 — 日志规范）。
- JSON 输出（生产）/ Console 输出（开发）
- 上下文绑定（run_id, phase）
- 分级：DEBUG / INFO / WARN / ERROR
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog

_configured = False


def setup_logging(
    level: str = "INFO",
    format: str = "json",  # json | console
    log_file: Path | None = None,
) -> structlog.BoundLogger:
    """配置 structlog 全局日志。

    Args:
        level: 日志级别 (DEBUG/INFO/WARN/ERROR)
        format: 输出格式 (json=生产, console=开发)
        log_file: 可选日志文件路径
    """
    global _configured

    if _configured:
        return structlog.get_logger()

    log_level = getattr(logging, level.upper(), logging.INFO)

    # 标准库 logging 处理器
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(str(log_file), encoding="utf-8"))

    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        handlers=handlers,
        force=True,
    )

    # structlog 处理链
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if format == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer(
            ensure_ascii=False
        )
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # 配置 formatter
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    for handler in handlers:
        handler.setFormatter(formatter)

    _configured = True
    return structlog.get_logger("hermes")


def get_logger(name: str = "hermes", **kwargs: str) -> structlog.BoundLogger:
    """获取带上下文的 logger。

    Usage:
        log = get_logger("orchestrator", run_id="abc123")
        log.info("phase_started", phase="research")
    """
    return structlog.get_logger(name).bind(**kwargs)


def bind_context(**kwargs: str) -> None:
    """绑定上下文变量（所有后续日志都会包含）。

    Usage:
        bind_context(run_id="abc123", phase="research")
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    """清除所有上下文变量。"""
    structlog.contextvars.clear_contextvars()
