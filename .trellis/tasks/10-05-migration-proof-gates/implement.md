# 实施计划：迁移执行前证明与安全门

## Phase 0：冻结执行

- [ ] 将当前 `postgres-migration` 的发现全部归档到风险登记表；停止未经过 Gate 的业务代码修改。
- [ ] 建立 stable inventory ID、环境 manifest、证据等级和未覆盖清单。
- [ ] 明确本任务与 5 个架构子任务的 owner/boundary/dependency。

## Phase 1：结构合同

- [ ] 生成表/列/FK/CHECK/index/trigger inventory。
- [ ] 逐项完成 43 个真实事务调用点的分类与调用方映射；任何数量差异必须有解释。
- [ ] 完成 SQLite/PG dialect matrix；运行期查询和建表约束分别验证。
- [ ] 完成 RAG lexical/vector、authorization、revision/citation 的接口边界。

## Phase 2：并发证明

- [ ] 为每个事务入口填写 lock/CAS/counter/lease/settle 模板。
- [ ] 冻结全局锁顺序，检查所有路径是否反向。
- [ ] 在两个以上 PostgreSQL 连接上验证：重复 lease、租户超额、重复 event seq、双 settle、cancel/settle、recovery。
- [ ] 注入 deadlock、serialization failure、connection loss、process crash，验证有界重试和终态收敛。
- [ ] 删除每个关键锁/CAS/counter 的 mutation，确保对应回归失败。

## Phase 3：最小 schema prototype

- [ ] 先建立只含 agent control tables/counters/audit 的 baseline prototype。
- [ ] refusal guards 先于 DDL；upgrade/downgrade/重复执行/中断恢复均验证。
- [ ] 不加入 RAG 搜索实现、不切 writer、不导入历史数据。

## Phase 4：导入与对账设计

- [ ] 定义静态 SQLite snapshot 生成和来源证明。
- [ ] 实现 staging import、ID/sequence/FK 保留、row/count/hash/scope/status/audit 对账。
- [ ] 实现对账失败 refusal、重复导入幂等、断点重试和 restore rehearsal。

## Phase 5：开发切换前置

- [ ] 由 operations 子任务完成连接预算、备份/PITR、writer epoch 和回滚 runbook。
- [ ] 由 control/provider/RAG 子任务完成各自接缝验收。
- [ ] 由 load-acceptance 子任务完成多 account/space/kind 矩阵。
- [ ] 所有前置子任务达到 `verified` 后，才进入 dev shadow/read。

## Phase 6：开发灰度与收尾

- [ ] shadow read → control writer → agent execution writer → domain writer。
- [ ] 每一步观察 control p95/p99、queue wait、DB wait、retry、audit、scope/hash 对账。
- [ ] 失败立即回滚路由，不双主、不手工修复、不自动切线上。
- [ ] 完成 backend/agent/migration/backup/restore 全量检查后才归档父任务。

## 每个实现切片的固定检查单

```text
[ ] PRD/design 已更新
[ ] 输入/输出/不变量/错误语义明确
[ ] 真实调用方已读
[ ] 隔离探针通过
[ ] 正向用例通过
[ ] 负向用例通过
[ ] mutation 能捕获删除保护
[ ] 受影响全量回归通过
[ ] 未覆盖项已登记
[ ] 回滚点可执行
[ ] 代码与任务 worktree/分支一致
```

## 禁止事项

- 不在 main 直接实现；
- 不在未确认工作区/任务分支前编辑；
- 不把本地 SQLite 测试通过当作 PostgreSQL 通过；
- 不用单次探针推断多租户公平、恢复或生产可用；
- 不在未完成对账和备份恢复前切 writer；
- 不把发现的缺陷留在聊天里，必须落到 PRD/design/evidence/test。
