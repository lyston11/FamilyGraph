"""家庭认证域 Pydantic 模型。字段与前端 types/api.ts 一一对应（人工同步）。

09-04 起：家庭认证响应不再包含 system_admin / is_admin / platform_role 等
后台身份枚举；系统管理员会话投影见 schemas/admin_auth.py（仅 8002）。
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PIN_PATTERN = r"^\d{6}$"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    pin_must_change: bool
    principal_type: Literal["family_user"] = "family_user"
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


def public_user_payload(user: Any) -> dict[str, Any]:
    """从家庭 User 构造用户投影；权限判断仍在服务端依赖完成。

    不含任何后台身份字段（is_admin/platform_role 已移除，SF-F6）。
    """
    account = user.account
    return {
        "id": user.id,
        "name": user.name,
        "pin_must_change": bool(account.pin_must_change) if account else False,
        "principal_type": "family_user",
        "claim_status": account.status if account else "managed",
        "profile_status": user.profile_status,
    }
