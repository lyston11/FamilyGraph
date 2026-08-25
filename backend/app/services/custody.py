"""代管权判定单点（m1a design 权限矩阵；architecture.md §1/D5）。

M1 阶段无可见性模块：非相关者一律 none（API 层 404 语义，防枚举）；
M2 由 visibility.py 接管 summary 层，本模块届时仅保留 edit/delete 判定。
admin 编辑/删除走同一入口，审计由各路由强制记录（矩阵"audit 记录"要求）。
"""

from dataclasses import dataclass

from app.errors import CUSTODY_HANDOVER_DONE, USER_NOT_FOUND, raise_api_error
from app.models.user import User

VIEW_FULL = "full"
VIEW_SUMMARY = "summary"  # M2 起由 visibility.py 接管，M1 不产生
VIEW_NONE = "none"


@dataclass(frozen=True)
class RelationAccess:
    """actor 对 target 的权限三元组（design.md 权限矩阵的行投影）。"""

    view: str
    edit: bool
    delete: bool


def resolve_relation(actor: User, target: User) -> RelationAccess:
    """按权限矩阵逐行判定（self > 创建者 > admin > 其他）。"""
    if actor.id == target.id:
        return RelationAccess(VIEW_FULL, True, True)

    if target.created_by == actor.id:
        if target.privacy_mode == "perpetual":
            # D5：创建者永久编辑权，认领不失权
            return RelationAccess(VIEW_FULL, True, True)
        if target.claim_status == "managed":
            # handover 未 claimed：创建者代管
            return RelationAccess(VIEW_FULL, True, True)
        # handover 已 claimed：编辑权移交本人，创建者退只读
        return RelationAccess(VIEW_FULL, False, False)

    if actor.is_admin:
        return RelationAccess(VIEW_FULL, True, True)

    return RelationAccess(VIEW_NONE, False, False)


_HANDOVER_DONE_MESSAGE = "该档案已被本人认领，编辑权已按归属约定移交给本人"
_NOT_FOUND_MESSAGE = "资源不存在"


def assert_can_edit(actor: User, target: User) -> RelationAccess:
    """编辑权统一入口（PATCH 档案/disclosure/将来附件共用）。

    - view none → 404 USER_NOT_FOUND（防枚举）
    - view full 但失权 → 403 CUSTODY_HANDOVER_DONE
    """
    access = resolve_relation(actor, target)
    if access.view == VIEW_NONE:
        raise_api_error(404, USER_NOT_FOUND, _NOT_FOUND_MESSAGE)
    if not access.edit:
        raise_api_error(403, CUSTODY_HANDOVER_DONE, _HANDOVER_DONE_MESSAGE)
    return access


def assert_can_delete(actor: User, target: User) -> None:
    """删除权入口：本人 ∨ 代管创建者（perpetual 或 handover 未 claimed）∨ admin。"""
    access = resolve_relation(actor, target)
    if access.view == VIEW_NONE:
        raise_api_error(404, USER_NOT_FOUND, _NOT_FOUND_MESSAGE)
    if not access.delete:
        raise_api_error(403, CUSTODY_HANDOVER_DONE, _HANDOVER_DONE_MESSAGE)


def require_visible_target(session_target: User | None) -> User:
    """目标存在性统一判定：不存在与不可见同一 404 响应（防枚举）。"""
    if session_target is None:
        raise_api_error(404, USER_NOT_FOUND, _NOT_FOUND_MESSAGE)
    return session_target


__all__ = [
    "RelationAccess",
    "VIEW_FULL",
    "VIEW_NONE",
    "VIEW_SUMMARY",
    "assert_can_delete",
    "assert_can_edit",
    "require_visible_target",
    "resolve_relation",
]
