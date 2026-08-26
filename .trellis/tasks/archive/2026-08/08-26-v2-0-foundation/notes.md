# V2.0 注记

- 这是 Agent 安全前置，不是普通“模型接入准备”。
- v1 `is_admin` 不可直接映射到 Steward；platform_operator 也不自动是 space_admin。
- “被创建账号”与“空间成员”必须拆开：provisional node 可见不等于 membership。
- v1 直系结构边跨空间 full 与 v2 设计冲突，由本任务显式取代。
- owner 删除 FK CASCADE 是已登记的 v2 遗留，必须改成引导移交。
- 无真实数据意味着省略生产迁移复杂度，不意味着可以省略 Alembic、空库回归或备份恢复。
