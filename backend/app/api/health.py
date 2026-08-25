"""健康检查端点。"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """存活探针：公开端点，不经认证依赖管辖（architecture.md §1）。"""
    return {"status": "ok"}
