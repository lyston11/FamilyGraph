"""Embedding 服务的部署配置。

## 为什么配置要 fail-loud

这个服务被 CPU 与内存**硬限制**（见 `docker-compose.yml`）。若配置值本身不合理
（例如并发 0、批量 0、超时 0），服务会在运行期表现为「永久拒绝」或「永久等待」，
而运维只能看到 503——不知道是资源不足还是配置错误。因此在启动时校验并拒绝启动。

## 资源预算的三层

```text
容器层：cpus / mem_limit        <- 真正兜底，超出即被限制或 OOM
进程层：EMBEDDING_THREADS=1     <- 不让 BLAS/ONNX 自行开满核
推理层：MAX_CONCURRENT + MAX_WAITING + WAIT_TIMEOUT
```

第三层最关键：**并发 1 + 有界等待 + 总期限**，使「同时有多少份推理在跑」有上界。
没有它，多租户并发会把 4 核机器占满。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: `bge-small-zh-v1.5` 的官方检索指令。
#:
#: 官方文档说明：v1.5 不加指令也只有「轻微下降」，且**最佳做法是在自己的任务上实测**。
#: 这里默认加上，因为本项目主要是「短问题检索长资料」；可用环境变量清空以对照实测。
DEFAULT_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


def _int_env(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} 必须是整数，实际为 {raw!r}") from None
    if value < minimum:
        raise ValueError(f"{name} 必须 >= {minimum}，实际为 {value}")
    return value


def _float_env(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} 必须是数字，实际为 {raw!r}") from None
    if value < minimum:
        raise ValueError(f"{name} 必须 >= {minimum}，实际为 {value}")
    return value


@dataclass(frozen=True)
class EmbeddingConfig:
    """不可变配置。`from_env()` 是唯一构造入口。"""

    model_dir: str
    model_name: str
    dimension: int
    max_tokens: int
    query_instruction: str

    # 并发与排队
    max_concurrent: int
    max_batch: int
    max_waiting: int
    wait_timeout_seconds: float

    # 请求上界（防止单个请求独占名额）
    max_texts_per_request: int
    max_chars_per_text: int

    threads: int

    @property
    def model_path(self) -> str:
        return f"{self.model_dir}/model.onnx"

    @property
    def tokenizer_path(self) -> str:
        return f"{self.model_dir}/tokenizer.json"

    def validate(self) -> None:
        """启动时校验。任何一条不满足都拒绝启动。"""
        problems: list[str] = []
        if self.dimension <= 0:
            problems.append("EMBEDDING_DIMENSION 必须为正")
        if self.max_tokens <= 0:
            problems.append("EMBEDDING_MAX_TOKENS 必须为正")
        if self.max_concurrent <= 0:
            problems.append("EMBEDDING_MAX_CONCURRENT 必须 >= 1")
        if self.max_batch <= 0:
            problems.append("EMBEDDING_MAX_BATCH 必须 >= 1")
        if self.max_waiting < 0:
            problems.append("EMBEDDING_MAX_WAITING 不能为负")
        if self.wait_timeout_seconds <= 0:
            problems.append("EMBEDDING_WAIT_TIMEOUT_SECONDS 必须为正")
        if self.max_texts_per_request <= 0:
            problems.append("EMBEDDING_MAX_TEXTS_PER_REQUEST 必须 >= 1")
        if self.max_chars_per_text <= 0:
            problems.append("EMBEDDING_MAX_CHARS_PER_TEXT 必须 >= 1")
        if self.threads <= 0:
            problems.append("EMBEDDING_THREADS 必须 >= 1")
        # 批量大于并发上限没有意义：同时最多跑 max_concurrent 份推理。
        if self.max_batch > self.max_tokens and self.max_batch > 4096:
            problems.append("EMBEDDING_MAX_BATCH 明显过大（>4096）")
        if problems:
            raise ValueError("embedding 配置无效：" + "；".join(problems))

    def public_summary(self) -> dict[str, object]:
        """供 `/readyz` 输出的**非敏感**配置摘要。

        只含预算与模型标识，不含路径（路径会泄露部署结构）。
        """
        return {
            "model": self.model_name,
            "dimension": self.dimension,
            "max_tokens": self.max_tokens,
            "max_concurrent": self.max_concurrent,
            "max_batch": self.max_batch,
            "max_waiting": self.max_waiting,
            "wait_timeout_seconds": self.wait_timeout_seconds,
            "threads": self.threads,
            "query_instruction_enabled": bool(self.query_instruction),
        }


def from_env() -> EmbeddingConfig:
    """从环境构造并校验。"""
    config = EmbeddingConfig(
        model_dir=os.environ.get("EMBEDDING_MODEL_DIR", "/models/bge-small-zh-v1.5"),
        model_name=os.environ.get("EMBEDDING_MODEL_NAME", "bge-small-zh-v1.5"),
        dimension=_int_env("EMBEDDING_DIMENSION", 512, minimum=1),
        max_tokens=_int_env("EMBEDDING_MAX_TOKENS", 512, minimum=1),
        query_instruction=os.environ.get(
            "EMBEDDING_QUERY_INSTRUCTION", DEFAULT_QUERY_INSTRUCTION
        ),
        # 默认并发 1：这台机器同时跑生产与开发，embedding 不能与它们抢核。
        max_concurrent=_int_env("EMBEDDING_MAX_CONCURRENT", 1, minimum=1),
        max_batch=_int_env("EMBEDDING_MAX_BATCH", 4, minimum=1),
        max_waiting=_int_env("EMBEDDING_MAX_WAITING", 8, minimum=0),
        wait_timeout_seconds=_float_env(
            "EMBEDDING_WAIT_TIMEOUT_SECONDS", 5.0, minimum=0.001
        ),
        max_texts_per_request=_int_env("EMBEDDING_MAX_TEXTS_PER_REQUEST", 16, minimum=1),
        max_chars_per_text=_int_env("EMBEDDING_MAX_CHARS_PER_TEXT", 8000, minimum=1),
        threads=_int_env("EMBEDDING_THREADS", 1, minimum=1),
    )
    config.validate()
    return config
