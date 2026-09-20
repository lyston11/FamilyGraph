#!/usr/bin/env bash
# FamilyGraph 线上环境一键安装器（幂等，可重复执行）。
#
# 用途：在服务器上把 /home/ubuntu/fg-prod（独立 clone）作为**第二套**栈拉起，
# 经 Cloudflare 隧道以 https://fg.lyston.qzz.io 提供家庭端；系统管理员后台只
# 发布宿主回环 8101，仅经 SSH 隧道访问。与开发环境（systemd 用户单元 +
# ~/projects/FamilyGraph + 宿主 8000/8001/8002/18080）完全隔离。
#
# 隔离契约见 deploy/production/docker-compose.prod.yml 与任务 prd.md R1–R4。
#
# 前置条件：
#   - docker + docker compose plugin 可用
#   - ~/.config/familygraph/familygraph-prod.env 已创建（0600），
#     三个密钥为全新随机值且与开发环境不同；模板见
#     deploy/production/familygraph-prod.env.example
#   - 宿主回环 8100 / 8101 未被占用
#
# 幂等性：重复执行不重建卷、不重置数据、不重复建管理员；只重建镜像并
# 对齐容器与定时器状态。
#
# 手动操作（脚本不做的）：Cloudflare 侧 fg.lyston.qzz.io 的 Public Hostname
# 指向 http://127.0.0.1:8100（属用户侧配置，见 README 运维手册）。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_NAME="familygraph-prod"
ENV_FILE="$HOME/.config/familygraph/familygraph-prod.env"
COMPOSE_FILES=(-f "$REPO_ROOT/docker-compose.yml" -f "$REPO_ROOT/deploy/production/docker-compose.prod.yml")
UNIT_DIR="$HOME/.config/systemd/user"
PROD_WEB_PORT=8100
PROD_ADMIN_PORT=8101

log()  { printf '\033[1;36m[fg-prod]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[fg-prod]\033[0m %s\n' "$*" >&2; exit 1; }

