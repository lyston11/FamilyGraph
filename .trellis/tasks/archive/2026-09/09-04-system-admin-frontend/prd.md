# 独立系统管理员前端应用

## Goal

创建独立的 `system-admin-frontend/` 应用，运行在开发端口 5174、生产管理端口 127.0.0.1:8081。该应用只访问 `/admin-api`，使用独立认证、存储和路由，围绕“系统管理员 → 空间管理员 → 家族空间健康”提供监控和只读钻取。

## Requirements

### FE-F1：物理独立

- 独立 `package.json`、lockfile、Vite 配置、入口、router、Pinia stores、API client、类型、测试和 Dockerfile。
- 不 import 家庭 `frontend/` 的 router、store、组件、API client、types、CSS 运行时代码或构建产物。
- 家庭前端删除后台模块图；后台前端只代理 `/admin-api`，不代理家庭 `/api`。

### FE-F2：认证与会话

- 登录调用 `/admin-api/auth/login`，用户名+强密码；family token、家庭凭据、错误 audience/issuer 均拒绝。
- access token 仅内存；管理员 refresh key 使用独立命名空间，不能使用 `fg.refresh_token`。
- 首次改密、自身用户名/密码设置、refresh 轮换、logout、session expired 均回后台 origin，不跳家庭端。
- password_must_change 时只允许强制改密和必要认证动作；其它页面由 guard 拦截。
- 敏感访问会话票据只存内存，刷新页面即丢失并重新申请。

### FE-F3：后台信息架构

- 一级导航是“概览 / 空间管理员 / 异常队列 / 运营治理 / Agent 监控 / 读取审计”，主路径为管理员→其管理空间。
- 概览显示管理员数量、空间数量、健康摘要、异常数量和最近运营状态。
- 空间详情钻取成员/档案、关系/confirmed facts、治理积压、通知、统计、Agent/job 和审计时间线。
- 无管理员、双管理员、管理员锁定/删除、关系/投影异常单独展示，不提供自动修复。
- 列表支持分页、搜索、状态/时间筛选；禁止全库一次性渲染。

### FE-F4：字段与敏感详情

- 基础档案显示姓名、性别、生卒、简介、状态和头像鉴权缩略图；不显示高敏感档案。
- 关系显示结构化边和 confirmed facts；不显示证据原文、RawRelationInput、private note 或消息。
- 附件只显示安全元数据，不出现路径、原文或下载按钮。
- Agent 只显示状态、队列、耗时、错误码、重试、工具名、资源 ID 和后端二次脱敏诊断；不显示 prompt、消息、context、tool result、密钥或 token。
- 当前不存在的电话/邮箱/地址不伪造 UI；未来电话/邮箱详情需票据，精确地址等字段永不显示。

### FE-F5：访问会话与审批

- 敏感详情前弹理由表单，调用 access-session API 获取绑定单个 user/space、TTL 30 分钟的会话；不写 localStorage/sessionStorage。
- 详情请求带 `X-Admin-Access-Session`，处理过期/错目标/403 时清理内存票据并提示重新授权。
- approve 为唯一可直接执行的业务批准动作；reject 必须填写理由；两者均二次确认并展示终态不可改判提示。
- 不提供重置家庭 PIN、修改档案、删除/恢复、导出、附件下载和 Agent 控制按钮。

### FE-F6：可用性

- 覆盖 375px、桌面、键盘可达性、loading/empty/error/no-permission/no-store 状态。
- 五秒轮询 Agent/job 聚合状态，页面不可见时暂停，组件卸载时取消 timer/请求。
- 统一处理 401/403/429/网络失败，不在错误 UI 中显示 token、堆栈原文或后台内部密钥。

## Acceptance Criteria

- [ ] `system-admin-frontend/` 可独立安装、type-check、lint、test、build，开发端口为 5174。
- [ ] 家庭前端依赖图中不存在后台模块、路由、API、身份枚举或后台端口；后台前端依赖图中不存在家庭前端依赖。
- [ ] 管理员登录、首次改密、用户名/密码修改、refresh、logout 和过期跳转均只访问 `/admin-api` 和后台 origin。
- [ ] 后台主导航符合管理员→空间；分页、搜索、筛选、异常队列、关联钻取和 Agent 五秒轮询可用。
- [ ] 敏感详情必须先填写理由获取 30 分钟目标票据；票据不持久化；过期/错目标/无票据正确拒绝。
- [ ] 基础档案、关系事实、附件元数据和 Agent 脱敏诊断的 UI 字段符合后端白名单；无私人正文/认证秘密入口。
- [ ] approve/reject 是唯一业务写 UI，reject 理由必填、二次确认和终态提示完整；不存在其他高风险操作入口。
- [ ] 键盘、375px、加载/空/错误态和 build 门禁通过。

## Out of scope

- Admin API、数据库、JWT 签发和访问审计实现（子任务 1/2）。
- Docker 网络与家庭前端删除（子任务 4）。
- MFA、家庭注册、联系方式字段、新增档案字段、私人正文、批量导出和附件原文。

## Constraints

- 不把后台路由或组件塞回家庭 `frontend/`。
- 不在前端判断/伪造家庭权限；服务端 admin schema 和 token 是唯一授权来源。
- 不持久化 admin access session 或管理员 refresh 到家庭 key。
- 不覆盖其他任务对家庭前端的并行修改。
