# ActionCard 前端代码规范

> V2.4 跨层合同。服务端 ActionCard 状态、权限和错误是唯一真源；前端只渲染安全投影并发起用户明确的动作。

## 1. Scope / Trigger

- 触发范围：ActionCard Inbox、Assistant 消息中的卡片引用、空间切换、登出/撤权和卡片状态操作。
- 适用代码：`types/actionCard.ts`、`api/actionCards.ts`、`stores/actionCards.ts`、`components/actioncard/ActionCardItem.vue`、`ActionCardInbox.vue`、`agent.ts`、`MessageList.vue`、`PanelContent.vue`。
- Inbox 和 Assistant 必须按当前 `space_id` 消费同一个 store 和同一个 `ActionCardItem`，不创建第二套卡片状态或动作实现。

## 2. Signatures (API/store/component)

### API

```ts
fetchActionCards(spaceId: number, state?: ActionCardState): Promise<ActionCard[]>
viewActionCard(cardId: number): Promise<ActionCardTransitionResponse>
dismissActionCard(cardId: number): Promise<ActionCardTransitionResponse>
acceptActionCard(cardId: number): Promise<ActionCardTransitionResponse>
executeActionCard(cardId: number): Promise<ActionCardExecuteResponse>
```

所有函数调用 `/api/action-cards`；execute 发送空 JSON body `{}`，不接受浏览器生成的授权/目标参数。

### Store

```ts
loadForSpace(spaceId: number): Promise<void>
ensureLoaded(spaceId: number): Promise<void>
transition(spaceId: number, cardId: number, action: 'view'|'dismiss'|'accept'): Promise<void>
execute(spaceId: number, cardId: number): Promise<void>
resetForSpace(spaceId: number): void
clear(): void
```

`CardPartition` 为 `cards/loading/loaded/hidden/error`，按 space 分区；action 成功后以服务端响应回填并刷新同空间列表。

### Component

```ts
<ActionCardItem :card="ActionCard" />
<MessageList :space-id="number | null" />
```

`ActionCard` 必须包含后端的 `id`、`kind`、`space_id`、参与者、`evidence`、`proposed_action`、`privacy_effect`、`state`、`expires_at`、`created_at`、`revision`。

## 3. Contracts (request/response/state)

- kind 只允许 `household_link | lineage_request`；动作只允许 `create_household | request_lineage`。不得把后端值改写为 `join_space`、`create_shared_household` 或 `lineage_join`。
- 卡片状态为 `pending | viewed | accepted | executed | dismissed | expired | superseded`。pending/viewed 显示了解详情、不接受、接受；accepted 只显示“发起申请”；四个终态只读。
- `accept` 只表示用户接受建议，不调用领域命令；accepted 的“发起申请”必须先打开确认弹层，再由用户显式确认调用 execute。
- 确认弹层必须再次展示目标空间、动作和 `privacy_effect`；execute 成功后刷新 store。execute 409 失败保留 accepted 可重试，410 失败按服务端刷新为终态。
- `MessageList` 只从当前空间 partition 找到消息引用的 card；找不到空间、卡片或有效权限时只显示文本，不凭 id 拉取跨空间详情。
- `agent.ts` 只接受 payload 中正整数 `card_ids`、`card_refs` 或单个 `card_id`，去重并过滤非法值；不把任意 payload 对象当作卡片。
- `PanelContent` 在面板打开且当前空间确定时调用 `ensureLoaded(spaceId)`。空间切换 reset 旧 partition；auth.clearSession 清空全部卡片缓存。

## 4. Validation & Error Matrix

| 条件 | 前端行为 |
|---|---|
| kind/action 未知 | 通用标题/动作降级，禁止执行未知动作 |
| 403 `SPACE_FORBIDDEN_ACTOR` 或 503 flag 关闭 | 当前 partition `hidden=true`，隐藏 Inbox 入口 |
| 409 `CARD_STATE_CONFLICT` | 刷新同空间列表，保留服务端状态并提示 |
| 409 `CARD_EXECUTE_REJECTED` | 刷新同空间列表，保留 accepted 可重试，展示 `detail.reason` |
| 410 `CARD_EXPIRED` 或过期终态 | 刷新同空间列表，终态只读 |
| 网络/未知错误 | 保留 error/retry UI，不乐观改变服务端状态 |
| 无 current space 或 card 不在该 partition | 不渲染 ActionCard，不跨空间请求 |
| 登出、账号切换、撤权 | `clear()` 清理全部 partition；不得留敏感卡片内存 |

API 错误统一通过 `ApiError` 和 `friendlyActionCardError` 映射；不能以 404/403 差异向用户证明卡片存在。

## 5. Good/Base/Bad Cases

- Good：Assistant SSE 只携带 `{card_ids: [3]}`，当前空间已加载卡片 3，MessageList 复用 Inbox 的 ActionCardItem；点击操作后两个入口都由同一 partition 刷新。
- Good：用户点击 accepted 卡的“发起申请”，确认弹层显示目标 LineageSpace 和隐私影响；点击“再想想”不会调用 execute。
- Base：pending 卡可以直接点击接受，状态进入 accepted；dismiss 后 store 重新加载，badge 不再把它计为 pending。
- Bad：从消息 payload 直接渲染完整卡片对象、按 card id 跨空间请求，或在组件内把 state 改成 executed；这些都绕过服务端授权/状态真源。
- Bad：点击“接受”就调用 execute，或 execute rejected 后显示“申请已发送”；这会把建议确认误当成最终领域命令。

## 6. Tests Required (with assertion points)

- 类型/API：fixture 使用后端 kind/action，响应包含 revision，execute 请求 body 为 `{}`。
- 组件 FSM：pending/viewed/accepted/四种终态的按钮可见性、文案、原因/证据/隐私/有效期渲染；pending 直接 accept 的回填。
- 两步确认：accepted 先开弹层；取消不调用 execute；明确确认才调用；409 rejected 保持可重试。
- Store：按 space 分区加载和刷新、hidden/error 降级、并发冲突/过期处理、resetForSpace/clear 清理。
- Assistant：card_ids/card_refs/单 card_id 解析、正整数过滤与去重；消息无当前空间或卡片不在 partition 时不渲染。
- 共享入口：Inbox 与 Assistant mount 同一 ActionCardItem/store；一个入口 view/dismiss/accept/execute 后另一入口观察到刷新状态。
- 运行门禁：`npm run type-check`、`npm run lint`、`npm test`、`npm run build`。

## 7. Wrong vs Correct

### Wrong

```ts
// 接受建议就直接发送申请，并相信消息里的完整对象。
await executeActionCard(message.card.id)
message.card.state = 'executed'
```

### Correct

```ts
// 接受只走 FSM；最终 execute 必须来自 accepted 卡的显式确认，
// 请求不携带客户端生成的 target/权限数据，结果回填同空间 store。
await actionCards.accept(spaceId, card.id)
// 用户在确认弹层点击“确认”后：
await actionCards.execute(spaceId, card.id)
```

原因：浏览器只是呈现建议和用户意图，最终权限、事实 revision、目标空间和命令 FSM 必须由后端重新校验。
