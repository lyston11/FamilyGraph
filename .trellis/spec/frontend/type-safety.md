# 类型安全规范（初始规范 v0）

- tsconfig strict: true；禁止 any（必要时 unknown + 收窄）；禁 @ts-ignore（@ts-expect-error 需注释原因）。
- API 响应类型集中 types/api.ts，与后端 Pydantic schema 字段一一对应（人工同步，code review 对照）。
- **运行时校验**：api/ 层拦截器对响应做轻量校验（zod 或自写守卫），字段缺失/MASKED 结构不符时抛可观测错误而不是让 undefined 流入组件——遮罩结构 {__masked__: true} 必须有类型判别联合。
- 枚举值（dir_class/status/cal_type）用 const object + type 推导，与后端错误码常量表对齐。
- 日期处理只用 dayjs + lunar-typescript（或等价 lunar 库的 TS 版），禁止手写历法换算。
