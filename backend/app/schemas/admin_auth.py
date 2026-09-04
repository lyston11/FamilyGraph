"""管理员认证域 Pydantic 模型（09-04 /admin-api 专用，与家庭 schema 完全隔离）。

AdminSessionOut 是管理员会话的字段白名单：只含 id/username/password_must_change/
status；绝不包含 password_hash、failed_attempts、locked_until、token/密钥或任何
家庭档案字段（design §3）。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.system_admin import SystemAdmin, SystemAdminAccount


class AdminSessionOut(BaseModel):
    id: int
    username: str
    password_must_change: bool
    status: Literal["managed", "claimed"]


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=128)


class AdminRefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class AdminTokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    admin: AdminSessionOut


class AdminPasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class AdminUsernameChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=100)


def admin_session_payload(admin: SystemAdmin, account: SystemAdminAccount) -> dict[str, Any]:
    """AdminSessionOut 投影：系统主体最小字段（白名单唯一来源）。"""
    return {
        "id": admin.id,
        "username": admin.username,
        "password_must_change": bool(account.password_must_change),
        "status": account.status,
    }
