# Implement：索引生命周期与维护

## 前提

2026-09-14 已获授权继续修复验收；在 B 本轮修复通过后，于主检出 start D，再把 B 与父任务累计分支合入 D 的既有 worktree。检查真实 Alembic heads，不预占迁移编号；不与 B 并行修改来源、检索和迁移代码。

## 本轮修复顺序

- [x] D-I01/I02：规范来源唯一键、镜像预检、并发稳定重放；完整物化时固定摘要/等价证据，同 revision 改文与缺块组合不改写旧定位。
- [x] D-I03/I04/I05/I09：不可变 lease/owner/round/policy/target 条件更新、固定轮次水位、最终有效开关/来源重验；真实 tick 的整个 RAG 批次失败可回滚。
- [x] D-I06/I07/I10：真实算法目标、有界完整换版、活动版本检索、普通维护不降级；缺块/FTS 恢复与全局来源合法性一致。
- [x] D-I08：旧 0045 降级入口在第一项破坏动作前无损拒绝不兼容数据；新迁移保全真实保存依赖和 FK ON/OFF 下全部子块。
- [x] 独立 D 核验、F-07～11 与原 D-AC1～8 回填；保留 B 精确引用、A 来源、C Pi 恢复及 RAG-only 晚开启正对照。

最新结果与限制见 [最终验收](research/acceptance-final.md)。实现记录、独立红绿证据和生命周期合同均已保存；提交后由父任务唯一串行通道集成与清理。

依据：[独立问题报告](../09-14-memory-rag-acceptance-audit/research/d-integration-check.md)、[执行前核对](../09-14-memory-rag-acceptance-audit/research/d-execution-preflight.md)、[累计验收设计](../09-14-memory-rag-acceptance-audit/design.md)。旧探针/日志/哈希不改写，新的持久回归与结果另行记录。

## 初版执行记录（历史勾选不代表本轮复验通过）

- [x] 先构造 MR-25 的隔离回归：同 revision invalidated document 被旧 rebuild 激活、重复重建换 chunk；这是尚需执行的验证，不把静态分析称为已经发生。
- [x] 固定 document 唯一身份、source tombstone/index_superseded 区别和 chunk/index_version 契约。
- [x] 修 index_memory/rebuild 的状态判定、幂等、条件更新与并发约束；旧数据重复和未知失效原因生成报告。
- [x] 在隔离库验证新增迁移、失效/retention/legacy/依赖与来源删除情形。
- [x] 实现有界维护职责、可恢复全轮游标、失败退避与 RAG-only/平台晚开启；物化/失败/游标原子提交，补过期 lease/旧策略不能回写的竞争测试。
- [x] 验证关闭→保存→开启→补齐、重启、并发、来源撤销竞争；开关 PUT 不执行全库索引。
- [x] 分离 FTS repair 和业务物化；新搜索只读活动版本，历史引用/保存依赖精确读取原片段；覆盖 staging 不外露、换版/回滚、实际 RAGHit 版本及解除来源隔离后的恢复。
- [x] 增加安全进度/错误元数据；必要前端入口与既有管理任务确定唯一所有者后再动。
- [x] 执行检查，回填 D-AC1～8、批次参数、迁移结果及已知限制。

## 验证

backend：`.venv/bin/ruff check .`、`.venv/bin/ruff format --check .`、`.venv/bin/mypy app`；memory_rag_models/service、platform_features、maintenance 和新 lifecycle/concurrency 测试。
新增迁移先在隔离 DATA_DIR 用 `.venv/bin/alembic upgrade head`，确认环境指向合成库；禁止直接对开发/线上主库实验。
若改动前端，按 AGENTS.md 对实际包做 lint/type-check/相关 tests/build；若未改，不无故全量跑管理员前端。
集成 smoke 必须分清成功/环境阻塞，不能拿手调 rebuild 成功代替真实维护入口验收。

## 完成

合法旧数据可由维护恢复，tombstone/legacy 不复活，同版本引用稳定，重放/关闭/重启正确，安全状态可观测且无正文泄露。记录未验证的线上数据规模和真实延迟；D 不承诺物理擦除或完全消除 Provider 已收到的数据。
