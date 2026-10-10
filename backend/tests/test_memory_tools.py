"""受控记忆工具（P2-b）：`search_memory` 只读、`propose_memory` 只提议。

## 这组测试守住的三条边界

1. **只能提议，不能确认**：`propose_memory` 的产物必须是 `pending` 候选，
   且**不会**产生可检索的 `Memory`/`RAGDocument`。确认是用户的明示同意，
   不是模型能代替的动作。
2. **检索不新增路径**：`search_memory` 复用 `search_rag`，因此授权与
   `search_rag` 逐字相同——测试用同一 query 比两侧结果的 `source_id` 集合。
3. **不广告无法使用的能力**：记忆或检索未启用时，两个工具都不进 allowlist。
   广告一个必然被拒的工具只会消耗模型调用并让它误判自己的能力。
"""

from __future__ import annotations

import pytest
from test_agent_query_tools import _assistant_run, _call, _error_code

from app.models.memory import Memory, MemoryCandidate
from app.models.platform_features import PlatformFeatureConfig
from app.models.rag import RAGDocument
from app.services import agent_queue, agent_tools, memory_rag
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture, create_agent_message, create_agent_session


def _set_features(db, *, memory: bool, rag: bool) -> None:
    """写 `platform_feature_configs` 行；`platform_features` 计算状态是 frozen 的。"""
    row = db.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(id=1, updated_at=utcnow())
        db.add(row)
    row.memory_enabled = memory
    row.rag_enabled = rag
    db.commit()


SEARCH = agent_tools.TOOL_SEARCH_MEMORY
PROPOSE = agent_tools.TOOL_PROPOSE_MEMORY


@pytest.fixture()
def memory_world(db_session):
    owner, space = create_agent_fixture(db_session, name="memory-tools")
    _set_features(db_session, memory=True, rag=True)
    session_row = create_agent_session(db_session, account_id=owner.account.id, space_id=space.id)
    return owner, space, session_row


def _confirm(db, owner, text: str) -> Memory:
    candidate = memory_rag.propose_candidate(
        db,
        author_account_id=owner.account.id,
        source={"kind": "manual"},
        source_quote=text,
        summary=text,
        suggested_scope="private",
        purpose="tools test",
    )
    return memory_rag.confirm_candidate(
        db,
        candidate_id=candidate.id,
        confirmer=owner,
        confirmer_account=owner.account,
        scope="private",
    )


def test_tools_are_registered_as_assistant_only():
    for name in (SEARCH, PROPOSE):
        spec = agent_tools.REGISTRY[name]
        assert spec.required_kind == "assistant", name
        assert spec.version == 1
    # Steward 不得进入记忆工具：记忆是账号级私有事实。
    steward_allowlist = agent_tools.default_allowlist("steward")
    assert SEARCH not in steward_allowlist and PROPOSE not in steward_allowlist


def test_search_memory_matches_search_rag_exactly(db_session, memory_world):
    """授权等价性：不是靠约定，而是同一段代码。"""
    owner, space, session_row = memory_world
    memory = _confirm(db_session, owner, "奶奶最喜欢喝龙井茶，早上一定要泡一杯。")
    db_session.commit()
    run = _assistant_run(db_session, session_row, [SEARCH])

    tool_result = _call(db_session, run, session_row, SEARCH, {"query": "奶奶喜欢喝什么茶"})
    direct = memory_rag.search_rag(
        db_session,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        query="奶奶喜欢喝什么茶",
        limit=5,
        for_model=True,
    )
    assert [item["source_id"] for item in tool_result["results"]] == [
        hit.source_id for hit in direct
    ]
    assert str(memory.id) in [item["source_id"] for item in tool_result["results"]]
    # 输出只含句柄与元数据，不含来源敏感字段以外的内容。
    assert tool_result["results"][0]["citation"].startswith("rag:")


