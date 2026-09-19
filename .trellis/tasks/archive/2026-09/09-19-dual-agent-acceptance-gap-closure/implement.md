# G 执行计划：验收缺口补齐

本任务只补缺格与修正口径。不部署、不调用真实模型、不改生产默认参数。
已通过的 60 格不重跑，只跑新增格 + 受影响回归。

## 准备

- [ ] 启动本任务 worktree；固定待验 SHA（当前 `main`），记录 backend/agent/frontend 依赖版本与迁移 head。
- [ ] 确认两个 smoke 脚本仍可复现（作为基线：41/41 + 19/19），失败先按环境阻塞处理，不在本任务修既有格。

## 缺口一：取消计时（G-R1/G-R2）

- [ ] 改 `_scenario_cancel`：`collect_worker` 不再挡在计时之前；分别记录 `cancel_accept_ms` / `terminal_visible_ms` / `worker_stop_ms`，并写明轮询粒度。
- [ ] 断言改为：终态为 `cancelled` **且**三个计时都有值；不再用 `converged_after_s` 作为端到端结论（可保留为「终态查询耗时」并改名说明）。
- [ ] 新增 backend 回归（`test_agent_browser_api.py` / `test_agent_queue.py`）：取消后结算 `failed` 保持 `failed` 不被改写；取消后结算 `succeeded` 改判 `cancelled` + 审计；落终态后 reaper 不覆盖。
- [ ] 三个回归都要能通过还原修复转红（先验证它们真的会失败）。

## 缺口二：撤权浏览器语义（G-R3）

- [ ] `run_browser_acceptance.py` 增加第二主体：朱标登录 → 在「明皇室」建会话并触发真实流。
- [ ] harness 用朱元璋 token 经真实 API 移除朱标成员资格。
- [ ] 新增格 UI2-8（受限内容不残留）、UI2-9（重连不复活）、UI2-10（迟到事件不回写）。
- [ ] 逐格如实判定；环境无法构造时记 `blocked` 并写明原因。
- [ ] 在补验记录里引用既有 `fence_execution` 成员资格 403 证据，不重复实现服务端部分。

## 口径修正（G-R4）

- [ ] 修正 `matrix.md` 与 spec 中压缩/取消的表述：`model_turn` 不得称纯推理；`settle` 是持久间隔估计；取消计时按新的三量口径。
- [ ] 新增 `evidence/gap-closure-<date>.md` **追加**补验结果，不覆盖归档的 `matrix.md`。
- [ ] 明确写出「撤权后已持久化历史事件仍可被该账号读取」这一现状边界。

## 验证与交付

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app
cd backend && .venv/bin/pytest -q tests/test_agent_browser_api.py tests/test_agent_queue.py tests/test_agent_events.py tests/test_internal_agent_api.py
python3 scripts/smoke/run_controlled_acceptance.py --report /tmp/g-gap-controlled.json
python3 scripts/smoke/run_browser_acceptance.py --report /tmp/g-gap-browser.json
./scripts/frontend-api-smoke.sh --report /tmp/g-gap-smoke.json   # 退出 2 = blocked，不得算通过
```

- [ ] 受影响包门禁（backend ruff/format/mypy/pytest；若改前端则加 lint/type-check/test/build）。
- [ ] 逐格 pass/fail/blocked 与缺失原因可追溯；新证据路径写入报告。
- [ ] 更新父任务 AC-08 的本地证据（浏览器验证范围扩大）。
- [ ] 提交、串行集成、归档；清理本任务 worktree 与分支（`-d`，不使用 force）。

## 失败处理

发现产品缺陷（非探针问题）→ 记录归属任务与可复现断言，退回所属任务修复后只重跑受影响格 + 必要累计检查。机器争用 flake 不得靠弱化业务边界解决；环境阻塞保持 blocked。
