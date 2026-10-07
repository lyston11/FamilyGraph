"""ONNX Runtime CPU 推理。

## 为什么用 ONNX Runtime 而不是 PyTorch

目标是 **CPU 推理且不引入 GPU/CUDA 栈**。ONNX Runtime 的 CPU 包体积小、依赖少，
在 ARM64 上可用，且能精确控制线程数（`intra_op_num_threads`）。

PyTorch 也能跑，但会带入数 GB 依赖，且默认线程策略会尝试占满所有核——这台机器
同时运行生产与开发，不能接受。

## 为什么不用量化（INT8）作为起点

量化在 ARM64 上的收益与质量影响**必须实测**。先以 FP32 建立正确性基线，
再决定是否量化；不为了省内存而在没有对照的情况下降低检索质量。

## 池化方式：CLS，不是 mean

`bge-small-zh-v1.5` 的 `1_Pooling/config.json` 声明
`pooling_mode_cls_token: true`、`pooling_mode_mean_tokens: false`。

取错池化方式不会报错，只会让向量质量下降——检索结果变差但系统「看起来正常」。
因此池化方式**从模型配置读取并校验**，不硬编码。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np


class ModelNotReady(Exception):
    """模型尚未加载完成（启动期或加载失败）。"""


@dataclass(frozen=True)
class PoolingConfig:
    """从模型目录读到的池化配置。"""

    cls_token: bool
    mean_tokens: bool

    @classmethod
    def load(cls, model_dir: str) -> PoolingConfig:
        path = Path(model_dir) / "1_Pooling" / "config.json"
        if not path.exists():
            # 缺失时 fail-loud：猜一个池化方式会让向量质量静默变差。
            raise ModelNotReady(f"缺少池化配置：{path}")
        raw = json.loads(path.read_text())
        return cls(
            cls_token=bool(raw.get("pooling_mode_cls_token")),
            mean_tokens=bool(raw.get("pooling_mode_mean_tokens")),
        )


class OnnxEncoder:
    """线程安全的 ONNX 编码器。

    ## 为什么加载在 `load()` 而不是 `__init__`

    模型加载要读约 100MB 权重并做图优化，耗时数秒。若在构造函数里做，服务无法
    先启动 HTTP 再后台加载，健康检查会在启动期失败并触发容器重启循环。
    因此：构造对象 → 启动 HTTP（`/readyz` 返回未就绪）→ `load()` → 就绪。

    ## 线程安全

    ONNX `InferenceSession.run` 是可重入的，但本服务把并发限制为 1（见 `gate.py`），
    因此不需要额外的推理锁。这里加锁是为了保护**加载状态**，不是保护推理。
    """

    def __init__(
        self,
        *,
        model_path: str,
        tokenizer_path: str,
        max_tokens: int,
        threads: int,
        dimension: int,
        query_instruction: str,
    ) -> None:
        self._model_path = model_path
        self._tokenizer_path = tokenizer_path
        self._max_tokens = max_tokens
        self._threads = threads
        self._dimension = dimension
        self._query_instruction = query_instruction

        self._session = None
        self._tokenizer = None
        self._input_names: tuple[str, ...] = ()
        self._pooling: PoolingConfig | None = None
        self._lock = threading.Lock()
        self._load_error: str | None = None
        self._load_seconds: float | None = None

    # ---- 加载 ------------------------------------------------------

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._session is not None

    @property
    def load_error(self) -> str | None:
        with self._lock:
            return self._load_error

    def load(self) -> None:
        """加载模型与分词器。失败时记录原因（`ready` 保持 False），不抛到调用方。

        不抛的原因：服务已经启动，抛异常只会让进程退出并进入容器重启循环，
        而「未就绪」是更准确的状态——`/readyz` 会返回 503，运维能看到原因。
        """
        started = time.monotonic()
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            pooling = PoolingConfig.load(str(Path(self._model_path).parent))
            if not pooling.cls_token:
                raise ModelNotReady(
                    "本模型期望 CLS 池化，但配置未启用 cls_token；"
                    "取错池化不会报错，只会让检索质量静默下降"
                )

            tokenizer = Tokenizer.from_file(self._tokenizer_path)
            tokenizer.enable_truncation(max_length=self._max_tokens)
            # padding 在 encode_batch 时按批动态设置（见 `_encode_batch`）。

            options = ort.SessionOptions()
            # 线程数硬绑定：默认会让 ONNX 尝试占满所有核。
            options.intra_op_num_threads = self._threads
            options.inter_op_num_threads = 1
            # 关闭图优化之外的额外并行，减少对同机其他服务的干扰。
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            session = ort.InferenceSession(
                self._model_path, sess_options=options, providers=["CPUExecutionProvider"]
            )

            inputs = tuple(i.name for i in session.get_inputs())
            if "input_ids" not in inputs or "attention_mask" not in inputs:
                raise ModelNotReady(f"模型输入缺少 input_ids/attention_mask：{inputs}")

            with self._lock:
                self._session = session
                self._tokenizer = tokenizer
                self._input_names = inputs
                self._pooling = pooling
                self._load_error = None
                self._load_seconds = time.monotonic() - started
        except Exception as exc:  # noqa: BLE001 - 任何加载失败都记录为未就绪
            with self._lock:
                self._load_error = f"{type(exc).__name__}: {exc}"
                self._load_seconds = time.monotonic() - started

    # ---- 编码 ------------------------------------------------------

    def encode(
        self, texts: list[str], *, is_query: bool
    ) -> tuple[list[list[float]], list[int], list[bool]]:
        """编码文本，返回 `(向量, 真实 token 数, 是否被截断)`。

        `is_query=True` 时加上检索指令（BGE 官方建议对**短查询**加指令，
        对**文档**不加）。加错侧不会报错，但会降低检索质量，因此由调用方显式声明。

        ## 为什么必须返回 token 数与截断标记

        模型上限 512 tokens，超长文本会被**静默截断**。若只返回向量，调用方会以为
        整段都进了向量，实际只有前半段——检索会漏掉后半段内容且无人发现。
        因此把「实际用了多少 token、有没有被截断」作为返回值暴露出去。
        """
        if not texts:
            return [], [], []
        with self._lock:
            session = self._session
            tokenizer = self._tokenizer
            input_names = self._input_names
        if session is None or tokenizer is None:
            raise ModelNotReady(self._load_error or "模型未加载")

        prepared = [
            f"{self._query_instruction}{t}" if is_query and self._query_instruction else t
            for t in texts
        ]

        # 按长度排序再分批：同一批内长度接近，padding 浪费最小。
        # 这不是微优化——一批里混入一个超长文本会让整批按最长补齐。
        order = sorted(range(len(prepared)), key=lambda i: len(prepared[i]))
        vectors: list[list[float] | None] = [None] * len(prepared)
        token_counts: list[int] = [0] * len(prepared)
        truncated: list[bool] = [False] * len(prepared)

        tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        encodings = tokenizer.encode_batch([prepared[i] for i in order])

        for position, encoding in enumerate(encodings):
            original_index = order[position]
            # `attention_mask` 的和 = 未补齐的真实 token 数（含 [CLS]/[SEP]）。
            real_tokens = int(sum(encoding.attention_mask))
            token_counts[original_index] = real_tokens
            # 达到上限即说明可能被截断：模型上限是硬截断，无其他信号可依。
            truncated[original_index] = real_tokens >= self._max_tokens

            ids = np.asarray([encoding.ids], dtype=np.int64)
            mask = np.asarray([encoding.attention_mask], dtype=np.int64)
            feeds: dict[str, np.ndarray] = {
                "input_ids": ids,
                "attention_mask": mask,
            }
            if "token_type_ids" in input_names:
                feeds["token_type_ids"] = np.asarray(
                    [encoding.type_ids], dtype=np.int64
                )
            outputs = session.run(None, feeds)
            # 第一个输出是 last_hidden_state: [1, seq, hidden]
            hidden = outputs[0]
            pooled = hidden[:, 0, :]  # CLS 池化（见 PoolingConfig 校验）
            norm = np.linalg.norm(pooled, axis=1, keepdims=True)
            normalized = pooled / np.clip(norm, 1e-12, None)
            vectors[original_index] = normalized[0].astype(float).tolist()

        result: list[list[float]] = []
        for vector in vectors:
            if vector is None:
                raise RuntimeError("编码结果缺失（内部错误）")
            if len(vector) != self._dimension:
                raise RuntimeError(
                    f"模型输出维度 {len(vector)} 与配置 {self._dimension} 不一致；"
                    "不一致会让向量插入失败或语义错乱，必须 fail-loud"
                )
            result.append(vector)
        return result, token_counts, truncated

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "ready": self._session is not None,
                "load_error": self._load_error,
                "load_seconds": self._load_seconds,
                "dimension": self._dimension,
                "max_tokens": self._max_tokens,
                "threads": self._threads,
                "pooling": (
                    None
                    if self._pooling is None
                    else {
                        "cls_token": self._pooling.cls_token,
                        "mean_tokens": self._pooling.mean_tokens,
                    }
                ),
            }
