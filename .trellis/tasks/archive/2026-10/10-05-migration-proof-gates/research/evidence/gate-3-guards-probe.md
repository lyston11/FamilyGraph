# Gate 3 剩余项闭合：refusal 顺序、中断回滚、重复幂等、审计 exactly-once

## 实测（隔离 PostgreSQL 16）

```
[OK ] refusal guard -> refused: 1 行的 legacy_role 不在新枚举内
      版本=1（应 1）迁移行=0（应 0）审计=0（应 0）
[OK ] 中断恢复 -> 版本=1 迁移行=0 审计=0（全部回滚）
[OK ] 重复执行 -> 第一次=ok 第二次=ok 版本 2->2 审计 2->2（审计不增）
[OK ] 审计 exactly-once -> [(1,1),(2,1)]（每行恰好 1 条）
PASS
```

## 验证的四条硬约束

| 约束 | 断言 | 依据 |
|---|---|---|
| refusal guard **先于** DDL/版本移动 | 非法数据 → 版本仍 1、迁移行 0、审计 0 | memory #398 |
| 中断必须整体回滚 | 中途抛错 → 版本 1、迁移行 0、审计 0 | 无半迁移状态 |
| 重复执行幂等 | 第二次 → 版本不变、审计不增 | 重跑安全 |
| 审计 exactly-once | 每行恰好 1 条 | 账本完整性 |

## 探针发现了我自己实现里的一个真实幂等缺陷

初版迁移逻辑写成：

```python
conn.execute("UPDATE bg_rows SET migrated = true WHERE migrated = false")
for (rid,) in conn.execute("SELECT id FROM bg_rows WHERE migrated = true"):
    insert_audit(rid)
```

第二次执行时，`WHERE migrated = true` 会把**上次已迁移**的行也算进来，审计翻倍
（实测 `2 -> 4`）。探针报 FAIL，指出「审计不是 exactly-once」。

正确形态是用 `RETURNING` 取回**本次真正改动**的行集合：

```python
changed = conn.execute("UPDATE bg_rows SET migrated = true WHERE migrated = false RETURNING id")
for (rid,) in changed: insert_audit(rid)
```

修复后审计稳定在每行 1 条。

**这个缺陷正是本 Gate 存在的理由**：一个「看起来对」的迁移实现（先 UPDATE 再 SELECT）
在单次执行下完全正确，只有**重复执行**才暴露。若不测幂等，它会在真实重跑时污染账本。

## 证据等级

**L2（原型）**：真实 PostgreSQL、真实事务回滚、真实重复执行。
**不是**真实业务 schema 的迁移实现——真实迁移需在 88 表上重做同样四类验证。
