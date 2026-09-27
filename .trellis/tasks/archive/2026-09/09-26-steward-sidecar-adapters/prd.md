# E2 Steward sidecar KindAdapter：executeJob 零 `if kind`

> 父任务：`09-25-steward-pi-child-run-design`（`design.md` 是技术权威，本文只记录 E2 的落地边界）。
> 本阶段**不启用** Pi 载体（四种 kind 仍全走 `inproc`），目标是让 sidecar 的 kind 差异
> 收敛到一个适配器，并把 Pi 协议补齐到「一旦打开开关就能工作」。

## Goal

`executeJob` 目前把「run 生命周期」（与 kind 无关）和「会话语义」（与 kind 有关）混在一个函数里，
kind 分支散落 5 处。把后者抽成 `KindAdapter`，使 `executeJob` 零 `if kind`，并补齐 Pi 协议里
两处缺失（E1 改名后未同步的租约端点、settle 未携带产物）。

## Requirements

### R1 适配器承载全部 kind 差异

`agent/src/adapters/kind.ts` 定义 `KindAdapter`，`assistantAdapter` / `stewardAdapter` 两个实现。
适配器**必须**覆盖 `executeJob` 与 `buildRunSession` 里现有的每一处 kind 判断：

| 现有分支 | 适配器成员 |
|---|---|
| `session.ts` systemPrompt 三元 | `systemPrompt` |
| `session.ts` cacheKey 三元 | `cacheKey(projection)` |
| `session.ts` `emptyIsInvalid = kind === "assistant"` | `emptyToolAllowlistIsInvalid` |
| `session.ts` `toolNamesFor(kind)` | `toolNames()` |
| `worker.ts` steward prompt 版本校验 | `verifyProjection(projection)` |
| `worker.ts` `adoptServerConcurrency`（仅 steward） | `adoptsServerConcurrency` |
| settle 是否携带产物 | `reportsProductOnSettle` |

### R2 补齐 Pi 协议的两处缺失（E1 遗留）

1. **租约端点**：后端已把 `/steward/jobs/lease` 改名为 `/steward/attempts/lease`，且新请求体
   **要求 `space_id`**（`lease_attempt` 按空间选行）。sidecar 仍打旧路径 → 404。
   `adapter.leaseRequest()` 提供 `{path, body}`，并由适配器声明如何解析响应。
2. **产物上报**：steward 不能走消息类事件（后端 422 拒绝），产物必须随 settle 的
   `output_text` 回传；当前 sidecar 从不发送，导致 `settle_attempt` 拿到 `text=None`
   而在 `_settle_attempt` 的 `assert text is not None` 处崩溃。适配器用
   `reportsProductOnSettle` + `extractProduct(text)` 表达。

### R3 不得削弱的既有合同

- 槽位模型（多槽 Map、预留、按 `run_id` 定位、按 kind 独立预算）**不动**：S1 已证明正确。
- `tryLeaseAndRun` / `leaseIntoSlot` 的分工**不动**（pollLoop 里 await 会把槽位串行化）。
- 行为等价：`FG_AGENT_ROLE` 默认 `assistant`，四种 assist kind 仍全走 `inproc`，
  `steward_model_calls.run_id` 仍全 NULL。

## Acceptance Criteria

- [x] AC1 `executeJob` / `buildRunSession` 中不再出现 `agent_kind === "steward"` 之类的分支
      （结构性断言：源码里 kind 判断只允许出现在 `adapters/` 与 `config.ts`/`tools.ts` 的注册表；
      变异测试验证）。
- [x] AC2 新增 `adapters/kind.test.ts`：两个适配器的每条差异各有用例（systemPrompt 不同、
      toolNames 空/非空、cacheKey 公式、prompt 版本校验、产物提取、租约请求体）。
- [x] AC3 租约请求：steward 打到 `/internal/agent/steward/attempts/lease`；assistant 仍打
      `/internal/agent/jobs/lease`。**实际交付与本文原描述相反**：steward body **不含**
      `space_id`——见下方 Notes 的修正。
- [x] AC4 steward settle 携带 `output_text`；assistant settle 不携带。
- [x] AC5 回归：`worker-slots.test.ts` / `poll-scheduling.test.ts` 全绿且语义不变。
- [x] AC6 agent 全量：`npm run type-check && npm run lint && npm test && npm run build`（200 tests）。

## 交付范围修正（相对本 PRD 初稿）

1. **`space_id` 由服务端选，不由 sidecar 传**。本文 Notes 原写「后端在未收到 `space_id` 时返回
   可租空间列表」，实际改为**服务端直接选一个有容量且有到期工作的空间**（`space_id` 可选）。
   理由：返回列表会让 sidecar 需要多一次往返与本地状态，而选择权本来就该在服务端；
   且该选择会跳过已满的空间，**不回退到全库 1**（有变异测试验证的回归）。
2. **本任务实际覆盖 E3 + E4**。E2 的两处协议修复（租约端点、产物上报）只有把 Pi 载体真正跑起来
   才能证明，因此 terminology 与其余三类的载体迁移与验收一并完成。详见 `implement.md`。
3. **E5 未执行**：其前置「生产已切换并稳定」不成立，理由见 `implement.md` §5。

## Notes

- E2 **不打开** Pi 载体：`STEWARD_ASSIST_<KIND>_CARRIER` 仍全为 `inproc`，因此 R2 的两处修复
  在本阶段是「协议正确但未被调用」。这是刻意的分层：E2 让协议正确，E3 才启用它。
- sidecar 无法自行得知要租哪个空间：`space_id` 必须由后端在**某个**响应里给出，或由 sidecar
  按空间逐个轮询。E2 采用前者——后端在 steward 租约端点未收到 `space_id` 时返回可租空间列表，
  具体形状见 `design.md`。
- 依赖：E1（已合并）。
