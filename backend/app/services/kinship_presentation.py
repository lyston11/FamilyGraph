"""Viewer 绑定的亲属称谓呈现服务（任务 09-13-steward-kinship-presentation）。

职责（A design.md §2）：
- 把当前 PFV/Terms/规范化路径组合为一份版本化 ``KinshipPresentation``，
  供建议详情、通知和推测面板同源消费；
- 参考人永远是认证身份派生的 viewer，方向由 reference/target 显式承载，
  绝不靠字符串猜；人物展示值一律经 visibility payload，不直接读 ORM name；
- 候选（未确认）表达必须带「可能」状态；confirmed PFV 才可用无条件标签；
- 纯展示组合器：不调用模型、不另算关系、不写事实/词条。

显示文字不决定事实；来源枚举的显示文案由闭合映射给出，未知来源中性降级。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.personal_family_view import PersonalFamilyView, PersonalFamilyViewEdge
from app.models.user import User
from app.services import visibility

PRESENTATION_VERSION = 1

# 统一来源枚举（A-R2）：personal/space/locale/system/derived/structural/steward。
# steward 在 B 接入后出现；本服务先闭合其显示文案（中性降级由前端兜底）。
SOURCE_LABELS: dict[str, str] = {
    "personal": "我的叫法",
    "space": "家庭叫法",
    "locale": "地区用词",
    "system": "通用称谓",
    "derived": "管家称谓",
    "structural": "关系描述",
    "steward": "管家称谓",
}

EVIDENCE_CONFIRMED_PATH = "confirmed_path"
EVIDENCE_INFERRED_PATH = "inferred_path"
EVIDENCE_UNVERIFIED_CANDIDATE = "unverified_candidate"
EVIDENCE_UNAVAILABLE = "unavailable"


def source_label(source_level: str | None) -> str | None:
    """来源字样闭合映射；未知来源中性降级，不直接显示内部枚举。"""
    if source_level is None:
        return None
    return SOURCE_LABELS.get(source_level, "管家称谓")


def _display_payload(session: Session, viewer: User, target: User) -> dict[str, Any] | None:
    decision = visibility.evaluate(session, viewer, target, purpose=visibility.PURPOSE_PROFILE)
    if not decision.visible:
        return None
    return visibility.payload_from_decision(decision, target)


def _display_name(payload: dict[str, Any] | None, user_id: int) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("name"), str) and payload["name"]:
        return str(payload["name"])
    return "一位成员"


def _current_pfv_edge(
    session: Session, *, account: Account, space_id: int, target_user_id: int
) -> PersonalFamilyViewEdge | None:
    view = session.scalar(
        select(PersonalFamilyView).where(
            PersonalFamilyView.viewer_account_id == account.id,
            PersonalFamilyView.root_user_id == account.user_id,
            PersonalFamilyView.space_id == space_id,
        )
    )
    if view is None:
        return None
    return session.scalar(
        select(PersonalFamilyViewEdge).where(
            PersonalFamilyViewEdge.view_id == view.id,
            PersonalFamilyViewEdge.from_user_id == account.user_id,
            PersonalFamilyViewEdge.to_user_id == target_user_id,
            PersonalFamilyViewEdge.inclusion_reason_code != "inferred_path",
        )
    )


def build_relation_presentation(
    session: Session,
    *,
    viewer: User,
    account: Account,
    space_id: int,
    subject_user_id: int,
    object_user_id: int,
    relation_state: str,
    inferred: bool = False,
    term: str | None = None,
    term_source_level: str | None = None,
    related_fact_count: int | None = None,
) -> dict[str, Any]:
    """构造一份 viewer 绑定的称谓呈现（建议/通知共用）。

    - ``relation_state``: confirmed / inferred / proposal（由调用方按真实状态
      传入；本服务不从不成熟的显示文字反推状态）。
    - ``inferred=True`` 表示候选/推测：summary 必须带「可能」。
    - ``term`` 缺省时优先消费当前 PFV 的 confirmed 边称谓；无 PFV 则 term=None，
      返回自然线索而不伪装成已确认个人称谓。
    """
    subject = session.get(User, subject_user_id)
    object_row = session.get(User, object_user_id)
    if subject is None or object_row is None:
        return _unavailable(subject_user_id, object_user_id)
    subject_display = _display_payload(session, viewer, subject)
    object_display = _display_payload(session, viewer, object_row)
    if subject_display is None or object_display is None:
        return _unavailable(subject_user_id, object_user_id)

    subject_name = _display_name(subject_display, subject_user_id)
    object_name = _display_name(object_display, object_user_id)

    reference_user_id = viewer.id
    if term is None:
        pfv_edge = _current_pfv_edge(
            session,
            account=account,
            space_id=space_id,
            target_user_id=(object_user_id if subject_user_id == viewer.id else subject_user_id),
        )
        if pfv_edge is not None and pfv_edge.term:
            term = pfv_edge.term
            raw_level = pfv_edge.authorization_basis_json.get("term_source_level")
            if term_source_level is None and isinstance(raw_level, str):
                term_source_level = raw_level

    # 方向句：viewer 是端点之一时用「你的 X」；否则第三方方向句。
    if subject_user_id == viewer.id:
        summary = (
            f"{object_name}可能是你的{term}"
            if inferred and term
            else f"{object_name}是你的{term}"
            if term
            else f"你与{object_name}可能存在待核实的关系线索"
            if inferred
            else f"{subject_name}与{object_name}存在关系线索"
        )
    elif object_user_id == viewer.id:
        summary = (
            f"{subject_name}可能是你的{term}"
            if inferred and term
            else f"{subject_name}是你的{term}"
            if term
            else f"{subject_name}与你可能存在待核实的关系线索"
            if inferred
            else f"{subject_name}与你存在关系线索"
        )
    else:
        summary = (
            f"{subject_name}可能是{object_name}的{term}"
            if inferred and term
            else f"{subject_name}是{object_name}的{term}"
            if term
            else f"{subject_name}与{object_name}可能存在待核实的关系线索"
            if inferred
            else f"{subject_name}与{object_name}存在关系线索"
        )

    if related_fact_count is not None and related_fact_count > 0 and not inferred:
        evidence_kind = EVIDENCE_CONFIRMED_PATH
    elif inferred and related_fact_count is not None and related_fact_count > 0:
        evidence_kind = EVIDENCE_INFERRED_PATH
    elif inferred:
        evidence_kind = EVIDENCE_UNVERIFIED_CANDIDATE
    else:
        evidence_kind = EVIDENCE_UNAVAILABLE

    return {
        "version": PRESENTATION_VERSION,
        "availability": "ready",
        "reference_user_id": reference_user_id,
        "target_user_id": object_user_id if subject_user_id == viewer.id else subject_user_id,
        "subject_user_id": subject_user_id,
        "object_user_id": object_user_id,
        "subject_display": subject_display,
        "object_display": object_display,
        "term": term,
        "term_source_level": term_source_level,
        "term_source_label": source_label(term_source_level),
        "summary": summary,
        "relation_state": relation_state,
        "inferred": inferred,
        "evidence": {
            "kind": evidence_kind,
            "related_fact_count": related_fact_count,
        },
        "requires_action": False,
    }


def build_inferred_edge_presentation(
    session: Session,
    *,
    viewer: User,
    space_id: int,
    subject_user_id: int,
    object_user_id: int,
    term: str | None,
    evidence_fact_count: int,
) -> dict[str, Any]:
    """推测面板（PFV inferred_edges）的方向化呈现：单跳推测 + 确定性称谓。

    结构连线仍描述两个真实端点（A—term—B 歧义在此修正为方向句）；
    证据计数只承认可核验的相关 confirmed 事实，推测永远带「可能」。
    """
    subject = session.get(User, subject_user_id)
    object_row = session.get(User, object_user_id)
    if subject is None or object_row is None:
        return _unavailable(subject_user_id, object_user_id)
    subject_display = _display_payload(session, viewer, subject)
    object_display = _display_payload(session, viewer, object_row)
    if subject_display is None or object_display is None:
        return _unavailable(subject_user_id, object_user_id)
    subject_name = _display_name(subject_display, subject_user_id)
    object_name = _display_name(object_display, object_user_id)
    if subject_user_id == viewer.id and term:
        summary = f"{object_name}可能是你的{term}"
    elif object_user_id == viewer.id and term:
        summary = f"{subject_name}可能是你的{term}"
    elif term:
        summary = f"{subject_name}可能是{object_name}的{term}"
    else:
        summary = f"{subject_name}与{object_name}之间存在待核实的推测关系"
    if evidence_fact_count > 0:
        evidence_kind = EVIDENCE_INFERRED_PATH
    else:
        evidence_kind = EVIDENCE_UNVERIFIED_CANDIDATE
    return {
        "version": PRESENTATION_VERSION,
        "availability": "ready",
        "reference_user_id": viewer.id,
        "target_user_id": object_user_id if subject_user_id == viewer.id else subject_user_id,
        "subject_user_id": subject_user_id,
        "object_user_id": object_user_id,
        "subject_display": subject_display,
        "object_display": object_display,
        "term": term,
        "term_source_level": None,
        "term_source_label": source_label("derived"),
        "summary": summary,
        "relation_state": "inferred",
        "inferred": True,
        "evidence": {
            "kind": evidence_kind,
            "related_fact_count": evidence_fact_count if evidence_fact_count > 0 else None,
        },
        "requires_action": False,
    }


def _unavailable(subject_user_id: int, object_user_id: int) -> dict[str, Any]:
    return {
        "version": PRESENTATION_VERSION,
        "availability": "unavailable",
        "reference_user_id": None,
        "target_user_id": None,
        "subject_user_id": subject_user_id,
        "object_user_id": object_user_id,
        "subject_display": None,
        "object_display": None,
        "term": None,
        "term_source_level": None,
        "term_source_label": None,
        "summary": "该关系线索当前不可见",
        "relation_state": "inferred",
        "inferred": True,
        "evidence": {
            "kind": EVIDENCE_UNAVAILABLE,
            "related_fact_count": None,
        },
        "requires_action": False,
    }


__all__ = [
    "PRESENTATION_VERSION",
    "build_inferred_edge_presentation",
    "build_relation_presentation",
    "source_label",
]
