"""集中配置：数据卷路径、密钥、Token TTL。

所有环境变量读取集中在本模块（m0a design：配置集中 config.py）。
"""

import os
from pathlib import Path

# 数据根目录：容器内为 /data（见 docker-compose.yml），本地开发默认 ./data
DATA_DIR: Path = Path(os.environ.get("DATA_DIR", "./data"))

DB_PATH: Path = DATA_DIR / "db" / "app.db"
UPLOADS_DIR: Path = DATA_DIR / "uploads"
BACKUPS_DIR: Path = DATA_DIR / "backups"
# 部署 bootstrap 凭据文件目录（0600 一次性交付；SF-F3）
BOOTSTRAP_DIR: Path = DATA_DIR / "bootstrap"

DATABASE_URL: str = f"sqlite:///{DB_PATH}"

# Token TTL（AD-2：access 2h / refresh 30d）；m0b 认证实现消费
ACCESS_TOKEN_TTL_SECONDS: int = 2 * 60 * 60
REFRESH_TOKEN_TTL_SECONDS: int = 30 * 24 * 60 * 60

# ---- 09-04 独立 Admin API listener 与管理员 JWT 签发域（SF-F1/SF-F4）----
# admin listener 默认 127.0.0.1 fail-closed：compose 部署显式绑定 admin 内部网络接口。
ADMIN_API_PORT: int = int(os.environ.get("ADMIN_API_PORT", "8002"))
ADMIN_API_HOST: str = os.environ.get("ADMIN_API_HOST", "127.0.0.1")
# 管理员 access 短效（15 分钟）；refresh 轮换但受绝对有效期约束（轮换不续期）
ADMIN_ACCESS_TOKEN_TTL_SECONDS: int = int(os.environ.get("ADMIN_ACCESS_TOKEN_TTL_SECONDS", "900"))
ADMIN_REFRESH_TOKEN_TTL_SECONDS: int = int(
    os.environ.get("ADMIN_REFRESH_TOKEN_TTL_SECONDS", str(12 * 60 * 60))
)
# 独立签发域：只从环境变量读取，缺失/过弱/与家庭 SECRET_KEY 相同即拒绝启动，
# 绝不回退默认值（SF-F4；校验见 ensure_ready/ensure_admin_ready）。
ADMIN_JWT_SECRET: str = os.environ.get("ADMIN_JWT_SECRET", "")
ADMIN_JWT_ISSUER: str = os.environ.get("ADMIN_JWT_ISSUER", "")
ADMIN_JWT_AUDIENCE: str = os.environ.get("ADMIN_JWT_AUDIENCE", "")
ADMIN_JWT_SECRET_MIN_LENGTH: int = 32
# bootstrap 初始密码长度（secrets.token_urlsafe 字节数 → ~24 可见字符）
ADMIN_BOOTSTRAP_PASSWORD_BYTES: int = 18

# ---- 09-05 家庭账号开通与注册（PRD 决策 2/6；design.md §3/§4）----
# 部署开关：默认开；关闭时注册端点与未知路径同形 404（不给探测信号），等于整体回滚开关
REGISTRATION_ENABLED: bool = os.environ.get("REGISTRATION_ENABLED", "1").lower() in ("1", "true")
# 注册端点 IP 滑窗限流（进程内；compose 单 API 进程无横向扩容，多实例再落共享存储）
REGISTRATION_RATE_LIMIT_MAX_ATTEMPTS: int = int(
    os.environ.get("REGISTRATION_RATE_LIMIT_MAX_ATTEMPTS", "10")
)
REGISTRATION_RATE_LIMIT_WINDOW_SECONDS: int = int(
    os.environ.get("REGISTRATION_RATE_LIMIT_WINDOW_SECONDS", "900")
)

# ---- 09-05 dev 演示数据种子（固定清单增量补缺；默认关闭）----
# 仅显式 "1" 开启；开启时按固定清单 insert-only 收敛播种（缺什么补什么，既有
# 行零改动，收敛规则见 app/dev_seed.py）。演示 PIN 统一 123456（公开 dev 演示值，
# PRD 红线允许日志）。
DEV_SEED_DEMO_DATA: str = os.environ.get("DEV_SEED_DEMO_DATA", "0")

