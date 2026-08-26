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
