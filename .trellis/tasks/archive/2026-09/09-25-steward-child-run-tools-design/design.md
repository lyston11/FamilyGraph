# S3 Steward 只读工具集技术设计

## 1. 边界与数据流

```text
StewardModelCall / AgentRun token claims
  → ContextOut + tool_allowlist
  → Pi adapter 注册 Steward 工具
  → provider wire name (familygraph_*)
  → internal /runs/{id}/tools/{canonical}/execute
  → reject-user-jwt + decode run token
  → StewardExecution fence
  → registry/schema/allowlist
  → steward_tools dispatch (纯 SELECT)
  → output policy guard + safe audit
  → Pi tool result
```

内部工具执行不走浏览器 API，也不把 Steward 伪装成 Assistant session。`AgentRun.session_id` 对 Steward 始终为 NULL；scope 由 run token 和 attempt 记录确定。

## 2. 工具合同

使用独立的 Steward canonical names，避免 Assistant 新增工具意外继承：

| 类别 | 工具 | 输入 | 输出核心 | scope |
|---|---|---|---|---|
| space | `familygraph.steward.get_space_snapshot` | `{}` | `space_id`、publication/generation revision、有限 node/evidence counts、policy version | token `space_id` |
| space | `familygraph.steward.list_space_nodes` | `{cursor?, limit?}` | 代号、稳定 user id、可用状态；不含原始个人字段 | token `space_id` |
| viewer | `familygraph.steward.get_viewer_target` | `{target_user_id}` | 当前 viewer 对目标的 ready/unavailable 投影、path/term 状态、revision | token `space_id + viewer_account_id` |
| viewer | `familygraph.steward.get_viewer_term` | `{root_user_id, target_user_id}` | 当前 viewer 的 term projection 状态、term source/revision/reason code | token `space_id + viewer_account_id` |
| evidence | `familygraph.steward.get_evidence` | `{target_user_id, evidence_ids?}` | 当前发布视图允许的结构化 relation/fact evidence id/type/revision/direction | token `space_id + steward_attempt_id` + published view |
| evidence | `familygraph.steward.get_relationship_path` | `{from_user_id, to_user_id}` | 已确认/已发布路径、每跳证据引用、算法/revision | token `space_id`; 若带 viewer 则再受 viewer view 限制 |

工具输入 schema 只包含业务查询字段；不接受 `space_id`、`account_id`、`viewer_account_id`、`agent_kind`、`run_id`、`attempt_id`、`provider`、`policy_version` 或任意额外字段。数值和数组有严格上限。

`get_viewer_target` 与 `get_viewer_term` 只有 token 带 viewer 时可用；没有 viewer 时返回固定 `STEWARD_VIEWER_SCOPE_UNAVAILABLE`，不返回目标身份或是否存在。对外需要把不可见/不存在/未发布统一为安全的 unavailable 枚举。

## 3. 后端分层

### 3.1 registry

在 `app/services/agent_tools.py` 保留单一 registry/版本/schema/required_kind 来源，新增 Steward specs 与 `STEWARD_TOOL_NAMES`。现有 Assistant query/Web/diagnostic specs 不改 required kind。allowlist 生成函数按 `agent_kind` 取集合，`open_child_run` 从该函数注入 token 与 LeaseOut。

### 3.2 `steward_tools.py`

新建领域只读服务，依赖现有 model/projection/read helpers，不反向导入 agent_tools。入口接收：

```python
execute_steward_tool(
    db, *, run, attempt, claims, name, input_payload
) -> dict[str, Any]
```

入口先解析 scope，再按工具分派。所有查询限定 `space_id`；viewer 工具使用 `viewer_account_id`，证据工具使用 `steward_attempt_id` 与 attempt 的 prompt/input/evidence binding。输出通过共享有界 JSON sanitizer，禁止返回 ORM。

优先复用：`StewardPublication`、`StewardGeneration`、`StewardGenerationView`、`StewardViewTarget`、`StewardTermProjection`、现有 `steward_snapshot`/`steward_views` 和关系确定性解析器。不能证明属于当前发布/attempt 的行一律不返回。

### 3.3 internal API

扩展 `agent_tools.execute`/`_dispatch` 接受 `agent_session: AgentSession | None`，按 `run.kind` 分派：

- Assistant：保留当前 session/`ExecutionIdentity` 路径，行为不变。
- Steward：先由 endpoint 完成 `_authorize_steward_run` 与 `StewardExecution` fence，再进入 `steward_tools`；不调用 `_resolve_scope` 的 Assistant 路径。

`execute_tool` 在调用统一 `policy_guard.tool_call_hook` 前后保持现有 cancel/allowlist/result guard。Steward 工具失败写安全审计，审计仅包含 tool、code、run/attempt/space 关联 id，不含原始 payload。

### 3.4 child-run allowlist

在创建/租约 Steward child run 时由 registry 生成只读 Steward allowlist，写入 token claim 和 context projection。未启用工具时不能发非空 allowlist；双开关关闭行为不变。sidecar adapter 只注册上述 Steward names。

## 4. sidecar 设计

`agent/src/tools.ts` 增加 Steward TypeBox schema 与声明，`toolNamesFor("steward")` 返回独立集合。Assistant 集合保持原样，结构断言保证两集合无交集。provider wire mapping 继续由单一 mapping 负责 canonical↔wire。

Steward 工具描述要求模型：只读、不得编造未返回事实、遇到 unavailable/insufficient evidence 必须说明不确定性、不得把证据 id 当作事实本身。sidecar 不执行本地授权，不读取数据库，不缓存跨 run tool result。

## 5. 安全矩阵

| 场景 | 结果 |
|---|---|
| Assistant token 调 Steward run/tool | 404/401 安全拒绝，不执行 |
| Steward token 调 Assistant-only tool | 403/404 + audit |
| Steward token tool 不在 allowlist | 403 + audit |
| 输入含 actor/space/viewer scope | 422 schema reject |
| space 不同 | 安全 unavailable/404，不泄露存在性 |
| 缺 viewer 调 viewer 工具 | 403 `STEWARD_VIEWER_SCOPE_UNAVAILABLE` |
| viewer inactive/revoked | 403/安全 unavailable，不读 projection |
| evidence revision/attempt 不匹配 | 安全 unavailable 或 409，零返回 |
| tool 试图写入 | registry 不注册；代码结构/测试禁止 |
| user JWT 调 internal endpoint | 403，先于 kind 探测 |

## 6. 兼容与回退

- `FG_AGENT_ROLE=assistant` 时 Assistant 现有工具完全不变。
- `STEWARD_PI_RUNTIME_ENABLED=0` 时不会有 Steward child run；新工具注册不改变 in-process carrier。
- 不新增迁移；若现有 projection 缺行，工具返回 unavailable 而不是从私人数据或其他空间补齐。
- 不包含 S5 删除 in-process 路径、web 工具开放、写入工具、前端改动或生产配置切换。

## 7. 验证设计

后端：registry 快照、tool schema、kind/allowlist、Steward execute 授权、space/viewer/evidence 对抗、零写入、日志脱敏、revision fence、internal HTTP。

sidecar：adapter 集合、TypeBox schema 快照、wire name round-trip、unknown/Assistant tool rejection、工具结果错误传播。

变异保护：移除 required kind、scope filter、viewer membership、published-generation filter、attempt binding 或 allowlist 校验时，至少一个对抗测试失败。
