"""缺 `redis` 客户端依赖时的降级合同（**不需要真实 Redis**）。

## 为什么单独一个文件

`test_redis_degradation.py` 顶部有模块级 `pytestmark`，要求 `REDIS_URL` 存在，
否则整体 skip——因为那些用例注入的是**真实 Redis 故障**。

而「缺依赖」是「没有 Redis 可用」的**最极端形态**，也是**生产镜像**上的真实形态：
实测 `familygraph-prod-api-1` 内 `import redis` 直接 `ModuleNotFoundError`，
因为 `redis` 当时未被声明为运行时依赖（`pip install .` 不会装未声明依赖）。

把它放在被 skip 的文件里，等于让这条合同在 CI 上永远不被执行——而它要防的恰恰是
「本地 venv 恰好装过、镜像里没装」的形态。

## 被修掉的缺陷

`_client_or_none()` 里的 `import redis` 原本在 try 块**之外**，因此
`ModuleNotFoundError` 不会被「任何后端错误都按不可用降级」的 except 捕获；
而 `available()` 此时仍返回 True（它只看 url 非空），于是调用方以为加速层健康、
随后在读写路径上抛异常。

修法有两处，缺一不可：

1. `pyproject.toml` 声明 `redis==8.1.0` 为**运行时**依赖（与 `psycopg` 同类理由）；
2. `import redis` 移入 try 并降级——依赖缺失是**部署错误**，但仍不得让调用方崩溃。
"""

from __future__ import annotations

from app.services import redis_accel
from app.services.redis_accel import RedisAccelerator


def test_missing_client_library_degrades_instead_of_raising(monkeypatch):
    """缺 `redis` 依赖时必须**降级**，不得抛异常。

    ## 为什么这条不能放进上面的 skip 组

    上面所有用例都需要真实 Redis（`REDIS_URL`），因此在本机/CI 无 Redis 时整体
    skip。而**缺依赖**恰好是「没有 Redis 可用」的最极端形态，也是**生产镜像**上
    的真实形态——实测 `familygraph-prod-api-1` 内 `import redis` 直接
    `ModuleNotFoundError`，因为 `redis` 曾经未被声明为运行时依赖。

    该缺陷的危害不是「功能缺失」而是**崩溃**：
    `_client_or_none()` 里的 `import redis` 原本在 try 块之外，因此
    `ModuleNotFoundError` 不会被「任何后端错误都按不可用降级」的 except 捕获；
    而 `available()` 此时仍返回 True（它只看 url 非空），于是调用方以为加速层健康、
    随后在读写路径上抛异常。

    这条断言把「三态降级」的合同扩展到**依赖缺失**这一情形：`get` → `None`、
    `set` → `False`、`try_set_if_absent` → `None`，且 `health()` 如实报告原因。
    """
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "redis":
            raise ImportError("No module named 'redis'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    accel = RedisAccelerator("redis://127.0.0.1:6379/0")
    assert accel.configured is True

    # 全部操作都必须降级，不得抛异常。
    assert accel.get("k") is None
    assert accel.set("k", "v", ttl_seconds=1) is False
    assert accel.try_set_if_absent("k", ttl_seconds=1) is None
    assert accel.delete("k") is False

    health = accel.health()
    assert health.available is False
    assert health.last_error == "ImportError"
    assert health.policy == redis_accel.REDIS_DEGRADATION_POLICY
