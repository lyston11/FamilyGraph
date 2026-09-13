# Implement：索引生命周期与维护

## 前提

保持 planning；A/B 合同交付后在主检出 start D，并进入记录的独立 worktree。检查现有 maintenance/迁移/平台任务改动及实际 heads；不与其他任务共用 SQLite/端口。

## 执行顺序

- [ ] 先构造 MR-25 的隔离回归：同 revision invalidated document 被旧 rebuild 激活、重复重建换 chunk；这是尚需执行的验证，不把静态分析称为已经发生。
- [ ] 固定 document 唯一身份、source tombstone/index_superseded 区别和 chunk/index_version 契约。
- [ ] 修 index_memory/rebuild 的状态判定、幂等、条件更新与并发约束；旧数据重复和未知失效原因生成报告。
- [ ] 在隔离库验证新增迁移、失效/retention/legacy/依赖与来源删除情形。
- [ ] 实现有界维护职责、可恢复全轮游标、失败退避与 RAG-only/平台晚开启；物化/失败/游标原子提交，补过期 lease/旧策略不能回写的竞争测试。
- [ ] 验证关闭→保存→开启→补齐、重启、并发、来源撤销竞争；开关 PUT 不执行全库索引。
- [ ] 分离 FTS repair 和业务物化；新搜索只读活动版本，历史引用/保存依赖精确读取原片段；覆盖 staging 不外露、换版/回滚、实际 RAGHit 版本及解除来源隔离后的恢复。
- [ ] 增加安全进度/错误元数据；必要前端入口与既有管理任务确定唯一所有者后再动。
- [ ] 执行检查，回填 D-AC1～8、批次参数、迁移结果及已知限制。

## 验证

backend：`.venv/bin/ruff check .`、`.venv/bin/ruff format --check .`、`.venv/bin/mypy app`；memory_rag_models/service、platform_features、maintenance 和新 lifecycle/concurrency 测试。
新增迁移先在隔离 DATA_DIR 用 `.venv/bin/alembic upgrade head`，确认环境指向合成库；禁止直接对开发/线上主库实验。
若改动前端，按 AGENTS.md 对实际包做 lint/type-check/相关 tests/build；若未改，不无故全量跑管理员前端。
集成 smoke 必须分清成功/环境阻塞，不能拿手调 rebuild 成功代替真实维护入口验收。

## 完成

合法旧数据可由维护恢复，tombstone/legacy 不复活，同版本引用稳定，重放/关闭/重启正确，安全状态可观测且无正文泄露。记录未验证的线上数据规模和真实延迟；D 不承诺物理擦除或完全消除 Provider 已收到的数据。
