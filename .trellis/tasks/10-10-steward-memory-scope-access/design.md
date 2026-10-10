# 设计：管家记忆读取与 RAG 空跑修正

## 1. 配置表征（两层交集 + 部署上界）

### 1.1 表示

单列字符串 `steward_memory_scopes`，规范化编码：`MEMORY_SCOPES` 的子集，按 `MEMORY_SCOPES`
声明顺序去重、逗号分隔；空串 = 空集。选择单列而非三个布尔列的理由：语义本就是**集合**（"允许哪些级别"），
未来新增 scope 不需要再加列；且交集运算是集合运算，不是布尔与。

- 平台：`PlatformFeatureConfig.steward_memory_scopes`，`TEXT NOT NULL DEFAULT ''`
- 空间：`AgentSpaceProviderSetting.steward_memory_scopes`，`TEXT NOT NULL DEFAULT ''`
- 部署上界：`config.STEWARD_MEMORY_SCOPES`（env，默认空）

### 1.2 有效值

```text
effective = env_scopes ∩ platform_scopes ∩ space_scopes
```

三层都用交集，任一层为空即整体为空。env 为空 = 部署级关闭（沿用
`platform_features._steward_assist_effective` 的 kill-switch 语义：env 关 → 一律关）。

默认三层全空 → `effective = ∅` → steward 读不到任何记忆，**行为与改动前逐字等价**。

### 1.3 新增模块

`backend/app/services/steward_memory.py`：

```python
SCOPES = MEMORY_SCOPES  # 值域真源，不复制

def normalize_scopes(raw: str | None) -> tuple[str, ...]: ...   # 解析 + 校验 + 规范化
def encode_scopes(scopes: Iterable[str]) -> str: ...             # 规范化编码（存库）
def platform_scopes(db) -> tuple[str, ...]: ...
def space_scopes(db, space_id: int) -> tuple[str, ...]: ...
def effective_scopes(db, *, space_id: int) -> tuple[str, ...]: ...          # 三层交集
def readable_scopes(db, *, space_id: int, viewer_account_id: int | None) -> tuple[str, ...]:
    """在 effective 之上施加 R2：无 viewer 时去掉 private。"""
```

非法 scope 值：读取时**忽略未知项并告警**（fail-closed，不因脏配置放开）；写入时 422。

## 2. 检索层：单份 eligibility + 显式 scope 允许集

### 2.1 改动点

`memory_rag._ELIGIBILITY_SQL` 的 private 分支由

```sql
(d.scope = 'private' AND d.author_account_id = :account_id AND :is_assistant = 1)
```

改为

```sql
(d.scope = 'private' AND d.author_account_id = :private_reader_account_id)
```

并新增一道前置的显式允许集条件：

```sql
AND d.scope IN :allowed_scopes
```

绑定值：

| 调用方 | `allowed_scopes` | `private_reader_account_id` |
|---|---|---|
| assistant（现有路径） | `("private","household","lineage","public")` | `account.id` |
| steward 工具 | `readable_scopes(...)`（可能为 ∅） | `attempt.viewer_account_id`（空间级 kind 为 `None`） |

**为什么这样等价且更安全**：assistant 路径过去靠 `is_assistant = 1` 放行 private，
现在改为「显式允许集包含 private **且** `author_account_id` 等于该调用方的 account」。
assistant 仍只传自己的 account，故语义逐字不变；steward 无法再靠"换个 account 参数"
读到别人（或管理员）的私有记忆——`private_reader_account_id` 只能来自 fenced attempt 的
`viewer_account_id`，空间级 kind 恒为 `NULL`，而 `author_account_id = NULL` 恒假。

**只保留一份 SQL**：取代/有效区间/敏感度谓词仍只有 `_ELIGIBILITY_SQL` 一处真源，
新增的是两个绑定参数，不是第二份副本。

`public` 分支继续保留 `:is_assistant = 1`（steward 不得读无限制公开材料）。

### 2.2 `search_rag` 签名

新增两个关键字参数（默认值保持既有行为，避免影响任何现存调用方）：

```python
allowed_scopes: Sequence[str] | None = None,          # None = 不施加允许集（等价现状）
private_reader_account_id: int | None = None,         # None = 回落到 account.id
```

### 2.3 身份选择（已知近似，需在代码注释中写明）

household/lineage 分支沿用既有的 `space_members` active 成员 EXISTS，`:user_id` 取：

- 有 viewer 的 kind（`terminology`）→ viewer 本人；
- 空间级 kind → space admin（`_steward_run_context` 既有的回落逻辑）。

steward 的真实授权根是 run fence（服务端自己的、绑定到该空间的 agent），
成员 EXISTS 在此是"空间处于活跃状态"的代理判据，不是授权来源。viewer 在 run 期间
被移出成员时该分支 fail-closed，这是期望行为。

## 3. steward 工具

### 3.1 注册与广告

- canonical name：`familygraph.steward.search_memory`，version 1，`required_kind="steward"`。
- 输入 schema：`query`（string，maxLength 500，required）、`limit`（integer，1..10）；
  `additionalProperties: false`。显式拒绝 `space_id`/`account_id`/`viewer_account_id`/
  `scope`/`run_id`/`attempt_id`（这些全部来自 fenced run）。
- 描述必须写明「只读」——`agent/test/tools.test.ts` 对 steward 工具断言 `description` 含「只读」。
- `agent_tools.default_allowlist`：`readable_scopes(...) == ∅` 时**不加入** allowlist
  （与 `search_memory`/`propose_memory` 在 memory/rag 未启用时不广告同口径）。
