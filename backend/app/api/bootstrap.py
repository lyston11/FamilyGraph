"""首启引导路由：GET /bootstrap/status、POST /bootstrap/initialize。

无任何用户时允许一次性创建管理员；凭据仅在本次响应返回，不可回看
（锁定决策 A4 + 待定 Q3 默认方案）。
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.auth import (
    BootstrapStatusResponse,
    InitializeRequest,
    InitializeResponse,
    UserOut,
    public_user_payload,
)
from app.services import bootstrap as bootstrap_service

router = APIRouter(prefix="/bootstrap", tags=["bootstrap"])


@router.get("/status", response_model=BootstrapStatusResponse)
def status(session: Session = Depends(get_db)) -> BootstrapStatusResponse:
    """公开端点：前端据此决定进入登录页还是首启引导页。"""
    return BootstrapStatusResponse(initialized=bootstrap_service.has_any_user(session))


@router.post("/initialize", response_model=InitializeResponse)
def initialize(
    payload: InitializeRequest, request: Request, session: Session = Depends(get_db)
) -> InitializeResponse:
    """一次性创建管理员；随机 PIN 仅本次响应可见。"""
    user, pin = bootstrap_service.initialize_admin(
        session,
        payload.name,
        ip=request.client.host if request.client else None,
    )
    session.commit()
    return InitializeResponse(
        user=UserOut(**public_user_payload(user)),
        one_time_pin=pin,
    )
