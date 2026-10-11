# 设计：授权模型统一（U1）

## 1. 现状与缺陷

`services/visibility.py` 已有完整模型：

- 四级阶梯 `self_private > household_detail > lineage_summary > none`（`_LEVEL_ORDER`）
- `evaluate(session, actor, target, space_context, purpose)` → `VisibilityDecision(level, fields, purpose, source)`
- `fields: dict[str, str]` 覆盖 `PROFILE_FIELDS = BASELINE_FIELDS(id,name) + CONTENT_FIELDS(gender,birth,death,bio,avatar_path) + META_FIELDS`
- 掩码规则：`none` 全遮；`lineage_summary` 只清 `CATEGORY_FIELD_MAP` 里已披露类别对应字段（`pending` 源不享受披露扩展）；`household_detail`/`self_private` 清全部 `CONTENT_FIELDS`
- **未成年人 overlay 最后收紧**：非 `self_private` 且 `is_minor(target)` → 移除 `MINOR_OVERLAY_FIELDS = (birth, death, bio)`
- `HIGH_RISK_CATEGORIES = (health, address, school, contact, private_notes)`：注释写明"不因 lineage/household/Agent/operator 身份自动开放"

缺陷：**这套掩码到不了 RAG 语料文本**。`memory_sources._author_visible` 只把 `evaluate` 当"作者可见/不可见"一道门用；读取路径用 `RAGDocument.scope` + 空间成员，与四级阶梯**不是同一套**。

`RAGDocument.visibility_snapshot`（JSON，`default=dict`）与 `visibility_snapshot_key`（String）**已存在但全树恒为 `{}` / 占位字符串**——没有任何语义。这就是本任务的接缝：列名本身已说明用途，无需迁移。

## 2. 关键判断：掩码只约束"关于他人"的内容

**scope 是主体自己选的；字段掩码管的是主体没选择的可见性。**

- `memory`：用户在确认候选时**显式选了 scope**（private/household/lineage）。因此对该条记忆，**scope 就是授权**，掩码不应再收紧。语料矩阵任务也明确要求 `memory` **冻结、不改行为**。
- `authorized_document`（10-11 在建）：内容取自**某人档案附件**，被授权给某空间；**空间成员读它时，主体没有逐条选择可见性**。这里必须由主体的四级阶梯裁决。

因此引入 **`subject_user_id` 作为 opt-in 标记**：

| `subject_user_id` | 语义 | 行为 |
|---|---|---|
| `NULL` | 内容不是"关于某个具体人的档案派生" | **保持现状**：scope + 空间成员裁决。`memory` / `public_kinship` 走这条 |
| 非 `NULL` | 内容派生自该主体的档案 | **主体阶梯裁决**：字段掩码 + 未成年人 overlay + HIGH_RISK 自限 |

这同时满足两个约束：`memory` 行为逐字不变；10-11 拿到它需要的机制。

## 3. 声明的形状

`visibility_snapshot` 承载**读取时所需的最低清空要求**（不是"写入时的快照"，而是"读这条内容需要什么"）：

```json
{
  "subject_user_id": 42,
  "required_categories": ["dates", "school"],
  "key": "profile-v1"
}
```

`visibility_snapshot_key` 与 `key` 同步（`"profile-v1"`），版本化语义：换了判据含义就换 key，旧文档因 key 不符而**不可读**（fail closed），而不是按新语义重解释。

`_check_document_metadata` 已对 `metadata` 的**每个键**做等值比较，所以这两个字段自动进入文档身份校验——同 `(source_type, source_id, revision)` 但声明不同 → `_index_conflict()`，不会静默覆盖。

## 4. 判据（新增在 `visibility.py`，单一真源）

```python
def required_clearance_satisfied(
    decision: VisibilityDecision, required_categories: Sequence[str]
) -> bool:
    """读一条声明了 required_categories 的内容，是否被该 decision 允许。

    两条规则，都刻意保守：
    1. HIGH_RISK 类别**只有主体本人**（self_private）可读 —— 与
       HIGH_RISK_CATEGORIES 的既有注释一致（不因 lineage/household/Agent 身份开放）。
       它们的 CATEGORY_FIELD_MAP 条目当前是空占位，所以不能用"字段是否清空"表达。
    2. 其余类别要求其映射字段全部 FIELD_CLEAR。未成年人 overlay 已在 evaluate 内
       移除 birth/death/bio，因此这里自动生效，不需要第二处判断。
    未知类别按 HIGH_RISK 处理（fail closed）：无法分类的内容只有主体可读。
    """
```

`unknown → HIGH_RISK` 是刻意的 fail-closed 默认：分类不了的内容不能被"顺手"放开。

## 5. 施加点：投影复核层

掩码判据落在 `memory_sources._can_read_document`（**投影复核层**），不落在 `_ELIGIBILITY_SQL`：

- SQL 层继续做**粗筛**（scope 允许集、来源类型、取代/有效区间）——它必须是可索引的；
- 复核层做**细判**（字段清空、阶梯、overlay）——它需要 `evaluate`，而 `evaluate` 要读关系、成员、披露记录。

这正是既有的**两层各自独立挡住**设计：任何一层单独失效都不得让内容漏出。把 `evaluate` 塞进 SQL 会把细判降级成粗筛，反而更弱。

复核层需要读者身份：`document_readable(...)` 已接收 `actor` / `account` / `space_id`，新增可选参数 `purpose` 与 `agent_kind`（后者已存在）。

## 6. 写入侧

`ingest_authorized_document` 增加两个可选参数：

```python
subject_user_id: int | None = None,
required_categories: Sequence[str] = (),
```

- 传了 `subject_user_id` → 写 `visibility_snapshot = {"subject_user_id":…, "required_categories":[…], "key":"profile-v1"}` 且 `visibility_snapshot_key = "profile-v1"`。
- 未传 → `{}` + 调用方给的 `visibility_snapshot_key`（**现状不变**）。
- `required_categories` 校验值域：必须是 `CATEGORY_FIELD_MAP` 的键；未知 → 422（写入侧严格，读取侧保守，与 scope 校验同口径）。

10-11 的附件授权路径传 `subject_user_id = 附件所有者`、`required_categories = 由内容种类派生`。

## 7. 不变量

- `memory` 与 `public_kinship` 的 `visibility_snapshot` 保持 `{}` → **读取行为逐字不变**（有回归测试钉住）。
- `visibility.py` 仍是唯一授权真源；RAG 层不复制阶梯、不复制 `HIGH_RISK_CATEGORIES`、不复制 overlay 字段。
- 两层独立挡住的设计不变。
- 无迁移：复用既有列。

## 8. 验收

- [ ] `memory` / `public_kinship` 读取行为不变（既有 RAG 测试全绿）。
- [ ] `subject_user_id` 非空且 `required_categories` 含 HIGH_RISK → 非本人读不到（含 mutation：去掉自限后测试失败）。
- [ ] 主体为未成年人时，`birth`/`death`/`bio` 相关类别对非本人不可读（overlay 自动生效）。
- [ ] 未知类别 → 只有本人可读。
- [ ] `visibility_snapshot_key` 不符 → 不可读（fail closed），不按新语义重解释。
- [ ] 同 `(source_type, source_id, revision)` 但声明不同 → `_index_conflict()`。
- [ ] `ruff` / `mypy` / `pytest` 通过。
