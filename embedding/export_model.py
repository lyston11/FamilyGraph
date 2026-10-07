"""导出 `bge-small-zh-v1.5` 为 ONNX（构建期执行一次）。

## 为什么需要这个脚本

官方仓库没有 ONNX 权重（已核对文件清单），因此必须在构建期导出。

## 关键点：`last_hidden_state`，不是 pooled 输出

导出 `last_hidden_state`（`[batch, seq, hidden]`），池化在运行期用 numpy 做。
这样池化方式（CLS vs mean）由我们显式控制，且与 `1_Pooling/config.json` 的声明
一致（该模型是 CLS）。

若导出时让模型做池化，池化方式会固化在 ONNX 图里，运行时**无法校验**——
取错池化不会报错，只会让检索质量静默下降。

## 为什么不用 optimum

`optimum` 的导出封装会引入额外的版本约束（实测 `optimum==1.21.2` 要求
`transformers<4.43.0`，与 `transformers==4.44.2` 冲突），而 `torch.onnx.export`
已足够且依赖更少。

## 为什么固定 opset

opset 影响算子集与兼容性。固定为 17（ONNX Runtime 1.19 稳定支持），避免不同
构建时间产出不同的图。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

MODEL_REPO = os.environ.get("MODEL_REPO", "BAAI/bge-small-zh-v1.5")
OUT_DIR = Path("/export/out")


def main() -> int:
    from transformers import AutoModel, AutoTokenizer

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"下载 {MODEL_REPO} …")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_REPO)
    model = AutoModel.from_pretrained(MODEL_REPO)
    model.eval()

    onnx_path = OUT_DIR / "model.onnx"
    dummy = tokenizer("样例文本", return_tensors="pt")

    import torch

    # dynamo=False：使用稳定的 TorchScript 导出器。新导出器在部分版本上对
    # BERT 类模型仍会产出无法在 onnxruntime CPU 上运行的图。
    torch.onnx.export(
        model,
        (dummy["input_ids"], dummy["attention_mask"], dummy["token_type_ids"]),
        str(onnx_path),
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=["last_hidden_state"],
        dynamic_axes={
            "input_ids": {0: "batch", 1: "sequence"},
            "attention_mask": {0: "batch", 1: "sequence"},
            "token_type_ids": {0: "batch", 1: "sequence"},
            "last_hidden_state": {0: "batch", 1: "sequence"},
        },
        opset_version=17,
        do_constant_folding=True,
        dynamo=False,
    )
    print(f"已导出 {onnx_path}（{onnx_path.stat().st_size / 1e6:.1f} MB）")

    # tokenizer.json 是运行期唯一需要的分词文件（`tokenizers` 直接读它）。
    tokenizer.save_pretrained(str(OUT_DIR))

    # 池化配置必须一起带走：`model.py` 会读它并校验 CLS 池化，缺失即拒绝就绪。
    from huggingface_hub import hf_hub_download

    pooling = hf_hub_download(MODEL_REPO, "1_Pooling/config.json")
    pooling_dir = OUT_DIR / "1_Pooling"
    pooling_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pooling, pooling_dir / "config.json")

    config = json.loads((OUT_DIR / "config.json").read_text())
    hidden = int(config["hidden_size"])
    print(f"hidden_size={hidden}（运行期维度必须与此一致）")
    print("导出完成：", sorted(p.name for p in OUT_DIR.iterdir()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
