# 人物重复建档防护与 Steward 回溯审计：决策记录

## 2026-09-03 · 规划基线：核心已随 personal-family-view 交付

规划时深入代码发现：本任务标题下的两大能力**已随 8c1c892（09-01-personal-family-view）提交进主干**，并已固化为 `.trellis/spec/architecture.md` §0.9 权威合同：

- 判定口径唯一真源 `services/person_identity.py`（归一/强度/两入口）；
- 建档写入门禁 `commands/members.py` `_guard_duplicate_person`（strong 拒绝、weak 消歧、BEGIN IMMEDIATE 并发保证）；
- Steward 回溯审计 `services/steward.py` `_detect_duplicate_persons`（发现项 → `steward.conflict_detected` 事件 + checkpoint 签名幂等）;
- 回归测试 `tests/test_person_dedupe.py`。

因此本任务 PRD 采用"已交付基线 + 回归保护"结构：R1 把现状固化为需求，AC-1 是回归保护性质；真正的增量是处置闭环与失效联动。

## 本轮规划决策

### 合并处置的范围裁定

- **只允许合并双方均 managed（未认领）的重复档案**：每个 User 携带可登录凭据，认领本人的合并是身份冲突，走既有 `claim_dispute` 人工兜底；两条路径不可混用。
- **不搬运档案字段**：survivor 的 name/birth 保持原值（归一永不覆写存储值），字段取舍由创建者后续手动编辑——避免"择优"语义和第二套字段真源。
- **改指向 + 硬删除**：`space_profile_refs`、`source_facts`（两端）、`attachments` 先改指向 survivor 再删 retired 行，与其余 FK CASCADE 清理兼容（FK survey 结论见 design §2.2）；DerivedFact/卡片等可重建行交由 CASCADE + 下轮作业。
- **SourceFact 指向迁移走 revision+1 + `source_fact.revised` 事件**：指向变化不是内容变化，不新建行、不 supersede，append-only 由事件与 audit 保证。

### 失效联动的边界

- 本任务只向 `domain_events.py` 失效监听**追加 `profile.` 前缀**（merged/deleted/created/updated），并给 `profile.deleted` 补 `payload.space_ids`。
- 调查中发现的相邻缺陷——实际发射的事件是 `space.membership.changed` 而监听前缀是 `space_member.`（后者从未被发射）——**不在本任务修**，归 in_progress 的 `09-02-personal-family-view-followup`（PFV-F4 回归面），PRD Out of scope 已记录，实现时须协调 `domain_events.py` 的合入顺序。

### 明确不做

- 发现项 ActionCard 化/通知投影：`CARD_KINDS` CHECK 只有两种 kind，扩 kind 是独立变更；处置入口 = 显式命令 + 最小只读查询端点。
- Steward 自动合并：合并是显式两步确认命令（R8 of personal-family-view：Steward 不创造正式事实）。
- 跨空间批量去重、全局唯一索引、zhconv 异体字增强（§0.9 已知缺陷维持 weak 消歧路径）。
