# Hermes

**Claude Code 四阶循环工作流引擎**

[![Tests](https://img.shields.io/badge/tests-198%20passed-brightgreen)](https://github.com/jarrey-0804/hermes/actions)
[![Coverage](https://img.shields.io/badge/coverage-86%25-green)](https://github.com/jarrey-0804/hermes)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-v0.1.0-orange)](https://github.com/jarrey-0804/hermes/releases/tag/v0.1.0)

可审计、可回滚、受约束的 AI 代码执行器。通过外部状态机驱动 Claude 完成 **RESEARCH → PLAN → EXECUTE → QC** 四阶段代码修改任务。

## 特性

### 四阶循环引擎
- **RESEARCH** — 只读调查，产出 `findings.json`（Haiku 模型，节省成本）
- **PLAN** — 拓扑排序执行计划，≤12 步，每步 ≤3 文件
- **EXECUTE** — 逐步代码实现，带 QC 反馈重试
- **QC** — 双通道质检：硬检脚本 + Claude 审查，3 级 verdict（PASS / SOFT_FAIL / HARD_FAIL）

### 安全纵深防御
- **PathGuard** — 阻止访问 `.env`, `.ssh`, `.git/hooks` 等敏感路径
- **Injection Scanner** — 8 种 Prompt Injection 模式检测
- **Git 操作白名单** — 禁止 `checkout`, `add`, `commit` 等危险操作
- **Pydantic `extra='forbid'`** — 配置拼写错误立即报错

### 可靠性保障
- **WAL (Write-Ahead Log)** — JSONL + fsync + hash chain，崩溃恢复 + 防篡改
- **try/finally 收尾保证** — 任何异常都会写入 WAL 和保存状态
- **BudgetTracker** — 实时成本追踪 + 阈值告警 + 硬限熔断
- **NETWORK_ERROR** — 区分网络故障和逻辑错误，精确归因

### 开发者体验
- **CLI 工具链** — `hermes run` / `status` / `logs` / `config` / `doctor`
- **结构化日志** — structlog JSON 输出，上下文绑定
- **配置验证** — 启动时强制验证 + 交叉约束检查

## 架构

```
触发层 (CLI / GitHub Actions / Webhook)
    ↓
调度层 (TaskQueue → BudgetTracker → ConfigValidator → Orchestrator)
    ↓
执行层 (RESEARCH → PLAN → EXECUTE → QC)  ← Docker 容器沙箱
    ↓
持久层 (WAL + Artifacts + Config + Audit Log)
    ↓
可观测层 (Dashboard + structlog + Cost Tracker)
```

## 安装

### 环境要求
- Python 3.11+
- Claude Code CLI (`claude`)
- Git
- Docker（可选，用于沙箱隔离）

### 快速安装

```bash
git clone git@github.com:jarrey-0804/hermes.git
cd hermes
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

### 验证安装

```bash
# 检查环境依赖
hermes doctor

# 验证配置文件
hermes config validate
```

## 快速开始

### 1. 初始化配置

复制并编辑配置文件：

```bash
cp config/hermes.yaml.example config/hermes.yaml
# 根据需要修改配置
```

### 2. 运行任务

```bash
# 基本用法
hermes run "修复 auth.py 中的登录 bug" --project ./my-project

# 指定阶段（调试用）
hermes run "添加单元测试" --project ./my-project --phase RESEARCH

# 干跑模式（不实际执行）
hermes run "重构数据库层" --project ./my-project --dry-run
```

### 3. 查看状态

```bash
# 查看当前运行状态
hermes status

# 查看运行日志
hermes logs <run-id>

# 导出运行报告
hermes export <run-id> --format json
```

### 4. 管理任务

```bash
# 列出所有任务
hermes list

# 取消运行中的任务
hermes cancel <run-id>

# 重试失败的任务
hermes retry <run-id>
```

## 配置

配置文件位于 `config/hermes.yaml`，支持以下配置项：

```yaml
general:
  project_dir: /workspace
  data_dir: ./runs
  log_level: INFO
  log_format: json

model:
  research: haiku
  plan: sonnet
  execute: sonnet
  qc: haiku

stages:
  research:
    timeout_sec: 900
    max_turns: 8
  plan:
    timeout_sec: 600
    max_turns: 6
  execute:
    timeout_sec: 1800
    max_turns: 12
  qc:
    timeout_sec: 600
    max_turns: 4

budget:
  max_per_task_usd: 5.0
  max_daily_usd: 50.0
  alert_threshold_pct: 80

security:
  forbidden_git_ops: [checkout, add, commit]
  protected_paths: [.env, .ssh, .git/hooks]
```

详细配置说明请参考 [配置文档](docs/configuration.md)。

## 项目结构

```
hermes/
├── src/hermes/
│   ├── orchestrator/       # TCB 核心
│   │   ├── state_machine   # 状态机 (98% coverage)
│   │   ├── core            # 主循环 (TCB 三件事)
│   │   └── wal             # 预写日志 (93%)
│   ├── executor/           # 执行层
│   │   ├── claude_runner   # Claude CLI (95%)
│   │   ├── context_bridge  # 上下文桥接 (85%)
│   │   └── prompt_builder  # Prompt 构建 (96%)
│   ├── qc/                 # 质检
│   │   ├── artifact        # Schema 模型 (88%)
│   │   └── hard_checks     # 硬检脚本 (95%)
│   ├── safety/             # 安全
│   │   └── path_guard      # 路径守卫 (89%)
│   ├── observability/      # 可观测性
│   │   └── logger          # 结构化日志 (100%)
│   └── utils/              # 工具
│       ├── config          # 配置模型 (94%)
│       ├── budget          # 预算追踪 (95%)
│       └── git_ops         # Git 操作 (72%)
├── tests/                  # 测试套件 (198 tests)
├── config/                 # 配置文件
├── hooks/                  # PreToolUse/PostToolUse/Stop
├── prompts/                # Jinja2 模板
└── docker/                 # Docker 部署
```

## 质量指标

| 指标 | 数值 |
|------|------|
| 测试数量 | **198** (全部通过) |
| 代码覆盖率 | **86%** |
| 源码行数 | **7,265** |
| 模块数 | **21** |
| 已修复问题 | **P0×5 + P1×4 + P2×2 = 11** |

### 测试覆盖详情

| 模块 | 覆盖率 | 说明 |
|------|--------|------|
| `logger.py` | 100% | 完整覆盖 |
| `state_machine.py` | 98% | 状态机核心 |
| `prompt_builder.py` | 96% | Prompt 构建 |
| `claude_runner.py` | 95% | Claude 调用 |
| `hard_checks.py` | 95% | 质检脚本 |
| `budget.py` | 95% | 预算追踪 |
| `config.py` | 94% | 配置管理 |
| `wal.py` | 93% | 预写日志 |
| `path_guard.py` | 89% | 路径守卫 |
| `artifact.py` | 88% | Artifact 模型 |
| `context_bridge.py` | 85% | 上下文桥接 |
| `cli.py` | 79% | CLI 命令 |
| `git_ops.py` | 72% | Git 操作 |
| `core.py` | 53% | 编排器核心 |

## 路线图

### v0.1.0 (当前版本)
- ✅ 四阶循环引擎
- ✅ 安全纵深防御
- ✅ WAL 崩溃恢复
- ✅ CLI 工具链
- ✅ 198 个单元测试

### v1.1.0 (计划中)
- 多任务并发执行
- 任务队列持久化
- Webhook 触发支持

### v1.2.0 (计划中)
- SQLite WAL 替代 JSONL
- 增强错误恢复机制
- 性能优化

## 已知限制

- 单任务串行（多任务并发在 v1.1）
- WAL 使用 JSONL 文件（SQLite 在 v1.2）
- Orchestrator 核心覆盖率 53%（需集成测试）
- Prompt 效果需真实任务调优

## 开发

### 运行测试

```bash
# 运行所有测试
pytest tests/

# 运行单元测试
pytest tests/unit/

# 运行集成测试
pytest tests/integration/

# 生成覆盖率报告
pytest --cov=src/hermes --cov-report=html
```

### 代码质量

```bash
# 代码检查
ruff check src/

# 类型检查
mypy src/

# 代码格式化
black src/ tests/
```

## 贡献

欢迎贡献！请遵循以下步骤：

1. Fork 项目
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 开启 Pull Request

### 开发指南

- 遵循 PEP 8 代码风格
- 为新功能编写测试
- 保持测试覆盖率 ≥ 80%
- 更新相关文档

## 许可证

MIT License - 详见 [LICENSE](LICENSE) 文件

## 致谢

- [Claude Code](https://www.anthropic.com/claude-code) - AI 执行层
- [Pydantic](https://docs.pydantic.dev/) - 数据验证
- [structlog](https://www.structlog.org/) - 结构化日志
- [Typer](https://typer.tiangolo.com/) - CLI 框架

## 联系方式

- GitHub Issues: [提交问题](https://github.com/jarrey-0804/hermes/issues)
- Email: 543162855@qq.com

---

**Hermes** - 让 AI 代码修改变得可控、可审计、可回滚。
