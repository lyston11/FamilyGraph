# 实施记录：授权模型统一（U1）

## 交付

| 文件 | 变更 |
|---|---|
| `services/visibility.py` | 新增 `PROFILE_CLEARANCE_KEY` / `required_clearance_satisfied` / `profile_clearance_snapshot` |
| `services/memory_sources.py` | 新增 `_profile_clearance_ok`，接入 `_can_read_document` |
| `services/memory_rag.py` | `ingest_authorized_document` 增加 `subject_user_id` / `required_categories` |
| `tests/test_rag_profile_clearance.py` | 新增 9 条 |
| `.trellis/spec/backend/memory-rag-execution-contract.md` | 新增 §12 |

**无迁移**：复用既有但此前恒为 `{}` 的 `RAGDocument.visibility_snapshot`（JSON）与
`visibility_snapshot_key`（String）。

## 实施中的两处关键发现

### 1. `subject_user_id` 必须是 opt-in，不能对所有语料生效

原设计打算让字段掩码约束全部 RAG 语料。核对后否决：**scope 是主体自己选的，字段掩码管的是
主体没选择过的可见性。**

- `memory`：用户在确认候选时**显式选了 scope**，再叠加掩码等于推翻主体的选择。语料矩阵任务也
  明确要求 `memory` 冻结、不改行为。
- `authorized_document`：内容取自他人档案附件，读它的空间成员**没有逐条选择可见性**——这里必须
  由主体阶梯裁决。

因此以 `subject_user_id` 非空作为 opt-in 标记。`memory` / `public_kinship` 保持 `{}`，
读取行为逐字不变（有测试钉住）。

### 2. RAG 的字段清空实际来自**披露**，不是成员身份

实现后发现 `_PURPOSE_LEVEL_CAP[PURPOSE_RAG] = LEVEL_LINEAGE_SUMMARY` 把 RAG 层级封在
`lineage_summary`，而该层只清 `disclosed_categories` 映射到的字段——**不是** `household_detail`
那样清全部 `CONTENT_FIELDS`。

这不是障碍，而是正确的语义：**空间成员身份本身不足以读别人档案派生的内容，必须主体已披露该
类别。** 披露系统由此第一次真正接到语料文本上。测试相应改为「未披露不可读 → 披露后可读」，
并在 `_disclose` 的 docstring 里写明原因。

## 验证

| 项 | 结果 |
|---|---|
| 新增测试 | 9 passed |
| **mutation 1**：去掉 `required_clearance_satisfied` 的 HIGH_RISK 自限 | **2 failed**（HIGH_RISK 与未知类别两条） |
| **mutation 2**：去掉复核层的 `_profile_clearance_ok` 调用 | **5 failed** |
| 全量 | **2417 passed / 38 skipped** |
| `ruff check` / `ruff format --check` / `mypy app` | 通过 |

`ruff format --check app/` 另报 3 个文件（`capacity_bootstrap.py` / `redis_accel.py` /
`writer_epoch.py`）需格式化——**在 `main` 上即如此，非本任务引入**，未触碰。

## 与 `10-11-attachment-content-agent-grant` 的接口

该任务正在另一 worktree 建 `authorized_document` 的第一个真实写入方。它需要的两个参数已就绪：

```python
memory_rag.ingest_authorized_document(
    ...,
    subject_user_id=<附件所有者 user_id>,
    required_categories=<由内容种类派生的披露类别>,
)
```

**需要它对齐的一点**：`required_categories` 的值域是 `visibility.CATEGORY_FIELD_MAP` 的键
（`avatar/photos/dates/bio/attachments/health/address/school/contact/private_notes`）。
附件内容若含学校/住址/健康/联系方式，必须声明对应的 HIGH_RISK 类别——否则内容对空间成员开放，
而它本该只有主体可读。

## 未做（属后续子任务）

- U2 统一检索入口：本任务只把授权判据统一，未收拢五条读路径。
- U3 预算与重要性衰减。
- U5 维护循环。
- U6 稳定前缀。

父任务：`.trellis/tasks/10-11-unified-knowledge-pipeline/prd.md`。
