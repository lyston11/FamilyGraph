# E 实施计划

仅规划。依赖 D 请求关联协议已冻结并集成；主会话串行执行。

- [x] 最新规划获批后 task.py start 并进入本任务 worktree；重新核对 SDK锁文件与实际session配置，不依据全局Pi安装推断项目行为。（SDK 锁定 `@earendil-works/pi-ai` / `pi-coding-agent` 0.84.3，实测探针）
- [x] 逐一检查 ProviderGateway 的所有返回/异常/迭代器关闭分支，列出审计遗漏与上游状态到SDK行为的映射。
- [x] 真实SDK+本地假上游先证红：永久400/401/403、连接异常审计、单失败成功、失败耗尽、流中断与session重试。
- [x] 冻结安全机器码/header形状和分类，确认不与内部认证/失租冲突；需要改变整体次数时先提交策略表供选择。
- [x] 实现分类与恰好一次审计；依批准方案治理两层重试，保留overflow压缩、空回答失败和取消优先级。
- [ ] 验证上下游请求数、审计数、D统计、费用/unknown分类一致，无错误原文/密钥哨兵泄漏。
- [ ] 定向与受影响包门禁、internal真实联调、API smoke；由F复验浏览器失败/取消呈现。
- [x] 更新 agent-runtime/错误合同和父summary；提交、串行集成、按已完成范围验收。策略阻塞未解不得标全完成。（Spec 已更新、父 summary/HANDOFF/任务图已同步、已提交并集成 main；E-R3/E-R5 数值仍阻塞，故任务保持 in_progress 不归档）
- [ ] 交G发布；归档后在合并且干净前提下清理本任务worktree/分支。（待 E-R5 决策与 G 发布；worktree 暂留供后续补齐）

```bash
cd backend
.venv/bin/pytest -q tests/test_provider_proxy.py tests/test_agent_events.py tests/test_admin_agent_latency.py tests/test_system_admin_boundary.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app
.venv/bin/pytest -q
cd ../agent
npm run lint
npm run type-check
npx vitest run test/worker.integration.test.ts test/session-history.test.ts
npm test
npm run build
```

新测试沿现有 Provider/worker 测试组织；不升级依赖来绕过问题。记录不同故障下各层实际attempt、等待、取消时间及SDK版本。若改frontend错误文案映射，补相应store/组件与frontend门禁。smoke使用显式隔离环境，退出2保留blocked。

本轮不运行这些命令、不调用真实模型。实现回退应覆盖网关分类与sidecar识别两端，禁止只回退一端留下重试语义漂移。
