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
    is_admin: bool
    pin_must_change: bool
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


def public_user_payload(session: Session, user: Any) -> dict[str, Any]:
    """从 ORM User 构造对外用户视图（含 account.pin_must_change）。

    v2：is_admin 键保留以兼容前端，语义改为 platform_operator 角色派生；
    该角色不携带任何家庭数据读取权。
    """
    account = user.account
    return {
        "id": user.id,
        "name": user.name,
        "is_admin": is_platform_operator(session, account),
        "pin_must_change": bool(account.pin_must_change) if account else False,
        "claim_status": account.status if account else "managed",
        "profile_status": user.profile_status,
    }
