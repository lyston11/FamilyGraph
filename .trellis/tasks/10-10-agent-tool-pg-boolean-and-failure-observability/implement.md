# 实施与验收记录

## 1. 根因

```
sqlalchemy.exc.ProgrammingError: (psycopg.errors.UndefinedFunction)
  operator does not exist: boolean = integer
  LINE 1: ...WHERE id = $1 AND status = 'running' AND cancel_requested = 0
```

`agent_runs.cancel_requested` 在 PostgreSQL 上是 `boolean`；SQLite 无严格类型（存 0/1），
所以 `= 0` 在 SQLite 上通过全部测试，切到 PG（2026-10-08）当天起**每一次 agent 工具
调用都 500**。

## 2. 变更

| 文件 | 变更 |
|---|---|
| `services/agent_tools.py` | 准入 CAS `cancel_requested = 0` → `= FALSE`；新增 `except HTTPException: raise` 与 `except Exception` 兜底；新增 `_record_tool_failure()`；新增模块 logger |
| `services/provider_proxy.py` | provider 准入 CAS `= 0` → `= FALSE` |
| `tests/test_sql_portability.py` | 新增结构性守卫：动态收集 `Mapped[bool]` 列名，扫描 raw SQL 里的 `列 = 0\|1` |
| `tests/test_agent_tools.py` | 新增 `test_unexpected_dispatch_failure_is_audited_and_typed` |

### 为什么 `except HTTPException: raise` 是必须的

`raise_api_error` 用 `HTTPException` 传递**全部**领域错误（404 `FG_PROFILE_NOT_AVAILABLE`、
409 去重冲突、403 scope 不匹配……）。兜底分支若不排除它，会把每一条领域错误都变成通用
500。这是引入兜底时**实际踩到的回归**（全量测试报出 4 处 `AGENT_TOOL_EXECUTION_FAILED`
替换了 `FG_PROFILE_NOT_AVAILABLE` 等既有错误码），已修正并有既有测试覆盖。

### 失败留痕为何进 `audit_log` 而非 `agent_tool_calls`

`agent_tool_calls` 是**副作用去重台账**（同 `(run_id, tool_call_id)` 至多一行），且它在
准入 CAS **之后**才写入——CAS 自身抛异常时根本无法插入。失败调用没有副作用，语义上
也不属于该表。因此与既有的 `agent_tool_denied` 同路，进 `audit_log`。

detail 只含 `tool` / `version` / `error_class` / `tool_call_id` / `agent_kind` / `space_id`，
**不含** 异常 message、SQL 文本或参数（可能含数据）。审计写入自身失败只记日志，
不掩盖原异常。

## 3. 验证

### 3.1 静态守卫的 mutation 验证

把 `agent_tools.py` 的 `= FALSE` 改回 `= 0` 后：

```
E  AssertionError: 发现 raw SQL 把 boolean 列与整数比较。…
E      app/services/agent_tools.py:743  cancel_requested = 0
```

守卫抓住缺陷并指出行号。还原后通过。

### 3.2 真实 PostgreSQL 上的 SQL 验证（开发库 `fg-dev-pg`）

```
FAIL  旧：cancel_requested = 0            operator does not exist: boolean = integer
OK    新：cancel_requested = FALSE        rowcount=0
FAIL  provider 旧                         operator does not exist: boolean = integer
OK    provider 新                         rowcount=0
```

### 3.3 端到端：真实 steward run 的工具调用恢复

部署到开发环境（`git pull` + 重启 `familygraph-api`）后触发 steward 扫描：

```
agent_tool_calls（2026-10-10 13:56，run 1270）：
  9682  familygraph.steward.get_relationship_path
  9681  familygraph.steward.get_evidence
  9680  familygraph.steward.list_space_nodes
  9679  familygraph.steward.get_space_snapshot
```

**这是 2026-10-07（run 1168）以来第一批成功的 steward 工具调用**。
随后 8 分钟内 ranking run 又成功调用 7 次。`boolean = integer` 计数为 **0**。

### 3.4 回归

| 检查 | 结果 |
|---|---|
| `pytest`（含与并行任务的合并结果） | **2400 passed, 38 skipped** |
| `ruff check` / `ruff format --check`（本次改动文件） | 通过 |
| `mypy app` | No issues found |

## 4. 一个被排除的"疑似缺陷"

服务层直调 `steward_tools.execute_steward_tool(TOOL_GET_SPACE_SNAPSHOT, ...)` 曾报
`Object of type datetime is not JSON serializable`。经核对这**不是**产品缺陷：HTTP 边界
在返回前会 `jsonable_encoder(output)`（`agent_tools.execute` 内），是我的探针绕过了该层。
端到端验证中 `get_space_snapshot` 经 HTTP 成功即为证据。

## 5. 未解决（属于第二层，本任务范围外）

`familygraph.steward.search_memory` 在空间 1 已广告（run 1256 的 allowlist 含它），
但**尚未观察到任何一次调用**。注意：空间 1 目前只产生 `candidate` run，而 `candidate`
本就不调用工具（历史上调用工具的是 `ranking`/`terminology`）。因此这**不能**证明
"模型不会用记忆"，只能说明现有证据不足。

第二层（steward prompt 与工具集的关系、`prompt_digest` 契约、投影范式）需要单独决策，
不在本任务内改动。

## 6. 生产影响与部署

- 生产 `familygraph-prod-api-1` 近 72h 有 **2324 次**同一条 SQL 失败，
  最后成功的 steward 工具调用停在 2026-10-07 → **生产同样受影响**。
- 本任务**未部署生产**（生产是 Docker Compose 栈，部署需走 `scripts/deploy-prod.sh`，
  属独立操作）。
- 修复仅涉及后端两个文件，无迁移、无契约变更；sidecar 无需重建。
