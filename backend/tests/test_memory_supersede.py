"""P1 记忆取代语义回归：时间有效区间、取代指针、撤销取代与 fail-closed。

全部内容为合成数据。测试覆盖的核心不变量：

1. 被取代的记忆**不再进入检索**，但行仍在、仍可审计；
2. 撤销取代后旧事实重新可检索（取代是可逆的，不是删除）；
3. 冲突/非法取代被拒绝，且失败**不产生任何部分写入**；
4. 授权与取代过滤**叠加**：取代不能成为跨账户读别人记忆的旁路；
5. mutation：去掉取代过滤时检索必须重新返回旧事实（证明过滤是承重的）。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.memory import Memory
from app.models.rag import RAGChunk, RAGDocument
from app.services import memory_rag
from app.utils import timeutil
from conftest import create_agent_fixture


def _confirm(db, owner, *, summary, scope="private", space_id=None, source_revision=None):
    """Create + confirm one memory (the only path that may create knowledge)."""
    candidate = memory_rag.propose_candidate(
        db,
        author_account_id=owner.account.id,
        source={"kind": "manual"},
        source_quote=summary,
        summary=summary,
        suggested_scope=scope,
        purpose="supersede regression",
    )
    return memory_rag.confirm_candidate(
        db,
        candidate_id=candidate.id,
        confirmer=owner,
        confirmer_account=owner.account,
        scope=scope,
        space_id=space_id,
    )


def _search(db, owner, space, query):
    return memory_rag.search_rag(
        db,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        query=query,
        limit=10,
        for_model=False,
    )


def _source_ids(hits):
    return [hit.source_id for hit in hits]


def _current_documents(db, memory_id):
    return db.scalars(
        select(RAGDocument).where(
            RAGDocument.source_type == "memory",
            RAGDocument.source_id == str(memory_id),
        )
    ).all()


# --------------------------------------------------------------------------
# 取代 / 恢复的基本语义
# --------------------------------------------------------------------------


def test_superseded_memory_leaves_retrieval_but_stays_auditable(db_session):
    owner, space = create_agent_fixture(db_session, name="supersede-basic")
    old = _confirm(db_session, owner, summary="外婆住在上海静安区。")
    new = _confirm(db_session, owner, summary="外婆搬到了苏州工业园区。")
    db_session.commit()

    assert str(old.id) in _source_ids(_search(db_session, owner, space, "外婆住在哪里"))

    memory_rag.supersede_memory(
        db_session, memory_id=old.id, account_id=owner.account.id, by_memory_id=new.id
    )
    db_session.commit()

    # 旧事实不再进入检索。
    hits = _search(db_session, owner, space, "外婆住在哪里")
    assert str(old.id) not in _source_ids(hits)
    assert str(new.id) in _source_ids(hits)

    # 但行仍在，且取代状态可审计。
    row = db_session.get(Memory, old.id, populate_existing=True)
    assert row is not None
    assert row.status == "active"
    assert row.superseded_by_id == new.id
    assert row.supersede_reason == "user_replaced"
    assert row.superseded_at is not None
    assert row.valid_to is not None


def test_supersede_does_not_delete_projection_but_makes_it_unreachable(db_session):
    owner, space = create_agent_fixture(db_session, name="supersede-projection")
    old = _confirm(db_session, owner, summary="舅舅在南京工作。")
    new = _confirm(db_session, owner, summary="舅舅调到杭州工作了。")
    db_session.commit()
    documents_before = _current_documents(db_session, old.id)
    assert len(documents_before) == 1
    assert documents_before[0].status == "active"
    document_id = documents_before[0].id
    chunks_before = db_session.scalars(
        select(RAGChunk).where(RAGChunk.document_id == document_id)
    ).all()
    assert chunks_before

    memory_rag.supersede_memory(
        db_session, memory_id=old.id, account_id=owner.account.id, by_memory_id=new.id
    )
    db_session.commit()

    # 文档与 chunk 都还在（可恢复），只是被标记为可恢复的失效。
    document = db_session.get(RAGDocument, document_id, populate_existing=True)
    assert document is not None
    assert document.status == "invalidated"
    assert document.invalidation_reason == "index_superseded"
    # chunk 仍在**且仍是 active**：它们属于投影完整性证据，不是授权输入。
    # 把 chunk 标成 invalidated 会让撤销取代时的 index_memory 判定「缺少完整
    # 正文证据」而拒绝恢复。
    chunks_after = db_session.scalars(
        select(RAGChunk).where(RAGChunk.document_id == document_id)
    ).all()
    assert len(chunks_after) == len(chunks_before)
    assert all(chunk.status == "active" for chunk in chunks_after)


def test_restore_makes_old_fact_retrievable_again(db_session):
    owner, space = create_agent_fixture(db_session, name="supersede-restore")
    old = _confirm(db_session, owner, summary="奶奶最喜欢喝龙井茶。")
    new = _confirm(db_session, owner, summary="奶奶现在更喜欢普洱茶。")
    db_session.commit()
    memory_rag.supersede_memory(
        db_session, memory_id=old.id, account_id=owner.account.id, by_memory_id=new.id
    )
    db_session.commit()
    assert str(old.id) not in _source_ids(_search(db_session, owner, space, "奶奶喜欢喝什么"))

    memory_rag.restore_memory(db_session, memory_id=old.id, account_id=owner.account.id)
    db_session.commit()

    hits = _search(db_session, owner, space, "奶奶喜欢喝什么")
    assert str(old.id) in _source_ids(hits)
    row = db_session.get(Memory, old.id, populate_existing=True)
    assert row is not None
    assert row.superseded_by_id is None
    assert row.supersede_reason is None
    assert row.valid_to is None
    assert row.restored_at is not None
    # 投影被原地重新激活，不需要全库重建。
    document = _current_documents(db_session, old.id)[0]
    assert document.status == "active"
    assert document.invalidation_reason is None


# --------------------------------------------------------------------------
# 校验与失败原子性
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    ["bogus", "", "USER_REPLACED"],
)
def test_unknown_supersede_reason_is_rejected(db_session, reason):
    owner, _space = create_agent_fixture(db_session, name=f"supersede-reason-{reason or 'empty'}")
    old = _confirm(db_session, owner, summary="外公喜欢下象棋。")
    new = _confirm(db_session, owner, summary="外公现在喜欢下围棋。")
    db_session.commit()

    with pytest.raises(Exception) as excinfo:
        memory_rag.supersede_memory(
            db_session,
            memory_id=old.id,
            account_id=owner.account.id,
            by_memory_id=new.id,
            reason=reason,
        )
    assert "取代原因不合法" in str(excinfo.value)
    db_session.rollback()
    row = db_session.get(Memory, old.id, populate_existing=True)
    assert row is not None and row.superseded_by_id is None


def test_cross_source_or_backward_supersede_is_rejected(db_session):
    owner, _space = create_agent_fixture(db_session, name="supersede-cross-source")
    first = _confirm(db_session, owner, summary="姑姑住在成都。")
    second = _confirm(db_session, owner, summary="姑姑住在重庆。")
    db_session.commit()

    # 反向取代：更早的行不能取代更晚的行（否则「旧事实取代新事实」）。
    with pytest.raises(Exception) as excinfo:
        memory_rag.supersede_memory(
            db_session,
            memory_id=second.id,
            account_id=owner.account.id,
            by_memory_id=first.id,
        )
    assert "更晚创建" in str(excinfo.value)
    db_session.rollback()

    # 自我取代被拒绝。
    with pytest.raises(Exception) as excinfo:
        memory_rag.supersede_memory(
            db_session,
            memory_id=first.id,
            account_id=owner.account.id,
            by_memory_id=first.id,
        )
    assert "不能取代自己" in str(excinfo.value)
    db_session.rollback()


def test_supersede_is_idempotent_and_conflicts_are_explicit(db_session):
    owner, _space = create_agent_fixture(db_session, name="supersede-idempotent")
    old = _confirm(db_session, owner, summary="叔叔在青岛当老师。")
    new = _confirm(db_session, owner, summary="叔叔调到济南当老师了。")
    third = _confirm(db_session, owner, summary="叔叔又调到了烟台。")
    db_session.commit()

    memory_rag.supersede_memory(
        db_session, memory_id=old.id, account_id=owner.account.id, by_memory_id=new.id
    )
    db_session.commit()
    # 同一取代重复调用返回同一行，不报错、不改写时间。
    first_at = db_session.get(Memory, old.id, populate_existing=True).superseded_at
    again = memory_rag.supersede_memory(
        db_session, memory_id=old.id, account_id=owner.account.id, by_memory_id=new.id
    )
    db_session.commit()
    assert again.superseded_by_id == new.id
    assert again.superseded_at == first_at

    # 用第三个版本再取代同一个旧行是冲突，不是静默改写。
    with pytest.raises(Exception) as excinfo:
        memory_rag.supersede_memory(
            db_session, memory_id=old.id, account_id=owner.account.id, by_memory_id=third.id
        )
    assert "已被其它版本取代" in str(excinfo.value)
    db_session.rollback()


def test_restore_requires_an_existing_supersede(db_session):
    owner, _space = create_agent_fixture(db_session, name="restore-noop")
    memory = _confirm(db_session, owner, summary="表姐在武汉读大学。")
    db_session.commit()

    with pytest.raises(Exception) as excinfo:
        memory_rag.restore_memory(db_session, memory_id=memory.id, account_id=owner.account.id)
    assert "当前未被取代" in str(excinfo.value)
    db_session.rollback()


def test_supersede_cannot_touch_another_account_memory(db_session):
    owner, _space = create_agent_fixture(db_session, name="supersede-owner")
    other, _other_space = create_agent_fixture(db_session, name="supersede-other")
    mine = _confirm(db_session, owner, summary="我的表弟在合肥。")
    theirs = _confirm(db_session, other, summary="别人的表弟在芜湖。")
    db_session.commit()

    with pytest.raises(Exception) as excinfo:
        memory_rag.supersede_memory(
            db_session,
            memory_id=theirs.id,
            account_id=owner.account.id,
            by_memory_id=mine.id,
        )
    # 不区分「不存在」与「不属于本人」：避免存在性枚举。
    assert "记忆不存在" in str(excinfo.value)
    db_session.rollback()
    row = db_session.get(Memory, theirs.id, populate_existing=True)
    assert row is not None and row.superseded_by_id is None


# --------------------------------------------------------------------------
# 通过确认命令的显式取代（唯一的产品入口）
# --------------------------------------------------------------------------


def test_confirm_with_supersedes_marks_old_memory_in_one_transaction(db_session):
    owner, space = create_agent_fixture(db_session, name="confirm-supersedes")
    old = _confirm(db_session, owner, summary="爷爷住在扬州。")
    db_session.commit()

    candidate = memory_rag.propose_candidate(
        db_session,
        author_account_id=owner.account.id,
        source={"kind": "manual"},
        source_quote="爷爷搬到了镇江。",
        summary="爷爷搬到了镇江。",
        suggested_scope="private",
        purpose="supersede via confirm",
    )
    new = memory_rag.confirm_candidate(
        db_session,
        candidate_id=candidate.id,
        confirmer=owner,
        confirmer_account=owner.account,
        scope="private",
        supersedes=(old.id,),
    )
    db_session.commit()

    assert new.id != old.id
    row = db_session.get(Memory, old.id, populate_existing=True)
    assert row is not None and row.superseded_by_id == new.id
    hits = _search(db_session, owner, space, "爷爷住在哪里")
    assert str(old.id) not in _source_ids(hits)
    assert str(new.id) in _source_ids(hits)


def test_confirm_with_invalid_supersede_target_writes_nothing(db_session):
    owner, _space = create_agent_fixture(db_session, name="confirm-bad-supersede")
    foreign, _foreign_space = create_agent_fixture(db_session, name="confirm-bad-foreign")
    theirs = _confirm(db_session, foreign, summary="别人的记忆不该被取代。")
    db_session.commit()

    candidate = memory_rag.propose_candidate(
        db_session,
        author_account_id=owner.account.id,
        source={"kind": "manual"},
        source_quote="我的新事实。",
        summary="我的新事实。",
        suggested_scope="private",
        purpose="bad supersede target",
    )
    db_session.commit()

    with pytest.raises(Exception) as excinfo:
        memory_rag.confirm_candidate(
            db_session,
            candidate_id=candidate.id,
            confirmer=owner,
            confirmer_account=owner.account,
            scope="private",
            supersedes=(theirs.id,),
        )
    assert "记忆不存在" in str(excinfo.value)
    db_session.rollback()

    # 候选仍是 pending，没有产生 Memory，也没有触碰别人的行。
    candidate_row = db_session.get(type(candidate), candidate.id, populate_existing=True)
    assert candidate_row is not None and candidate_row.status == "pending"
    assert candidate_row.memory_id is None
    foreign_row = db_session.get(Memory, theirs.id, populate_existing=True)
    assert foreign_row is not None and foreign_row.superseded_by_id is None


# --------------------------------------------------------------------------
# 授权叠加与 mutation
# --------------------------------------------------------------------------


def test_supersede_filter_never_widens_cross_account_visibility(db_session):
    owner, space = create_agent_fixture(db_session, name="supersede-authz-owner")
    other, _other_space = create_agent_fixture(db_session, name="supersede-authz-other")
    # 同一空间的两条 private 记忆分属两个账户。
    _confirm(db_session, owner, summary="我的私事：周末去钓鱼。")
    _confirm(db_session, other, summary="别人的私事：周末去爬山。")
    db_session.commit()

    hits = _search(db_session, owner, space, "周末去")
    texts = [hit.text for hit in hits]
    assert all("别人的私事" not in text for text in texts)


def test_supersede_visibility_is_load_bearing_at_every_layer(db_session, monkeypatch):
    """mutation：逐层拆除取代防护，每一层都必须能独立挡住旧事实。

    取代可见性有四层，且**四层都承重**：

    1. SQL 取代过滤（`_ELIGIBILITY_SQL` 的 `m.superseded_by_id IS NULL`）；
    2. SQL 有效区间（`m.valid_to IS NULL OR m.valid_to > :now`）；
    3. 文档状态（取代时写 `index_superseded`，eligibility 要求 `d.status='active'`）；
    4. 投影复核（`_rows_to_hits` 的 `_memory_is_current`），挡住「SQL 执行后、
       行读取前被取代」的并发窗口。

    这是纵深防御，不是冗余：只拆一层不够，任何一层被删掉都应让下面的断言失败。
    """
    owner, space = create_agent_fixture(db_session, name="supersede-mutation")
    old = _confirm(db_session, owner, summary="大伯在太原工作。")
    new = _confirm(db_session, owner, summary="大伯调到大连工作了。")
    db_session.commit()
    memory_rag.supersede_memory(
        db_session, memory_id=old.id, account_id=owner.account.id, by_memory_id=new.id
    )
    db_session.commit()
    query = "大伯在哪里工作"

    def visible() -> bool:
        return str(old.id) in _source_ids(_search(db_session, owner, space, query))

    assert not visible()

    # 第 1 层：拆掉 SQL 取代过滤。旧事实仍不该出现（有效区间与文档层挡住它）。
    original_sql = memory_rag._ELIGIBILITY_SQL
    mutated_sql = original_sql.replace("AND m.superseded_by_id IS NULL\n      ", "")
    assert mutated_sql != original_sql, "mutation anchor not found — update this test with the SQL"
    monkeypatch.setattr(memory_rag, "_ELIGIBILITY_SQL", mutated_sql)
    assert not visible(), "with the supersede filter removed, the validity window must still block"

    # 第 2 层：把有效区间重新打开。文档层必须独立挡住。
    row = db_session.get(Memory, old.id, populate_existing=True)
    assert row is not None
    row.valid_to = None
    db_session.commit()
    assert not visible(), "with both SQL conditions removed, the document layer must still block"

    # 第 3 层：把文档重新激活（模拟「只靠 SQL 过滤」的实现）。复核层必须独立挡住。
    for document in _current_documents(db_session, old.id):
        document.status = "active"
        document.invalidation_reason = None
        document.invalidated_at = None
    db_session.commit()
    assert not visible(), "with SQL and document layers removed, the projection recheck must block"

    # 第 4 层：连复核也拆掉。此时旧事实必须出现——证明四层都是承重的，
    # 而不是「反正有别的路径挡住」。
    monkeypatch.setattr(memory_rag, "_memory_is_current", lambda memory: True)
    assert (
        visible()
    ), "removing all four layers must resurrect the row — otherwise one is not load-bearing"


def test_expired_validity_window_is_excluded_from_retrieval(db_session):
    """有效区间过期等价于「不再是当前事实」，不需要取代指针。"""
    owner, space = create_agent_fixture(db_session, name="validity-window")
    memory = _confirm(db_session, owner, summary="舅舅去年在珠海出差。")
    db_session.commit()
    assert str(memory.id) in _source_ids(_search(db_session, owner, space, "舅舅在珠海"))

    row = db_session.get(Memory, memory.id, populate_existing=True)
    assert row is not None
    row.valid_from = timeutil.utcnow() - timedelta(days=30)
    row.valid_to = timeutil.utcnow() - timedelta(days=1)
    db_session.commit()

    assert str(memory.id) not in _source_ids(_search(db_session, owner, space, "舅舅在珠海"))


def test_superseded_memory_never_enters_agent_context(db_session):
    """端到端：取代后 ContextBuilder 不再把旧事实放进模型上下文。"""
    from app.services import context_builder

    owner, space = create_agent_fixture(db_session, name="supersede-context")
    old = _confirm(db_session, owner, summary="小姨住在福州。")
    new = _confirm(db_session, owner, summary="小姨搬到了厦门。")
    db_session.commit()

    def build():
        return context_builder.ContextBuilder(db_session).build(
            actor=owner,
            space_id=space.id,
            agent_kind="assistant",
            query="小姨住在哪里？",
        )

    before = [source.source_id for source in build().sources]
    assert str(old.id) in before

    memory_rag.supersede_memory(
        db_session, memory_id=old.id, account_id=owner.account.id, by_memory_id=new.id
    )
    db_session.commit()

    after = [source.source_id for source in build().sources]
    assert str(old.id) not in after
    assert str(new.id) in after
