#!/usr/bin/env bash
# 线上环境发布（fg.lyston.qzz.io，compose 项目 familygraph-prod）。
#
# 把「备份 → 拉取 → 重建 → 迁移核对 → 健康校验 → 失败回滚」固化为一条命令，
# 消除两个已踩过的坑：
#   1) 代码 pull 到磁盘 ≠ 服务加载（迁移在容器 CMD 里跑，失败会 crash-loop）；
#   2) 迁移前进后回滚代码会造成代码与库不一致，比停在当前状态更危险。
#
# 用法（必须在线上工作目录 /home/ubuntu/fg-prod 内执行）：
#   bash scripts/deploy-prod.sh                 # 发布 origin/main 最新
#   bash scripts/deploy-prod.sh <sha|ref>       # 发布/回滚到指定目标
#   bash scripts/deploy-prod.sh --no-rollback   # 校验失败只报告，不自动回滚
#   bash scripts/deploy-prod.sh --skip-backup   # 跳过发布前备份（不推荐）
#   bash scripts/deploy-prod.sh --force-dir     # 允许在非默认目录执行
#
# 为什么默认先备份：发布是唯一会同时动代码与库的操作，出事时快照是唯一的
# 数据锚点。走 app.backup（SQLite online backup API + integrity_check），
# 禁止运行期 cp WAL 主库。
#
# 为什么迁移前进时拒绝自动回滚：回滚代码不会回滚库，结果是「旧代码 + 新 schema」，
# 比「新代码起不来」更难诊断。此时脚本停下并要求人工决策。
#
# 本脚本不读取、不打印任何密钥值；所有实际动作都委托给既有原语：
#   scripts/install-prod-automation.sh（构建/启动/等 healthy）
#   compose exec api python -m app.backup（备份）
set -euo pipefail

PROJECT_NAME="familygraph-prod"
PROD_DIR_DEFAULT="$HOME/fg-prod"
ENV_FILE="$HOME/.config/familygraph/familygraph-prod.env"
PUBLIC_HEALTH_URL="https://fg.lyston.qzz.io/api/health"
ADMIN_LEAK_PROBE_URL="https://fg.lyston.qzz.io/admin-api/health"
API_IMAGE="familygraph-prod-api:0.1.0"
COMPOSE_FILES=(-f docker-compose.yml -f deploy/production/docker-compose.prod.yml)

TARGET="origin/main"
ROLLBACK_ALLOWED=1
SKIP_BACKUP=0
FORCE_DIR=0

# ---- 输出与日志 ----
LOG_FILE="$(mktemp -t fg-deploy-prod.XXXXXX.log)"
log()  { printf '\033[1;36m[deploy-prod]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[deploy-prod]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[deploy-prod]\033[0m %s\n' "$*" >&2; exit 1; }

PREV_SHA=""
TARGET_SHA=""
PREV_ALEMBIC=""
BACKUP_FILE=""
NOOP_RELEASE=0
ROLLED_BACK=0

# ---- 参数 ----
while [ $# -gt 0 ]; do
    case "$1" in
        --no-rollback) ROLLBACK_ALLOWED=0 ;;
        --skip-backup) SKIP_BACKUP=1 ;;
        --force-dir)   FORCE_DIR=1 ;;
        -h|--help)
            sed -n '2,25p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        -*) fail "未知参数：$1（用 --help 查看用法）" ;;
        *)  TARGET="$1" ;;
    esac
    shift
done

