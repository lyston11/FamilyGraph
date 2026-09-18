"""0051 助手源计时迁移：可空增列、旧行 NULL、拒绝式降级、祖先拒绝先于 DDL。

背景（09-17 D）：`agent_run_events.created_at` 是后端入库时刻，sidecar 默认
250ms 批量 flush，所以同一批内的短阶段（实测 125ms 工具执行）在库里只差约
1.3ms。精确时长必须由 producer 通过 `timing_json` 上报；`run.started` 又由 SDK
`agent_start` 产生，晚于 context 获取与 session 创建，故首次取得执行权的时刻由
`agent_runs.first_leased_at` 单独记录（`lease_expires_at` 会被心跳前移）。

本迁移只加两个 nullable 列，旧行保持 NULL（历史不可还原，不倒推）。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

BACKEND = Path(__file__).parents[1]
HEAD = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini"))).get_current_head()
PARENT = "0050_term_alias_spouse_fix"

# 统计真实执行的 DDL 语句：SQLite DDL 不保证事务回滚，所以"先删列再拒绝"会留下
# 半降级 schema。降级必须证明拒绝发生在任何 DDL 之前。
RUNNER = r"""
import sys
from sqlalchemy import event
from sqlalchemy.engine import Engine
from alembic import command
from alembic.config import Config

ddl = []
@event.listens_for(Engine, 'before_cursor_execute')
def witness(conn, cursor, statement, parameters, context, executemany):
    if statement.lstrip().upper().startswith(('CREATE ', 'ALTER ', 'DROP ')):
        ddl.append(statement.split()[0].upper())
try:
    getattr(command, sys.argv[1])(Config('alembic.ini'), sys.argv[2])
finally:
    print('ACTUAL_ALEMBIC_DDL_COUNT=' + str(len(ddl)))
