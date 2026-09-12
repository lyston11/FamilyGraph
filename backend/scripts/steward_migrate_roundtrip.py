#!/usr/bin/env python3
"""Steward 迁移往返验证（09-11 R5/AC-5）。

隔离性：全程临时 DATA_DIR（tempfile.mkdtemp），绝不触碰业务库；也绝不运行
tests/conftest.py 的 ``downgrade base``（本脚本只在与 Steward 相关的迁移边界
往返，base 是业务库永不该到达的迁移原点）。

验证三段：
1. 空库往返：upgrade head → downgrade <pre-steward> → upgrade head（无数据，
   只验迁移链可执行、新表/索引随 up 重建）；
2. 旧数据上行：在 pre-steward 版本插入"旧数据"（用户/账号/空间/成员/
   confirmed SourceFact/StewardJob/ActionCard 行）→ upgrade head → 旧行保留、
   新表出现；
3. 下行再上行：head → downgrade pre-steward（Steward 新表删除、旧数据保留）
   → upgrade head（新表/索引重建、旧数据仍保留）。

pre-steward 边界动态确定：定位 0036_steward_production_scheduling 迁移文件的
down_revision（不硬编码 revision 字符串；文件名前缀在脚本常量中声明，实施时
head 为 0038_steward_suggestions，与五个依赖任务交付一致）。

证据：stdout 每步打印行数校验结果，任何断言失败非零退出。

用法：cd backend && .venv/bin/python scripts/steward_migrate_roundtrip.py
"""

from __future__ import annotations

import glob
import importlib.util
import os
import subprocess
import tempfile
from pathlib import Path

# ---- 环境必须在导入 app 之前就绪（与 tests/conftest.py 同一合同）----
_TMP = tempfile.mkdtemp(prefix="familygraph-steward-migrate-")
os.environ["DATA_DIR"] = _TMP
os.environ.setdefault("SECRET_KEY", "migrate-secret-key-not-a-real-secret")
os.environ.setdefault("BCRYPT_ROUNDS", "4")
os.environ.setdefault("ADMIN_JWT_SECRET", "migrate-admin-jwt-secret-0123456789abcdef")
os.environ.setdefault("ADMIN_JWT_ISSUER", "familygraph-admin-migrate")
os.environ.setdefault("ADMIN_JWT_AUDIENCE", "familygraph-admin-web-migrate")

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "migrations" / "versions"
# Steward 09-11 依赖交付的新迁移（文件名前缀；revision id 动态读取）
_STEWARD_MIGRATION_PREFIXES = ("0036_", "0037_", "0038_")
_PRE_STEWARD_PREFIX = "0035_"

STEWARD_TABLES = (
    "steward_space_schedules",
    "steward_assist_batches",
    "steward_suggestions",
    "steward_suggestion_recipients",
)


def _load_migration(prefix: str) -> tuple[str, str | None]:
    """读取迁移模块的 (revision, down_revision)。"""
    matches = sorted(glob.glob(str(VERSIONS / f"{prefix}*.py")))
    assert matches, f"迁移文件未找到: {prefix}*"
    spec = importlib.util.spec_from_file_location(f"_mig_{prefix}", matches[0])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.revision), getattr(module, "down_revision", None)


def alembic(*args: str) -> None:
    result = subprocess.run(
        [".venv/bin/alembic", *args], cwd=str(ROOT), capture_output=True, text=True
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stderr}"


def table_exists(conn, table: str) -> bool:
    from sqlalchemy import inspect

    return inspect(conn).has_table(table)


def index_exists(conn, table: str, index: str) -> bool:
    # 部分唯一索引（sqlite_where）不被 SQLAlchemy inspector 报告，直接查
    # sqlite_master（只验存在性，不依赖 dialect 内省行为）。
    from sqlalchemy import text

    row = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='index' AND name=:name"), {"name": index}
    ).scalar()
    return row is not None


