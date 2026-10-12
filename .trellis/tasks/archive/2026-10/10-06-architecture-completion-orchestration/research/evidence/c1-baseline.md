# C1：PostgreSQL baseline（schema + 触发器等价物）

## 为什么不用 `alembic upgrade head`

三条历史迁移实测在 PostgreSQL 上直接失败（C1 前已确认，见 `10-05` 的 `migration-replay-blockers.md`）：

| 迁移 | 构造 | PG 结果 |
|---|---|---|
| `0042` | `json_extract` 写入 CHECK | `UndefinedFunction` |
| `0022` | `SELECT last_insert_rowid()` | `UndefinedFunction` |
| `0014` | `CREATE VIRTUAL TABLE ... fts5` | `SyntaxError` |

因此 baseline = **ORM metadata + 显式 plpgsql 触发器等价物**。

## 建表结果（隔离 PostgreSQL 16，实跑）

| 项 | 值 |
|---|---|
| ORM metadata create_all | 成功 |
| 表 | 87 |
| 索引 | 252 |
| CHECK | 114 |
| FK | 212 |
| UNIQUE | 32 |
| **局部索引** | **16**（>= 16，谓词未丢失） |
| 触发器（create_all 后） | **0** ← 关键：元数据看不到触发器 |

## 触发器等价物

- SQLite 实际触发器：**69**（源码位点只有 14，循环展开后 69）
- 生成并应用：**66**
- 被索引机制取代：**3**
- PostgreSQL 触发器总数：**66**

### 分类

```
{
  "revision_mirror": 2,
  "revision_counter": 60,
  "immutable_guard": 4,
  "substituted:fts_sync": 3
}
```

### FTS 同步的语义替换（不是丢失）

3 个 rag_chunks_* FTS 同步触发器不转换为触发器：PGroonga 是索引，自动跟随表变更。这不是丢失，而是机制替换；需另用搜索可见性用例证明。

### JSON 列的 PostgreSQL 阻塞点（本次新发现）

SQLAlchemy JSON 在 PostgreSQL 上是 `json` 类型，而 `json` 没有相等运算符；比较 JSON 列的触发器必须显式 `::jsonb`，否则运行期 UndefinedFunction。SQLite 侧 JSON 是 TEXT，不存在该问题，因此这是纯 PostgreSQL 阻塞点。

受影响列：

```
{
  "users": [
    "birth",
    "death"
  ],
  "admin_access_sessions": [
    "scopes_json"
  ],
  "agent_providers": [
    "allowed_models_json",
    "compat_json",
    "input_modalities_json",
    "thinking_levels_json"
  ],
  "profile_fact_reviews": [
    "item_ref_json"
  ],
  "admin_access_audits": [
    "filters_json"
  ],
  "web_platform_configs": [
    "allowed_domains_json",
    "denied_domains_json"
  ],
  "web_space_configs": [
    "allowed_use_cases_json"
  ],
  "derived_facts": [
    "alt_paths_json",
    "evidence_fact_ids_json",
    "main_path_json"
  ],
  "personal_family_bridg
```

## 负向 + 正向双向验证

通过 **13/13**。

| 用例 | 结果 |
|---|---|
| 改 account_id | OK |
| 改 space_id | OK |
| 改 agent_kind | OK |
| 写同值（正向） | OK |
| 任何 UPDATE | OK |
| versioned -> legacy | OK |
| legacy -> unsupported（正向） | OK |
| revision != source_revision（插入） | OK |
| revision = source_revision（正向） | OK |
| UPDATE 使两者不等 | OK |
| sri counter increments | OK |
| counter increments with trigger | OK |
| counter stops without trigger | OK |

### 覆盖的语义

- `scope_immutable`：改 account/space/kind 被拒，写同值通过；
- `append_only`：任何 UPDATE 被拒；
- `sticky status`：versioned 不可退回，非 versioned 可改；
- `revision mirror`：插入/更新时 revision != source_revision 被拒；
- `sri counter`：INSERT 递增，**反证**（有触发器 +1、删触发器不变）证明递增来自触发器。

## 过程中修掉的三个真实缺陷（都由测试抓出）

1. **sri 转换只插一列** → `null value in column "structural"`。SQLite 的 INSERT 显式给出三列，
   转换必须同样给出三列（未变动的写 0）。
2. **`json` 没有相等运算符** → 12 个 sri 触发器 + `trg_scev_immutable` 运行期 `UndefinedFunction`。
   SQLite 的 JSON 是 TEXT，所以 SQLite 测试永远测不出；必须显式 `::jsonb`。
3. **反证是破坏性的** → 第一版直接 `DROP TRIGGER sri_accounts_structural_insert`，
   把基线从 66 变成 65。反证必须在临时表上做，否则「验证」本身腐蚀被测对象。

## 边界

- 本 baseline 覆盖 **schema 与触发器**；counter/lease/CAS/settle/recovery 属 C2；
- 触发器只验证了 5 类语义的代表用例，**未逐表验证 60 个 sri 的 scope 子查询**；
- PGroonga 索引本身属 C6；此处只确认 FTS 同步触发器被索引机制取代。
