# G2 实施计划

主会话内联。前端单点改动，不涉及协议、迁移或后端。

## 顺序

- [ ] `task.py start` 建 worktree；同步 `origin/main`（须含 F 的 UI2-6/UI2-7 探针）。
- [ ] `stores/agent.ts`：`finishRun` 在终态分支把临时投影转为终态展示（保留正文、去掉 `provisional`），不新增用户可见文案。
- [ ] 回归（frontend vitest）：`text_delta → run.cancelled` 保留正文且不再 provisional；`run.failed` 同；`text_reset` 仍是隐藏；权威替换与切换会话丢弃不退化。
- [ ] frontend 门禁：lint / type-check / test / build。
- [ ] F 复验：UI2-6 与 UI2-7 同时通过；UI1/UI2 其余格不退化。
- [ ] spec：把 09-18 的「标终态」落成可执行描述（终态=去掉 provisional 标记，保留正文），避免下一位读者再按「保留但不标」实现。
- [ ] 提交、串行集成 main、F 全量累计复验。

## 最小验证入口

```bash
cd frontend && npx vitest run src/stores/__tests__/agent.spec.ts
cd frontend && npm run lint && npm run type-check && npm test && npm run build
python3 scripts/smoke/run_browser_acceptance.py --report /tmp/f-browser-g2.json
```

## 风险与回退

- 改动只在终态分支；`text_reset`（隐藏）与进行中展示不变，回退即还原该分支。
- 不改 `AgentMessageView` 形状，不动引用/历史绑定路径。