# ---- m0b 认证限流参数（design.md 回滚形态：集中在 config，可经 env 热调）----
AUTH_MAX_FAILED_ATTEMPTS: int = int(os.environ.get("AUTH_MAX_FAILED_ATTEMPTS", "5"))
AUTH_LOCK_MINUTES: int = int(os.environ.get("AUTH_LOCK_MINUTES", "15"))
AUTH_CHALLENGE_TTL_MINUTES: int = int(os.environ.get("AUTH_CHALLENGE_TTL_MINUTES", "5"))
# 关闭锁定仅允许开发态使用；生产误配时启动日志给 WARNING 二次确认线索
AUTH_LOCKOUT_DISABLED: bool = os.environ.get("AUTH_LOCKOUT_DISABLED", "").lower() in ("1", "true")

# bcrypt cost：生产 12；测试经 env 降到 4 保证套件速度
BCRYPT_ROUNDS: int = int(os.environ.get("BCRYPT_ROUNDS", "12"))

# 会话签名密钥：只从环境变量读取，禁止代码内默认值
SECRET_KEY: str = os.environ.get("SECRET_KEY", "")

# ---- v2 Foundation 命令层参数（design.md：短期/过期合同集中在 config）----
# owner onboarding link 有效期（短期、单次、可撤销；§0.5）
OWNER_INVITATION_TTL_MINUTES: int = int(os.environ.get("OWNER_INVITATION_TTL_MINUTES", "1440"))
# owner 移交 pending 惰性过期时限（ownership_transfers FSM expired 终态）
OWNERSHIP_TRANSFER_TTL_HOURS: int = int(os.environ.get("OWNERSHIP_TRANSFER_TTL_HOURS", "168"))
# 自助导出文件下载有效期（有过期下载，§0.6）
DATA_EXPORT_TTL_HOURS: int = int(os.environ.get("DATA_EXPORT_TTL_HOURS", "24"))
# 可见性策略版本：数据权利请求快照所用口径（异步结果继承 VisibilityPolicy）
POLICY_VERSION: str = "v2-foundation-1"

# ---- V2.3 Relationship Intelligence（Block E1 起：feature flag 默认关闭）----
# E4 用它门禁关系智能新端点/Agent 工具；本块只加配置。关闭时不影响既有 v1 结构边读写。
RELATIONSHIP_INTELLIGENCE_ENABLED: bool = os.environ.get(
    "RELATIONSHIP_INTELLIGENCE_ENABLED", ""
).lower() in ("1", "true")

