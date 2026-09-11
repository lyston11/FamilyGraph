#!/usr/bin/env bash
# 真实 API smoke 包装（09-11 frontend-system-admin-audit-remediation Phase B）：
#   ./scripts/frontend-api-smoke.sh --report /tmp/familygraph-smoke.json
# 启动隔离 DATA_DIR 的真实三 listener 并执行家庭/后台/交叉拒绝用例；
# 退出码：0 通过 / 1 有失败 / 2 环境阻塞。报告脱敏（无姓名/PIN/JWT/路径）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPORT_ARGS=()
if [ $# -gt 0 ]; then
  REPORT_ARGS=("$@")
fi

exec "$ROOT/backend/.venv/bin/python" "$ROOT/scripts/smoke/run_api_smoke.py" "${REPORT_ARGS[@]}"
