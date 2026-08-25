"""SQLAlchemy engine/session 与 SQLite PRAGMA 统一设置。

启动序列（architecture.md §5）：create engine → connect 事件钩子执行四项 PRAGMA。
WAL 文件与主库同目录（同一数据卷），满足 AD-6 的备份前提。
"""

from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app import config

engine: Engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """每个新建连接统一执行 architecture.md §5 的四项 PRAGMA。"""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
