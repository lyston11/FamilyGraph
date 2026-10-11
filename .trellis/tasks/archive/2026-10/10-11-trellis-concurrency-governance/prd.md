# Trellis 并行隔离与全局模板治理

## Goal

为 Trellis 增加任务范围冲突阻断、串行资源与依赖调度、唯一集成通道约束，并同步到全局模板。

## 实施归属

本文件是 FamilyGraph 侧的需求来源记录，不是实现入口。真正的实现任务已创建在全局 Trellis 仓库：

- Parent：`/Users/lyston/PycharmProjects/agent-context/Trellis/.trellis/tasks/10-11-task-concurrency-governance/`
- Runtime child：`10-11-task-resource-conflict-guard`
- Template child：`10-11-task-governance-template-rollout`

两个 child 都保持 planning；必须先完成 runtime child 并合入全局 Trellis main，再启动 template child。

## Confirmed problem

本项目曾出现多个 agent 同时修改 `memory_rag.py`、`agent_tools.py`、`maintenance.py` 与同一份 spec，冲突直到 merge 才暴露。现有 task.json 没有机器可校验的资源声明，`task.py start` 也没有路径、串行组、集成 lane 或显式依赖阻断。

## Desired outcome

任务在 start 前声明 `owned_paths`、可选 `shared_paths`、`serialization_group`、`blocked_by` 与 `integration_lane`；Trellis 在启动和集成预检时 fail closed，输出冲突任务、资源和解除方式。planning 默认不占用执行资源，避免所有草稿任务互相阻断。

## Acceptance reference

验收标准、兼容旧 task.json、保守 glob overlap、唯一集成通道、模板 parity、update dry-run 及现有未提交 Pi extension 的保护，均以全局 Trellis parent/children 的 `prd.md`、`design.md`、`implement.md` 为准。
