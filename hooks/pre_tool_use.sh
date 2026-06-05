#!/usr/bin/env bash
# PreToolUse Hook — 在 Claude 调用工具前拦截危险操作。
#
# Exit 0 + JSON stdout = 结构化决策（allow/deny）
# Exit 2 + stderr = 硬阻断
#
# 通过 stdin 接收 JSON：{"tool_name": "...", "tool_input": {...}}

set -euo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null || echo "")
TOOL_INPUT=$(echo "$INPUT" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin).get('tool_input',{})))" 2>/dev/null || echo "{}")

# 禁止的 git 操作
FORBIDDEN_GIT="checkout|add|commit|reset|stash|merge|rebase|push|fetch"

if [ "$TOOL_NAME" = "Bash" ]; then
    CMD=$(echo "$TOOL_INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('command',''))" 2>/dev/null || echo "")

    # 检查 git 操作
    if echo "$CMD" | grep -qE "^git\s+($FORBIDDEN_GIT)\b"; then
        echo "BLOCKED: Forbidden git operation: $CMD" >&2
        exit 2
    fi

    # 检查 npm install
    if echo "$CMD" | grep -qE "npm\s+install"; then
        echo "BLOCKED: npm install not allowed during execution" >&2
        exit 2
    fi

    # 检查 sudo
    if echo "$CMD" | grep -qE "^sudo\b"; then
        echo "BLOCKED: sudo not allowed" >&2
        exit 2
    fi

    # 检查 curl/wget 到非白名单域名
    if echo "$CMD" | grep -qE "(curl|wget)\s+http"; then
        echo "BLOCKED: External HTTP requests not allowed" >&2
        exit 2
    fi
fi

if [ "$TOOL_NAME" = "Write" ] || [ "$TOOL_NAME" = "Edit" ]; then
    FILE_PATH=$(echo "$TOOL_INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('file_path',''))" 2>/dev/null || echo "")

    # 保护敏感文件
    if echo "$FILE_PATH" | grep -qE "\.(env|pem|key|p12)$|credentials|\.ssh/|\.aws/"; then
        echo "BLOCKED: Protected file: $FILE_PATH" >&2
        exit 2
    fi
fi

# 允许操作
echo '{"decision": "allow"}'
exit 0
