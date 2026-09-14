# 前端规范路由

## 范围

适用于 `frontend/` 与 `system-admin-frontend/` 的 Vue 3、Vite、TypeScript、Naive UI、Pinia、Vue Flow 和主题 token 改动。

## 适用性

- 目录、组件、composable、状态、类型安全或质量门禁变化：按需读取对应叶文件。
- ActionCard 或 Memory Contract 改动：只读取对应叶文件。
- 涉及全局身份、授权、空间状态或数据权利：从 [全局架构规范路由](../architecture/index.md) 选择具体叶文件。
- 跨越后端接口与前端状态/组件时，补读 [Cross-Layer Thinking Guide](../guides/cross-layer-thinking-guide.md)。

## 合同叶文件

- [directory-structure.md](directory-structure.md)
- [component-guidelines.md](component-guidelines.md)
- [hook-guidelines.md](hook-guidelines.md)
- [state-management.md](state-management.md)
- [action-card.md](action-card.md)
- [type-safety.md](type-safety.md)
- [quality-guidelines.md](quality-guidelines.md)
- [Memory Contract](../backend/memory-contract.md)

## 验证入口

按所选叶文件的 Required validation 执行；不要因为读取本路由而默认读取全部前端规范。
