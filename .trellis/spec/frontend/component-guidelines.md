# 组件规范（初始规范 v0）

- `<script setup lang="ts">` 组合式 API；禁 Options API。
- 组件命名 PascalCase 多词（MemberCard.vue）；composable 用 useXxx。
- props 用 defineProps<T>() 类型化，必填项显式；emit 事件名 kebab-case。
- 遮罩渲染统一走 `<MaskedField :value :visible>`：visible=false 显示锁样式占位，禁止散落 v-if 手写。
- Element Plus 为主 UI 库；主题色定制走 CSS 变量，不覆盖组件内部类。
- 可访问性基线：表单控件必须绑定 label；弹窗用 ElDialog 自带焦点陷阱；图标按钮必须有 aria-label；颜色对比度 AA。
- 空状态必须给引导动作（如空画布 → "添加第一位家人"按钮）。
