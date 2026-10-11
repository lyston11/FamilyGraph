"""Behavioral contracts for explicit memory confirmation and scope isolation."""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import config
from app.commands.attachment_content import (
    confirm_content_version,
    create_content_version,
    create_grant,
)
from app.commands.context import ActorContext
from app.models.attachment import Attachment
from app.models.platform_features import PlatformFeatureConfig
from app.models.rag import RAGChunk
from app.models.space import FamilySpace
from app.models.steward import StewardJob
from app.models.v2_foundation import DomainEvent
from app.services import context_builder as cb
from app.services.domain_events import emit
from app.services.memory_rag import (
    confirm_candidate,
    ingest_authorized_document,
    propose_candidate,
    revoke_memory,
    search_rag,
)
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture, create_space_member, create_user_with_pin


def test_candidate_is_not_retrievable_until_explicit_confirmation(db_session):
    owner, space = create_agent_fixture(db_session, name="memory-owner")
    create_space_member(db_session, space.id, owner.id, role="owner")
    candidate = propose_candidate(
        db_session,
        author_account_id=owner.account.id,
        source={"kind": "manual"},
        source_quote="Our family recipe is kept in the blue notebook.",
        summary="The family recipe is kept in the blue notebook.",
        suggested_scope="household",
        purpose="family reference",
    )
    db_session.commit()

    assert (
        search_rag(
            db_session,
            actor=owner,
            account=owner.account,
            space_id=space.id,
            query="blue notebook",
        )
        == []
    )

    memory = confirm_candidate(
        db_session,
        candidate_id=candidate.id,
        confirmer=owner,
        confirmer_account=owner.account,
        scope=f"household:{space.id}",
    )
    db_session.commit()

    hits = search_rag(
        db_session,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        query="blue notebook",
    )
    assert len(hits) == 1
    assert hits[0].source_id == str(memory.id)


def test_private_memory_is_available_to_assistant_context(db_session):
    owner, space = create_agent_fixture(db_session, name="memory-private-rag")
    create_space_member(db_session, space.id, owner.id, role="owner")
    candidate = propose_candidate(
        db_session,
        author_account_id=owner.account.id,
        source={"kind": "manual"},
        source_quote="The private note mentions a silver locket.",
        summary="A silver locket is mentioned in a private note.",
        suggested_scope="private",
        purpose="private reference",
    )
    db_session.commit()

    memory = confirm_candidate(
        db_session,
        candidate_id=candidate.id,
        confirmer=owner,
        confirmer_account=owner.account,
        scope="private",
    )
    db_session.commit()

    hits = search_rag(
        db_session,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        query="silver locket",
        agent_kind="assistant",
    )
    assert [hit.source_id for hit in hits] == [str(memory.id)]


def test_shared_memory_scope_matches_space_kind(db_session):
    owner, household = create_agent_fixture(db_session, name="memory-scope-kind")
    create_space_member(db_session, household.id, owner.id, role="owner")
    lineage = FamilySpace(
        name="memory-scope-kind-lineage",
        kind="lineage",
        owner_id=owner.id,
        created_at=owner.created_at,
    )
    db_session.add(lineage)
    db_session.commit()
    create_space_member(db_session, lineage.id, owner.id, role="owner")
    candidate = propose_candidate(
        db_session,
        author_account_id=owner.account.id,
        source={"kind": "manual"},
        source_quote="A scope-kind test note.",
        summary="A scope-kind test note.",
        suggested_scope="household",
        purpose="scope validation",
    )
    db_session.commit()

    with pytest.raises(HTTPException) as error:
        confirm_candidate(
            db_session,
            candidate_id=candidate.id,
            confirmer=owner,
            confirmer_account=owner.account,
            scope=f"household:{lineage.id}",
        )
    assert error.value.status_code == 422


