# M0 技术设计（父级）

权威契约在 [architecture.md](../../spec/architecture.md) §1/§2 与 spec/backend、frontend 各规范，本文件只做里程碑级决策汇总：

- **分层**：m0a 先行建立分层骨架（api→services→models 单向依赖），m0b 在其上实现认证域。auth 相关代码落位：api/auth.py、services/（audit.py）、utils/security.py。
- **数据契约起点**：users/accounts/audit_log 三表是全局 users 模型的首批字段子集，m1a 扩展档案字段时必须走 Alembic 增量迁移而非改初始迁移。
- **JWT 形态**：双 token + token_version 是后续所有"敏感操作使会话失效"的机制基础（改 PIN/重置/删除档案共用），m0b 实现时封装为独立函数供后续调用。
- **风险回滚点**：见 implement.md；两子任务各自独立可回滚（m0b 失败不影响骨架可用）。
