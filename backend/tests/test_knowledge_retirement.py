"""U5：知识维护循环的退役扫描。

## 缺的是什么

`_ELIGIBILITY_SQL` 在**查询时**按取代/有效区间排除，所以真源已不可达的记忆本来就
检索不到 —— 但它们的派生文档仍是 `status='active'`：占存储、被 embedding 索引，
并且如果 eligibility 谓词将来变动（或有人写出不经过它的查询）就会**复活**。

`retire_unreachable_documents` 补上这一层。它**不修改真源**：退役是「这份派生数据
不再有真源支撑」，不是「删掉这个事实」。

## 为什么不需要游标

退役后文档不再是 `active`，自动从选择集里消失 —— 扫描是**自排空**的。因此不需要新
状态列，也就不需要迁移。本文件的 `test_the_sweep_is_self_draining` 钉住这一点。
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models.memory import Memory
from app.models.platform_features import PlatformFeatureConfig
from app.models.rag import RAGChunk, RAGDocument
from app.services import memory_rag, rag_maintenance
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture


def _features(db) -> None:
    row = db.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(
            id=1, memory_enabled=True, rag_enabled=True, updated_at=utcnow()
        )
        db.add(row)
    else:
        row.memory_enabled = True
        row.rag_enabled = True
    db.flush()


def _indexed_memory(db, user, *, quote: str) -> tuple[Memory, RAGDocument]:
    candidate = memory_rag.propose_candidate(
        db,
        author_account_id=user.account.id,
        source={"kind": "manual"},
        source_quote=quote,
        summary=quote,
        suggested_scope="private",
        purpose="u5",
    )
    memory = memory_rag.confirm_candidate(
        db,
        candidate_id=candidate.id,
        confirmer=user,
        confirmer_account=user.account,
        scope="private",
    )
    memory_rag.index_memory(db, memory)
    db.flush()
    document = db.scalar(
        select(RAGDocument).where(
            RAGDocument.source_type == "memory", RAGDocument.source_id == str(memory.id)
        )
    )
    assert document is not None, "前提：记忆已被索引（否则本测试什么也没证明）"
    return memory, document


def _status(db, document_id: int) -> tuple[str, str | None, str]:
    db.expire_all()
    document = db.get(RAGDocument, document_id)
    chunk_statuses = set(
        db.scalars(select(RAGChunk.status).where(RAGChunk.document_id == document_id)).all()
    )
    assert len(chunk_statuses) == 1, "chunk 状态应一致"
    return document.status, document.invalidation_reason, next(iter(chunk_statuses))


def test_current_memory_is_left_alone(db_session):
    """仍然可达的记忆不得被退役（否则退役扫描就是删数据）。"""
    user, _space = create_agent_fixture(db_session, name="u5-kept")
    _features(db_session)
    _memory, document = _indexed_memory(db_session, user, quote="orchidgrove kept")
    db_session.commit()

    counters = rag_maintenance.retire_unreachable_documents(db_session)
    assert counters["retired"] == 0
    status, _reason, chunk_status = _status(db_session, document.id)
    assert (status, chunk_status) == ("active", "active")


def test_superseded_memory_retires_its_derived_document(db_session):
    """被取代的记忆：派生文档与 chunk 一起退役，真源不动。"""
    user, _space = create_agent_fixture(db_session, name="u5-superseded")
    _features(db_session)
    old, old_document = _indexed_memory(db_session, user, quote="orchidgrove old")
    new, new_document = _indexed_memory(db_session, user, quote="orchidgrove new")
    # 取代：真源上只改 superseded_by_id（U1 的生命周期字段）。
    old.superseded_by_id = new.id
    old.updated_at = utcnow()
    db_session.commit()

    counters = rag_maintenance.retire_unreachable_documents(db_session)
    db_session.commit()
    assert counters["retired"] == 1
    status, reason, chunk_status = _status(db_session, old_document.id)
    assert status == "invalidated"
    assert reason == "source_retired:memory_not_current"
    assert chunk_status == "invalidated"
    # 真源仍存在（退役只动派生层）。
    assert db_session.get(Memory, old.id) is not None
    # 新的那条不受影响。
    assert _status(db_session, new_document.id)[0] == "active"


def test_expired_memory_retires_its_derived_document(db_session):
    """过期的记忆（valid_to 已过）同样退役。"""
    user, _space = create_agent_fixture(db_session, name="u5-expired")
    _features(db_session)
    memory, document = _indexed_memory(db_session, user, quote="orchidgrove expired")
    memory.valid_to = utcnow() - timedelta(days=1)
    memory.updated_at = utcnow()
    db_session.commit()

    counters = rag_maintenance.retire_unreachable_documents(db_session)
    db_session.commit()
    assert counters["retired"] == 1
    assert _status(db_session, document.id)[1] == "source_retired:memory_not_current"


def test_inactive_memory_retires_its_derived_document(db_session):
    """真源被撤销（status != active）：理由必须与「过期」区分开，便于排查。"""
    user, _space = create_agent_fixture(db_session, name="u5-inactive")
    _features(db_session)
    memory, document = _indexed_memory(db_session, user, quote="orchidgrove revoked")
    memory.status = "revoked"
    memory.updated_at = utcnow()
    db_session.commit()

    counters = rag_maintenance.retire_unreachable_documents(db_session)
    db_session.commit()
    assert counters["retired"] == 1
    assert _status(db_session, document.id)[1] == "source_retired:memory_inactive"


def test_missing_truth_source_retires_its_derived_document(db_session):
    """真源行消失（被删除）：派生文档不能继续 active。"""
    user, _space = create_agent_fixture(db_session, name="u5-missing")
    _features(db_session)
    memory, document = _indexed_memory(db_session, user, quote="orchidgrove missing")
    db_session.delete(memory)
    db_session.commit()

    counters = rag_maintenance.retire_unreachable_documents(db_session)
    db_session.commit()
    assert counters["retired"] == 1
    assert _status(db_session, document.id)[1] == "source_retired:memory_missing"


def test_the_sweep_is_self_draining_and_idempotent(db_session):
    """自排空：退役过的文档不再出现在选择集里，因此重复运行是 no-op。

    这条性质是「不需要游标」的依据。没有它，`limit` 会在前 N 条都不需要退役时
    永远卡在同一个窗口。
    """
    user, _space = create_agent_fixture(db_session, name="u5-drain")
    _features(db_session)
    first, first_document = _indexed_memory(db_session, user, quote="orchidgrove drain-1")
    second, second_document = _indexed_memory(db_session, user, quote="orchidgrove drain-2")
    first.superseded_by_id = second.id
    first.updated_at = utcnow()
    db_session.commit()

    # limit=1：一次只退一个，验证分批判推进。
    assert rag_maintenance.retire_unreachable_documents(db_session, limit=1)["retired"] == 1
    db_session.commit()
    assert _status(db_session, first_document.id)[0] == "invalidated"
    # 第二次运行不应重复退役已退役的那条。
    assert rag_maintenance.retire_unreachable_documents(db_session, limit=10)["retired"] == 0
    db_session.commit()
    assert _status(db_session, second_document.id)[0] == "active"


def test_retirement_is_wired_into_the_maintenance_batch(db_session, monkeypatch):
    """接入 run_maintenance_batch，并把它记进 tick 计数器。"""
    from app import config
    from app.services import maintenance

    monkeypatch.setattr(config, "RAG_ENABLED", True)
    user, _space = create_agent_fixture(db_session, name="u5-wired")
    _features(db_session)
    old, old_document = _indexed_memory(db_session, user, quote="orchidgrove wired-old")
    new, _new_document = _indexed_memory(db_session, user, quote="orchidgrove wired-new")
    old.superseded_by_id = new.id
    old.updated_at = utcnow()
    db_session.commit()

    result = rag_maintenance.run_maintenance_batch(db_session, worker_id="u5-test")
    db_session.commit()
    assert result.get("retired", 0) >= 1, result
    assert _status(db_session, old_document.id)[0] == "invalidated"

    counters = maintenance.run_maintenance_tick()
    assert "rag_index_retired" in counters
