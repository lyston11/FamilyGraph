"""V2 Foundation 治理路由：owner 邀请兑换 / owner 移交 / 确档清单 / 数据权利。

全部为薄路由（AC-F7）：schema 解析 + 认证上下文构造 → 调用应用命令 → 序列化；
授权、FSM、写入、事件与审计均在命令层同一短事务内完成。
operator 专属端点见 api/admin.py；本文件只承载普通认证主体端点。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.commands import data_rights as data_right_commands
from app.commands import identity as identity_commands
from app.commands import owner_onboarding as onboarding_commands
from app.commands import ownership as ownership_commands
from app.commands.context import ActorContext
from app.errors import SPACE_NOT_FOUND, raise_api_error
from app.models.account import Account
from app.models.user import User
from app.models.v2_foundation import ClaimDispute
from app.schemas.space import SpaceOut
from app.schemas.v2_foundation import (
    ClaimDisputeCreate,
    ClaimDisputeOut,
    CorrectRequestPayload,
    DataRightRequestOut,
    FactReviewDecision,
    FactReviewOut,
    IdentityConfirmResult,
    RedeemPayload,
    TransferCreate,
    TransferOut,
)
from app.services import space_fsm

router = APIRouter(tags=["v2-governance"])


def _ctx(request: Request, identity: tuple[User, Account]) -> ActorContext:
    actor, account = identity
    ip = request.client.host if request.client else None
    return ActorContext.from_identity(actor, account, ip=ip)


# ---- 「这是我」合并确认与确档清单（F-1）----


@router.post("/me/identity/confirm", response_model=IdentityConfirmResult)
def confirm_identity(
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> IdentityConfirmResult:
    """本人确认「这是我」：Account managed→claimed 与本人 Profile 确认合并转换
    （PRD F-1 唯一合法联动，其余路径两条状态机独立）。首登门禁白名单内，
    可先于改 PIN 调用。"""
    result = identity_commands.claim_and_confirm_own_identity(session, _ctx(request, identity))
    return IdentityConfirmResult(**result)


@router.get("/me/fact-reviews", response_model=list[FactReviewOut])
def list_fact_reviews(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[FactReviewOut]:
    """本人的确档清单（proposed + 已决议历史）。"""
    rows = identity_commands.list_own_fact_reviews(
        session, ActorContext.from_identity(identity[0], identity[1])
    )
    return [FactReviewOut.model_validate(r) for r in rows]


@router.post("/me/fact-reviews/{review_id}/decide", response_model=FactReviewOut)
def decide_fact_review(
    review_id: int,
    payload: FactReviewDecision,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> FactReviewOut:
    """清单项决议：confirmed | disputed（终态）；仅档案本人。"""
    row = identity_commands.decide_fact_review(
        session,
        _ctx(request, identity),
        review_id,
        decision=payload.decision,
        note=payload.note,
    )
    return FactReviewOut.model_validate(row)


# ---- owner 移交（AC-F5）----


@router.post("/spaces/{space_id}/ownership-transfers", status_code=201, response_model=TransferOut)
def create_transfer(
    space_id: int,
    payload: TransferCreate,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> TransferOut:
    transfer = ownership_commands.create_transfer(
        session, _ctx(request, identity), space_id=space_id, to_user_id=payload.to_user_id
    )
    return TransferOut.model_validate(transfer)


@router.get("/spaces/{space_id}/ownership-transfers", response_model=list[TransferOut])
def list_transfers(
    space_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[TransferOut]:
    actor, _account = identity
    member = space_fsm.find_membership(session, space_id, actor.id)
    if member is None or space_fsm.effective_status(member) != "active":
        raise_api_error(404, SPACE_NOT_FOUND, "家庭空间不存在")
    rows = ownership_commands.list_transfers_for_space(session, space_id)
    return [TransferOut.model_validate(r) for r in rows]


@router.post("/ownership-transfers/{transfer_id}/accept", response_model=TransferOut)
def accept_transfer(
    transfer_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> TransferOut:
    row = ownership_commands.accept_transfer(session, _ctx(request, identity), transfer_id)
    return TransferOut.model_validate(row)


@router.post("/ownership-transfers/{transfer_id}/cancel", response_model=TransferOut)
def cancel_transfer(
    transfer_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> TransferOut:
    row = ownership_commands.cancel_transfer(
        session, ActorContext.from_identity(identity[0], identity[1]), transfer_id
    )
    return TransferOut.model_validate(row)


# ---- owner onboarding 兑换（AC-F3；operator 签发/撤销在 admin 路由）----


@router.post("/owner-invitations/redeem", status_code=201, response_model=SpaceOut)
def redeem_invitation(
    payload: RedeemPayload,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceOut:
    """兑换 owner 邀请：原子消费 token → 创建独立 LineageSpace 并授予 owner。

    兑换者只获得这一个新空间：不授予 platform_operator，也不连接其他空间。
    managed/pin 未改账号 403 引导先认领。"""
    space = onboarding_commands.redeem_owner_invitation(
        session, _ctx(request, identity), raw_token=payload.token
    )
    out = SpaceOut.model_validate(space)
    out.member_count = 1
    return out


# ---- 数据权利（AC-F6）----


@router.post("/data-rights/export", status_code=201, response_model=DataRightRequestOut)
def request_export(
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> DataRightRequestOut:
    """申请结构化导出：异步生成（BackgroundTasks 独立会话），产物继承可见性策略并过期。"""
    row = data_right_commands.create_data_right_request(
        session, _ctx(request, identity), request_type="export"
    )
    background_tasks.add_task(data_right_commands.process_export_request, row.id)
    return DataRightRequestOut.model_validate(row)


@router.post("/data-rights/correct", status_code=201, response_model=DataRightRequestOut)
def request_correction(
    payload: CorrectRequestPayload,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> DataRightRequestOut:
    """申请资料更正：fields 为目标字段白名箱子集；operator 决议走 break-glass 审计。"""
    row = data_right_commands.create_data_right_request(
        session, _ctx(request, identity), request_type="correct", payload={"fields": payload.fields}
    )
    return DataRightRequestOut.model_validate(row)


@router.post("/data-rights/delete", status_code=201, response_model=DataRightRequestOut)
def request_deletion(
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> DataRightRequestOut:
    """申请删除/注销：创建即发布 profile.delete.requested 冻结事件（Agent/RAG 合同）。"""
    row = data_right_commands.create_data_right_request(
        session, _ctx(request, identity), request_type="delete"
    )
    return DataRightRequestOut.model_validate(row)


@router.get("/data-rights", response_model=list[DataRightRequestOut])
def list_my_data_rights(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[DataRightRequestOut]:
    rows = data_right_commands.list_own_requests(
        session, ActorContext.from_identity(identity[0], identity[1])
    )
    return [DataRightRequestOut.model_validate(r) for r in rows]


class ExecuteDeleteBody(BaseModel):
    confirm_name: str = Field(min_length=1, max_length=100)


@router.post("/data-rights/{request_id}/execute-delete")
def execute_delete(
    request_id: int,
    body: ExecuteDeleteBody,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> dict[str, Any]:
    """执行删除类请求：tombstone 失效事件随事务发布；物理文件提交后清理。"""
    ctx = _ctx(request, identity)
    _row, purge = data_right_commands.execute_delete_request(
        session, ctx, request_id, confirm_name=body.confirm_name
    )

    def _purge_files(paths: list[str]) -> None:
        from pathlib import Path

        from app.config import UPLOADS_DIR

        for name in paths:
            path = UPLOADS_DIR / Path(name).name
            if path.exists():
                path.unlink()

    background_tasks.add_task(_purge_files, purge)
    return {"status": "completed"}


@router.get("/data-rights/{request_id}/export-file")
def download_export(
    request_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> Response:
    """下载导出：命令层完成归属/未过期/一次性消费校验并解密密文后以明文流返回。"""
    _row, plaintext = data_right_commands.open_export_file(
        session, _ctx(request, identity), request_id
    )
    return Response(
        content=plaintext,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="data_export_{request_id}.json"',
            "X-Content-Type-Options": "nosniff",
        },
    )


# ---- 认领争议 ----


@router.post("/claim-disputes", status_code=201, response_model=ClaimDisputeOut)
def raise_dispute(
    payload: ClaimDisputeCreate,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> ClaimDisputeOut:
    """发起认领争议：evidence 原文保留；决议走 operator break-glass 接口。"""
    dispute = identity_commands.raise_claim_dispute(
        session, _ctx(request, identity), profile_id=payload.profile_id, evidence=payload.evidence
    )
    return ClaimDisputeOut.model_validate(dispute)


@router.get("/me/claim-disputes", response_model=list[ClaimDisputeOut])
def list_my_disputes(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[ClaimDisputeOut]:
    """我发起的争议列表（发起人视角；operator 全量列表见 admin 路由）。"""
    rows = (
        session.scalars(
            select(ClaimDispute)
            .where(ClaimDispute.raised_by_account_id == identity[1].id)
            .order_by(ClaimDispute.id.desc())
        )
        .unique()
        .all()
    )
    return [ClaimDisputeOut.model_validate(r) for r in rows]


@router.post("/claim-disputes/{dispute_id}/withdraw", response_model=ClaimDisputeOut)
def withdraw_dispute(
    dispute_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> ClaimDisputeOut:
    row = identity_commands.withdraw_claim_dispute(session, _ctx(request, identity), dispute_id)
    return ClaimDisputeOut.model_validate(row)
