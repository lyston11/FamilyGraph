"""成员档案域 Pydantic 模型（m1a）。字段与前端 types/api.ts 一一对应（人工同步）。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.user import BASIC_DISCLOSURE_KEYS
from app.schemas.relation import DirClass
from app.services.custody import RelationAccess, resolve_relation
from app.services.disclosure import basic_disclosure_flags

GenderType = Literal["m", "f", "unknown"]
CalType = Literal["solar", "lunar", "none"]
PrivacyMode = Literal["perpetual", "handover"]
ClaimStatus = Literal["managed", "claimed"]


# 遮罩哨兵 {__masked__: true} 由 visibility.MASKED 常量给出；MemberOut 对应
# 字段以 Any 透传（Pydantic 下划线字段不参与序列化，故不用模型承载）。


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
    """披露开关载荷（v2 §0.1）。

    - 基础五类必填（整体替换语义，缺键/多键均 422）；
    - space_id 选填：提供时写入该空间的逐空间覆盖行，且仅档案本人可调
      （commands.members.update_disclosure 强制 self）；
    - 高敏感类别类型恒为 Literal[False] | None：传 true 直接产生 literal_error
      → 422 —— 键存在是为了让未来任务无法静默放宽合同，false 为不可变更默认。
    """

    model_config = ConfigDict(extra="forbid")

    avatar: bool
    photos: bool
    dates: bool
    bio: bool
    attachments: bool
    space_id: int | None = Field(default=None, gt=0)
    # 高敏感类别：合同上只能为 false（Literal[False] 使 true 在校验层即被拒）
    health: Literal[False] | None = None
    address: Literal[False] | None = None
    school: Literal[False] | None = None
    contact: Literal[False] | None = None
    private_notes: Literal[False] | None = None


class SpaceMembershipInline(BaseModel):
    """建档时可同时加入某个家庭空间（AD-4 新建 managed 直连例外）。"""

    space_id: int = Field(gt=0)


class MemberCreateRequest(BaseModel):
    """建档请求（F-1：名字 + 与创建者的关系必填）。

    AD-4 新建账号例外：新建 managed 新档时 relation 直接 active（创建者即代管人）；
    只有目标是已存在/已 claimed 账号才走 pending 合并请求流（POST /connection-requests）。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    gender: GenderType = "unknown"
    birth: StructuredDate | None = None
    death: StructuredDate | None = None
    bio: str | None = Field(default=None, max_length=2000)
    privacy_mode: PrivacyMode = "handover"
    space_membership: SpaceMembershipInline | None = None
    # 与创建者的关系（必填）：dir_class + 可选称谓 + 可选关系原文（append-only ≤200 字）
    relation_dir_class: DirClass
    relation_label: str | None = Field(default=None, max_length=64)
    relation_text: str | None = Field(default=None, max_length=200)


class MemberUpdateRequest(BaseModel):
    """PATCH 档案：全部可选，仅应用显式提供的字段。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    gender: GenderType | None = None
    birth: StructuredDate | None = None
    death: StructuredDate | None = None
    bio: str | None = Field(default=None, max_length=2000)


class SpaceDisclosureOut(BaseModel):
    """单一空间的逐空间披露覆盖视图（缺省类别为 False）。"""

    space_id: int
    allowed: dict[str, bool]


class DisclosureMatrixOut(BaseModel):
    """GET /users/{id}/disclosure 合并矩阵：全局偏好 + 逐空间覆盖。

    键名 "global" 为 Python 关键字，字段用 global_flags + 别名双向映射。
    """

    model_config = ConfigDict(populate_by_name=True)

    global_flags: dict[str, bool] = Field(validation_alias="global", serialization_alias="global")
    spaces: list[SpaceDisclosureOut]


class MemberPermissions(BaseModel):
    """当前 actor 对该档案的可用操作（resolve_relation 投影；view 非 full 不出列表/详情）。"""

    edit: bool
    delete: bool


class MemberOut(BaseModel):
    id: int
    name: str
    is_admin: bool = False
    # summary 级可见性下可为 {__masked__: true} 哨兵（Any 直传保证形状）
    gender: Any = "unknown"
    birth: Any = None
    death: Any = None
    bio: Any = None
    avatar_path: Any = None
    privacy_mode: PrivacyMode
    claim_status: ClaimStatus
    created_by: int | None = None
    created_at: datetime
    clan_disclosure: dict[str, bool]
    permissions: MemberPermissions


class MemberCreateResponse(BaseModel):
    """建档响应：PIN 明文仅此一次（replayed=True 时 pin 为 null，不回放初始 PIN）。"""

    user: MemberOut
    pin: str | None = Field(default=None, pattern=r"^\d{6}$")
    replayed: bool = False


def structured_date_payload(value: Any) -> dict[str, Any] | None:
    """JSON 列原样透传（写入前已经 Pydantic 校验）；空值归一为 None。"""
    if value is None:
        return None
    return dict(value)


def member_payload(session: Any, target: Any, actor: Any) -> dict[str, Any]:
    """构造对外成员视图：字段经 visibility 投影（F2/F3），再附披露开关与权限。

    与 GET /users/{id} 的字段级投影同源：provisional/minor/masked 字段一律以
    MASKED 哨兵返回，不允许任何列表/写回出口绕过字段投影读原始列。
    """
    from app.services import visibility
    from app.services.platform_roles import is_platform_operator

    access: RelationAccess = resolve_relation(actor, target)
    payload = visibility.user_payload_for(
        session, actor, target, purpose=visibility.PURPOSE_PROFILE
    )
    if payload is None:  # 防御：调用方应在入口已过滤（不可见 = 404）
        payload = visibility.payload_from_decision(
            visibility.evaluate(session, actor, target, purpose=visibility.PURPOSE_PROFILE),
            target,
        )
    payload["is_admin"] = is_platform_operator(session, target.account)
    payload["clan_disclosure"] = {
        key: bool(value) for key, value in basic_disclosure_flags(session, target).items()
    }
    payload["permissions"] = {"edit": access.edit, "delete": access.delete}
    return payload


__all__ = [
    "BASIC_DISCLOSURE_KEYS",
    "DisclosureMatrixOut",
    "DisclosurePayload",
    "MemberCreateRequest",
    "MemberCreateResponse",
    "MemberOut",
    "MemberPermissions",
    "MemberUpdateRequest",
    "SpaceDisclosureOut",
    "StructuredDate",
    "member_payload",
]
