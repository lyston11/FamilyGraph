# M1 建档、关系与家庭空间（里程碑章程）

> 权威上下文：[HANDOFF.md](../../HANDOFF.md)、[architecture.md](../../spec/architecture.md)。父任务只做编排与门禁。

## 里程碑目标（出口）

**能把父母/子女/配偶建出来并正确显示世代结构**：建档向导+一次性 PIN+代管权；四分类关系 FSM；家庭空间成员 FSM；三布局呈现；公农历基础换算。

## 子任务与依赖

| 子任务 | 内容 | 依赖 |
|---|---|---|
| [m1a 档案、账号认领与代管权](../08-25-m1a-profile-account-claim/prd.md) | 建档向导/AD-1 身份模型/删除档案 API | m0b |
| [m1b 关系模型与状态机](../08-25-m1b-relations-fsm/prd.md) | 四分类/环检测/connection_request 合并请求/图查询 | m1a |
| [m1c 家庭空间与成员状态机](../08-25-m1c-family-space-membership/prd.md) | 空间 CRUD/成员 FSM/首登空间引导 | m0b（联调需 m1b） |
| [m1d 三布局及基础日期能力](../08-25-m1d-three-layouts-dates/prd.md) | 画布/树状/列表 + lunar-python 基础 | m1a/m1b/m1c |

## 审计边界修正记录

- 连接确认的**审批 UX** 移至 m2c；m1b 只实现合并请求的后端契约。
- 删除档案 API 归属 **m1a**（原 v1.0 错误地写在 M4）`[AD-5]`。
- 公农历"完善交互"移至 m3b，此处仅基础能力。

## 出口门禁

- [ ] 四个子任务验收全绿
- [ ] M0-M4 重复边界已消除（本表记录为准）
