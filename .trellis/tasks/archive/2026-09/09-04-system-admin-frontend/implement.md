# 独立系统管理员前端应用：实施计划

## 1. Preconditions

- [ ] 子任务 1 admin auth API、token issuer/audience 和 listener 已验收。
- [ ] 子任务 2 admin read schemas、分页 envelope、access-session 和审批 API 合同已锁定。
- [ ] 读取 frontend spec（directory/component/state/type/quality）和父任务设计。
- [ ] 复核家庭 frontend 的并行改动，尤其不删除/覆盖其他任务文件。

## 2. Ordered implementation

- [ ] 初始化独立 `system-admin-frontend/` 项目和最小依赖；建立自己的 lockfile、Vite、tsconfig、入口、CSS 和 Dockerfile。
- [ ] 实现 admin API client、token reader、refresh rotation、logout、session expired 和独立 storage keys。
- [ ] 实现 router/guard：登录、强制改密、管理员→空间路由、异常/运营/Agent/审计页面；未知路径统一 admin 404。
- [ ] 实现登录与自身凭据设置，不暴露家庭登录/主体文案。
- [ ] 实现 AdminShell 和管理员→空间主导航；不包含家庭壳、家庭 stores 或 `/api` client。
- [ ] 实现 overview/space-admin/space detail/异常队列与分页钻取。
- [ ] 实现基础档案、头像鉴权缩略图、关系/confirmed facts、附件元数据 UI 白名单。
- [ ] 实现 access-session 理由 modal、30 分钟内存票据、目标绑定、no-store 和 403 重新授权。
- [ ] 实现运营治理只读页、申请 approve/reject 唯一写例外和二次确认。
- [ ] 实现 Agent/job 五秒轮询、可见性暂停、二次脱敏诊断 UI 和审计查看。
- [ ] 添加全量组件、路由、store、响应式、无障碍和模块图测试。

## 3. Verification

```bash
cd system-admin-frontend
npm ci
npm run type-check
npm run lint
npm test -- --run
npm run build
```

```bash
# 家庭项目不含后台依赖/字符串（由子任务 4 完成）
cd frontend
npm run type-check && npm run lint && npm test -- --run && npm run build
```

## 4. Stop points

- 如果必须 import 家庭 frontend 才能构建后台，停止并复制必要的安全基础设施到独立项目。
- 如果 admin client 访问 `/api` 或家庭 refresh key，停止。
- 如果 UI 渲染原始 Agent error、消息、prompt、token、密钥、附件原文或证据原文，停止。
- 如果敏感详情不经过后端 access-session，停止。
- 如果出现除 approve/reject 外的高风险写按钮，停止。
- 如果页面提供后台入口或 URL 给家庭项目，停止。

## 5. Rollback

- admin frontend 可单独停用，家庭前端不因其不可用而改变路由或显示后台提示。
- 前端 schema 不兼容时锁定对应 panel 为安全错误/空态，不降级到家庭 API。
- access-session 或监控 UI 有漏洞时隐藏敏感 panel，保留安全 overview。

## 6. Handoff

交付独立项目目录、路由清单、存储 key、API endpoint 使用表、页面/字段白名单、测试结果和部署参数，供子任务 4 构建 admin web 并清理家庭端。
