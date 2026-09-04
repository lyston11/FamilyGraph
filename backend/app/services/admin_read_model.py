"""/admin-api/v1 读模型查询服务（09-04 子任务 2）。

聚合根判定只查询 ``SpaceMember(space_id, role='space_admin', status='active')``；
``owner_id``、``is_admin``、家庭 visibility 链一律不参与授权或聚合。

红线：
- 所有投影使用显式列 + 专用 schema（schemas/admin_read.py 白名单）；
- ``RawRelationInput.text``、证据原文、私人描述、认证秘密永不查询；
- 列表统一分页 envelope，page_size ≤ 100，稳定排序；
- 敏感字段读取使用批量名称解析，控制 N+1。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, cast

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.admin_access import AdminAccessAudit
from app.models.agent import AgentJob, AgentRun, AgentSession
from app.models.attachment import Attachment
from app.models.notification import Notification
from app.models.relation import Relation
from app.models.relationship_facts import SourceFact
from app.models.space import (
    FamilySpace,
    ManagerTransferConsent,
    SpaceManagerApplication,
    SpaceMember,
    SpaceProfileRef,
)
from app.models.user import User
from app.schemas.admin_read import (
    AdminAgentErrorOut,
    AdminAgentJobOut,
    AdminAgentRunOut,
    AdminAttachmentMetadataOut,
    AdminAuditAccessOut,
    AdminFactOut,
    AdminMemberOut,
    AdminNotificationOut,
    AdminOperationsQueueItemOut,
    AdminOverviewItemOut,
    AdminOverviewTotalsOut,
    AdminRelationOut,
    AdminSpaceAdminOut,
    AdminSpaceSummaryOut,
)
from app.services.admin_sanitizer import sanitize_error_payload, sanitize_text
from app.utils import timeutil

ADMIN_RESOURCE_NOT_FOUND = "ADMIN_RESOURCE_NOT_FOUND"
ADMIN_RESOURCE_NOT_FOUND_MESSAGE = "资源不存在或不可访问"


# 列值由 DB CHECK 约束兜底（family_spaces.kind / relations.dir_class /
# admin_access_audits.target_type），读取侧收窄为 Literal 以满足响应 schema。
def _kind(value: str) -> Literal["household", "lineage"]:
    return cast(Literal["household", "lineage"], value)


def _dir_class(value: str) -> Literal["elder", "younger", "peer", "spouse"]:
    return cast(Literal["elder", "younger", "peer", "spouse"], value)


def _target_type(value: str | None) -> Literal["user", "space"] | None:
    return cast(Literal["user", "space"] | None, value)


def page_envelope(items: list[Any], total: int, page: int, page_size: int) -> dict[str, Any]:
    """统一分页 envelope：{items, page, page_size, total, has_more}。"""
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": page * page_size < total,
    }


def _names_for(session: Session, user_ids: set[int]) -> dict[int, str]:
    """批量解析档案名（避免逐行 N+1）；已删除档案不出现。"""
    if not user_ids:
        return {}
    rows = session.execute(select(User.id, User.name).where(User.id.in_(user_ids))).all()
    return {row[0]: row[1] for row in rows}


# ---- 空间健康与异常（overview / queue / detail 共用） ----

# (空间, 其 active space_admin 成员组 [(member, user, account)])。
# 正常情况每组恰好一个成员行；同一空间出现多行（违反唯一索引不变量）时
# 由 _anomalies_for 标记 duplicate_active_admin，只读展示、不自动修复。
SpaceRow = tuple[FamilySpace, list[tuple[SpaceMember, User | None, Account | None]]]


def _space_rows(session: Session) -> list[SpaceRow]:
    """全部空间 × active space_admin 成员组 × 档案 × 账号（单查询，按空间分组）。"""
    admin_member = and_(
        SpaceMember.space_id == FamilySpace.id,
        SpaceMember.role == "space_admin",
        SpaceMember.status == "active",
    )
    rows = session.execute(
        select(FamilySpace, SpaceMember, User, Account)
        .join(SpaceMember, admin_member, isouter=True)
        .join(User, User.id == SpaceMember.user_id, isouter=True)
        .join(Account, Account.user_id == User.id, isouter=True)
        .order_by(FamilySpace.id)
    ).all()
    grouped: dict[int, SpaceRow] = {}
    for space, member, user, account in rows:
        entry = grouped.get(space.id)
        if entry is None:
            entry = (space, [])
            grouped[space.id] = entry
        if member is not None:
            entry[1].append((member, user, account))
    return list(grouped.values())


def _member_counts(session: Session) -> dict[int, int]:
    rows = session.execute(
        select(SpaceMember.space_id, func.count())
        .where(SpaceMember.status == "active")
        .group_by(SpaceMember.space_id)
    ).all()
    return {int(row[0]): int(row[1]) for row in rows}


def _anomalies_for(
    admins: list[tuple[SpaceMember, User | None, Account | None]], now: datetime
) -> list[str]:
    """只读异常判定：无管理员 / 双管理员 / 管理员被删 / 管理员账号缺失 / 被锁定。

    后台只展示、不自动修复（PRD RM-F1 / design §2）。双管理员仅在唯一索引
    不变量被绕过时出现，读侧独立检测以保持异常队列合同完整。
    """
    if not admins:
        return ["no_active_admin"]
    found: list[str] = []
    if len(admins) > 1:
        found.append("duplicate_active_admin")
    for _member, user, account in admins:
        if user is None or user.deleted_at is not None:
            if "admin_deleted" not in found:
                found.append("admin_deleted")
        if account is None:
            if "admin_account_missing" not in found:
                found.append("admin_account_missing")
        elif account.locked_until is not None and account.locked_until > now:
            if "admin_locked" not in found:
                found.append("admin_locked")
    return found


def _sorted_anomaly_items(
    session: Session, *, kind_filter: str | None, status_filter: str | None
) -> list[AdminOperationsQueueItemOut]:
    if kind_filter not in (None, "space_anomaly"):
        return []
    if status_filter not in (None, "open"):
        return []
    now = timeutil.utcnow()
    items: list[AdminOperationsQueueItemOut] = []
    for space, admins in _space_rows(session):
        anomalies = _anomalies_for(admins, now)
        if not anomalies:
            continue
        items.append(
            AdminOperationsQueueItemOut(
                kind="space_anomaly",
                status="open",
                reference_id=space.id,
                space_id=space.id,
                space_name=space.name,
                space_kind=_kind(space.kind),
                anomaly=anomalies[0],
                applicant_user_id=None,
                applicant_name=None,
                request_kind=None,
                created_at=space.created_at,
            )
        )
    return items


# ---- overview / space-admins / spaces ----


def overview(
    session: Session,
    *,
    page: int,
    page_size: int,
    search: str | None,
    status: str | None,
    from_dt: datetime | None,
    to_dt: datetime | None,
) -> dict[str, Any]:
    """空间健康总览：分页列表 + 全局统计；聚合判定只看 active space_admin。"""
    now = timeutil.utcnow()
    rows = _space_rows(session)
    counts = _member_counts(session)

    totals_active_admins = 0
    anomaly_spaces = 0
    items: list[AdminOverviewItemOut] = []
    for space, admins in rows:
        anomalies = _anomalies_for(admins, now)
        totals_active_admins += len(admins)
        if anomalies:
            anomaly_spaces += 1
        manager_user_id: int | None = None
        manager_name: str | None = None
        if admins:
            primary_member, primary_user, _primary_account = admins[0]
            manager_user_id = primary_member.user_id
            if primary_user is not None and primary_user.deleted_at is None:
                manager_name = primary_user.name
        if search:
            needle = search.lower()
            if needle not in space.name.lower() and needle not in (manager_name or "").lower():
                continue
        if status == "healthy" and anomalies:
            continue
        if status == "anomaly" and not anomalies:
            continue
        if from_dt is not None and space.created_at < from_dt:
            continue
        if to_dt is not None and space.created_at > to_dt:
            continue
        items.append(
            AdminOverviewItemOut(
                space_id=space.id,
                name=space.name,
                kind=_kind(space.kind),
                created_at=space.created_at,
                manager_user_id=manager_user_id,
                manager_name=manager_name,
                member_count=counts.get(space.id, 0),
                status="anomaly" if anomalies else "healthy",
                anomalies=anomalies,
            )
        )

    pending_applications = (
        session.scalar(
            select(func.count())
            .select_from(SpaceManagerApplication)
            .where(SpaceManagerApplication.status == "pending")
        )
        or 0
    )
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "page": page,
        "page_size": page_size,
        "total": len(items),
        "has_more": page * page_size < len(items),
        "totals": AdminOverviewTotalsOut(
            spaces_total=len(rows),
            healthy_spaces=len(rows) - anomaly_spaces,
            anomaly_spaces=anomaly_spaces,
            active_space_admins=totals_active_admins,
            pending_applications=int(pending_applications),
        ),
    }


def space_admins(
    session: Session, *, page: int, page_size: int, search: str | None, status: str | None
) -> dict[str, Any]:
    """active space_admin 聚合行（同一用户在多个空间合计 space_count）。"""
    stmt = (
        select(
            User.id,
            User.name,
            User.gender,
            User.profile_status,
            User.avatar_path.is_not(None).label("avatar_available"),
            User.created_at,
            Account.status.label("account_status"),
            func.count(SpaceMember.space_id).label("space_count"),
        )
        .join(
            SpaceMember,
            and_(
                SpaceMember.user_id == User.id,
                SpaceMember.role == "space_admin",
                SpaceMember.status == "active",
            ),
        )
        .join(Account, Account.user_id == User.id)
        .group_by(
            User.id,
            User.name,
            User.gender,
            User.profile_status,
            User.avatar_path,
            User.created_at,
            Account.status,
        )
        .order_by(User.id)
    )
    if search:
        stmt = stmt.where(User.name.ilike(f"%{search}%"))
    if status:
        stmt = stmt.where(Account.status == status)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    items = [
        AdminSpaceAdminOut(
            admin_user_id=row.id,
            name=row.name,
            gender=row.gender,
            profile_status=row.profile_status,
            account_status=row.account_status,
            avatar_available=bool(row.avatar_available),
            space_count=int(row.space_count),
            created_at=row.created_at,
        )
        for row in rows
    ]
    return page_envelope(items, int(total), page, page_size)


def admin_spaces(
    session: Session, admin_user_id: int, *, page: int, page_size: int
) -> dict[str, Any]:
    """某管理员管理的空间；未知管理员返回空页（防存在性枚举）。"""
    member_cond = and_(
        SpaceMember.space_id == FamilySpace.id,
        SpaceMember.role == "space_admin",
        SpaceMember.status == "active",
        SpaceMember.user_id == admin_user_id,
    )
    stmt = (
        select(FamilySpace, SpaceMember, User)
        .join(SpaceMember, member_cond)
        .join(User, User.id == SpaceMember.user_id)
        .order_by(FamilySpace.id)
    )
    total = (
        session.scalar(
            select(func.count())
            .select_from(FamilySpace)
            .join(SpaceMember, member_cond)
            .join(User, User.id == SpaceMember.user_id)
        )
        or 0
    )
    rows = session.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    items = [
        AdminSpaceSummaryOut(
            space_id=space.id,
            name=space.name,
            kind=_kind(space.kind),
            created_at=space.created_at,
            manager_user_id=member.user_id,
            manager_name=user.name,
        )
        for space, member, user in rows
    ]
    return page_envelope(items, int(total), page, page_size)


def space_detail(session: Session, space_id: int) -> dict[str, Any] | None:
    """单空间健康详情；未知空间 None（路由统一安全 404）。"""
    now = timeutil.utcnow()
    for space, admins in _space_rows(session):
        if space.id != space_id:
            continue
        anomalies = _anomalies_for(admins, now)
        counts = _member_counts(session)
        primary_member, primary_user = admins[0][:2] if admins else (None, None)
        return {
            "space_id": space.id,
            "name": space.name,
            "kind": space.kind,
            "created_at": space.created_at,
            "manager_user_id": primary_member.user_id if primary_member is not None else None,
            "manager_name": (
                primary_user.name
                if primary_user is not None and primary_user.deleted_at is None
                else None
            ),
            "member_count": counts.get(space.id, 0),
            "anomalies": anomalies,
        }
    return None


# ---- 空间成员 / 关系 / 事实 ----


def space_members(
    session: Session,
    space_id: int,
    *,
    page: int,
    page_size: int,
    search: str | None,
    status: str | None,
) -> dict[str, Any]:
    """空间成员投影；未知空间返回空页（防存在性枚举）。"""
    stmt = (
        select(SpaceMember, User)
        .join(User, User.id == SpaceMember.user_id)
        .where(SpaceMember.space_id == space_id)
        .order_by(SpaceMember.id)
    )
    if search:
        stmt = stmt.where(User.name.ilike(f"%{search}%"))
    if status:
        stmt = stmt.where(SpaceMember.status == status)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    items = [
        AdminMemberOut(
            user_id=member.user_id,
            name=user.name,
            role=member.role,
            status=member.status,
            created_at=member.created_at,
            updated_at=member.updated_at,
        )
        for member, user in rows
    ]
    return page_envelope(items, int(total), page, page_size)


def space_relations(
    session: Session, space_id: int, *, page: int, page_size: int, status: str | None
) -> dict[str, Any]:
    """空间内结构化关系边（敏感：需 space 访问会话）；label 经脱敏。

    空间归属 = 任一端点是该空间 active 成员或 active provisional 引用；
    RawRelationInput.text 与证据原文永不查询。
    """
    member_ids = select(SpaceMember.user_id).where(
        SpaceMember.space_id == space_id, SpaceMember.status == "active"
    )
    ref_ids = select(SpaceProfileRef.user_id).where(
        SpaceProfileRef.space_id == space_id, SpaceProfileRef.status == "active"
    )
    cond = or_(
        Relation.from_user.in_(member_ids),
        Relation.from_user.in_(ref_ids),
        Relation.to_user.in_(member_ids),
        Relation.to_user.in_(ref_ids),
    )
    stmt = select(Relation).where(cond)
    if status:
        stmt = stmt.where(Relation.status == status)
    stmt = stmt.order_by(Relation.id)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(session.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all())
    names = _names_for(session, {r.from_user for r in rows} | {r.to_user for r in rows})
    items = []
    for row in rows:
        label_out = None
        if row.label:
            outcome = sanitize_text(row.label)
            label_out = outcome.value if outcome.reliable else None
        items.append(
            AdminRelationOut(
                id=row.id,
                from_user_id=row.from_user,
                from_user_name=names.get(row.from_user),
                to_user_id=row.to_user,
                to_user_name=names.get(row.to_user),
                dir_class=_dir_class(row.dir_class),
                status=row.status,
                space_id=space_id,
                label_safe=label_out,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )
    return page_envelope(items, int(total), page, page_size)


def space_facts(session: Session, space_id: int, *, page: int, page_size: int) -> dict[str, Any]:
    """confirmed 事实（state 恒 confirmed；provenance 为枚举非原文）。"""
    member_ids = select(SpaceMember.user_id).where(
        SpaceMember.space_id == space_id, SpaceMember.status == "active"
    )
    cond = or_(
        SourceFact.space_id == space_id,
        and_(
            SourceFact.space_id.is_(None),
            or_(
                SourceFact.subject_user_id.in_(member_ids),
                SourceFact.object_user_id.in_(member_ids),
            ),
        ),
    )
    stmt = select(SourceFact).where(cond, SourceFact.state == "confirmed").order_by(SourceFact.id)
    total = (
        session.scalar(
            select(func.count())
            .select_from(SourceFact)
            .where(cond, SourceFact.state == "confirmed")
        )
        or 0
    )
    rows = list(session.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all())
    names = _names_for(
        session, {r.subject_user_id for r in rows} | {r.object_user_id for r in rows}
    )
    items = [
        AdminFactOut(
            id=row.id,
            fact_type=row.fact_type,
            subject_user_id=row.subject_user_id,
            subject_name=names.get(row.subject_user_id),
            object_user_id=row.object_user_id,
            object_name=names.get(row.object_user_id),
            space_id=row.space_id,
            state="confirmed",
            provenance=row.provenance,
            revision=row.revision,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]
    return page_envelope(items, int(total), page, page_size)


# ---- 档案 / 头像 / 附件 ----


def user_profile(session: Session, user_id: int) -> dict[str, Any] | None:
    """基础档案白名单投影（敏感：需 user 访问会话）；claim_status 来自 accounts。"""
    row = session.execute(
        select(User, Account)
        .join(Account, Account.user_id == User.id)
        .where(User.id == user_id, User.deleted_at.is_(None))
    ).first()
    if row is None:
        return None
    user, account = row
    return {
        "id": user.id,
        "name": user.name,
        "gender": user.gender,
        "birth": user.birth,
        "death": user.death,
        "bio": user.bio,
        "avatar_available": user.avatar_path is not None,
        "profile_status": user.profile_status,
        "claim_status": account.status,
        "created_at": user.created_at,
    }


def user_avatar_filename(session: Session, user_id: int) -> str | None:
    """头像存储文件名（仅供 8002 缩略图端点内部读取，不外泄路径）。"""
    row = session.execute(
        select(User.avatar_path).where(User.id == user_id, User.deleted_at.is_(None))
    ).first()
    if row is None or row[0] is None:
        return None
    return str(row[0])


def user_attachments(
    session: Session, user_id: int, *, page: int, page_size: int
) -> dict[str, Any]:
    """附件安全元数据：id/type/title_safe/created_at；url/path/description 永不出。"""
    stmt = (
        select(Attachment.id, Attachment.type, Attachment.title, Attachment.created_at)
        .where(Attachment.user_id == user_id)
        .order_by(Attachment.id)
    )
    total = (
        session.scalar(
            select(func.count()).select_from(Attachment).where(Attachment.user_id == user_id)
        )
        or 0
    )
    rows = session.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    items = []
    for att_id, att_type, title, created_at in rows:
        title_out = None
        if title:
            outcome = sanitize_text(title)
            title_out = outcome.value if outcome.reliable else None
        items.append(
            AdminAttachmentMetadataOut(
                id=att_id, type=att_type, title_safe=title_out, created_at=created_at
            )
        )
    return page_envelope(items, int(total), page, page_size)


# ---- 运营队列 / 通知 ----


def operations_queue(
    session: Session, *, page: int, page_size: int, kind: str | None, status: str | None
) -> dict[str, Any]:
    """异常队列 + 管理员申请裁决队列（只读展示，不自动修复）。

    规模受控：异常项 ≤ 空间数；申请按 id 降序取前 1000 条后内存归并分页。
    """
    items: list[AdminOperationsQueueItemOut] = []
    items.extend(_sorted_anomaly_items(session, kind_filter=kind, status_filter=status))

    if kind != "space_anomaly":
        stmt = (
            select(SpaceManagerApplication, User, FamilySpace)
            .join(User, User.id == SpaceManagerApplication.applicant_user_id)
            .join(FamilySpace, FamilySpace.id == SpaceManagerApplication.space_id)
            .order_by(SpaceManagerApplication.id.desc())
            .limit(1000)
        )
        if status is not None:
            stmt = stmt.where(SpaceManagerApplication.status == status)
        rows = session.execute(stmt).all()
        for application, applicant, space in rows:
            items.append(
                AdminOperationsQueueItemOut(
                    kind="manager_application",
                    status=application.status,
                    reference_id=application.id,
                    space_id=space.id,
                    space_name=space.name,
                    space_kind=_kind(space.kind),
                    anomaly=None,
                    applicant_user_id=application.applicant_user_id,
                    applicant_name=applicant.name,
                    request_kind=application.request_kind,
                    created_at=application.created_at,
                )
            )

    items.sort(key=lambda item: (item.created_at, item.kind, item.reference_id), reverse=True)
    start = (page - 1) * page_size
    return page_envelope(items[start : start + page_size], len(items), page, page_size)


def notifications(
    session: Session, *, page: int, page_size: int, space_id: int | None, read: bool | None
) -> dict[str, Any]:
    """通知最小投影（title/summary 为服务端生成的最小安全文案）。"""
    stmt = select(Notification).order_by(Notification.id.desc())
    if space_id is not None:
        stmt = stmt.where(Notification.space_id == space_id)
    if read is True:
        stmt = stmt.where(Notification.read_at.is_not(None))
    if read is False:
        stmt = stmt.where(Notification.read_at.is_(None))
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(session.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all())
    items = [
        AdminNotificationOut(
            id=row.id,
            kind=row.kind,
            space_id=row.space_id,
            recipient_account_id=row.recipient_account_id,
            actor_user_id=row.actor_user_id,
            title=row.title,
            summary=row.summary,
            created_at=row.created_at,
            read_at=row.read_at,
        )
        for row in rows
    ]
    return page_envelope(items, int(total), page, page_size)


# ---- Agent 运行诊断 ----


def _agent_error(raw_error: Any, *, error_code: str | None = None) -> AdminAgentErrorOut | None:
    """原始 error_json → 二次脱敏诊断；原文永不直接序列化。"""
    if raw_error is None:
        return None
    resolved_code = error_code
    if resolved_code is None and isinstance(raw_error, dict):
        candidate = raw_error.get("error_code")
        resolved_code = str(candidate) if isinstance(candidate, str) else None
    payload = sanitize_error_payload(raw_error, error_code=resolved_code)
    if payload is None:
        return None
    return AdminAgentErrorOut(**payload)


def agent_runs(
    session: Session,
    *,
    page: int,
    page_size: int,
    space_id: int | None,
    status: str | None,
    from_dt: datetime | None,
    to_dt: datetime | None,
) -> dict[str, Any]:
    stmt = select(AgentRun, AgentSession).join(AgentSession, AgentSession.id == AgentRun.session_id)
    if space_id is not None:
        stmt = stmt.where(AgentSession.space_id == space_id)
    if status is not None:
        stmt = stmt.where(AgentRun.status == status)
    if from_dt is not None:
        stmt = stmt.where(AgentRun.created_at >= from_dt)
    if to_dt is not None:
        stmt = stmt.where(AgentRun.created_at <= to_dt)
    stmt = stmt.order_by(AgentRun.id.desc())
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    items = [
        AdminAgentRunOut(
            id=run.id,
            session_id=run.session_id,
            job_id=run.job_id,
            space_id=agent_session.space_id,
            account_id=agent_session.account_id,
            kind=run.kind,
            status=run.status,
            attempt=run.attempt,
            max_attempts=run.max_attempts,
            lease_expires_at=run.lease_expires_at,
            heartbeat_at=run.heartbeat_at,
            cancel_requested=run.cancel_requested,
            error_code=run.error_code,
            error=_agent_error(run.error_json, error_code=run.error_code),
            created_at=run.created_at,
            updated_at=run.updated_at,
            settled_at=run.settled_at,
        )
        for run, agent_session in rows
    ]
    return page_envelope(items, int(total), page, page_size)


def agent_jobs(
    session: Session, *, page: int, page_size: int, space_id: int | None, status: str | None
) -> dict[str, Any]:
    stmt = select(AgentJob)
    if space_id is not None:
        stmt = stmt.where(AgentJob.space_id == space_id)
    if status is not None:
        stmt = stmt.where(AgentJob.status == status)
    stmt = stmt.order_by(AgentJob.id.desc())
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(session.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all())
    items = [
        AdminAgentJobOut(
            id=row.id,
            run_id=row.run_id,
            space_id=row.space_id,
            account_id=row.account_id,
            kind=row.kind,
            status=row.status,
            attempt=row.attempt,
            max_attempts=row.max_attempts,
            lease_expires_at=row.lease_expires_at,
            heartbeat_at=row.heartbeat_at,
            cancel_requested=row.cancel_requested,
            error=_agent_error(row.error_json),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]
    return page_envelope(items, int(total), page, page_size)


# ---- 审批投影（approve/reject 响应复用） ----


def serialize_admin_application(
    session: Session, application: SpaceManagerApplication
) -> dict[str, Any]:
    """申请最小投影：申请人名 + 目标空间名；无家庭档案字段。"""
    applicant = session.get(User, application.applicant_user_id)
    space = session.get(FamilySpace, application.space_id)
    consent = session.scalar(
        select(ManagerTransferConsent).where(
            ManagerTransferConsent.application_id == application.id
        )
    )
    return {
        "id": application.id,
        "applicant_user_id": application.applicant_user_id,
        "applicant_name": applicant.name if applicant else None,
        "space_id": application.space_id,
        "space_name": space.name if space else None,
        "space_kind": space.kind if space else None,
        "request_kind": application.request_kind,
        "status": application.status,
        "decision_note": application.decision_note,
        "transfer_consent_id": consent.id if consent else None,
        "transfer_consent_status": consent.status if consent else None,
        "created_at": application.created_at,
        "decided_at": application.decided_at,
        "system_admin_decided_by": application.system_admin_decided_by,
    }


# ---- 审计时间线 ----


def access_audits(
    session: Session,
    *,
    page: int,
    page_size: int,
    target_type: str | None,
    target_id: int | None,
) -> dict[str, Any]:
    """后台访问审计时间线（分页；审计行永久保留，无正文/敏感值）。"""
    stmt = select(AdminAccessAudit).order_by(AdminAccessAudit.id.desc())
    if target_type is not None:
        stmt = stmt.where(AdminAccessAudit.target_type == target_type)
    if target_id is not None:
        stmt = stmt.where(AdminAccessAudit.target_id == target_id)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(session.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all())
    items = [
        AdminAuditAccessOut(
            id=row.id,
            system_admin_id=row.system_admin_id,
            session_id=row.session_id,
            action=row.action,
            target_type=_target_type(row.target_type),
            target_id=row.target_id,
            endpoint=row.endpoint,
            filters=dict(row.filters_json or {}),
            result_count=row.result_count,
            request_id=row.request_id,
            ip=row.ip,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return page_envelope(items, int(total), page, page_size)
