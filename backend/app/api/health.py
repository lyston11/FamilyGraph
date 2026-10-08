"""健康检查端点。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services import capacity_bootstrap, rag_search_provider, writer_epoch

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

    只返回治理元数据（阶段、epoch、更新时间、容量就绪），不含业务数据、连接串或凭据。

    ## `pg_all` 阶段必须报告容量未就绪

    这是「迁移完成」与「配额生效」的绑定点：`pg_all` 下若计数行缺失，配额**完全没有
    生效**（`try_acquire` 返回 None = 不限制），而系统外观完全正常。因此就绪探针
    必须失败，使编排器/负载均衡把该实例摘除，而不是让它带着失效的配额继续服务。
    """
    health = writer_epoch.migration_health(db)
    payload: dict[str, object] = {"status": "ok", **health}

    # 必需扩展：PostgreSQL 上缺 PGroonga 会让中文词法检索静默退化为无索引扫描
    # （结果仍返回、只是慢且无相关度排序）。因此就绪探针必须失败，而不是让它
    # 带着退化的检索接流量。
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    extensions = rag_search_provider.extension_status(db, dialect)
    payload["extensions"] = extensions
    missing_ext = [name for name, present in extensions.items() if not present]
    if missing_ext:
        payload["status"] = "degraded"
        payload["extensions_missing"] = missing_ext
        raise HTTPException(status_code=503, detail=payload)

    try:
        capacity_bootstrap.assert_ready(db, stage=str(health["writer_stage"]))
        payload["capacity_ready"] = True
    except capacity_bootstrap.CapacityBootstrapIncomplete as exc:
        # 503：就绪探针的语义是「能否接流量」，容量未就绪时不能。
        payload["capacity_ready"] = False
        payload["capacity_error"] = str(exc)
        raise HTTPException(status_code=503, detail=payload) from exc
    return payload
