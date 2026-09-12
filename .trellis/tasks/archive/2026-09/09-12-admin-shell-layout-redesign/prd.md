# PRD — 重做管理员后台壳层与概览首屏布局

## Goal

修复管理员后台实际页面的布局主轴错误，使后台与 FamilyGraph 用户端保持连续的应用壳结构：桌面端左侧固定导航 + 右侧顶部栏与内容区，移动端侧栏抽屉化。首屏应直接看到概览标题、指标和空间列表，不能出现大块空黑区或内容被推到侧栏下方。

## Confirmed facts

- `system-admin-frontend/src/App.vue` 已将受保护路由挂载到 `AdminShell`。
- `AdminShell` 使用 `.admin-shell` 包含侧栏和 `.admin-shell-body`，期望横向排列。
- `system-admin-frontend/src/styles/main.css:104-108` 的旧规则仍将 `.admin-shell` 设置为 `flex-direction: column`；后续刷新规则只写 `display: flex`，没有覆盖主轴。
- 截图表现与该冲突一致：侧栏占据首个整屏高度，右侧内容壳被排到下方，顶部栏和概览内容出现约一屏垂直偏移。
- 用户端 `frontend/src/components/shell/AppShell.vue:347-410` 已有可复用的壳层几何基线：横向 flex、固定侧栏、右侧 body、sticky topbar、移动端隐藏侧栏。

## Requirements

1. 修复 `.admin-shell` 主轴，使桌面端侧栏和内容壳横向排列。
2. 统一管理员壳层的宽度、侧栏高度、顶部栏和主内容间距，避免旧规则重复覆盖产生漂移。
3. 保留现有管理员导航路由、账号菜单、移动端菜单、Escape 关闭、滚动锁定和所有既有 testid。
4. 移动端不产生页面级横向溢出，侧栏打开时仍为可操作抽屉。
5. 不修改管理员 API、权限、数据绑定或概览业务行为。

## Acceptance criteria

- 在 1440×900 左右桌面视口，侧栏从页面顶部开始固定在左侧，顶部栏位于右侧内容壳顶部；概览标题在顶部栏下方首屏可见。
- 不再出现侧栏下方的大块空黑区，概览指标和列表连续排列。
- 在 ≤768px 视口，侧栏默认不占布局空间，菜单按钮可打开侧栏；Escape、路由切换和卸载继续清理滚动锁定。
- 现有管理员测试、type-check、lint、build 通过，并新增至少一个覆盖真实 App 壳层几何关系的回归断言。
- `git diff --check` 通过。

## Out of scope

- 不重写概览 API、表格字段、分页、搜索或状态筛选。
- 不修改家庭端 `frontend/` 组件或后端代码。
- 不引入新的组件库、图标系统或跨应用状态依赖。

## Open questions

无。用户已明确要求沿用用户端设计，代码证据已确定首要根因和最小修复边界。
