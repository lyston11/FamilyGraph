"""U6：稳定前缀与增量上下文。

## 问题

steward 的 prompt 有 **digest 契约**：`prompt_digest = sha256(f"{system}\\n{user_content}")`，
发送前 fence 会核对。`candidate_user_content` 按字节预算取**确定前缀子集**，正是为了让
`input_hash` / `prompt_digest` 稳定 —— 否则发送前 fence 会误判、拒绝率上升。

**记忆是易变的**（取代、有效区间、新确认）。把记忆塞进 `_user_content_for` 会让 digest
随记忆抖动。因此本任务的约定是：

| 部分 | 载体 | 性质 |
|---|---|---|
| 冻结基线 | `_user_content_for` 投影（digest 覆盖它） | **必须**与记忆状态无关 |
| 易变增量 | `familygraph.steward.search_memory` 工具结果 | 不进静态 prompt |

## 本文件钉住什么

1. **投影不随记忆变化**：新增/确认/取代记忆后，同一 attempt 的 `_user_content_for` 与
   `prompt_digest` 逐字节不变；
2. **结构性守卫**：`steward_assist` 不得调用检索入口 —— 让「以后有人把记忆内联进投影」
   直接失败，而不是等到生产上 fence 开始拒单。

第 2 条是承重的：第 1 条只能证明**当前**实现没抖动，第 2 条挡住**将来**的内联。
"""

from __future__ import annotations

import ast
import hashlib
import pathlib

import pytest
from sqlalchemy import select
from test_steward_candidate_evidence_integration import _core, _world

from app import config
from app.models.memory import Memory
from app.models.steward import StewardAssistPlan, StewardModelCall
from app.services import memory_rag, steward_assist

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    """Steward engine on, Pi carrier off，并打开记忆（否则候选确认会被拒）。"""
    for flag in (
        "STEWARD_ENABLED",
        "PERSONAL_FAMILY_VIEW_ENABLED",
        "STEWARD_ASSIST_CANDIDATE",
        "MEMORY_ENABLED",
    ):
        monkeypatch.setattr(config, flag, True)
    for flag in ("STEWARD_WORKER_ENABLED", "STEWARD_PI_RUNTIME_ENABLED"):
        monkeypatch.setattr(config, flag, False)


def _candidate_attempt(db_session):
    world = _world(db_session)
    _core(db_session, world)
    plan = db_session.scalar(select(StewardAssistPlan).order_by(StewardAssistPlan.id.desc()))
    assert plan is not None
    attempt = db_session.scalar(
        select(StewardModelCall).where(
            StewardModelCall.plan_id == plan.id,
            StewardModelCall.assist_kind == "candidate",
        )
    )
    assert attempt is not None, "前提：candidate 尝试存在（否则本测试什么也没证明）"
    return world, attempt


def _digest(user_content: str) -> str:
    return hashlib.sha256(user_content.encode("utf-8")).hexdigest()


def test_projection_and_digest_do_not_move_when_memory_changes(db_session):
    """新增并确认记忆后，同一 attempt 的投影与 digest 必须逐字节不变。

    这是 `prompt_digest` 契约在「知识库是活的」这一前提下的可维持条件：投影只能依赖
    不会被知识写入改变的状态。记忆走工具（`familygraph.steward.search_memory`），
    不进静态 prompt。
    """
    world, attempt = _candidate_attempt(db_session)
    before = steward_assist._user_content_for(db_session, attempt)
    before_digest = _digest(before)
    assert before.strip(), "前提：投影非空（否则「不变」是同义反复）"

    # 知识库发生真实变化：新增一条记忆候选并确认它。
    candidate = memory_rag.propose_candidate(
        db_session,
        author_account_id=world.a.account.id,
        source={"kind": "manual"},
        source_quote="orchidgrove stable-prefix probe",
        summary="orchidgrove stable-prefix probe",
        suggested_scope="private",
        purpose="u6",
    )
    memory = memory_rag.confirm_candidate(
        db_session,
        candidate_id=candidate.id,
        confirmer=world.a,
        confirmer_account=world.a.account,
        scope="private",
    )
    memory_rag.index_memory(db_session, memory)
    db_session.commit()
    assert db_session.scalars(select(Memory).where(Memory.id == memory.id)).first() is not None

    after = steward_assist._user_content_for(db_session, attempt)
    assert after == before
    assert _digest(after) == before_digest


def test_steward_assist_never_calls_the_retrieval_entry():
    """结构性守卫：`steward_assist` 不得调用检索入口。

    把记忆内联进投影会让 `prompt_digest` 随知识写入抖动，而 digest 是发送前 fence 的
    判据 —— 表现为「管家 run 开始随机被拒」，排查成本极高（没有错误码指向根因）。

    允许的调用方是工具路径（`steward_tools`）与上下文构建（`context_builder`）；
    `steward_assist` 的投影必须只依赖不会被知识写入改变的状态。
    """
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path.name != "steward_assist.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name in {"retrieve", "search_rag"}:
                    offenders.append(f"{path.name}:{node.lineno} {name}()")
    assert not offenders, (
        "steward 的投影（digest 覆盖的静态 prompt）不得包含检索结果。"
        "记忆是易变的，内联会让 `input_hash`/`prompt_digest` 抖动，抬高发送前 fence 的"
        "拒绝率。易变部分应当经 `familygraph.steward.search_memory` 工具进入上下文：\n"
        + "\n".join(f"  {o}" for o in offenders)
    )
