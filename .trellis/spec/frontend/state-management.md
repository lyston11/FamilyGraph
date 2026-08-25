# 状态管理规范（初始规范 v0）

- Pinia setup store 风格；一个领域一个 store：auth(token/user/PIN_CHANGE_REQUIRED)、spaces(空间列表+当前空间)、graph(成员/关系/位置)、ui(布局模式/弹窗)。
- 服务端数据唯一来源是 store；组件不缓存副本。图数据变更（建档/断连/移动卡片）通过 action 调 API 后更新 store，不做乐观更新（v1 网络环境简单）。
- 缓存失效边界：空间/图数据在切换空间、收到连接变更通知、重新登录时强制刷新；无后台轮询。
- **敏感缓存清理红线**：logout 与 token 失效(401/token_version)时必须清空全部 store + localStorage + 内存图数据，路由守卫兜底跳登录页。
- localStorage 只允许存 refresh token 与 UI 偏好（布局模式），其余一律内存态。
