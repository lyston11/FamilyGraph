# G-1 发布准入与只读盘点（2026-09-19）

只读核对，未做任何部署、重启或迁移。所有命令经 `ssh lyston`（systemd user 作用域）。

## 冻结候选版本

| 项 | 值 |
| --- | --- |
| 源码 SHA | `33d8be6`（`main`，已 push 到 origin/main） |
| 迁移 head（代码） | `0051_run_event_timing` |
| 迁移 head（生产库） | `0051_run_event_timing`（**一致**） |
| 关键列 | `agent_run_events.timing_json` ✓、`agent_runs.first_leased_at` ✓ |
| DB | `~/projects/FamilyGraph/backend/data/db/app.db`，29.5 MB，mtime 2026-09-19 08:34 UTC |

生产库 schema 已在 09-18 由前一轮升级到 `0051`，本轮**不需要迁移**。

## 决定性发现：运行进程落后于 Git HEAD（Q09 复现）

远端检出已是最新 `33d8be6`（code-sync 定时器每 30 分钟 pull），但**运行中的服务与
制品停留在 2026-09-18**：

| 运行对象 | 启动/构建时间（北京时间） | PID |
| --- | --- | --- |
| `familygraph-api.service` | 2026-09-18 **17:48** | 635302 |
| `familygraph-agent.service` | 2026-09-18 **18:07** | 645875 |
| `agent/dist/*.js` | 2026-09-18 **18:07** | — |

而待发布修复的提交时间：

| 提交 | 内容 | 提交时间（北京时间） | 是否已加载 |
| --- | --- | --- | --- |
| `46d7ac9` | D-F1：取消裁决不再被当普通冲突 | 2026-09-18 **23:04** | **否** |
| `2d6fab1` | `run.compacted` 压缩归属修正 | 2026-09-19 **01:09** | **否** |
| `9f9f470` | 撤权后 Run 直接终态化 | 2026-09-19 **14:37** | **否** |

**三重证据**（不靠单一信号）：

1. 进程启动时间早于全部三个提交；
2. `dist/worker.js` 与 `dist/client.js` 中 `run.compacted`、`RunCancelledError`
   的出现次数均为 **0**，而源码 `src/events.ts` 中有 5 处；
3. `dist/*.js` mtime = 2026-09-18 18:07，早于 `src/events.ts` 的 2026-09-18 17:30
   之后的任何改动。

因此「远端 HEAD 已更新」「迁移已到 head」都**不能**证明修复已生效——这正是 G-R2
要求核对 PID/启动时间/制品而非只看 HEAD 的原因。

## 拓扑与兼容顺序

- 三 listener 均在 `127.0.0.1`：8000 家庭 / 8001 内部 / 8002 管理员，同一进程 635302。
- sidecar：`node dist/main.js`，cwd `~/projects/FamilyGraph/agent`。
- 远端**无** `frontend/dist`——家庭前端由本地 Vite 经 SSH 隧道提供，故前端制品不在
  本次远端发布范围（本地合并与 HMR 分开记录）。
- **发布顺序硬约束**（约束 #453）：后端必须先于 sidecar。`run.compacted` 若由新
  sidecar 发往旧后端会被 422 拒，导致**整批 append 失败、run 直接失败**。
  回退顺序相反：sidecar → 后端。

## 在途状态与排空

| 状态 | 数量 |
| --- | --- |
| `agent_runs` succeeded | 23 |
| `agent_runs` failed | 3 |
| `agent_jobs` 非终态 | **0** |

无 `queued`/`leased`/`running` 的 Run 或 Job，**无需排空**，不会让已返回未保存的调用
退化为 unknown。3 条 failed 是历史行，不清理、不改写。

## 备份

现有备份（`data/backups/`）：

- `familygraph-20260918-094746.db`（27.1 MB，最近一次）
- `familygraph-20260918-091702.tar.gz`、`familygraph-20260918-094746.tar.gz`
- `pre-reset-20260913-144115.db`

G-2 发布前将再做一次在线备份（用 `python -m app.backup`，禁止直接 cp 运行中主库）。

## 漂移防护

`familygraph-code-sync.timer` 每 30 分钟 `git pull --rebase --autostash` + push。
发布窗口内必须**先暂停该 timer**，否则检出可能在发布中途前进，使「冻结版本」失效。
下次触发：2026-09-19 09:01:33 UTC。

## 门禁基线（候选版本，已在本地验证）

| 检查 | 结果 |
| --- | --- |
| backend `pytest` 全量 | 1716 passed, 3 skipped |
| backend ruff / format / mypy | 全绿 |
| agent vitest / lint / type-check | 167 passed / 全绿 |
| frontend vitest / lint / type-check / build | 767 passed / 全绿 |
| `run_controlled_acceptance.py` | 41/41 |
| `run_browser_acceptance.py` | 22/22 |
| `frontend-api-smoke.sh` | 56/56，退出 0 |

## 结论

- **准入通过**：F 必需项全过，候选版本冻结在 `33d8be6`，无待迁移，无在途任务。
- **G-2 必要且非空操作**：三个待发布修复确实未生效，部署会真实改变运行行为。
- **G-3 仍需单独批准**：真实小样本的费用上限未获授权，本轮不发起任何真实模型请求。
