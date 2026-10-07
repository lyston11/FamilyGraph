"""SQLAlchemy engine/session 与 SQLite PRAGMA 统一设置。

启动序列（architecture.md §5）：create engine → connect 事件钩子执行四项 PRAGMA。
WAL 文件与主库同目录（同一数据卷），满足 AD-6 的备份前提。
"""

import types
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

#: 是否使用 SQLite。**方言判定必须集中在这里**：连接参数、PRAGMA、事务语义都
#: 依赖它，散落判断会让「加了 PostgreSQL 支持但某处仍按 SQLite 处理」变成静默缺陷。
IS_SQLITE = config.DATABASE_URL.startswith("sqlite")

#: 连接参数按方言区分。
#:
#: `check_same_thread=False` 是 **SQLite 专属**：psycopg 不接受该参数，传了会直接
#: 报 `ProgrammingError`。因此不能无条件传。
_connect_args: dict[str, Any] = {"check_same_thread": False} if IS_SQLITE else {}

engine: Engine = create_engine(
    config.DATABASE_URL,
    connect_args=_connect_args,
    pool_size=POOL_SIZE,
    max_overflow=POOL_MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT_SECONDS,
    # 连接健康检查：长连接在 PG 侧可能已被服务器关闭（idle timeout、failover）。
    # 不检查会在复用时拿到死连接并报错；`pre_ping` 让池自动丢弃并重建。
    # SQLite 无此问题（进程内文件），因此只在 PG 上启用。
    pool_pre_ping=not IS_SQLITE,
)


@event.listens_for(engine, "connect")
def set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """每个新建连接统一执行 architecture.md §5 的四项 PRAGMA。

    **只在 SQLite 上执行**：PostgreSQL 上 `PRAGMA` 是未知语法，直接报错。
    """
    if not IS_SQLITE:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


# ---- 连接池等待测量（10-01）----
#
# 包装 `pool.connect`：池满时等待**正好**发生在这里（内部 `_do_get` 阻塞在
# `queue.get(wait, timeout)`），因此这段耗时就是「等了多久」。不 hook 私有方法、
# 不改池实现，也覆盖所有取连接的路径（Session 第一次 execute、engine.connect）。
#
# 为何要在产品内测：池满与「事件循环被同步代码占住」在现象上都是「请求变慢」，
# 但处置完全不同。没有这个指标时只能靠外部探针 + py-spy 事后拼接，09-30 已因此
# 两次把 token 过期误判成别的原因。
def _instrument_pool_wait() -> None:
    from app.services import runtime_diagnostics

    original = engine.pool.connect

    def timed_connect(self: Any) -> Any:
        started = runtime_diagnostics.now_seconds()
        try:
            return original()
        finally:
            # 必须在 finally 里记录：池满时 `original()` 会**抛** TimeoutError，
            # 而「池满到超时」正是最需要被观测的情形。只在成功路径记录会让
            # 最严重的情况恰好没有日志。
            runtime_diagnostics.note_pool_wait(runtime_diagnostics.now_seconds() - started, self)

    # mypy 把池实例上的方法赋值视为 method-assign；这里是有意的运行时插桩
    # （SQLAlchemy 的池对象就是普通实例），因此显式忽略该检查。
    engine.pool.connect = types.MethodType(timed_connect, engine.pool)  # type: ignore[method-assign]


_instrument_pool_wait()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
