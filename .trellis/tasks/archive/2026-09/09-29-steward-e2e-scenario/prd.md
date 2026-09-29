# Steward e2e 场景过时：household_link 被 R5 正确抑制

## 目标与用户价值

让 `backend/scripts/steward_e2e.py` 的场景与当前产品规则一致，从而恢复它作为「确定性内核链路」验证入口的价值；并让「脚本为何不覆盖模型辅助」这一点在代码与规范里都是显式事实，而不是靠读者推断。

本任务由上一任务（`09-29-steward-legacy-cleanup`）交付说明中的「顺带发现」触发：脚本已能跑完，但它的**场景**假设仍然过时——它停在步骤 7 是因为产品规则正确地抑制了它期待的卡片，不是脚本有 bug。

## 背景与证据边界

### 已确认事实（两个独立的门，实测得出）

对 `evaluate_recommendation` 四种组合实测：

```
kind=household confirmed=False  eligible=False reason=profile_not_confirmed
kind=household confirmed=True   eligible=False reason=already_connected
kind=lineage   confirmed=False  eligible=False reason=profile_not_confirmed
kind=lineage   confirmed=True   eligible=True  actions=(create_household,)
```

- **门一（首要阻塞）：身份未确认。** 脚本的 `register()`（`steward_e2e.py:245-252`）只调 `/api/auth/register` + `/api/auth/login`，**从未调用 `/api/me/identity/confirm`**。注册产生 `User(provisional) + Account(claimed)`（`api/auth.py:92` docstring），而推荐矩阵要求双端 `identity_confirmed`（`identity_fsm.recommendation_eligible`）。生产库印证：51 个用户**全部** `identity_confirmed`，`provisional` 为 0。
- **门二：空间类型。** `share_active_household`（`steward.py:1533`）只匹配 `FamilySpace.kind == "household"`；脚本用 `{"kind": "household"}` 建空间并把 b、c 加为 active 成员 → 命中 R5 抑制 → `already_connected`。`household_link`（"与某位亲属共建家庭空间"）天然产自**家族空间**：`steward.py:1490` 的 `lineage_possible` 只在 `kind != "household"` 时计算；`tests/test_steward.py:258-266` 已证明「lineage + spouse」产出该卡片。
- **生产数据一致**：开发库中 `household_link` 卡片 **39 张全部在 lineage 空间**，household 空间 **0 张**。
- 脚本**不启动 sidecar**，因此不验证模型辅助（已在上一任务中确认并写入 spec）；本任务**不改变**这一范围。

两个门必须同时打开；只修一个仍得到 0 张卡。

### 证据边界

本任务只改脚本场景与相关文档，不改产品规则。R5 抑制是刻意行为（09-19 `2d1e9bf` 引入，修「子辈被推荐为同辈」与「重复家庭卡」），**不得**为了迁就脚本而放宽。

## 需求

### R1 脚本场景必须能产出确定性卡片

脚本的场景必须落在产品规则允许产出卡片的形状上，使「tick → job → 卡片/PFV/通知」这一步有真实结果可断言，而不是静默为空。**两个门都要打开**：

- 参与者必须经 `/api/me/identity/confirm` 确认身份（推荐资格要求双端 `identity_confirmed`）；
- 场景空间必须是 `lineage`（`household_link` 产自家族空间，household 空间被 R5 正确抑制）。

不得通过放宽 R5 抑制、绕过 `share_active_household`、绕过 `recommendation_eligible`、直接改库写 `profile_status` 或直接插卡来达成。场景应使用**真实 API** 建立（与脚本既有做法一致）。

### R2 断言必须反映真实意图

场景修好后，原本因场景为空而被迫放宽的断言，应恢复为对**真实结果**的断言。

- 步骤 7 的辅助部分仍不得断言成功（无 sidecar），但**内核部分**（卡片/PFV/通知）必须有实际断言，不能只是 `step()` 记录。
- 不得引入恒真断言（例如断言一个必然为空的集合）。

### R3 文档与代码一致

脚本 docstring 与 spec 必须说明：脚本覆盖确定性内核链路、不覆盖模型辅助、以及场景为何用该空间类型。

## 验收标准

| ID | 可观察结果 | 对应需求 |
|---|---|---|
| AC-1 | 脚本跑完并产出证据 JSON；`core_tick` 步骤报告非零 `cards_created`（至少一张 `household_link`），且该卡片由真实 tick 路径产生（非直接插库） | R1 |
| AC-2 | 卡片/PFV/通知相关断言为真实断言；用变异验证（删掉关系确认步骤）证明它们会失败，而不是恒真 | R2 |
| AC-3 | 脚本 docstring 与 `.trellis/spec/backend/steward-action-card.md` 的验证入口说明与该脚本实际覆盖范围一致 | R3 |
| AC-4 | backend 全量 pytest / ruff / mypy 通过；R5 抑制规则未被改动（`share_active_household` 与 `recommendation_matrix` 无行为 diff） | R1–R3 |

## 不在范围

- 让脚本启动 sidecar 或覆盖模型辅助（独立工作量，已在上一任务明确排除）。
- 修改 R5 抑制规则、`recommendation_matrix` 判据或任何产品行为。
- 脚本业务场景的扩展（撤权/恢复等既有步骤保持不变）。
- 生产环境操作；线上由用户手动发布。
