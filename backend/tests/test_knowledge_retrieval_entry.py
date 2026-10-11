"""U2：统一授权检索入口。

钉住三件事：

1. **派生规则集中在一处**：`scope_allowlist` / `private_reader_account_id` /
   `source_types` 由身份派生，不再散落在每个调用点的参数列表里；
2. **未知 kind fail closed**，不退回「读全部」；
3. **结构性守卫**：`search_rag` 只能在 `knowledge_retrieval` 内部被调用 —— 新增消费者
   不能绕过统一入口自己拼授权参数。

第 3 条是这个任务真正要防的事：`search_rag` 的默认值语义是「读全部」，
所以**最省事的写法恰好是最宽的授权**。
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from fastapi import HTTPException

from app.models.memory import MEMORY_SCOPES
from app.models.platform_features import PlatformFeatureConfig
from app.services import knowledge_retrieval
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture, create_user_with_pin

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"


def _features(db, *, rag: bool = True) -> None:
    row = db.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(id=1, memory_enabled=True, rag_enabled=rag, updated_at=utcnow())
        db.add(row)
    else:
        row.memory_enabled = True
        row.rag_enabled = rag
    db.flush()


def _identity(db, *, agent_kind, user, space, viewer_account_id=None, source_types=("memory",)):
    return knowledge_retrieval.RetrievalIdentity(
        agent_kind=agent_kind,
        actor=user,
        account=user.account,
        space_id=space.id,
        viewer_account_id=viewer_account_id,
        source_types=source_types,
    )


def test_assistant_identity_derives_the_full_memory_scope_set(db_session):
    user, space = create_agent_fixture(db_session, name="u2-assistant")
    identity = _identity(db_session, agent_kind="assistant", user=user, space=space)
    assert knowledge_retrieval.resolve_authorization(db_session, identity) == tuple(MEMORY_SCOPES)
    assert identity.private_reader_account_id == user.account.id


def test_steward_private_reader_is_the_viewer_never_the_admin(db_session):
    """空间级 steward（无 viewer）的私有读者必须是 NULL，不是 space admin 的账号。

    这是承重的：管家的空间级 kind 会回落到 space admin 作为身份，把 private 绑到
    「kind 能不能读」上就会读到管理员本人的私事。
    """
    admin, space = create_agent_fixture(db_session, name="u2-steward-admin")
    viewer = create_user_with_pin(db_session, "u2-steward-viewer", "123456")
    _features(db_session)
    with_viewer = _identity(
        db_session,
        agent_kind="steward",
        user=admin,
        space=space,
        viewer_account_id=viewer.account.id,
    )
    without_viewer = _identity(db_session, agent_kind="steward", user=admin, space=space)
    assert with_viewer.private_reader_account_id == viewer.account.id
    assert without_viewer.private_reader_account_id is None


def test_unknown_kind_fails_closed(db_session):
    user, space = create_agent_fixture(db_session, name="u2-unknown")
    identity = _identity(db_session, agent_kind="something_else", user=user, space=space)
    with pytest.raises(HTTPException) as excinfo:
        knowledge_retrieval.resolve_authorization(db_session, identity)
    assert excinfo.value.status_code == 422


def test_context_source_types_constant_is_gone():
    """`CONTEXT_SOURCE_TYPES` 必须**不存在**，而不只是被改成具体元组。

    它的名字描述单一消费者（"context"），却被 `context_builder` /
    `memory_rag.build_context` / `rag.py` 三个不同消费者复用；其中
    `ContextBuilder.build()` 同时服务 assistant 与 steward。保留一个语义含糊的共享常量，
    等于把「复用错常量」这条路留着。消费者必须各自写出自己是谁。
    """
    assert not hasattr(
        knowledge_retrieval, "CONTEXT_SOURCE_TYPES"
    ), "来源集必须按消费者显式声明，不得再有共享常量"


def test_retrieve_rejects_an_undeclared_source_set(db_session):
    """运行时传 `None` 必须 422，不得静默读全部。

    类型标注已经收窄成必填 `tuple[str, ...]`，但标注不约束运行时（未类型化的调用点、
    `**kwargs` 组装、反射都能绕过），而这个默认值的失效后果是「静默读全部」。
    """
    owner, space = create_agent_fixture(db_session, name="u2-none-source")
    _features(db_session)
    identity = _identity(db_session, agent_kind="assistant", user=owner, space=space)
    object.__setattr__(identity, "source_types", None)
    with pytest.raises(HTTPException) as excinfo:
        knowledge_retrieval.retrieve(db_session, identity, "anything")
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["__api_error__"]["code"] == "MEMORY_SCOPE_FORBIDDEN"


def test_empty_readable_set_is_an_authorization_conclusion_not_read_all(db_session):
    """可读集为空时返回空结果，且**不**退化成「读全部」。

    默认配置下 steward 的可读集就是空的。旧写法（`scope_allowlist=None`）在这一刻
    会读全部三个 scope —— 那正是本任务要消除的隐式放宽。
    """
    admin, space = create_agent_fixture(db_session, name="u2-empty")
    _features(db_session)
    identity = _identity(db_session, agent_kind="steward", user=admin, space=space)
    assert knowledge_retrieval.resolve_authorization(db_session, identity) == ()
    outcome = knowledge_retrieval.retrieve(db_session, identity, "anything")
    assert outcome.hits == ()
    assert outcome.scope_allowlist == ()
    assert outcome.scanned == 0


def test_outcome_distinguishes_no_content_from_authorization_filter(db_session):
    """`filtered_out` 把「没记过」与「不给你看」分开。

    RAG 之前无法区分这两者，而它们在产品上是完全不同的事。
    """
    owner, space = create_agent_fixture(db_session, name="u2-filter")
    _features(db_session)
    identity = _identity(db_session, agent_kind="assistant", user=owner, space=space)

    # 库里没有任何内容 → 不是「被过滤」，是真的没有。
    empty = knowledge_retrieval.retrieve(db_session, identity, "nothing here at all")
    assert empty.hits == ()
    assert empty.filtered_out is False

    # 有内容但被授权挡掉 → `filtered_out`。
    from app.services import memory_rag

    candidate = memory_rag.propose_candidate(
        db_session,
        author_account_id=owner.account.id,
        source={"kind": "manual"},
        source_quote="orchidgrove locket",
        summary="orchidgrove locket",
        suggested_scope="private",
        purpose="u2",
    )
    memory = memory_rag.confirm_candidate(
        db_session,
        candidate_id=candidate.id,
        confirmer=owner,
        confirmer_account=owner.account,
        scope="private",
    )
    memory_rag.index_memory(db_session, memory)
    db_session.commit()

    hit = knowledge_retrieval.retrieve(db_session, identity, "orchidgrove locket")
    assert hit.hits, "作者本人必须读得到自己的私有记忆"
    assert hit.filtered_out is False

    other = create_user_with_pin(db_session, "u2-filter-other", "123456")
    other_identity = _identity(db_session, agent_kind="assistant", user=other, space=space)
    blocked = knowledge_retrieval.retrieve(db_session, other_identity, "orchidgrove locket")
    assert blocked.hits == ()


def test_search_rag_is_only_called_from_the_unified_entry():
    """结构性守卫：`search_rag(` 只能出现在 `memory_rag` 与 `knowledge_retrieval` 内。

    为什么这是承重的：`search_rag` 的 `scope_allowlist=None` 语义是「读全部」，
    所以绕过统一入口的最省事写法恰好是最宽的授权。守卫让"新增一个消费者"必须
    经过身份派生。

    `memory_rag` 自身是定义处（含 `rag.py` 之外的内部分派），`knowledge_retrieval`
    是唯一被允许的调用方。
    """
    allowed = {"app/services/memory_rag.py", "app/services/knowledge_retrieval.py"}
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        rel = str(path.relative_to(APP_DIR.parent))
        if rel in allowed:
            continue
        source = path.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "search_rag"
            ):
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        "发现绕过统一授权检索入口直接调用 `search_rag` 的位置。"
        "`search_rag` 的默认值是「读全部 scope」，所以直接调用等于隐式放宽授权。"
        "改用 `knowledge_retrieval.retrieve(...)` 并以 `RetrievalIdentity` 声明身份：\n"
        + "\n".join(f"  {o}" for o in offenders)
    )
