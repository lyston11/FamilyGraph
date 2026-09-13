#!/usr/bin/env bash
# 服务器自动化一次性安装器（重装即可恢复全部自动化）：
#   - familygraph-api.service                : 后端三 listener（8000 家庭 / 8001 agent 内部 / 8002 管理员）
#   - familygraph-agent.service              : agent sidecar（assistant run 执行器，健康端口 18080）
#   - familygraph-code-sync.service/.timer   : 每 30 分钟 commit+rebase+push
#   - familygraph-db-backup.service/.timer   : 每小时备份到 lyston11/familygraph-backups
#
# 前置条件：
#   - backend/.venv 已创建（cd backend && python3 -m venv .venv && pip install -e '.[dev]'）
#   - agent/ 构建所需 node ≥ 20（服务器 v22 实测可构建运行；engines 声明 >=24 仅
#     npm 警告，见 README 运维手册的版本豁免记录）
#   - ~/.config/familygraph/familygraph.env 已创建（强随机 SECRET_KEY/AGENT_SERVICE_SECRET/
#     ADMIN_JWT_SECRET 等，含 PUBLIC_API_HOST=127.0.0.1 保持仅回环+SSH 隧道可达）
# 均为 systemd 用户单元；enable-linger 保证注销/重启后自愈。
# 后端代码生效链路（手动）：git pull 后 `systemctl --user restart familygraph-api`。
set -euo pipefail
UNIT_DIR="$HOME/.config/systemd/user"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$HOME/.config/familygraph/familygraph.env"

[ -f "$ENV_FILE" ] || { echo "缺少 $ENV_FILE（强随机密钥等），先创建后再安装" >&2; exit 1; }
[ -x "$REPO_ROOT/backend/.venv/bin/python" ] || { echo "缺少 backend/.venv，先创建" >&2; exit 1; }
NODE_BIN="$(command -v node || true)"
[ -n "$NODE_BIN" ] || { echo "缺少 node（agent sidecar 构建与运行需要）" >&2; exit 1; }

chmod +x "$REPO_ROOT/scripts/server-sync-code.sh" "$REPO_ROOT/scripts/server-backup.sh"

# agent sidecar 构建（幂等）：node_modules/dist 不同步进仓库，每次安装重建。
# 服务器 node 22 与 engines >=24 仅产生 npm 警告（非 engine-strict），实测可构建运行。
echo "building agent sidecar…"
(
    cd "$REPO_ROOT/agent"
    npm ci --no-audit --no-fund
    npm run build
)

# 数据库迁移（幂等）：服务 fail-closed，拒绝在未迁移库上自动 bootstrap
echo "applying alembic migrations…"
(
    cd "$REPO_ROOT/backend"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
    "$REPO_ROOT/backend/.venv/bin/alembic" upgrade head
)

# 无人值守提交的 git 身份（已有全局/仓库配置则不覆盖）
git -C "$REPO_ROOT" config user.name  >/dev/null 2>&1 || \
    git -C "$REPO_ROOT" config user.name "lyston11"
git -C "$REPO_ROOT" config user.email >/dev/null 2>&1 || \
    git -C "$REPO_ROOT" config user.email "lyston11@users.noreply.github.com"

mkdir -p "$UNIT_DIR"

# 1) 后端服务：app.serve 三 listener；WorkingDirectory=backend 使 DATA_DIR 默认
#    落在 backend/data（gitignore 内），与本地开发布局一致
cat > "$UNIT_DIR/familygraph-api.service" <<EOF
[Unit]
Description=FamilyGraph API (public 8000 / internal 8001 / admin 8002)
After=network.target

[Service]
Type=simple
WorkingDirectory=$REPO_ROOT/backend
EnvironmentFile=$ENV_FILE
ExecStart=$REPO_ROOT/backend/.venv/bin/python -m app.serve
Restart=on-failure
RestartSec=5
# 与 serve.py SHUTDOWN_GRACE_SECONDS（默认 5s，uvicorn 强断 SSE/慢请求）对齐：
# 预留 lifespan 收尾余量；超时 SIGKILL 兜底，保证端口秒级释放、restart 不再竞态。
TimeoutStopSec=15

[Install]
WantedBy=default.target
EOF

# 2) agent sidecar：assistant run 执行器，轮询 internal listener 租约并执行模型调用。
#    只读复用 ENV_FILE（AGENT_SERVICE_SECRET 等，仅进程内存）；健康端口用 18080，
#    避开服务器已占用的 8080。sidecarId 固定，便于审计区分实例。
cat > "$UNIT_DIR/familygraph-agent.service" <<EOF
[Unit]
Description=FamilyGraph Agent sidecar (assistant run executor, health 18080)
After=network.target familygraph-api.service

[Service]
Type=simple
WorkingDirectory=$REPO_ROOT/agent
EnvironmentFile=$ENV_FILE
Environment=FG_API_BASE_URL=http://127.0.0.1:8000
Environment=FG_INTERNAL_API_BASE_URL=http://127.0.0.1:8001
Environment=AGENT_SIDECAR_ID=lyston-server-1
Environment=HEALTH_PORT=18080
ExecStart=$NODE_BIN dist/main.js
Restart=on-failure
RestartSec=5
TimeoutStopSec=15

[Install]
WantedBy=default.target
EOF

# 3) 定时任务（Persistent=true：关机错过的任务开机会自动补跑）
write_unit() { # name, on-calendar, exec
    local name="$1" calendar="$2" exec="$3"
    cat > "$UNIT_DIR/$name.service" <<EOF
[Unit]
Description=FamilyGraph $name (oneshot)

[Service]
Type=oneshot
ExecStart=$exec
EOF
    cat > "$UNIT_DIR/$name.timer" <<EOF
[Unit]
Description=FamilyGraph $name schedule

[Timer]
OnCalendar=$calendar
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
EOF
}
write_unit "familygraph-code-sync" "*:0/30" "$REPO_ROOT/scripts/server-sync-code.sh"
write_unit "familygraph-db-backup" "hourly" "$REPO_ROOT/scripts/server-backup.sh"

systemctl --user daemon-reload
systemctl --user enable --now familygraph-api.service familygraph-agent.service familygraph-code-sync.timer familygraph-db-backup.timer

if ! loginctl show-user "$USER" -p Linger | grep -q yes; then
    sudo -n loginctl enable-linger "$USER" || \
        echo "WARNING: could not enable linger; services stop at logout"
fi

echo "installed. status:"
systemctl --user list-timers 'familygraph-*' --no-pager
systemctl --user is-active familygraph-api.service || true
systemctl --user is-active familygraph-agent.service || {
    echo "WARNING: familygraph-agent 未运行（assistant 将停在 queued）；" \
         "检查 journalctl --user -u familygraph-agent 与 18080 端口占用" >&2
    true
}
