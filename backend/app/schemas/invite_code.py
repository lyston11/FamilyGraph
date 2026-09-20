"""邀请码 API 域 Pydantic 模型（09-05 Chunk C；字段与前端 api/inviteCodes.ts 对应）。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

InviteCodeKind = Literal["household", "lineage", "stranger"]


class InviteCodeOut(BaseModel):
    """我的码投影：创建者可见明文码（分享渲染为 …/register?code=XXX，决策 12/14）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    kind: InviteCodeKind
    space_id: int | None = None
    space_name: str | None = None
    max_uses: int | None = None
    used_count: int
    expires_at: datetime
    revoked_at: datetime | None = None
    created_at: datetime


class CreateInviteCodeRequest(BaseModel):
    """建码请求：household/lineage 必填 space_id 且 max_uses 恒为 1（原语层校验）。"""

    kind: InviteCodeKind
    space_id: int | None = Field(default=None, gt=0)
    # stranger 可设使用上限；NULL=不限次；一次性码类型不接受该参数
    max_uses: int | None = Field(default=None, ge=1)
    # 有效期天数：默认 7（决策 11），创建时可选
    ttl_days: int | None = Field(default=None, ge=1, le=365)


class RedeemInviteCodeRequest(BaseModel):
    """设置页填码（家庭/家族码；陌生人码在登录态兑换被 400 明确拒绝）。"""

    code: str = Field(min_length=8, max_length=12)
    # 与码创建者的关系词（自由文本，必填，≤64）
    relation_label: str = Field(min_length=1, max_length=64)
