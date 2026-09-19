#!/usr/bin/env python3
"""管家错误推荐的只读 dry-run 核查（09-19-steward-recommendation-correctness S4）。

只读、不写库、不调用模型、不发通知。对给定库输出：

- 候选 / 建议 / 推测边 / 卡片 四类对象的总量与「实际命中」量；
- 每条命中的 object_id、space_id、状态与命中原因；
- 受保护历史摘要（已提交/已确认异常单列，不宣称已自动处理）。

用法（必须在隔离库上运行，或对生产库做只读盘点时显式指定）：

    DATA_DIR=<隔离目录> PYTHONPATH=backend .venv/bin/python \
        backend/scripts/steward_recommendation_dryrun.py [--json]

红线：本脚本只执行 SELECT。它不提供、也不替代任何批量 UPDATE/DELETE 清理路径；
存量收敛必须经既有 effective_state 读模型与 review/supersede FSM 完成。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models.relationship_facts import SourceFact
from app.models.space import FamilySpace
from app.models.steward import ActionCard, StewardLlmCandidate
from app.models.steward_inferred import StewardInferredEdge
from app.models.steward_suggestion import StewardSuggestion
from app.services import steward
from app.services import steward_candidate_policy as policy

# 与 steward_inferred.INFERRED_ACTIVE_STATE 一致（避免导入私有常量）
_EDGE_ACTIVE = "proposed"
_ACTIVE_SUGGESTION_STATES = ("proposed", "submitted")


def _conflict_index_for(session: Any, space_id: int) -> policy.CandidateConflictIndex:
    return policy.build_conflict_index(session, space_id=space_id)


def _candidate_rows(session: Any) -> list[dict[str, Any]]:
    """候选按端点冲突分类。候选本身不因冲突被删除（保留审计与归因历史）。"""
    hits: list[dict[str, Any]] = []
    total = 0
    for candidate in session.scalars(select(StewardLlmCandidate).order_by(StewardLlmCandidate.id)):
        total += 1
        payload = candidate.payload_json if isinstance(candidate.payload_json, dict) else {}
        kind = payload.get("kind")
        subject = payload.get("subject_user_id")
        obj = payload.get("object_user_id")
        if not isinstance(kind, str) or not isinstance(subject, int) or not isinstance(obj, int):
            continue
        index = _conflict_index_for(session, candidate.space_id)
        reason = index.conflicts(relation_kind=kind, subject_user_id=subject, object_user_id=obj)
        if reason is None:
            continue
        hits.append(
            {
                "object": "candidate",
                "id": candidate.id,
                "space_id": candidate.space_id,
                "status": candidate.status,
                "attribution": candidate.attribution_status,
                "reason": reason,
                "endpoints": [subject, obj],
            }
        )
    return [{"total": total, "hits": len(hits)}, *hits]


def _suggestion_rows(session: Any) -> list[dict[str, Any]]:
    """建议：区分「活跃可行动」「已提交/已确认（人工核查）」「已终态」。"""
    hits: list[dict[str, Any]] = []
    total = 0
    for suggestion in session.scalars(select(StewardSuggestion).order_by(StewardSuggestion.id)):
        total += 1
        if suggestion.kind != "relation_proposal":
            continue
        obj = suggestion.object_user_id
        if obj is None:
            continue
        index = _conflict_index_for(session, suggestion.space_id)
        reason = index.conflicts(
            relation_kind=str(suggestion.value_json.get("fact_type") or ""),
            subject_user_id=int(suggestion.subject_user_id),
            object_user_id=int(obj),
        )
        if reason is None:
            continue
        # 已经产生正式提案/事实关联的建议一律不动（即使状态仍是 submitted），
        # 连同所有非活跃终态一起列为人工核查。
        protected = (
            suggestion.linked_fact_id is not None
            or suggestion.status not in _ACTIVE_SUGGESTION_STATES
        )
        hits.append(
            {
                "object": "suggestion",
                "id": suggestion.id,
                "space_id": suggestion.space_id,
                "status": suggestion.status,
                "reason": reason,
                "linked_fact_id": suggestion.linked_fact_id,
                # 已提交/已关联事实/已确认/已驳回等一律不动，只列为人工核查
                "needs_manual_review": protected,
            }
        )
    return [{"total": total, "hits": len(hits)}, *hits]


def _edge_rows(session: Any) -> list[dict[str, Any]]:
    """推测边：活跃且冲突的应由 review/supersede FSM 退役（本脚本只报告）。"""
    hits: list[dict[str, Any]] = []
    total = 0
    for edge in session.scalars(select(StewardInferredEdge).order_by(StewardInferredEdge.id)):
        total += 1
        if edge.status != _EDGE_ACTIVE:
            continue
        index = _conflict_index_for(session, edge.space_id)
        reason = index.conflicts(
            relation_kind=edge.relation_kind,
            subject_user_id=int(edge.subject_user_id),
            object_user_id=int(edge.object_user_id),
        )
        if reason is None:
            continue
        hits.append(
            {
                "object": "inferred_edge",
                "id": edge.id,
                "space_id": edge.space_id,
                "status": edge.status,
                "reason": reason,
                "relation_kind": edge.relation_kind,
            }
        )
    return [{"total": total, "hits": len(hits)}, *hits]


def _card_rows(session: Any) -> list[dict[str, Any]]:
    """卡片：只对 household_link 判定「双方已共享家庭」，不按空间 kind 或旧数字筛选。"""
    hits: list[dict[str, Any]] = []
    total = 0
    for card in session.scalars(select(ActionCard).order_by(ActionCard.id)):
        total += 1
        if card.state not in ("pending", "viewed", "accepted"):
            continue
        if not steward.card_household_conflict(session, card):
            continue
        hits.append(
            {
                "object": "action_card",
                "id": card.id,
                "space_id": card.space_id,
                "status": card.state,
                "reason": "household_already_shared",
                "kind": card.kind,
            }
        )
    return [{"total": total, "hits": len(hits)}, *hits]


def _protected_summary(session: Any) -> dict[str, int]:
    """受保护历史：只读计数，任何自动流程都不得改写。"""
    return {
        "confirmed_source_facts": int(
            len(list(session.scalars(select(SourceFact).where(SourceFact.state == "confirmed"))))
        ),
        "spaces": int(len(list(session.scalars(select(FamilySpace.id))))),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（默认人类可读）")
    args = parser.parse_args(argv)

    from app import config

    session = SessionLocal()
    try:
        report = {
            "data_dir": str(config.DATA_DIR),
            "candidates": _candidate_rows(session),
            "suggestions": _suggestion_rows(session),
            "inferred_edges": _edge_rows(session),
            "action_cards": _card_rows(session),
            "protected": _protected_summary(session),
            "note": (
                "只读报告。候选/建议/边/卡的总量与命中量必须分开陈述；"
                "needs_manual_review=true 的行只供人工核查，不自动撤回。"
            ),
        }
    finally:
        session.close()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"DATA_DIR: {report['data_dir']}")
    for key in ("candidates", "suggestions", "inferred_edges", "action_cards"):
        head, *rows = report[key]
        print(f"\n== {key}: total={head['total']} hits={head['hits']}")
        for row in rows:
            extra = ""
            if row.get("needs_manual_review"):
                extra = " [人工核查，不自动处理]"
            print(
                f"   id={row['id']:<6} space={row['space_id']:<4} "
                f"status={row['status']:<12} reason={row['reason']}{extra}"
            )
    print(f"\n== protected: {report['protected']}")
    print(f"\n{report['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
