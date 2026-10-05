# 技术设计：迁移执行前证明与安全门

## 1. 证据分级

每条结论必须标记证据等级：

- `L0`：源码/注释推断，只能用于提出假设；
- `L1`：SQLite 单元/静态检查，只证明 SQLite 合同；
- `L2`：隔离 PostgreSQL 单连接或小探针，证明方言/局部行为；
- `L3`：真实 PostgreSQL 多连接、故障注入或恢复演练；
- `L4`：开发环境灰度、真实运行链路和用户可见结果。

Acceptance 不得用 L0/L1 冒充 L3/L4。

## 2. 每个变更的合同卡

实现任何迁移切片前，先填写：

```text
Contract ID
事实来源/调用方
输入与输出
持久不变量
锁参与者与锁序
成功路径
竞争路径
异常/取消/超时路径
重试与幂等键
审计/计费影响
SQLite 与 PostgreSQL 差异
正向/负向/mutation 用例
回滚点
证据等级
```

缺少任一栏只能停留在 planning/probe，不得写业务实现。

## 3. 全局锁序

第一版固定为：

```text
global capacity → kind capacity → tenant capacity → parent/resource row → run row → attempt/event row
```

同一事务不得先取得后面的锁再回头取前面的锁。若现有调用方不能遵守，必须在边界重构为：

- 进入事务前完成 admission；或
- 通过单独的幂等 release transaction 释放 counter；或
- 采用 CAS/唯一约束避免持有两类锁。

所有锁顺序需要静态调用图 + 两连接死锁探针双重验证。

## 4. 事务语义分类

- 单行状态：条件 `UPDATE` + affected-row CAS；
- 已有资源资格与多表写入：固定父行 `FOR UPDATE`；
- 自然键尚不存在：事务 advisory lock + DB unique constraint；
- 租约/配额：持久 counter row + candidate `SKIP LOCKED`；
- 无法分解的极少数跨行不变量：`SERIALIZABLE` + bounded retry，并记录冲突指标。

不得把 `_immediate_tx` 的数量当作迁移设计；必须说明它保护的具体不变量。

## 5. 分阶段闸门

```text
Gate 0：scope、worktree、环境和任务边界
Gate 1：schema/SQL/dialect inventory
Gate 2：锁序、CAS、counter、幂等证明
Gate 3：真实 PostgreSQL prototype
Gate 4：故障注入与恢复
Gate 5：snapshot/import/reconciliation/backup
Gate 6：开发 shadow/cutover
Gate 7：跨子任务 load acceptance
```

任何 Gate 失败，回到该 Gate 修订设计；不跳到下一阶段，不通过“先实现再补测试”绕过。

## 6. 失败处理

测试失败分为：实现 bug、测试 oracle 错、环境阻塞、设计未决、范围越界。每类必须记录根因和下一步；不能用修改断言、放宽阈值、切换环境或删除用例消除失败。

## 7. 跨任务接缝

`postgres-migration` 提供：事务、capacity、lease/recovery、migration health、writer epoch 和数据对账接口。

其他子任务提供：

- control-plane：独立 worker/DB reserve 和故障域；
- provider：stream quota、backpressure、circuit；
- operations：连接总预算、备份/PITR/HA、切换；
- lexical/RAG：检索质量、索引生命周期和授权过滤；
- load acceptance：最终矩阵与 release gate。

未完成的子任务只能阻塞父任务，不能由本任务用简化实现替代。
