"""成员档案域 Pydantic 模型（m1a）。字段与前端 types/api.ts 一一对应（人工同步）。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.user import DISCLOSURE_KEYS
from app.services.custody import RelationAccess, resolve_relation

GenderType = Literal["m", "f", "unknown"]
CalType = Literal["solar", "lunar", "none"]
PrivacyMode = Literal["perpetual", "handover"]
ClaimStatus = Literal["managed", "claimed"]


class StructuredDate(BaseModel):
    """生卒结构化值（D7）：cal_type + YYYY-MM-DD + 原文备注。"""

    cal_type: CalType = "none"
    date: str | None = None
    original_text: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _validate_consistency(self) -> StructuredDate:
        if self.cal_type == "none":
            if self.date is not None:
                raise ValueError("cal_type 为 none 时不得携带 date")
            return self
        if not self.date:
            raise ValueError("cal_type 为 solar/lunar 时 date 必填")
        try:
            date.fromisoformat(self.date)
        except ValueError:
            raise ValueError("date 必须是合法的 YYYY-MM-DD 日期") from None
        return self


class DisclosurePayload(BaseModel):
    """AD-9 披露开关：五类布尔，键集合必须恰好（缺键/多键均 422）。"""

    model_config = ConfigDict(extra="forbid")

    avatar: bool
    photos: bool
    dates: bool
    bio: bool
    attachments: bool


class MemberCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    gender: GenderType = "unknown"
    birth: StructuredDate | None = None
    death: StructuredDate | None = None
    bio: str | None = Field(default=None, max_length=2000)
    privacy_mode: PrivacyMode = "handover"


class MemberUpdateRequest(BaseModel):
    """PATCH 档案：全部可选，仅应用显式提供的字段。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    gender: GenderType | None = None
    birth: StructuredDate | None = None
    death: StructuredDate | None = None
    bio: str | None = Field(default=None, max_length=2000)


class MemberPermissions(BaseModel):
    """当前 actor 对该档案的可用操作（resolve_relation 投影；view 非 full 不出列表/详情）。"""

    edit: bool
    delete: bool


class MemberOut(BaseModel):
    id: int
    name: str
    is_admin: bool = False
    gender: GenderType
    birth: dict[str, Any] | None = None
    death: dict[str, Any] | None = None
    bio: str | None = None
    avatar_path: str | None = None
    privacy_mode: PrivacyMode
    claim_status: ClaimStatus
    created_by: int | None = None
    created_at: datetime
    clan_disclosure: dict[str, bool]
    permissions: MemberPermissions


class MemberCreateResponse(BaseModel):
    """建档响应：PIN 明文仅此一次，此后任何接口不可再取（A3/AD-1）。"""

    user: MemberOut
    pin: str = Field(pattern=r"^\d{6}$")


def structured_date_payload(value: Any) -> dict[str, Any] | None:
    """JSON 列原样透传（写入前已经 Pydantic 校验）；空值归一为 None。"""
    if value is None:
        return None
    return dict(value)


def member_payload(target: Any, actor: Any) -> dict[str, Any]:
    """构造对外成员视图：档案字段 + AD-9 披露开关 + 当前主体权限投影。

    M1 仅在 resolve_relation.view == full 时调用（none 已被路由 404 拦截）。
    """
    access: RelationAccess = resolve_relation(actor, target)
    return {
        "id": target.id,
        "name": target.name,
        "is_admin": bool(target.is_admin),
        "gender": target.gender,
        "birth": structured_date_payload(target.birth),
        "death": structured_date_payload(target.death),
        "bio": target.bio,
        "avatar_path": target.avatar_path,
        "privacy_mode": target.privacy_mode,
        "claim_status": target.claim_status,
        "created_by": target.created_by,
        "created_at": target.created_at,
        "clan_disclosure": {key: bool(value) for key, value in target.clan_disclosure.items()},
        "permissions": {"edit": access.edit, "delete": access.delete},
    }


__all__ = [
    "DISCLOSURE_KEYS",
    "DisclosurePayload",
    "MemberCreateRequest",
    "MemberCreateResponse",
    "MemberOut",
    "MemberPermissions",
    "MemberUpdateRequest",
    "StructuredDate",
    "member_payload",
]
