# B 实施前独立检索基线

2026-09-13，主线程在已提交的 A worktree（`d1f43a5`）运行。C 同期只改 sidecar；本测量的后端生产文件冻结。

冻结数据：[retrieval-fixture-v1.json](retrieval-fixture-v1.json)。16 条核心与父任务协议逐项对应，M5 使用确定性无关键词填充，使完整生日/花生限制句跨旧 1200 字符边界（1188～1206）。另外在 B 实施前独立冻结 2 条英文、10 条诊断扩展和 2 条无资料问题；扩展集不能用于定向补词典。

实际链为：临时迁移库 → 显式 manual propose/confirm → 持久会话/消息与真实 queued/leased run → `ContextBuilder.build` → `memory_rag.search_rag` → included blocks。Q16 的允许前文已经存入同 session，原 ContextBuilder 没有利用它。只使用合成资料和本地 Provider 元数据，没有调用模型。

| 分组 | 命中 | Recall@5 | MRR |
|---|---|---|---|
| 中文核心 | 3/16 | 18.75% | 0.1875 |
| 英文 | 2/2 | 100% | 1.0 |
| 独立扩展 | 0/10 | 0% | 0 |

原始结果与 fixture/实际导入源码哈希见 [retrieval-baseline.json](retrieval-baseline.json)。完整跨块事实也是 Q13 的成功条件，不能只凭 Memory ID 命中通过。无资料组记录返回来源，但单独的检索结果不是模型幻觉或回答忠实度证据。

复现（从目标 worktree 的 backend 执行）：

```bash
PYTHONPATH=.:tests FG_RAG_PROBE_REPORT=/path/to/report.json .venv/bin/python -m pytest -p conftest -p no:cacheprovider -q -s --tb=short /path/to/research/test_retrieval_probe.py
```

本次预期红测为 `1 failed in 1.21s`，失败点明确是核心验收 `3 == 16`，不是环境、导入或 fixture 异常。B 实施后将同一数据与脚本对最终 ContextBuilder 重跑；若新增必需 attempt 参数，只适配真实调用合同，不改问题/资料/期望值。后续仍需独立权限、异常查询、扫描界限和引用链回归，本基线不替代它们。
