from __future__ import annotations

from sqlalchemy import select
from test_attachment_content import _attachment, _ctx

from app.commands.attachment_content import (
    confirm_content_version,
    create_content_version,
    create_grant,
    revoke_grant,
)
from app.models.platform_features import PlatformFeatureConfig
from app.models.rag import RAGDocument
from app.services import memory_rag
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture


def _enable_rag(db):
    row = db.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(id=1, updated_at=utcnow())
        db.add(row)
    row.memory_enabled = True
    row.rag_enabled = True
    db.commit()


def test_authorized_document_is_indexed_with_grant_and_revoked_before_cleanup(db_session):
    _enable_rag(db_session)
    owner, space = create_agent_fixture(db_session, name="RAG附件主人")
    attachment = _attachment(db_session, owner, kind="link")
    draft = create_content_version(
        db_session,
        _ctx(owner),
        attachment_id=attachment.id,
        text="祖屋照片说明：院子里的桂花树。",
    )
    version = confirm_content_version(db_session, _ctx(owner), version_id=draft.id)
    grant = create_grant(db_session, _ctx(owner), version_id=version.id, space_id=space.id)

    source_id = f"attachment:{attachment.id}:version:{version.id}:grant:{grant.id}"
    document = db_session.scalar(
        select(RAGDocument).where(
            RAGDocument.source_type == "authorized_document",
            RAGDocument.source_id == source_id,
        )
    )
    assert document is not None
    assert document.scope == "household"
    assert document.space_id == space.id
    assert document.owner_user_id == owner.id

    hits = memory_rag.search_rag(
        db_session,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        query="祖屋 桂花",
        source_types=("authorized_document",),
        for_model=False,
    )
    assert hits
    assert hits[0].source_type == "authorized_document"
    assert hits[0].citation_handle.startswith("rag:attachment:")

    revoked = revoke_grant(db_session, _ctx(owner), grant_id=grant.id, reason="撤销测试")
    assert revoked.status == "revoked"
    db_session.commit()
    assert (
        db_session.scalar(select(RAGDocument.status).where(RAGDocument.id == document.id))
        == "invalidated"
    )
    assert (
        memory_rag.search_rag(
            db_session,
            actor=owner,
            account=owner.account,
            space_id=space.id,
            query="祖屋 桂花",
            source_types=("authorized_document",),
            for_model=False,
        )
        == []
    )


def test_authorized_document_requires_explicit_source_type_filter_for_memory_tool():
    # Contract-level proof is kept beside the real integration test: callers must
    # opt into this category; `None` remains the low-level compatibility default.
    from app.services import agent_tools

    assert agent_tools.MEMORY_TOOL_SOURCE_TYPES == ("memory", "authorized_document")


def test_grant_source_shape_is_discriminated_from_profile_derived_documents():
    """`authorized_document` 下两种授权机制必须靠**完整形状**区分，不能靠前缀。

    背景（实测冲突）：`authorized_document` 同时承载两种来源——

    - 附件授权（本任务）：`attachment:<a>:version:<v>:grant:<g>`，由
      `agent_content_grants` 授权；
    - 档案派生内容（visibility_snapshot 清空声明）：`attachment:<subject>:<categories>`，
      由主体的四级阶梯授权，**没有** grant。

    若用笼统的 `attachment:` 前缀判别，第 2 类会被一并卡死（实测：profile 清空用例
    全部不可读）；若干脆不判别，第 1 类就绕过 grant，成为授权漏洞。
    """
    from app.services.memory_sources import is_attachment_grant_source

    # 附件授权形状 → 需要 grant 复核
    assert is_attachment_grant_source("attachment:7:version:2:grant:3") is True
    # 档案派生形状 → 不施加 grant 要求（由 _profile_clearance_ok 裁决）
    assert is_attachment_grant_source("attachment:1:dates") is False
    assert is_attachment_grant_source("attachment:1:dates,school") is False
    assert is_attachment_grant_source("public-with-profile") is False
    # 形状不完整一律 fail closed（当作非 grant 形状 → 由另一道门裁决；真正的
    # 附件文档永远由 ingest_attachment_content_grant 生成完整形状）
    assert is_attachment_grant_source("attachment:7:version:2") is False
    assert is_attachment_grant_source("attachment:7:version:2:grant:") is False
    assert is_attachment_grant_source("attachment:0:version:2:grant:3") is False
