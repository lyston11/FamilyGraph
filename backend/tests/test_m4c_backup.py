"""m4c 备份/恢复演练测试（AD-6）：online backup → restore → integrity + 行数一致。"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from conftest import create_user_with_pin

from app.backup import create_backup, verify_restore


def test_backup_restore_roundtrip(db_session, tmp_path: Path):
    # 造数据：3 用户 1 关系
    a = create_user_with_pin(db_session, "甲", "111111", claim_status="claimed")
    b = create_user_with_pin(db_session, "乙", "222222", claim_status="claimed")
    c = create_user_with_pin(db_session, "丙", "333333", claim_status="claimed")
    from app.models.relation import Relation
    from app.utils.timeutil import utcnow

    now = utcnow()
    db_session.add(
        Relation(
            from_user=a.id,
            to_user=b.id,
            dir_class="elder",
            created_by=a.id,
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()

    snapshot, archive = create_backup()
    assert snapshot.exists() and archive.exists()

    # 恢复到临时库并校验
    restore_path = tmp_path / "restored.db"
    shutil.copy(snapshot, restore_path)
    counts = verify_restore(restore_path)
    assert counts["users"] == 3
    assert counts["relations"] == 1
    assert counts["accounts"] == 3

    void = c
    del void


def test_backup_excludes_wal_corruption_risk():
    """快照文件可独立打开（非 WAL 半成品）：integrity_check 由 verify_restore 保证。"""
    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE TABLE t (x)")
        con.commit()
        # online backup 的产物总是完整数据库文件——此处仅验证 API 可用性语义
        dst = sqlite3.connect(":memory:")
        con.backup(dst)
        assert dst.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
        dst.close()
    finally:
        con.close()
