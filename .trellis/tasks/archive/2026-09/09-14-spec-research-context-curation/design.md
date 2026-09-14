# 技术设计

## 边界

本任务是 Trellis 文档与上下文治理改造，不修改业务运行时。权威入口为根目录 `AGENTS.md` 与 `.trellis/spec/`；`.trellis/workspace/index.md` 和 `.trellis/workspace/<developer>/index.md` 仅用于会话记录，不纳入 Spec discovery。

## 结构

1. 在 `AGENTS.md` 的项目 Trellis 指引附近新增一段简明的 Context Curation 规则，作为 agent 的强约束摘要。
2. 新建 `.trellis/spec/guides/context-curation.md`，保存完整合同：叶节点判定、索引职责、Research index/summary/evidence 三层结构、JSONL 合法引用、adoption boundary。
3. 在 `.trellis/spec/guides/index.md` 增加叶文档链接，并将该历史/聚合索引中与新合同冲突的重复性说明最小化，避免再次造成大块注入。
4. 审计 backend/frontend/guides 的现有索引。对确实只是路由的索引保留并精简；对混合正文、历史记录或多独立合同的文件，优先拆出叶文件并让索引只链接它们。已有历史资料在 adoption marker 前不迁移。
5. 用任务 manifest 仅引用 `context-curation.md` 及必要的独立叶文档；不引用索引、AGENTS 或任务规划文件。

## 兼容性与取舍

- 不改变 `get_context.py` 或任务上下文加载器的运行逻辑；先通过文档结构和 manifest 约束降低默认注入体积，避免扩大本任务风险。
- 不把所有长文档机械按字数切片；只有多个可独立适用合同共存时才拆分。
- 现有索引若包含历史说明，将改为简短的非权威兼容提示或移除重复正文，但不重写旧任务档案。

## 预期改动文件

- `AGENTS.md`
- `.trellis/spec/guides/context-curation.md`
- `.trellis/spec/guides/index.md`
- 必要时 `.trellis/spec/backend/index.md`、`.trellis/spec/frontend/index.md` 及拆出的 Spec 叶文件
- 当前任务的 `prd.md`、`design.md`、`implement.md`、`implement.jsonl`、`check.jsonl`

## 明确不做

- 不处理 workspace 两个 index.md；它们不是 spec 索引，且保留会话索引职责。
- 不迁移 adoption marker 前创建的任务 artifacts。
- 不调整其他活跃任务的 PRD、设计或实现记录。
- 不修改业务代码、测试、数据库或运行时上下文算法，除非审计证明文档规则无法表达所需约束。
