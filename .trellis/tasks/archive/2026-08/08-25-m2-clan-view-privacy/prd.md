# M2 家族视图与可见性（里程碑章程）

> 权威上下文：[HANDOFF.md](../../HANDOFF.md)、[architecture.md](../../spec/architecture.md)。父任务只做编排与门禁。

## 里程碑目标（出口）

**两家人连起来且隐私不泄露（API 层可验证）**：授权矩阵逐行 IDOR 测试通过；家族连通视图可用；申请/断连闭环且权限即时降级。

## 子任务与依赖

| 子任务 | 内容 | 依赖 |
|---|---|---|
| [m2a 授权矩阵与可见性模块](../08-25-m2a-authz-matrix-visibility/prd.md) | visibility.py 单点 + IDOR 测试 | M1 全部 |
| [m2b 家族连通视图](../08-25-m2b-clan-connected-view/prd.md) | 连通分量/树形大图/折叠/切换 | m2a |
| [m2c 加入申请与断连流程](../08-25-m2c-request-disconnect-flows/prd.md) | 两类请求闭环/幂等/降级验证 | m2a、m1c |

## 审计边界修正记录

- 搜索的**接口骨架与命中范围约束**由 m2a 矩阵先行覆盖，搜索产品化在 m3d（消除 v1.0 的 M2/M3 搜索重叠）。
- m1b 已实现 connection_request 后端契约，本里程碑只做审批 UX 与 join_request。

## 出口门禁

- [ ] 三个子任务验收全绿；IDOR 测试进入 CI 门禁
- [ ] HANDOFF §五.5 通过
