"""0050 配偶旁系词方向修复：存量改正、用户词条隔离、升级幂等。

方向真源：`Sm`=丈夫、`Sf`=妻子（services/relationship_resolver.py 的 `_SYM_LETTER`
与既有 `Sm-Um`=公公 / `Sf-Um`=岳父 交叉印证）。旧种子把 `Sm-*` 写成妻子一方，
属数据缺陷而非可回退特征，因此 downgrade 刻意不改回错误文本。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

BACKEND = Path(__file__).parents[1]
HEAD = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini"))).get_current_head()
PRE_FIX = "0049_steward_candidate_evidence"

RUNNER = """
import sys
from alembic import command
from alembic.config import Config
getattr(command, sys.argv[1])(Config('alembic.ini'), sys.argv[2])
"""

# (concept_code, 旧错误文本, 正确文本)
FIXES = (
    ("Sm-Bm", "妻子的兄弟", "丈夫的兄弟"),
    ("Sm-Bf", "妻子的姐妹", "丈夫的姐妹"),
    ("Sf-Bm", "丈夫的兄弟", "妻子的兄弟"),
    ("Sf-Bf", "丈夫的姐妹", "妻子的姐妹"),
)


def _migrate(data_dir, direction, target):
    result = subprocess.run(
        [str(BACKEND / ".venv/bin/python"), "-c", RUNNER, direction, target],
        cwd=BACKEND,
        env={**os.environ, "DATA_DIR": str(data_dir), "PYTHONPATH": str(BACKEND)},
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr


def _engine(data_dir):
    return create_engine(f"sqlite:///{data_dir / 'db/app.db'}")


def _seed_wrong_rows(engine) -> None:
    """把四条行还原成修复前的错误文本，模拟存量生产库。"""
    with engine.begin() as connection:
        for code, wrong, _right in FIXES:
            connection.execute(
                text(
                    "UPDATE term_entries SET term = :wrong "
                    "WHERE level = 'locale' AND locale = 'zh-CN' AND concept_code = :code"
                ),
                {"code": code, "wrong": wrong},
            )


def _seed_user_rows(engine) -> None:
    """写入一条 personal 与一条 space 词条，断言迁移不碰用户数据。"""
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO term_entries "
                "(concept_code, level, space_id, owner_account_id, locale, term, status, "
                "revision, created_at, updated_at) "
                "VALUES ('Sm-Bm', 'personal', NULL, 1, NULL, '我的叫法', 'active', 3, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO term_entries "
                "(concept_code, level, space_id, owner_account_id, locale, term, status, "
                "revision, created_at, updated_at) "
                "VALUES ('Sm-Bm', 'space', 1, NULL, NULL, '空间叫法', 'active', 5, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )


def _spouse_rows(engine) -> dict[str, tuple[str, int]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT concept_code, term, revision FROM term_entries "
                "WHERE level = 'locale' AND locale = 'zh-CN' "
                "AND concept_code IN ('Sm-Bm','Sm-Bf','Sf-Bm','Sf-Bf')"
            )
        ).all()
    return {row[0]: (row[1], row[2]) for row in rows}


def _user_rows(engine) -> list[tuple[str, str, str, int]]:
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                text(
                    "SELECT concept_code, level, term, revision FROM term_entries "
                    "WHERE level IN ('personal','space') ORDER BY level, concept_code"
                )
            ).all()
        ]


def test_existing_wrong_rows_are_corrected_and_user_rows_untouched(tmp_path):
    _migrate(tmp_path, "upgrade", PRE_FIX)
    engine = _engine(tmp_path)
    try:
        _seed_wrong_rows(engine)
        _seed_user_rows(engine)
        before_users = _user_rows(engine)
        before_revisions = {code: rev for code, (_term, rev) in _spouse_rows(engine).items()}

        _migrate(tmp_path, "upgrade", HEAD)

        after = _spouse_rows(engine)
        for code, _wrong, right in FIXES:
            term, revision = after[code]
            assert term == right, f"{code} 应为 {right}，实际 {term}"
            # revision 前移才能让词条版本哈希与已发布 PFV 失效重算。
            assert revision == before_revisions[code] + 1
        assert _user_rows(engine) == before_users
    finally:
        engine.dispose()


def test_upgrade_is_idempotent_when_rows_already_correct(tmp_path):
    _migrate(tmp_path, "upgrade", HEAD)
    engine = _engine(tmp_path)
    try:
        first = _spouse_rows(engine)
        for code, _wrong, right in FIXES:
            assert first[code][0] == right
        # 再次升级到 head 不应重复改词或再推 revision。
        _migrate(tmp_path, "upgrade", HEAD)
        assert _spouse_rows(engine) == first
    finally:
        engine.dispose()


def test_downgrade_keeps_corrected_terms(tmp_path):
    """方向错误是数据缺陷；降级不得把它改回错误方向。"""
    _migrate(tmp_path, "upgrade", PRE_FIX)
    engine = _engine(tmp_path)
    try:
        _seed_wrong_rows(engine)
        _migrate(tmp_path, "upgrade", HEAD)
        corrected = _spouse_rows(engine)

        _migrate(tmp_path, "downgrade", PRE_FIX)

        assert _spouse_rows(engine) == corrected
    finally:
        engine.dispose()


@pytest.mark.parametrize("code", [code for code, _w, _r in FIXES])
def test_wrong_direction_never_reappears(code, tmp_path):
    """回归护栏：四条词在全新库上必须与编码方向一致。"""
    _migrate(tmp_path, "upgrade", HEAD)
    engine = _engine(tmp_path)
    try:
        expected = {c: right for c, _wrong, right in FIXES}
        assert _spouse_rows(engine)[code][0] == expected[code]
    finally:
        engine.dispose()