compose() { docker compose -p "$PROJECT_NAME" --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" "$@"; }

# 在 api 运行时环境里执行 python 代码。
# 关键：容器 crash-loop 时 compose exec 会失败（"is restarting, wait until the
# container is running"），而此时恰恰最需要读库/备份。所以退化到一次性容器
# 直接挂数据卷，与 read_alembic 的兜底同理。实测触发点：AC5-C 注入启动失败后
# 重跑发布，备份步骤因 api 在 crash-loop 而中止，脚本无法自我恢复。
run_in_api() {
    if compose exec -T api "$@" 2>/dev/null; then
        return 0
    fi
    docker run --rm -e DATA_DIR=/data -w /app \
        -v "${PROJECT_NAME}_app_data:/data" "$API_IMAGE" "$@"
}

# 读取线上库的 alembic 版本。两级兜底：优先问正在运行的 api 容器；容器起不来时
# 用一次性容器直接挂数据卷读文件（不依赖任何服务 running，也不依赖宿主有 sqlite3）。
read_alembic() {
    local q="import sqlite3;print(sqlite3.connect('/data/db/app.db').execute('select version_num from alembic_version').fetchone()[0])"
    local out=""
    out="$(compose exec -T api python -c "$q" 2>/dev/null | tr -d '\r' | tail -1 || true)"
    if [ -z "$out" ]; then
        out="$(docker run --rm -v "${PROJECT_NAME}_app_data:/data" "$API_IMAGE" \
            python -c "$q" 2>/dev/null | tr -d '\r' | tail -1 || true)"
    fi
    printf '%s' "$out"
}

# 等待四个容器全部 healthy（有界）。
# 为什么需要等待：install-prod-automation.sh 只等 api healthy；web/admin-web/agent
# 的 healthcheck 有 start_period=10s、interval=30s，在 api 变 healthy 之后仍会
# 处于 starting 数十秒。立即断言会稳定误报（首次实现就踩到了）。
wait_all_healthy() {
    local timeout="${FG_DEPLOY_HEALTH_TIMEOUT:-180}" waited=0 snapshot=""
    while [ "$waited" -lt "$timeout" ]; do
        snapshot="$(compose ps -a --format '{{.Service}}|{{.State}}|{{.Health}}' 2>/dev/null || true)"
        if [ "$(printf '%s\n' "$snapshot" | grep -c '|running|healthy$')" -eq 4 ]; then
            return 0
        fi
        # 任一容器已退出就不必再等（等下去也不会自愈）
        if printf '%s\n' "$snapshot" | grep -q '|exited|'; then
            return 1
        fi
        sleep 5
        waited=$((waited + 5))
    done
    return 1
}

# 发布后校验：全部通过返回 0；任一失败打印原因并返回 1。
verify_release() {
    local ok=1

    # 4a 四个容器 running + healthy（有界等待）
    if ! wait_all_healthy; then
        warn "4a 容器未在时限内全部 healthy："
        compose ps -a --format '{{.Service}}|{{.State}}|{{.Health}}' 2>/dev/null >&2 || true
        ok=0
    else
        log "4a 容器全部 healthy（4 个）"
    fi

    # 4b 线上库迁移版本 == 镜像内 head
    # 优先用运行中的容器（工作目录正确）；容器起不来时用一次性容器在 /app 里跑
    # alembic（镜像内含 alembic.ini 与 migrations/，实测可用）。
    local db_ver head_ver
    db_ver="$(read_alembic || true)"
    head_ver="$(compose exec -T api alembic heads 2>/dev/null \
        | awk '/\(head\)/{print $1; exit}' || true)"
    if [ -z "$head_ver" ]; then
        head_ver="$(docker run --rm -w /app "$API_IMAGE" alembic heads 2>/dev/null \
            | awk '/\(head\)/{print $1; exit}' || true)"
    fi
    if [ -z "$db_ver" ] || [ -z "$head_ver" ]; then
        warn "4b 无法读取迁移版本（db='$db_ver' head='$head_ver'）"; ok=0
    elif [ "$db_ver" != "$head_ver" ]; then
        warn "4b 库迁移版本与代码 head 不一致：db=$db_ver head=$head_ver"; ok=0
    else
        log "4b 迁移版本一致：$db_ver"
    fi

    # 4c 公网家庭端健康
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$PUBLIC_HEALTH_URL" || true)"
    if [ "$code" = "200" ]; then
        log "4c 公网 $PUBLIC_HEALTH_URL → 200"
    else
        warn "4c 公网健康检查失败：$PUBLIC_HEALTH_URL → $code"; ok=0
    fi

    # 4d 后台不外泄（家庭端必须普通 404）
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$ADMIN_LEAK_PROBE_URL" || true)"
    if [ "$code" = "404" ]; then
        log "4d 后台未外泄：家庭端 /admin-api/health → 404"
    else
        warn "4d 后台隔离回归：/admin-api/health → $code（期望 404）"; ok=0
    fi

    [ "$ok" = 1 ]
}

report() {
    local status="$1"
    echo
    log "──────── 发布摘要 ────────"
    log "  结果        : $status"
    log "  发布前 sha  : ${PREV_SHA:-未知}"
    log "  目标 sha    : ${TARGET_SHA:-未知}"
    log "  当前 sha    : $(git rev-parse --short HEAD 2>/dev/null || echo 未知)"
    log "  迁移前      : ${PREV_ALEMBIC:-未知}"
    log "  迁移后      : $(read_alembic 2>/dev/null || echo 未知)"
    [ -n "$BACKUP_FILE" ] && log "  发布前备份  : $BACKUP_FILE"
    [ "$NOOP_RELEASE" = 1 ] && log "  说明        : 目标与当前一致（no-op release，仅复验）"
    [ "$ROLLED_BACK" = 1 ] && log "  说明        : 已自动回滚到发布前版本"
    log "  详细日志    : $LOG_FILE"
    log "──────────────────────────"
}

# ---- 0. 前置校验（fail-fast：任何一条不过都不产生副作用）----
EXPECTED_DIR="${FG_PROD_DIR:-$PROD_DIR_DEFAULT}"
if [ "$(pwd -P)" != "$(cd "$EXPECTED_DIR" 2>/dev/null && pwd -P)" ]; then
    if [ "$FORCE_DIR" != 1 ]; then
        fail "必须在线上工作目录 ${EXPECTED_DIR} 内执行（当前 $(pwd -P)）。
     这是防止误把开发 checkout 发布上线的硬校验；确实要换目录时用 --force-dir。"
    fi
    warn "--force-dir：在非默认目录 $(pwd -P) 执行（期望 $EXPECTED_DIR）"
fi

[ -f deploy/production/docker-compose.prod.yml ] || \
    fail "当前目录不是线上工作目录（缺 deploy/production/docker-compose.prod.yml）"
[ -f scripts/install-prod-automation.sh ] || fail "缺 scripts/install-prod-automation.sh"
[ -f "$ENV_FILE" ] || fail "缺 $ENV_FILE"
[ "$(stat -c '%a' "$ENV_FILE")" = "600" ] || fail "$ENV_FILE 权限必须为 600"
command -v docker >/dev/null || fail "缺少 docker"

# 工作区必须干净：否则 checkout 会覆盖或带着未提交改动发布
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    fail "工作区有未提交改动，先提交或丢弃：
$(git status --short --untracked-files=no)"
fi

log "拉取 origin/main…"
git fetch -q origin main

TARGET_SHA="$(git rev-parse --short "${TARGET}^{commit}" 2>/dev/null)" || \
    fail "无法解析目标版本：$TARGET"
TARGET_FULL="$(git rev-parse "${TARGET}^{commit}")"

# 目标必须是 origin/main 的祖先：禁止发布未推送的本地提交
if ! git merge-base --is-ancestor "$TARGET_FULL" origin/main; then
    fail "目标 $TARGET_SHA 不是 origin/main 的祖先（未推送的提交不能发布上线）"
fi

PREV_SHA="$(git rev-parse --short HEAD)"
PREV_ALEMBIC="$(read_alembic || true)"
[ -n "$PREV_ALEMBIC" ] || fail "读不到线上库迁移版本；服务可能未正常运行，先排查再发布"

[ "$TARGET_SHA" = "$PREV_SHA" ] && NOOP_RELEASE=1

log "发布前状态：sha=$PREV_SHA alembic=$PREV_ALEMBIC → 目标 sha=$TARGET_SHA"

trap 'status=$?; if [ $status -ne 0 ]; then warn "发布未成功结束（exit=$status）；日志 $LOG_FILE"; fi' EXIT

# ---- 1. 发布前备份（默认必做；失败即中止）----
if [ "$SKIP_BACKUP" = 1 ]; then
    warn "--skip-backup：跳过发布前备份（出问题时无数据锚点）"
else
    log "发布前备份（online backup API）…"
    if ! run_in_api python -m app.backup >> "$LOG_FILE" 2>&1; then
        fail "发布前备份失败，中止发布（不做无备份的发布）；日志 $LOG_FILE"
    fi
    BACKUP_FILE="$(run_in_api sh -c \
        'ls -1t /data/backups/familygraph-*.db 2>/dev/null | head -1' | tr -d '\r')"
    log "备份完成：${BACKUP_FILE:-（未找到快照文件，请检查日志）}"
fi

# ---- 2. 切换代码 ----
# 默认目标（origin/main）走分支快进，让线上 checkout 始终停在 main 上，
# 避免每次发布都留一个无基线的 detached HEAD。显式指定 sha 时才 detached。
# 切换失败必须给出可操作的诊断：ff-only 失败通常意味着线上检出与 origin/main
# 分岔（例如被人为改写过远端历史），盲目继续会发布出来源不明的代码。
switch_code() {
    if [ "$TARGET" = "origin/main" ]; then
        log "切换代码到 origin/main（分支快进）…"
        git checkout -q main || return 1
        if ! git merge --ff-only origin/main 2>/dev/null; then
            warn "无法快进到 origin/main：本地 main 与远端分岔"
            warn "本地: $(git rev-parse --short HEAD)  远端: $(git rev-parse --short origin/main)"
            warn "处理：确认本地无未推送改动后用 git reset --hard origin/main 对齐"
            return 1
        fi
    else
        log "切换代码到 $TARGET_SHA（detached，用于回滚或定版）…"
        git checkout -q "$TARGET_FULL" || return 1
    fi
    return 0
}

if ! switch_code; then
    warn "代码切换失败，未做任何部署动作（容器未改动）"
    report "失败（代码切换失败）"
    exit 1
fi

# ---- 3. 重建并启动（委托给既有安装器；捕获退出码而不让它直接终结脚本）----
log "重建并启动（scripts/install-prod-automation.sh，输出见日志）…"
set +e
bash scripts/install-prod-automation.sh 2>&1 | tee -a "$LOG_FILE"
INSTALLER_RC="${PIPESTATUS[0]}"
set -e

# ---- 4/5. 校验，失败则按安全闸门决定是否回滚 ----
if [ "$INSTALLER_RC" -ne 0 ]; then
    warn "安装器退出码 $INSTALLER_RC"
    VERIFY_FAILED_REASON="安装器失败"
else
    VERIFY_FAILED_REASON=""
fi

if [ -z "$VERIFY_FAILED_REASON" ] && verify_release; then
    report "成功"
    exit 0
fi

# 走到这里说明失败（安装器失败或后置校验失败）
[ -n "$VERIFY_FAILED_REASON" ] || VERIFY_FAILED_REASON="后置校验失败"
warn "$VERIFY_FAILED_REASON"

# 安全闸门：迁移是否已前进
POST_ALEMBIC="$(read_alembic || true)"
if [ -z "$POST_ALEMBIC" ]; then
    warn "读不到发布后的迁移版本（容器可能起不来），无法判定迁移是否前进"
    warn "保守处理：不自动回滚代码，请人工确认库版本后再决定"
    report "失败（需人工处置）"
    exit 1
fi
if [ "$POST_ALEMBIC" != "$PREV_ALEMBIC" ]; then
    warn "线上库迁移版本已前进：$PREV_ALEMBIC → $POST_ALEMBIC"
    warn "拒绝自动回滚代码：库已升级而回滚代码会造成「旧代码 + 新 schema」，"
    warn "比停在当前状态更难诊断，且可能损坏数据。人工处置选项："
    warn "  1) 修复问题并发布到更新的 commit（推荐）"
    warn "  2) 用发布前快照恢复库后再回滚代码：${BACKUP_FILE:-（本次未备份）}"
    warn "  3) 确认真实可接受数据丢失时，自行评估并手工 downgrade（本脚本刻意不自动执行）"
    report "失败（迁移已前进，需人工处置）"
    exit 1
fi

if [ "$ROLLBACK_ALLOWED" != 1 ]; then
    warn "--no-rollback：跳过自动回滚，保持当前状态供排查"
    report "失败（未回滚）"
    exit 1
fi

if [ "$NOOP_RELEASE" = 1 ]; then
    warn "目标与发布前是同一版本，无版本可回退；请直接排查当前部署"
    report "失败（no-op 发布，无可回退版本）"
    exit 1
fi

# 迁移未前进 → 可以安全地回滚代码。
# 回滚用 detached checkout 而不是把 main 往回移：本项目规则禁止改写 main 历史，
# 且发布检出不应该成为 main 的第二个写入者。下次发布会 checkout main 恢复。
log "迁移未前进（仍为 $PREV_ALEMBIC），自动回滚到 $PREV_SHA…"
git checkout -q "$PREV_SHA"
if bash scripts/install-prod-automation.sh >> "$LOG_FILE" 2>&1 && verify_release >> "$LOG_FILE" 2>&1; then
    ROLLED_BACK=1
    log "已回滚到 $PREV_SHA，服务恢复 healthy"
    report "失败但已自动回滚"
    exit 1
fi

warn "自动回滚未能恢复健康，需要人工介入"
warn "排障起点：$LOG_FILE"
report "失败（回滚未成功）"
exit 1
