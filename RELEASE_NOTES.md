# Hermes v0.1.0

**可审计、可回滚、受约束的 AI 代码执行器**

Claude Code Workflow 自动化四阶循环系统，通过外部状态机驱动 Claude 完成 RESEARCH → PLAN → EXECUTE → QC 四阶段代码修改任务。

---

## 🏗️ 核心架构

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

## ✨ 功能特性

### 四阶循环引擎
- **RESEARCH** — 只读调查，产出 `findings.json`（Haiku 模型，节省成本）
- **PLAN** — 拓扑排序执行计划，≤12 步，每步 ≤3 文件
- **EXECUTE** — 逐步代码实现，带 QC 反馈重试
- **QC** — 双通道质检：硬检脚本 + Claude 审查，3 级 verdict

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

## 📊 质量指标

| 指标 | 数值 |
|------|------|
| 测试数量 | **198** (全部通过) |
| 代码覆盖率 | **86%** |
| 源码行数 | **7,265** |
| 模块数 | **21** |
| 已修复问题 | **P0×5 + P1×4 + P2×2 = 11** |

## 🚀 快速开始

```bash
# 安装
git clone git@github.com:jarrey-0804/hermes.git
cd hermes
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# 检查环境
hermes doctor

# 验证配置
hermes config validate

# 提交任务
hermes run "Fix the login bug in auth.py" --project ./my-project

# 查看状态
hermes status

# 查看日志
hermes logs <run-id>
```

## 📁 项目结构

```
src/hermes/
├── orchestrator/       # TCB 核心
│   ├── state_machine   # 状态机 (98% coverage)
│   ├── core            # 主循环 (TCB 三件事)
│   └── wal             # 预写日志 (93%)
├── executor/           # 执行层
│   ├── claude_runner   # Claude CLI (95%)
│   ├── context_bridge  # 上下文桥接 (85%)
│   └── prompt_builder  # Prompt 构建 (96%)
├── qc/                 # 质检
│   ├── artifact        # Schema 模型 (88%)
│   └── hard_checks     # 硬检脚本 (95%)
├── safety/             # 安全
│   └── path_guard      # 路径守卫 (89%)
├── observability/      # 可观测性
│   └── logger          # 结构化日志 (100%)
└── utils/              # 工具
    ├── config          # 配置模型 (94%)
    ├── budget          # 预算追踪 (95%)
    └── git_ops         # Git 操作 (72%)
```

## 🔧 技术栈

- **Python 3.11+** / asyncio
- **Pydantic v2** — 配置验证 + 数据模型
- **structlog** — 结构化 JSON 日志
- **Jinja2** — Prompt 模板引擎
- **Typer** — CLI 框架
- **Claude Code CLI** — AI 执行层
- **Docker** (可选) — 容器化部署

## ⚠️ 已知限制

- 单任务串行（多任务并发在 V1.1）
- WAL 使用 JSONL 文件（SQLite 在 V1.2）
- Orchestrator 核心覆盖率 53%（需集成测试）
- Prompt 效果需真实任务调优

## 📝 Changelog

- `290fe7f` — P2: is_safe off-by-one + Pydantic forbid + 测试 65%→86%
- `d67f35c` — P1: TCB 瘦身 + NETWORK_ERROR + Git 解析器 + 配置警告
- `bb86dc6` — P0: 异常收尾保证 + Artifact I/O + .git 保护
- `59ef89c` — 初始版本 v0.1.0
