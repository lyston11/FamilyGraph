"""时间工具：全库统一 UTC naive datetime（SQLite 文本排序安全）。"""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """当前 UTC 时间，naive（去 tzinfo）以保证 SQLite 存储与比较的一致性。"""
    return datetime.now(UTC).replace(tzinfo=None)
