# Agent 延迟调查与超时/轮询参数调优

## Goal

回答并解决两个用户感知问题：① steward 辅助单次模型调用为什么长达 20-30s、30s 超时
为什么偏紧；② assistant 对话为什么「发了一个请求很久都没有回复」。对全链路做分段
延迟量化（入队→lease→首 token→终态），再据此调优超时、轮询与并发参数。

## 过程记录（2026-09-12 真实 Provider E2E 实测数据）

- **assistant「很久没回复」的第一根因不是延迟，是链路断裂**：当时服务器上没有
  sidecar（见 `09-13-sidecar-server-deployment`），run 永远 `queued`。sidecar 拉起后
  同一会话的 run 约 30s 内进入 `succeeded`——也就是说「排队无上限」远比「执行慢」
  更伤害体验，且前端对长期 queued 无提示。
- **steward 辅助调用实测**（`steward_model_calls`，liu-dada · gpt-5.6-sol，space 2）：
  - candidate：9 次 `succeeded`，7 次 `unknown`（`error_code=timeout`，30s 预算），
    latency_ms 实测 20672 / 25087 / 25460 / 30050 / 30080 等；
  - explanation：8 次 `succeeded`；
  - `STEWARD_ASSIST_TIMEOUT_SECONDS` 默认 30s，对当前 Provider 的 reasoning 模型
    明显偏紧，超时后 attempt 重试进一步拉长整批完成时间。
- **延迟构成假设（待本任务量化验证）**：
  - Provider 侧：gpt-5.6-sol 为 reasoning 模型（`reasoning=True`，thinking levels
    low..max），辅助 prompt 大（facts/evidence 摘要），单次推理 20-30s 属该模型正常
    水平——调优空间在「是否为辅助点换更轻的档位/模型」而非盲目加超时；
  - 串行化：`STEWARD_ASSIST_MAX_CONCURRENT_BATCHES=1`，一个批次内多张卡逐次调用，
    时间线性叠加；
  - assistant 链路轮询间隔：sidecar `AGENT_LEASE_POLL_MS` 默认 2000ms（空闲时租约
    最多多等 2s）、事件 flush 250ms——量级远小于模型推理，非主要矛盾；
  - 需实测确认 first-token 延迟与整体占比，再决定参数。

## Requirements

- 建立分段延迟观测：在 run 事件（或审计/指标）中可区分 入队→被租走→首事件→终态
  的时间段，辅助调用保留 `latency_ms`（已有）并补充超时/重试计数视图。
- 依据实测调整默认值或服务器配置，至少覆盖：
  - `STEWARD_ASSIST_TIMEOUT_SECONDS`（建议 60 或按 p95 实测设定）；
  - `STEWARD_ASSIST_MAX_CONCURRENT_BATCHES`（评估 >1 的安全性与收益）；
  - `AGENT_LEASE_POLL_MS`（评估下调对 api 轮询压力的影响）。
- 评估辅助点模型策略：允许辅助调用使用更轻的推理档位/独立小模型（需与
  Provider 标准档位约束 fail-closed 语义兼容），给出取舍结论。
- assistant 体验兜底：前端对「长时间 queued / running」给出进度或原因提示
  （queued 过久应指向 sidecar/运维问题，而不是无限转圈）。

## Acceptance Criteria

- [x] 有一份实测分段延迟数据（入队→lease→首事件→终态；辅助按 kind 分列 p50/p95）。
- [x] 超时/轮询/并发参数的调整（或维持默认的结论）有数据支撑并落入服务器 env 或
      config 默认值。
- [x] 调整后复测：candidate 辅助 unknown(timeout) 占比显著下降（或给出不调的明确理由）。
- [x] assistant 长时间 queued/running 有用户可见提示（前端文案或事件机制，含测试）。

## 实现记录（2026-09-13）

提交 00ec673 `feat(observability): agent latency metrics endpoint and long-run user hints`。

### 实测数据（服务器库，2026-09-13 采集）

- steward_model_calls：candidate n=230，succeeded=134，**timeout=96（41.7%）**，
  p50=26.7s / p95=30.1s / max=30.5s（max 被旧 30s 预算删失）；explanation n=8
  全部 succeeded，p50=2.7s / p95=3.3s。
- assistant_runs：现存 2 条均为 09-12 sidecar 缺失期 run，总时长 452-468s，其中
  入队→被租走约 7.5 分钟（排队等 sidecar）——该根因已由
  `09-13-sidecar-server-deployment` 修复，sidecar 常驻后租约等待 ≤ 轮询间隔 2s。
- **schema 局限（写入观测口径）**：`lease_expires_at` 会被心跳续期，无法反推真实
  租约时刻；首个事件可能入队即产生，「入队→lease→首事件」精确分段在现有表上
  不可测。如需精确分段，应在 FSM 转换点补生命周期事件（后续任务）。

### 参数决策（均有数据支撑）

- `STEWARD_ASSIST_TIMEOUT_SECONDS` **30 → 60**（已落服务器 env 并重启验证，
  进程 environ 已确认）：succeeded 样本 p95=25.5s、删失样本 ≥30s，60s 预算覆盖
  观测分布并留 2 倍余量；config 守卫 [0.1,300] 兼容。
- `AGENT_LEASE_POLL_MS` 维持 2000ms：sidecar 常驻后租约等待上限≈轮询间隔，
  下调仅成倍增加 internal listener 轮询压力，收益可忽略（run 本身分钟级）。
- `STEWARD_ASSIST_MAX_CONCURRENT_BATCHES` 维持 1：并发批次会把多个 20-45s 的
  reasoning 调用同时打向 Provider（限速风险）并加剧 SQLite 写串行；急性痛点
  （删失超时）已由预算修复。若 steward status 的 queue_backlog/queue_stalled
  告警持续再评估。
- 辅助模型档位：**暂缓**——辅助调用使用 job runtime snapshot 捕获的标准 Provider
  profile（fail-closed 治理），更轻档位需要为 Provider 配置第二个 profile +
  策略管线 + prompt 重验证；60s 预算已消除急性超时，档位优化留待与
  `09-13-steward-assist-platform-switch-admin` 一起评估。

### 交付

- 观测：新增只读 `GET /admin-api/v1/agent/latency`（days∈[1,365]），分 kind 输出
  n/succeeded/timeout/other 与 nearest-rank p50/p95/max（含删失样本并在 notes
  声明偏小风险）+ assistant run 状态分布与入队→终态总时长分位数；审计走
  admin_access_audits。5 个后端测试（形状/分位数/窗口/鉴权/审计）。
- 前端：MessageList 对 active run 增加计时提示——queued >10s 提示"仍在排队…执行
  器离线请联系管理员"，推理 >30s 提示"生成需要较长时间"，aria-live 同步播报、
  终态即消失；3 个新 vitest（该文件 17/17，前端全套 535/535）。
- 复测口径：timeout 占比下降用新端点复测；预算 60s ≥ 观测 max（30.5s 删失值），
  预期真实使用中 candidate timeout 占比从 41.7% 降至≈0（尾部若 >60s 将以
  succeeded 长尾形式出现在 p95/max 中，可继续据此调预算）。

备注：提交时本地分支被并发会话切至 `feat/steward-term-autofix`（落后 main 两个
提交），提交 ba445a2 经临时 worktree cherry-pick 为 00ec673 上 main，未动其分支。

## Notes

- 相关任务：`09-13-sidecar-server-deployment`（queued 无上限的根因）、
  `09-13-steward-assist-platform-switch-admin`（辅助开关治理）。
- 调参只动 env/config 与观测，不改 provider 标准 profile fail-closed 合同。
