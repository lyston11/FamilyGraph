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

DATABASE_URL: str = f"sqlite:///{DB_PATH}"

# Token TTL（AD-2：access 2h / refresh 30d）；m0b 认证实现消费
ACCESS_TOKEN_TTL_SECONDS: int = 2 * 60 * 60
REFRESH_TOKEN_TTL_SECONDS: int = 30 * 24 * 60 * 60

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
# context 端点返回的最近消息投影条数上限
AGENT_CONTEXT_MESSAGE_LIMIT: int = int(os.environ.get("AGENT_CONTEXT_MESSAGE_LIMIT", "50"))
# 浏览器创建 Assistant Run 的默认策略版本与消息正文上限（RT-4 content 校验）
AGENT_POLICY_VERSION: str = os.environ.get("AGENT_POLICY_VERSION", "v2-agent-runtime-1")
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
STEWARD_MAX_ATTEMPTS: int = int(os.environ.get("STEWARD_MAX_ATTEMPTS", "3"))
# 卡片有效期与 dismissed 后同 kind 冷却天数（ST-4 有效期 / ST-3 不重复骚扰）
STEWARD_CARD_TTL_DAYS: int = int(os.environ.get("STEWARD_CARD_TTL_DAYS", "14"))
STEWARD_COOLDOWN_DAYS: int = int(os.environ.get("STEWARD_COOLDOWN_DAYS", "7"))

# ---- V2.5 Memory / RAG / Policy Guard（可独立回滚，默认关闭 Memory/RAG）----
# 关闭 Memory 时不产生候选/确认记忆；RAG 关闭时保留结构化 Assistant 工具路径，
# 不把会话全文作为隐式补偿 Context。Policy Guard 关闭时 fail-closed，不向 Provider 发送请求。
MEMORY_ENABLED: bool = os.environ.get("MEMORY_ENABLED", "").lower() in ("1", "true")
RAG_ENABLED: bool = os.environ.get("RAG_ENABLED", "").lower() in ("1", "true")
BEHAVIOR_PROJECTION_ENABLED: bool = os.environ.get("BEHAVIOR_PROJECTION_ENABLED", "").lower() in (
    "1",
    "true",
)
POLICY_GUARD_ENABLED: bool = os.environ.get("POLICY_GUARD_ENABLED", "1").lower() in ("1", "true")


def ensure_data_dirs() -> None:
    """确保数据卷目录存在（db/uploads/backups），幂等。"""
    for directory in (DB_PATH.parent, UPLOADS_DIR, BACKUPS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def ensure_ready() -> None:
    """启动前校验：SECRET_KEY 必须由环境变量提供，否则拒绝启动。"""
    if not SECRET_KEY.strip():
        raise RuntimeError(
            "SECRET_KEY 未设置：请通过环境变量提供会话签名密钥（拒绝以弱默认值启动）"
        )
    ensure_data_dirs()
