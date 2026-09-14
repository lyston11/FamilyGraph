# Thinking Guides

> 路由器：按任务选择适用的思考指南；不要把索引当作默认注入正文。

## Available Guides

- [Context Curation](./context-curation.md) — Spec 叶节点、索引路由、Research 分层和任务 manifest 边界
- [Code Reuse Thinking Guide](./code-reuse-thinking-guide.md) — 识别重复模式并减少重复
- [Cross-Layer Thinking Guide](./cross-layer-thinking-guide.md) — 思考跨层数据流与边界

## Applicability

- 跨越 API、Service、Component、Database 或改变跨层数据格式时，读取 Cross-Layer 指南。
- 发现重复模式、修改常量/配置或新增 helper 时，读取 Code Reuse 指南。
- 修改 Spec、Research 或任务上下文 manifest 时，读取 Context Curation 指南。

## Validation

- 按所选叶文档中的 Required validation 执行验证。
- 索引只承担发现职责；任务 manifest 不得引用本索引。