def test_steward_cannot_retrieve_authorized_documents(db_session):
    """管家不得读附件授权文档——即使它已确认、scope 正确、本人是 active 成员。

    2026-10-11 起 `authorized_document` 的真实合同是「附件本人把某份已确认内容版本
    授权给某空间里的**助手**」。因此两个独立理由都让管家读不到：

    1. `agent_content_grants.agent_kind` 的 CHECK 只允许 `assistant`；
    2. `_ELIGIBILITY_SQL` 的 grant 分支要求 `g.agent_kind = :agent_kind`。

    本测试取代了旧的 `test_steward_can_retrieve_current_space_shared_rag_only`。
    旧断言（管家能读 `authorized_document`）编码的是**未实现时**的占位行为：
    当时该来源没有任何授权真源，所以「空间成员即可读」看起来是对的。现在它错了——
    用户授权时选择的是「助手」，把同一份内容同时交给第二个 Agent 会让「我授权给谁」
    失去意义。管家能读的是 `memory`，那有自己的确认流程与可读集。
    """
    owner, space = create_agent_fixture(db_session, name="memory-steward-shared")
    create_space_member(db_session, space.id, owner.id, role="owner")
    row = db_session.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(id=1, updated_at=utcnow())
        db_session.add(row)
    row.memory_enabled = True
    row.rag_enabled = True
    db_session.commit()

    attachment = Attachment(
        user_id=owner.id,
        type="link",
        url_or_path="https://example.com/steward-shared",
        title="资料",
        description="说明",
        uploaded_by=owner.id,
        created_at=utcnow(),
    )
    db_session.add(attachment)
    db_session.commit()
    ctx = ActorContext.from_identity(owner, owner.account)
    draft = create_content_version(
        db_session, ctx, attachment_id=attachment.id, text="A confirmed shared family fact."
    )
    version = confirm_content_version(db_session, ctx, version_id=draft.id)
    grant = create_grant(db_session, ctx, version_id=version.id, space_id=space.id)
    db_session.commit()

    # 管家：即使是同一空间的 active 成员，也不得读到这份附件授权文档。
    assert (
        search_rag(
            db_session,
            actor=owner,
            account=owner.account,
            space_id=space.id,
            query="confirmed shared family fact",
            agent_kind="steward",
        )
        == []
    )
    # 反证：同一查询在 assistant 身份下能召回——所以「空结果」不是语料不存在。
    assert [
        hit.source_id
        for hit in search_rag(
            db_session,
            actor=owner,
            account=owner.account,
            space_id=space.id,
            query="confirmed shared family fact",
            agent_kind="assistant",
        )
    ] == [f"attachment:{attachment.id}:version:{version.id}:grant:{grant.id}"]


def test_private_memory_event_does_not_enqueue_steward_job(db_session, monkeypatch):
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="memory-private-event")
    create_space_member(db_session, space.id, owner.id, role="owner")

    candidate = propose_candidate(
        db_session,
        author_account_id=owner.account.id,
        source={"kind": "manual"},
        source_quote="A private note that must not become Steward input.",
        summary="A private note.",
        suggested_scope="private",
        purpose="private reflection",
    )
    db_session.commit()

    event = db_session.scalar(
        select(DomainEvent).where(
            DomainEvent.type == "memory.candidate.proposed",
            DomainEvent.aggregate_id == candidate.id,
        )
    )
    assert event is not None
    assert db_session.scalar(select(StewardJob).where(StewardJob.space_id == space.id)) is None


