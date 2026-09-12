# Design — 管理员壳层布局几何修复

## Boundary

改动集中在 `system-admin-frontend/src/styles/main.css`，必要时调整 `AdminShell.vue` 的语义结构测试锚点；不改变路由和业务数据流。根因位于共享壳层 CSS 的级联冲突，不在 OverviewView 数据或 API。

## Layout contract

- `.admin-shell`：`display:flex; flex-direction:row; min-height:100vh`，作为侧栏与内容壳的横向容器。
- `.admin-sidebar`：桌面 `flex:0 0 248px`、`height:100vh`、sticky；移动端 fixed 抽屉，不参与默认文档流。
- `.admin-shell-body`：`flex:1; min-width:0; display:flex; flex-direction:column`。
- `.admin-header`：右侧 body 内 sticky topbar，不能拥有会把内容推离首屏的固定高度或额外外边距。
- `.admin-main`：右侧 body 内唯一内容容器，宽度受控、响应式 padding。

## Compatibility

保留 `admin-nav-panel`、`mobile-nav-toggle`、`account-menu`、导航链接和既有 class/testid。旧的横向顶部导航规则需要删除或由更高优先级的壳层规则明确覆盖，避免后续 CSS 继续依赖源文件顺序。

## Rollback

所有改动只在管理员前端样式与壳层测试内；若视觉回归，可单独回滚本次提交，不影响 API、认证或家庭端。
