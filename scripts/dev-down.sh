#!/usr/bin/env bash
# 本地开发一键停止：
#   停止后端三 listener（8000/8001/8002）+ agent sidecar（8080）+ 家庭前端（5173）+ 管理员前端（5174）
# 使用 lsof 查找占用端口的进程并优雅停止（SIGTERM）。
set -euo pipefail

log()  { printf '\033[1;36m[dev-down]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[dev-down]\033[0m %s\n' "$*"; }

kill_port() {
  local port=$1
  local name=$2
  local pids
  pids=$(lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  
  if [ -z "$pids" ]; then
    log "$name (端口 $port) 未运行"
    return 0
  fi
  
  log "停止 $name (端口 $port, PID: $pids)..."
  for pid in $pids; do
    kill -TERM "$pid" 2>/dev/null || warn "无法停止 PID $pid"
  done
  
  # 等待进程退出（最多 5 秒）
  for _ in $(seq 1 10); do
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      sleep 0.5
    else
      log "$name 已停止"
      return 0
    fi
  done
  
  warn "$name 未在 5 秒内停止，可能需要手动清理"
}

# 停止所有服务
kill_port 8000 "后端家庭 API"
kill_port 8001 "后端 Agent 内部 API"
kill_port 8002 "后端管理员 API"
kill_port 8080 "Agent Sidecar"
kill_port 5173 "家庭前端"
kill_port 5174 "管理员前端"

log "所有开发服务已停止"