"""


def _migrate(data_dir, direction, target, *, expect_success=True):
    result = subprocess.run(
        [str(BACKEND / ".venv/bin/python"), "-c", RUNNER, direction, target],
        cwd=BACKEND,
        env={**os.environ, "DATA_DIR": str(data_dir), "PYTHONPATH": str(BACKEND)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    if expect_success:
        assert result.returncode == 0, result.stderr
    return result


def _engine(data_dir):
    return create_engine(f"sqlite:///{data_dir / 'db/app.db'}")


def _columns(engine, table: str) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(table)}


def _head(engine) -> str:
    with engine.connect() as connection:
        return connection.scalar(text("SELECT version_num FROM alembic_version"))


def _seed_timing(engine) -> None:
    """写入一条真实的 producer 计时与一次首次租赁，模拟上线后已有证据。"""
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO agent_runs (id, session_id, job_id, kind, status, attempt, "
                "max_attempts, policy_version, tool_allowlist_json, created_at, updated_at, "
                "first_leased_at) "
                "VALUES (9001, 1, 1, 'assistant', 'running', 1, 3, 'p1', '[]', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO agent_run_events (run_id, seq, type, public_payload, created_at, "
                "timing_json) VALUES (9001, 1, 'run.started', '{}', CURRENT_TIMESTAMP, "
                '\'{"source": "sidecar-v1", "duration_ms": 4200}\')'
            )
        )


def _seed_ancestor_evidence(engine) -> None:
    """写入祖先（0049）自己的拒绝证据：已采用的 attribution。

    0051 的 downgrade 必须让祖先合同先拒绝，否则深层降级会先删掉本迁移两列
    再由祖先拒绝，留下半降级 schema（实测到 0044 时 timing_json 已丢失）。
    """
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO steward_llm_candidates "
                "(id, space_id, job_id, candidate_kind, payload_json, candidate_digest, "
                "status, attribution_status, created_at) "
                "VALUES (41, 1, 1, 'relation_proposal', '{}', 'digest', 'proposed', "
                "'versioned', CURRENT_TIMESTAMP)"
            )
        )


def test_upgrade_adds_nullable_columns_and_keeps_legacy_rows_null(tmp_path):
    """新列可空、旧行 NULL：历史没有源计时，必须报告未知而不是倒推。"""
    _migrate(tmp_path, "upgrade", PARENT)
    engine = _engine(tmp_path)
    try:
        assert "timing_json" not in _columns(engine, "agent_run_events")
        assert "first_leased_at" not in _columns(engine, "agent_runs")
        # 迁移前的历史事件行（只可能有 created_at）
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO agent_run_events (run_id, seq, type, public_payload, created_at) "
                    "VALUES (1, 1, 'run.started', '{}', CURRENT_TIMESTAMP)"
                )
            )

        _migrate(tmp_path, "upgrade", HEAD)

        assert "timing_json" in _columns(engine, "agent_run_events")
        assert "first_leased_at" in _columns(engine, "agent_runs")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT timing_json FROM agent_run_events")) is None
            assert connection.scalar(text("SELECT first_leased_at FROM agent_runs LIMIT 1")) is None
        # 两列都必须可空，否则旧行无法保留。
        for table, column in (
            ("agent_run_events", "timing_json"),
            ("agent_runs", "first_leased_at"),
        ):
            info = next(c for c in inspect(engine).get_columns(table) if c["name"] == column)
            assert info["nullable"] is True
    finally:
        engine.dispose()


def test_downgrade_is_lossless_before_any_evidence(tmp_path):
    """无证据时降级干净移除两列并回到父 revision。"""
    _migrate(tmp_path, "upgrade", HEAD)
    engine = _engine(tmp_path)
    try:
        result = _migrate(tmp_path, "downgrade", PARENT)
        assert "sidecar timing evidence exists" not in result.stderr
        assert _head(engine) == PARENT
        assert "timing_json" not in _columns(engine, "agent_run_events")
        assert "first_leased_at" not in _columns(engine, "agent_runs")
    finally:
        engine.dispose()


@pytest.mark.parametrize("evidence", ["timing_json", "first_leased_at"])
def test_downgrade_refuses_without_destroying_the_only_precise_timing_source(tmp_path, evidence):
    """已有源计时/首次租赁证据时拒绝降级，且先于任何 DDL。"""
    _migrate(tmp_path, "upgrade", HEAD)
    engine = _engine(tmp_path)
    try:
        _seed_timing(engine)
        before = (
            sorted(_columns(engine, "agent_run_events")),
            sorted(_columns(engine, "agent_runs")),
        )

        result = _migrate(tmp_path, "downgrade", PARENT, expect_success=False)

        assert result.returncode != 0
        assert "retain data and roll forward" in result.stderr
        # 拒绝发生在 DDL 之前：schema 与版本都不动。
        assert "ACTUAL_ALEMBIC_DDL_COUNT=0" in result.stdout
        assert _head(engine) == HEAD
        assert (
            sorted(_columns(engine, "agent_run_events")),
            sorted(_columns(engine, "agent_runs")),
        ) == before
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT timing_json FROM agent_run_events")) is not None
    finally:
        engine.dispose()


def test_deep_relative_downgrade_does_not_half_drop_before_ancestor_refusal(tmp_path):
    """祖先拒绝合同必须先于本迁移的 DDL。

    `-4` 会越过 0044 合并分叉：Alembic 在走位阶段即报 "Ambiguous walk"。该错误
    必须在本迁移删列之前出现，否则版本仍停在 0051 而两列已丢失（半降级）。
    """
    _migrate(tmp_path, "upgrade", HEAD)
    engine = _engine(tmp_path)
    try:
        result = _migrate(tmp_path, "downgrade", "-4", expect_success=False)

        assert result.returncode != 0
        assert "ACTUAL_ALEMBIC_DDL_COUNT=0" in result.stdout
        assert _head(engine) == HEAD
        assert "timing_json" in _columns(engine, "agent_run_events")
        assert "first_leased_at" in _columns(engine, "agent_runs")
    finally:
        engine.dispose()


def test_deep_absolute_downgrade_refuses_before_any_ddl(tmp_path):
    """深层绝对降级：祖先拒绝先触发，本迁移不留下半降级 schema。"""
    _migrate(tmp_path, "upgrade", HEAD)
    engine = _engine(tmp_path)
    try:
        _seed_ancestor_evidence(engine)

        result = _migrate(tmp_path, "downgrade", "0044_rag_citation_contract", expect_success=False)

        assert result.returncode != 0
        assert "retain data and roll forward" in result.stderr
        assert "ACTUAL_ALEMBIC_DDL_COUNT=0" in result.stdout
        assert _head(engine) == HEAD
        assert "timing_json" in _columns(engine, "agent_run_events")
        assert "first_leased_at" in _columns(engine, "agent_runs")
    finally:
        engine.dispose()
