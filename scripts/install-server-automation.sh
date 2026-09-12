#!/usr/bin/env bash
# 服务器自动化一次性安装器（重装即可恢复全部自动化）：
#   - familygraph-api.service                : 后端三 listener（8000 家庭 / 8001 agent 内部 / 8002 管理员）
#   - familygraph-code-sync.service/.timer   : 每 30 分钟 commit+rebase+push
#   - familygraph-db-backup.service/.timer   : 每小时备份到 lyston11/familygraph-backups
#
# 前置条件：
#   - backend/.venv 已创建（cd backend && python3 -m venv .venv && pip install -e '.[dev]'）
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

chmod +x "$REPO_ROOT/scripts/server-sync-code.sh" "$REPO_ROOT/scripts/server-backup.sh"

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

[Install]
WantedBy=default.target
EOF

# 2) 定时任务（Persistent=true：关机错过的任务开机会自动补跑）
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
systemctl --user enable --now familygraph-api.service familygraph-code-sync.timer familygraph-db-backup.timer

if ! loginctl show-user "$USER" -p Linger | grep -q yes; then
    sudo -n loginctl enable-linger "$USER" || \
        echo "WARNING: could not enable linger; services stop at logout"
fi

echo "installed. status:"
systemctl --user list-timers 'familygraph-*' --no-pager
systemctl --user is-active familygraph-api.service || true
