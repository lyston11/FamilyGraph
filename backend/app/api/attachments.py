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
from app.commands import attachments as att_commands
from app.commands.context import ActorContext
from app.errors import ATTACHMENT_NOT_FOUND, USER_NOT_FOUND, raise_api_error
from app.models.account import Account
from app.models.attachment import Attachment
from app.models.user import User
from app.services import attachments as att_service
from app.services.disclosure import disclosed_categories
from app.services.visibility import (
    LEVEL_HOUSEHOLD_DETAIL,
    LEVEL_LINEAGE_SUMMARY,
    LEVEL_SELF_PRIVATE,
    evaluate,
)

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
    """目标可见性门禁：none → 404（防枚举）。custodian 由 evaluate 内部覆盖。"""
    target = session.get(User, owner_id)
    if target is None:
        raise_api_error(404, USER_NOT_FOUND, "档案不存在")
    decision = evaluate(session, viewer, target)
    return decision.level, target


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

    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    file.file.seek(0)
    data = file.file.read()
    row = att_commands.add_image_attachment(
        session,
        ctx,
        user_id=user_id,
        filename=file.filename or "",
        data=data,
        title=title,
    )
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
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    row = att_commands.add_link_attachment(
        session,
        ctx,
        user_id=user_id,
        url=payload.url,
        title=payload.title,
        description=payload.description,
    )
    session.refresh(row)
    return _attachment_out(row)


@router.get("/users/{user_id}/attachments")
def list_attachments(
    user_id: int,
    session: OrmSession = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[dict[str, Any]]:
    """可见性：household_detail 及以上 → 元数据；lineage_summary 需归属者开放
    attachments 披露；否则 404 语义空。"""
    actor, _account = identity
    target = session.get(User, user_id)
    if target is None:
        raise_api_error(404, USER_NOT_FOUND, "档案不存在")
    decision = evaluate(session, actor, target)
    allowed = decision.level in (LEVEL_HOUSEHOLD_DETAIL, LEVEL_SELF_PRIVATE)
    if not allowed and decision.level == LEVEL_LINEAGE_SUMMARY:
        allowed = "attachments" in disclosed_categories(session, target)
    if not allowed:
        raise_api_error(404, USER_NOT_FOUND, "档案不存在")

    rows = session.scalars(select(Attachment).where(Attachment.user_id == user_id)).all()
    return [_attachment_out(r) for r in rows]


@router.get("/attachments/{attachment_id}/raw")
def download_attachment(
    attachment_id: int,
    session: OrmSession = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> FileResponse:
    """授权流式下载：household_detail/self 可下；lineage_summary 仅当归属者开放
    attachments 披露。"""
    actor, _account = identity
    row = session.get(Attachment, attachment_id)
    if row is None:
        raise_api_error(404, ATTACHMENT_NOT_FOUND, "附件不存在")
    target = session.get(User, row.user_id)
    if target is None:
        raise_api_error(404, USER_NOT_FOUND, "档案不存在")

    decision = evaluate(session, actor, target)
    allowed = decision.level in (LEVEL_HOUSEHOLD_DETAIL, LEVEL_SELF_PRIVATE)
    if not allowed and decision.level == LEVEL_LINEAGE_SUMMARY:
        allowed = "attachments" in disclosed_categories(session, target)
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
    """删除（命令：commands.attachments.delete_attachment）：先事务删记录 + 失效事件，
    后异步删物理文件。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    path_name = att_commands.delete_attachment(session, ctx, attachment_id)

    if path_name:
        deleted = att_service.delete_file_quiet(path_name)
        if not deleted:
            import logging

            logging.getLogger(__name__).warning("orphan file cleanup deferred: %s", path_name)
    return Response(status_code=204)
