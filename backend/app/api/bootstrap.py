"""首启引导路由：GET /bootstrap/status（家庭面最小合同）。

09-04 起：系统管理员不再经网页初始化（POST /bootstrap/initialize 已移除），
改由部署启动 preflight 自动创建唯一 admin 账号并交付 0600 凭据文件
（services/admin_bootstrap，SF-F3）。status 只反映家庭用户是否存在，
不暴露任何系统主体信息。
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.user import User
from app.schemas.auth import BootstrapStatusResponse

router = APIRouter(prefix="/bootstrap", tags=["bootstrap"])


@router.get("/status", response_model=BootstrapStatusResponse)
def status(session: Session = Depends(get_db)) -> BootstrapStatusResponse:
    """公开端点：只统计家庭 User，不探测系统管理员存在性。"""
    user_count = session.query(func.count(User.id)).scalar()
    return BootstrapStatusResponse(initialized=bool(user_count and user_count > 0))
