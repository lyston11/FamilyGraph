"""DerivedFact 缓存与失效（V2.3 Block E2，KI-2 / AC-KI7 / AC-KI8）。

缓存正确性合同：
- evidence_hash = sha256(snapshot_hash + algorithm_version)；snapshot_hash 是
  参与计算 confirmed facts 的 (id, revision, type) 有序集指纹。读取时重算当前
  指纹并与缓存行比较，不一致即重算+upsert——过期缓存绝不返回（AC-KI8）。
- KINSHIP_ALGO_VERSION 升版使旧缓存整体自然失效（evidence_hash 不再匹配）。
- 失效入口 invalidate_for_event 消费 E1 的 source_fact.* 领域事件 payload
  （fact 双方 user ids + space），按最小充分规则删除 subject/object 作为任一端
  的缓存行：空间事实只影响该空间行，全局事实影响所有空间。
- 行是可重建投影；删除/重建不影响 SourceFact 真源。由调用方事务统一提交。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.derived_fact import DerivedFact
from app.models.user import User
from app.models.v2_foundation import DomainEvent
from app.services.relationship_resolver import (
    PathStep,
    RelationshipResolution,
    describe_path,
    path_class_for_path,
    resolve_relationship,
    steps_to_json,
)
from app.utils.timeutil import utcnow

# 算法版本：改动解析算法（编码/排序/语义）必须升版本，旧缓存经 hash 失效
KINSHIP_ALGO_VERSION = "v1"

_SOURCE_FACT_EVENT_PREFIX = "source_fact."


def evidence_hash_for(snapshot_hash: str) -> str:
    """sha256(snapshot_hash + algorithm_version)——缓存新鲜度判据。"""
    return hashlib.sha256(f"{snapshot_hash}{KINSHIP_ALGO_VERSION}".encode()).hexdigest()


@dataclass(frozen=True)
class DerivedFactResult:
    """get_or_compute 结果：缓存值 + 命中标记 + 完整结构解析。"""

    cache_hit: bool
    found: bool
    viewer_user_id: int
    target_user_id: int
    space_id: int
    concept_code: str | None
    path_class: str
    main_path_json: list[dict[str, Any]]
    alt_paths_json: list[list[dict[str, Any]]]
    alt_descriptions: tuple[str, ...]
    explanation_structural: str | None
    evidence_fact_ids: list[int]
    evidence_hash: str | None
    algorithm_version: str
    term_version: str | None
    resolution: RelationshipResolution


def _fact_ids(resolution: RelationshipResolution) -> list[int]:
    return [step.fact_id for step in resolution.main_path]


def _path_class_from_json(
    steps_json: list[dict[str, Any]], *, viewer_user_id: int, target_user_id: int
) -> str:
    """从缓存行 step JSON 重建 path_class（复用 resolver 同一判定函数，单一真相）。"""
    return path_class_for_path(
        viewer_user_id=viewer_user_id,
        target_user_id=target_user_id,
        path=steps_from_json(steps_json),
    )


_EMPTY_RESOLUTION = RelationshipResolution(
    viewer_user_id=0,
    target_user_id=0,
    space_id=0,
    found=False,
    path_class="none",
    concept_code=None,
)


def _result_from_row(row: DerivedFact) -> DerivedFactResult:
    """从缓存行构造结果（cache_hit=True 路径；不回填结构解析以省查询）。"""
    return DerivedFactResult(
        cache_hit=True,
        found=True,
        viewer_user_id=row.viewer_user_id,
        target_user_id=row.target_user_id,
        space_id=row.space_id,
        concept_code=row.concept_code,
        path_class=_path_class_from_json(
            list(row.main_path_json),
            viewer_user_id=row.viewer_user_id,
            target_user_id=row.target_user_id,
        ),
        main_path_json=list(row.main_path_json),
        alt_paths_json=list(row.alt_paths_json),
        alt_descriptions=(),
        explanation_structural=None,
        evidence_fact_ids=[int(fid) for fid in row.evidence_fact_ids_json],
        evidence_hash=row.evidence_hash,
        algorithm_version=row.algorithm_version,
        term_version=row.term_version,
        resolution=_EMPTY_RESOLUTION,
    )


def get_or_compute(
    session: Session,
    *,
    viewer_user_id: int,
    target_user_id: int,
    space_id: int,
    force_rebuild: bool = False,
) -> DerivedFactResult:
    """读缓存 → 比较 evidence_hash/algorithm_version → 命中返回或重算 upsert。

    无路径（found=false）不落缓存行并清除既有行（不泄露存在性）；
    force_rebuild 供 rebuild_space/运维强制全量重算。
    """
    resolution = resolve_relationship(
        session, viewer_user_id=viewer_user_id, target_user_id=target_user_id, space_id=space_id
    )
    row = session.scalar(
        select(DerivedFact).where(
            DerivedFact.viewer_user_id == viewer_user_id,
            DerivedFact.target_user_id == target_user_id,
            DerivedFact.space_id == space_id,
        )
    )

    if not resolution.found:
        if row is not None:
            session.delete(row)
            session.flush()
        return DerivedFactResult(
            cache_hit=False,
            found=False,
            viewer_user_id=viewer_user_id,
            target_user_id=target_user_id,
            space_id=space_id,
            concept_code=None,
            path_class=resolution.path_class,
            main_path_json=[],
            alt_paths_json=[],
            alt_descriptions=(),
            explanation_structural=None,
            evidence_fact_ids=[],
            evidence_hash=None,
            algorithm_version=KINSHIP_ALGO_VERSION,
            term_version=None,
            resolution=resolution,
        )

    current_evidence_hash = evidence_hash_for(resolution.snapshot_hash)
    if (
        not force_rebuild
        and row is not None
        and row.evidence_hash == current_evidence_hash
        and row.algorithm_version == KINSHIP_ALGO_VERSION
    ):
        return _result_from_row(row)

    main_json = steps_to_json(resolution.main_path)
    alts_json = [steps_to_json(path) for path in resolution.alt_paths]
    now = utcnow()
    if row is None:
        row = DerivedFact(
            viewer_user_id=viewer_user_id,
            target_user_id=target_user_id,
            space_id=space_id,
            concept_code=resolution.concept_code or "",
            main_path_json=main_json,
            alt_paths_json=alts_json,
            evidence_fact_ids_json=_fact_ids(resolution),
            evidence_hash=current_evidence_hash,
            algorithm_version=KINSHIP_ALGO_VERSION,
            term_version=None,  # E3 TermRegistry 接入后填充
            computed_at=now,
        )
        session.add(row)
    else:
        row.concept_code = resolution.concept_code or ""
        row.main_path_json = main_json
        row.alt_paths_json = alts_json
        row.evidence_fact_ids_json = _fact_ids(resolution)
        row.evidence_hash = current_evidence_hash
        row.algorithm_version = KINSHIP_ALGO_VERSION
        row.computed_at = now
    session.flush()

    return DerivedFactResult(
        cache_hit=False,
        found=True,
        viewer_user_id=viewer_user_id,
        target_user_id=target_user_id,
        space_id=space_id,
        concept_code=resolution.concept_code,
        path_class=resolution.path_class,
        main_path_json=main_json,
        alt_paths_json=alts_json,
        alt_descriptions=resolution.alt_descriptions,
        explanation_structural=resolution.explanation_structural,
        evidence_fact_ids=_fact_ids(resolution),
        evidence_hash=current_evidence_hash,
        algorithm_version=KINSHIP_ALGO_VERSION,
        term_version=row.term_version,
        resolution=resolution,
    )


def steps_from_json(steps_json: list[dict[str, Any]]) -> tuple[PathStep, ...]:
    """缓存行 step JSON → PathStep 序列（单一真相：结构语义与 resolver 一致）。

    公开给 E3 TermRegistry 合成层复用（替代路径 concept 编码）。
    """
    return tuple(
        PathStep(
            from_id=int(step["from"]),
            to_id=int(step["to"]),
            edge_type=str(step["edge_type"]),
            subtype=None if step.get("subtype") is None else str(step["subtype"]),
            direction=str(step["direction"]),
            fact_id=int(step["fact_id"]),
        )
        for step in steps_json
    )


def describe_result(session: Session, result: DerivedFactResult) -> tuple[str, list[str]]:
    """（主描述，替代描述列表）：缓存命中也可用的确定性中文描述。

    姓名不进入描述；性别等展示属性实时读库，性别修正后描述随之更新，
    而缓存结构部分仍由 evidence_hash 守护。
    """
    if not result.found:
        return "", []
    if result.viewer_user_id == result.target_user_id:
        return "这是你自己。", []

    def build(steps_json: list[dict[str, Any]]) -> str:
        steps = steps_from_json(steps_json)
        genders: dict[int, str] = {}
        for uid in (steps[0].from_id, *(step.to_id for step in steps)):
            user = session.get(User, uid)
            if user is not None:
                genders[uid] = user.gender
        return describe_path(steps, genders)

    return build(result.main_path_json), [build(path) for path in result.alt_paths_json]


def invalidate_for_event(session: Session, event: DomainEvent) -> int:
    """消费 source_fact.* 事件：删除 subject/object 任一端参与的缓存行。

    空间事实只影响该空间行；全局事实（space_id NULL）影响所有空间。
    返回删除行数。未知事件类型忽略（幂等安全）。
    """
    if not event.type.startswith(_SOURCE_FACT_EVENT_PREFIX):
        return 0
    payload = event.payload or {}
    subject_id = payload.get("subject_user_id")
    object_id = payload.get("object_user_id")
    if subject_id is None or object_id is None:
        return 0
    stmt = delete(DerivedFact).where(
        (DerivedFact.viewer_user_id.in_((subject_id, object_id)))
        | (DerivedFact.target_user_id.in_((subject_id, object_id)))
    )
    if event.space_id is not None:
        stmt = stmt.where(DerivedFact.space_id == event.space_id)
    result = session.execute(stmt)
    session.flush()
    return int(result.rowcount or 0)


def rebuild_space(session: Session, space_id: int) -> dict[str, int]:
    """全量重算入口（测试与运维）：逐行强制重建，无路径的行删除。

    返回 {kept, dropped}：kept 为仍有路径的行数，dropped 为因资料变化而删除的行数。
    """
    row_keys = session.execute(
        select(DerivedFact.viewer_user_id, DerivedFact.target_user_id).where(
            DerivedFact.space_id == space_id
        )
    ).all()
    kept = dropped = 0
    for viewer_user_id, target_user_id in row_keys:
        result = get_or_compute(
            session,
            viewer_user_id=viewer_user_id,
            target_user_id=target_user_id,
            space_id=space_id,
            force_rebuild=True,
        )
        if result.found:
            kept += 1
        else:
            dropped += 1
    return {"kept": kept, "dropped": dropped}
