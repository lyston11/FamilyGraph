"""双 listener 启动入口：公开 API 与 internal agent 协议分端口 serve。

P1 网络隔离裁定（08-29 任务）：internal 协议（/internal/agent/*）不得暴露在
公开 listener/宿主端口上。compose 部署中 api 容器仅发布公开端口，internal
端口只在 backend 内部网络可达（sidecar → api）。两个 app 共享同一套中间件、
错误外壳与 lifespan（config 校验 fail-closed 对两个 listener 同时生效）。

信号与停机合同：uvicorn 每个 Server.serve() 都会重装 SIGINT/SIGTERM 处理器，
双 server 下后装者覆盖先装者——SIGTERM 只会让第二个 server 优雅退出，第一个
只能等 SIGKILL。这里子类禁用各自 capture_signals，由本模块安装共享处理器，
一次性让两个 server 同时进入优雅停机；lifespan 的维护循环随之以引用计数
启停（见 services/maintenance.py），不因单侧 listener 退出而误停。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Generator
from types import FrameType

import uvicorn

logger = logging.getLogger(__name__)

PUBLIC_PORT = int(os.environ.get("PUBLIC_API_PORT", "8000"))
INTERNAL_PORT = int(os.environ.get("INTERNAL_AGENT_API_PORT", "8001"))
PUBLIC_HOST = os.environ.get("PUBLIC_API_HOST", "0.0.0.0")
# internal listener 绑定地址：默认 127.0.0.1 fail-closed（仅本机可达）。
# compose 部署显式设为 api 在 backend 网络的接口 IP（见 docker-compose.yml）；
# 裸机/容器默认下 internal 协议不可被其他容器或宿主网卡触达。
INTERNAL_HOST = os.environ.get("INTERNAL_AGENT_API_HOST", "127.0.0.1")


class _NoSignalCaptureServer(uvicorn.Server):
    """禁用 per-server 信号重装：信号由 app.serve main() 统一安装分发。"""

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:  # noqa: D102
        yield


async def _serve() -> None:
    from app.main import app, internal_app

    public = _NoSignalCaptureServer(
        uvicorn.Config(app, host=PUBLIC_HOST, port=PUBLIC_PORT, log_config=None)
    )
    internal = _NoSignalCaptureServer(
        uvicorn.Config(internal_app, host=INTERNAL_HOST, port=INTERNAL_PORT, log_config=None)
    )

    def _shutdown_all(sig: int, frame: FrameType | None) -> None:
        logger.info("shutdown signal %s received; stopping both listeners", sig)
        for server in (public, internal):
            server.handle_exit(sig, frame)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown_all, sig, None)

    servers = [asyncio.create_task(public.serve()), asyncio.create_task(internal.serve())]
    try:
        await asyncio.gather(*servers)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


def _validate_bind_plan() -> None:
    """启动前校验 listener 绑定计划（fail-closed）。

    生产 posture（未显式 DEV_ALLOW_WEAK_SECRETS）下 internal listener 不得绑定
    通配地址——compose 部署必须显式绑定 backend 内部网络接口 IP。
    另做端口可用性预检：uvicorn 绑定失败会在任务内 sys.exit 导致脏退出，
    这里提前给出明确错误并以非零码退出。
    """
    import socket

    from app import config

    if PUBLIC_PORT == INTERNAL_PORT:
        raise RuntimeError(
            f"PUBLIC_API_PORT({PUBLIC_PORT}) 与 INTERNAL_AGENT_API_PORT({INTERNAL_PORT}) 不得相同"
        )
    if not config.DEV_ALLOW_WEAK_SECRETS and INTERNAL_HOST in ("", "0.0.0.0", "::", "[::]"):
        raise RuntimeError(
            "生产环境 INTERNAL_AGENT_API_HOST 不得为通配地址："
            "请绑定 backend 内部网络接口（compose）或 127.0.0.1（本机）"
        )
    for host, port, name in (
        (PUBLIC_HOST, PUBLIC_PORT, "public"),
        (INTERNAL_HOST, INTERNAL_PORT, "internal"),
    ):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind((host if host not in ("", "0.0.0.0", "::") else "127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(
                f"{name} listener 无法绑定 {host}:{port}（端口被占用或地址不可用）：{exc}"
            ) from None
        finally:
            probe.close()


def main() -> None:
    _validate_bind_plan()
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:  # pragma: no cover
        logger.info("shutdown requested")


if __name__ == "__main__":
    main()
