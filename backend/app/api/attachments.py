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
from app.commands import attachment_content as content_commands
from app.commands import attachments as att_commands
from app.commands.context import ActorContext
from app.errors import (
    ATTACHMENT_NOT_FOUND,
    USER_NOT_FOUND,
    raise_api_error,
)
from app.models.account import Account
from app.models.attachment import Attachment
from app.models.attachment_content import AgentContentGrant, AttachmentContentVersion
from app.models.user import User
from app.schemas.attachment_content import (
    ContentVersionConfirmOut,
    ContentVersionCreate,
    ContentVersionOut,
    GrantCreate,
    GrantOut,
    GrantRevokeRequest,
)
from app.services import attachment_access, custody, family_projection
from app.services import attachments as att_service

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


def _readable_types(
    session: Session,
    viewer: User,
    account: Account,
    target: User,
    space_id: int | None,
) -> frozenset[str]:
    if space_id is not None:
        family_projection.authorized_space_or_404(session, account=account, space_id=space_id)
    return attachment_access.readable_attachment_types(
        session,
        viewer=viewer,
        account=account,
        target=target,
        space_id=space_id,
    )


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


@router.post(
    "/attachments/{attachment_id}/content-versions",
    status_code=201,
    response_model=ContentVersionOut,
)
def create_content_version(
    attachment_id: int,
    body: ContentVersionCreate,
    request: Request,
    session: OrmSession = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> ContentVersionOut:
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    row = content_commands.create_content_version(
        session,
        ctx,
        attachment_id=attachment_id,
        text=body.text,
        content_kind=body.content_kind,
    )
    session.refresh(row)
    return ContentVersionOut.model_validate(row)


@router.get(
    "/attachments/{attachment_id}/content-versions",
    response_model=list[ContentVersionOut],
)
def list_content_versions(
    attachment_id: int,
    session: OrmSession = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[ContentVersionOut]:
    actor, _account = identity
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise_api_error(404, ATTACHMENT_NOT_FOUND, "附件不存在")
    target = session.get(User, attachment.user_id)
    if target is None:
        raise_api_error(404, ATTACHMENT_NOT_FOUND, "附件不存在")
    custody.assert_can_edit(actor, target)
    rows = session.scalars(
        select(AttachmentContentVersion)
        .where(AttachmentContentVersion.attachment_id == attachment_id)
        .order_by(AttachmentContentVersion.revision.desc())
    ).all()
    return [ContentVersionOut.model_validate(row) for row in rows]


@router.post(
    "/attachment-content-versions/{version_id}/confirm",
    response_model=ContentVersionConfirmOut,
)
def confirm_content_version(
    version_id: int,
    request: Request,
    session: OrmSession = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> ContentVersionConfirmOut:
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    row = content_commands.confirm_content_version(session, ctx, version_id=version_id)
    session.refresh(row)
    return ContentVersionConfirmOut.model_validate(row)


@router.post(
    "/attachment-content-versions/{version_id}/grants",
    status_code=201,
    response_model=GrantOut,
)
def create_content_grant(
    version_id: int,
    body: GrantCreate,
    request: Request,
    session: OrmSession = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> GrantOut:
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    row = content_commands.create_grant(
        session,
        ctx,
        version_id=version_id,
        space_id=body.space_id,
        agent_kind=body.agent_kind,
        purpose=body.purpose,
        grant_scope=body.grant_scope,
    )
    session.refresh(row)
    return GrantOut.model_validate(row)


@router.post("/attachment-grants/{grant_id}/revoke", response_model=GrantOut)
def revoke_content_grant(
    grant_id: int,
    body: GrantRevokeRequest,
    request: Request,
    session: OrmSession = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> GrantOut:
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    row = content_commands.revoke_grant(session, ctx, grant_id=grant_id, reason=body.reason)
    session.refresh(row)
    return GrantOut.model_validate(row)


@router.get(
    "/attachments/{attachment_id}/grants",
    response_model=list[GrantOut],
)
def list_content_grants(
    attachment_id: int,
    session: OrmSession = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[GrantOut]:
    actor, _account = identity
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise_api_error(404, ATTACHMENT_NOT_FOUND, "附件不存在")
    target = session.get(User, attachment.user_id)
    if target is None:
        raise_api_error(404, ATTACHMENT_NOT_FOUND, "附件不存在")
    custody.assert_can_edit(actor, target)
    rows = session.scalars(
        select(AgentContentGrant)
        .join(
            AttachmentContentVersion,
            AgentContentGrant.content_version_id == AttachmentContentVersion.id,
        )
        .where(AttachmentContentVersion.attachment_id == attachment_id)
        .order_by(AgentContentGrant.id.desc())
    ).all()
    return [GrantOut.model_validate(row) for row in rows]


@router.get("/users/{user_id}/attachments")
def list_attachments(
    user_id: int,
    space_id: int | None = None,
    session: OrmSession = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[dict[str, Any]]:
    """按当前空间与类别返回可读附件元数据。"""
    actor, account = identity
    target = session.get(User, user_id)
    if target is None:
        raise_api_error(404, USER_NOT_FOUND, "档案不存在")
    allowed = _readable_types(session, actor, account, target, space_id)
    if not allowed:
        raise_api_error(404, USER_NOT_FOUND, "档案不存在")

    rows = session.scalars(
        select(Attachment).where(Attachment.user_id == user_id, Attachment.type.in_(allowed))
    ).all()
    return [_attachment_out(r) for r in rows]


@router.get("/attachments/{attachment_id}/raw")
def download_attachment(
    attachment_id: int,
    space_id: int | None = None,
    session: OrmSession = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> FileResponse:
    """原图独立复核同一空间的 photos 权限，禁止其它类型走文件路径。"""
    actor, account = identity
    row = session.get(Attachment, attachment_id)
    if row is None or row.type != "image":
        raise_api_error(404, ATTACHMENT_NOT_FOUND, "附件不存在")
    target = session.get(User, row.user_id)
    if target is None:
        raise_api_error(404, USER_NOT_FOUND, "档案不存在")

    if "image" not in _readable_types(session, actor, account, target, space_id):
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
            "Cache-Control": "private, no-store",
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
