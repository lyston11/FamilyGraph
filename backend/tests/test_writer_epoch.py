"""C7：writer epoch 与 migration health。

## 为什么这两个是切换安全的前提

切换 SQLite → PostgreSQL 的最大风险是**双主**：两个 writer 同时裁决
lease/settle/counter，产生两个真相，且无法事后对账修复（两边都可能已对外产生结果）。

本文件验证两件基础设施：

1. **epoch 使旧实例立刻失效**：阶段变化时 epoch +1，持有旧 epoch 的实例核对失败并
   **拒绝写入**。这使切换不需要逐实例重启（重启期间新旧并存正是双主窗口）。
2. **阶段只能逐级移动**：跳级会让「哪些面已经切过」不可知，从而无法安全回滚。

## 回滚语义（被断言的部分）

回滚 = 相邻退一级 + epoch 递增。断言：退回后 `postgres_is_authoritative` 变回 False，
且旧 epoch 失效。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services import writer_epoch
from app.services.writer_epoch import WriterEpochMismatch


def _seed_state(db_session, *, stage: str, epoch: int) -> None:
    db_session.execute(
        text(
            "INSERT INTO writer_state (id, stage, epoch, updated_at, updated_by)"
            " VALUES (1, :stage, :epoch, CURRENT_TIMESTAMP, 'test')"
            " ON CONFLICT (id) DO UPDATE SET stage = :stage, epoch = :epoch"
        ),
        {"stage": stage, "epoch": epoch},
    )
    db_session.commit()


def test_default_state_falls_back_to_deployment_stage(db_session):
    """无行时回落到部署默认阶段。

    迁移**之前**表还不存在（或为空），而 health 必须能回答。回落使迁移前后的
    行为一致——迁移本身不改变任何路由。
    """
    state = writer_epoch.read_state(db_session)
    assert state.stage == writer_epoch.DEFAULT_STAGE
    assert state.epoch == 0


def test_advance_increments_epoch(db_session):
    """阶段推进必须递增 epoch，否则旧实例不会失效。"""
    _seed_state(db_session, stage="sqlite", epoch=5)
    state = writer_epoch.advance(db_session, to_stage="shadow", actor="ops")
    db_session.commit()
    assert state.stage == "shadow"
    assert state.epoch == 6, "推进阶段未递增 epoch：旧实例仍会继续写"


def test_advance_rejects_stage_skipping(db_session):
    """跳级必须被拒绝：跳级后「哪些面已切过」不可知，无法安全回滚。"""
    _seed_state(db_session, stage="sqlite", epoch=0)
    with pytest.raises(ValueError, match="one step at a time"):
        writer_epoch.advance(db_session, to_stage="pg_all", actor="ops")


def test_rollback_is_adjacent_and_invalidates_old_epoch(db_session):
    """回滚 = 相邻退一级 + epoch 递增（让旧实例立刻失效）。"""
    _seed_state(db_session, stage="pg_control", epoch=3)
    state = writer_epoch.advance(db_session, to_stage="shadow", actor="ops")
    db_session.commit()
    assert state.stage == "shadow"
    assert state.epoch == 4
    assert state.postgres_is_authoritative is False, "回滚后 PG 不应仍是真源"
    # 持有旧 epoch 的实例必须被拒绝
    with pytest.raises(WriterEpochMismatch):
        writer_epoch.check_epoch(db_session, held=3)


def test_epoch_mismatch_is_a_safety_error_not_a_retry(db_session):
    """epoch 过期是**安全**异常，不是暂时性故障。

    继续写会造成双主，因此必须拒绝而不是重试。
    """
    _seed_state(db_session, stage="pg_all", epoch=10)
    with pytest.raises(WriterEpochMismatch) as exc_info:
        writer_epoch.check_epoch(db_session, held=9)
    assert exc_info.value.expected == 9
    assert exc_info.value.actual == 10


def test_check_epoch_passes_when_current(db_session):
    """持有当前 epoch 时核对通过。"""
    _seed_state(db_session, stage="pg_all", epoch=7)
    writer_epoch.check_epoch(db_session, held=7)  # 不应抛出


def test_stage_predicates_are_distinct(db_session):
    """四个阶段的语义必须彼此区分——尤其 `shadow` **不是**真源。

    把 shadow 当成真源会让「只读对照期间」的写入被误判为已切换，
    从而在真正切换前就停止写 SQLite。
    """
    expectations = {
        "sqlite": (False, False, False),
        "shadow": (False, False, False),
        "pg_control": (True, True, False),
        "pg_all": (True, True, True),
    }
    for stage, (auth, control, all_domains) in expectations.items():
        _seed_state(db_session, stage=stage, epoch=1)
        state = writer_epoch.read_state(db_session)
        assert state.postgres_is_authoritative is auth, f"{stage}: authoritative"
        assert state.control_plane_on_postgres is control, f"{stage}: control"
        assert state.all_domains_on_postgres is all_domains, f"{stage}: all_domains"


def test_unknown_stage_is_rejected(db_session):
    """未知阶段必须报错，不得静默当作某一级。"""
    with pytest.raises(ValueError, match="unknown writer stage"):
        writer_epoch._stage_index("half_migrated")


def test_migration_health_contains_no_secrets(db_session):
    """health 端点可能公开，因此只能含治理元数据。"""
    _seed_state(db_session, stage="pg_control", epoch=2)
    health = writer_epoch.migration_health(db_session)
    assert set(health) == {
        "writer_stage",
        "writer_epoch",
        "postgres_authoritative",
        "control_plane_on_postgres",
        "all_domains_on_postgres",
        "updated_at",
        "updated_by",
        "valid_stages",
    }
    # 不得出现连接串、凭据或业务字段
    blob = repr(health)
    for forbidden in ("postgresql://", "password", "secret", "token"):
        assert forbidden not in blob.lower()


def test_database_check_constraints_reject_invalid_state(db_session):
    """数据库侧兜底：非法阶段、负 epoch、非单例都必须被拒绝。

    应用层判断可能被绕过（例如运维手工 UPDATE），因此约束必须落在数据库上。
    """
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db_session.execute(
            text("INSERT INTO writer_state (id, stage, epoch) VALUES (1, 'half', 0)")
        )
        db_session.commit()
    db_session.rollback()

    with pytest.raises(IntegrityError):
        db_session.execute(
            text("INSERT INTO writer_state (id, stage, epoch) VALUES (1, 'sqlite', -1)")
        )
        db_session.commit()
    db_session.rollback()

    with pytest.raises(IntegrityError):
        db_session.execute(
            text("INSERT INTO writer_state (id, stage, epoch) VALUES (2, 'sqlite', 0)")
        )
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------- 写路径守卫


def test_guard_adopts_epoch_on_first_use(db_session):
    """进程启动后的第一次写入**采纳**当前 epoch，而不是拒绝。

    刚启动的进程持有的 epoch 就是当前值；此时拒绝会让每次启动后的第一个写失败。
    """
    writer_epoch.reset_process_epoch_for_tests()
    _seed_state(db_session, stage="pg_all", epoch=42)
    writer_epoch.guard(db_session)  # 不应抛出


def test_guard_rejects_after_epoch_changes(db_session):
    """epoch 变化后守卫必须拒绝——这是「切换让旧实例失效」的机制。"""
    writer_epoch.reset_process_epoch_for_tests()
    _seed_state(db_session, stage="sqlite", epoch=1)
    writer_epoch.guard(db_session)  # 采纳 1

    # 模拟另一个实例完成切换：epoch 变为 2
    _seed_state(db_session, stage="pg_control", epoch=2)
    with pytest.raises(WriterEpochMismatch):
        writer_epoch.guard(db_session)


def test_advance_updates_this_process_epoch(db_session):
    """执行切换的实例必须同步采纳新 epoch，否则会把自己锁在门外。"""
    writer_epoch.reset_process_epoch_for_tests()
    _seed_state(db_session, stage="sqlite", epoch=0)
    writer_epoch.guard(db_session)  # 采纳 0

    writer_epoch.advance(db_session, to_stage="shadow", actor="ops")
    db_session.commit()
    # 若不采纳新 epoch，这一行会抛 WriterEpochMismatch
    writer_epoch.guard(db_session)


def test_guard_can_be_disabled_for_stable_deployments(db_session, monkeypatch):
    """迁移完成后可关闭守卫，避免每次写都读状态表。

    默认开启：默认关闭会让「忘了打开」变成静默的双主风险。
    """
    writer_epoch.reset_process_epoch_for_tests()
    _seed_state(db_session, stage="pg_all", epoch=5)
    writer_epoch.guard(db_session)  # 采纳 5
    _seed_state(db_session, stage="pg_all", epoch=99)

    monkeypatch.setenv("FG_WRITER_EPOCH_GUARD", "0")
    writer_epoch.guard(db_session)  # 关闭后不比对


def test_command_transaction_calls_the_guard(db_session, monkeypatch):
    """`command_transaction` 必须在事务起点调用守卫。

    结构性断言：若某次重构去掉这行，切换期间旧实例会继续写——而单测很难用
    真实双实例复现。因此用「守卫被调用」这一可观测事实守护它。
    """
    from app.commands import context as commands_context

    calls: list[str] = []
    monkeypatch.setattr(commands_context.writer_epoch, "guard", lambda _db: calls.append("guard"))
    with commands_context.command_transaction(db_session, commit=False):
        pass
    assert calls == ["guard"], "command_transaction 未调用 writer epoch 守卫"
