# FamilyGraph Embedding 服务（ARM64 CPU）
#
# ## 为什么是两阶段构建
#
# 导出 ONNX 需要 PyTorch + transformers（约 1GB 依赖），但**运行期完全不需要**——
# 运行时只用 onnxruntime + tokenizers。两阶段让运行镜像保持小体积，减少磁盘与
# 启动加载时间；这台机器根盘只剩约 39GB，不该让导出工具链常驻。
#
# ## 为什么在构建期导出而不是用现成 ONNX
#
# 官方仓库（BAAI/bge-small-zh-v1.5）**没有 ONNX 权重**（已核对文件清单：
# 只有 model.safetensors / pytorch_model.bin）。因此必须自行导出。
#
# ## 为什么模型在构建期下载并固化
#
# 运行期不访问 HuggingFace：这台机器上还有生产服务，运行期外网依赖会让启动
# 受外部可用性影响。模型一旦固化进镜像，重启不需要网络。

# ---------- 阶段 1：导出 ONNX ----------
FROM python:3.12-slim-bookworm AS exporter

# 固定版本：导出结果必须可复现，浮动版本会让 ONNX 图随上游变化。
#
# 不含 `optimum`：导出脚本直接用 `torch.onnx.export`，不需要 optimum。
# 曾误加 optimum==1.21.2，它与 transformers==4.44.2 冲突
# （optimum 1.21.2 要求 transformers<4.43.0），且是**未使用**的依赖。
#
# 不含 `sentencepiece`：本模型是 BertTokenizer（vocab.txt/tokenizer.json），
# 不用 sentencepiece。
RUN pip install --no-cache-dir \
    torch==2.4.1 \
    transformers==4.44.2

WORKDIR /export
COPY export_model.py /export/export_model.py

# 模型仓库 id 与目标维度由参数给出，便于替换为其他模型时无需改脚本。
ARG MODEL_REPO=BAAI/bge-small-zh-v1.5
ENV MODEL_REPO=${MODEL_REPO}
RUN python /export/export_model.py

# ---------- 阶段 2：运行 ----------
FROM python:3.12-slim-bookworm AS runtime

# 运行期依赖单独锁定（不含 torch）。
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

WORKDIR /srv

COPY app /srv/app

# 导出产物：model.onnx + tokenizer.json + 1_Pooling/config.json
# 池化配置是必需的——`model.py` 会读它并校验 CLS 池化，缺失即拒绝就绪。
COPY --from=exporter /export/out /models/bge-small-zh-v1.5

# 非 root 运行：容器内不需要写权限。
RUN useradd --create-home --uid 10001 embedder \
    && chown -R embedder:embedder /models /srv
USER embedder

# 线程数由环境变量控制，且默认 1（见 app/config.py）：
# 这台机器同时跑生产与开发，不能让 ONNX 自行占满 4 核。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    EMBEDDING_THREADS=1 \
    EMBEDDING_MODEL_DIR=/models/bge-small-zh-v1.5

EXPOSE 8090

# 单 worker：多 worker 会各自加载一份模型，直接翻倍内存。
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]
