# E 实施计划

仅规划。依赖 D 请求关联协议已冻结并集成；主会话串行执行。

- [x] 最新规划获批后 task.py start 并进入本任务 worktree；重新核对 SDK锁文件与实际session配置，不依据全局Pi安装推断项目行为。（SDK 锁定 `@earendil-works/pi-ai` / `pi-coding-agent` 0.84.3，实测探针）
- [x] 逐一检查 ProviderGateway 的所有返回/异常/迭代器关闭分支，列出审计遗漏与上游状态到SDK行为的映射。
- [x] 真实SDK+本地假上游先证红：永久400/401/403、连接异常审计、单失败成功、失败耗尽、流中断与session重试。
- [x] 冻结安全机器码/header形状和分类，确认不与内部认证/失租冲突；需要改变整体次数时先提交策略表供选择。
- [x] 实现分类与恰好一次审计；依批准方案治理两层重试，保留overflow压缩、空回答失败和取消优先级。
- [x] 验证上下游请求数、审计数、D统计、费用/unknown分类一致，无错误原文/密钥哨兵泄漏。
- [x] 定向与受影响包门禁、internal真实联调、API smoke；F 已复验浏览器失败/取消呈现（含本轮 D-F1 修复）。
- [x] 更新 agent-runtime/错误合同和父summary；提交、串行集成、按已完成范围验收。策略阻塞未解不得标全完成。（Spec 已更新、父 summary/HANDOFF/任务图已同步、已提交并集成 main；E-R3/E-R5 数值仍阻塞，故任务保持 in_progress 不归档）
- [x] 交G发布；归档后在合并且干净前提下清理本任务worktree/分支。（E-R5 已决策并实施；发布与真实小样本验收属 G）

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

## 与 09-18 的边界与 E-R5 决议（2026-09-18 更新）

候选与实测由 `09-18-assistant-low-latency` 提供，**策略决定归本任务 E-R5**。用户已批准按
A+B 推进，E 的策略部分据此实施（`4a850d1`，集成 `e0ee321`），完整数据见 `evidence.md` §8。

**决议：次数不变（请求层 5 + 会话层 3），改两处有界行为。**

- 真实可用性数据否决了降次数：2 个 run / 11 次出站 / 7 次失败，**run 级 2/2 成功**；run 2 第二轮
  恰好用满 5 次重试（6 次尝试）才成功，降到 1 次会让该轮直接失败。
- 新增**首响应期限**（`AGENT_PROVIDER_PROXY_HEADER_TIMEOUT_SECONDS`，默认 20s，仅约束等待
  响应头阶段，超时取消等待），针对「单次 503 拖 29.8s」；流式生成阶段不受约束。
- 暂时错误返回**有界退避** `retry-after-ms: 500`（默认退避第一级，对任意次数不劣于默认；
  用满 5 次时 15.5s→2.5s）；429 与上游自带 `Retry-After` 不受覆盖。
- 费用仍无法在仓库内计算（两侧 cost 置零），但因次数不变，最坏请求数上限未变（24 次）。

仍未完成（属 G，不阻塞本任务归档）：未部署、未跑真实模型；20s 阈值为可调默认值，
需部署后按 `header_ms` 真实分布复核。

## D-F1 补充（2026-09-19）

F 的受控矩阵发现并已在本任务闭合的取消分类缺陷（详见 `evidence.md` §9）：

- [x] 后端取消分支返回机器可读 `detail.reason=cancel_requested`（`fence_execution`、provider 网关及中流复核）。
- [x] sidecar 新增 `RunCancelledError` + `InternalClient.onRunCancelled`，取消不再只依赖心跳节奏。
- [x] worker 心跳与 `executeJob` 两处按取消收敛，不结算。
- [x] reaper 不等租约过期即收敛被取消 run（原 304s → 0.01s），审计记 `reason`。
- [x] 回归：backend 真实端点 + reaper 两例；agent 客户端三例 + worker 一例（均可还原转红）。
- [x] 门禁：backend ruff/format/mypy/`pytest` 全量；agent tsc/eslint/`vitest`；F 矩阵 A6 两格通过。

E-R3/E-R5 的预算数值已由用户在 09-18 决策（次数不变 + 有界退避 + 首响应期限），
部署与真实样本验证属 G。
