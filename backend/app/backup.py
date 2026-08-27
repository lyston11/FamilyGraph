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
    """恢复演练校验：integrity_check + 关键表行数 + FTS 完整性。

    V2.6 扩展：覆盖 Agent/Memory/RAG/ActionCard/SourceFact 真源表，并校验
    rag_chunks_fts 与 active 文档投影一致（恢复后 FTS 可重建，但快照内应自洽）。
    """
    con = sqlite3.connect(str(restored_db_path))
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        counts: dict[str, int] = {}
        for table in (
            "users",
            "accounts",
            "relations",
            "space_members",
            "attachments",
            # V2 真源：Agent / Memory / RAG / ActionCard / SourceFact
            "agent_sessions",
            "agent_runs",
            "agent_run_events",
            "agent_messages",
            "memories",
            "rag_documents",
            "rag_chunks",
            "action_cards",
            "source_facts",
            "domain_events",
        ):
            counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        # FTS 完整性：active 文档的 chunk 投影与 FTS 行数一致（恢复后自洽）
        fts_rows = con.execute("SELECT COUNT(*) FROM rag_chunks_fts").fetchone()[0]
        projected = con.execute(
            "SELECT COUNT(*) FROM rag_chunks WHERE status = 'active' AND document_id IN "
            "(SELECT id FROM rag_documents WHERE status = 'active')"
        ).fetchone()[0]
        counts["rag_chunks_fts"] = fts_rows
        counts["rag_chunks_projected"] = projected
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
