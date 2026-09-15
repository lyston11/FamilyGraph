#!/usr/bin/env bash
# 本地开发一键启动（幂等，可重复执行）：
#   后端三 listener（8000/8001/8002）+ agent sidecar（8080）+ 家庭前端（5173）+ 管理员前端（5174）
# 已在运行的服务自动跳过；日志写入 .dev-logs/。
# 注意：dbx 数据库查看器已下线本地部署（2026-09-12），一律走服务器全局 dbx（远程模式经隧道 4225 访问）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/.dev-logs"
mkdir -p "$LOG_DIR"

log()  { printf '\033[1;36m[dev-up]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[dev-up]\033[0m %s\n' "$*" >&2; exit 1; }

port_up() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# ---- 1) 后端：app.serve 三 listener（8000 家庭 / 8001 agent 内部 / 8002 管理员）----
if port_up 8000 && port_up 8001 && port_up 8002; then
  log "后端已在运行（8000/8001/8002）"
else
  if port_up 8000 || port_up 8001 || port_up 8002; then
    fail "后端端口被部分占用（疑似残留单端口 uvicorn），请先清理，例如：kill \$(lsof -t -nP -iTCP:8001 -sTCP:LISTEN)"
  fi
  [ -x "$ROOT/backend/.venv/bin/python" ] || fail "缺少 backend/.venv：cd backend && python3 -m venv .venv && pip install -e '.[dev]'"
  [ -f "$ROOT/.env" ] || fail "缺少根目录 .env（SECRET_KEY / ADMIN_JWT_SECRET 等），生成方式见 README"
  log "启动后端 app.serve（8000/8001/8002）…"
  (
    cd "$ROOT/backend"
    set -a
    # shellcheck disable=SC1091
    source "$ROOT/.env"
    set +a
    nohup .venv/bin/python -m app.serve > "$LOG_DIR/backend.log" 2>&1 &
  )
fi

# ---- 2) Agent sidecar（8080 健康端点）----
if port_up 8080; then
  log "Agent sidecar 已在运行（8080）"
else
  [ -d "$ROOT/agent/node_modules" ] || fail "缺少 agent/node_modules：cd agent && npm install"
  [ -d "$ROOT/agent/dist" ] || fail "缺少 agent/dist：cd agent && npm run build"
  [ -f "$ROOT/.env" ] || fail "缺少根目录 .env（AGENT_SERVICE_SECRET 等），生成方式见 README"
  log "启动 agent sidecar（8080）…"
  (
    cd "$ROOT/agent"
    set -a
    # shellcheck disable=SC1091
    source "$ROOT/.env"
    # 本地开发环境变量
    export FG_API_BASE_URL="http://localhost:8000"
    export FG_INTERNAL_API_BASE_URL="http://localhost:8001"
    export HEALTH_PORT=8080
    set +a
    nohup node dist/main.js > "$LOG_DIR/agent-sidecar.log" 2>&1 &
  )
fi

# ---- 3) 两个前端（vite：/api 代理 8000，/admin-api 代理 8002）----
if port_up 5173; then
  log "家庭前端已在运行 → http://localhost:5173"
else
  log "启动家庭前端（5173）…"
  ( cd "$ROOT/frontend" && nohup npm run dev > "$LOG_DIR/frontend-5173.log" 2>&1 & )
fi

if port_up 5174; then
  log "管理员前端已在运行 → http://localhost:5174"
else
  log "启动管理员前端（5174）…"
  ( cd "$ROOT/system-admin-frontend" && nohup npm run dev > "$LOG_DIR/admin-frontend-5174.log" 2>&1 & )
fi

# ---- 4) 健康检查 ----
probe() { # $1=url $2=名称
  for _ in $(seq 1 20); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$1" 2>/dev/null || true)" = "200" ] && { log "OK   $2 → $1"; return 0; }
    sleep 1
  done
  log "FAIL $2 -> $1 (check logs in $LOG_DIR)"
  return 1
}

FAILED=0
probe http://localhost:8000/api/health    "家庭 API"   || FAILED=1
probe http://localhost:8002/admin-api/health "管理员 API" || FAILED=1
probe http://localhost:8080/healthz       "Agent Sidecar" || FAILED=1
probe http://localhost:5173               "家庭前端"   || FAILED=1
probe http://localhost:5174               "管理员前端" || FAILED=1

[ "$FAILED" -eq 0 ] && log "全部服务就绪 ✅" || fail "有服务未就绪，请查看 $LOG_DIR 下日志"
