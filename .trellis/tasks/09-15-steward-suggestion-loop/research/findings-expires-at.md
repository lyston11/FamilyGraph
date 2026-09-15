# 事实核查：expires_at 为何为 NULL（R1 前提核实）

## 结论：R1 的前提与代码现状冲突，且按 R1 原样实现会引入回归

### 1. `expires_at=None` 是 2026-09-14 的**刻意设计**，不是遗漏

`backend/app/services/steward_terminology.py:558`（提交 `c8805bf` "fix(steward): close kinship authorization and terminology lifecycle gaps"）：

```python
suggestion.expires_at = None
# Optional controls remain available as long as their projection is current.
# Evidence changes and explicit feedback, rather than a timer, retire them.
```

`upsert_suggestion` 对普通 kind 已经写 `expires_at or default_expires_at(now)`（`steward_suggestions.py:219`），
所以 NULL 只来自这一行的显式覆盖。远端 535 条全是 `term_preference`（`kind` 分组实测），与代码路径一致。

### 2. 存量数据来源：一次性批量回填，不是长期沉淀

```
created_at 直方图：
2026-09-15 06:53 → 6
2026-09-15 06:54 → 172
2026-09-15 06:55 → 357
```

535 条集中在 3 分钟内产生（96 个不同 term × 30 个 viewer），是术语投影首次生成时的一次性批量产出，
不是"只增不减"的长期累积证据。按账号分摊后每人 1–26 条（account 1 / space 2 仅 4 条）。

### 3. 按 R1 原样加 TTL 会引入**具体回归**：过期后永久抑制重建

`steward_terminology.py:522-539` 的历史去重查询**不带 status 过滤**：

```python
existing_any = db.scalar(
    select(StewardSuggestion)
    .where(... StewardSuggestion.kind == "term_preference",
             viewer_account_id, subject_user_id, object_user_id,
             value_json["term"], value_json["concept_code"] ...)
    .order_by(StewardSuggestion.id.desc()).limit(1)
)
if existing_any is not None:
    ...
    return existing_any, False        # 任何历史行（含 expired）都抑制重建
```

因此：`expires_at` 到期 → 用户看到建议消失 → 投影下次刷新时**无法重建**（被过期行挡住）。
有效偏好的提示被定时器永久压制，且没有任何路径能恢复。

要做对 R1，必须同时改历史去重的语义（允许过期后重建），这是一处实质行为变更，不是"补一个字段"。

### 4. 真正的缺陷：没有任何物化型回收，死建议永远留在列表里

- `steward_gc.collect()`（`steward_gc.py`）只回收 generation/view/intent/publication，**完全不碰 steward_suggestions**。
- `effective_state()` 会在读时把投影已失效的建议判为 `superseded`，但**不回写 status**；
  行永远保持 `proposed`，继续被 `list_suggestions_page` 取出并序列化（`display_actions` 降级为 `["open_details"]`）。
- 结论：R2 指向的缺陷真实存在（无物化回收），但修法应是**物化读时判定**，而不是引入 TTL 定时器。

### 5. 提醒闭环（R3）现状

- 建议通知通道存在且已去重：`notifications` 表 `kind='steward_suggestion'`，
  UNIQUE `(recipient_account_id, space_id, suggestion_id)`，`_record_suggestion_notifications` 幂等。
- 但 `term_preference` 走 `notify=False`（`upsert_term_preference_suggestion`），**不产生任何通知**；
  远端 `notifications` 表只有 21 条 `action_card`，无一条 `steward_suggestion`。
- 前端 `NotificationsView` 的"待核实"分区只渲染通知行，`suggestions.load()` 的结果**没有渲染**；
  term_preference 仅出现在 `KinshipTermPanel`（按 target 单人查询）中。
- 所以 535 条建议对用户的实际可见面 = 打开对应人物资料抽屉时的一小块区域。这解释了"管家没起作用"的观感。
