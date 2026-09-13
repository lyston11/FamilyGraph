"""Build and read the server-authoritative PersonalFamilyView projection.

09-11 projection-consistency 合同：

- 计算口径：图快照 hash + 称谓词典版本 + 统一 policy_version（config.POLICY_VERSION，
  不再借用 purpose="graph"）+ computation_version 共同决定 input_hash；
- 读取口径：GET 只做授权复核与新鲜度判定，绝不隐式物化/重算；stale/failed/
  版本漂移一律返回安全空内容并触发显式短事务重算登记；
- 路径证据：服务前逐条重验主路径与替代路径的每一步事实（存在、confirmed、
  空间适用、端点一致、方向一致）与全部中间节点当前可见性——只过滤两端不够；
- 初始化：注册/合法成员资格获得事件后台建立 queued 行（见 domain_events）；
  无空间不建行，仅 provisional 引用的人物不伪造 Account。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import config
from app.errors import PERSONAL_FAMILY_VIEW_NOT_FOUND, raise_api_error
from app.models.account import Account
from app.models.personal_family_view import (
    PersonalFamilyView,
    PersonalFamilyViewEdge,
    PersonalFamilyViewNode,
)
from app.models.relationship_facts import SourceFact
from app.models.space import FamilySpace, SpaceMember
from app.models.steward_inferred import StewardInferredEdge
from app.models.term_registry import TermEntry
from app.models.user import User
from app.services import kinship_presentation, steward_inferred, visibility
from app.services.relationship_graph import (
    ExtraEdge,
    birth_from_user,
    load_birth_years,
    load_graph,
    scoped_confirmed_facts,
    topology_edges_from_facts,
)
from app.services.relationship_resolver import (
    PathStep,
    concept_code_for_path,
    resolve_relationship,
    steps_to_json,
)
from app.services.source_facts import FACT_CONFIRMED
from app.services.terms import VariantContext, resolve_term_or_structural, space_locale
from app.utils.timeutil import utcnow

COMPUTATION_VERSION = "pfv-v3"
POLICY_VERSION = config.POLICY_VERSION

logger = logging.getLogger(__name__)

# 读取安全态（空内容）返回的状态集合
_SAFE_EMPTY_STATUSES = ("never_computed", "queued", "running", "stale", "failed")


def _active_space_member(session: Session, *, space_id: int, user_id: int) -> bool:
    return (
        session.scalar(
            select(SpaceMember.id).where(
                SpaceMember.space_id == space_id,
                SpaceMember.user_id == user_id,
                SpaceMember.status == "active",
            )
        )
        is not None
    )


def _view_for_actor(session: Session, *, account: Account, space_id: int) -> PersonalFamilyView:
    if session.get(FamilySpace, space_id) is None or not _active_space_member(
        session, space_id=space_id, user_id=account.user_id
    ):
        raise_api_error(404, PERSONAL_FAMILY_VIEW_NOT_FOUND, "个人家族视图不存在")
    view = session.scalar(
        select(PersonalFamilyView).where(
            PersonalFamilyView.viewer_account_id == account.id,
            PersonalFamilyView.root_user_id == account.user_id,
            PersonalFamilyView.space_id == space_id,
        )
    )
    if view is None:
        now = utcnow()
        view = PersonalFamilyView(
            viewer_account_id=account.id,
            root_user_id=account.user_id,
            space_id=space_id,
            status="never_computed",
            view_version=0,
            computation_version=COMPUTATION_VERSION,
            policy_version=POLICY_VERSION,
            created_at=now,
            updated_at=now,
        )
        session.add(view)
        session.flush()
    return view


def _term_registry_hash(session: Session, *, account_id: int, space_id: int) -> str:
    """四级词典（personal/space/locale/system 中对本账号生效的 active 行）版本指纹。"""
    locale = space_locale(session, space_id)
    rows = session.execute(
        select(TermEntry.id, TermEntry.revision)
        .where(
            TermEntry.status == "active",
            or_(
                (TermEntry.level == "personal") & (TermEntry.owner_account_id == account_id),
                (TermEntry.level == "space") & (TermEntry.space_id == space_id),
                (TermEntry.level == "locale") & (TermEntry.locale == locale),
                (TermEntry.level == "system"),
            ),
        )
        .order_by(TermEntry.id)
    ).all()
    digest = hashlib.sha256()
    for entry_id, revision in rows:
        digest.update(f"{entry_id}:{revision}\n".encode())
    return digest.hexdigest()


def _input_hash(graph_hash: str, *, policy_version: str, term_hash: str) -> str:
    return hashlib.sha256(
        f"{graph_hash}:{term_hash}:{policy_version}:{COMPUTATION_VERSION}".encode()
    ).hexdigest()


def _current_input_hash(
    session: Session, *, account: Account, space_id: int, graph_hash: str
) -> str:
    return _input_hash(
        graph_hash,
        policy_version=POLICY_VERSION,
        term_hash=_term_registry_hash(session, account_id=account.id, space_id=space_id),
    )


def _node_display(
    session: Session,
    actor: User,
    target: User,
    space_id: int,
    *,
    bridge_authorized: bool = False,
) -> tuple[dict[str, Any], str, visibility.VisibilityDecision]:
    decision = visibility.evaluate(
        session, actor, target, space_context=space_id, purpose=visibility.PURPOSE_GRAPH
    )
    if bridge_authorized and not decision.visible:
        decision = visibility.VisibilityDecision(
            visibility.LEVEL_LINEAGE_SUMMARY,
            {
                field: visibility.FIELD_CLEAR
                if field in visibility.BASELINE_FIELDS
                else visibility.FIELD_MASKED
                for field in visibility.PROFILE_FIELDS
            },
            visibility.PURPOSE_GRAPH,
        )
    if not decision.visible:
        raise ValueError("invisible node cannot enter PersonalFamilyView")
    return (
        jsonable_encoder(visibility.payload_from_decision(decision, target)),
        decision.level,
        decision,
    )


def rebuild_view(session: Session, *, account: Account, space_id: int) -> PersonalFamilyView:
    """Rebuild one view from current confirmed graph facts, atomically in caller transaction."""
    view = _view_for_actor(session, account=account, space_id=space_id)
    actor = session.get(User, account.user_id)
    if actor is None:  # pragma: no cover - account FK guarantees this
        raise_api_error(404, PERSONAL_FAMILY_VIEW_NOT_FOUND, "个人家族视图不存在")
    graph = load_graph(session, viewer_user_id=actor.id, space_id=space_id)
    # 长幼消歧出生数据：展示口径（PURPOSE_GRAPH）的可见性决定随 _node_display
    # 逐节点取得；birth 字段脱敏（含未成年人 overlay / lineage 层）→ None。
    births: dict[int, tuple[str, int] | None] = {}
    now = utcnow()
    view.status = "running"
    view.updated_at = now
    session.flush()
    session.query(PersonalFamilyViewNode).filter(PersonalFamilyViewNode.view_id == view.id).delete()
    session.query(PersonalFamilyViewEdge).filter(PersonalFamilyViewEdge.view_id == view.id).delete()
    confirmed_reached: set[int] = set()
    for target_id in sorted(graph.node_genders):
        target = session.get(User, target_id)
        if target is None:
            continue
        if target.id == actor.id:
            resolution = None
        else:
            resolution = resolve_relationship(
                session, viewer_user_id=actor.id, target_user_id=target.id, space_id=space_id
            )
            if not resolution.found:
                continue
            confirmed_reached.add(target.id)
        display, level, node_decision = _node_display(
            session,
            actor,
            target,
            space_id,
            bridge_authorized=target.id in graph.bridge_user_ids,
        )
        births[target.id] = (
            birth_from_user(target)
            if node_decision.fields.get("birth") == visibility.FIELD_CLEAR
            else None
        )
        session.add(
            PersonalFamilyViewNode(
                view_id=view.id,
                user_id=target.id,
                display_json=display,
                visibility_level=level,
                inclusion_reason_code="root" if target.id == actor.id else "confirmed_path",
                source_fact_ids_json=[],
                authorization_basis_json={"space_id": space_id, "visibility": level},
                policy_version=POLICY_VERSION,
                computation_version=COMPUTATION_VERSION,
            )
        )
        if resolution is None:
            continue
        main_path = steps_to_json(resolution.main_path)
        alt_paths = [steps_to_json(path) for path in resolution.alt_paths]
        source_ids = sorted(
            {
                step["fact_id"]
                for step in main_path
                if step.get("fact_id") is not None and step["fact_id"] > 0
            }
        )
        # R4：四级词典解析（personal > space > locale > system），结构描述仅兜底；
        # 不修改 SourceFact / raw_relation_inputs 原文。09-13：传入长幼消歧
        # 上下文（展示口径出生数据，脱敏节点为 None），词典泛化词升级为
        # 兄弟姐妹类具体长幼称谓。
        term_view = resolve_term_or_structural(
            session,
            account_id=account.id,
            space_id=space_id,
            concept_code=resolution.concept_code,
            structural_description=resolution.explanation_structural or "",
            variant_context=VariantContext(
                viewer_user_id=actor.id,
                path=main_path,
                births=births,
            ),
        )
        session.add(
            PersonalFamilyViewEdge(
                view_id=view.id,
                from_user_id=actor.id,
                to_user_id=target.id,
                edge_kind=main_path[-1]["edge_type"] if main_path else "self",
                source_fact_id=source_ids[0] if source_ids else None,
                path_json=main_path,
                alternative_paths_json=alt_paths,
                path_class=resolution.path_class,
                concept_code=resolution.concept_code,
                term=term_view["term"],
                inclusion_reason_code="confirmed_path",
                authorization_basis_json={
                    "space_id": space_id,
                    "visibility": level,
                    "term_source_level": term_view["source_level"],
                },
                policy_version=POLICY_VERSION,
                computation_version=COMPUTATION_VERSION,
            )
        )
    # ---- 推测层（09-13）：增广图二次解析，推测节点/边以 inferred_path 落库 ----
    # 推测边永不写事实层；此处只物化「显示投影行」，GET 按 inclusion_reason_code
    # 拆分 edges / inferred_edges，并逐行重验推测边仍处 proposed。
    _emit_inferred_projection(
        session,
        account=account,
        actor=actor,
        view=view,
        graph=graph,
        space_id=space_id,
        confirmed_reached=confirmed_reached,
        now=now,
    )

    view.status = "current"
    view.view_version += 1
    view.input_hash = _current_input_hash(
        session, account=account, space_id=space_id, graph_hash=graph.snapshot_hash
    )
    view.policy_version = POLICY_VERSION
    view.computation_version = COMPUTATION_VERSION
    view.computed_at = now
    view.invalidated_at = None
    view.failed_reason = None
    view.updated_at = now
    session.flush()
    return view


_INFERRED_SUBTYPE_BY_KIND = {
    "biological_parent": "biological",
    "adoptive_parent": "adoptive",
    "step_parent": "step",
    "guardian": "guardian",
}
_PATH_CLASS_INFERRED = "inferred"


def _inferred_hop_step(edge: Any, genders: dict[int, str]) -> tuple[PathStep, dict[str, Any]]:
    """推测单跳的规范化步（subject→object 方向）与 JSON 形状。

    parent 类：subject 是 object 的家长 → direction=down（token = D+性别）；
    对称类：direction=sym。fact_id = -(推测边 id) 标记推测步。
    """
    if edge.relation_kind in _INFERRED_SUBTYPE_BY_KIND:
        step = PathStep(
            from_id=edge.subject_user_id,
            to_id=edge.object_user_id,
            edge_type="parent",
            subtype=_INFERRED_SUBTYPE_BY_KIND[edge.relation_kind],
            direction="down",
            fact_id=-int(edge.id),
        )
    else:
        edge_type = (
            "spouse"
            if edge.relation_kind == "spouse"
            else "partner"
            if edge.relation_kind == "partner"
            else "sibling"
        )
        step = PathStep(
            from_id=edge.subject_user_id,
            to_id=edge.object_user_id,
            edge_type=edge_type,
            subtype=None,
            direction="sym",
            fact_id=-int(edge.id),
        )
    return step, step.to_json()


def _emit_inferred_projection(
    session: Session,
    *,
    account: Account,
    actor: User,
    view: PersonalFamilyView,
    graph: Any,
    space_id: int,
    confirmed_reached: set[int],
    now: Any,
) -> None:
    """推测层显示投影（rebuild_view 事务内；开关关闭或无活跃边时零操作）。

    - 逐条活跃推测边物化一行 inferred_path 边（subject/object + 单跳步 +
      确定性单跳称谓）；
    - 端点中「尚无 confirmed 路径」者以 inferred_path 节点上树，其 viewer 视角
      称谓经增广图确定性解析（含长幼消歧），存入边的 authorization_basis_json
      （viewer_term/viewer_path/new_user_id）；
    - 推测边永不写 SourceFact；确认转正走 relationship_proposals 合同。
    """
    if not steward_inferred.effective_enabled(session, space_id):
        return
    active = steward_inferred.active_edges(session, space_id)
    if not active:
        return
    known = confirmed_reached | {actor.id}
    extra = tuple(
        ExtraEdge(
            edge_id=edge.id,
            subject_user_id=edge.subject_user_id,
            object_user_id=edge.object_user_id,
            relation_kind=edge.relation_kind,
        )
        for edge in active
    )
    genders = graph.node_genders
    nodes_added: set[int] = set()
    for edge in active:
        step, step_json = _inferred_hop_step(edge, genders)
        hop_code = concept_code_for_path((step,), genders)
        hop_term_view = resolve_term_or_structural(
            session,
            account_id=account.id,
            space_id=space_id,
            concept_code=hop_code,
            structural_description="",
        )
        endpoints = {edge.subject_user_id, edge.object_user_id}
        new_users = endpoints - known
        new_user_id = next(iter(new_users)) if len(new_users) == 1 else None
        viewer_term: str | None = None
        viewer_path: list[dict[str, Any]] = []
        if new_user_id is not None:
            resolution = resolve_relationship(
                session,
                viewer_user_id=actor.id,
                target_user_id=new_user_id,
                space_id=space_id,
                extra_edges=extra,
            )
            if resolution.found:
                main_path = steps_to_json(resolution.main_path)
                inferred_steps = [
                    s for s in main_path if isinstance(s.get("fact_id"), int) and s["fact_id"] <= 0
                ]
                if len(inferred_steps) == 1 and inferred_steps[0]["fact_id"] == -int(edge.id):
                    viewer_path = main_path
                    persons = {actor.id, new_user_id}
                    for step_json in main_path:
                        persons.add(int(step_json["from"]))
                        persons.add(int(step_json["to"]))
                    births = load_birth_years(
                        session,
                        viewer_user_id=actor.id,
                        space_id=space_id,
                        user_ids=persons,
                    )
                    term_view = resolve_term_or_structural(
                        session,
                        account_id=account.id,
                        space_id=space_id,
                        concept_code=resolution.concept_code,
                        structural_description=resolution.explanation_structural or "",
                        variant_context=VariantContext(
                            viewer_user_id=actor.id,
                            path=main_path,
                            births=births,
                        ),
                    )
                    viewer_term = term_view["term"]
        # 端点中尚无 confirmed 路径者以 inferred_path 节点上树
        for user_id in sorted(endpoints - known):
            if user_id in nodes_added:
                continue
            target = session.get(User, user_id)
            if target is None:
                continue
            display, level, _decision = _node_display(session, actor, target, space_id)
            session.add(
                PersonalFamilyViewNode(
                    view_id=view.id,
                    user_id=target.id,
                    display_json=display,
                    visibility_level=level,
                    inclusion_reason_code="inferred_path",
                    source_fact_ids_json=[],
                    authorization_basis_json={"space_id": space_id, "visibility": level},
                    policy_version=POLICY_VERSION,
                    computation_version=COMPUTATION_VERSION,
                )
            )
            nodes_added.add(user_id)
        evidence_fact_ids = [
            int(f[0])
            for f in (edge.evidence_json or {}).get("facts", [])
            if isinstance(f, list | tuple) and f and isinstance(f[0], int)
        ]
        session.add(
            PersonalFamilyViewEdge(
                view_id=view.id,
                from_user_id=edge.subject_user_id,
                to_user_id=edge.object_user_id,
                edge_kind=edge.relation_kind,
                source_fact_id=None,
                path_json=[step_json],
                alternative_paths_json=[],
                path_class=_PATH_CLASS_INFERRED,
                concept_code=hop_code,
                term=hop_term_view["term"],
                inclusion_reason_code="inferred_path",
                authorization_basis_json={
                    "space_id": space_id,
                    "inferred_edge_id": int(edge.id),
                    "evidence_fact_ids": evidence_fact_ids,
                    "new_user_id": new_user_id,
                    "viewer_term": viewer_term,
                    "viewer_path": viewer_path,
                    "term_source_level": hop_term_view["source_level"],
                },
                policy_version=POLICY_VERSION,
                computation_version=COMPUTATION_VERSION,
            )
        )


def get_current_view(
    session: Session, *, account: Account, space_id: int
) -> PersonalFamilyView | None:
    """Read an existing projection without materializing or rebuilding it."""
    if session.get(FamilySpace, space_id) is None or not _active_space_member(
        session, space_id=space_id, user_id=account.user_id
    ):
        raise_api_error(404, PERSONAL_FAMILY_VIEW_NOT_FOUND, "个人家族视图不存在")
    return session.scalar(
        select(PersonalFamilyView).where(
            PersonalFamilyView.viewer_account_id == account.id,
            PersonalFamilyView.root_user_id == account.user_id,
            PersonalFamilyView.space_id == space_id,
        )
    )


def get_view(session: Session, *, account: Account, space_id: int) -> PersonalFamilyView:
    """授权复核后的投影行读取；绝不触发隐式物化/重算（R5）。

    首次访问返回 never_computed 占位行（调用方若需首读物化必须显式提交）；
    重算由后台初始化或显式短事务登记的 Steward 作业承担。
    """
    return _view_for_actor(session, account=account, space_id=space_id)


def empty_view_payload(*, space_id: int) -> dict[str, Any]:
    """无投影行时的安全空响应（GET 全程只读，不建行、不入队写）。"""
    return {
        "space_id": space_id,
        "status": "never_computed",
        "view_version": 0,
        "computed_at": None,
        "nodes": [],
        "inferred_edges": [],
        "edges": [],
        "topology_edges": [],
        "truncated": False,
        "next_cursor": None,
        "stale_reason": None,
    }


def view_is_current(
    session: Session, *, view: PersonalFamilyView, account: Account, space_id: int
) -> bool:
    """新鲜度判定：status/policy/computation 版本 + 当前图与词典指纹一致。

    旧 policy_version='graph'、缺词典版本指纹的视图一律判不新鲜，
    绝不当作有效 current 提供（也绝不返回 304）。
    """
    if view.status != "current":
        return False
    if view.policy_version != POLICY_VERSION or view.computation_version != COMPUTATION_VERSION:
        return False
    if not view.input_hash:
        return False
    actor = session.get(User, account.user_id)
    if actor is None:
        return False
    graph = load_graph(session, viewer_user_id=actor.id, space_id=space_id)
    return view.input_hash == _current_input_hash(
        session, account=account, space_id=space_id, graph_hash=graph.snapshot_hash
    )


def view_payload(session: Session, *, account: Account, space_id: int) -> dict[str, Any]:
    view = get_view(session, account=account, space_id=space_id)
    return _view_payload_for_view(session, account=account, space_id=space_id, view=view)


def current_view_payload(
    session: Session, *, account: Account, space_id: int
) -> dict[str, Any] | None:
    """Read and authorize an existing projection without creating one."""
    view = get_current_view(session, account=account, space_id=space_id)
    if view is None:
        return None
    return _view_payload_for_view(session, account=account, space_id=space_id, view=view)


def _path_evidence_valid(
    session: Session, *, path: Any, space_id: int, visible_ids: set[int]
) -> bool:
    """逐步重验一条路径：每一步事实存在、confirmed、空间适用、端点/方向一致，
    且路径上所有节点（含中间人）当前对 actor 可见。任一步失败整条无效。"""
    if not isinstance(path, list):
        return False
    for step in path:
        if not isinstance(step, dict):
            return False
        from_user, to_user = step.get("from"), step.get("to")
        fact_id = step.get("fact_id")
        if not isinstance(from_user, int) or not isinstance(to_user, int):
            return False
        if from_user not in visible_ids or to_user not in visible_ids:
            return False
        edge_type = step.get("edge_type")
        if not isinstance(fact_id, int) or fact_id <= 0:
            # 桥接步（fact_id=0）没有 SourceFact；仅承认 bridge 边型。
            if edge_type != "bridge":
                return False
            continue
        fact = session.get(SourceFact, fact_id)
        if fact is None or fact.state != FACT_CONFIRMED:
            return False
        if fact.space_id not in (space_id, None):
            return False
        subject_id, object_id = fact.subject_user_id, fact.object_user_id
        if subject_id is None or object_id is None:
            return False
        if {subject_id, object_id} != {from_user, to_user}:
            return False
        if edge_type == "parent":
            direction = step.get("direction")
            # 方向合同：subject 是 object 的家长；up 表示 to 是 from 的家长。
            if direction == "up" and subject_id != to_user:
                return False
            if direction == "down" and subject_id != from_user:
                return False
    return True


def _safe_empty_payload(
    *, space_id: int, view: PersonalFamilyView, reason: str | None
) -> dict[str, Any]:
    return {
        "space_id": space_id,
        "status": view.status if view.status in _SAFE_EMPTY_STATUSES else "stale",
        "view_version": view.view_version,
        "computed_at": view.computed_at,
        "nodes": [],
        "edges": [],
        "topology_edges": [],
        "truncated": False,
        "next_cursor": None,
        "stale_reason": reason,
    }


def _view_payload_for_view(
    session: Session, *, account: Account, space_id: int, view: PersonalFamilyView
) -> dict[str, Any]:
    # R5/R3：非 current 或新鲜度不达标 → 安全空内容（绝不把旧投影当 current 提供）。
    if not view_is_current(session, view=view, account=account, space_id=space_id):
        reason = view.failed_reason or ("version_drift" if view.status == "current" else None)
        return _safe_empty_payload(space_id=space_id, view=view, reason=reason)
    actor = session.get(User, account.user_id)
    if actor is None:
        raise_api_error(404, PERSONAL_FAMILY_VIEW_NOT_FOUND, "个人家族视图不存在")
    graph = load_graph(session, viewer_user_id=actor.id, space_id=space_id)
    nodes = session.scalars(
        select(PersonalFamilyViewNode)
        .where(PersonalFamilyViewNode.view_id == view.id)
        .order_by(PersonalFamilyViewNode.user_id)
    ).all()
    edges = session.scalars(
        select(PersonalFamilyViewEdge)
        .where(PersonalFamilyViewEdge.view_id == view.id)
        .order_by(PersonalFamilyViewEdge.to_user_id)
    ).all()
    authorized_nodes: list[dict[str, Any]] = []
    visible_ids: set[int] = set()
    for node in nodes:
        target = session.get(User, node.user_id)
        if target is None:
            continue
        decision = visibility.evaluate(
            session, actor, target, space_context=space_id, purpose=visibility.PURPOSE_GRAPH
        )
        if not decision.visible and node.user_id not in graph.bridge_user_ids:
            continue
        if node.user_id in graph.bridge_user_ids and not decision.visible:
            decision = visibility.VisibilityDecision(
                visibility.LEVEL_LINEAGE_SUMMARY,
                {
                    field: visibility.FIELD_CLEAR
                    if field in visibility.BASELINE_FIELDS
                    else visibility.FIELD_MASKED
                    for field in visibility.PROFILE_FIELDS
                },
                visibility.PURPOSE_GRAPH,
            )
        # R5：GET 只读——重授权的 display/level 仅进入响应，不写回 ORM 快照行。
        authorized_nodes.append(
            {
                "user_id": node.user_id,
                "display": jsonable_encoder(visibility.payload_from_decision(decision, target)),
                "visibility_level": decision.level,
                "inclusion_reason_code": node.inclusion_reason_code,
            }
        )
        visible_ids.add(node.user_id)
    served_edges: list[tuple[PersonalFamilyViewEdge, list[list[dict[str, Any]]]]] = []
    inferred_edges: list[dict[str, Any]] = []
    for edge in edges:
        if edge.inclusion_reason_code == "inferred_path":
            # 推测边（09-13）：逐行重验推测边仍处 proposed + 两端当前可见；
            # 已转正/驳回/失效的推测边不再渲染（确认后的信息由 confirmed 边承载）。
            raw_basis = edge.authorization_basis_json
            basis: dict[str, Any] = raw_basis if isinstance(raw_basis, dict) else {}
            edge_id = basis.get("inferred_edge_id")
            row = (
                session.get(StewardInferredEdge, int(edge_id)) if isinstance(edge_id, int) else None
            )
            if row is None or row.status != "proposed":
                continue
            if edge.from_user_id not in visible_ids or edge.to_user_id not in visible_ids:
                continue
            assert isinstance(edge_id, int)  # row 非 None 已保证
            evidence_fact_ids = basis.get("evidence_fact_ids") or []
            # A-R1：推测面板与通知同源的方向化呈现（“X 可能是你的 Y”）；
            # 结构端点仍由 subject/object 承载，证据计数只认可核验相关事实。
            edge_presentation = kinship_presentation.build_inferred_edge_presentation(
                session,
                viewer=actor,
                space_id=space_id,
                subject_user_id=edge.from_user_id,
                object_user_id=edge.to_user_id,
                term=edge.term,
                evidence_fact_count=len(evidence_fact_ids),
            )
            inferred_edges.append(
                {
                    "id": edge_id,
                    "subject_user_id": edge.from_user_id,
                    "object_user_id": edge.to_user_id,
                    "relation_kind": edge.edge_kind,
                    "term": edge.term,
                    "path": edge.path_json or [],
                    "viewer_term": basis.get("viewer_term"),
                    "viewer_path": basis.get("viewer_path") or [],
                    "new_user_id": basis.get("new_user_id"),
                    "evidence_fact_ids": evidence_fact_ids,
                    "presentation": edge_presentation,
                    "revision": int(row.revision),
                    "created_at": row.created_at,
                }
            )
            continue
        if edge.from_user_id not in visible_ids or edge.to_user_id not in visible_ids:
            continue
        # R3：主路径与替代路径分别重验；主路径失效整条边不输出，
        # 失效的替代路径单独剔除，绝不回传未验证的保存 path_json。
        if not _path_evidence_valid(
            session, path=edge.path_json, space_id=space_id, visible_ids=visible_ids
        ):
            continue
        verified_alts = [
            path
            for path in (edge.alternative_paths_json or [])
            if _path_evidence_valid(session, path=path, space_id=space_id, visible_ids=visible_ids)
        ]
        served_edges.append((edge, verified_alts))
    # 结构拓扑：confirmed 直接亲属事实（当前空间或全局、两端点均在本次授权
    # 可见集合内），与个人摘要 edges 并列输出。不从 ORM 摘要边反推结构——
    # 该表只保存本人起点的路径摘要（推测层可能另行写入其中）。
    topology_edges = topology_edges_from_facts(
        scoped_confirmed_facts(session, space_ids={space_id}, visible_ids=visible_ids)
    )
    return {
        "space_id": space_id,
        "status": view.status,
        "view_version": view.view_version,
        "computed_at": view.computed_at,
        "nodes": authorized_nodes,
        "inferred_edges": inferred_edges,
        "edges": [
            {
                "from_user_id": edge.from_user_id,
                "to_user_id": edge.to_user_id,
                "edge_kind": edge.edge_kind,
                "path": edge.path_json,
                "alternative_paths": verified_alts,
                "path_class": edge.path_class,
                "concept_code": edge.concept_code,
                "term": edge.term,
                "inclusion_reason_code": edge.inclusion_reason_code,
            }
            for edge, verified_alts in served_edges
        ],
        "topology_edges": topology_edges,
        "truncated": False,
        "next_cursor": None,
        "stale_reason": None,
    }


def etag_for(view: PersonalFamilyView, *, account: Account) -> str:
    """条件请求指纹：绑定授权 epoch（token_version）+ 视图版本/状态 + 事实/
    词典版本（input_hash）+ 计算/策略版本。任一变化都使旧 If-None-Match 失效。"""
    return json.dumps(
        [
            view.id,
            view.view_version,
            view.status,
            view.input_hash,
            view.policy_version,
            view.computation_version,
            account.token_version,
        ],
        separators=(",", ":"),
    )


def invalidate_view_scopes(session: Session, *, scopes: dict[int, set[int | None]]) -> int:
    """按 (space_id, viewer_account_id 范围) 标 stale；None 表示该空间全部视图。

    在事件所属事务内同步执行（R3：撤权事务内同步失效）。
    """
    if not scopes:
        return 0
    now = utcnow()
    count = 0
    for space_id, account_ids in scopes.items():
        concrete = {int(a) for a in account_ids if isinstance(a, int)}
        if None not in account_ids and not concrete:
            continue
        stmt = select(PersonalFamilyView).where(
            PersonalFamilyView.space_id == int(space_id),
            PersonalFamilyView.status != "never_computed",
        )
        if None not in account_ids:
            stmt = stmt.where(PersonalFamilyView.viewer_account_id.in_(concrete))
        for row in session.scalars(stmt).all():
            row.status = "stale"
            row.invalidated_at = now
            row.updated_at = now
            count += 1
    return count


def invalidate_space_views(session: Session, *, space_id: int) -> int:
    """Mark projections stale; read authorization still gates every response."""
    return invalidate_view_scopes(session, scopes={space_id: {None}})


def initialize_account_views(session: Session, *, account_id: int, user_id: int) -> int:
    """R2 后台初始化：为该账号每个 active 成员空间建 queued 视图行（幂等）。

    无空间 → 不建任何行；没有 Account（仅 provisional 引用）→ 不建行。
    由注册与成员资格获得事件在合法事务内调用；初始行绝不包含计算内容。
    """
    account = session.get(Account, account_id)
    if account is None or account.user_id != user_id:
        return 0
    space_ids = sorted(
        int(space_id)
        for space_id in session.scalars(
            select(SpaceMember.space_id).where(
                SpaceMember.user_id == user_id, SpaceMember.status == "active"
            )
        ).all()
    )
    now = utcnow()
    created = 0
    for space_id in space_ids:
        if session.get(FamilySpace, space_id) is None:
            continue
        exists = session.scalar(
            select(PersonalFamilyView.id).where(
                PersonalFamilyView.viewer_account_id == account_id,
                PersonalFamilyView.root_user_id == user_id,
                PersonalFamilyView.space_id == space_id,
            )
        )
        if exists is not None:
            continue
        session.add(
            PersonalFamilyView(
                viewer_account_id=account_id,
                root_user_id=user_id,
                space_id=space_id,
                status="queued",
                view_version=0,
                computation_version=COMPUTATION_VERSION,
                policy_version=POLICY_VERSION,
                created_at=now,
                updated_at=now,
            )
        )
        created += 1
    if created:
        session.flush()
    return created


def request_view_recompute(*, space_id: int) -> None:
    """显式短事务登记重算作业（独立 Session/事务；GET 事务绝不承担入队写）。

    通过 canonical enqueue 合同入队；队列已活跃或水位已被 succeeded 覆盖时
    幂等无操作。任何失败只记日志，绝不影响 GET 的安全空响应。
    """
    if not config.STEWARD_ENABLED:
        return
    from app.db import SessionLocal
    from app.services.steward import current_event_watermark, enqueue_steward_job

    try:
        with SessionLocal() as session:
            enqueue_steward_job(
                session,
                space_id=space_id,
                # Version drift has no new domain event to advance the watermark.
                cause="integrity_scan",
                trigger_cursor=current_event_watermark(session),
            )
    except Exception as exc:
        # 日志脱敏：异常原文可能携带 SQL 绑定参数，只记异常类名
        logger.warning(
            "pfv recompute enqueue failed for space %s (error=%s)",
            space_id,
            type(exc).__name__,
        )


def rebuild_space_views(session: Session, *, space_id: int) -> int:
    """Rebuild stale projections for a Steward space job.

    每份视图在独立 SAVEPOINT 内重建（R5：一份视图失败不污染 Session、
    不导致整个空间回滚；失败视图标记 failed 终态原因）。
    """
    rows = session.scalars(
        select(PersonalFamilyView).where(
            PersonalFamilyView.space_id == space_id,
            (
                PersonalFamilyView.status.in_(("queued", "stale", "failed", "never_computed"))
                | (PersonalFamilyView.policy_version != POLICY_VERSION)
                | (PersonalFamilyView.computation_version != COMPUTATION_VERSION)
                | PersonalFamilyView.input_hash.is_(None)
            ),
        )
    ).all()
    rebuilt = 0
    for row in rows:
        account = session.get(Account, row.viewer_account_id)
        if account is None:
            continue
        view_id = row.id
        try:
            with session.begin_nested():
                rebuild_view(session, account=account, space_id=space_id)
        except Exception as exc:  # keep one malformed projection from blocking the space
            failed = session.get(PersonalFamilyView, view_id)
            if failed is not None:
                failed.status = "failed"
                failed.failed_reason = type(exc).__name__
                failed.updated_at = utcnow()
                session.flush()
        else:
            rebuilt += 1
    return rebuilt


__all__ = [
    "COMPUTATION_VERSION",
    "POLICY_VERSION",
    "current_view_payload",
    "empty_view_payload",
    "etag_for",
    "get_current_view",
    "get_view",
    "initialize_account_views",
    "invalidate_space_views",
    "invalidate_view_scopes",
    "rebuild_space_views",
    "rebuild_view",
    "request_view_recompute",
    "view_is_current",
    "view_payload",
]
