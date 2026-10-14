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

# agent/dist 是构建产物（被 agent/.gitignore 忽略），**不随 git pull 更新**，
# 而 sidecar 跑的是 `node dist/main.js`。源码更新后若不重建，sidecar 会继续跑
# 旧代码——实测后果是后端下发的工具白名单在 sidecar 侧「不存在」，
# run 直接 SIDECAR_ERROR、前端显示「助手服务暂时不可用」。
#
# 放在 pull **之后**：判据是「输入是否比 dist 新」，拉取恰好会更新输入。
# fresh 时零开销（只做 mtime 比较）；构建失败不阻断同步——代码同步与产物构建
# 是两件事，后者失败要留痕但不能让 push 也失败（push 是唯一的代码备份路径）。
if [ -x scripts/agent-build-if-stale.sh ]; then
    if scripts/agent-build-if-stale.sh; then
        echo "$LOG_TAG agent dist up to date"
    else
        echo "$LOG_TAG WARN agent dist build failed; sidecar may run stale code" >&2
    fi
fi

git push origin main
echo "$LOG_TAG pushed to GitHub"
