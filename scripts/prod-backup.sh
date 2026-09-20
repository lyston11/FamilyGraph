#!/usr/bin/env bash
# FamilyGraph 线上库备份（由 systemd 用户定时器 familygraph-prod-backup.timer 调用）。
#
# 为什么不在宿主上直接读库文件：线上库在 Docker named volume 内，宿主路径由
# Docker 管理；且 SQLite 运行在 WAL 模式，运行期直接 cp 会产生不一致快照
# （spec: architecture/8--8-wal-ad-6）。因此一律走容器内的 `python -m app.backup`
# ——它使用 SQLite online backup API 逐页复制并做 integrity_check 自检。
#
# 产物：线上数据卷 /data/backups/familygraph-<时间戳>.db 与同名 .tar.gz
# 保留：30 天（与本仓库开发侧的 scripts/server-backup.sh 口径一致）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_NAME="familygraph-prod"
ENV_FILE="$HOME/.config/familygraph/familygraph-prod.env"
RETENTION_DAYS=30
LOG_TAG="[familygraph-prod-backup]"

# systemd **user** manager 可能启动于用户加入 docker 组之前（本机就是：user
# manager 自 2026-07-27 常驻，而 docker 组在那之后才生效），其派生的单元环境
# 不含 docker 组，docker CLI 会报 permission denied。检测到不可用时用 sg 在
# docker 组下重新执行自身；FG_PROD_BACKUP_REEXEC 保证只发生一次。手工运行
# （shell 已在 docker 组）不会走这条路径。
if ! docker ps -q >/dev/null 2>&1; then
    if [ "${FG_PROD_BACKUP_REEXEC:-}" != "1" ] && command -v sg >/dev/null 2>&1; then
        export FG_PROD_BACKUP_REEXEC=1
        exec sg docker -c "$(printf '%q ' "$0" "$@")"
    fi
    echo "$LOG_TAG 无法访问 docker（需要当前用户在 docker 组内）" >&2
    exit 1
fi

compose() {
    docker compose -p "$PROJECT_NAME" --env-file "$ENV_FILE" \
        -f "$REPO_ROOT/docker-compose.yml" \
        -f "$REPO_ROOT/deploy/production/docker-compose.prod.yml" "$@"
}

if ! compose ps --status running --format '{{.Service}}' 2>/dev/null | grep -qx api; then
    echo "$LOG_TAG api 容器未运行，跳过本次备份" >&2
    exit 1
fi

compose exec -T api python -m app.backup

compose exec -T api sh -c \
    "find /data/backups -maxdepth 1 -name 'familygraph-*' -mtime +$RETENTION_DAYS -delete"

echo "$LOG_TAG 备份完成（保留 ${RETENTION_DAYS} 天）"
