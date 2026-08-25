# Bootstrap Task: Fill Project Development Guidelines

**You (the AI) are running this task. The developer does not read this file.**

## 状态（2026-08-25 更新：死结已解除）

原 PRD 要求"从既有代码提取真实约定"，但本项目当时没有任何业务代码——这是审计记录指出的流程死结。修订后的策略：

> **初始规范基于锁定的技术栈与架构决策制定（已完成），M0 完成后用真实代码实例校正。**

`.trellis/spec/backend/`、`.trellis/spec/frontend/` 共 11 份规范已全部填充为 **Initial draft**（内容与 HANDOFF.md 锁定决策及 spec/architecture.md 一致），两个 index 的状态列已从 "To fill" 更新。

## 剩余检查项

- [x] Fill backend guidelines（初始版完成，见 backend/index.md）
- [x] Fill frontend guidelines（初始版完成，见 frontend/index.md）
- [ ] Add code examples ← **M0 完成后执行**：从真实代码提取示例替换/补充各规范中的约定描述，并将状态升级为 "Verified against code"
- [ ] 校正与代码不一致的条目（预期至少：目录结构微调、门禁命令细节）

## 校正时的操作指引

1. 对照 `backend/app/` 实际目录更新 directory-structure.md。
2. 用实际运行的门禁命令输出校验 quality-guidelines.md 中的命令清单。
3. 把 M0/M1 中踩到的第一个真实坑写进对应规范的 "common mistakes" 段。
4. 完成后在本文件勾选并在 journal 记录，随后 `task.py archive` 本任务。

## 关联文档

- [HANDOFF.md](../../HANDOFF.md) — 锁定决策
- [architecture.md](../../spec/architecture.md) — AD-1~8 默认假设
- 审计记录第三节问题 2（本任务的原死结描述）
