#!/usr/bin/env bash
# PostToolUse Hook — 在 Claude 工具调用后审计记录。
#
# 通过 stdin 接收 JSON：{"tool_name": "...", "tool_input": {...}, "tool_output": "..."}

set -euo pipefail

INPUT=$(cat)
RUN_ID="${RUN_ID:-unknown}"
AUDIT_DIR="${AUDIT_DIR:-/audit}"

# 记录到审计日志（append-only）
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
TOOL_NAME=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null || echo "unknown")

if [ -d "$AUDIT_DIR" ]; then
    echo "{\"ts\":\"$TIMESTAMP\",\"run_id\":\"$RUN_ID\",\"tool\":\"$TOOL_NAME\"}" >> "$AUDIT_DIR/tool-calls.jsonl" 2>/dev/null || true
fi

exit 0
