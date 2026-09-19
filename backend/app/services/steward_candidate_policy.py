"""模型关系候选的确定性负向安全判据（09-19 steward-recommendation-correctness）。

职责边界：只回答「这个候选是否与当前已确认事实冲突」。它不生成正向证据
（共同父母证书见 ``steward_candidate_evidence``）、不改状态机、不算称谓。

首版只服务 ``direct_sibling``：模型把父→子、祖→孙误判为同辈时，该候选不得
生成公开建议、通知或推测边。判定范围保守且可解释：

- 直接 confirmed ``biological/adoptive/step_parent`` 对（任一方向）→ 冲突；
- 仅沿 confirmed ``biological_parent`` 的可达祖先/后代（≥2 步）→ 冲突；
  收养/继亲/监护混合长路径不归约为血亲，不在此规则内；
- 只有 guardian / spouse / partner 等不同类型关系 → **不**冲突（不引入
  「任意不同关系互斥」假设，也不限制用户手工维护多重亲属身份）；
- 缺少正向支撑且无冲突 → 仍是既有 unsupported 公开流程，不由本模块隐藏。

事实范围复用 Steward 既有授权口径（当前空间 ∪ 全局、双端点可见），不扩大
读取权；本模块只返回原因码/布尔值，绝不把中间人或他空间信息交给呈现层。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# ---- 冲突原因码（机器可读；只进内部审计/日志，不面向用户文案）----
CONFLICT_PARENT_CHILD = "confirmed_parent_child"
CONFLICT_BIOLOGICAL_ANCESTRY = "confirmed_biological_ancestry"
CONFLICT_ANCESTRY_BUDGET_EXCEEDED = "confirmed_biological_ancestry_budget_exceeded"

# 直接父子判定覆盖的 parent 类事实（保守：收养/继亲同样不应被模型改写成同辈）
_DIRECT_PARENT_FACT_TYPES = ("biological_parent", "adoptive_parent", "step_parent")
# 祖先路径只沿血亲边，不把继养/监护路径折叠为血亲
_BIOLOGICAL_PARENT_FACT_TYPE = "biological_parent"

# 祖先可达性节点预算：超预算按「未能证明无冲突」处理（fail-closed），
# 不放开候选也不伪造关系。真实空间规模远低于此值。
MAX_ANCESTRY_NODES = 4096


@dataclass(frozen=True)
class CandidateConflictIndex:
    """一次快照内可复用的冲突判据（避免按候选逐条重查整图）。"""

    parent_child_pairs: frozenset[tuple[int, int]]
    biological_parents: Mapping[int, frozenset[int]]

    @classmethod
    def empty(cls) -> CandidateConflictIndex:
        return cls(parent_child_pairs=frozenset(), biological_parents={})

    def sibling_conflict_reason(self, subject_user_id: int, object_user_id: int) -> str | None:
        """``direct_sibling`` 候选的冲突原因；None 表示无冲突（不表示已获支撑）。"""
        if subject_user_id == object_user_id:
            return None
        if (subject_user_id, object_user_id) in self.parent_child_pairs or (
            object_user_id,
            subject_user_id,
        ) in self.parent_child_pairs:
            return CONFLICT_PARENT_CHILD
        budget_exceeded = False
        for start, target in (
            (subject_user_id, object_user_id),
            (object_user_id, subject_user_id),
        ):
            found, over = self._reaches(start, target)
            if found:
                return CONFLICT_BIOLOGICAL_ANCESTRY
            budget_exceeded = budget_exceeded or over
        if budget_exceeded:
            return CONFLICT_ANCESTRY_BUDGET_EXCEEDED
        return None

    def conflicts(
        self, *, relation_kind: str, subject_user_id: int, object_user_id: int
    ) -> str | None:
        """按候选 kind 分派；当前只有同辈候选需要负向判据。"""
        if relation_kind != "direct_sibling":
            return None
        return self.sibling_conflict_reason(subject_user_id, object_user_id)

    def _reaches(self, start: int, target: int) -> tuple[bool, bool]:
        """``target`` 是否为 ``start`` 的血亲祖先（迭代、去环、有界）。"""
        seen = {start}
        frontier: Sequence[int] = (start,)
        while frontier:
            nxt: list[int] = []
            for node in frontier:
                for parent in self.biological_parents.get(node, ()):
                    if parent == target:
                        return True, False
                    if parent in seen:
                        continue
                    seen.add(parent)
                    if len(seen) > MAX_ANCESTRY_NODES:
                        return False, True
                    nxt.append(parent)
            frontier = nxt
        return False, False


def build_conflict_index_from_facts(facts: Iterable[Any]) -> CandidateConflictIndex:
    """从已授权的 confirmed 事实行构造判据（纯函数，无 IO）。

    调用方传入的事实集合必须已按 Steward 口径过滤（当前空间 ∪ 全局、双端点
    在可见集合内），本模块不再自行放宽范围。
    """
    pairs: set[tuple[int, int]] = set()
    parents: dict[int, set[int]] = {}
    for fact in facts:
        fact_type = getattr(fact, "fact_type", None)
        subject = getattr(fact, "subject_user_id", None)
        obj = getattr(fact, "object_user_id", None)
        if isinstance(subject, bool) or isinstance(obj, bool):
            continue
        if not isinstance(subject, int) or not isinstance(obj, int):
            continue
        if fact_type in _DIRECT_PARENT_FACT_TYPES:
            pairs.add((subject, obj))
        if fact_type == _BIOLOGICAL_PARENT_FACT_TYPE:
            parents.setdefault(obj, set()).add(subject)
    return CandidateConflictIndex(
        parent_child_pairs=frozenset(pairs),
        biological_parents={child: frozenset(rows) for child, rows in parents.items()},
    )


def build_conflict_index(session: Any, *, space_id: int) -> CandidateConflictIndex:
    """按空间读取授权事实并构造判据（读取/命令路径用）。

    局部导入 steward 以避免模块级循环依赖（沿用 steward_delivery 的既有做法）。
    """
    from app.models.space import FamilySpace
    from app.services import steward

    space = session.get(FamilySpace, space_id)
    if space is None:
        return CandidateConflictIndex.empty()
    visible = steward._space_visible_user_ids(session, space)
    return build_conflict_index_from_facts(
        steward._applicable_confirmed_facts(session, space, visible)
    )


def sibling_conflict_reason(
    session: Any, *, space_id: int, subject_user_id: int, object_user_id: int
) -> str | None:
    """单次查询入口（详情/提交/转正等单行路径）。"""
    return build_conflict_index(session, space_id=space_id).sibling_conflict_reason(
        subject_user_id, object_user_id
    )


__all__ = [
    "CONFLICT_ANCESTRY_BUDGET_EXCEEDED",
    "CONFLICT_BIOLOGICAL_ANCESTRY",
    "CONFLICT_PARENT_CHILD",
    "MAX_ANCESTRY_NODES",
    "CandidateConflictIndex",
    "build_conflict_index",
    "build_conflict_index_from_facts",
    "sibling_conflict_reason",
]
