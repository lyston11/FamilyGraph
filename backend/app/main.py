"""FastAPI 入口：启动校验与路由挂载。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import config
from app.api.health import router as health_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # SECRET_KEY 缺失时在此抛错，uvicorn 拒绝完成启动（m0a design：配置校验）
    config.ensure_ready()
    yield


app = FastAPI(title="FamilyGraph API", lifespan=lifespan)
app.include_router(health_router, prefix="/api")