compose() { docker compose -p "$PROJECT_NAME" --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" "$@"; }

# ---- 0) 前置校验（fail-fast，不留下半成品状态）----
command -v docker >/dev/null || fail "缺少 docker"
docker compose version >/dev/null 2>&1 || fail "缺少 docker compose plugin"
[ -f "$REPO_ROOT/docker-compose.yml" ] || fail "缺少 $REPO_ROOT/docker-compose.yml"
[ -f "$REPO_ROOT/deploy/production/docker-compose.prod.yml" ] || \
    fail "缺少 deploy/production/docker-compose.prod.yml"
[ -f "$ENV_FILE" ] || fail "缺少 $ENV_FILE（模板见 deploy/production/familygraph-prod.env.example）"
[ "$(stat -c '%a' "$ENV_FILE")" = "600" ] || fail "$ENV_FILE 权限必须为 600（当前 $(stat -c '%a' "$ENV_FILE")）"

# 三个密钥必须齐备且与开发环境不同（隔离的硬前提，prd.md R2）
DEV_ENV_FILE="$HOME/.config/familygraph/familygraph.env"
for key in SECRET_KEY AGENT_SERVICE_SECRET ADMIN_JWT_SECRET; do
    prod_val="$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
    [ -n "$prod_val" ] || fail "$ENV_FILE 缺少 $key（不得留空）"
    if [ -f "$DEV_ENV_FILE" ]; then
        dev_val="$(grep -E "^${key}=" "$DEV_ENV_FILE" | head -1 | cut -d= -f2- || true)"
        if [ -n "$dev_val" ] && [ "$dev_val" = "$prod_val" ]; then
            fail "$key 与开发环境相同，隔离失效；请用 openssl rand -hex 32 重新生成"
        fi
    fi
done

# 宿主端口不得被**别的**东西占用（否则 compose up 失败；提前给出清晰原因）。
# 被本 compose 项目自己的容器占用是正常的：重跑安装器时栈已经在跑，
# 只要不是我们自己的容器在监听就必须报错，否则会静默地把线上一半换掉。
for port in "$PROD_WEB_PORT" "$PROD_ADMIN_PORT"; do
    holders="$(docker ps --filter "publish=$port" --format '{{.Names}}' 2>/dev/null || true)"
    if [ -z "$holders" ]; then
        if ss -tlnH "sport = :$port" 2>/dev/null | grep -q .; then
            fail "宿主端口 $port 已被非本项目的进程占用（线上需要它做回环入口）"
        fi
    else
        foreign="$(printf '%s\n' "$holders" | grep -v "^${PROJECT_NAME}-" || true)"
        [ -z "$foreign" ] || fail "宿主端口 $port 被非本项目的容器占用：$foreign"
    fi
done

# 磁盘预检：三个镜像构建需要余量；不足只告警，由用户决定是否 prune
avail_gb="$(df -BG --output=avail / | tail -1 | tr -dc '0-9')"
if [ "${avail_gb:-0}" -lt 10 ]; then
    log "WARNING: 根分区仅剩 ${avail_gb}G，镜像构建可能失败；可先 docker builder prune"
fi

log "校验通过：$REPO_ROOT（project=$PROJECT_NAME）"

# ---- 1) 构建镜像（幂等；代码更新后重跑本脚本即生效）----
log "构建镜像…"
compose build

# ---- 2) 启动（compose 自带依赖顺序：web/admin-web/agent 等 api healthy）----
log "启动容器…"
compose up -d --remove-orphans

# ---- 3) 等待 api healthy ----
log "等待 api healthy…"
for _ in $(seq 1 60); do
    status="$(docker inspect --format '{{.State.Health.Status}}' \
        "$(compose ps -q api)" 2>/dev/null || echo unknown)"
    [ "$status" = "healthy" ] && break
    sleep 2
done
if [ "$status" != "healthy" ]; then
    compose logs --tail 40 api >&2 || true
    fail "api 未在 120s 内 healthy（上方为最近日志）"
fi
log "api healthy"

# ---- 4) 备份定时器（systemd 用户单元；在线 backup API，禁止运行期 cp 主库）----
# 实际动作在 scripts/prod-backup.sh：经 compose exec 调用既有 app.backup
# （AD-6：SQLite online backup API + integrity_check），快照落在线上
# /data/backups 卷内，保留 30 天。
chmod +x "$REPO_ROOT/scripts/prod-backup.sh"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/${PROJECT_NAME}-backup.service" <<EOF
[Unit]
Description=FamilyGraph 线上库备份（online backup API，落线上数据卷）

[Service]
Type=oneshot
WorkingDirectory=$REPO_ROOT
ExecStart=$REPO_ROOT/scripts/prod-backup.sh
EOF
cat > "$UNIT_DIR/${PROJECT_NAME}-backup.timer" <<EOF
[Unit]
Description=FamilyGraph 线上库备份调度（每 6 小时）

[Timer]
OnCalendar=*-*-* 0/6:17:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "${PROJECT_NAME}-backup.timer"

if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q yes; then
    sudo -n loginctl enable-linger "$USER" 2>/dev/null || \
        log "WARNING: 未能启用 linger，注销后备份定时器会停止"
fi

# ---- 5) 结果摘要与验收提示 ----
log "容器状态："
compose ps

log "本地健康检查（宿主回环）："
printf '  web        : '; curl -s -o /dev/null -w '%{http_code}\n' \
    --max-time 5 "http://127.0.0.1:$PROD_WEB_PORT/" || echo FAIL
printf '  web→api    : '; curl -s -o /dev/null -w '%{http_code}\n' \
    --max-time 5 "http://127.0.0.1:$PROD_WEB_PORT/api/health" || echo FAIL
printf '  admin-web  : '; curl -s -o /dev/null -w '%{http_code}\n' \
    --max-time 5 "http://127.0.0.1:$PROD_ADMIN_PORT/" || echo FAIL
printf '  后台痕迹检查（家庭端 /admin-api/ 必须 404）：' ; \
    curl -s -o /dev/null -w '%{http_code}\n' \
    --max-time 5 "http://127.0.0.1:$PROD_WEB_PORT/admin-api/health" || echo FAIL

log "公网入口（需 Cloudflare 侧 Public Hostname 已指向 127.0.0.1:$PROD_WEB_PORT）："
printf '  https://fg.lyston.qzz.io/api/health : '; curl -s -o /dev/null -w '%{http_code}\n' \
    --max-time 15 https://fg.lyston.qzz.io/api/health || echo FAIL

log "系统管理员后台（仅内网，SSH 隧道访问）："
log "  ssh -N -L $PROD_ADMIN_PORT:127.0.0.1:$PROD_ADMIN_PORT <本机到服务器的别名>"
log "  然后浏览器打开 http://127.0.0.1:$PROD_ADMIN_PORT"
log "  初始凭据：docker compose -p $PROJECT_NAME exec api cat /data/bootstrap/admin-credentials"
log "  （首登强制改密后该文件自动删除）"

log "完成。备份定时器："
systemctl --user list-timers "${PROJECT_NAME}-*" --no-pager