# ---- V2.1 Agent Runtime（RT-6：feature flag 总开关，默认整体关闭）----
# 关闭时 /internal/agent/* 一律 503；开启仍要求 AGENT_SERVICE_SECRET 配置，否则 fail-closed
AGENT_RUNTIME_ENABLED: bool = os.environ.get("AGENT_RUNTIME_ENABLED", "").lower() in ("1", "true")
# 首版云模型 profile 门禁：生产只允许与本机 Pi 配置一致的 liu-dada/gpt-5.6-sol。
# 该门禁不可由运行环境关闭；测试夹具如需合成 Provider，必须在进程内显式
# monkeypatch 该常量，避免把一个部署环境变量误当成安全开关。
AGENT_PROVIDER_STANDARD_PROFILE_ONLY: bool = True
# sidecar 与 FastAPI 共享的 HMAC 签名密钥（service/run token）；未配置时内部协议全部拒绝
AGENT_SERVICE_SECRET: str = os.environ.get("AGENT_SERVICE_SECRET", "")
# service token 仅用于 lease（notes.md 两级认证）；run token 绑定 run/scope，exp 上限 600s
AGENT_SERVICE_TOKEN_TTL_SECONDS: int = int(os.environ.get("AGENT_SERVICE_TOKEN_TTL_SECONDS", "120"))
AGENT_RUN_TOKEN_TTL_SECONDS_MAX: int = 600
AGENT_RUN_TOKEN_TTL_SECONDS: int = int(os.environ.get("AGENT_RUN_TOKEN_TTL_SECONDS", "600"))
# lease 时长与重试上限（reaper 按 lease_expires_at 回队/判死）
AGENT_LEASE_TTL_SECONDS: int = int(os.environ.get("AGENT_LEASE_TTL_SECONDS", "300"))
AGENT_MAX_ATTEMPTS: int = int(os.environ.get("AGENT_MAX_ATTEMPTS", "3"))
# RT-2：每账户最多两个并发 Assistant Run
AGENT_ACCOUNT_ASSISTANT_RUN_LIMIT: int = int(
    os.environ.get("AGENT_ACCOUNT_ASSISTANT_RUN_LIMIT", "2")
)
# Context projection deliberately returns the complete durable transcript;
# there is no recent-N truncation knob because truncation would break Pi
# session rehydration and make answers depend on an arbitrary environment cap.
# 浏览器创建 Assistant Run 的默认策略版本与消息正文上限（RT-4 content 校验）
AGENT_POLICY_VERSION: str = os.environ.get("AGENT_POLICY_VERSION", "v2-agent-runtime-1")
# ProviderGateway 代理（唯一 egress）：sidecar 经 internal 代理端点调用云端
# Provider，真实凭据与外网 egress 全部留在 api 容器内（P1 收口）
AGENT_PROVIDER_PROXY_TIMEOUT_SECONDS: int = int(
    os.environ.get("AGENT_PROVIDER_PROXY_TIMEOUT_SECONDS", "300")
)
AGENT_PROVIDER_PROXY_CONNECT_TIMEOUT_SECONDS: int = int(
    os.environ.get("AGENT_PROVIDER_PROXY_CONNECT_TIMEOUT_SECONDS", "10")
)
AGENT_PROVIDER_PROXY_MAX_BYTES: int = int(
    os.environ.get("AGENT_PROVIDER_PROXY_MAX_BYTES", str(10 * 1024 * 1024))
)
AGENT_MESSAGE_MAX_LENGTH: int = int(os.environ.get("AGENT_MESSAGE_MAX_LENGTH", "8000"))
# SSE 实时通知的兜底轮询间隔与心跳间隔（跨进程 append 不在本进程注册表内，靠轮询兜底；
# 重连回放始终以 DB 为准保证不漏序）
AGENT_SSE_POLL_SECONDS: float = float(os.environ.get("AGENT_SSE_POLL_SECONDS", "0.5"))
AGENT_SSE_KEEPALIVE_SECONDS: float = float(os.environ.get("AGENT_SSE_KEEPALIVE_SECONDS", "15"))

