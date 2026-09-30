"""SQLAlchemy engine/session 与 SQLite PRAGMA 统一设置。

启动序列（architecture.md §5）：create engine → connect 事件钩子执行四项 PRAGMA。
WAL 文件与主库同目录（同一数据卷），满足 AD-6 的备份前提。
"""

from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app import config

# 连接池容量：显式声明（此前依赖 QueuePool 默认值）。
#
# 这两个数字**必须**与「共享工作线程的并发准入」保持一致：连接池（15）远小于
# AnyIO 工作线程池（40），因此一旦连接池耗尽，等待连接的请求会占住工作线程直到
# `POOL_TIMEOUT_SECONDS`。40 个这样的请求即耗尽全部工作线程，使心跳、lease、
# 健康检查等**所有**端点一起失效（09-30 实测复现：40 个请求全部耗时精确等于
# pool_timeout，期间 anyio borrowed/total 持续 40/40）。
# 所以工具执行并发必须留在 `POOL_MAX_CONNECTIONS` 之内并留出余量。
POOL_SIZE = 5
POOL_MAX_OVERFLOW = 10
POOL_TIMEOUT_SECONDS = 30.0
POOL_MAX_CONNECTIONS = POOL_SIZE + POOL_MAX_OVERFLOW

engine: Engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False},
    pool_size=POOL_SIZE,
    max_overflow=POOL_MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT_SECONDS,
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
