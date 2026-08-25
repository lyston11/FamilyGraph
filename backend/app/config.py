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

# 会话签名密钥：只从环境变量读取，禁止代码内默认值
SECRET_KEY: str = os.environ.get("SECRET_KEY", "")


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
