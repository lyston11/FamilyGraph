# 技术设计：修正 steward e2e 场景的空间类型

## 1. 根因（两个独立的门，实测得出）

脚本停在步骤 7 不是单一原因。用 `evaluate_recommendation` 对四种组合实测：

```
kind=household confirmed=False share=True  eligible=False reason=profile_not_confirmed
kind=household confirmed=True  share=True  eligible=False reason=already_connected
kind=lineage   confirmed=False share=False eligible=False reason=profile_not_confirmed
kind=lineage   confirmed=True  share=False eligible=True  actions=(create_household,)
```

### 门一：身份未确认（`profile_not_confirmed`）

脚本的 `register()`（`:245-252`）只调 `/api/auth/register` + `/api/auth/login`，**从未调用 `/api/me/identity/confirm`**。注册产生的是 `User(provisional) + Account(claimed)`（见 `api/auth.py:92` 的 docstring），而推荐矩阵要求双端 `identity_confirmed`。

**这才是首要阻塞**：无论空间是什么类型，未确认身份都无法通过。独立印证：生产库 51 个用户**全部** `identity_confirmed`，`provisional` 为 0；39 张 `household_link` 卡片的两端全部已确认。

### 门二：空间类型（`already_connected`）

脚本用 `{"kind": "household"}` 建空间并把 b、c 都加为 active 成员，于是 `share_active_household` 为真 → R5 抑制生效。`household_link`（“与某位亲属共建家庭空间”）天然产自**家族空间**——你在家族树里看到尚未与你同住的亲属。

生产数据一致：`household_link` 39 张**全部**在 lineage 空间，household 空间 **0 张**。

两个门必须同时打开；只修一个仍然得到 0 张卡。

## 2. 方案

### 2.1 注册后确认身份（门一）

`register()` 末尾调用 `POST /api/me/identity/confirm`。该端点在首登门禁白名单内，可先于改 PIN 调用，且是**唯一**合法的本人确认路径（`identity_fsm.confirm_profile_identity`）。不得直接写库改 `profile_status`。

### 2.2 场景空间改为 lineage（门二）

`household_link` 的语义是「建议与某位亲属共建家庭空间」，它天然产生于**家族空间**。依据：`steward.py:1490` 的 `lineage_possible` 只在 `kind != "household"` 时计算；`tests/test_steward.py:258-266` 已用「lineage + spouse」证明该形状产出 `household_link`。

### 为什么不放宽 R5

R5 抑制是刻意行为（09-19 `2d1e9bf`，修「子辈被推荐为同辈」与「重复家庭卡」）。为迁就一个测试脚本而放宽它，等于用降低产品质量换取测试通过。**不改产品规则。**

### 为什么不改用 profile ref（非成员对端）

`tests/test_steward.py` 用 `ref=True` 的非成员对端。但那要求脚本改走「建档向导 / profile ref」路径，而脚本步骤 2 的语义是「成员加入 → owner 批准 → 本人接受」。保留成员路径更贴近脚本原意，且改动面更小。

## 3. 断言恢复

场景修好后，步骤 5 与步骤 7 的内核部分应有真实结果：

- 步骤 5（`core_tick`）：`cards_created` 非空。恢复为**真实断言**（至少一张 `household_link`），使「tick → job → 卡片」这条链有回归保护。
- 步骤 7（辅助）：**仍不断言成功**——脚本不启动 sidecar，辅助不可能执行。保持 `assist_reservation_observed` 的如实记录。

## 4. 兼容与回退

- 只改脚本与文档；无产品代码、无 schema、无 wire 变更。
- 回退 = revert 本任务提交。
- R5 抑制与 `recommendation_matrix` 必须零 diff（AC-4 断言）。

## 5. 风险

- 断言若写成「卡片数 == N」会因场景微调而脆断；应断言**存在**至少一张 `household_link`（意图是「该链有产出」），而非精确计数。
- 变异验证必须能失败：删掉关系确认步骤后，卡片断言必须失败。否则它只是记录了「tick 跑过」。