def test_public_rag_requires_active_space_membership(db_session):
    """`search_rag` 要求调用者是该空间的 active 成员，且平台角色不得进入。

    载体用 `public_kinship`（内置称谓包）而不是 `authorized_document`：
    后者自 2026-10-11 起只能以 `household` scope 入索引（见下方
    `test_authorized_document_rejects_non_household_scope`），拿它当 `public` 的
    载体已不成立。改用真正合法的 `scope='public'` 来源反而让本测试更强——
    它现在测的是生产语料，而不是一个人造的越权文档。
    """
    from app.models.platform_features import PlatformFeatureConfig
    from app.services import terms

    owner, space = create_agent_fixture(db_session, name="memory-public-owner")
    create_space_member(db_session, space.id, owner.id, role="owner")
    member = create_user_with_pin(db_session, "memory-public-member", "234567")
    create_space_member(db_session, space.id, member.id, role="member")
    operator = create_user_with_pin(db_session, "memory-public-operator", "345678", is_admin=True)
    create_space_member(db_session, space.id, operator.id, role="member")
    outsider = create_user_with_pin(db_session, "memory-public-outsider", "123456")
    db_session.commit()

    row = db_session.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(id=1, updated_at=utcnow())
        db_session.add(row)
    row.memory_enabled = True
    row.rag_enabled = True
    db_session.commit()
    terms.seed_builtin_packs(db_session)
    db_session.commit()

    query = "家谱称谓知识包 外婆"
    assert search_rag(
        db_session,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        query=query,
    )
    assert search_rag(
        db_session,
        actor=member,
        account=member.account,
        space_id=space.id,
        query=query,
    )
    # A platform operator can satisfy the SQL active-membership predicate, but
    # the server-side platform role still fails the RAG visibility check.
    assert (
        search_rag(
            db_session,
            actor=operator,
            account=operator.account,
            space_id=space.id,
            query=query,
        )
        == []
    )
    assert (
        search_rag(
            db_session,
            actor=outsider,
            account=outsider.account,
            space_id=space.id,
            query=query,
        )
        == []
    )


def test_authorized_document_rejects_non_household_scope(db_session):
    """附件授权文档只能以 household scope 入索引：写入时就拒绝，不留静默不可达。

    本测试取代旧的 `test_steward_cannot_retrieve_public_rag_documents`：那条断言
    以一个 `scope='public'` 的 `authorized_document` 为反例，而这种文档现在
    已经不可能存在（入索引即 422）。管家读不到公共语料的边界改由
    `test_rag_public_kinship.py::test_steward_cannot_read_public_kinship` 守护。
    """
    owner, space = create_agent_fixture(db_session, name="memory-authdoc-scope")
    create_space_member(db_session, space.id, owner.id, role="owner")
    with pytest.raises(HTTPException) as error:
        ingest_authorized_document(
            db_session,
            source_type="authorized_document",
            source_id="authdoc-public-1",
            text_value="must not be indexed",
            author_account_id=None,
            scope="public",
            space_id=None,
        )
    assert error.value.status_code == 422


def test_high_public_document_is_rejected(db_session):
    with pytest.raises(HTTPException) as error:
        ingest_authorized_document(
            db_session,
            source_type="authorized_document",
            source_id="public-high-1",
            text_value="must not be indexed",
            author_account_id=None,
            scope="public",
            space_id=None,
            sensitivity="high",
        )
    assert error.value.status_code == 422


def test_domain_event_tombstones_rag_document_and_chunks(db_session):
    owner, space = create_agent_fixture(db_session, name="memory-delete-event")
    create_space_member(db_session, space.id, owner.id, role="owner")
    document = ingest_authorized_document(
        db_session,
        source_type="profile",
        source_id=str(owner.id),
        text_value="Profile text to remove from retrieval.",
        author_account_id=owner.account.id,
        scope="household",
        space_id=space.id,
        sensitivity="normal",
    )
    db_session.commit()
    chunk = db_session.scalar(select(RAGChunk).where(RAGChunk.document_id == document.id))
    assert chunk is not None

    emit(
        db_session,
        event_type="profile.deleted",
        aggregate_type="profile",
        aggregate_id=owner.id,
        space_id=space.id,
        actor_account_id=owner.account.id,
        payload={"reason": "user_request"},
    )
    db_session.commit()

    db_session.refresh(document)
    db_session.refresh(chunk)
    assert document.status == "invalidated"
    assert chunk.status == "invalidated"
    assert (
        search_rag(
            db_session,
            actor=owner,
            account=owner.account,
            space_id=space.id,
            query="Profile text",
        )
        == []
    )


