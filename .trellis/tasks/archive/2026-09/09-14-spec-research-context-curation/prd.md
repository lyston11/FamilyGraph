# 整治 Spec 与 Research 上下文注入

## Goal

按分层索引与叶节点治理规则降低 Trellis 初始上下文占用，并优化项目级代理指引。

## Requirements

- 将用户提供的 Spec/Research 上下文治理原则纳入项目级 `AGENTS.md`，明确 Spec 叶节点、索引路由、Research 分层、任务 JSONL manifest 的边界。
- 将完整治理合同保存为 `.trellis/spec/guides/context-curation.md`，并从 `.trellis/spec/guides/index.md` 引用；该索引只承担发现职责。
- 盘点 `.trellis/spec/` 下现有索引与叶文档，明确 workspace 索引不是 Spec 索引入口。
- 将现有过度聚合、把历史资料与可执行规范混在一起的 Spec 索引，按独立适用合同拆分或降级为兼容/历史路由；不迁移 adoption marker 之前的任务历史记录。
- 保持既有代码开发规则和任务生命周期规则不被意外删改；本任务只调整上下文治理相关文档、索引及必要的任务上下文规则。
- 对复杂 Research 约定目录结构、summary 可注入边界和 evidence 按需读取边界，避免默认注入完整调查材料。

## Constraints

- 只把 `.trellis/spec/` 中的叶文档和 `research/*-summary.md` 作为任务 `implement.jsonl` / `check.jsonl` 的合法引用。
- 不把 `AGENTS.md`、任何 Spec/Research `index.md`、`research/evidence/*`、源代码或任务规划文档加入 JSONL manifest。
- 不改动用户已有未提交业务代码和其他活跃任务的规划内容。
- 采用最小必要拆分；独立适用性是拆分标准，文件行数只作为审计信号。

## Acceptance Criteria

- [ ] `AGENTS.md` 包含上下文治理的简明执行规则，并明确索引入口是 `.trellis/spec/**/index.md`，不是 `.trellis/workspace/**/index.md`。
- [ ] `.trellis/spec/guides/context-curation.md` 存在且被 `.trellis/spec/guides/index.md` 链接。
- [ ] 所有现行 Spec 索引均为路由器：包含范围/适用性/叶链接/验证入口，不复制叶正文或历史调查。
- [ ] 需要默认注入的任务上下文可定位到独立 Spec 叶或 Research summary；manifest 规则可由检查脚本验证。
- [ ] 运行结构化检查，且不引入对现有任务和工作区文件的无关修改。
