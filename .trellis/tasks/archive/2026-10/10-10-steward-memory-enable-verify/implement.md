# 实施与验收记录

日期：2026-10-10
环境：**开发**（systemd `familygraph-api`/`familygraph-agent` + `fg-dev-pg`）。
生产（`familygraph-prod-*` Docker 栈）**未被触碰**。

## 1. 环境事实（先核对，再动手）

| 项 | 值 |
|---|---|
| 代码 | `/home/ubuntu/projects/FamilyGraph`，`c18f0e5`（= main） |
| 数据库 | 容器 `fg-dev-pg`，`127.0.0.1:55450`，库 `familygraph` |
| 迁移起点 | **无 `alembic_version` 表**（基线由 `pg_baseline_build.py` 建），schema 等价 `0058` |
| 数据 | 51 users / 20 spaces / **0 memories** / 1250 agent_runs |
| `familygraph-api.service` | 自 2026-10-08 06:55:31 未重启（`NRestarts=0`） |
| `familygraph-agent.service` | 同一时间启动，`agent/dist` 是 **10-02** 的构建 |

## 2. 执行与证据

### 2.1 备份

```bash
docker exec fg-dev-pg pg_dump -U postgres -d familygraph --no-owner | gzip > ~/fg-backups/fgdev-pre0060-20261010-120623.sql.gz
# 6.0M
```

### 2.2 迁移 0059 + 0060

```bash
alembic stamp 0058_writer_state   # 标定已知 schema 状态（该库无 alembic_version）
alembic upgrade head              # 0059_memory_supersede → 0060_steward_memory_scopes
```

核对：

```
alembic_version                    = 0060_steward_memory_scopes
platform_feature_configs           .steward_memory_scopes  default ''::varchar
agent_space_provider_settings      .steward_memory_scopes  default ''::varchar
memories                           六列齐备（valid_from/valid_to/superseded_by_id/
                                   supersede_reason/superseded_at/restored_at）
```

### 2.3 部署层启用 + 重启

`~/.config/familygraph/familygraph.env` 追加 `STEWARD_MEMORY_SCOPES=household`
（先备份为 `.bak-steward-memory-*`），随后 `systemctl --user restart familygraph-api.service`。

核对：`/api/ready` → 200，`capacity_ready: true`，`extensions.pgroonga: true`，
`maintenance tick failed` 计数 **0**（C9 记录过这个静默失败形态，故单独核对）。

### 2.4 发现并修复：sidecar 陈旧导致 steward 全量失败

重启后端后，**所有** steward run 失败：

```
steward_model_calls 2321-2325  status=failed  error_code=SIDECAR_ERROR  completion_chars=0
sidecar 日志: "steward context is missing steward_prompt_version"
```

根因：后端已是 10-10 代码（10-09 任务删除了 `steward_prompt_version`），
而 sidecar 仍是 **10-02 的 `agent/dist`**，它仍要求该字段。
`familygraph-code-sync.timer` 只 `git pull`，**不构建、不重启**。

修复：`cd agent && npm ci --no-audit --no-fund && npm run build` →
`systemctl --user restart familygraph-agent.service`。

修复后 space 1 的 run 1256 `status=succeeded`。

### 2.5 三层配置（先只开 `household`，`private` 不开）

平台层：走服务层函数 `platform_features.set_platform_feature_state`。
**注意**：该环境的系统管理员密码已轮换（`system_admin_accounts.password_version=5`，
`~/.config/familygraph/familygraph.env` 里的 `ADMIN_INITIAL_PASSWORD` 已不能登录），
因此**没有**走 `/admin-api/v1/platform-features` HTTP 面，也**没有**产生
`admin_access_audits` 行。这是本次验收的一个已知缺口。

空间层：走真实 HTTP `PUT /api/spaces/1/model-settings`（家庭 JWT，`朱元璋`/PIN 123456，
`enabled=true` + 既有 `provider_id=2`/`model=deepseek-v4.1-flash`）。

### 2.6 交集变异验证（证明「三层」不是「某一层」）

```
baseline            platform_set='household'  platform_eff='household'  space_eff='household'
platform cleared    platform_set=''           platform_eff=''           space_eff=''
platform restored   platform_set='household'  platform_eff='household'  space_eff='household'
```

清空平台层 → 空间侧 effective 立即变空；恢复 → 立即恢复。交集语义成立。

### 2.7 广告开关的真实证据（同一空间、前后对比）

| run | 时间 | `tool_allowlist_json` 含 `search_memory` |
|---|---|---|
| 1253-1255 | 12:10（配置为空） | **否** |
| 1256 | 12:21（配置生效） | **是** |

即「可读集为空不广告、非空才广告」在真实运行中双向可见。

### 2.8 造一条已确认的空间级记忆（R4）

走真实 HTTP：`POST /api/memory-candidates`（`suggested_scope=household`）→
`POST /api/memory-candidates/1/confirm`，`scope` 需用 API 拼写 **`household:1`**
（`private 不得绑定空间，shared 必须绑定空间`）。

结果：`memories.id=2`，`scope=household`，`space_id=1`，`status=active`，
`confirmation_status=confirmed`；RAG 已索引（`rag_documents.id=8` `status=active`、
`rag_chunks.id=7` `status=active`、1 条 embedding）。

