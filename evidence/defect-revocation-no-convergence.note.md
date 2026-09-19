# G-D1（P1）成员资格被撤销后 Run 不收敛：排队重试 3 次、约 15 分钟才 `expired`

## 现象

受控浏览器验收新增的 F-R4「失权」格（UI2-8/9/10）在**真实**链路上暴露：
被撤权者本人正在收流时，owner 经真实 API 撤销其成员资格后——

- 服务端授权边界**正确**：撤权后该成员无法再创建 Agent 会话（403 `SPACE_FORBIDDEN_ACTOR`）；
- 在途 worker **正确**停止：22 条 `agent_internal_authz_denied`（`reason=active_membership_missing`），
  无工具调用、无业务写回、无模型请求（`agent_provider_egress` 停在撤权前那笔）；
- **但 Run 不收敛**：停在 `leased`，浏览器侧临时气泡继续渲染「生成中…」，
  且直到约 15 分钟后才以 `expired` 收口。

## 机理（DB 时间线证实）

```
05:22:37.845  run 4 created (message.user_added)
05:22:38.569  turn.started
05:22:39.833  assistant.text_delta      ← 撤权前最后一条事件
05:22:39.865  space_member_left (space 2, action=remove)   ← 真实 API 撤权
05:22:40.545…  agent_internal_authz_denied ×22 (active_membership_missing)
05:27:40.887  agent_lease_expired {job_id:4, attempt:1, outcome:"queued", reason:"lease_expired"}
05:32:40.997  job 4 再次 lease（attempt 2），lease_expires_at 前移
```

1. 撤权后每次内部请求都被 `_authorize_run` 拒（403 `AGENT_TOKEN_SCOPE_MISMATCH`，
   `reason=active_membership_missing`）——这一层是对的。
2. sidecar 心跳也 403 → `markLeaseLost` → abort。sidecar 不再续租，**也不再结算**。
3. 于是没有任何一方写终态。reaper 到期后按 `attempt(1) < max_attempts(3)` 判定为
   **可重试** → `outcome="queued"` **回队**。
4. 新 attempt 重新 lease → 再次 `getRunContext` 403 → 再次失租 → 再次等 300s。
5. 三次尝试耗尽后（约 `3 × AGENT_LEASE_TTL_SECONDS(300)` = **900s**）才 `expired`。

`expired` 的语义是「租约过期且 attempt 耗尽」，但它并不是这里的原因：成员资格被
永久撤销，重试**必然**以同样方式失败。把一次确定性的授权失效当成暂时性租约丢失，
既浪费时间又让用户看着一个永远不会完成的「生成中…」。

## 为何是缺陷（不是预期行为）

- 用户侧：撤权后 15 分钟内页面仍声称「生成中…」，无错误、无终态、无重试入口。
  这是**假进行中**，与 D-F3 修掉的「取消后仍标生成中」同一类问题。
- 系统侧：3 次必然失败的重试 + 3 个完整租约周期；`expired` 会污染延迟指标的
  失败分母，把「授权失效」记成「租约超时」。
- 与已修 D-F1 同属「非可重试条件被当成可重试」这一类：D-F1 修的是
  `cancel_requested`（终态意图直接收敛），本条是 `membership revoked`（授权永久失效）。

## 现有测试为何没发现

`backend/tests/test_internal_agent_api.py` 覆盖了「撤权后内部请求被拒」（403 +
`active_membership_missing`），但**没有覆盖 Run 的终态收敛**；
`test_agent_queue.py` 的 reaper 用例只覆盖「租约过期」与「取消」两条路径，
没有「执行身份永久失效」这条。两者之间正是本缺陷所在的缝隙。

## 修复方向（未实施，需独立设计）

在 `agent_queue.reaper_pass`（或结算路径）识别「执行身份已永久失效」并**直接终态化**
而不是回队，与 D-F1 对 `cancel_requested` 的处理同形。设计时必须解决：

- 如何可靠区分「成员资格行不存在」与「查询失败/DB 抖动」——后者仍必须回队重试，
  否则一次读失败会误杀健康的 Run；
- 终态用哪个状态与错误码（`cancelled`？新增 `AGENT_MEMBERSHIP_REVOKED`？）以及
  前端文案；
- 是否要同时补一条 SSE/回放收口，让已经打开的流立即结束而不是等下一次轮询。

## 复现

```bash
python3 scripts/smoke/run_browser_acceptance.py --report /tmp/gap-browser.json --keep
# UI2-8 pass（授权边界）、UI2-9 fail（run=running timed_out=True, provisional=1）、UI2-10 pass
sqlite3 <kept DATA_DIR>/db/app.db \
  "select id,status,attempt,max_attempts,settled_at from agent_runs order by id;"
```

## 归属

`agent_queue` 的租约/收敛语义。建议另立最小修复任务（与 D-F1 同族），
不在本验收任务里改业务代码（PRD G-R5：发现业务缺陷退回所属任务）。
