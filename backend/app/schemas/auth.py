"""认证与账号域 Pydantic 模型。字段与前端 types/api.ts 一一对应（人工同步）。"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.services.platform_roles import is_platform_operator

PIN_PATTERN = r"^\d{6}$"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_admin: bool = False
    pin_must_change: bool
    principal_type: Literal["family_user", "system_admin"] = "family_user"
    platform_role: Literal["platform_operator"] | None = None
    # v2 身份状态（F-1）：账号生命周期 managed→claimed 与档案确档 provisional→
    # identity_confirmed 是两条独立状态机，/me 直出供路由守卫判定（不再由前端
    # 从 fact-reviews 推断）。
    claim_status: Literal["managed", "claimed"]
    profile_status: Literal["provisional", "identity_confirmed"]


class LoginRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    pin: str = Field(pattern=PIN_PATTERN)


class ChallengeCandidate(BaseModel):
    id: int
    name: str
    # m1a 兼容补齐：候选档案的代管创建者名（managed 档案提示"由谁代管"）
    created_by_name: str | None = None


class ChallengeResponse(BaseModel):
    """同名同 PIN 409 响应体（architecture.md §2 AD-2 定义的专用结构）。"""

    challenge_id: str
    candidates: list[ChallengeCandidate]


class SelectCandidateRequest(BaseModel):
    challenge_id: str = Field(min_length=1, max_length=128)
    user_id: int = Field(gt=0)


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class LogoutResponse(BaseModel):
    success: bool


class ChangeNameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ChangePinRequest(BaseModel):
    old_pin: str = Field(pattern=PIN_PATTERN)
    new_pin: str = Field(pattern=PIN_PATTERN)


class BootstrapStatusResponse(BaseModel):
    initialized: bool


class InitializeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class InitializeResponse(BaseModel):
    user: UserOut
    # 明文 PIN 仅出现在本响应一次，之后服务端无任何明文痕迹（Q3 默认方案）
    one_time_pin: str


def public_system_admin_payload(admin: Any, account: Any) -> dict[str, Any]:
    """最小系统主体投影；不伪造 User/PersonProfile 字段。"""
    return {
        "id": admin.id,
        "name": admin.login_name,
        "is_admin": True,  # legacy display-only projection, never authorization
        "pin_must_change": bool(account.pin_must_change),
        "principal_type": "system_admin",
        "platform_role": "platform_operator",
        "claim_status": "claimed" if account.status == "claimed" else "managed",
        "profile_status": "identity_confirmed",
    }


def public_user_payload(session: Session, user: Any) -> dict[str, Any]:
    """从家庭 User 构造兼容用户投影；权限判断仍在服务端依赖完成。"""
    account = user.account
    return {
        "id": user.id,
        "name": user.name,
        "is_admin": is_platform_operator(session, account),
        "pin_must_change": bool(account.pin_must_change) if account else False,
        "principal_type": "family_user",
        "platform_role": "platform_operator" if is_platform_operator(session, account) else None,
        "claim_status": account.status if account else "managed",
        "profile_status": user.profile_status,
    }
