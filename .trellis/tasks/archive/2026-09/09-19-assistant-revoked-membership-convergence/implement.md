# 执行计划：撤权后 Run 的终态收敛

- [ ] 在 `reaper_pass` 的 stale 循环内加入成员资格判定（先于 attempt 耗尽判断），
      失效时终态 `failed` + 新错误码 `AGENT_MEMBERSHIP_REVOKED`，不回队。
- [ ] `app/errors.py` 新增 `AGENT_MEMBERSHIP_REVOKED`；审计 detail 用 `reason` 区分
      `membership_revoked` / `cancel_requested` / `lease_expired`。
- [ ] 四条 backend 回归（撤权收敛、撤权且 attempt 耗尽、健康租约仍回队、健康耗尽仍 expired），
      并验证前两条在还原修复后转红。
- [ ] 前端 `AGENT_ERROR_COPY` 增加文案 + 断言。
- [ ] 门禁：`ruff check` / `ruff format --check` / `mypy app` / `pytest`（backend），
      `npm run lint` / `type-check` / `test`（frontend，仅受影响范围 + 全量）。
- [ ] 回到 `09-19-dual-agent-acceptance-gap-closure` 复跑浏览器 UI2-8/9/10 与受控 A6，
      确认 UI2-9 转绿且无回归。
- [ ] 提交、串行集成、归档、清理 worktree 与分支（`-d`）。
