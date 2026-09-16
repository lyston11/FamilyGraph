#!/usr/bin/env bash
# 备份 FamilyGraph 运行数据到私有 GitHub 仓库 lyston11/familygraph-backups 并推送。
#
# 收集内容：
#   - db/app-<时间戳>.db.gz            SQLite 热备（python sqlite3 backup API，对 WAL 安全、不锁库）
#   - uploads/uploads-<时间戳>.tar.gz  附件目录归档
#   - secrets/familygraph.env          部署密钥副本（chmod 600，私有仓库）
#
# 保留策略：30 天轮转。由服务器 systemd 用户定时器 familygraph-db-backup（每 5 小时）无人值守调用。
# 数据三处存放：服务器 ↔ GitHub ↔ 本机；单文件 100MB 上限由轮转与附件体积兜底。
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${FAMILYGRAPH_BACKUP_DIR:-$HOME/familygraph-backups}"
RETENTION_DAYS=30
STAMP="$(date '+%Y%m%d-%H%M%S')"
LOG_TAG="[familygraph-backup]"
DATA_DIR="$REPO_ROOT/backend/data"
ENV_FILE="$HOME/.config/familygraph/familygraph.env"

mkdir -p "$BACKUP_DIR"
cd "$BACKUP_DIR"

if [ ! -d .git ]; then
    git clone git@github.com:lyston11/familygraph-backups.git "$BACKUP_DIR"
fi
if git rev-parse --verify -q HEAD >/dev/null; then
    git pull --rebase --autostash origin main
fi

mkdir -p db uploads secrets

# 1. SQLite 热备（backup API 逐页复制，含 WAL 中未落盘内容）
if [ -f "$DATA_DIR/db/app.db" ]; then
    "$REPO_ROOT/backend/.venv/bin/python" - "$DATA_DIR/db/app.db" "db/app-$STAMP.db" <<'PY'
import sqlite3
import sys

src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src)
o = sqlite3.connect(dst)
try:
    s.backup(o)
finally:
    s.close()
    o.close()
PY
    gzip -f "db/app-$STAMP.db"
    echo "$LOG_TAG sqlite backup: db/app-$STAMP.db.gz"
else
    echo "$LOG_TAG sqlite: $DATA_DIR/db/app.db 不存在，跳过"
fi

# 2. 附件目录归档（空目录跳过，避免空 tar 噪音）
if [ -d "$DATA_DIR/uploads" ] && [ -n "$(ls -A "$DATA_DIR/uploads" 2>/dev/null)" ]; then
    tar -czf "uploads/uploads-$STAMP.tar.gz" -C "$DATA_DIR" uploads
    echo "$LOG_TAG uploads archive: uploads/uploads-$STAMP.tar.gz"
fi

# 3. 部署密钥副本（私有仓库；不需要时删除这一段）
if [ -f "$ENV_FILE" ]; then
    install -m 600 "$ENV_FILE" "secrets/familygraph.env"
    echo "$LOG_TAG secrets: secrets/familygraph.env"
fi

# 4. 轮转
find db uploads -type f -mtime +$RETENTION_DAYS -delete 2>/dev/null || true

# 5. 无变化则跳过提交
if [ -z "$(git status --porcelain)" ]; then
    echo "$LOG_TAG nothing new to back up"
    exit 0
fi
git add -A
git commit -m "backup: $(date '+%F %T')"
git push origin main
echo "$LOG_TAG pushed backup to GitHub"