def test_shared_memory_isolation_and_revoke_tombstone(db_session):
    owner, space = create_agent_fixture(db_session, name="memory-owner-2")
    create_space_member(db_session, space.id, owner.id, role="owner")
    other = create_user_with_pin(db_session, "memory-other", "123456")
    db_session.commit()
    candidate = propose_candidate(
        db_session,
        author_account_id=owner.account.id,
        source={"kind": "manual"},
        source_quote="The private family archive uses a cedar box.",
        summary="The family archive uses a cedar box.",
        suggested_scope="household",
        purpose="family reference",
    )
    db_session.commit()
    memory = confirm_candidate(
        db_session,
        candidate_id=candidate.id,
        confirmer=owner,
        confirmer_account=owner.account,
        scope="household",
        space_id=space.id,
    )
    db_session.commit()

    assert (
        search_rag(
            db_session,
            actor=other,
            account=other.account,
            space_id=space.id,
            query="cedar box",
        )
        == []
    )
    assert search_rag(
        db_session,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        query="cedar box",
    )

    revoke_memory(db_session, memory_id=memory.id, account_id=owner.account.id)
    db_session.commit()
    assert (
        search_rag(
            db_session,
            actor=owner,
            account=owner.account,
            space_id=space.id,
            query="cedar box",
        )
        == []
    )


def test_assistant_context_degrades_to_empty_when_rag_disabled(db_session, monkeypatch):
    """RAG 关闭时助手仍可运行：context 降级为空（不把会话全文当补偿上下文）。"""
    monkeypatch.setattr(config, "RAG_ENABLED", False)
    owner, space = create_agent_fixture(db_session, name="rag-off")
    create_space_member(db_session, space.id, owner.id, role="owner")
    db_session.commit()

    built = cb.ContextBuilder(db_session).build(
        actor=owner,
        space_id=space.id,
        agent_kind="assistant",
        query="anything",
        run_id=None,
        provider_kind="openai_compatible",
    )
    # 不抛 RAG_DISABLED；context 为空且可信边界不变
    assert built.sources == ()
    assert built.as_data_blocks() == []
    assert built.provider_policy == "allowed"


def test_cross_space_confirmation_from_session_source_rejected(db_session):
    """P1 RAG session-space 绑定：空间 A 会话来源的候选不得确认到空间 B。"""
    from conftest import create_agent_message, create_agent_session

    owner, space_a = create_agent_fixture(db_session, name="rag-bind-a")
    create_space_member(db_session, space_a.id, owner.id, role="owner")
    # 空间 B：本人也是 active 成员（否则会被成员检查先拒绝，测不到绑定规则）
    from app.models.space import FamilySpace as _FS

    space_b = _FS(
        name="rag-bind-b-space", kind="household", owner_id=owner.id, created_at=owner.created_at
    )
    db_session.add(space_b)
    db_session.commit()
    create_space_member(db_session, space_b.id, owner.id, role="owner")

    session_a = create_agent_session(db_session, account_id=owner.account.id, space_id=space_a.id)
    message = create_agent_message(db_session, session_a, content={"text": "Space A only secret."})
    db_session.commit()

    candidate = propose_candidate(
        db_session,
        author_account_id=owner.account.id,
        source_message_id=message.id,
        source_quote="Space A only secret.",
        summary="A secret from space A conversation.",
        suggested_scope="household",
        purpose="family reference",
    )
    db_session.commit()

    with pytest.raises(HTTPException) as ei:
        confirm_candidate(
            db_session,
            candidate_id=candidate.id,
            confirmer=owner,
            confirmer_account=owner.account,
            scope=f"household:{space_b.id}",
        )
    assert ei.value.status_code == 422
    assert ei.value.detail["__api_error__"]["code"] == "MEMORY_SCOPE_FORBIDDEN"

    # 同空间确认不受影响
    memory = confirm_candidate(
        db_session,
        candidate_id=candidate.id,
        confirmer=owner,
        confirmer_account=owner.account,
        scope=f"household:{space_a.id}",
    )
    assert memory.space_id == space_a.id
