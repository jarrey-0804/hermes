FROM python:3.11-slim

# 安装 Node.js（Claude CLI 依赖）
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl git && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g @anthropic-ai/claude-code && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 创建非特权用户
RUN useradd -m -u 1000 claude-agent

# 安装 Hermes
WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
COPY config/ config/
COPY prompts/ prompts/
COPY hooks/ hooks/
COPY scripts/ scripts/
COPY templates/ templates/

RUN pip install --no-cache-dir . && \
    chmod 755 scripts/* 2>/dev/null || true

# 审计目录：root 可写，claude-agent 只读
RUN mkdir -p /audit && chmod 750 /audit

# 运行时数据
VOLUME /workspace
VOLUME /runs

# 以 root 启动（Orchestrator 负责 drop privileges 调 Claude）
ENTRYPOINT ["hermes"]
CMD ["run"]
