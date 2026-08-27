# 后端开发规范

> 技术栈：FastAPI + SQLAlchemy + SQLite(WAL) + lunar-python + JWT。全局架构契约见 [../architecture.md](../architecture.md)（身份模型/状态机/授权矩阵/删除级联的权威定义）。

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | 分层结构与单向依赖规则 | Initial draft |
| [Database Guidelines](./database-guidelines.md) | PRAGMA/迁移/事务/约束/备份 | Initial draft |
| [Error Handling](./error-handling.md) | 统一错误结构/错误码/防枚举 | Initial draft |
| [Quality Guidelines](./quality-guidelines.md) | 质量门禁命令/禁止模式/测试门槛 | Initial draft |
| [Logging Guidelines](./logging-guidelines.md) | 结构化日志/PII 脱敏红线/audit_log | Initial draft |
| [Agent Runtime](./agent-runtime.md) | Pi sidecar 六端点合同/token/事件/Provider 治理 | V2.1 |
| [Relationship Intelligence](./relationship-intelligence.md) | SourceFact/DerivedFact/概念码/TermRegistry 四级/Extractor/API 隐私红线 | V2.3 |
| [Steward and ActionCard](./steward-action-card.md) | space-scoped Steward jobs、DomainEvent/BehaviorProjection、ActionCard FSM、推荐资格矩阵与执行重校验 | V2.4 |
| [Controlled Web](./controlled-web.md) | 双层开关/工具披露、Egress/SSRF、approved token、query PII 最小化、配额审计与引用 | V2.6 |

> 状态说明：Initial draft = 基于锁定技术栈与架构决策制定的初始规范。M0 完成后必须用真实代码实例校正并补充示例。
