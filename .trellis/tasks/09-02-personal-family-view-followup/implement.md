# PersonalFamilyView 遗留投影实施计划

## 1. 实施前检查

- [ ] 读取本任务 `prd.md`、`design.md`、研究文件和注入的架构/数据库规范。
- [ ] 检查工作树，保留 `09-01-remove-guest-role` 及其他 unrelated WIP，不使用 reset/checkout 覆盖修改。
- [ ] 盘点当前 Alembic head、PersonalFamilyView 表/服务、DomainEvent、ActionCard、空间成员和现有前端合同。
- [ ] 固定旧 `/stats` 调用方兼容策略，确认新的 `space_id` 合同不会静默改变旧响应。

## 2. 服务端合同与 schema

- [ ] 新增/补齐 household card 专用 schema、查询 service 和 `/household-card` 路由。
- [ ] 新增空间统计 schema、授权聚合 service 和 `stats?space_id=` 路由分支；旧无空间统计保留明确兼容语义。
- [ ] 盘点通知持久化能力；若无可用投影表，新增最小 Notification 模型/迁移，包含收件人、空间、领域状态、ActionCard 引用、脱敏 payload 和 read_at。
- [ ] 所有响应使用显式字段白名单，禁止 ORM 直接序列化。
- 回滚点：先只增加 schema/query tests 和关闭 feature flag，不改旧 graph 或旧统计数据源。

## 3. 授权与版本

- [ ] 在三个 GET 端点统一接入 family_user 认证、space 授权、PersonalFamilyView 绑定和当前 VisibilityPolicy 复核。
- [ ] 实现安全 404/未就绪状态，阻止空间存在性、隐藏对象数量和撤权前内容泄露。
- [ ] 统一生成合同版本、view_version、policy_version 参与的 ETag，并实现 If-None-Match/304。
- [ ] 将 membership revoke、Bridge revoke/expire、policy 收紧、空间删除接入投影失效/版本变化。

## 4. 统计与通知命令

- [ ] 以安全 PersonalFamilyView snapshot 聚合 node/edge/member/relation 计数和服务端待办计数。
- [ ] 固定 non-current 状态、上一份安全快照和撤权后的空/未就绪响应口径。
- [ ] 实现通知按 `recipient_account_id + space_id` 查询和脱敏投影。
- [ ] 实现单条 read/read-all 的短事务条件更新；只修改 read_at，不触发领域命令。
- [ ] 为 action-card 引用、Bridge 管理员通知和幂等通知生成增加测试。

## 5. 前端联调与回归

- [ ] 让现有 household、spaceStats、notifications API/store 接入真实端点，保持 decoder 和安全降级。
- [ ] 验证空间切换清理、epoch 丢弃迟到响应、401/logout 清理和 ETag 快照复用。
- [ ] 验证 none/masked/lineage_summary、stale/failed、候选关系和推荐 current 边界。
- [ ] 搜索并证明 HouseholdCardView、StatsView、NotificationsView 未新增旧接口 fallback。

## 6. 验证命令

定向后端：

```bash
cd backend && .venv/bin/python -m pytest -q tests/test_personal_family_view.py tests/test_personal_family_view_api.py tests/test_household_card.py tests/test_space_stats.py tests/test_notifications.py
```

后端质量门禁：

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m mypy app && .venv/bin/python -m pytest -q
```

前端质量门禁：

```bash
cd frontend && npm run type-check && npm run lint && npm test && npm run build
```

迁移验证使用临时数据目录执行 `upgrade head → downgrade <previous revision> → upgrade head`，不得污染默认数据库。

## 7. 开始前和交付检查点

- [ ] PRD 无 TBD、无阻塞产品决策；design/implement 与合同一致。
- [ ] implement.jsonl/check.jsonl 已包含真实 spec/research 条目。
- [ ] 明确与其他 active 任务的交叉文件范围，保留 unrelated WIP。
- [ ] 所有定向、质量门禁和安全回归结果已记录到 notes/check manifest。
- [ ] 仅在用户明确批准最终规划摘要后执行 `task.py start`；本阶段不得 start。

## 风险与停止点

- 发现旧统计调用方依赖无空间字段：停止并固定兼容响应，不直接替换。
- 发现通知领域引用无法安全映射到当前收件人/空间：停止，不返回宽泛通知，先补领域映射设计。
- 发现撤权后仍可从旧投影得到数据：停止开放端点，先修查询层授权复核。
- 发现 Alembic 存量数据无法安全迁移：停止，不静默删除或转换通知/领域数据。