### 2.9 触发真实 steward run

`UPDATE steward_space_schedules SET next_scan_at = now() - interval '1 hour' WHERE space_id = 1`
（把该空间的扫描排期改到期，其余交给正常维护循环；不直接造 job）。

结果：job 23419（`cause=integrity_scan`）→ run 1256 → call 2326
`assist_kind=candidate` `status=succeeded` `prompt_chars=1587` `completion_chars=60`。

输出：`{"items": [{"kind": "direct_sibling", "subject_user_id": 3, "object_user_id": 4}]}`

### 2.10 工具本身的直接验证（把「工具可用」与「模型是否调用」分开）

HTTP 端点 `POST /internal/agent/runs/{id}/tools/{name}/execute` 需要 run token，
而 run 1256 已 settle、token 有 TTL，故直接调服务层同一函数
（`steward_tools.execute_steward_tool`，即该端点内部调用的那一个）：

```
[无 viewer（空间级 kind）] OK -> {"scopes": ["household"], "results": [
   {"citation": "rag:2:r1:c7", "source_type": "memory", "source_id": "2",
    "scope": "household", "sensitivity": "normal", "revision": 1,
    "excerpt": "朱元璋每年冬至会在明皇室家宴上亲手煮一锅赤豆糯米饭，全家按辈分分食。"}]}
[有 viewer（terminology）] OK -> 同上
```

两者相同是**预期**的：本任务只开 `household`，未开 `private`，故 viewer 差异不体现。
`private` 的「无 viewer 即剔除」由 `tests/test_steward_memory_tool.py` 覆盖。

### 2.11 模型**没有**调用该工具

```
agent_tool_calls WHERE run_id=1256   → 0 行
agent_tool_calls 最近一次 steward 调用 → run 1168（2026-10-07，get_relationship_path ×5）
```

工具循环对 steward 是**通的**（10-07 有真实调用记录），但本次 run 中模型一次都没调用
`search_memory`。原因明确：`_PROMPTS[candidate/ranking/terminology/explanation]`
**完全没有提到工具或记忆**，只要求「按给定输入输出 JSON」。

### 2.12 写回（R4 第四项）

输出 `direct_sibling(3,4)` 已存在于 `steward_suggestions`（647/646，2026-09-27/28），
候选策略去重后**没有产生新的写回**。这是正确行为，但意味着本次 run 没有可观察的
新增可见改善。

## 3. 四项分列结论

| # | 项 | 结论 | 依据 |
|---|---|---|---|
| 1 | 部署有效启用 | **已证明** | 三层交集经变异验证；run 1256 allowlist 含新工具而 1253-1255 不含；`/api/ready` 200 + `capacity_ready` |
| 2 | 真实模型调用 | **部分证明** | 真实模型调用**发生**（call 2326 succeeded），但**模型未调用 `search_memory`**（`agent_tool_calls` 0 行）→ 「模型使用记忆」**未证明** |
| 3 | 取得合法输出 | **已证明** | call 2326 `status=succeeded`，输出为合法候选 JSON 并通过 `steward_guard`；工具本身经服务层直调返回合法结果 |
| 4 | 自动写回/可见改善 | **未证明** | 输出被去重（`direct_sibling(3,4)` 已存在），无新增写回，用户可见结果无变化 |

## 4. 未证明项的阻塞与后续

**第 2、4 项的阻塞是同一个**：steward 的四条 prompt 都没有提到工具，模型没有理由
调用 `search_memory`，于是它照旧只输出 JSON。因此当前状态是
**「能力已部署、已授权、可用，但对模型是惰性的」**。

不降低校验、不伪造通过。要让第 2 项成立，需要（另建任务，不在本任务内改）：

- 在 `_PROMPTS[kind]` 里显式说明可用工具与何时使用（这会改变 `prompt_digest`，
  属于受审文本变更）；**或**
- 在投影里给出「本空间已开放记忆级别」这一事实，让模型知道存在该入口。

第 4 项要可观察，需要一次**不被去重**的输出（例如新空间、或新关系）。

## 5. 本次发现的环境缺陷（均已处置）

| # | 缺陷 | 处置 |
|---|---|---|
| 1 | 开发 PG 无 `alembic_version`，落后 main 两个迁移（0059/0060） | `stamp 0058` + `upgrade head`，已到 0060 |
| 2 | `agent/dist` 是 10-02 构建，且 code-sync 不重建 → 重启后端后 steward 全量 `SIDECAR_ERROR` | `npm ci && npm run build` + 重启 sidecar；已记入项目记忆 |
| 3 | 管理员密码已轮换，`ADMIN_INITIAL_PASSWORD` 失效 → 管理面 HTTP 不可用 | 如实记录；平台层改走服务层函数，**无** `admin_access_audits` 行 |

## 6. 生产未触碰的核对

- 所有写操作的目标 DSN 均为 `127.0.0.1:55450`（`fg-dev-pg`）。
- 未执行任何 `docker exec familygraph-prod-*`；未连接生产 PG 库 `familygraph`
  （compose 网络内、无宿主端口）。
- `familygraph-prod-*` 容器状态与 `Up` 时长在本任务前后未变化。