# ---- V2.4 Steward 与 ActionCard（Block S1：feature flag 默认关闭）----
# 关闭时 enqueue/run 入口一律 503 STEWARD_DISABLED（回滚形态：scheduler 独立开关）
STEWARD_ENABLED: bool = os.environ.get("STEWARD_ENABLED", "").lower() in ("1", "true")
# lease 时长与重试上限（reaper 按 lease_expires_at 回队/判死）
STEWARD_LEASE_TTL_SECONDS: int = int(os.environ.get("STEWARD_LEASE_TTL_SECONDS", "300"))
# 进程内 Steward worker 泵（与 STEWARD_ENABLED 双开关；测试/单进程默认关）
STEWARD_WORKER_ENABLED: bool = os.environ.get("STEWARD_WORKER_ENABLED", "").lower() in ("1", "true")
# 后台维护循环周期（agent reaper + steward pump）
MAINTENANCE_INTERVAL_SECONDS: float = float(os.environ.get("MAINTENANCE_INTERVAL_SECONDS", "5"))
STEWARD_MAX_ATTEMPTS: int = int(os.environ.get("STEWARD_MAX_ATTEMPTS", "3"))
# 卡片有效期与 dismissed 后同 kind 冷却天数（ST-4 有效期 / ST-3 不重复骚扰）
STEWARD_CARD_TTL_DAYS: int = int(os.environ.get("STEWARD_CARD_TTL_DAYS", "14"))
STEWARD_COOLDOWN_DAYS: int = int(os.environ.get("STEWARD_COOLDOWN_DAYS", "7"))
# ---- 09-11 Steward 建议审核投影（candidate-review）----
# 建议有效期与按收件人驳回冷却天数（同证据版本内不重复骚扰）
STEWARD_SUGGESTION_TTL_DAYS: int = int(os.environ.get("STEWARD_SUGGESTION_TTL_DAYS", "30"))
STEWARD_SUGGESTION_COOLDOWN_DAYS: int = int(os.environ.get("STEWARD_SUGGESTION_COOLDOWN_DAYS", "7"))
# ---- 09-06 Steward 模型辅助层（候选/排序/解释；平台级 per-kind 开关，默认全关）----
# 有效开关 = 平台级（此处）AND 空间级（agent_space_provider_settings.assist_* 列，
# owner 经空间模型设置设置）。全部关闭时 Steward 行为与确定性基线逐字节等价。
STEWARD_ASSIST_CANDIDATE: bool = os.environ.get("STEWARD_ASSIST_CANDIDATE", "").lower() in (
    "1",
    "true",
)
STEWARD_ASSIST_RANKING: bool = os.environ.get("STEWARD_ASSIST_RANKING", "").lower() in ("1", "true")
STEWARD_ASSIST_EXPLANATION: bool = os.environ.get("STEWARD_ASSIST_EXPLANATION", "").lower() in (
    "1",
    "true",
)
# 09-13 称谓自主优化（terminology）：默认关闭；默认关闭时确定性称谓继续可用
STEWARD_ASSIST_TERMINOLOGY: bool = os.environ.get("STEWARD_ASSIST_TERMINOLOGY", "").lower() in (
    "1",
    "true",
)
# 每 job 预算与上限（超限 skip 并留审计行，绝不拖垮确定性流水线）
STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB: int = int(
    os.environ.get("STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB", "6")
)
STEWARD_ASSIST_MAX_TOKENS_PER_JOB: int = int(
    os.environ.get("STEWARD_ASSIST_MAX_TOKENS_PER_JOB", "20000")
)
STEWARD_ASSIST_TIMEOUT_SECONDS: float = float(
    os.environ.get("STEWARD_ASSIST_TIMEOUT_SECONDS", "30")
)
STEWARD_ASSIST_MAX_CARDS_PER_JOB: int = int(os.environ.get("STEWARD_ASSIST_MAX_CARDS_PER_JOB", "5"))
# ---- 09-11 辅助批次执行限制（R5：字节/并发/墙钟均有上界）----
# prompt 明文字节上界（超限不发送，记 skipped prompt_too_large）
STEWARD_ASSIST_MAX_PROMPT_BYTES: int = int(
    os.environ.get("STEWARD_ASSIST_MAX_PROMPT_BYTES", str(64 * 1024))
)
# HTTP 响应体流式读取字节上界（超限中止读取，记 failed response_too_large）
STEWARD_ASSIST_MAX_RESPONSE_BYTES: int = int(
    os.environ.get("STEWARD_ASSIST_MAX_RESPONSE_BYTES", str(256 * 1024))
)
# 辅助批次 lease 时长（同时是单批总墙钟 deadline；lease 过期由恢复收敛）
STEWARD_ASSIST_BATCH_LEASE_SECONDS: int = int(
    os.environ.get("STEWARD_ASSIST_BATCH_LEASE_SECONDS", "120")
)
# 全局并发批次上界（1=串行；调度器一次至多 lease 一个未过期批次）
# terminology 有界目标（每 job 至多 2 个 viewer 组、每组至多 8 个目标）
STEWARD_TERMINOLOGY_MAX_VIEWER_GROUPS_PER_JOB: int = int(
    os.environ.get("STEWARD_TERMINOLOGY_MAX_VIEWER_GROUPS_PER_JOB", "2")
)
STEWARD_TERMINOLOGY_MAX_TARGETS_PER_GROUP: int = int(
    os.environ.get("STEWARD_TERMINOLOGY_MAX_TARGETS_PER_GROUP", "8")
)
STEWARD_ASSIST_MAX_CONCURRENT_BATCHES: int = int(
    os.environ.get("STEWARD_ASSIST_MAX_CONCURRENT_BATCHES", "1")
)

