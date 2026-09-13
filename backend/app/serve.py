"""多 listener 启动入口：公开家庭 API、internal agent 协议与 admin API 分端口 serve。

P1 网络隔离裁定（08-29 任务）：internal 协议（/internal/agent/*）不得暴露在
公开 listener/宿主端口上。compose 部署中 api 容器仅发布公开端口，internal
端口只在 backend 内部网络可达（sidecar → api）。三个 app 共享同一套中间件、
错误外壳与 lifespan（config 校验 fail-closed 对所有 listener 同时生效）。

09-04 起新增 admin listener（:8002 /admin-api）：系统管理员独立认证面，
与家庭 app 共享 engine/lifespan 但不共享 router 与 JWT 签发域；默认绑定
127.0.0.1 fail-closed，compose 部署显式绑定 admin 内部网络接口 IP。

信号与停机合同：uvicorn 每个 Server.serve() 都会重装 SIGINT/SIGTERM 处理器，
多 server 下后装者覆盖先装者——SIGTERM 只会让最后一个 server 优雅退出，其余
只能等 SIGKILL。这里子类禁用各自 capture_signals，由本模块安装共享处理器，
一次性让所有 server 同时进入优雅停机；lifespan 的维护循环随之以引用计数
启停（见 services/maintenance.py），不因单侧 listener 退出而误停。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from collections.abc import Callable, Generator, Sequence
from types import FrameType

import uvicorn

logger = logging.getLogger(__name__)

PUBLIC_PORT = int(os.environ.get("PUBLIC_API_PORT", "8000"))
INTERNAL_PORT = int(os.environ.get("INTERNAL_AGENT_API_PORT", "8001"))
ADMIN_PORT = int(os.environ.get("ADMIN_API_PORT", "8002"))
PUBLIC_HOST = os.environ.get("PUBLIC_API_HOST", "0.0.0.0")
# internal/admin listener 绑定地址：默认 127.0.0.1 fail-closed（仅本机可达）。
# compose 部署显式设为对应内部网络接口 IP（见 docker-compose.yml）；
# 裸机/容器默认下 internal/admin 协议不可被其他容器或宿主网卡触达。
INTERNAL_HOST = os.environ.get("INTERNAL_AGENT_API_HOST", "127.0.0.1")
ADMIN_HOST = os.environ.get("ADMIN_API_HOST", "127.0.0.1")

# 优雅停机时限（秒）：到点后 uvicorn 强制断开仍在处理的连接（SSE 长流、慢请求），
# 避免旧实例拖住端口、放大 restart 竞态窗口。需大于正常请求 P99 且远小于
# systemd TimeoutStopSec（见 scripts/install-server-automation.sh）。
SHUTDOWN_GRACE_SECONDS = int(os.environ.get("SHUTDOWN_GRACE_SECONDS", "5"))
# bind 预检重试节奏：首次探测后按此序列退避（总窗约 4.5s）。旧实例正在退出时
# 端口在秒级内释放，进程内吸收该窗口，避免 systemd 以 5s 间隔循环重启。
_BIND_RETRY_DELAYS: tuple[float, ...] = (1.0, 1.5, 2.0)


class _NoSignalCaptureServer(uvicorn.Server):
    """禁用 per-server 信号重装：信号由 app.serve main() 统一安装分发。"""

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:  # noqa: D102
        yield


async def _serve() -> None:
    from app.main import admin_app, app, internal_app

    public = _NoSignalCaptureServer(
        uvicorn.Config(
            app,
            host=PUBLIC_HOST,
            port=PUBLIC_PORT,
            log_config=None,
            timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS,
        )
    )
    internal = _NoSignalCaptureServer(
        uvicorn.Config(
            internal_app,
            host=INTERNAL_HOST,
            port=INTERNAL_PORT,
            log_config=None,
            timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS,
        )
    )
    admin = _NoSignalCaptureServer(
        uvicorn.Config(
            admin_app,
            host=ADMIN_HOST,
            port=ADMIN_PORT,
            log_config=None,
            timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS,
        )
    )
    servers = (public, internal, admin)

    def _shutdown_all(sig: int, frame: FrameType | None) -> None:
        logger.info("shutdown signal %s received; stopping all listeners", sig)
        for server in servers:
            server.handle_exit(sig, frame)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown_all, sig, None)

    tasks = [asyncio.create_task(server.serve()) for server in servers]
    try:
        await asyncio.gather(*tasks)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


def _probe_bind(host: str, port: int) -> None:
    """单次 bind 预检；不可用即抛 OSError。

    设置 SO_REUSEADDR 与 asyncio/uvicorn 真实绑定语义一致：TIME_WAIT 残留
    （重启竞态的典型现场，`ss -tln` 无监听者但 bind 报 EADDRINUSE）不算占用；
    只要有活跃 LISTEN 就仍会失败。
    """
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host, port))
    finally:
        probe.close()


def _ensure_ports_bindable(
    plan: Sequence[tuple[str, str, int]],
    *,
    delays: Sequence[float] = _BIND_RETRY_DELAYS,
    sleep: Callable[[float], None] = time.sleep,
    prober: Callable[[str, int], None] = _probe_bind,
) -> None:
    """逐 listener 预检绑定，EADDRINUSE 时有限退避重试，超窗才报错（fail-closed）。

    重启竞态主因：旧实例尚未完成端口释放，新实例预检即失败退出，systemd 以
    5s 间隔循环重启，约 1 分钟后才有一个实例成功（09-13 任务实测）。进程内
    重试吸收秒级释放窗口；重试期间逐次告警，最终失败给出与「端口被他人占用」
    可区分的排障口径。
    """
    for name, host, port in plan:
        probe_host = host if host not in ("", "0.0.0.0", "::") else "127.0.0.1"
        last_exc: OSError | None = None
        for attempt in range(len(delays) + 1):
            if attempt:
                logger.warning(
                    "%s listener 端口 %s:%s 尚未释放（疑似旧实例退出中），%.1fs 后重试（%d/%d）",
                    name,
                    host,
                    port,
                    delays[attempt - 1],
                    attempt,
                    len(delays),
                )
                sleep(delays[attempt - 1])
            try:
                prober(probe_host, port)
            except OSError as exc:
                last_exc = exc
                continue
            last_exc = None
            break
        if last_exc is not None:
            raise RuntimeError(
                f"{name} listener 无法绑定 {host}:{port}（重试 {len(delays)} 次后仍被占用："
                f"或为上一实例尚未完全退出，或被其他进程持有，"
                f"可用 lsof -iTCP:{port} -sTCP:LISTEN 定位）：{last_exc}"
            ) from None


def _validate_bind_plan() -> None:
    """启动前校验 listener 绑定计划（fail-closed）。

    生产 posture（未显式 DEV_ALLOW_WEAK_SECRETS）下 internal/admin listener
    不得绑定通配地址——compose 部署必须显式绑定内部网络接口 IP。另做端口
    可用性预检：uvicorn 绑定失败会在任务内 sys.exit 导致脏退出，这里提前
    给出明确错误并以非零码退出；对旧实例尚未释放的端口做有限退避重试。
    """
    from app import config

    ports = {
        "public": PUBLIC_PORT,
        "internal": INTERNAL_PORT,
        "admin": ADMIN_PORT,
    }
    if len(set(ports.values())) != len(ports):
        raise RuntimeError(f"listener 端口不得重复：{ports}")
    if not config.DEV_ALLOW_WEAK_SECRETS and INTERNAL_HOST in ("", "0.0.0.0", "::", "[::]"):
        raise RuntimeError(
            "生产环境 INTERNAL_AGENT_API_HOST 不得为通配地址："
            "请绑定 backend 内部网络接口（compose）或 127.0.0.1（本机）"
        )
    if not config.DEV_ALLOW_WEAK_SECRETS and ADMIN_HOST in ("", "0.0.0.0", "::", "[::]"):
        raise RuntimeError(
            "生产环境 ADMIN_API_HOST 不得为通配地址："
            "请绑定 admin 内部网络接口（compose）或 127.0.0.1（本机）"
        )
    _ensure_ports_bindable(
        (
            ("public", PUBLIC_HOST, PUBLIC_PORT),
            ("internal", INTERNAL_HOST, INTERNAL_PORT),
            ("admin", ADMIN_HOST, ADMIN_PORT),
        )
    )


def main() -> None:
    _validate_bind_plan()
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:  # pragma: no cover
        logger.info("shutdown requested")


if __name__ == "__main__":
    main()
