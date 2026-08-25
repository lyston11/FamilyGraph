"""数据库契约验证：四项 PRAGMA 必须在每个连接上生效（architecture.md §5）。"""

from sqlalchemy import text

from app.db import engine


def test_sqlite_pragmas_applied_on_connect() -> None:
    with engine.connect() as connection:
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
        busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()
        synchronous = connection.execute(text("PRAGMA synchronous")).scalar_one()

    assert foreign_keys == 1
    assert str(journal_mode) == "wal"
    assert int(busy_timeout) == 5000
    assert int(synchronous) == 1  # NORMAL
