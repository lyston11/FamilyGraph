# F 实施计划

仅规划。C/D/E集成是完整执行前置条件；所有子任务串行，无子智能体。

- [ ] 获批后启动本任务worktree；固定待验SHA、依赖版本、环境和C/D/E验收入口。
- [ ] 盘点已有测试覆盖并生成逐格matrix，注明复用/新增/未覆盖；将父任务原五个反例纳入回归映射。
- [ ] 准备显式隔离DATA_DIR、专用端口和loopback假上游，lsof核对不误用生产SSH隧道。
- [ ] 运行实际SDK/真实backend的矩阵；有迁移先隔离upgrade head，不能用create_all替代。
- [ ] 启动真实frontend，浏览器验证首帧→发送→等待→工具→正文→终态及刷新/重连/权限场景；不以route mock替代。
- [ ] 量化gateway chunk、公共SSE接收、渲染及代理/时钟边界；将精度与未知明确入报告。
- [ ] 执行受影响包门禁与真实API smoke；任何失败/blocked保留，不填写通过豁免。
- [ ] 输出matrix、前后对照、隐私检查与环境退出证明；更新父AC01/02/03/04/05/06/08的本地证据。
- [ ] 交G部署准入；提交/串行集成/归档与worktree清理，不把G真实验证算作本任务已完成。

## 最小验证入口

```bash
cd backend
.venv/bin/pytest -q tests/test_steward_assist.py tests/test_admin_agent_latency.py tests/test_agent_events.py tests/test_provider_proxy.py tests/test_steward_terminology_delivery_integration.py
cd ../agent
npx vitest run test/events.test.ts test/worker.integration.test.ts test/session-history.test.ts test/assistant-delta-gap.test.ts
cd ../frontend
npx vitest run src/stores/__tests__/agent.spec.ts src/components/agent/__tests__/AgentPrimitives.spec.ts
```

之后分别运行受影响包lint/type-check/test/build（backend为ruff、format、mypy、pytest）。浏览器自动化沿仓库现有工具，不新增无关测试框架；必要新脚本需记录启动/停止方法。仓库根的`./scripts/frontend-api-smoke.sh --report /tmp/familygraph-controlled-smoke.json`必须确认连到本次隔离栈；参数/env以脚本实际支持为准，退出2=blocked。

报告模板每格至少：场景ID、源码SHA、依赖版本、输入fixture、时序注入、命令、请求数/状态、阶段指标、容差、结果与证据路径。新证据不得覆盖原始历史失败日志；最终矩阵必须区分mock上游与真实Provider。
