# 系统管理员治理路由实施计划

## 1. 实施前检查

- [ ] 读取本任务 PRD、design、研究文件和架构规范 §0.8。
- [ ] 检查当前工作树，特别是 `frontend/src/views/SystemAdminView.vue` 的 guest 删除未提交修改；不得覆盖或回退。
- [ ] 盘点现有 auth API、auth store、router guard、ChangePinView、SystemAdminShell、系统管理员 API 测试和前端 fixtures。
- [ ] 确认 `admin.py` 当前未被 `main.py` 注册，并记录为必须保持的安全基线。

## 2. 系统管理员登录页

- [ ] 实现 `/system-admin/login` 表单、提交状态、统一错误态和站内 redirect 过滤。
- [ ] 调用已有 `/api/auth/login`，使用共享 decoder，强制校验 `principal_type=system_admin`。
- [ ] family_user/未知主体响应不得写入后台 auth 状态；登录成功后按 `pin_must_change` 进入首改或后台。
- [ ] 添加登录页、主体错误、错误文案和已登录访问登录页的前端测试。

## 3. PIN、refresh、logout 和过期回跳

- [ ] 调整 ChangePinView，使 system_admin 完成后回 system-admin 入口/后台，family_user 维持原路径。
- [ ] 调整 auth store 的 sessionExpiredRedirect，根据当前 principal 选择入口并清理对应缓存。
- [ ] 验证 refresh 不改变 principal_type，logout 撤销正确 refresh session。
- [ ] 在 SystemAdminShell 增加 logout 控件和测试；确认不引入家庭导航或家庭数据请求。

## 4. 治理 API 边界回归

- [ ] 补 system-admin token、family-user token、无 token、错误 principal_type 的路由测试。
- [ ] 补 schema 字段白名单和未知 space_id 防枚举测试。
- [ ] 补 `/me`、`/spaces` 拒绝 system_admin 的反向隔离测试。
- [ ] 补 route registration 断言，确认旧 `admin.py` 没有重新挂载。
- [ ] 将家庭 PIN 重置、档案修改、custody transfer、claim dispute、data-rights 标注为后续 break-glass，不写实现。

## 5. 验证命令

后端定向：

```bash
cd backend && .venv/bin/python -m pytest -q tests/test_system_admin_boundary.py tests/test_m4b_admin.py tests/test_manager_applications.py
```

后端质量门禁：

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m mypy app && .venv/bin/python -m pytest -q
```

前端定向与质量门禁：

```bash
cd frontend && npm test -- --run src/views/__tests__/system-admin.spec.ts src/router/__tests__/guard.spec.ts src/stores/__tests__/auth.spec.ts
cd frontend && npm run type-check && npm run lint && npm test && npm run build
```

## 6. 停止点与交付检查

- [ ] 发现登录 API 不能安全区分主体：停止，不在前端猜测或复用家庭权限。
- [ ] 发现旧 admin.py 需要家庭数据访问才能满足需求：停止，拆成独立 break-glass 任务。
- [ ] 发现 guest 删除 WIP 被覆盖：停止并恢复用户修改后再继续。
- [ ] 发现任何治理 schema 泄露家庭档案字段：停止开放该路由并补字段白名单。
- [ ] PRD/design/implement 与实际改动一致，manifest 已有真实研究条目。
- [ ] 仅在用户明确批准最终规划摘要后执行 `task.py start`；当前阶段保持 planning。