def test_search_memory_does_not_read_other_source_types(db_session, memory_world):
    """目的限定：记忆工具只读 `memory`，不得把其它语料交给助手。

    这条防的是一个真实缺陷：`_search_memory_tool` 调 `search_rag` 时**没传
    `source_types`**，而 `search_rag` 的 `source_types=None` 是「读全部类别」。
    2026-10-10 接入公共称谓语料（`public_kinship`）后，它已经静默进了这个工具的
    结果——工具名叫「记忆」，返回的却是空间外的公共知识。`authorized_document` /
    `family_story` 落地时会以同样方式静默继承。

    反证：把 `source_types` 参数去掉，这条断言必须失败（下方同时验证
    `search_rag(source_types=None)` 确实能召回公共语料，证明差别是承重的）。
    """
    from app.services import terms

    owner, space, session_row = memory_world
    _confirm(db_session, owner, "奶奶最喜欢喝龙井茶，早上一定要泡一杯。")
    terms.seed_builtin_packs(db_session)
    db_session.commit()
    run = _assistant_run(db_session, session_row, [SEARCH])

    query = "外婆 舅舅 称谓"
    tool_result = _call(db_session, run, session_row, SEARCH, {"query": query})
    assert [item["source_type"] for item in tool_result["results"]] == [], tool_result["results"]

    # 反证：不限定类别时同一查询确实能召回公共语料——所以「结果为空」不是
    # 因为语料不存在或查询无效，而是因为目的限定真的生效了。
    unfiltered = memory_rag.search_rag(
        db_session,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        query=query,
        limit=5,
        for_model=True,
    )
    assert any(
        hit.source_type == "public_kinship" for hit in unfiltered
    ), f"公共语料未建立或不匹配，反证无效：{[h.source_type for h in unfiltered]}"

    # 类别元组是显式枚举：新增 source_type 默认对助手也不可读。
    assert agent_tools.MEMORY_TOOL_SOURCE_TYPES == ("memory",)
    assert "public_kinship" not in agent_tools.MEMORY_TOOL_SOURCE_TYPES


def test_search_memory_rejects_empty_and_out_of_range(db_session, memory_world):
    _owner, _space, session_row = memory_world
    run = _assistant_run(db_session, session_row, [SEARCH])
    with pytest.raises(Exception) as empty:
        _call(db_session, run, session_row, SEARCH, {"query": "   "})
    assert _error_code(empty) == "AGENT_TOOL_SCHEMA_INVALID"
    with pytest.raises(Exception) as too_many:
        _call(db_session, run, session_row, SEARCH, {"query": "茶", "limit": 99})
    assert _error_code(too_many) == "AGENT_TOOL_SCHEMA_INVALID"


def test_propose_memory_creates_pending_candidate_only(db_session, memory_world):
    """核心边界：工具产物必须是 pending 候选，且不产生可检索记忆或索引。"""
    owner, _space, session_row = memory_world
    run = _assistant_run(db_session, session_row, [PROPOSE])
    # 工具必须引用**本轮用户的原始消息全文**，因此 run 必须挂一条 user 消息。
    message = create_agent_message(
        db_session,
        session_row,
        content={"text": "请记住：外婆生日是农历八月初二。"},
    )
    run.message_id = message.id
    db_session.flush()

    output = _call(db_session, run, session_row, PROPOSE, {"summary": "外婆生日是农历八月初二。"})
    assert output["status"] == "pending"
    assert output["requires_user_confirmation"] is True
    assert output["suggested_scope"] == "private"

    candidate = db_session.get(MemoryCandidate, output["candidate_id"])
    assert candidate is not None and candidate.status == "pending"
    # 原话必须是用户原文全文，不是模型转述——来源全等校验依赖这一点。
    assert candidate.source_quote == "请记住：外婆生日是农历八月初二。"
    assert candidate.extractor_version == agent_tools.MEMORY_TOOL_EXTRACTOR_VERSION
    # 关键断言：没有 Memory，也没有 RAGDocument。
    assert db_session.query(Memory).filter(Memory.source_candidate_id == candidate.id).count() == 0
    assert db_session.query(RAGDocument).count() == 0


