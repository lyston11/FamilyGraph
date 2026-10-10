# 管家记忆读取在开发环境的启用与端到端验收

## Goal

在**开发环境**实际启用 `steward_memory_scopes`，并按 `#424` 的四项分列口径验收，不得把任何一项混同为「已生效」。

四项必须**分别**给出证据：

1. **部署有效启用**：配置在真实运行环境中生效（不是「文件里写了」）。
2. **真实模型调用**：steward 真的被调用，且调用中出现了 `search_memory`。
3. **取得合法输出**：模型输出通过 `steward_guard` 校验并落库。
4. **自动写回或可见改善**：产物被自动写回或用户可见结果发生变化。

上游无合格结果时**如实记录阻塞**，不降低校验、不伪造通过。

## 环境事实（2026-10-10 核对）

服务器 `lyston`（`instance-20260530-1507`）上有**两套**环境，不能混：

| | 开发（本任务目标） | 生产 |
|---|---|---|
| 运行方式 | systemd user unit `familygraph-api.service` | Docker Compose `familygraph-prod-*` |
| 代码 | `/home/ubuntu/projects/FamilyGraph`（GitHub 同步，当前 `c18f0e5`） | 镜像 `familygraph-prod-api:0.1.0` |
| 数据库 | 容器 `fg-dev-pg`，`127.0.0.1:55450`，库 `familygraph` | 容器 `familygraph-prod-postgres-1`，库 `familygraph` |
| 环境变量 | `~/.config/familygraph/familygraph.env` | `~/.config/familygraph/familygraph-prod.env` |
| embedding | 容器 `fg-embed`，`127.0.0.1:8091` | 容器 `familygraph-prod-embedding-1` |

**不触碰生产**：本任务只动开发环境。

### 关键状态

- 开发 PG 由 `scripts/migration-proof/pg_baseline_build.py` 建基线（**没有 `alembic_version` 表**），
  现有 schema 等价于 `0058_writer_state`（有 `agent_capacity_counters`、`writer_state`）。
- **缺 0059 与 0060**：`memories` 无 `valid_to`/`superseded_by_id` 等六列；
  两张配置表无 `steward_memory_scopes`。
- `familygraph-api.service` 自 **2026-10-08 06:55:31** 起未重启（`NRestarts=0`），
  运行的是 10-08 的代码；main 已前进到 `c18f0e5`。
- 开发 PG 数据量：51 users / 20 spaces / **0 memories** / 1250 agent_runs。
  → **没有已确认记忆**，必须先造一条，否则第 3、4 项无法验收。
- `familygraph.env` 现有：`MEMORY_ENABLED=1`、`RAG_ENABLED=1`、
  `STEWARD_ASSIST_CANDIDATE=1`、`STEWARD_PI_RUNTIME_ENABLED=1`、`FG_WRITER_STAGE=pg_all`、
  `FG_AGENT_ROLE=both`。

## Requirements

### R1 迁移 0059 + 0060 到开发 PG

- 开发 PG 无 `alembic_version`：先 `alembic stamp 0058_writer_state` 标定已知 schema 状态，
  再 `alembic upgrade head`。
- 两个迁移都是纯加列（0059 六列可空；0060 两列 `NOT NULL DEFAULT ''`），
  不改任何现有行语义，`upgrade` 不改变检索行为。
- 不触碰生产 PG。执行前先备份开发库。

### R2 重启开发 API 服务，使 main 代码生效

- `systemctl --user restart familygraph-api.service`。
- 重启后 `/api/ready` 必须 200 且 `capacity_ready: true`（`pg_all` 阶段下计数行缺失
  会让配额完全失效，就绪探针必须失败——这条是既有的承重合同）。
- 确认新代码生效：`GET /api/spaces/{id}/model-settings` 返回
  `steward_memory_scopes_effective` 字段。

### R3 分层启用（先只开 `household`）

- 平台层：`PUT /admin-api/v1/platform-features` 设 `steward_memory_scopes="household"`。
- 空间层：`PUT /api/spaces/{id}/model-settings` 设 `steward_memory_scopes="household"`。
- **部署层 env 也要开**：`STEWARD_MEMORY_SCOPES` 是三层交集的一员，env 为空则整体为空。
  需加入 `familygraph.env` 并重启。
- 启用后必须核对 `steward_memory_scopes_effective == "household"`（而不是只看写入成功）。
- **`private` 本任务不启用**（观察期后再定）。

### R4 造一条已确认的空间级记忆

- 走应用自身的流程（`propose_candidate` → `confirm_candidate`，或经 API），不用裸 SQL。
- scope = `household`，`confirmation_status = confirmed`，并确保 RAG 索引已建
  （`RAGDocument`/`RAGChunk` 存在且 `status='active'`）。
- 内容要能被一个自然语言查询命中，且与查询**零字面重合**不足以证明，故用可控的中文短句。

### R5 触发真实 steward run 并核对四项

- 触发一个 steward attempt（`candidate`/`ranking`/`explanation` 是空间级；`terminology` 带 viewer）。
- 从 `StewardModelCall` 与 sidecar 日志核对：
  - run 的 `tool_allowlist_json` 含 `familygraph.steward.search_memory`；
  - 模型真的调用了它（sidecar 侧 tool call 记录 / 后端 `agent_tool_call` 审计）；
  - 输出通过 `steward_guard` 并落库；
  - 产物被写回且用户可见。
- 若上游无合格结果（例如模型不调用工具、或输出不合法），**如实记录阻塞**。

## Acceptance Criteria

- [ ] 开发 PG `alembic current` = `0060_steward_memory_scopes`；两张表新列存在；
      `memories` 六列存在。
- [ ] `/api/ready` 200 且 `capacity_ready: true`；服务重启后无 `maintenance tick failed` 增长。
- [ ] `steward_memory_scopes_effective` 在平台级与空间级都读到 `household`（不是只写入成功）。
- [ ] 存在至少一条 `scope='household'`、`confirmation_status='confirmed'` 的记忆且已建 RAG 索引。
- [ ] steward run 的 allowlist 含 `search_memory`；有真实模型调用记录；
      有 `search_memory` 的工具调用记录；输出通过校验。
- [ ] 四项分列报告，每项标注「已证明 / 未证明 / 阻塞」，阻塞项给出原因与复现命令。
- [ ] 生产环境未被触碰（给出未触碰的核对证据）。

## Constraints

- 只动开发环境；任何命令不得指向 `familygraph-prod-*` 或生产 PG 库 `familygraph`。
- 备份先于迁移：迁移前对开发 PG 做 `pg_dump`（容器内无 `pg_dump` 时用应用侧备份或
  命名卷快照，并在报告中说明所用方式）。
- 不降低校验、不伪造通过、不为「看起来成功」而放宽断言。
- `private` 不启用。
- 不修改代码；若验收发现缺陷，另建任务修复，不在本任务内顺手改。
