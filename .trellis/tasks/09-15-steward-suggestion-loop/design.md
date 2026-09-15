# 管家建议闭环：核查结论与技术设计

## 0. 核查结论（推翻 PRD 的 R1/R2 前提）

| PRD 原假设 | 实测结论 |
|---|---|
| R1「`expires_at` 为 NULL 是遗漏，应补 TTL」 | **证伪**。`steward_terminology.py:558` 是 09-14 `c8805bf` 的**刻意覆盖**，注释写明「Optional controls remain available as long as their projection is current. Evidence changes and explicit feedback, rather than a timer, retire them.」。`upsert_suggestion` 对普通 kind 本就写 TTL，NULL 只来自这一行。 |
| R2「建议只增不减，GC 未清理」 | **部分证伪**。`steward_gc.collect()` 确实不碰 suggestions，但读时 `effective_state()` 已判 `superseded`；且远端 535 条**投影全部 alive**（`unchanged`、`term == baseline_term`、`semantic_hash` 匹配），**当前死建议数 = 0**。 |
| R3「需要聚合提醒防刷屏」 | **证伪一半**。`term_preference` 走 `notify=False`（刻意不产生通知），因此**不存在刷屏**；真正的问题是它**在通知中心完全不可见**。 |
| — | **新发现（真正的缺陷）**：`NotificationsView.vue:98` 调用 `suggestions.load()`，但模板**从不渲染其结果**（`activeForSpace` 全项目零引用）——`3c2daac`/`d26bb83` 留下的**未完成接线**。535 条建议因此只在「打开对应人物资料」时可见。这就是「管家没起作用」的直接原因。 |

### 实测证据（隔离库 `/tmp/fg-sg`，生产库在线备份副本）

```
BEFORE                 read-state=proposed   db.status=proposed   projection.status=unchanged
语义依据漂移后          read-state=superseded db.status=proposed   ← 未物化（可接受，见下）
同语义重建（漂移中）    created=False  revision 1→2  semantic updated=True   ← 自愈正常
同语义重建（投影恢复）  created=False  same_row=True                      ← 历史去重挡住重建
```

最后一行是 **R1 字面实现的回归**：`steward_terminology.py:522` 的历史去重查询**不带状态过滤**，
所以一旦按 R1 加上 TTL，过期行会**永久挡住重建**，且注释明确要求「同语义同词的历史终态（含 resolved）也去重：
恢复过/保留过的建议不重生」——即这个"挡住"是**刻意保护**（防止用户已"恢复默认叫法"的建议复活）。

结论：加 TTL 必须同时区分「`expired` 可重建」与「`resolved`/`dismissed` 不可重建」，
否则要么永久抑制、要么让用户已否决的建议复活。两者都是实质行为变更。

### 决策：走 A（不加 TTL），并修正 PRD

- 可选偏好按 09-14 设计**由证据变化与显式反馈退役，而非定时器**；TTL 会与之冲突且引入上述回归。
- 真正要修的是**可见性**：完成 `NotificationsView` 未完成的接线。
- 「只增不减」当前不成立（死建议 = 0）；物化型回收作为后续项，触发条件写入 §5。

## 1. 改动范围

**只改前端**（后端零改动）：

| 文件 | 改动 |
|---|---|
| `frontend/src/views/NotificationsView.vue` | 「待核实」分区在既有通知行之外，增列**服务端授权的建议投影**（含无通知的 `term_preference`），按 suggestion id 去重，只显示 active 状态 |
| `frontend/src/views/__tests__/notifications.spec.ts` | 新增回归：建议渲染、去重、active 过滤、详情弹层接线 |

## 2. 设计

### 数据流

```
suggestions.load(spaceId)            ← 已存在（服务端授权 + 分页 + epoch 防旧响应）
  └─ store.forSpace(spaceId).items   ← 已存在，但从未被渲染
       └─ [新增] 视图内派生：
            verifyFromStore = items
              .filter(state ∈ {proposed, submitted})        # active 才进「待核实」
              .filter(id ∉ sections.verify 的 suggestion_id) # 与通知行去重
```

### 为什么在视图层过滤而不改后端

- 后端 `list_suggestions_page` 已用 `display_actions` 做动作过滤；返回 `superseded` 行时动作仅剩
  `open_details`，是**列表语义**（可回看）而非缺陷，改动它属于扩大范围。
- 前端过滤是最小正确边界：`activeForSpace` 名字承诺 active 但实现返回全部 items，
  在渲染点显式过滤，语义自洽且不动既有后端测试。

### 渲染形态

「待核实」分区内两个块，既有块**逐字不动**：

1. 既有：`sections.verify`（通知引用行）→ `NoticeItemRow` + `openSuggestion`。
2. 新增：建议投影行 → 紧凑行（双方姓名 + 状态徽章 + 建议值摘要）+「查看详情」→ 复用同一个
   `SuggestionReviewDialog`（`openSuggestionById`）。

`SuggestionReviewDialog` 已对 `term_preference` 显示「无需处理」并提供「保留为我的叫法 / 忽略」，
不需要任何改动。

### 不做的事

- 不加 TTL、不动 `existing_any` 历史去重语义（理由见 §0）。
- 不给 `term_preference` 打开 `notify`：会一次性产生 535 条通知（R3 明确禁止刷屏），
  且 09-14 设计已把它定为"可选偏好、无需逐条待办"。
- 不做聚合推送通知：现有 notifications 通道只有领域事件驱动，没有 digest 调度器；
  本任务的可见性修复已让建议进入用户日常路径，聚合推送留给有真实触发条件的后续项。
- 不加分页：`fetchSuggestions` 默认 20 条，单账号最多 26 条（远端实测），
  翻页缺口记为已知限制（§5），不为它引入新的分页状态。

## 3. 兼容性

- 通知行渲染路径与 `classifyNotifications` 语义**零改动**；既有测试不受影响。
- 建议列表加载失败（404/403/503）时 `suggestions.load` 已由 `Promise.allSettled` 隔离，
  不阻塞通知分区；新块在无数据时显示空态而非错误横幅。
- 切空间 / 切账号：沿用 store 既有 epoch 机制，视图侧沿用同一 `spaceId` computed。

## 4. 测试设计

`notifications.spec.ts` 新增（沿用既有 `mountNotifications` + mock 基建）：

1. 建议投影渲染：`fetchSuggestions` 返回 1 条 `term_preference`（无对应通知行）→
   「待核实」分区出现该行。
2. 去重：同一 id 同时出现在通知行与建议列表 → 只渲染一次。
3. active 过滤：`state='superseded'` / `'expired'` 的建议不进入「待核实」。
4. 详情接线：点击建议行的「查看详情」→ `fetchSuggestionDetail(spaceId, id)` 被调用。

## 5. 后续项（本任务不做，写入触发条件）

- **物化型回收**：当 `effective_state` 判 `superseded` 的建议数 > 0 且持续增长时，
  在维护 tick 中把投影已消失的建议物化为 `superseded`。当前实测死建议 = 0，无真实触发条件，
  且批量版 `effective_state` 与读时逻辑存在双源风险，需单独设计与验证。
- **建议列表分页**：单账号 active 建议 > 20 时，通知中心只显示首页。
- **聚合提醒**：若未来需要主动触达，需先有 digest 调度器与「未读建议」读模型
  （`steward_suggestion_recipients.read_at` 已存在但从未写入）。
