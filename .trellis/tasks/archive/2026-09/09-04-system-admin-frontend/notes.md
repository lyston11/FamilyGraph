# 子任务 3 决策与实现笔记

- 后台前端必须是独立 `system-admin-frontend/`，不是家庭项目里的第二套入口；家庭 bundle 从模块图上完全不包含后台。
- 后台只调用 `/admin-api`，使用独立 token/storage；不通过家庭 `/api` 或家庭 auth store 兜底。
- 主导航是管理员→空间，而不是用户/表格数据库浏览器；管理员异常空间单独告警。
- 敏感详情采用单 user/space 绑定、30 分钟内存访问会话；理由不写 URL 或持久存储，响应 no-store。
- 首版只保留空间管理员申请 approve/reject 写 UI；approve 可无理由，reject 必须理由，均二次确认。
- Agent 错误可显示完整诊断信息的安全投影，但绝不将服务端原始错误、prompt、消息、上下文、工具结果或密钥送进浏览器。
- 五秒轮询仅用于 Agent/job 运行元数据，页面不可见暂停，避免后台监控制造持续负载。
