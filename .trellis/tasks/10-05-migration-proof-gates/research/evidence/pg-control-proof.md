# Gate 2/3 PostgreSQL control prototype

## Environment

在开发服务器上使用一次性 `postgres:16-alpine` 容器和独立 `proof` database 执行；容器命令结束后自动删除。没有使用 FamilyGraph 开发库、生产库或 live SQLite 文件。

## Proof script

`scripts/migration-proof/pg_control_proof.py`

脚本建立隔离的：

- global/tenant capacity counter；
- run/attempt 状态；
- `(run_id, seq)` event 幂等表。

并使用 8 个 PostgreSQL 连接并发执行：

1. 固定锁序 `global counter → tenant counter → candidate row`；
2. 两个租户同时 lease；
3. 每租户容量上限为 2；
4. `FOR UPDATE SKIP LOCKED` 防止候选行重复领取；
5. event 重复 append 使用 `ON CONFLICT DO NOTHING`；
6. settle 使用 owner/status CAS；
7. 重复 settle 不重复归还 counter；
8. 最终 global counter 恢复为 0。

## Result

脚本返回码为 0，证明该最小 prototype 在真实 PostgreSQL 多连接下满足：

- 8 个 worker 无挂起；
- 每租户最多 2 个 in-flight；
- candidate 不重复领取；
- event duplicate 不产生第二行；
- settle 是单赢家；
- counter enter/leave 守恒。

证据等级：**L3（隔离 PostgreSQL 多连接）**。

## 边界

该 prototype 不是业务迁移实现，也没有接入真实 writer、Provider、sidecar 或开发环境。仍未证明真实 FamilyGraph 全部事务入口、故障注入、历史导入、备份恢复和跨任务接缝。
