#!/usr/bin/env bash
# 远程开发模式一键启动（幂等，可重复执行）：
#   后端/数据库/dbx 跑在 lyston 服务器，本地经 launchd SSH 隧道访问；
#   本地只启动两个前端 dev server（HMR 需要毫秒级响应）：
#     launchd 隧道（8000/8001/8002 + 4225→服务器 dbx 4224）
#     + 家庭前端（5173）+ 管理员前端（5174）
# 与 dev-up.sh（全本地模式）互斥使用：远程模式下本地不跑后端与 dbx（4225 冲突）。
# 后端代码生效（服务器上手动）：git pull 后 systemctl --user restart familygraph-api
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/.dev-logs"
PLIST_LABEL="com.familygraph.dev-tunnel"
PLIST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
GUI_TARGET="gui/$(id -u)/$PLIST_LABEL"
mkdir -p "$LOG_DIR"

log()  { printf '\033[1;36m[dev-up-remote]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[dev-up-remote]\033[0m %s\n' "$*" >&2; exit 1; }

port_up() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# ---- 1) launchd 隧道（KeepAlive 自愈；未安装 plist 先按 README 安装）----
[ -f "$PLIST" ] || fail "缺少 $PLIST（本地隧道 launchd 配置），请先安装（见 Codex 库 FamilyGraph 运维记录）"
if port_up 8000 && port_up 8002 && port_up 4225; then
  log "隧道已在运行（8000/8002/4225）"
else
  log "启动/重启隧道 $PLIST_LABEL …"
  launchctl kickstart -k "$GUI_TARGET" >/dev/null 2>&1 || {
    launchctl unload "$PLIST" >/dev/null 2>&1 || true
    launchctl load "$PLIST"
    launchctl kickstart -k "$GUI_TARGET" >/dev/null 2>&1 || true
  }
  for _ in $(seq 1 15); do
    port_up 8000 && port_up 8002 && port_up 4225 && break
    sleep 1
  done
  port_up 8000 || fail "隧道未就绪（本地 8000 未监听），日志见 /tmp/familygraph-dev-tunnel.log"
fi

# ---- 2) 两个前端（vite：/api 代理 8000，/admin-api 代理 8002，均走隧道）----
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

# ---- 3) 健康检查（经隧道打服务器，顺带验证全链路）----
probe() { # $1=url $2=名称
  for _ in $(seq 1 20); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$1" 2>/dev/null || true)" = "200" ] && { log "OK   $2 → $1"; return 0; }
    sleep 1
  done
  log "FAIL $2 → $1（隧道/服务器侧排查；服务器日志 journalctl --user -u familygraph-api）"
  return 1
}

FAILED=0
probe http://localhost:8000/api/health       "家庭 API（隧道）"   || FAILED=1
probe http://localhost:8002/admin-api/health "管理员 API（隧道）" || FAILED=1
probe http://localhost:5173                  "家庭前端"           || FAILED=1
probe http://localhost:5174                  "管理员前端"         || FAILED=1
probe http://127.0.0.1:4225                  "dbx（隧道→服务器）" || FAILED=1

# agent sidecar 不监听本地端口，经 ssh 探测服务器侧 systemd 状态；
# 缺失时 assistant run 会永远 queued（无告警），必须显式给出修复提示。
if ssh -o ConnectTimeout=10 lyston 'systemctl --user is-active --quiet familygraph-agent.service' 2>/dev/null; then
  log "OK   agent sidecar（服务器 systemd）"
else
  log "FAIL agent sidecar：familygraph-agent.service 未运行 → assistant 会停在 queued。" \
      "修复：ssh lyston 后执行 bash projects/FamilyGraph/scripts/install-server-automation.sh（幂等）" \
      "或 systemctl --user start familygraph-agent；日志 journalctl --user -u familygraph-agent"
  FAILED=1
fi

[ "$FAILED" -eq 0 ] && log "全部服务就绪 ✅（后端/数据库在 lyston 服务器）" || fail "有服务未就绪，请查看 $LOG_DIR 下日志"
