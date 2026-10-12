# C8/C1：真实历史快照导入 PostgreSQL 与对账

## 结果

```
baseline 表数        : 88
回填前向 FK          : 2 表，失败 0
导入成功             : 87 表 / 143,134 行
逐表行数对账          : 全部一致
列级摘要对账          : 7 张关键表一致
PASS
```

数据来源：`app.backup` 产出的**隔离快照**（不是运行中主库的复制），
51 users / 1151 runs / 31821 events / 22852 domain_events / 26580 audit_log。

| 关键表 | 行数 |
|---|---|
| users / accounts | 51 / 51 |
| family_spaces / space_members / relations | 20 / 94 / 74 |
| agent_sessions / agent_jobs / agent_runs / agent_run_events | 16 / 28 / 1151 / 31821 |
| domain_events / action_cards | 22852 / 39 |
| steward_model_calls / audit_log | 2221 / 26580 |

## 列级摘要对账（不只看行数）

行数一致**不等于**内容一致。因此对 7 张关键表做**列级摘要**比对
（逐行若干列拼接后 SHA-256，两侧同样 `ORDER BY id`）：

```
users / accounts / family_spaces / space_members / relations / agent_runs / action_cards
→ 7 张全部一致
```

## 导入顺序：三次错误尝试（都值得记录）

导入顺序是本任务最容易写错的地方。三次尝试的错误与修正：

| # | 做法 | 结果 | 根因 |
|---|---|---|---|
| 1 | **硬编码 17 张表** | 只导入 4 表 | 真实库有 **95 张有数据的表**；漏掉的表恰是 FK 目标，下游全被跳过 |
| 2 | **只用 NOT NULL 边排序** | 82 表，`agent_runs` 失败 | 可空边不参与定序 → `agent_sessions` 排到 `agent_runs` **之后**（36 vs 16 位） |
| 3 | **「软边偏好」修补** | 同上 | 排序仍不稳定 |
| 4 | **全边拓扑排序 + 只删可空边破环** | **87 表全部成功** | 正确 |

### 为什么第 4 版正确

**所有 FK 边都参与排序**（于是 `agent_sessions` 自然排在 `agent_runs` 之前），
只有在 Kahn 算法**卡住**（真成环）时才删除一条**可空**边：

```
真实数据里唯一的环：
  agent_jobs.run_id -> agent_runs.id   （NOT NULL，不可删）
  agent_runs.job_id -> agent_jobs.id   （可空，可删）
→ 被删的必然是可空那条，由阶段 2 回填
```

这保证延迟**只发生在真正成环的列**上。

### 为什么「无条件延迟可空前向 FK」是错的

第 2 版被迫延迟 `agent_runs.session_id`，随即撞 CHECK：

```
ck_agent_runs_scope_binding:
  CHECK ((kind = 'assistant' AND session_id IS NOT NULL)
      OR (kind = 'steward' AND session_id IS NULL AND job_id IS NULL))
```

assistant run 的 `session_id` **必须非空**。因此延迟必须**最小化**——
最终版本只在真成环处延迟 `job_id`。

## 其他真实发现

1. **布尔类型差异**：SQLite 存 `pin_must_change=0`（整数），PostgreSQL 是 `BOOLEAN`
   → `column "pin_must_change" is of type boolean but expression is of type integer`。
   必须按 inspector 的列类型显式转换。
2. **`NOT IN :tuple` 不展开**：psycopg 下需 `bindparam(..., expanding=True)`，
   不能拼字符串（那会引入注入面）。
3. **`family_spaces.lineage_space_id` 自引用**（10 行）、`users.created_by` 自引用。
4. **拒绝守卫必须覆盖多张表**：只看 `agent_runs` 会放行「users 有数据但 runs 为空」的
   部分导入结果，随后撞 duplicate key（实测）。现在检查 5 张业务表。
5. **空表在 PG 侧未建不是数据差异**：0 行 vs 表不存在语义等价；但有行而表不存在是
   真差异。`attachments` 属前者。

## 部署含义

真实迁移**不能**用「逐表 COPY」一次完成。必须：

```text
1. 全 FK 边拓扑排序（含可空边），只在真成环处删除可空边
2. 被删边的列在全部导入完成后回填（阶段 2）
3. 布尔列按目标方言转换
4. 拒绝守卫覆盖多张业务表，防止在部分导入结果上重跑
5. 逐表行数 + 关键表列级摘要双重对账
6. 差异不自动修复（refusal）
```

## 证据等级

**L3/L4（真实数据）**：真实开发库快照、真实 88 表 PostgreSQL schema、
143,134 行、行数与列级摘要双重对账。**不是**生产切换演练（那属 C9）。

## 仍未覆盖

- **RPO/RTO 时间指标**：143k 行约 8 分钟（经 SSH 隧道），真实库更大；
- **增量/断点续传**：本次是全量导入；
- **导入期间源库持续写入**（本次用静态快照）；
- **线上规模数据量**。
