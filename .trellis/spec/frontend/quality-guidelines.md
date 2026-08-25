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
