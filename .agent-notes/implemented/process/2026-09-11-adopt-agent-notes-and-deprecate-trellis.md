# Agent Note: 用 Agent Notes 取代 Trellis 工作流

Status: implemented

## Problem

项目原先把开发流程、规范、任务状态和会话记忆分散在 Trellis 的 `.trellis/`、`.agents/skills/` 与 Codex hooks 中。新会话需要先加载 Trellis 状态，架构决策又容易停留在任务文件或聊天记录里，代码入口缺少对长期取舍的直接追溯点。

## Decision

项目采用 `write-notes-like-deepseek` 的 Agent Notes 体系作为新的工程决策记录和校验入口。规范副本位于 `.write-notes-like-deepseek/`，可执行脚本位于根目录 `scripts/`，决策记录使用 `.agent-notes/{proposed,implemented,rejected,archived}/{class}/yyyy-mm-dd-topic.md` 路径。`npm run verify-agent-notes` 校验生命周期、类别、文件名、Markdown 链接和正文骨架；`npm run init-board` 生成决策看板。

`.trellis/` 仅保留历史资料，不再承载当前任务、规范、会话状态或自动注入。新的非平凡改动先创建或更新 Agent Note，落地 Note 与代码在同一批变更中维护；核心入口通过 `Note: ... — 见 .agent-notes/...` 注释反向链接到决定。

## Alternatives considered

- **继续维护 Trellis 并额外复制 Agent Notes** — 保留现有任务编排的迁移成本最低，但会产生两个规范来源和两套状态机，后续 Agent 仍可能读取过期的 Trellis 合同。
- **只写普通 README/ADR，不接机械校验** — 文档位置更熟悉，短期文件更少，但无法稳定检查生命周期、备选方案和相对链接，跨会话容易重新引入已否决路线。
- **不做流程迁移，继续依赖聊天上下文** — 不需要改仓库，但无法为无历史上下文的 Agent 提供可验证的决策边界，因此不满足长期维护要求。

## Consequences

- **收益**：工程决策与代码路径同库保存，Agent 可以从源码入口直接跳到原因、备选和已知上限；校验脚本提供可重复的结构门禁；Trellis 历史资料仍可查阅。
- **代价与已知上限**：现有 Trellis 任务和规范不会自动转换为逐篇 Note；迁移旧决策需要按当前代码重新整理。`.agent-notes` 是约定路径，若未来迁移到受平台自动发现的 `.agents/notes`，必须同步更新脚本默认路径、AGENTS 入口和所有反向链接。

## Verification

当前仓库的 `AGENTS.md` 指向 `.write-notes-like-deepseek/SKILL.md`，根目录脚本默认读取 `.agent-notes`，Trellis 目录包含弃用声明；执行 `npm run verify-agent-notes` 可验证本 Note 及目录结构。