- sidecar `agent/src/tools.ts`：加入 `STEWARD_TOOL_NAMES` 与 `TOOL_VERSIONS`；
  与后端 canonical name 逐字一致（`agent/test/tools.test.ts` 的 `STEWARD_CONTRACT` 需同步）。

### 3.2 执行

`steward_tools.execute_steward_tool` 新增分支（保持"steward 工具分派集中在 steward_tools.py"
的形状），内部：

1. `readable_scopes(db, space_id=execution.space_id, viewer_account_id=<attempt viewer>)`
2. 空集 → 返回结构化拒绝（`TOOL_SCOPE_DENIED` 语义）；不静默返回空列表。
3. 解析 actor/account：viewer 优先，否则 space admin（与 `_steward_run_context` 同一逻辑，
   抽成 `steward_memory.resolve_reader(db, *, space_id, viewer_account_id)` 复用）。
4. `memory_rag.search_rag(..., agent_kind="steward", allowed_scopes=scopes,
   private_reader_account_id=viewer_account_id, limit=..., for_model=True)`。
5. 输出只含句柄与**截断摘要**（复用 `search_memory` 的既有输出形状与长度上限），
   不返回 chunk 全文；再经 `policy_guard.tool_result_hook`。

### 3.3 为什么是工具而不是投影

见 PRD「为什么不能把记忆内联进 prompt」：投影内联会让 `input_hash` 随记忆变化而抖动，
抬高发送前 fence 拒绝率；工具结果不进静态 prompt，digest 契约不受影响。

## 4. RAG 空跑与审计修正

### 4.1 现状缺陷

`_steward_run_context` 执行完整 RAG 检索（含候选收集、评分、`ContextBuild` 与
`ContextBuildItem` 写入），随后用 `_steward_projection_blocks` 覆盖 `context_blocks`，
检索结果被丢弃。后果：白跑一次检索；`ContextBuildItem.included=True` 声称纳入了模型
从未收到的内容。

### 4.2 修法

`ContextBuilder.build()` 已有 `prefetched` 参数：非 `None` 时**不再调用 `search_rag`**，
直接采用给定 sources。因此：

- steward 路径改为传入 `prefetched=(<steward 投影 ContextSource>,)`；
- `context_blocks` 改为由 `built.included` 派生（`[s.as_data_block() for s in built.included]`），
  与 `ContextBuildItem` 同源，消除两处各自构造的漂移；
- 投影块的 `ContextSource` 字段取自既有块字典（`source_type="steward_projection"`、
  `source_id=f"attempt:{id}"`、`citation_handle=prompt_digest`、`revision=attempt_no`、
  `sensitivity="normal"`、`trust="untrusted_data"`、`text=user_content`）。

投影块 `sensitivity="normal"`，因此 `high`/`local_required` 的本地 Provider 门禁不受影响。

### 4.3 不变量

- `prompt_digest` / `input_hash` 的取值与计算方式**不变**（仍由 `_user_content_for` 决定）。
- 实际发送的 prompt 仍等于 digest 描述的那段（`test_the_sent_prompt_is_the_one_the_digest_describes` 必须继续通过）。
- assistant 路径不进入 `prefetched` 分支。

## 5. 迁移 0060

```text
0060_steward_memory_scopes
  platform_feature_configs        + steward_memory_scopes TEXT NOT NULL DEFAULT ''
  agent_space_provider_settings   + steward_memory_scopes TEXT NOT NULL DEFAULT ''
```

- 只加列、带默认值：`upgrade head` 不改变任何既有行为。
- 降级：drop 两列。语义丢失（配置回落到默认关），需在迁移 docstring 说明，
  且**不得**把已生效的配置伪装成"仍然生效"。
- 不设 CHECK 约束（值域校验在服务层，便于未来扩展 scope 而不改 schema）。

## 6. 管理面

- 平台级：`GET/PUT /admin-api/v1/platform-features`（`admin_platform_features.py`）
  增加 `steward_memory_scopes`（读写）+ `steward_memory_scopes_source`（读）。
- 家庭端只读：`GET /api/platform-features`（`PlatformFeatureFlagsOut`）不暴露该字段
  （与 memory/rag 开关的暴露口径一致，避免泄露平台配置细节）。
- 空间级：`GET/PUT /api/spaces/{space_id}/model-settings`（`space_model_settings.py`）
  增加 `steward_memory_scopes`（读写，space admin）+ `steward_memory_scopes_effective`（读）。

## 7. 风险与已否决方案

| 风险 | 处置 |
|---|---|
| 误把 `is_assistant` 直接放开 → 空间级 kind 读到 space admin 的私有记忆 | 改为显式 `private_reader_account_id`，空间级 kind 恒 `NULL` |
| 配置写成脏值（未知 scope） | 读取时忽略未知项并告警；写入时 422 |
| 记忆进入静态 prompt 破坏 digest/fence 稳定 | 已否决（用户明确否决）；改用工具 |
| 两份 eligibility SQL 漂移 | 只加绑定参数，保持单份 SQL |
| 工具成为跨 viewer 读取通道 | 作用域只来自 fenced attempt；输入 schema 拒绝一切身份字段 |

**已否决**：把记忆内联进 `_user_content_for`（`input_hash` 抖动）；
给 steward 开放 `public`（无限制公开材料，非个性化所需）；
为 steward 复制一份 eligibility SQL。
