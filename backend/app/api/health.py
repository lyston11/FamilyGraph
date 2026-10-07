"""健康检查端点。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services import writer_epoch

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """存活探针：公开端点，不经认证依赖管辖（architecture.md §1）。

    **不查数据库**：存活探针回答「进程还在吗」，加数据库依赖会让数据库抖动
    变成「进程已死」，从而触发不必要的重启。migration 状态见 `/ready`。
    """
    return {"status": "ok"}


@router.get("/ready")
def ready(db: Session = Depends(get_db)) -> dict[str, object]:
    """就绪探针：报告 writer 阶段与 epoch。

    与 `/health` 分开是刻意的：就绪探针**应该**反映依赖状态（数据库、迁移阶段），
    而存活探针不应该。合并两者会让「数据库不可用」触发进程重启，而重启不能修复
    数据库。

    只返回治理元数据（阶段、epoch、更新时间），不含业务数据、连接串或凭据。
    """
    return {"status": "ok", **writer_epoch.migration_health(db)}
