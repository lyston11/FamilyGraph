# m4a 技术设计

- 响应式：全局断点 ≤768px——顶栏按钮折叠为图标+文字缩短；stats 卡片单列；画布高度自适应
- a11y 基线（spec/frontend/component-guidelines）：表单 label 绑定、图标按钮 aria-label、
  ElDialog 焦点陷阱自带、颜色对比度 AA——逐页过检（Login/Onboarding/Settings/ChangePin/FamilySpace/Stats）
- 切换动画：scope-switch 视图过渡用 CSS transform scale 淡入（基础版，60fps 目标）
