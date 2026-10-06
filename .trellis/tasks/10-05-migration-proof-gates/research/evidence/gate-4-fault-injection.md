# Gate 4：故障注入（真实 PostgreSQL，多连接）

## 实测结果

`research/tools/pg_fault_injection.py`，隔离 PostgreSQL 16：

```
[OK ] 提交前断连      -> status=reserved owner=None counter=0     （全部回滚）
[OK ] 提交后断连      -> status=in_flight counter=1               （已提交状态持久）
[OK ] 重复 settle     -> 第一次=1 第二次=0 counter=1              （counter 只归还一次）
[OK ] cancel vs settle -> ['cancel:cancelled','settle:noop'] 终态=cancelled（恰好一个赢家）
[OK ] 崩溃恢复        -> 回收=1 终态=unknown counter=1            （过期租约被回收并归还）
[OK ] serialization   -> SerializationFailure 出现 1 次           （必须 bounded retry）
PASS
```

## 每类故障的合同（本次验证的语义）

| 故障 | 期望 | 实测 |
|---|---|---|
| 提交前连接丢失 | 无部分写入 | 状态回到 `reserved`，counter=0 |
| 提交后连接丢失 | 状态持久 | `in_flight` 且 counter=1 |
| 重复 settle | CAS 门 + counter 只减一次 | 第二次 0 行受影响，counter 保持 1 |
| cancel vs settle | 终态唯一，不双写 | 一个赢家，终态 `cancelled` |
| 崩溃后租约回收 | 过期且未应用的 attempt 可回收 | 回收 1 个，终态 `unknown`，counter 归还 |
| `SERIALIZABLE` 冲突 | 抛 `SerializationFailure` 而非丢更新 | 抛出，调用方须 bounded retry |

**关键设计点**：`重复 settle` 与 `崩溃恢复` 都要求 counter 归还**恰好一次**，门是
`status` 条件 UPDATE 的 affected rows（settle）与 `applied_at IS NULL`（recovery）。
两者都实测通过。

## 覆盖边界（必须诚实声明）

1. **这是原型，不是真实业务 schema**。`fi_*` 表是本次为验证语义而建的最小模型，
   不是 `agent_runs`/`steward_model_calls` 的真实结构。原型 L3 **不能**等同于
   真实入口 L3。
2. **未覆盖**：membership revoke 期间执行、connection loss **after upstream may have
   sent**（需要真实 provider 链路）、process crash（只模拟了租约过期，未真正 kill 进程）。
3. **未覆盖**：三把以上锁的锁序（Gate 2 已记为缺口）。
4. **未覆盖**：真实 `deadlock_timeout`（默认 1s）对 lease/settle 延迟预算的影响。

## 证据等级

**L3（原型）**：真实 PostgreSQL 多连接、真实故障注入（断连、并发竞争、序列化冲突）。
**不是**真实业务入口的 L3。
