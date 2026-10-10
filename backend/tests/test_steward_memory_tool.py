"""Steward 记忆读取工具（10-10）：配置门禁、private 约束与来源限定。

## 这组测试证明的四件事

1. **配置就是门禁**：可读集为空时工具不被广告、直接调用被拒（403），而不是返回
   空列表——空列表会把「配置不允许」伪装成「没有相关内容」。
2. **private 只属于它的作者**：带 viewer 的 run 只能读该 viewer 本人的私有记忆；
   同一个空间里 space admin 的私有记忆读不到。这是防「管家空间级 kind 回落到
   space admin 身份后读到管理员私事」的那条门。
3. **无 viewer 的 kind 拿不到 private**，即使三层配置都允许。
4. **只读记忆**：工具限定 `source_type='memory'`，同一 scope 下的其它 RAG 材料
   （家族故事/授权文档）不进结果。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import config
from app.models.agent_provider import AgentSpaceProviderSetting
from app.models.platform_features import PlatformFeatureConfig
from app.services import agent_tools, memory_rag, steward_memory, steward_tools
from app.services.agent_execution import StewardExecution
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture, create_space_member, create_user_with_pin

TOOL = steward_tools.TOOL_SEARCH_MEMORY


# ---- 夹具 ----


def _enable_memory_and_rag(db) -> None:
    row = db.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(id=1, updated_at=utcnow())
        db.add(row)
    row.memory_enabled = True
    row.rag_enabled = True
    db.commit()


def _configure(db, *, space_id: int, platform: str, space: str, env: str) -> None:
    _enable_memory_and_rag(db)
    row = db.get(PlatformFeatureConfig, 1)
    assert row is not None
    row.steward_memory_scopes = platform
    setting = AgentSpaceProviderSetting(
        space_id=space_id, agent_kind="steward", steward_memory_scopes=space, enabled=True
    )
    db.add(setting)
    db.commit()
    config.STEWARD_MEMORY_SCOPES = env


def _memory(db, owner, summary: str, *, scope: str, space_id: int | None = None):
    candidate = memory_rag.propose_candidate(
        db,
        author_account_id=owner.account.id,
        source={"kind": "manual"},
        source_quote=summary,
        summary=summary,
        suggested_scope="private",
        purpose="steward memory tool tests",
    )
    memory = memory_rag.confirm_candidate(
        db,
        candidate_id=candidate.id,
        confirmer=owner,
        confirmer_account=owner.account,
        scope=scope if space_id is None else f"{scope}:{space_id}",
    )
    db.commit()
    return memory


def _execution(space_id: int, *, viewer_account_id: int | None = None) -> StewardExecution:
    return StewardExecution(
        run_id=999,
        steward_job_id=999,
        expected_attempt=1,
        space_id=space_id,
        viewer_account_id=viewer_account_id,
        agent_kind="steward",
        tool_allowlist=(TOOL,),
    )


def _call(db, execution, *, query: str = "silver locket", limit: int | None = None):
    payload: dict[str, object] = {"query": query}
    if limit is not None:
        payload["limit"] = limit
    return steward_tools.execute_steward_tool(
        db, execution=execution, name=TOOL, input_payload=payload
    )


# ---- 1. 配置就是门禁 ----


def test_tool_is_not_advertised_when_scopes_are_empty(db_session):
    _user, space = create_agent_fixture(db_session, name="memtool-empty")
    _configure(db_session, space_id=space.id, platform="", space="", env="")

    allowlist = agent_tools.default_allowlist("steward", db_session, space_id=space.id)
    assert TOOL not in allowlist


def test_tool_is_advertised_when_scopes_are_configured(db_session):
    _user, space = create_agent_fixture(db_session, name="memtool-ok")
    _configure(
        db_session,
        space_id=space.id,
        platform="household",
        space="household",
        env="household",
    )

    allowlist = agent_tools.default_allowlist("steward", db_session, space_id=space.id)
    assert TOOL in allowlist


def test_only_private_configured_and_no_viewer_is_not_advertised(db_session):
    # 只配了 private、而 run 没有 viewer → 可读集为空 → 不广告。
    _user, space = create_agent_fixture(db_session, name="memtool-private-only")
    _configure(db_session, space_id=space.id, platform="private", space="private", env="private")

    without_viewer = agent_tools.default_allowlist("steward", db_session, space_id=space.id)
    assert TOOL not in without_viewer
    with_viewer = agent_tools.default_allowlist(
        "steward", db_session, space_id=space.id, viewer_scope=True
    )
    assert TOOL in with_viewer


def test_empty_scopes_refuse_instead_of_returning_empty(db_session):
    user, space = create_agent_fixture(db_session, name="memtool-refuse")
    _configure(db_session, space_id=space.id, platform="", space="", env="")
    _memory(db_session, user, "The private note mentions a silver locket.", scope="private")

    with pytest.raises(HTTPException) as exc_info:
        _call(db_session, _execution(space.id, viewer_account_id=user.account.id))
    assert exc_info.value.status_code == 403
    # 关键：不是空结果。空结果会让模型以为「空间里没有这条内容」。
    assert "STEWARD_MEMORY_SCOPE_DENIED" in str(exc_info.value.detail)


# ---- 2. private 只属于它的作者 ----


def test_private_memory_is_readable_only_by_its_author_viewer(db_session):
    admin, space = create_agent_fixture(db_session, name="memtool-admin")
    viewer = create_user_with_pin(db_session, "memtool-viewer", "123456")
    create_space_member(db_session, space.id, viewer.id)
    _configure(
        db_session,
        space_id=space.id,
        platform="private,household",
        space="private,household",
        env="private,household",
    )
    _memory(db_session, viewer, "The viewer keeps a silver locket.", scope="private")
    _memory(db_session, admin, "The admin keeps a brass compass.", scope="private")

    # 带 viewer 的 run：只能读到该 viewer 本人的私有记忆。
    result = _call(
        db_session, _execution(space.id, viewer_account_id=viewer.account.id), query="keeps"
    )
    excerpts = " ".join(item["excerpt"] for item in result["results"])
    assert "silver locket" in excerpts
    assert "brass compass" not in excerpts


def test_space_scoped_kind_cannot_read_private_even_when_configured(db_session):
    admin, space = create_agent_fixture(db_session, name="memtool-nospaceviewer")
    _configure(db_session, space_id=space.id, platform="private", space="private", env="private")
    _memory(db_session, admin, "The admin keeps a brass compass.", scope="private")

    # 无 viewer 的 run（candidate/ranking/explanation）：可读集里没有 private，
    # 直接拒绝——绝不回落到 space admin 的账号去读它。
    with pytest.raises(HTTPException) as exc_info:
        _call(db_session, _execution(space.id), query="compass")
    assert exc_info.value.status_code == 403


def test_readable_scopes_drops_private_without_viewer(db_session):
    user, space = create_agent_fixture(db_session, name="memtool-scopes")
    _configure(
        db_session,
        space_id=space.id,
        platform="private,lineage",
        space="private,lineage",
        env="private,lineage",
    )
    assert steward_memory.readable_scopes(
        db_session, space_id=space.id, viewer_account_id=None
    ) == ("lineage",)
    assert steward_memory.readable_scopes(
        db_session, space_id=space.id, viewer_account_id=user.account.id
    ) == ("private", "lineage")


# ---- 3. 空间级 scope 的成员判据 ----


def test_household_memory_requires_active_membership(db_session):
    admin, space = create_agent_fixture(db_session, name="memtool-household")
    outsider = create_user_with_pin(db_session, "memtool-outsider", "123456")
    create_space_member(db_session, space.id, outsider.id, status="removed")
    _configure(
        db_session,
        space_id=space.id,
        platform="household",
        space="household",
        env="household",
    )
    _memory(
        db_session,
        admin,
        "The household recipe uses osmanthus.",
        scope="household",
        space_id=space.id,
    )

    # 已移出成员：空间级分支的 EXISTS 判据失败 → 读不到。
    result = _call(
        db_session,
        _execution(space.id, viewer_account_id=outsider.account.id),
        query="osmanthus",
    )
    assert result["results"] == []

    # active 成员读得到。
    result = _call(
        db_session, _execution(space.id, viewer_account_id=admin.account.id), query="osmanthus"
    )
    assert len(result["results"]) == 1
    assert result["results"][0]["source_type"] == "memory"
    assert result["results"][0]["scope"] == "household"


# ---- 4. 来源限定与 schema ----


def test_tool_returns_only_memory_source_type(db_session):
    user, space = create_agent_fixture(db_session, name="memtool-source-type")
    _configure(
        db_session,
        space_id=space.id,
        platform="household",
        space="household",
        env="household",
    )
    _memory(
        db_session,
        user,
        "The household recipe uses osmanthus.",
        scope="household",
        space_id=space.id,
    )

    # 同一查询、同一 eligibility，只换 source_types：证明限定生效（而不是碰巧没别的材料）。
    only_memory = memory_rag.search_rag(
        db_session,
        actor=user,
        account=user.account,
        space_id=space.id,
        query="osmanthus",
        agent_kind="steward",
        scope_allowlist=("household",),
        private_reader_account_id=None,
        source_types=("memory",),
    )
    assert len(only_memory) == 1

    no_memory = memory_rag.search_rag(
        db_session,
        actor=user,
        account=user.account,
        space_id=space.id,
        query="osmanthus",
        agent_kind="steward",
        scope_allowlist=("household",),
        private_reader_account_id=None,
        source_types=("family_story",),
    )
    assert no_memory == []


def test_unknown_source_type_is_rejected(db_session):
    user, space = create_agent_fixture(db_session, name="memtool-bad-source")
    _enable_memory_and_rag(db_session)
    with pytest.raises(HTTPException) as exc_info:
        memory_rag.search_rag(
            db_session,
            actor=user,
            account=user.account,
            space_id=space.id,
            query="anything",
            agent_kind="steward",
            source_types=("memory; DROP TABLE rag_documents",),
        )
    assert exc_info.value.status_code == 422


def test_input_schema_rejects_identity_fields(db_session):
    spec = agent_tools.REGISTRY[TOOL]
    for forbidden in (
        {"query": "x", "space_id": 1},
        {"query": "x", "account_id": 1},
        {"query": "x", "viewer_account_id": 1},
        {"query": "x", "scope": "private"},
        {"query": "x", "run_id": 1},
    ):
        with pytest.raises(agent_tools.ToolProtocolError):
            agent_tools.validate_input(spec, forbidden)


def test_output_carries_scopes_and_handles_not_raw_text(db_session):
    user, space = create_agent_fixture(db_session, name="memtool-output")
    _configure(
        db_session,
        space_id=space.id,
        platform="household",
        space="household",
        env="household",
    )
    _memory(
        db_session,
        user,
        "The household recipe uses osmanthus.",
        scope="household",
        space_id=space.id,
    )

    result = _call(db_session, _execution(space.id), query="osmanthus")
    assert result["scopes"] == ["household"]
    assert len(result["results"]) == 1
    entry = result["results"][0]
    assert entry["citation"].startswith("rag:")
    assert entry["excerpt"].startswith("The household recipe")
    # 句柄 + 摘要，不是原文全集。
    assert "summary" not in entry
