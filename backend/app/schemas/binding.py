"""绑定请求 API 域 Pydantic 模型（09-05 Chunk D；决策 16 并流绑定）。

target 侧视图只披露「这是我」判断所需最小字段：发起人显示名 + 建档人物名。
不含空间、关系、生卒等任何其他档案数据（确认前发起人零数据可见的镜像约束）。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.auth import PIN_PATTERN

BindingStatus = Literal["pending", "confirmed", "rejected", "cancelled"]


class BindingOut(BaseModel):
    """被绑定人视角的绑定请求投影（精确字段集，见 test_bindings 白名单断言）。"""

    id: int
    initiator_name: str | None = None
    person_name: str | None = None
    status: BindingStatus
    created_at: datetime
    resolved_at: datetime | None = None


class ConfirmBindingRequest(BaseModel):
    """确认载荷：PIN 复验（登录侧同一格式与校验惯例）。"""

    pin: str = Field(pattern=PIN_PATTERN)
