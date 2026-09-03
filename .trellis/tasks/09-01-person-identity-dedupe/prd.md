# 人物重复建档防护与 Steward 回溯审计

## Goal

FamilyGraph 的同一空间内不得存在同一个人的两份档案：每个 `User` 携带一个 `Account` 与一次性 PIN，重复建档等于多出一份可登录凭据，是身份问题而不只是数据质量问题。

本任务承接 `09-01-personal-family-view` 交付的基础（其提交 8c1c892 已包含判定口径、建档门禁与回溯审计，并固化为 `architecture.md` §0.9），完成两件事：

1. **固化已交付基线**：把"判定口径唯一真源 + 建档写入门禁 + Steward 回溯审计"的现状记录为本任务的已交付需求，验收标准以回归保护形式存在，防止后续任务漂移口径。
2. **补齐处置闭环与失效联动**：回溯审计目前只能"发现问题"（`steward.conflict_detected` 事件），没有处置手段；本任务新增残留重复档案的合并命令，并把人物身份事件接入 PersonalFamilyView 失效链路——这是 personal-family-view PRD 交给本任务的职责（"唯一人物真源和重复建档审计"、"人物合并/拆分必须使受影响视图失效"）。

## Background / Confirmed Facts

以下能力已随 8c1c892 交付并受 `architecture.md` §0.9 约束，本任务不重做、只回归保护：

- 判定口径唯一真源 `backend/app/services/person_identity.py`：姓名归一（NFKC → 去空白/分隔符 → 繁转简 → casefold）、生日归一（农历读 `mirror_date`，缺失才现算）、`strong/weak/none` 三档强度；键只在查询时现算，不落列、不建唯一索引，归一永不覆写存储值。
- 建档写入门禁 `backend/app/commands/members.py:94`（`_guard_duplicate_person`）：strong 一律 409 `PERSON_DUPLICATE_IN_SPACE` 并给出既有档案 id（resolution=reference_existing）；weak 未显式确认时 409 `PERSON_DUPLICATE_AMBIGUOUS`；`allow_duplicate_person` 只放宽 weak。并发保证由 `command_transaction(immediate=True)` 的 `BEGIN IMMEDIATE` 提供。
- Steward 回溯审计 `backend/app/services/steward.py:822`（`_detect_duplicate_persons`）：空间可见集合内同名簇两两比对，产出 `conflict/duplicate_person_strong|weak` 发现项；新签名落 `steward.conflict_detected` 领域事件（`_emit_new_findings` 以 checkpoint 签名幂等）。
- 回归测试 `backend/tests/test_person_dedupe.py`：纯函数口径、门禁拒绝/放行、并发建档恰好一个成功（BEGIN IMMEDIATE 唯一保证，去掉写锁必失败）。

尚缺的两块（本任务增量）：

- **处置闭环**：审计发现 `duplicate_person_*` 后没有任何合并/消除重复的领域命令。重复档案继续留在空间里，personal-family-view 的 Scenario E（两棵树通过同一个已去重人物连接、不能复制出第二个人物）在"重复已经发生"的场景下无法成立。
- **身份事件失效联动**：`backend/app/services/domain_events.py:24-29` 的 `_invalidate_personal_family_view` 只监听 `source_fact.` / `space_member.` / `space_profile_ref.` / `personal_family_bridge.` 前缀；人物档案事件（`profile.created` / `profile.updated` / `profile.deleted`）不在其中。合并与删除档案后，受影响空间的 PersonalFamilyView 不会被标 stale，依赖读取时逐节点复核兜底，违反 architecture.md §11"相关投影标为 stale"的合同。

## Requirements

### R1. 判定口径唯一真源（已交付，回归保护）

- 所有重复判定（写入门禁、回溯审计、合并前复核）必须调用 `services/person_identity.py`，禁止在任何其他位置重写归一/强度阈值。
- 归一只作用于比对键，永不覆写 `users.name` / `users.birth` 存储值。
- 空间作用域同时覆盖 `space_profile_refs`（provisional 引用）与 `space_members`（已认领成员）；两个不相干家庭各有一个"李秀英 1948-03-12"必须都允许。
- 并发建档恰好一个成功的回归用例必须保留，且去掉写锁后必须失败（§0.9 硬性要求）。

### R2. 残留重复的合并处置命令

- 新增显式领域命令：把空间内一对确认同一人的重复档案合并为唯一人物（survivor），处置被合并档案（retired）。
- 处置前必须在事务内用 `person_identity` 重新复核该对强度 ≥ weak；`none`（同名不同人）一律拒绝合并。
- 合并只适用于**双方均为 managed（未认领）**的档案。任一方已 claimed 即拒绝，并引导走既有 `claim_dispute` 人工兜底——认领本人与合并代管是两条不可混用的身份路径。
- 合并必须把 retired 的身份承载行改指向 survivor 后再删除 retired 行（复用既有硬删除语义，`users.deleted_at` 仍为占位不启用）：至少覆盖 `space_profile_refs`、`source_facts`（subject/object 两端）、`attachments`；其余子行（DerivedFact 缓存、旧 Relation、node_position、卡片等）允许随 FK CASCADE 清除并由派生层重算，但命令必须保证删除后不留下指向 retired 的 confirmed 结构事实。
- 合并不得覆写 survivor 的档案字段（name/birth 等以 survivor 为准；归一永不覆写存储值）。字段搬运不是本任务范围，创建者可后续手动编辑。
- 合并必须落 `profile.merged` 领域事件（append-only，payload 含 survivor_id、retired_id、受影响 space_ids、迁移行计数）与 audit 快照（沿用 `delete_profile_core` 的快照纪律）；事件由事务统一提交。
- 合并幂等：retired 已不存在时返回幂等成功（防重放）；owner 义务预检复用 `assert_no_owner_obligations`；retired 存在活跃会话时同事务吊销（复用 refresh_session 吊销）。

