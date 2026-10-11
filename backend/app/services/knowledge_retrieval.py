"""统一授权检索入口（U2）：一个 execution identity，一个检索服务。

## 为什么需要这一层

`memory_rag.search_rag` 已经是唯一的检索实现，但它的三个授权参数
（`scope_allowlist` / `private_reader_account_id` / `source_types`）**在多数调用点上
靠默认值**：

- `scope_allowlist=None` 的语义是「三个 scope 全开」——那是 assistant 的语义，
  却被未改造的调用点当成通用默认；
- `private_reader_account_id` 未传时会回落到调用方自己的 `account.id`；
- `source_types=None` 的语义是「全部来源类别」。

结果是「谁在读、被允许读什么」散落在每个调用点的参数列表里，**新增一个消费者时最省事
的写法恰好是最宽的授权**。本模块把这件事反过来：消费者声明**身份**，授权参数由身份
派生，派生规则集中在一处且对未知 kind fail closed。

## 分层的职责

| 层 | 位置 | 职责 |
|---|---|---|
| 身份 | `RetrievalIdentity` | 谁在读：agent kind、actor、account、space、viewer |
| 派生 | `resolve_authorization` | 从身份算出允许集、私有读者、来源目的 |
| 检索 | `memory_rag.search_rag` | 候选收集、重排、引用投影 |
| 过滤 | `_ELIGIBILITY_SQL` + `_can_read_document` | **两层各自独立挡住** |

本模块**不**复制任何授权判据：阶梯、`HIGH_RISK_CATEGORIES`、档案清空声明、未成年人
overlay 全部仍在 `visibility.py` 与 `memory_sources.py`。它只负责把身份翻译成参数。

## 为什么两层过滤不合并

SQL 层是可索引的粗筛（scope 允许集、来源类型、取代/有效区间），复核层是需要读关系、
成员与披露记录的细判。把细判塞进 SQL 会让它降级成粗筛；把粗筛去掉会让复核层面对
无界候选。两者都必须独立成立——任何一层单独失效都不得让内容漏出。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.errors import MEMORY_SCOPE_FORBIDDEN, raise_api_error
from app.models.account import Account
from app.models.memory import MEMORY_SCOPES
from app.models.rag import RAG_SOURCE_TYPES
from app.models.user import User
from app.services import memory_rag
from app.services.rag_query import QueryPlan

# 注：本模块**不**定义来源类别常量。来源策略的唯一真源是
# `app.models.rag.RAG_CONSUMER_SOURCE_TYPES`；`agent_tools` 与 `steward_memory` 的类别元组
# 是它的别名。
#
# 此前这里有一个 `CONTEXT_SOURCE_TYPES = None`（语义是「全部已声明类别」），被三个不同
# 消费者复用；其中 `ContextBuilder.build()` 同时服务 assistant 与 steward（它按 agent_kind
# 分支），却无论哪种 kind 都拿到同一份「全部」——于是 steward 的上下文能读到
# `authorized_document`（该类别由 grant 存在性把关，**没有** `:is_assistant` 门），
# 比 steward 自己的工具策略 `("memory",)` 更宽。
#
# 该常量已删除而不是改成某个具体元组：它的**名字**描述单一消费者，保留它就会继续被
# 其他消费者复用。现在每个调用点必须写出自己是谁。


@dataclass(frozen=True)
class RetrievalIdentity:
    """谁在读知识。所有授权参数从它派生。"""

    agent_kind: str
    actor: User
    account: Account
    space_id: int
    #: 来源目的限定。**必填**：`None` 的语义是「全部已声明来源」，那是最宽的授权，
    #: 不该是默认值。忘记声明应在构造期或入口期失败，而不是静默读全部。
    #: 放在带默认值的字段之前，因为 dataclass 不允许必填字段跟在默认字段后面。
    source_types: tuple[str, ...]
    #: steward 的 per-viewer 身份；空间级 kind 为 None（此时 private 不可读）。
    viewer_account_id: int | None = None

    @property
    def private_reader_account_id(self) -> int | None:
        """private 分支的读者。

        assistant 读自己的私有记忆；steward 只在**有 viewer** 时读该 viewer 的，
        空间级 kind 恒为 `None`（恒不可读）。这个区别是承重的：管家的空间级 kind
        会回落到 space admin 作为身份，把 private 绑到「kind 能不能读」上就会读到
        管理员本人的私事。
        """
        if self.agent_kind == "assistant":
            return self.account.id
        return self.viewer_account_id


@dataclass(frozen=True)
class RetrievalOutcome:
    """检索结果 + **为什么是这个结果**。

    `denied` 是「被授权过滤掉」的候选数。它与 `hits == ()` 一起才回答得了
    「是没有相关内容，还是被授权过滤了」——RAG 之前无法区分这两者，而它们在
    产品上是完全不同的事（前者是「没记过」，后者是「不给你看」）。
    """

    hits: tuple[memory_rag.RAGHit, ...]
    denied: int
    scanned: int
    scope_allowlist: tuple[str, ...]
    source_types: tuple[str, ...]

    @property
    def filtered_out(self) -> bool:
        """无命中**且**确实有候选被授权挡掉。"""
        return not self.hits and self.denied > 0


def resolve_authorization(db: Session, identity: RetrievalIdentity) -> tuple[str, ...]:
    """从身份派生允许读的 scope 集合。未知 kind fail closed。

    - assistant：三个 memory scope（`private`/`household`/`lineage`）。`public` 不走
      允许集，它由 `_ELIGIBILITY_SQL` 的 `:is_assistant` 分支控制。
    - steward：`steward_memory.readable_scopes`（env ∩ 平台列 ∩ 空间列，且无 viewer
      时去掉 private）。空集是**合法**结果，表示该空间没开放任何记忆——调用方必须
      把它当作「不可读」，而不是「读全部」。
    """
    if identity.agent_kind == "assistant":
        return tuple(MEMORY_SCOPES)
    if identity.agent_kind == "steward":
        from app.services import steward_memory

        return steward_memory.readable_scopes(
            db, space_id=identity.space_id, viewer_account_id=identity.viewer_account_id
        )
    raise_api_error(
        422,
        MEMORY_SCOPE_FORBIDDEN,
        "未知的检索身份",
        {"agent_kind": identity.agent_kind},
    )


def retrieve(
    db: Session,
    identity: RetrievalIdentity,
    query: str,
    *,
    limit: int = 20,
    provider_kind: str | None = None,
    raise_on_restricted: bool = False,
    for_model: bool = True,
    recent_messages: Sequence[str] = (),
    query_plan: QueryPlan | None = None,
    rank_version: str = memory_rag.RANK_VERSION_DEFAULT,
) -> RetrievalOutcome:
    """统一授权检索入口。所有消费者都从这里读知识。

    `trace` 由本函数自己持有：调用方拿 `RetrievalOutcome`，不需要（也不能）用
    传进来的可变字典旁路拿到内部计数。
    """
    scope_allowlist = resolve_authorization(db, identity)
    # 类型标注不约束运行时（未类型化的调用点、`**kwargs` 组装、反射都能绕过），而这个
    # 默认值的失效后果是「静默读全部」。宁可多一层 422。
    if identity.source_types is None:
        raise_api_error(
            422,
            MEMORY_SCOPE_FORBIDDEN,
            "检索身份必须显式声明来源类别",
            {"source_types": None},
        )
    if not scope_allowlist:
        # 可读集为空 = 这个身份此刻读不到任何语料。**不要**退化成「读全部」，
        # 也不要把空集当作「无内容」——它是授权结论。
        return RetrievalOutcome(
            hits=(),
            denied=0,
            scanned=0,
            scope_allowlist=(),
            source_types=identity.source_types,
        )
    trace: dict[str, Any] = {}
    hits = memory_rag.search_rag(
        db,
        actor=identity.actor,
        account=identity.account,
        space_id=identity.space_id,
        query=query,
        agent_kind=identity.agent_kind,
        limit=limit,
        provider_kind=provider_kind,
        raise_on_restricted=raise_on_restricted,
        for_model=for_model,
        recent_messages=recent_messages,
        query_plan=query_plan,
        trace=trace,
        rank_version=rank_version,
        scope_allowlist=scope_allowlist,
        private_reader_account_id=identity.private_reader_account_id,
        source_types=identity.source_types,
    )
    return RetrievalOutcome(
        hits=tuple(hits),
        denied=int(trace.get("denied") or 0),
        scanned=int(trace.get("scanned") or 0),
        scope_allowlist=scope_allowlist,
        source_types=identity.source_types,
    )


def validate_source_types(source_types: Sequence[str] | None) -> None:
    """来源类别必须是已声明枚举的子集（与 `search_rag` 同口径，提前报错）。"""
    if source_types is None:
        return
    unknown = sorted(set(source_types) - set(RAG_SOURCE_TYPES))
    if unknown:
        raise_api_error(
            422,
            MEMORY_SCOPE_FORBIDDEN,
            "未知的来源类别",
            {"source_types": unknown},
        )
