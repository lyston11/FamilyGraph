"""附件路由（m3a）：上传/链接/列表/授权下载/删除。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import get_db, require_authenticated_user
from app.errors import ATTACHMENT_NOT_FOUND, USER_NOT_FOUND, raise_api_error
from app.models.account import Account
from app.models.attachment import Attachment
from app.models.user import User
from app.services import attachments as att_service
from app.services import audit, custody
from app.services.visibility import SUMMARY, classify

router = APIRouter(tags=["attachments"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _attachment_out(row: Attachment) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "type": row.type,
        "title": row.title,
        "description": row.description,
        "url_or_path": None if row.type == "image" else row.url_or_path,
        "created_at": row.created_at.isoformat(),
    }


def _require_view(session: Session, viewer: User, owner_id: int) -> tuple[str, User]:
    target = session.get(User, owner_id)
    if target is None:
        raise_api_error(404, USER_NOT_FOUND, "档案不存在")
    level = visibility_level(session, viewer, target)
    if level == "invisible" and viewer.id != owner_id:
        # 管理员/本人由 classify 内部处理；此处仅拦不可见者
        if not (viewer.is_admin or _is_custodian(viewer, target)):
            raise_api_error(404, USER_NOT_FOUND, "档案不存在")
    return level, target


def visibility_level(session: Session, viewer: User, target: User) -> str:
    from app.services import visibility

    return visibility.classify(session, viewer, target)


def _is_custodian(viewer: User, target: User) -> bool:
    return target.created_by == viewer.id


@router.post("/users/{user_id}/attachments/image", status_code=201)
def upload_image(
    user_id: int,
    request: Request,
    file: UploadFile = File(...),  # noqa: B008
    title: str = "",
    session: OrmSession = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> dict[str, Any]:
    """同步路由：PIL 重编码为 CPU 密集操作，由 FastAPI 线程池执行（spec 禁止阻塞 async 路由）。"""

    actor, _account = identity
    target = session.get(User, user_id)
    if target is None:
        raise_api_error(404, USER_NOT_FOUND, "档案不存在")
    custody.assert_can_edit(actor, target)

    file.file.seek(0)
    data = file.file.read()
    att_service.validate_image_upload(file.filename or "", data)
    clean, _ext = att_service.reencode_strip_metadata(data)
    path = att_service.save_image(user_id, clean)

    row = Attachment(
        user_id=user_id,
        type="image",
        url_or_path=path.name,
        title=title or None,
        uploaded_by=actor.id,
        created_at=__import__("app.utils.timeutil", fromlist=["utcnow"]).utcnow(),
    )
    session.add(row)
    audit.write_audit(
        session,
        action="attachment_uploaded",
        actor_id=actor.id,
        target_id=user_id,
        ip=_client_ip(request),
        detail={"id": None},
    )
    session.commit()
    session.refresh(row)
    return _attachment_out(row)


class LinkPayload(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


@router.post("/users/{user_id}/attachments/link", status_code=201)
def add_link(
    user_id: int,
    payload: LinkPayload,
    request: Request,
    session: OrmSession = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> dict[str, Any]:
    actor, _account = identity
    target = session.get(User, user_id)
    if target is None:
        raise_api_error(404, USER_NOT_FOUND, "档案不存在")
    custody.assert_can_edit(actor, target)

    att_service.validate_link_url(payload.url)

    row = Attachment(
        user_id=user_id,
        type="link",
        url_or_path=payload.url.strip(),
        title=payload.title,
        description=payload.description,
        uploaded_by=actor.id,
        created_at=__import__("app.utils.timeutil", fromlist=["utcnow"]).utcnow(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _attachment_out(row)


@router.get("/users/{user_id}/attachments")
def list_attachments(
    user_id: int,
    session: OrmSession = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[dict[str, Any]]:
    """可见性：full 或 summary+attachments 披露 → 元数据；否则 404 语义空。"""
    actor, _account = identity
    target = session.get(User, user_id)
    if target is None:
        raise_api_error(404, USER_NOT_FOUND, "档案不存在")
    level = classify(session, actor, target)
    if level == "invisible":
        raise_api_error(404, USER_NOT_FOUND, "档案不存在")
    if level == SUMMARY:
        from app.services.visibility import _disclosure_flags

        if not _disclosure_flags(target).get("attachments"):
            raise_api_error(404, USER_NOT_FOUND, "档案不存在")

    rows = session.scalars(select(Attachment).where(Attachment.user_id == user_id)).all()
    return [_attachment_out(r) for r in rows]


@router.get("/attachments/{attachment_id}/raw")
def download_attachment(
    attachment_id: int,
    session: OrmSession = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> FileResponse:
    """授权流式下载：full 可下；summary 仅当归属者开放 attachments 披露。"""
    actor, _account = identity
    row = session.get(Attachment, attachment_id)
    if row is None:
        raise_api_error(404, ATTACHMENT_NOT_FOUND, "附件不存在")
    target = session.get(User, row.user_id)
    if target is None:
        raise_api_error(404, USER_NOT_FOUND, "档案不存在")

    level = classify(session, actor, target)
    allowed = level == "full"
    if level == SUMMARY:
        from app.services.visibility import _disclosure_flags

        allowed = _disclosure_flags(target).get("attachments", False)
    if not allowed:
        raise_api_error(404, ATTACHMENT_NOT_FOUND, "附件不存在")

    from app.config import UPLOADS_DIR

    path = UPLOADS_DIR / PathLike_name(row.url_or_path)
    if not path.exists():
        raise_api_error(404, ATTACHMENT_NOT_FOUND, "附件文件缺失")
    return FileResponse(
        path,
        media_type="image/png",
        headers={
            "Content-Disposition": f'inline; filename="{path.name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def PathLike_name(url_or_path: str) -> str:
    from pathlib import Path

    return Path(url_or_path).name


@router.delete("/attachments/{attachment_id}", status_code=204)
def delete_attachment(
    attachment_id: int,
    request: Request,
    session: OrmSession = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> Response:
    """删除（D5 编辑权主体）：先事务删记录，后异步删物理文件。"""
    actor, _account = identity
    row = session.get(Attachment, attachment_id)
    if row is None:
        raise_api_error(404, ATTACHMENT_NOT_FOUND, "附件不存在")
    target = session.get(User, row.user_id)
    if target is None:
        raise_api_error(404, USER_NOT_FOUND, "档案不存在")
    custody.assert_can_edit(actor, target)

    path_name = row.url_or_path if row.type == "image" else None
    session.delete(row)
    audit.write_audit(
        session,
        action="attachment_deleted",
        actor_id=actor.id,
        target_id=row.user_id,
        ip=_client_ip(request),
        detail={"path": path_name},
    )
    session.commit()

    if path_name:
        deleted = att_service.delete_file_quiet(path_name)
        if not deleted:
            import logging

            logging.getLogger(__name__).warning("orphan file cleanup deferred: %s", path_name)
    return Response(status_code=204)
