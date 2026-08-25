"""备份（AD-6）：SQLite online backup API 产出一致性快照，与 uploads 一并 tar 归档。

用法：python -m app.backup
禁止：运行期直接 cp 主库文件（WAL 模式下会产生不一致快照）。
"""

from __future__ import annotations

import sqlite3
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from app.config import BACKUPS_DIR, DB_PATH, UPLOADS_DIR


def create_backup() -> tuple[Path, Path]:
    """执行备份，返回 (db 快照路径, tar 归档路径)。"""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    snapshot_path = BACKUPS_DIR / f"familygraph-{stamp}.db"

    # online backup API：源连接 -> 目标空文件，保证一致性
    src = sqlite3.connect(str(DB_PATH))
    dst = sqlite3.connect(str(snapshot_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    # integrity 自检
    check = sqlite3.connect(str(snapshot_path))
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        raise RuntimeError(f"backup integrity_check failed: {result}")

    archive_path = BACKUPS_DIR / f"familygraph-{stamp}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(snapshot_path, arcname=snapshot_path.name)
        if UPLOADS_DIR.exists():
            for f in UPLOADS_DIR.iterdir():
                if f.is_file():
                    tar.add(f, arcname=f"uploads/{f.name}")
    return snapshot_path, archive_path


def verify_restore(restored_db_path: Path) -> dict[str, int]:
    """恢复演练校验：integrity_check + 行数统计。"""
    con = sqlite3.connect(str(restored_db_path))
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        counts: dict[str, int] = {}
        for table in ("users", "accounts", "relations", "space_members", "attachments"):
            counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return counts
    finally:
        con.close()


def main() -> None:
    snapshot, archive = create_backup()
    counts = verify_restore(snapshot)
    print(f"snapshot : {snapshot}")
    print(f"archive  : {archive}")
    print(f"row counts: {counts}")


if __name__ == "__main__":
    main()
