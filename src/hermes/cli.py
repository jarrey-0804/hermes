"""Hermes CLI — Typer 命令行接口。

设计参考：第8轮（CLI 接口设计 14 个命令）。
MVP 实现：run / status / logs / config / doctor。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from hermes import __version__

app = typer.Typer(
    name="hermes",
    help="Claude Code Workflow 自动化四阶循环系统",
    add_completion=False,
)
console = Console()


# ─── 任务管理 ──────────────────────────────────────────────


@app.command()
def run(
    task: str = typer.Argument(..., help="任务描述"),
    project_dir: Path = typer.Option(Path("."), "--project", "-p", help="项目目录"),
    config: Path = typer.Option(Path("config/hermes.yaml"), "--config", "-c", help="配置文件路径"),
    model: str | None = typer.Option(None, "--model", "-m", help="覆盖默认模型"),
    budget: float | None = typer.Option(None, "--budget", "-b", help="预算上限 USD"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅验证配置，不执行"),
) -> None:
    """提交并执行一个任务。"""
    from hermes.observability.logger import setup_logging
    from hermes.orchestrator.core import Orchestrator
    from hermes.utils.config import ConfigValidator, HermesConfig

    # 加载配置
    cfg = HermesConfig.load(config)
    if model:
        cfg.model.default = model
    if budget:
        cfg.budget.max_per_task_usd = budget

    # 验证配置
    validator = ConfigValidator(cfg)
    result = validator.validate()

    if not result.is_valid:
        console.print("[red]Configuration errors:[/red]")
        for err in result.errors:
            console.print(f"  ✗ {err}")
        raise typer.Exit(code=1)

    for warn in result.warnings:
        console.print(f"  ⚠ {warn}")

    if dry_run:
        console.print("[green]Configuration valid. Dry run complete.[/green]")
        return

    # 设置日志
    setup_logging(level=cfg.general.log_level, format=cfg.general.log_format)

    # 执行
    console.print(f"[bold]Hermes[/bold] v{__version__} — Starting task")
    console.print(f"  Task: {task[:100]}")
    console.print(f"  Project: {project_dir}")

    try:
        orch = Orchestrator(
            task_description=task,
            project_dir=project_dir,
            config=cfg,
        )
        console.print(f"  Run ID: {orch.run_id}")
        console.print(f"  Data dir: {orch.task_dir}")
        console.print()

        final_phase = orch.run()

        if final_phase.value == "done":
            console.print("\n[green]✓ Task completed successfully[/green]")
        else:
            console.print(f"\n[yellow]⚠ Task ended at: {final_phase.value}[/yellow]")

        console.print(f"  Cost: ${orch._total_cost:.4f}")

    except KeyboardInterrupt:
        console.print("\n[yellow]Aborted by user[/yellow]")
        raise typer.Exit(code=130)
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


@app.command()
def status(
    run_id: str | None = typer.Argument(None, help="Run ID（留空显示最近）"),
    data_dir: Path = typer.Option(Path("./runs"), "--data-dir", "-d", help="数据目录"),
    output: str = typer.Option("table", "--output", "-o", help="输出格式: table|json"),
) -> None:
    """查看任务状态。"""
    if run_id:
        state_file = data_dir / run_id / "orchestrator-state.json"
    else:
        # 找最近的任务
        state_file = _find_latest_state(data_dir)

    if not state_file or not state_file.exists():
        console.print("[yellow]No task found[/yellow]")
        raise typer.Exit(code=1)

    state = json.loads(state_file.read_text(encoding="utf-8"))

    if output == "json":
        console.print_json(json.dumps(state, indent=2))
    else:
        _print_task_status(state)


@app.command()
def logs(
    run_id: str = typer.Argument(..., help="Run ID"),
    data_dir: Path = typer.Option(Path("./runs"), "--data-dir", "-d"),
    event: str | None = typer.Option(None, "--event", "-e", help="过滤事件类型"),
    tail: int = typer.Option(50, "--tail", "-n", help="显示最后 N 条"),
) -> None:
    """查看任务 WAL 日志。"""
    from hermes.orchestrator.wal import WriteAheadLog

    wal_path = data_dir / run_id / "wal.jsonl"
    if not wal_path.exists():
        console.print(f"[red]WAL not found: {wal_path}[/red]")
        raise typer.Exit(code=1)

    wal = WriteAheadLog(wal_path)
    entries = wal.replay()

    if event:
        entries = [e for e in entries if e.event == event]

    for entry in entries[-tail:]:
        console.print(
            f"[dim]{entry.seq:04d}[/dim] "
            f"[cyan]{entry.event:<20s}[/cyan] "
            f"{json.dumps(entry.data, ensure_ascii=False)[:120]}"
        )


# ─── 配置管理 ──────────────────────────────────────────────


config_app = typer.Typer(help="配置管理")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show(
    config_path: Path = typer.Option(Path("config/hermes.yaml"), "--config", "-c"),
) -> None:
    """显示当前配置。"""
    from hermes.utils.config import HermesConfig

    cfg = HermesConfig.load(config_path)
    console.print_json(cfg.model_dump_json(indent=2))


@config_app.command("validate")
def config_validate(
    config_path: Path = typer.Option(Path("config/hermes.yaml"), "--config", "-c"),
) -> None:
    """验证配置文件。"""
    from hermes.utils.config import ConfigValidator, HermesConfig

    try:
        cfg = HermesConfig.load(config_path)
    except Exception as e:
        console.print(f"[red]Failed to load config: {e}[/red]")
        raise typer.Exit(code=1)

    validator = ConfigValidator(cfg)
    result = validator.validate()

    if result.errors:
        console.print("[red]Errors:[/red]")
        for err in result.errors:
            console.print(f"  ✗ {err}")

    if result.warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for warn in result.warnings:
            console.print(f"  ⚠ {warn}")

    if result.is_valid:
        console.print("[green]✓ Configuration is valid[/green]")
    else:
        raise typer.Exit(code=1)


# ─── 运维工具 ──────────────────────────────────────────────


@app.command()
def doctor() -> None:
    """检查 Hermes 运行环境。"""
    import shutil

    checks: list[tuple[str, bool, str]] = []

    # Python 版本
    py_version = sys.version.split()[0]
    checks.append(("Python >= 3.11", sys.version_info >= (3, 11), py_version))

    # Claude CLI
    claude_path = shutil.which("claude")
    checks.append(("Claude CLI", claude_path is not None, claude_path or "not found"))

    # Git
    git_path = shutil.which("git")
    checks.append(("Git", git_path is not None, git_path or "not found"))

    # Docker（可选）
    docker_path = shutil.which("docker")
    checks.append(("Docker (optional)", docker_path is not None, docker_path or "not found"))

    # API Key
    import os

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    checks.append(("ANTHROPIC_API_KEY", has_key, "set" if has_key else "not set"))

    # 依赖包
    required_packages = ["typer", "pydantic", "httpx", "structlog", "jinja2", "yaml"]
    for pkg in required_packages:
        try:
            __import__(pkg)
            checks.append((f"Package: {pkg}", True, "installed"))
        except ImportError:
            checks.append((f"Package: {pkg}", False, "missing"))

    # 显示结果
    table = Table(title="Hermes Doctor")
    table.add_column("Check", style="cyan")
    table.add_column("Status")
    table.add_column("Details", style="dim")

    for name, passed, detail in checks:
        status = "[green]✓[/green]" if passed else "[red]✗[/red]"
        table.add_row(name, status, detail)

    console.print(table)

    failed = sum(1 for _, p, _ in checks if not p)
    if failed:
        console.print(f"\n[red]{failed} checks failed[/red]")
        raise typer.Exit(code=1)
    else:
        console.print(f"\n[green]All {len(checks)} checks passed[/green]")


@app.command()
def version() -> None:
    """显示版本号。"""
    console.print(f"hermes {__version__}")


# ─── 辅助函数 ──────────────────────────────────────────────


def _find_latest_state(data_dir: Path) -> Path | None:
    """查找最近的任务状态文件。"""
    if not data_dir.exists():
        return None

    latest: Path | None = None
    latest_mtime: float = 0

    for state_file in data_dir.glob("*/orchestrator-state.json"):
        mtime = state_file.stat().st_mtime
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest = state_file

    return latest


def _print_task_status(state: dict) -> None:
    """打印任务状态表格。"""
    table = Table(title=f"Task: {state.get('run_id', 'unknown')}")
    table.add_column("Field", style="cyan")
    table.add_column("Value")

    table.add_row("Run ID", state.get("run_id", ""))
    table.add_row("Task", state.get("task", "")[:80])
    table.add_row("Phase", state.get("phase", ""))
    table.add_row("Cost", f"${state.get('total_cost_usd', 0):.4f}")
    table.add_row("QC Rounds", str(state.get("qc_rounds", 0)))
    table.add_row("Duration", f"{state.get('duration_sec', 0):.1f}s")

    console.print(table)

    # 历史
    sm = state.get("state_machine", {})
    history = sm.get("history", [])
    if history:
        console.print("\n[bold]Phase History:[/bold]")
        for h in history:
            console.print(f"  {h['from']} → {h['outcome']} → {h['to']}")


if __name__ == "__main__":
    app()
