#!/usr/bin/env bash
# 按需重建 agent sidecar 的构建产物（`agent/dist`）。
#
# ## 为什么需要这个脚本
#
# `agent/dist/` 是 **TypeScript 构建产物**，被 `agent/.gitignore` 忽略，
# 因此它**不随 `git pull` 更新**。而 sidecar 的运行命令是
# `node dist/main.js`——即跑的是产物，不是源码。
#
# 后果（2026-10-14 实测）：`agent/src` 更新了工具注册表，`dist/` 仍是几天前的
# 旧产物，于是后端下发的工具白名单在 sidecar 侧「不存在」：
#
#     context allowlist contains tools not available to assistant:
#       familygraph.propose_memory, familygraph.search_memory, ...
#     → run failed: SIDECAR_ERROR → 前端显示「助手服务暂时不可用」
#
# 这个缺陷形态极隐蔽：
#   - 代码审查看不出（源码是对的）；
#   - 单测全过（vitest 跑的是 `src/`，不是 `dist/`）；
#   - 只有真实发消息才暴露，且错误信息指向「工具不可用」而非「产物过期」。
#
# ## 判据是「是否过期」而不是「是否存在」
#
# 旧检查只断言 `dist` 目录存在（`dev-up.sh` 与 systemd unit 都如此），
# 那对「存在但过期」完全无感。本脚本比较 **`src/` 与配置文件的 mtime** 与
# `dist/main.js`，只在过期时重建——fresh 时零开销。
#
# ## 用法
#
#     scripts/agent-build-if-stale.sh            # 过期才构建
#     scripts/agent-build-if-stale.sh --force    # 无条件构建
#     scripts/agent-build-if-stale.sh --check    # 只检查，不构建（过期时退出码 1）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENT_DIR="$ROOT/agent"
DIST_ENTRY="$AGENT_DIR/dist/main.js"

MODE="stale"
case "${1:-}" in
    --force) MODE="force" ;;
    --check) MODE="check" ;;
    "") ;;
    *) printf '用法: %s [--force|--check]\n' "$0" >&2; exit 2 ;;
esac

log() { printf '\033[1;36m[agent-build]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[agent-build]\033[0m %s\n' "$*" >&2; }

[ -d "$AGENT_DIR" ] || { warn "缺 $AGENT_DIR"; exit 1; }

# 过期判据：任何构建输入比 dist/main.js 新。
#
# 输入清单必须包含**所有**影响产物的文件：
#   - src/（源码）
#   - package.json / package-lock.json（依赖版本变化会改变产物）
#   - tsconfig.json / tsconfig.build.json（编译目标变化会改变产物）
# 漏掉任何一个都会造成「改了但没重建」的同一类缺陷。
stale_inputs() {
    [ -f "$DIST_ENTRY" ] || { echo "$DIST_ENTRY"; return; }
    find \
        "$AGENT_DIR/src" \
        "$AGENT_DIR/package.json" \
        "$AGENT_DIR/package-lock.json" \
        "$AGENT_DIR/tsconfig.json" \
        "$AGENT_DIR/tsconfig.build.json" \
        -newer "$DIST_ENTRY" 2>/dev/null | head -5
}

STALE_FILES="$(stale_inputs || true)"

if [ -z "$STALE_FILES" ] && [ "$MODE" != "force" ]; then
    if [ "$MODE" = "check" ]; then
        log "dist 是最新的（$DIST_ENTRY）"
    fi
    exit 0
fi

if [ "$MODE" = "check" ]; then
    warn "dist 已过期，需要重建：$DIST_ENTRY"
    printf '%s\n' "$STALE_FILES" | sed 's/^/  比 dist 新: /' >&2
    exit 1
fi

if [ -n "$STALE_FILES" ]; then
    log "dist 已过期（以下输入比 $DIST_ENTRY 新）："
    printf '%s\n' "$STALE_FILES" | sed 's/^/  /'
else
    log "--force：无条件重建"
fi

[ -d "$AGENT_DIR/node_modules" ] || {
    warn "缺 agent/node_modules：先 cd agent && npm install"
    exit 1
}

log "构建 agent sidecar（npm run build）…"
if (cd "$AGENT_DIR" && npm run build); then
    log "构建完成：$(ls -la "$DIST_ENTRY" | awk '{print $6, $7, $8}')"
else
    warn "构建失败——dist 可能仍是旧产物，sidecar 会继续跑旧代码"
    exit 1
fi

# 构建后再次校验：产物必须真的比输入新。防「构建命令成功但没写产物」这类
# 静默失败（例如 tsconfig 的 outDir 被改错）。
if [ -n "$(stale_inputs || true)" ]; then
    warn "构建后 dist 仍比输入旧——检查 tsconfig 的 outDir 是否指向 dist"
    exit 1
fi
