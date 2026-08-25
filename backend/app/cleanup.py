"""孤儿文件清扫：uploads 中无 attachments 记录引用的文件。

用法：python -m app.cleanup
"""

from __future__ import annotations

from app.db import SessionLocal
from app.services.attachments import sweep_orphans


def main() -> None:
    session = SessionLocal()
    try:
        removed = sweep_orphans(session)
        print(f"swept {removed} orphan file(s)")
    finally:
        session.close()


if __name__ == "__main__":
    main()
