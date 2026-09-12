#!/usr/bin/env bash
# 服务器侧代码自动同步：有改动自动 commit → rebase → push（GitHub 为代码中枢）。
# 由服务器 systemd 用户定时器 familygraph-code-sync（每 30 分钟，Persistent=true）无人值守调用。
# 本地 Mac 不装自动 commit：未完成的工作不会被推送；push 即代码备份。
set -euo pipefail
cd "$(dirname "$0")/.."

LOG_TAG="[familygraph-code-sync]"

if [ -z "$(git status --porcelain)" ]; then
    echo "$LOG_TAG no local changes"
else
    git add -A
    git commit -m "chore(auto): server code sync $(date '+%F %T')"
    echo "$LOG_TAG committed pending changes"
fi

git pull --rebase --autostash origin main
git push origin main
echo "$LOG_TAG pushed to GitHub"