def test_propose_memory_requires_original_user_message(db_session, memory_world):
    """没有可引用的用户消息时必须拒绝，不能用模型文本伪造来源。"""
    _owner, _space, session_row = memory_world
    run = _assistant_run(db_session, session_row, [PROPOSE])
    with pytest.raises(Exception) as excinfo:
        _call(db_session, run, session_row, PROPOSE, {"summary": "随便什么"})
    assert _error_code(excinfo) == "AGENT_TOOL_RUN_INVALID"


def test_propose_memory_is_not_in_search_results_before_confirmation(db_session, memory_world):
    """提议之后、确认之前，这条内容不得出现在检索里。"""
    owner, space, session_row = memory_world
    run = _assistant_run(db_session, session_row, [PROPOSE])
    message = create_agent_message(
        db_session,
        session_row,
        content={"text": "请记住：大伯调到大连工作了。"},
    )
    run.message_id = message.id
    db_session.flush()
    _call(db_session, run, session_row, PROPOSE, {"summary": "大伯调到大连工作了。"})
    db_session.flush()

    hits = memory_rag.search_rag(
        db_session,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        query="大伯在哪里工作",
        limit=5,
        for_model=True,
    )
    assert hits == [], "未确认的候选绝不能进入检索结果"


def test_memory_tools_are_not_advertised_when_disabled(db_session):
    """未启用记忆/检索时不广告这两个工具（与 web / viewer 工具同一口径）。"""
    owner, space = create_agent_fixture(db_session, name="tools-disabled")
    _set_features(db_session, memory=False, rag=False)
    allowlist = agent_tools.default_allowlist(
        "assistant", db_session, account_id=owner.account.id, space_id=space.id
    )
    assert SEARCH not in allowlist and PROPOSE not in allowlist

    # 只启用记忆：`propose_memory` 可用，但 `search_memory` 仍不可用
    # （检索未启用时 search_rag 会拒绝）。
    _set_features(db_session, memory=True, rag=False)
    allowlist = agent_tools.default_allowlist(
        "assistant", db_session, account_id=owner.account.id, space_id=space.id
    )
    assert PROPOSE in allowlist
    assert SEARCH not in allowlist

    # 只启用检索：两个都不可用（记忆是前提）。
    _set_features(db_session, memory=False, rag=True)
    allowlist = agent_tools.default_allowlist(
        "assistant", db_session, account_id=owner.account.id, space_id=space.id
    )
    assert SEARCH not in allowlist and PROPOSE not in allowlist

    # 无 db 作用域时无法判定，一律不广告。
    assert SEARCH not in agent_tools.default_allowlist("assistant")
    assert PROPOSE not in agent_tools.default_allowlist("assistant")


def test_memory_tools_require_run_running_and_allowlist(db_session, memory_world):
    """两个工具都受既有四道门禁约束，不是旁路。"""
    _owner, _space, session_row = memory_world
    run = _assistant_run(db_session, session_row, [SEARCH])
    with pytest.raises(Exception) as excinfo:
        _call(db_session, run, session_row, PROPOSE, {"summary": "不在白名单"})
    assert _error_code(excinfo) == "AGENT_TOOL_SCOPE_DENIED"


def test_steward_cannot_call_memory_tools(db_session, memory_world):
    """Steward 即便伪造白名单也不能调用（required_kind 门禁）。"""
    _owner, _space, session_row = memory_world
    run = _assistant_run(db_session, session_row, [SEARCH])
    with pytest.raises(Exception) as excinfo:
        agent_tools.execute(
            db_session,
            run,
            session_row,
            {"agent_kind": "steward"},
            name=SEARCH,
            version=1,
            input_payload={"query": "茶"},
        )
    assert _error_code(excinfo) == "AGENT_TOOL_SCOPE_DENIED"


def test_queue_allowlist_includes_memory_tools_when_enabled(db_session):
    """经真实的 `default_allowlist` 路径（而非硬编码）确认工具确实可被广告。"""
    owner, space = create_agent_fixture(db_session, name="tools-enabled")
    _set_features(db_session, memory=True, rag=True)
    allowlist = agent_tools.default_allowlist(
        "assistant", db_session, account_id=owner.account.id, space_id=space.id
    )
    assert SEARCH in allowlist and PROPOSE in allowlist
    assert agent_queue is not None
