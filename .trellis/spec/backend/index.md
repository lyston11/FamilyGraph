# 后端规范路由

## 范围

适用于 `backend/` 中的 FastAPI、SQLAlchemy、SQLite/WAL、JWT、后台 API 与 Agent/Steward 运行时改动。

## 适用性

- 目录或分层变化：读取 [directory-structure.md](directory-structure.md)。
- 数据库、迁移、事务、备份或约束：读取 [database-guidelines.md](database-guidelines.md)。
- 错误、日志、质量门禁：按需读取对应叶文件。
- Agent、Memory/RAG、Steward、Controlled Web 或关系智能：只读取对应领域叶文件。
- 涉及全局身份、授权、空间状态或数据权利：从 [全局架构规范路由](../architecture/index.md) 选择具体叶文件。

## 合同叶文件

- [directory-structure.md](directory-structure.md)
- [database-guidelines.md](database-guidelines.md)
- [error-handling.md](error-handling.md)
- [quality-guidelines.md](quality-guidelines.md)
- [logging-guidelines.md](logging-guidelines.md)
- [agent-runtime.md](agent-runtime.md)
- [memory-contract.md](memory-contract.md)
- [memory-rag-execution-contract.md](memory-rag-execution-contract.md)
- [rag-index-lifecycle-contract.md](rag-index-lifecycle-contract.md)
- [relationship-intelligence.md](relationship-intelligence.md)
- [steward-action-card.md](steward-action-card.md)
- [steward-behavior-rebuild.md](steward-behavior-rebuild.md)
- [steward-candidate-evidence.md](steward-candidate-evidence.md)
- [steward-recommendation-suppression.md](steward-recommendation-suppression.md)
- [controlled-web.md](controlled-web.md)
- [assistant-history-restoration.md](assistant-history-restoration.md)

## 验证入口

按所选叶文件的 Required validation 执行；不要因为读取本路由而默认读取全部后端规范。