def main() -> int:
    pre_revision, _ = _load_migration(_PRE_STEWARD_PREFIX)
    head_revision, _ = _load_migration(_STEWARD_MIGRATION_PREFIXES[-1])
    print(f"[migrate] temp DATA_DIR={_TMP}")
    print(f"[migrate] pre-steward={pre_revision} head={head_revision}")

    # ---- 1. 空库往返 ----
    alembic("upgrade", "head")
    alembic("downgrade", pre_revision)
    from app.db import engine

    with engine.connect() as conn:
        gone = {t: table_exists(conn, t) for t in STEWARD_TABLES}
        assert not any(gone.values()), f"downgrade 后新表仍存在: {gone}"
    print("[migrate] empty-DB downgrade: steward tables dropped")
    alembic("upgrade", "head")
    with engine.connect() as conn:
        back = {t: table_exists(conn, t) for t in STEWARD_TABLES}
        assert all(back.values()), f"重新 upgrade 后新表缺失: {back}"
    print("[migrate] empty-DB roundtrip: OK")

    # ---- 2. 旧数据上行（在 pre-steward schema 插入数据）----
    alembic("downgrade", pre_revision)
    from datetime import datetime

    from sqlalchemy import insert, select

    from app.models.account import Account
    from app.models.relationship_facts import SourceFact
    from app.models.space import FamilySpace, SpaceMember
    from app.models.steward import ActionCard, StewardJob
    from app.models.user import User
    from app.utils import security

    now = datetime.now().astimezone()
    with engine.begin() as conn:
        user_id = conn.execute(
            insert(User)
            .values(
                name="mig-old-user",
                created_at=now,
                gender="unknown",
                privacy_mode="handover",
                profile_status="identity_confirmed",
            )
            .returning(User.id)
        ).scalar_one()
        user2_id = conn.execute(
            insert(User)
            .values(
                name="mig-old-user-2",
                created_at=now,
                gender="unknown",
                privacy_mode="handover",
                profile_status="identity_confirmed",
            )
            .returning(User.id)
        ).scalar_one()
        for uid in (user_id, user2_id):
            conn.execute(
                insert(Account).values(
                    user_id=uid,
                    pin_hash=security.hash_pin("123456"),
                    pin_must_change=False,
                    token_version=0,
                    failed_attempts=0,
                    status="claimed",
                    claimed_at=now,
                )
            )
        space_id = conn.execute(
            insert(FamilySpace)
            .values(name="mig-old-space", kind="household", owner_id=user_id, created_at=now)
            .returning(FamilySpace.id)
        ).scalar_one()
        conn.execute(
            insert(SpaceMember).values(
                space_id=space_id,
                user_id=user_id,
                role="member",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        fact_id = conn.execute(
            insert(SourceFact)
            .values(
                fact_type="direct_sibling",
                subject_user_id=user_id,
                object_user_id=user2_id,
                space_id=space_id,
                provenance="manual_entry",
                state="confirmed",
                revision=1,
                created_at=now,
                updated_at=now,
            )
            .returning(SourceFact.id)
        ).scalar_one()
        job_id = conn.execute(
            insert(StewardJob)
            .values(
                space_id=space_id,
                cause="source_fact",
                trigger_cursor=1,
                status="succeeded",
                attempt=1,
                max_attempts=3,
                policy_version="test",
                last_event_cursor=1,
                created_at=now,
                updated_at=now,
                settled_at=now,
            )
            .returning(StewardJob.id)
        ).scalar_one()
        conn.execute(
            insert(ActionCard).values(
                kind="household_link",
                space_id=space_id,
                recipient_account_id=conn.scalar(
                    select(Account.id).where(Account.user_id == user_id)
                ),
                subject_user_id=user_id,
                object_user_id=user2_id,
                evidence_json={"primary_fact_id": fact_id, "facts": [], "inputs": {}},
                evidence_hash="0" * 64,
                dedupe_key="household_link:1:2",
                proposed_action_json={"action": "create_household"},
                reason_text="mig roundtrip",
                privacy_effect="creates a shared household space",
                state="pending",
                revision=1,
                created_at=now,
                expires_at=None,
            )
        )
    print(
        f"[migrate] old data seeded at {pre_revision}: "
        f"user={user_id} space={space_id} job={job_id}"
    )

    alembic("upgrade", "head")
    with engine.connect() as conn:
        present = {t: table_exists(conn, t) for t in STEWARD_TABLES}
        assert all(present.values()), f"upgrade 后新表缺失: {present}"
        kept_user = conn.scalar(select(User.name).where(User.id == user_id))
        kept_job = conn.scalar(select(StewardJob.status).where(StewardJob.id == job_id))
        assert kept_user == "mig-old-user" and kept_job == "succeeded"
    print("[migrate] old-data upgrade head: rows preserved, new tables present")

    # ---- 3. 下行 → 再上行 ----
    alembic("downgrade", pre_revision)
    with engine.connect() as conn:
        gone = {t: table_exists(conn, t) for t in STEWARD_TABLES}
        assert not any(gone.values()), f"downgrade 后新表仍存在: {gone}"
        kept_user = conn.scalar(select(User.name).where(User.id == user_id))
        kept_job = conn.scalar(select(StewardJob.status).where(StewardJob.id == job_id))
        assert kept_user == "mig-old-user", "下行丢失用户数据"
        assert kept_job == "succeeded", "下行丢失作业数据"
    print("[migrate] downgrade pre-steward: new tables dropped, old data preserved")

    alembic("upgrade", "head")
    with engine.connect() as conn:
        back = {t: table_exists(conn, t) for t in STEWARD_TABLES}
        assert all(back.values()), f"再上行后新表缺失: {back}"
        kept_user = conn.scalar(select(User.name).where(User.id == user_id))
        kept_job = conn.scalar(select(StewardJob.status).where(StewardJob.id == job_id))
        assert kept_user == "mig-old-user" and kept_job == "succeeded"
        # 关键索引随迁移重建（部分索引/调度表主键）
        assert index_exists(conn, "source_facts", "uq_source_facts_active")
        assert index_exists(conn, "steward_jobs", "uq_steward_jobs_space_active")
    print("[migrate] re-upgrade head: new tables + indexes rebuilt, old data intact")

    # ---- 4. 新状态数据下行（真实生产形态：unknown/in_flight attempt + 建议通知）----
    from datetime import datetime

    from sqlalchemy import insert, select

    from app.models.notification import Notification
    from app.models.steward import StewardAssistBatch, StewardModelCall
    from app.models.steward_suggestion import StewardSuggestion

    now = datetime.now().astimezone()
    with engine.begin() as conn:
        acct_id = conn.scalar(select(Account.id).where(Account.user_id == user_id))
        batch_id = conn.execute(
            insert(StewardAssistBatch)
            .values(
                job_id=job_id,
                space_id=space_id,
                evidence_hash="0" * 64,
                policy_version="test",
                status="leased",
                created_at=now,
                updated_at=now,
            )
            .returning(StewardAssistBatch.id)
        ).scalar_one()
        for status in ("unknown", "in_flight"):
            conn.execute(
                insert(StewardModelCall).values(
                    space_id=space_id,
                    job_id=job_id,
                    batch_id=batch_id,
                    policy_version="test",
                    assist_kind="candidate",
                    subject_key="candidate:1",
                    input_hash="0" * 64,
                    prompt_digest="0" * 64,
                    prompt_chars=10,
                    status=status,
                    seq=1 if status == "unknown" else 2,
                    attempt_no=1 if status == "unknown" else 2,
                    reserved_input_tokens=10,
                    reserved_output_tokens=5,
                    created_at=now,
                )
            )
        sug_id = conn.execute(
            insert(StewardSuggestion)
            .values(
                space_id=space_id,
                origin="model",
                kind="relation_proposal",
                subject_user_id=user_id,
                object_user_id=user2_id,
                value_json={"fact_type": "direct_sibling"},
                evidence_json={"facts": []},
                evidence_hash="1" * 64,
                dedupe_key="dp:1",
                policy_version="test",
                status="proposed",
                revision=1,
                created_at=now,
                updated_at=now,
            )
            .returning(StewardSuggestion.id)
        ).scalar_one()
        conn.execute(
            insert(Notification).values(
                kind="steward_suggestion",
                space_id=space_id,
                recipient_account_id=acct_id,
                suggestion_id=sug_id,
                title="待核实建议",
                created_at=now,
            )
        )
    print("[migrate] new-state data seeded at head: batch + calls + suggestion notification")

    alembic("downgrade", pre_revision)
    with engine.connect() as conn:
        kept_statuses = list(conn.execute(select(StewardModelCall.status)).scalars().all())
        assert kept_statuses and all(
            s in ("succeeded", "failed", "degraded", "skipped") for s in kept_statuses
        ), f"下行后存在违反旧 CHECK 的状态: {kept_statuses}"
        suggestion_kind_rows = (
            conn.execute(select(Notification.id).where(Notification.kind == "steward_suggestion"))
            .scalars()
            .all()
        )
        assert not suggestion_kind_rows, "建议通知应在下行时被显式删除"
    print("[migrate] downgrade new-state: calls failed, suggestion notifications removed")

    alembic("upgrade", "head")
    with engine.connect() as conn:
        statuses = list(conn.execute(select(StewardModelCall.status)).scalars().all())
        assert statuses and all(
            s == "failed" for s in statuses
        ), f"再上行后状态意外变化: {statuses}"
    print("[migrate] re-upgrade with new-state data: converged rows stable")

    print(
        "[migrate] RESULT ok=true steps=4 "
        f"pre={pre_revision} head={head_revision} evidence=json_roundtrip"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
