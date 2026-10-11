"""U1：授权模型统一 —— 档案清空声明落到 RAG 语料文本。

`visibility.py` 的四级阶梯与字段级掩码原本到不了 RAG 语料：`_author_visible` 只把它
当"作者可见/不可见"一道门，读取路径用的是 `RAGDocument.scope` + 空间成员。

本文件钉住统一后的判据：

- `visibility_snapshot` 为空 → 走旧 scope 裁决（`memory` / `public_kinship` 不变）；
- 非空 → 由**主体的四级阶梯**裁决，字段掩码与未成年人 overlay 自动生效；
- `HIGH_RISK_CATEGORIES` 只有主体本人可读；
- 未知类别 fail closed（写入侧 422，读取侧只有主体可读）；
- 声明 key 不符 → 不可读，不按新语义重解释。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.platform_features import PlatformFeatureConfig
from app.models.rag import RAGDocument
from app.services import memory_rag, visibility
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture, create_space_member, create_user_with_pin


def _features(db, *, rag: bool = True) -> None:
    row = db.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(id=1, memory_enabled=True, rag_enabled=rag, updated_at=utcnow())
        db.add(row)
    else:
        row.memory_enabled = True
        row.rag_enabled = rag
    db.flush()


def _disclose(db, target, category: str, *, space_id: int | None = None, allowed: bool = True):
    """落一条披露偏好行。

    RAG 的字段清空**必须**走披露：`_PURPOSE_LEVEL_CAP[PURPOSE_RAG]` 把层级封在
    `lineage_summary`，而该层只清 `disclosed_categories` 映射到的字段。这不是绕路，
    而是正确的语义 —— 语料里的档案内容只能随主体**已披露**的类别开放。
    """
    from app.models.v2_foundation import DisclosurePreference

    db.add(
        DisclosurePreference(
            profile_id=target.id,
            category=category,
            scope="space" if space_id is not None else "global",
            space_id=space_id,
            allowed=allowed,
            updated_at=utcnow(),
        )
    )
    db.flush()


def _ingest(db, *, subject, space, categories, scope="household", author=None):
    return memory_rag.ingest_authorized_document(
        db,
        source_type="authorized_document",
        source_id=f"attachment:{subject.id}:{','.join(categories) or 'none'}",
        text_value="synthetic attachment text about the subject",
        author_account_id=(author or subject).account.id,
        scope=scope,
        space_id=space.id,
        subject_user_id=subject.id,
        required_categories=categories,
    )


def _readable(db, document, viewer, space) -> bool:
    return memory_sources_readable(db, document, viewer, space)


def memory_sources_readable(db, document, viewer, space) -> bool:
    from app.services import memory_sources

    return memory_sources.document_readable(
        db,
        document,
        actor=viewer,
        account=viewer.account,
        space_id=space.id,
        agent_kind="assistant",
        private_reader_account_id=viewer.account.id,
    )


def test_memory_and_public_kinship_declare_no_profile_requirement(db_session):
    """`memory` / `public_kinship` 必须保持空声明 —— 它们的行为由 scope 裁决。

    这是"不改变既有行为"的锚点：scope 是主体在确认候选时**选过的**可见性，
    再叠加字段掩码就等于推翻主体的显式选择。
    """
    user, space = create_agent_fixture(db_session, name="u1-empty")
    _features(db_session)
    candidate = memory_rag.propose_candidate(
        db_session,
        author_account_id=user.account.id,
        source={"kind": "manual"},
        source_quote="orchidgrove synthetic",
        summary="orchidgrove synthetic",
        suggested_scope="private",
        purpose="u1",
    )
    memory = memory_rag.confirm_candidate(
        db_session,
        candidate_id=candidate.id,
        confirmer=user,
        confirmer_account=user.account,
        scope="private",
    )
    document = memory_rag.index_memory(db_session, memory)
    assert document.visibility_snapshot == {}
    assert visibility.profile_clearance_snapshot(None, ["school"]) == {}


def test_high_risk_category_is_readable_only_by_the_subject(db_session):
    """HIGH_RISK 类别（health/address/school/contact/private_notes）不因空间身份开放。"""
    subject, space = create_agent_fixture(db_session, name="u1-hr-subject")
    other = create_user_with_pin(db_session, "u1-hr-other", "123456")
    create_space_member(db_session, space.id, other.id)
    _features(db_session)
    document = _ingest(db_session, subject=subject, space=space, categories=["school"])
    db_session.commit()

    assert _readable(db_session, document, subject, space) is True
    assert _readable(db_session, document, other, space) is False


def test_non_high_risk_category_follows_the_subject_disclosure(db_session):
    """非 HIGH_RISK 类别随**主体披露**开放 —— 未披露则他人不可读，披露后可读。

    `PURPOSE_RAG` 被 `_PURPOSE_LEVEL_CAP` 封在 `lineage_summary`，所以 RAG 读者的
    字段清空来自 `disclosed_categories`，不是来自 household 成员身份。这正是把披露
    系统接到语料文本上的意义：空间成员身份本身不足以读别人档案派生的内容。
    """
    subject, space = create_agent_fixture(db_session, name="u1-dates-subject")
    other = create_user_with_pin(db_session, "u1-dates-other", "123456")
    create_space_member(db_session, space.id, other.id)
    _features(db_session)
    document = _ingest(db_session, subject=subject, space=space, categories=["dates"])
    db_session.commit()

    assert _readable(db_session, document, subject, space) is True
    assert _readable(db_session, document, other, space) is False

    _disclose(db_session, subject, "dates", space_id=space.id)
    db_session.commit()
    assert _readable(db_session, document, other, space) is True


def test_minor_overlay_applies_to_profile_derived_content(db_session):
    """未成年人 overlay 最后收紧：`dates`（birth/death）对非本人不可读。

    这是本任务的核心价值：语料矩阵任务确认"未成年人 overlay 在 RAG 侧没有等价机制"。
    这里不需要在 RAG 层复制 overlay —— `evaluate` 已经移除 birth/death/bio，
    `required_clearance_satisfied` 只是要求它们为 clear。
    """
    subject, space = create_agent_fixture(db_session, name="u1-minor")
    subject.birth = {"year": utcnow().year - 10}
    other = create_user_with_pin(db_session, "u1-minor-adult", "123456")
    create_space_member(db_session, space.id, other.id)
    _features(db_session)
    document = _ingest(db_session, subject=subject, space=space, categories=["dates"])
    db_session.commit()

    decision = visibility.evaluate(
        db_session, other, subject, space_context=space.id, purpose=visibility.PURPOSE_RAG
    )
    # overlay 确实移除了 birth/death —— 前提成立，测试才有意义。
    assert decision.fields["birth"] == visibility.FIELD_MASKED
    assert _readable(db_session, document, other, space) is False
    assert _readable(db_session, document, subject, space) is True


def test_unknown_category_fails_closed_on_both_sides(db_session):
    """未知类别：写入侧 422，读取侧只有主体可读。"""
    subject, space = create_agent_fixture(db_session, name="u1-unknown")
    _features(db_session)
    with pytest.raises(HTTPException) as excinfo:
        _ingest(db_session, subject=subject, space=space, categories=["not_a_category"])
    assert excinfo.value.status_code == 422

    # 读取侧：手工塞入未知类别，证明复核层保守处理（不是同义反复）。
    document = _ingest(db_session, subject=subject, space=space, categories=["dates"])
    other = create_user_with_pin(db_session, "u1-unknown-other", "123456")
    create_space_member(db_session, space.id, other.id)
    document.visibility_snapshot = {
        "subject_user_id": subject.id,
        "required_categories": ["not_a_category"],
        "key": visibility.PROFILE_CLEARANCE_KEY,
    }
    db_session.commit()
    assert _readable(db_session, document, other, space) is False
    assert _readable(db_session, document, subject, space) is True


def test_snapshot_key_mismatch_is_unreadable(db_session):
    """声明 key 不符 → 不可读（fail closed），不按新语义重解释。"""
    subject, space = create_agent_fixture(db_session, name="u1-key")
    _features(db_session)
    document = _ingest(db_session, subject=subject, space=space, categories=["dates"])
    document.visibility_snapshot_key = "profile-v0-retired"
    db_session.commit()
    assert _readable(db_session, document, subject, space) is False


def test_profile_categories_cannot_be_public(db_session):
    """`public` 的定义是"不含个人数据"；声明了档案类别就不可能不含。"""
    subject, space = create_agent_fixture(db_session, name="u1-public")
    _features(db_session)
    with pytest.raises(HTTPException) as excinfo:
        memory_rag.ingest_authorized_document(
            db_session,
            source_type="authorized_document",
            source_id="public-with-profile",
            text_value="text",
            author_account_id=subject.account.id,
            scope="public",
            space_id=None,
            subject_user_id=subject.id,
            required_categories=["dates"],
        )
    assert excinfo.value.status_code == 422


def test_snapshot_participates_in_document_identity(db_session):
    """同 (source_type, source_id, revision) 但声明不同 → 索引冲突，不静默覆盖。"""
    subject, space = create_agent_fixture(db_session, name="u1-identity")
    _features(db_session)
    _ingest(db_session, subject=subject, space=space, categories=["dates"])
    db_session.commit()
    with pytest.raises(HTTPException):
        memory_rag.ingest_authorized_document(
            db_session,
            source_type="authorized_document",
            source_id=f"attachment:{subject.id}:dates",
            text_value="synthetic attachment text about the subject",
            author_account_id=subject.account.id,
            scope="household",
            space_id=space.id,
            subject_user_id=subject.id,
            required_categories=["school"],
        )


def test_subject_derived_document_is_searchable_by_the_ladder(db_session):
    """端到端：已披露时空间成员能检索到它，非成员仍不能（scope 层仍然挡住）。"""
    subject, space = create_agent_fixture(db_session, name="u1-e2e")
    member = create_user_with_pin(db_session, "u1-e2e-member", "123456")
    outsider = create_user_with_pin(db_session, "u1-e2e-outsider", "123456")
    create_space_member(db_session, space.id, member.id)
    _features(db_session)
    _disclose(db_session, subject, "dates", space_id=space.id)
    _ingest(db_session, subject=subject, space=space, categories=["dates"])
    db_session.commit()

    def hits(viewer):
        return memory_rag.search_rag(
            db_session,
            actor=viewer,
            account=viewer.account,
            space_id=space.id,
            query="synthetic attachment",
            agent_kind="assistant",
            private_reader_account_id=viewer.account.id,
            source_types=("authorized_document",),
        )

    assert hits(member)
    assert hits(outsider) == []
    documents = db_session.scalars(select(RAGDocument)).all()
    assert len(documents) == 1
