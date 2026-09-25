"""Steward 模型辅助的执行载体（09-25 E1）。

## 为什么需要这一层

重构前有**两条平行的执行路径**：``steward_assist.execute_batch``（进程内 httpx）
与 ``lease_child_run``/``settle_child_run``（Pi child run）。两条路径各自维护
attempt 状态机、计费、封闭校验与写回，共用判定点被各调一遍——任何一侧改动都要在
另一侧同步，否则静默漂移。

现在只有**一条链路**：

```
plan_for_job  →  lease_attempt  →  carrier.execute  →  settle_attempt
```

载体只负责「谁发起 HTTP、结果怎么回来」这一件事。attempt 状态机、计费、封闭
schema 校验、写回栅栏、CAS 应用全部在 ``steward_assist`` 里，与载体无关。

因此「逐 kind 迁移」不再需要批次内分流：换一个 kind 的载体，就是改一行配置。

## 载体契约

``execute`` 返回 ``CarrierOutcome``，**不**直接改数据库。结算由调用方在同一事务
里做，这样「发送」与「结算」的边界在任何载体下都一致，也保证 HTTP 永不发生在
业务写事务内。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app import config
from app.services.steward_assist import (
    _PROMPTS,
    REASON_PROVIDER_UNAVAILABLE,
    _build_payload,
    _fill_model,
    _post_json,
    _user_content_for,
)

# 载体名与配置值共用同一组字面量：两侧漂移会让调度层选到不存在的载体。
CARRIER_INPROC = "inproc"
CARRIER_PI = "pi"
CARRIERS: tuple[str, ...] = (CARRIER_INPROC, CARRIER_PI)


@dataclass(frozen=True)
class CarrierOutcome:
    """一个载体执行完一次 attempt 的结果（尚未落库）。

    刻意与 ``AttemptResult`` 同形但独立：载体不该知道 attempt 的行结构，否则
    新增载体就得跟着改 attempt 的字段。
    """

    status: str  # "succeeded" | "failed"
    text: str | None = None
    usage: dict[str, int] | None = None
    error_code: str | None = None
    latency_ms: int = 0
    response_bytes: int = 0
    exc: Exception | None = None


class AssistCarrier(Protocol):
    """执行一次 attempt。实现必须**无数据库写入**。"""

    carrier: str

    def execute(self, db: Any, attempt: Any, *, timeout: float, api: str) -> CarrierOutcome: ...


class InprocCarrier:
    """进程内 httpx 载体：直连 Provider（经 resolve_runtime 解密）。

    这是 09-06 引入的路径，也是重构前的唯一路径。保留它是因为：
    - 逐 kind 迁移需要一个可回退的默认值；
    - Pi 载体不可用时（sidecar 未部署/角色不含 steward）必须有明确行为，
      而不是静默不执行。
    """

    carrier = CARRIER_INPROC

    def execute(
        self, db: Any, attempt: Any, *, timeout: float, api: str, transport: Any = None
    ) -> CarrierOutcome:
        """Send one request. ``transport`` is a test seam for the fake upstream.

        It exists because the pre-refactor tests injected a fake transport to
        drive the whole pipeline without a model; removing that seam would have
        forced those tests to mock the carrier instead, which tests less.
        """
        import time

        from app.services import agent_provider

        started = time.monotonic()
        runtime = agent_provider.resolve_runtime(
            db, attempt.space_id, agent_kind=agent_provider.AGENT_KIND_STEWARD
        )
        if runtime is None:
            return CarrierOutcome(
                status="failed",
                error_code=REASON_PROVIDER_UNAVAILABLE,
                exc=RuntimeError("provider unresolved"),
            )
        user_content = _user_content_for(db, attempt)
        payload = _fill_model(
            _build_payload(
                api,
                _PROMPTS[attempt.assist_kind],
                user_content,
                attempt.reserved_output_tokens or 1,
            ),
            attempt.model or "",
        )
        # Resolve the sender through the module, not a bound name: tests patch
        # ``steward_assist._post_json`` to inject a fake upstream, and an
        # import-time binding would silently ignore that patch (it did — every
        # carrier test was hitting the real transport and reporting "degraded").
        from app.services import steward_assist

        send = transport if transport is not None else steward_assist._post_json
        try:
            data = send(
                f"{runtime.base_url.rstrip('/')}{_api_path(api)}",
                _auth_headers(runtime),
                payload,
                timeout,
            )
        except Exception as exc:  # noqa: BLE001 — 分类交给 _classify_transport_error
            return CarrierOutcome(
                status="failed",
                error_code=type(exc).__name__[:64],
                latency_ms=int((time.monotonic() - started) * 1000),
                exc=exc,
            )
        text, usage = _parse_response_for(api, data)
        return CarrierOutcome(
            status="succeeded",
            text=text,
            usage=usage,
            latency_ms=int((time.monotonic() - started) * 1000),
            response_bytes=len(text.encode("utf-8")),
        )


class PiCarrier:
    """受限 Pi child run 载体（09-25 E1 骨架；E3 实现执行）。

    ``carrier`` 已在 ``_reserve_attempt`` 时写进 attempt，sidecar 通过
    ``/internal/agent/steward/attempts/lease`` 取走；本类只声明契约，真正的
    执行发生在 sidecar 进程里，结果经 ``/runs/{id}/settle`` 回到
    ``settle_attempt``。因此 ``execute`` 在服务端**不应被调用**——服务端只负责
    建 run 与结算，不负责跑模型。
    """

    carrier = CARRIER_PI

    def execute(self, db: Any, attempt: Any, *, timeout: float, api: str) -> CarrierOutcome:
        raise NotImplementedError(
            "the pi carrier is executed by the sidecar, not in-process; "
            "settlement arrives through /internal/agent/runs/{id}/settle"
        )


def _api_path(api: str) -> str:
    from app.services.steward_assist import _API_PATHS

    return _API_PATHS[api]


def _auth_headers(runtime: Any) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {runtime.api_key}",
        "Content-Type": "application/json",
    }


def _parse_response_for(api: str, data: dict[str, Any]) -> tuple[str, dict[str, int] | None]:
    from app.services.steward_assist import _parse_response

    return _parse_response(api, data)


_INPROC = InprocCarrier()
_PI = PiCarrier()
_BY_NAME: dict[str, AssistCarrier] = {CARRIER_INPROC: _INPROC, CARRIER_PI: _PI}


def carrier_for(db: Any, space_id: int, kind: str) -> AssistCarrier:
    """Resolve the carrier for one kind.

    Read from config, not from the database: the carrier is a deployment decision
    (which executor this installation runs), not per-space data. An unknown value
    fails closed rather than silently falling back, because a typo would otherwise
    select a different executor than the operator intended.
    """
    name = getattr(config, f"STEWARD_ASSIST_{kind.upper()}_CARRIER", CARRIER_INPROC)
    carrier = _BY_NAME.get(name)
    if carrier is None:
        raise RuntimeError(
            f"unknown steward assist carrier for {kind}: {name!r}; " f"expected one of {CARRIERS}"
        )
    return carrier


__all__ = [
    "CARRIER_INPROC",
    "CARRIER_PI",
    "CARRIERS",
    "AssistCarrier",
    "CarrierOutcome",
    "InprocCarrier",
    "PiCarrier",
    "carrier_for",
]
