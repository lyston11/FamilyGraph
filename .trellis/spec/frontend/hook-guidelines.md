# Composables 规范（初始规范 v0）

- 有状态的请求封装放 Pinia store；纯逻辑复用（布局计算、防抖、challenge 流程）放 composables/。
- useLayout(mode): 输入图数据输出节点坐标；树状模式失败时返回 fallback 标记触发画布回退提示。
- useVisibility(): 包装当前用户与目标的可见关系判定，供卡片/详情组件统一取用；**前端判定仅做渲染优化，安全语义永远以后端返回为准**。
- composable 内禁止直接操作 DOM；副作用（事件监听/timer）必须在 onUnmounted 清理。
- 通用 hooks（useDebounce 等）优先自写小函数，不引第三方 hook 库。
