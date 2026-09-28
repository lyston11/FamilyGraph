# S5 切换 Pi 载体并删除 in-process 路径

## Goal

把 Steward 模型辅助的执行载体收敛为**唯一的** Pi child run，删除进程内直连 Provider 的整条路径与逐 kind 开关；并把生产从当前 in-process 架构切换到该状态。

本任务不新增模型能力、不改 prompt 文本、不改 attempt 状态机语义、不新增工具。

## 已核实背景（2026-09-28）

- 生产 `STEWARD_ENABLED=1`、`STEWARD_WORKER_ENABLED=1`、四个 assist 全 `1`；`steward_model_calls` 54 行，最近一次 `2026-09-20 08:57`。**生产一直在跑 Steward，只是跑在 in-process 路径上。**
- 生产代码 `bda8a35`（09-20），落后 main 47 个提交；`steward_model_calls` 无 `carrier` 列。
- main 上 E1–E4 已交付：单链路 + 可换 carrier，四个 kind 均已证明可经 Pi 运行，默认仍 `inproc`。
- 迁移 0055 的六个拒绝守卫在生产上全部通过；无 `in_flight` 行。

因此本任务是「把已验证的 Pi 路径变成唯一路径」，不是新建能力。

## Requirements

### R1 唯一载体

Pi child run 成为 Steward 模型辅助的唯一执行载体。不存在「本进程直接发 HTTP 给 Provider」的路径。

### R2 删除 in-process 发送实现

删除进程内发送与响应解析实现及其专属辅助函数；Provider 出站只能经网关（`provider_proxy`），从而保留 `agent_provider_egress` 审计。

### R3 删除逐 kind 开关

删除 `STEWARD_ASSIST_<KIND>_CARRIER` 与载体名解析；不再存在「按 kind 选载体」的配置面。

### R4 删除进程内调度泵

删除进程内租约执行入口、有界线程池与维护 tick 中的对应调用；Pi attempt 只由 sidecar 经内部端点取走。

### R5 保留不变量

保留 attempt 状态机、计费、封闭 schema 校验、发送前 fence、结算写回栅栏、CAS 应用、崩溃恢复，以及 fence 所需的 Provider API 能力校验。

### R6 数据兼容

不收紧 `carrier` 的 CHECK 约束，历史行保留可读（memory #399）。新行写 `pi`。

### R7 测试接缝迁移

把现有依赖「注入假 transport」的测试迁移到 Pi 路径的真实驱动方式（lease → child run → settle），不保留 in-process 执行接缝。

### R8 生产切换

把生产切到该状态，并验证一次真实 Pi 调用闭环。

## Acceptance Criteria

- [ ] AC-1 R1/R2/R3/R4：仓库内不再存在 `InprocCarrier`、`_post_json`、steward 侧 `_API_PATHS` 的进程内发送用法、`STEWARD_ASSIST_<KIND>_CARRIER`、进程内租约执行入口与线程池；无残留引用。
- [ ] AC-2 R2：Steward 模型调用只经网关出站；一次 Pi 调用产生且仅产生一条 `agent_provider_egress` 审计。
- [ ] AC-3 R5：fence 调用点数量不变；删除任一 fence 层仍有测试失败（变异验证）。
- [ ] AC-4 R5：四类 kind 的产物经 Pi 路径与既有断言等价；ranking 严格排列、candidate 原子 kind 围栏、explanation 槽位/证据围栏保持。
- [ ] AC-5 R6：`carrier` 列与 CHECK 保留；迁移往返后历史行状态不被破坏。
- [ ] AC-6 R7：backend 全量测试通过；无测试仍依赖被删除的执行接缝。
- [ ] AC-7 R8：生产部署后有一次真实 Pi child run 闭环（`agent_runs` 出现 steward 行、attempt 结算、egress 审计存在）。
- [ ] AC-8 受影响质量检查通过，并记录未运行的高成本检查及原因。

## Out of scope

不新增生产开关以外的能力；不改前端；不做 S3 工具集的成功路径补测；不实现跨空间发现或地区称谓；不动 `provider_proxy` 的网关语义。