### R3. 处置入口与授权

- 合并命令授权：操作者必须对**两个**档案同时具备 custody 编辑权（`custody.assert_can_edit` 双向通过）；空间管理员不因此获得额外合并权。
- 提供最小只读查询：返回当前空间内按 `person_identity` 复核过的疑似重复对（strength、双方最小元数据），供创建者/代管人在处置前查看；不得泄漏其他空间信息，不得包含家庭档案敏感字段。
- 合并是显式两步确认操作（调用方必须显式传入确认参数）；不得由 Steward 自动执行合并（Steward 不创造正式事实，R8 of personal-family-view）。

### R4. 人物身份事件接入 PersonalFamilyView 失效

- `profile.merged` 与 `profile.deleted` 事件必须使受影响空间的 PersonalFamilyView 标记 stale（走既有 `invalidate_space_views`），在 `domain_events.py` 的失效监听中以前缀方式接入，不得在命令里散布直接调用。
- 失效后受影响视图由既有 Steward 空间作业异步重算；读取路径的逐节点授权复核保持不变（撤权/删除后旧行不得继续暴露， personal-family-view AC-11/AC-12 延续）。
- 合并产生的 `source_fact` 指向变化天然携带既有 `source_fact.*` 失效路径，不得重复失效。

### R5. 审计与幂等收敛

- 合并完成后，下一次 Steward 空间作业的回溯审计不得再报出同一对签名（重复对已消除）；历史 `steward.conflict_detected` 事件不删除（append-only），以新签名的缺席表达收敛。
- 合并命令的 audit 记录必须包含 retired 快照与 survivor 引用，满足"可追溯唯一身份"（personal-family-view Dependencies 对本任务的表述）。

## Acceptance Criteria

- [ ] **AC-1 口径回归**：`test_person_dedupe.py` 既有三类断言（纯函数口径/门禁/并发）保持通过；任何阈值变更必须只发生在 `person_identity.py`。
- [ ] **AC-2 合并主路径**：同一空间内 strong 对（同名同生日、双方 managed）合并后：survivor 保留原字段；retired 行删除；其 `space_profile_refs` 与 confirmed `source_facts`（两端）改指向 survivor；`profile.merged` 事件与 audit 快照落库。
- [ ] **AC-3 claimed 拒绝**：任一方 claimed 时合并 409，提示走 claim_dispute；不产生任何写入。
- [ ] **AC-4 非重复拒绝**：对 `match_strength == none` 的 pair 调用合并被拒绝（同名不同人保护）。
- [ ] **AC-5 授权边界**：对两档案不同时具备 custody 编辑权的操作者合并被拒；空间管理员身份不额外放行。
- [ ] **AC-6 失效联动**：合并/删除档案后同空间 PersonalFamilyView 变为 stale，下一次 Steward 作业重建后不再包含 retired 人物；读取路径在重建前也不返回 retired 的节点。
- [ ] **AC-7 审计收敛**：合并后的下一次 Steward 作业不再产出同一重复对签名；`finding_signatures` checkpoint 语义不被破坏。
- [ ] **AC-8 幂等与防枚举**：重复合并同一对返回幂等成功或安全 404（retired 不存在时不泄漏他人档案信息）。
- [ ] **AC-9 图不复制人物**：合并后 `load_graph` / PersonalFamilyView 中 retired 人物不再出现，两棵个人树通过 survivor 连接（Scenario E 回归）。
- [ ] **AC-10 质量门禁**：backend 定向测试、全量 pytest、ruff、mypy 通过；如涉及迁移，按临时 `DATA_DIR` 执行 upgrade→downgrade→upgrade。

## Out of scope

- claimed 账号之间的身份冲突处置：走既有 `claim_dispute` / 数据权利人工兜底，本任务不新增机制。
- 合并时的字段搬运/择优（name/birth 取舍）、跨空间批量去重、全局身份唯一索引。
- zhconv 未覆盖异体字的归一增强（§0.9 已知缺陷，保持 weak 消歧路径）。
- `space.membership.changed` 与 `_invalidate_personal_family_view` 现有前缀（`space_member.`）不匹配的问题：这是 personal-family-view 交付的相邻缺陷，归 in_progress 的 `09-02-personal-family-view-followup` 收口；本任务只新增 `profile.` 前缀接入，不重写失效监听的既有前缀合同。
- 发现项的 ActionCard 化或通知投影（`CARD_KINDS` CHECK 仅两种，扩 kind 属独立变更）；本任务的处置入口是显式命令 + 最小只读查询。
- 新用户推荐（`09-01-new-user-family-recommendations`）。

## Dependencies

- `09-01-personal-family-view`（已完成）：提供失效合同（`invalidate_space_views`）、Scenario E、AC-11/AC-12 语义；本任务是其 Dependencies 中"唯一人物真源"承诺的兑现。
- `09-02-personal-family-view-followup`（in_progress）：拥有 PFV 失效回归面；`space.membership.changed` 前缀失配缺陷在其范围收口，本任务与其共享 `domain_events.py` 的失效监听函数，实现时须先合入其改动或基于同一函数追加。
- `09-01-agent-runtime-assistant-only`（已归档）：Steward 独立 Agent 身份与 StewardJob 链路是回溯审计的运行边界。
