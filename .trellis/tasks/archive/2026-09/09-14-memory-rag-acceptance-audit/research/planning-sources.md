# 规划依据与执行约束

- 当前工作流入口：[AGENTS.md](../../../../../../AGENTS.md)、[Trellis workflow](../../../../../workflow.md)。最初登记与分析授权已完成；2026-09-14 用户进一步授权审查、验收及通过后的提交、合并和归档，B/D 按既定所有权执行修复，不重复询问相同授权。
- 业务目标依据：[父PRD](../../09-13-agent-memory-rag-remediation/prd.md)、[B PRD](../../09-13-rag-retrieval-citations/prd.md)/[B设计](../../09-13-rag-retrieval-citations/design.md)、[D PRD](../../09-13-rag-index-lifecycle/prd.md)/[D设计](../../09-13-rag-index-lifecycle/design.md)。主线程已直接阅读决定性合同和将修改的记录。
- `.trellis/spec/guides/index.md`、backend/index.md、frontend/index.md当前自标“历史资料（不可执行）”。它们可帮助追溯，不能重新作为规范门禁。C补丁的spec记录是该修复的技术知识，不改变上述状态。
- 主检出用于生命周期和任务材料；业务修复/提交仅在专属分支与linked worktree。相交模块、迁移、SQLite或端口串行，禁止reset/rebase/force push或覆盖他人工作。
- 只读复查允许独立子代理；本次B/D报告不是最终裁决，主线程已沿出处抽查。新20组报告、旧26项及C补丁各保留版本和证据范围，不把重复测试累加成新覆盖。
- 校验只针对本任务文档和明确相关记录；主检出已有AGENTS/config/其他Steward任务/0041格式等脏文件保持原状。
- 证据中不保存密钥、token、生产资料或模型完整prompt。真实smoke使用临时合成库/身份和动态端口，退出清理；不复制运行中的SQLite主库。
- main 合并、归档与清理已获授权；部署未授权。隔离 smoke 先完成。工作树/分支只有已合并且无未提交业务改动、任务 archive 后才清理，不用 force 掩盖缺失前置条件。

最终规划交付证据见[校验记录](planning-validation.md)；未来实施应加载当前PRD/design/implement及本任务非空context manifests，重新确认实际代码head。
