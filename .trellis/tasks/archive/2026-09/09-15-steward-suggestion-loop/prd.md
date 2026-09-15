# 管家建议闭环：可见性与状态语义修正

> 父任务：09-15-agent-audit-remediation。优先级 P1。

## 背景

远端库 535 条 `steward_suggestions` 全部 `proposed`、`expires_at` 全为 NULL、0 条被 accept/dismiss。用户观感"管家没起作用"。

原 PRD 假设该现象源于「TTL 未落行 + GC 不清理 → 只增不减」。**核查后该假设被证伪**，详见 `design.md` §0 的实测证据：

- `expires_at=NULL` 是 09-14 `c8805bf` 在 `steward_terminology.py:558` 的**刻意设计**（可选偏好由证据变化与显式反馈退役，而非定时器）；普通 kind 本就写 TTL。
- 远端 535 条建议对应的投影**全部 alive**（`unchanged`、`term == baseline_term`、`semantic_hash` 匹配），**当前死建议 = 0**。
- `term_preference` 走 `notify=False`，**不存在刷屏**问题。
- **真正的缺陷**：`NotificationsView.vue:98` 加载了建议列表但模板从不渲染（`activeForSpace` 全项目零引用）——`3c2daac`/`d26bb83` 留下的未完成接线。535 条建议因此只在打开对应人物资料时可见。

## Requirements

- R1（**已按核查结论放弃**）：不再为 `term_preference` 补 TTL。理由：与 09-14 设计冲突；且 `steward_terminology.py:522` 的历史去重不带状态过滤，加 TTL 会导致过期建议**永久挡住重建**，而该"挡住"对 `resolved` 是刻意保护（防已恢复默认叫法的建议复活）。两者不可用同一条件区分，属实质行为变更，需单独设计与验证。
- R2：**修正 `activeForSpace` 的语义**——store 注释与名称承诺返回 `proposed`/`submitted` 的活跃建议，实现却返回全部 items。改为兑现承诺（`SUGGESTION_ACTIVE_STATES` 同口径）。
- R3：**完成通知中心接线**——「待核实」分区渲染服务端授权的建议投影，使 535 条建议进入用户日常路径。
  - 与既有通知引用行**按 suggestion id 去重**，同一建议不出现两次。
  - 建议投影行不携带通知载体，因此**不标记已读**，只提供「查看详情」。
  - 空态条件改为「通知引用行与建议投影行皆空」。
  - 复用既有 `SuggestionReviewDialog`（`term_preference` 已显示「无需处理 / 保留为我的叫法 / 忽略」），不改弹层。
- R4：不做聚合推送通知。现有 notifications 通道仅由领域事件驱动，无 digest 调度器；且 `notify=False` 意味着不存在刷屏，无真实触发条件。
- R5：测试：建议投影渲染、与通知行去重、非 active 状态过滤、详情接线、`activeForSpace` 过滤。

## Acceptance Criteria

1. `cd frontend && npm run lint && npm run type-check && npm test && npm run build` 全绿。
2. 新增回归测试证明：无对应通知行的 `term_preference` 建议出现在「待核实」分区；
   同一 id 在通知行与建议列表同时存在时只渲染一次；`superseded`/`expired` 不进「待核实」。
3. `activeForSpace` 只返回 `proposed`/`submitted`，既有 store 测试不回归。
4. 后端零改动；`notifications` 分区的既有分类语义与测试逐字不变。
5. 部署后远端 `/notifications` 页面「待核实」分区可见称谓偏好行并可打开详情。

## 设计取舍说明

- 在**视图层**过滤而非改后端：后端 `list_suggestions_page` 返回 `superseded` 行时动作仅剩 `open_details`，是「可回看」的列表语义，改它属于扩大范围。前端渲染点显式过滤是最小正确边界。
- 不做建议分页：`fetchSuggestions` 默认 20 条，单账号最多 26 条（远端实测），缺口记为已知限制。
- 物化型回收（把投影已消失的建议写为 `superseded`）留作后续项：当前死建议 = 0 无触发条件，且批量 `effective_state` 与读时逻辑存在双源风险，需单独设计与验证。

## 回滚

store 过滤与视图渲染各自独立 commit，可分别 revert；revert 后回到 09-14 状态（建议只在人物资料页可见），无数据迁移、无 schema 变更。
