# E2 技术设计：sidecar `KindAdapter` 与 Pi 协议补齐

> 父任务 `09-25-steward-pi-child-run-design/design.md` §9 是本设计的权威来源；
> 本文只记录 E2 的落地形状与两处 E1 遗留缺陷的修法。

## 1. 现状（实测，不是推断）

E1 把后端租约端点从 `/steward/jobs/lease` 改名为 `/steward/attempts/lease`，并把
`StewardLeaseRequest` 加了必填 `space_id`，但 sidecar 侧没有同步。实测（真实 internal app）：

```
sidecar's current path -> 404 {"detail":"Not Found"}
new path w/o space_id -> 422 missing body.space_id
```

第二处：`executeJob` 的 steward 分支从不把产物交给 settle。后端
`_settle_steward_run` 把 `body.output_text` 传进 `settle_attempt(text=...)`，而
`_settle_attempt` 有 `assert text is not None`——所以即使端点修好，第一条 steward child run
也会在结算处崩。两处都不在 E1 的测试面内（E1 测的是服务层与真实 HTTP 的后端侧，
没有覆盖 sidecar→后端这一跳）。

## 2. sidecar 结构

### 2.1 `KindAdapter`

```ts
export interface KindAdapter {
  readonly kind: AgentKind;
  readonly systemPrompt: string;
  readonly emptyToolAllowlistIsInvalid: boolean;
  toolNames(): string[];
  cacheKey(projection: RunContextProjection): string;
  /** 校验该 kind 专属的投影字段；不通过即 fail-closed 抛错。 */
  verifyProjection(projection: RunContextProjection): void;
  /** 是否采用服务端广播的并发上限（只有 steward 有该字段）。 */
  readonly adoptsServerConcurrency: boolean;
  /** 是否在 settle 时携带结构化产物（assistant 走消息事件，steward 走 settle）。 */
  readonly reportsProductOnSettle: boolean;
  /** 从会话终态取出要上报的产物文本；不适用时返回 null。 */
  extractProduct(finalText: string | null): string | null;
  /** 租约请求的路径与请求体。 */
  leaseRequest(config: AgentConfig): { path: string; body: Record<string, unknown> };
}
```

`adapterFor(kind)` 是唯一入口，`ASSISTANT_ADAPTER` / `STEWARD_ADAPTER` 两个冻结实例。

### 2.2 空间发现（`space_id` 从哪来）

sidecar 不知道有哪些空间，也不知道哪个空间有到期工作。三种方案里选**后端选空间**：

| 方案 | 否决理由 |
|---|---|
| sidecar 轮询空间列表 | 新增端点 + 客户端状态，且 sidecar 不该知道空间拓扑 |
| sidecar 固定一个空间 | 与「per-space 并发」直接矛盾 |
| **后端在未给 `space_id` 时自行选一个有容量且有到期工作的空间** | 最小改动，且 per-space 预算仍在 `lease_attempt` 内部裁决 |

`StewardLeaseRequest.space_id` 改为**可选**。显式给定时行为不变（用于测试与定向排空）；
省略时 `lease_attempt` 选空间：先取有到期 `reserved` attempt 的空间集合，再取这些空间的
在途计数，返回第一个未达 `STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE` 的空间。

**这不回退到全局 1**：预算是 per-space 的，且选择会跳过已满的空间——两个空间仍可同时推进。
（E1 在 schema 注释里写「必须指名空间」，其担忧是「不带 space_id 就丢掉 space 过滤」；
本设计保留过滤，只是把「选哪个空间」从调用方移到服务端。）

### 2.3 产物上报

`stewardAdapter.reportsProductOnSettle = true`，`extractProduct` 返回模型终态文本
（即 steward prompt 要求的那段 JSON）。`executeJob` 结算时：

```ts
await this.client.settleRun(job.run_id, job.run_token, "succeeded",
  undefined, adapter.reportsProductOnSettle ? { output_text: product } : undefined);
```

`assistantAdapter.reportsProductOnSettle = false`，其空产物判定（`PROVIDER_EMPTY_ANSWER`）
**只对 assistant 生效**：steward 的空文本由服务端封闭 schema 校验判为 `degraded`，
sidecar 不该替它下结论。

## 3. 不变量（不得削弱）

- 槽位模型（多槽 Map / 预留 / 按 `run_id` 定位 / 按 kind 独立预算）不动。
- `leaseIntoSlot` 与 `tryLeaseAndRun` 的分工不动。
- `FG_AGENT_ROLE` 默认 `assistant`；四种 kind 的 carrier 仍全为 `inproc`（E3 才开）。
- 双开关：steward 租约端点仍要求 `STEWARD_ENABLED` AND `STEWARD_PI_RUNTIME_ENABLED`。

## 4. 回退

`FG_AGENT_ROLE=assistant`：sidecar 完全不租 steward，回到 S1 之前的 assistant-only 行为。
E2 不涉及迁移与数据。

## 5. 验证

- `adapters/kind.test.ts`：每条适配器差异一个用例（含变异测试：删任一条成员会失败）。
- `worker-slots.test.ts` / `poll-scheduling.test.ts` 回归不变。
- 结构性断言：`executeJob`/`buildRunSession` 内无 kind 字面量比较。
- agent 全量：type-check / lint / test / build。
- 后端：steward 租约端点省略 `space_id` 时能选到有工作的空间（真实 HTTP）。