# ---- 09-13 Steward 推测层（inferred tree；fail-closed 默认关）----
# 推测边 = LLM 候选经管家作业投影的「建议关系」，只在 PFV 以虚线/角标呈现，
# 绝不写 confirmed 事实。有效开关 = 平台级（此处）AND 空间级
# （agent_space_provider_settings.inferred_tree 列）；任一关闭时投影与 PFV
# 推测区块均不发生，行为与现状逐字节等价（回滚形态 = 关开关）。
STEWARD_INFERRED_TREE_ENABLED: bool = os.environ.get(
    "STEWARD_INFERRED_TREE_ENABLED", ""
).lower() in ("1", "true")
# 单空间活跃（proposed）推测边上限：投影与 PFV 图增广共用，created_at 升序截断
STEWARD_INFERRED_MAX_ACTIVE_PER_SPACE: int = int(
    os.environ.get("STEWARD_INFERRED_MAX_ACTIVE_PER_SPACE", "50")
)

# ---- 09-11 Steward 生产调度（周期扫描与有限恢复；正数 + 上界校验见 ensure_ready）----
# 空间周期扫描间隔（首次启用/重新启用/policy_version 变化时该空间被置为立即到期追补）
STEWARD_SCAN_INTERVAL_SECONDS: int = int(os.environ.get("STEWARD_SCAN_INTERVAL_SECONDS", "300"))
# 单个扫描 tick 最多登记的 core job 数（防止单 tick 长时间占住事件循环外线程）
STEWARD_SCAN_MAX_JOBS_PER_TICK: int = int(os.environ.get("STEWARD_SCAN_MAX_JOBS_PER_TICK", "10"))
# 可重试失败的有限退避（第 1/2 次重试前等待秒数；max_attempts 沿用上方 3）
STEWARD_RETRY_BACKOFF_FIRST_SECONDS: int = int(
    os.environ.get("STEWARD_RETRY_BACKOFF_FIRST_SECONDS", "5")
)
STEWARD_RETRY_BACKOFF_SECOND_SECONDS: int = int(
    os.environ.get("STEWARD_RETRY_BACKOFF_SECOND_SECONDS", "30")
)
# 管理员单空间重跑冷却（8002 POST rerun；0 允许运维态关闭冷却）
STEWARD_RERUN_COOLDOWN_SECONDS: int = int(os.environ.get("STEWARD_RERUN_COOLDOWN_SECONDS", "60"))
# 队列积压告警阈值（秒；status API 的 alerts 输出）。0 = 自动 = max(2×扫描间隔, 60)。
# 仅产生可见告警，不改变任何调度/执行行为（09-11 发布可观测性 R2/AC-4）。
STEWARD_ALERT_QUEUE_SECONDS: int = int(os.environ.get("STEWARD_ALERT_QUEUE_SECONDS", "0"))

# PersonalFamilyView/bridge API and projection; default disabled for safe rollout.
PERSONAL_FAMILY_VIEW_ENABLED: bool = os.environ.get("PERSONAL_FAMILY_VIEW_ENABLED", "").lower() in (
    "1",
    "true",
)

# ---- V2.5 Memory / RAG / Policy Guard（可独立回滚，默认关闭 Memory/RAG）----
# 关闭 Memory 时不产生候选/确认记忆；RAG 关闭时保留结构化 Assistant 工具路径，
# 不把会话全文作为隐式补偿 Context。Policy Guard 关闭时 fail-closed，不向 Provider 发送请求。
MEMORY_ENABLED: bool = os.environ.get("MEMORY_ENABLED", "").lower() in ("1", "true")
RAG_ENABLED: bool = os.environ.get("RAG_ENABLED", "").lower() in ("1", "true")
BEHAVIOR_PROJECTION_ENABLED: bool = os.environ.get("BEHAVIOR_PROJECTION_ENABLED", "").lower() in (
    "1",
    "true",
)


