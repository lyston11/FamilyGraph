# Gate 3：PostgreSQL baseline prototype 与触发器等价物

## 为什么 Gate 3 不能从 ORM 元数据生成

69 个触发器**只存在于 Alembic 迁移里**，ORM 元数据没有任何对应声明。因此
`create_all` 路径（`pg-schema-feasibility.md` 的 87/87）**完全看不到它们**。

走 PostgreSQL baseline 时若不显式重写，后果不是报错，而是**数据库不再拒绝非法写入**：

- `agent_sessions` 的 scope（account/space/kind）可被改写；
- `raw_relation_inputs` 可被 UPDATE（append-only 语义丢失）；
- candidate evidence 可被改写；
- `steward_llm_candidates` 的内部状态可从 `versioned` 退回；
- 60 个 `sri_*` revision 计数器消失，steward 投影新鲜度判断失效。

这些是**账本与不可变性**不变量，不是风格问题。

## 原型：四类语义等价物（plpgsql）

`research/tools/pg_baseline_prototype.py` 为四类触发器各建一个 PostgreSQL 等价物：

| 类别 | 数量 | PostgreSQL 形态 |
|---|---|---|
| scope-immutable | 1 | `BEFORE UPDATE ... FOR EACH ROW` + `RAISE EXCEPTION` |
| append-only | 1 | 同上，无条件拒绝 |
| conditional-immutable | 1 | 条件判断受保护列是否变化 |
| sticky-status | 1 | 状态不可从 `versioned` 退回 |
| revision-counter | 60 | `AFTER INSERT/UPDATE/DELETE` + `INSERT ... ON CONFLICT DO UPDATE` 计数 |

## 实测结果（隔离 PostgreSQL 16，可复跑）

```
负向：非法操作必须被拒绝
  [OK ] scope-immutable 改 account_id / space_id / agent_kind
  [OK ] append-only UPDATE
  [OK ] conditional-immutable 改 content_sha256
  [OK ] sticky-status versioned -> internal
正向：合法操作必须被接受
  [OK ] scope-immutable 写同值
  [OK ] conditional-immutable 写非受保护列
  [OK ] sticky-status 从非 versioned 出发可改
  [OK ] revision-counter 正常更新（计数 = 2）
反证：移除 append-only 触发器后 UPDATE 被接受
PASS
```

**反证是关键**：删掉保护函数后非法操作变成可接受，证明上面的「被拒绝」确实来自触发器，
而不是别的原因（约束、类型、权限）。

## 过程中修正的一处测试错误

初版把「sticky-status 从非 versioned 出发可改」写在 id=1 上，但先前被拒的 UPDATE 已回滚，
id=1 仍是 `versioned`，因此被正确拒绝。这是**测试 oracle 错误**，不是触发器缺陷。
已改为另插一行 `draft` 记录再验证。这类区分正是 `design.md` §6 要求的失败分类。

## 覆盖边界（诚实声明）

- 原型覆盖**四类语义**，不是 69 个对象的逐条等价物。60 个 `sri_*` 只验证了**一类**
  行为（计数递增），没有逐表验证 `scope_id` 解析（`_scope_query` 的 UNION 分支、
  bridge 表 join）在不同表上的正确性。
- 未验证：`0047` 的 `rag_documents_revision_*`（2 个）与 `rag_chunks_*`（3 个）
  的**具体语义**（revision 镜像与 FTS 同步）。
- 未验证：`BEFORE UPDATE OF <column>` 列级触发在 PostgreSQL 中的等价写法。

## 证据等级

**L2**（真实隔离 PostgreSQL 单连接，含正向、负向与反证）。
