# 前端质量门禁（初始规范 v0）

提交前必须全绿：

```bash
cd frontend
npm run lint        # eslint + vue 插件
npm run type-check  # vue-tsc --noEmit
npm run test        # vitest: stores/composables 单测 + 关键组件测试
npm run build       # 生产构建零报错
```

- 禁止模式：组件内直连 axios；v-html 渲染用户输入（XSS）；内联样式承载布局逻辑；未类型化的 event.target 取值。
- 三布局切换、遮罩渲染、challenge 登录流程必须有组件级测试。
- 移动端验收（m4a）前，所有新页面需在 375px 视口人工过一遍并记录。
- 性能基线：家族视图首次渲染 <2s（几十人规模），超出即检查是否漏用折叠/虚拟化。

## V2.5 Memory/RAG 验收检查（2026-08-26）

- Memory/RAG 页面必须复用服务端权威 store；组件不直接拼接 scope、citation 或权限结果，不把 Assistant 文本摘要当作 Memory 真源。
- 候选确认前明确显示原始引用、摘要、scope、敏感等级、保留期限和隐私影响；确认、撤销、删除、搜索失败后重新请求服务端状态。
- 空间切换必须清理上一空间的卡片/记忆/RAG 引用，且组件测试验证不会短暂显示上一空间数据。跨空间、private、masked 和未确认事实需要可见的降级文案。
- Assistant 消息中的结构化 `card_ids`/RAG citation 与空间 Inbox 使用同一个 store 和渲染组件；任何操作后两入口都以服务端状态更新，不维护副本。
- Policy Guard 错误不能被 UI 捕获后静默吞掉或降级成普通空结果；需要保留可解释的 blocked/local-provider-required 状态。