# Policy Guard 关闭时 fail-closed，不向 Provider 发送请求。
POLICY_GUARD_ENABLED: bool = os.environ.get("POLICY_GUARD_ENABLED", "1").lower() in ("1", "true")

# ---- V2.6 Controlled Web（平台与空间双重开关，默认关闭）----
# FastAPI 是唯一 egress 边界；即使空间/平台配置打开，global flag 关闭也拒绝。
CONTROLLED_WEB_ENABLED: bool = os.environ.get("CONTROLLED_WEB_ENABLED", "").lower() in (
    "1",
    "true",
)
CONTROLLED_WEB_TOKEN_TTL_SECONDS: int = int(
    os.environ.get("CONTROLLED_WEB_TOKEN_TTL_SECONDS", "300")
)
CONTROLLED_WEB_CONNECT_TIMEOUT_SECONDS: float = float(
    os.environ.get("CONTROLLED_WEB_CONNECT_TIMEOUT_SECONDS", "5")
)
CONTROLLED_WEB_READ_TIMEOUT_SECONDS: float = float(
    os.environ.get("CONTROLLED_WEB_READ_TIMEOUT_SECONDS", "15")
)


def ensure_data_dirs() -> None:
    """确保数据卷目录存在（db/uploads/backups/bootstrap），幂等。"""
    for directory in (DB_PATH.parent, UPLOADS_DIR, BACKUPS_DIR, BOOTSTRAP_DIR):
        directory.mkdir(parents=True, exist_ok=True)


# 部署安全（design.md §2.1）：拒绝已知弱默认密钥，除非显式 DEV_ALLOW_WEAK_SECRETS。
DEV_ALLOW_WEAK_SECRETS: bool = os.environ.get("DEV_ALLOW_WEAK_SECRETS", "").lower() in (
    "1",
    "true",
)
_WEAK_SECRETS = {
    "dev-secret-change-me",
    "dev-agent-secret-change-me",
    "change-me",
    "secret",
    "",
}


def _reject_weak_default_secrets() -> None:
    """弱默认密钥防线：SECRET_KEY 或启用的 Agent 协议密钥命中已知弱值即拒启。"""
    if DEV_ALLOW_WEAK_SECRETS:
        return
    if SECRET_KEY.strip() in _WEAK_SECRETS:
        raise RuntimeError(
            "SECRET_KEY 命中已知弱默认值：请提供真实随机密钥，"
            "或仅开发态显式设置 DEV_ALLOW_WEAK_SECRETS=1"
        )
    if MAINTENANCE_INTERVAL_SECONDS <= 0:
        raise RuntimeError("MAINTENANCE_INTERVAL_SECONDS 必须为正数")
    _validate_steward_scheduling()
    if AGENT_RUNTIME_ENABLED and AGENT_SERVICE_SECRET in _WEAK_SECRETS:
        raise RuntimeError(
            "AGENT_SERVICE_SECRET 命中已知弱默认值：请提供真实随机密钥，"
            "或仅开发态显式设置 DEV_ALLOW_WEAK_SECRETS=1"
        )


