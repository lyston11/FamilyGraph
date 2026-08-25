# m4a 移动端与可访问性

> 父任务：[08-25-m4-polish-admin-deploy](../08-25-m4-polish-admin-deploy/prd.md)｜依赖：M1-M3 功能齐备

## Goal

手机浏览器可用 + 过渡体验打磨，达成 U3/U6。

## Requirements

- 响应式断点（≤768px）：登录/列表/详情/表单完整可用；导航折叠或底部化。
- 画布移动端：双指缩放、单指平移、卡片点按放大详情；不要求摆放编辑。
- 家庭⇄家族切换动画打磨（视口缩放+淡入）；三布局切换节点平滑过渡。
- 可访问性按 spec/frontend/component-guidelines 基线全量过检（label/aria/焦点/对比度）。

## Acceptance Criteria

- [ ] 375px 视口走通：登录→浏览三布局（只读可接受）→档案详情→改 PIN。
- [ ] 切换动画 DevTools 无长任务告警（目标 60fps）。
- [ ] a11y 检查清单逐项通过并留档于任务目录。

## Non-goals

- 原生 App；小程序；平板专属布局。
