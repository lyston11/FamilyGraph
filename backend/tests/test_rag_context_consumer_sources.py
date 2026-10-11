"""承重端到端：steward 的上下文不得读到 `authorized_document`，assistant 可以。

## 为什么这条必须用真实的 context build 而不是只看策略表

`test_rag_consumer_source_policy.py` 证明策略表**写对了**。本文件证明它**真的接在
检索路径上**——两者是不同的失败形态：

- 策略表写错 → 上一组测试失败；
- 策略表写对但 `ContextBuilder` 没按 `agent_kind` 取值（仍是本任务修掉的那个 bug）→
  只有真实的 context build 能发现。

被修掉的缺陷：`ContextBuilder.build(agent_kind=...)` 同时服务 assistant 与 steward
（它有显式的 steward 分支），但来源集恒为 `CONTEXT_SOURCE_TYPES`（`None` = 全部），
于是 steward 的上下文能读到 `authorized_document`——该类别由 grant 存在性把关，
**没有** `:is_assistant` 门，因此比 steward 自己的工具策略 `("memory",)` 更宽。

这是**收窄**（越界内容消失），不是放宽，所以本文件同时断言 assistant 侧仍然读得到，
以证明「steward 读不到」不是「授权文档整体坏了」。
"""

from __future__ import annotations

import pytest
from test_attachment_content import _attachment, _ctx

from app.commands.attachment_content import (
    confirm_content_version,
    create_content_version,
    create_grant,
)
from app.models.platform_features import PlatformFeatureConfig
from app.services import context_builder as cb
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture, create_space_member


def _enable_rag(db) -> None:
    row = db.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(id=1, updated_at=utcnow())
        db.add(row)
    row.memory_enabled = True
    row.rag_enabled = True
    db.commit()


def _authorized_document(db, *, name: str, text: str):
    """造一份已被明确授权给当前空间的附件文档，返回 (owner, space)。"""
    owner, space = create_agent_fixture(db, name=name)
    create_space_member(db, space.id, owner.id, role="owner")
    db.commit()
    attachment = _attachment(db, owner, kind="link")
    draft = create_content_version(db, _ctx(owner), attachment_id=attachment.id, text=text)
    version = confirm_content_version(db, _ctx(owner), version_id=draft.id)
    create_grant(db, _ctx(owner), version_id=version.id, space_id=space.id)
    db.commit()
    return owner, space


def _build(db, *, owner, space, agent_kind: str, query: str):
    return cb.ContextBuilder(db).build(
        actor=owner,
        space_id=space.id,
        agent_kind=agent_kind,
        query=query,
        run_id=None,
        provider_kind="openai_compatible",
    )


def test_steward_context_does_not_reach_authorized_documents(db_session):
    """steward 的上下文来源集是 `("memory",)`，读不到授权附件文档。"""
    _enable_rag(db_session)
    owner, space = _authorized_document(
        db_session, name="ctx-source-steward", text="祖屋照片说明：院子里的桂花树。"
    )

    built = _build(db_session, owner=owner, space=space, agent_kind="steward", query="祖屋 桂花树")
    assert [
        source for source in built.sources if source.source_type == "authorized_document"
    ] == [], "steward 上下文不得纳入附件授权文档"


def test_assistant_context_still_reaches_authorized_documents(db_session):
    """反证：同一份文档、同一个空间，assistant 上下文**读得到**。

    没有这条，上面那条断言可能只是因为授权文档整体不可检索而通过。
    """
    _enable_rag(db_session)
    owner, space = _authorized_document(
        db_session, name="ctx-source-assistant", text="祖屋照片说明：院子里的桂花树。"
    )

    built = _build(
        db_session, owner=owner, space=space, agent_kind="assistant", query="祖屋 桂花树"
    )
    assert [
        source for source in built.sources if source.source_type == "authorized_document"
    ], "assistant 上下文必须仍能读到授权附件文档"


def test_unknown_context_agent_kind_is_rejected(db_session):
    """未知 kind 的上下文构建 fail closed，不退化成最宽来源集。

    这里实际触发的是**更早**的一层门：`ContextBuilder.build()` 先过
    `is_policy_consumer_kind(agent_kind)`，未知 kind 直接 422 `POLICY_CONTEXT_INVALID`，
    还没走到来源集派生。两层各自独立成立（防御纵深）：本层拦在入口，
    `models.rag.context_source_types` 的 `ValueError` 由
    `test_rag_consumer_source_policy.py::test_unknown_agent_kind_fails_closed` 直接覆盖。

    无论哪层先拦，**不得**的结果是回退到「全部已声明来源」。
    """
    from fastapi import HTTPException

    _enable_rag(db_session)
    owner, space = _authorized_document(db_session, name="ctx-source-unknown", text="任意正文。")
    with pytest.raises(HTTPException) as excinfo:
        _build(db_session, owner=owner, space=space, agent_kind="system_admin", query="任意")
    assert excinfo.value.status_code == 422
