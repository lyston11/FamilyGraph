# 修复 PG boolean 缺陷并让工具执行失败可观测

## Goal

1. 修复 PostgreSQL 上**所有 agent 工具调用**（assistant + steward）恒失败的类型错误。
2. 让工具执行失败**留下持久记录与结构化错误码**，使同类问题不再静默潜伏。

## Background（真实事故）

### 缺陷 1：boolean 列与整数比较（生产级、静默）

```
sqlalchemy.exc.ProgrammingError: (psycopg.errors.UndefinedFunction)
  operator does not exist: boolean = integer
  LINE 1: ...WHERE id = $1 AND status = 'running' AND cancel_requested = 0
  SQL: UPDATE agent_runs SET updated_at = updated_at
       WHERE id = :run_id AND status = 'running' AND cancel_requested = 0
```

`agent_runs.cancel_requested` 在 PostgreSQL 上是 `boolean`；SQLite 无严格类型（存 0/1），
所以该语句在 SQLite 上通过全部测试，**切到 PG 当天（2026-10-08）就全废**。

两个调用点：

| 位置 | 作用 | 后果 |
|---|---|---|
| `services/agent_tools.py` | 工具调用准入 CAS | **每次工具调用 500** |
| `services/provider_proxy.py` | provider 调用准入 CAS | 同类 |

实测规模：

- 生产 `familygraph-prod-api-1` 近 72h：**2324 次**同一条 SQL 的 `boolean = integer`；
  路径全部是 `/internal/agent/runs/{id}/tools/{tool}/execute`。
- 开发环境（重启后端并重建 sidecar 后）：`get_space_snapshot` / `list_space_nodes` /
  `get_viewer_term` / `get_viewer_target` 全部 500。
- 生产最近一次成功的 steward 工具调用是 **2026-10-07**（run 1168）。

同一缺陷类别此前已在 `memory_rag` 的向量路径出现过（注释见
`services/memory_rag.py`：`实测在 PostgreSQL 上 :is_assistant = 1 触发 boolean = integer`），
当时只修了那一处。

### 缺陷 2：失败不可观测（这才是它能潜伏数天的原因）

工具执行路径的异常处理只有一条：

```python
except ToolProtocolError as exc:      # 已知拒绝：写 agent_tool_denied 审计 + 类型化错误
    ...
```

**任何其他异常直接逃逸**，落到 `app.main` 的 `unhandled_exception_handler`：

| 面 | 现状 |
|---|---|
| 模型 | 收到 errored tool result：`<tool> failed: internal endpoint 500: {"error":{"code":"INTERNAL_ERROR","message":"服务器内部错误"}}` —— 知道失败，但**没有任何可据以纠正的信息** |
| 500 是否重试 | 否。sidecar 只把 `[502,503,504]` 视为 transient |
| 后端日志 | 有：`logger.exception` 的完整 traceback（ERROR 级），但只在 journal |
| 持久记录 | **无**。`agent_tool_calls` 零行——准入 CAS 在写占位行**之前**就抛了 |
| 审计 | **无**。`agent_tool_denied` 只在 `ToolProtocolError` 分支写 |
| 指标/告警 | 无 |

后果：模型静默退化（拿不到工具数据仍产出结果）、run 仍 `succeeded`、
系统外观完全正常。

## Requirements

### R1 两处 SQL 改为方言可移植

- `cancel_requested = 0` → `cancel_requested = FALSE`。
- SQLite ≥3.23 与 PostgreSQL 都支持 `TRUE`/`FALSE` 字面量（Python 3.12 自带的
  sqlite3 ≥3.37），因此两方言都正确，无需方言分派。
- 不改语义：仍然是"未请求取消"的准入判定。

### R2 工具执行失败必须留下持久记录

- 捕获 `ToolProtocolError` **之外**的一切异常（`except Exception`），并且：
  1. `db.rollback()`（原事务已不可用）；
  2. 写 `audit_log` 一行 `agent_tool_failed`，detail 含
     `tool` / `version` / `error_class` / `tool_call_id` / `agent_kind` / `space_id`；
  3. 以 `ToolProtocolError(500, "AGENT_TOOL_EXECUTION_FAILED", ...)` 重新抛出，
     detail 含 `error_class`。
- **不泄露**：detail 不得含 SQL 文本、参数值、异常 message（可能含数据）——
  只放 `error_class`（异常类型名）。
- 审计写入本身失败不得掩盖原异常（`try/except` 包裹并记日志）。

### R3 结构化错误码返回给模型

- 500 + `AGENT_TOOL_EXECUTION_FAILED`（而非通用 `INTERNAL_ERROR`）。
- 保持不可重试（sidecar 只对 502/503/504 重试）：代码缺陷重试无意义。

### R4 结构性守卫：boolean 列不得与整数比较

- 扩展 `tests/test_sql_portability.py`（该文件已是方言可移植性的静态守卫）：
  扫描 `app/**/*.py` 的 raw SQL 字符串，若出现
  `<boolean 列名> = 0|1` 则失败。
- boolean 列名从 `app/models/*.py` 的 `Mapped[bool]` 声明**动态收集**，
  避免手写清单随模型演化而失效。

### R5 回归测试

- 分派期间抛出非 `ToolProtocolError` 异常时：返回 500 + `AGENT_TOOL_EXECUTION_FAILED`，
  且 `audit_log` 有一行 `agent_tool_failed`。
- 准入 CAS 正常路径仍工作（现有工具执行测试覆盖）。

## Acceptance Criteria

- [ ] `agent_tools.py` 与 `provider_proxy.py` 中不再有 `cancel_requested = 0|1`。
- [ ] 新守卫测试能**抓住**原始缺陷（mutation：把 `FALSE` 改回 `0` 后守卫失败）。
- [ ] 非协议异常 → 500 + `AGENT_TOOL_EXECUTION_FAILED`，且 `audit_log` 有
      `agent_tool_failed` 行，detail 无 SQL/参数/message。
- [ ] 开发 PG 上真实 steward run 的工具调用**成功**，`agent_tool_calls` 重新出现行。
- [ ] `cd backend && ruff check . && ruff format --check . && mypy app && pytest` 通过。
- [ ] 生产影响与部署步骤在交付说明中写明（本次不自动部署生产）。

## Constraints

- 不新增迁移（用既有 `audit_log`），避免与并行任务抢迁移序号。
- 不改 `agent_tool_calls` 语义（它是副作用去重台账，失败调用无副作用，不入该表）。
- 不引入指标框架。
- 不触碰生产环境与并行任务的 worktree（`fg-10-10-rag-authorized-corpus-matrix`）。
- 不改 steward prompt / 上下文工程（那是独立的第二层设计问题，需单独决策）。
