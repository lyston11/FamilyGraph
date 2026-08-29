"""FastAPI 入口：启动校验、全局依赖、统一错误结构、路由挂载。"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import NoReturn

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app import config, logctx
from app.api.action_cards import router as action_cards_router
from app.api.admin import router as admin_router
from app.api.admin_agent import router as admin_agent_router
from app.api.agent import router as agent_router
from app.api.attachments import router as attachments_router
from app.api.auth import router as auth_router
from app.api.bootstrap import router as bootstrap_router
from app.api.connections import router as connections_router
from app.api.controlled_web import admin_router as controlled_web_admin_router
from app.api.controlled_web import router as controlled_web_router
from app.api.deps import close_request_db, require_pin_changed
from app.api.governance import router as governance_router
from app.api.graph import router as graph_router
from app.api.health import router as health_router
from app.api.internal_agent import router as internal_agent_router
from app.api.kinship import router as kinship_router
from app.api.memory import router as memory_router
from app.api.misc import router as misc_router
from app.api.spaces import router as spaces_router
from app.api.users import members_router
from app.api.users import router as users_router
from app.errors import INTERNAL_ERROR, VALIDATION_ERROR, extract_api_error, raise_api_error

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # SECRET_KEY 缺失时在此抛错，uvicorn 拒绝完成启动（m0a design：配置校验）
    config.ensure_ready()
    if config.AUTH_LOCKOUT_DISABLED:
        # 生产禁用锁定需二次确认：留 WARNING 日志线索（design.md 回滚形态）
        logger.warning("AUTH_LOCKOUT_DISABLED=true：登录锁定已关闭，仅允许开发态使用")
    logctx.setup_logging()
    # 后台维护循环（agent reaper / steward canonical job 泵）：
    # serve.py 双 listener 共享 lifespan，start 内部进程级单例防重复启动。
    from app.services import maintenance

    maintenance.start_maintenance_loop()
    try:
        yield
    finally:
        await maintenance.stop_maintenance_loop()


def _error_envelope(code: str, message: str, detail: object = None) -> dict[str, object]:
    error: dict[str, object] = {"code": code, "message": message}
    if detail is not None:
        error["detail"] = detail
    return {"error": error}


app = FastAPI(
    title="FamilyGraph API",
    lifespan=lifespan,
    # 首登强制改 PIN 全局门禁（architecture.md §1；白名单见 deps.PIN_GATE_WHITELIST）
    dependencies=[Depends(require_pin_changed)],
)


@app.middleware("http")
async def request_context_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """注入 request_id 贯穿日志；请求结束释放 DB 会话。"""
    rid = logctx.new_request_id()
    logctx.request_id_var.set(rid)
    try:
        response = await call_next(request)
    finally:
        close_request_db(request)
    response.headers["X-Request-ID"] = rid
    return response


# ---- 统一错误响应外壳（spec/backend/error-handling.md）----


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=_error_envelope(INTERNAL_ERROR, "服务器内部错误"),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=_error_envelope(VALIDATION_ERROR, "请求参数不合法", detail=exc.errors()),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """业务错误（raise_api_error）与框架层 HTTPException 统一展开为 error 外壳。"""
    api_error = extract_api_error(exc.detail)
    if api_error is None:
        code = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND"}.get(
            exc.status_code, "HTTP_ERROR"
        )
        message = str(exc.detail) if exc.detail else "请求失败"
        body = _error_envelope(code, message)
    else:
        body = _error_envelope(
            str(api_error.get("code")),
            str(api_error.get("message")),
            detail=api_error.get("detail"),
        )
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


# ---- 路由挂载 ----
app.include_router(health_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(members_router, prefix="/api")
app.include_router(bootstrap_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(connections_router, prefix="/api")
app.include_router(graph_router, prefix="/api")
app.include_router(misc_router, prefix="/api")
app.include_router(attachments_router, prefix="/api")
app.include_router(spaces_router, prefix="/api")
app.include_router(governance_router, prefix="/api")
# 浏览器 Agent API（JWT；feature flag 关闭一律 503，RT-6）
app.include_router(agent_router, prefix="/api")
# V2.3 关系智能（TermRegistry/resolve；RELATIONSHIP_INTELLIGENCE_ENABLED 默认关，503）
app.include_router(kinship_router, prefix="/api")
# V2.4 ActionCard 生命周期（浏览器面；STEWARD_ENABLED 默认关，503）
app.include_router(action_cards_router, prefix="/api")
# V2.5 Memory cards and scope-filtered RAG
app.include_router(memory_router, prefix="/api")
# V2.6 Controlled Web（平台与空间双重 opt-in；默认关闭）
app.include_router(controlled_web_router, prefix="/api")
app.include_router(controlled_web_admin_router, prefix="/api")
# Agent Provider 治理：platform_operator 专属（同样受 feature flag 门禁）
app.include_router(admin_agent_router, prefix="/api/admin/agent")
# Internal Agent 协议：仅内部网络可达（sidecar → FastAPI），不走 /api 前缀，
# nginx 不代理该前缀；feature flag 关闭时端点一律 503（RT-6）。
# P1 网络隔离裁定：internal 协议不再挂载到公开 listener，由下方 internal_app
# 在独立端口（compose backend 网络，不发布宿主端口）单独 serve；公开 app 对
# /internal/* 一律显式 404 fail-closed。启动入口见 app.serve。


@app.api_route(
    "/internal/{rest:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    include_in_schema=False,
    response_model=None,
)
async def internal_route_rejected(rest: str) -> NoReturn:
    """公开 listener 上不存在 internal 协议：不泄露路由存在性。"""
    raise_api_error(404, "NOT_FOUND", "资源不存在")


# ---- internal app：独立 listener，与公开 app 共享中间件/错误外壳合同 ----
internal_app = FastAPI(title="FamilyGraph Internal Agent API", lifespan=lifespan)
internal_app.middleware("http")(request_context_middleware)
internal_app.add_exception_handler(Exception, unhandled_exception_handler)
internal_app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
internal_app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
internal_app.include_router(internal_agent_router, prefix="/internal/agent")
