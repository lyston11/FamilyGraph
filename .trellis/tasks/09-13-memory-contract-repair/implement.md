# Implement：记忆契约修复

## 启动前

用户已批准执行，主检出 task.py start 已将本子任务置为 in_progress。分支 feat/09-13-memory-contract-repair，worktree /private/tmp/familygraph-memory-rag/09-13-memory-contract-repair。读取父设计和本 PRD，检查当前迁移 heads、API/前端已有改动。不能写主检出业务文件或他人分支。

## 执行

- [ ] 用当前 UI payload 建真实 API 回归：无来源 422、带来源提交后 500/list 500；保留失败证据。
- [ ] 固定 source/legacy/来源依赖 DTO 与迁移；补删除来源、非法 ref、并发确认造数。
- [ ] 修 output mapper、提交前 DTO 检验、创建幂等与确认唯一性。
- [ ] 接 manual/agent_message/rag_chunk resolver，覆盖每个读写面和来源撤销。
- [ ] 修前端来源传递、RAG 原文只读、重复操作 key、Memory/RAG 四组合可达性。
- [ ] 将旧服务测试中的伪授权 doc 字符串替换为 manual 或真实授权来源造数，不能为过旧 fixture 放松校验。
- [ ] 隔离迁移前后样本检查，记录异常 legacy/重复数据而不删除。
- [ ] 向 B/D 交接 source/ref/revision 与权限 helper 合同；记录测试和迁移结果。

## 验证

在 backend 运行 `.venv/bin/ruff check .`、`.venv/bin/ruff format --check .`、`.venv/bin/mypy app`。
优先跑 memory_rag、platform_features、agent_browser_api 和新增真实 memory API 测试，再按变更范围执行 backend suite。
在 frontend 运行 `npm run lint`、`npm run type-check`、memory store/MemoryManager tests、`npm run build`。
真实创建→确认→检索不可只 mock frontend API；保存来源失效的每条读取路径都须有反例。

## 回滚和完成

无创建/列表序列化故障、无重复确认、拒绝路径不泄露来源、旧数据兼容和四开关组合通过 A-AC1～8 后才交付。回滚保留来源/确认记录，不清库、不绕过 scope，不强行套旧 CHECK。
