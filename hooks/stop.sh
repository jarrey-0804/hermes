#!/usr/bin/env bash
# Stop Hook — Claude 执行结束时调用。
# 用于清理和通知。

set -euo pipefail

RUN_ID="${RUN_ID:-unknown}"
echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Claude session ended for run: $RUN_ID" >&2

exit 0
