"""称谓/结构类显示推导（D3：不存反向行，动态反译）。

- viewer == from_user：dir_class 原样、label 原文
- viewer == to_user：结构类反译 elder<->younger、peer/spouse 对称；
  label 仍为创建者视角原文，label_from_creator=True 标注来源
- 第三者：按边原样（creator 视角），label_from_creator=True
"""

from __future__ import annotations

from app.models.relation import Relation

_INVERSE = {"elder": "younger", "younger": "elder", "peer": "peer", "spouse": "spouse"}


def display_relation(edge: Relation, viewer_id: int) -> tuple[str, str | None, bool]:
    """返回 (viewer 视角 dir_class, label, label_from_creator)。"""
    if viewer_id == edge.from_user:
        return edge.dir_class, edge.label, False
    return _INVERSE[edge.dir_class], edge.label, True