def _validate_steward_scheduling() -> None:
    """09-11 生产调度参数校验：正数 + 上界（防止误配产生无限/负退避）。"""
    bounds: tuple[tuple[str, int, int, int], ...] = (
        ("STEWARD_SCAN_INTERVAL_SECONDS", STEWARD_SCAN_INTERVAL_SECONDS, 1, 86400),
        ("STEWARD_SCAN_MAX_JOBS_PER_TICK", STEWARD_SCAN_MAX_JOBS_PER_TICK, 1, 100),
        ("STEWARD_RETRY_BACKOFF_FIRST_SECONDS", STEWARD_RETRY_BACKOFF_FIRST_SECONDS, 0, 3600),
        ("STEWARD_RETRY_BACKOFF_SECOND_SECONDS", STEWARD_RETRY_BACKOFF_SECOND_SECONDS, 0, 3600),
        ("STEWARD_RERUN_COOLDOWN_SECONDS", STEWARD_RERUN_COOLDOWN_SECONDS, 0, 3600),
        ("STEWARD_ALERT_QUEUE_SECONDS", STEWARD_ALERT_QUEUE_SECONDS, 0, 86400),
        ("STEWARD_ASSIST_MAX_PROMPT_BYTES", STEWARD_ASSIST_MAX_PROMPT_BYTES, 1024, 1 << 20),
        ("STEWARD_ASSIST_MAX_RESPONSE_BYTES", STEWARD_ASSIST_MAX_RESPONSE_BYTES, 1024, 1 << 22),
        ("STEWARD_ASSIST_BATCH_LEASE_SECONDS", STEWARD_ASSIST_BATCH_LEASE_SECONDS, 5, 3600),
        ("STEWARD_ASSIST_MAX_CONCURRENT_BATCHES", STEWARD_ASSIST_MAX_CONCURRENT_BATCHES, 1, 8),
        ("STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB", STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB, 1, 64),
        ("STEWARD_ASSIST_MAX_TOKENS_PER_JOB", STEWARD_ASSIST_MAX_TOKENS_PER_JOB, 100, 1_000_000),
        ("STEWARD_ASSIST_MAX_CARDS_PER_JOB", STEWARD_ASSIST_MAX_CARDS_PER_JOB, 1, 100),
        ("STEWARD_INFERRED_MAX_ACTIVE_PER_SPACE", STEWARD_INFERRED_MAX_ACTIVE_PER_SPACE, 1, 500),
    )
    if not (0.1 <= STEWARD_ASSIST_TIMEOUT_SECONDS <= 300):
        raise RuntimeError(
            f"STEWARD_ASSIST_TIMEOUT_SECONDS 必须在 [0.1, 300] 内，当前为 "
            f"{STEWARD_ASSIST_TIMEOUT_SECONDS}"
        )
    for name, value, low, high in bounds:
        if not low <= value <= high:
            raise RuntimeError(f"{name} 必须在 [{low}, {high}] 区间内（当前 {value}）")


def ensure_admin_ready() -> None:
    """管理员 JWT 签发域校验（SF-F4：缺失/过弱一律 fail-closed，无开发逃逸）。

    与家庭 SECRET_KEY 不同，ADMIN_* 不接受 DEV_ALLOW_WEAK_SECRETS 放行：
    管理员令牌与家庭令牌的签发域隔离是本任务的安全底线，弱配置等价于
    两个 listener 共享密钥。issuer/audience 也必须显式提供且互不相同。
    """
    if not ADMIN_JWT_SECRET.strip():
        raise RuntimeError(
            "ADMIN_JWT_SECRET 未设置：请通过环境变量提供管理员 JWT 签名密钥"
            "（拒绝以默认值或家庭 SECRET_KEY 启动）"
        )
    if len(ADMIN_JWT_SECRET.strip()) < ADMIN_JWT_SECRET_MIN_LENGTH:
        raise RuntimeError(
            f"ADMIN_JWT_SECRET 过弱：长度不得少于 {ADMIN_JWT_SECRET_MIN_LENGTH} 个字符"
        )
    if ADMIN_JWT_SECRET.strip() == SECRET_KEY.strip():
        raise RuntimeError("ADMIN_JWT_SECRET 不得与家庭 SECRET_KEY 相同：签发域必须隔离")
    if not ADMIN_JWT_ISSUER.strip() or not ADMIN_JWT_AUDIENCE.strip():
        raise RuntimeError(
            "ADMIN_JWT_ISSUER / ADMIN_JWT_AUDIENCE 未设置：管理员 JWT 必须显式声明签发域"
        )
    if ADMIN_JWT_ISSUER.strip() == ADMIN_JWT_AUDIENCE.strip():
        raise RuntimeError("ADMIN_JWT_ISSUER 与 ADMIN_JWT_AUDIENCE 不得相同")


def ensure_ready() -> None:
    """启动前校验：SECRET_KEY 必须由环境提供且非弱默认，否则拒绝启动。"""
    if not SECRET_KEY.strip():
        raise RuntimeError(
            "SECRET_KEY 未设置：请通过环境变量提供会话签名密钥（拒绝以弱默认值启动）"
        )
    _reject_weak_default_secrets()
    ensure_admin_ready()
    ensure_data_dirs()
