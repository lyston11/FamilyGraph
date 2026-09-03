# PersonalFamilyView 实施计划

## 实施顺序

1. **领域合同与迁移**
   - 固定 bridge/view 状态、reason、version、ETag、cursor 和 feature flag 常量。
   - 新增 PersonalFamilyView snapshot/node/edge 与最小 Bridge ORM；FK、CHECK、唯一索引、必要查询索引全部落 Alembic。
   - 增加 schema round-trip/空库迁移测试；旧 `/graph/me` 回归保持不变。
   - 回滚点：仅新增表和关闭 flag，不触碰旧事实表。

2. **桥接领域命令**
   - 实现创建 pending、双方本人 CAS consent、本人 revoke/expire、管理员通知。
   - 服务端从认证身份解析主体；禁止客户端指定 viewer、root 或替代同意人。
   - 校验双方 LineageSpace、anchor、claimed、confirmed 依据、scope 和重复 active bridge。
   - 测试管理员无否决权、未认领不生效、跨空间越权、重放/并发 consent、撤销失效。

3. **PersonalFamilyView 计算服务**
   - 将关系图加载、VisibilityPolicy、桥接最小导出、resolver 主路径和称谓组合为单一 service。
   - 计算节点/边纳入理由、证据 id/revision、授权依据、policy/computation version。
   - 实现 snapshot version 切换、input hash 幂等和失败状态；不写 SourceFact、不读 private 数据、不把旧投影当真源。
   - 测试 Scenario A–F 及 `none`/`lineage_summary` 的防存在性泄露。

4. **Steward 触发与失效**
   - 扩展 DomainEvent 到受影响 viewer/root/space 的 fan-out，复用现有 StewardJob/checkpoint/lease，不引入 generic `AgentJob(kind=steward)`。
   - 权限收紧事务中标 stale/invalidated；普通事实变化 queued/running/current；失败可见且可重试。
   - 测试重复事件、worker 重试、空间隔离、撤权后旧行不可读和只重算受影响视图。

5. **后端 API**
   - 新增 `GET /api/personal-family-view?space_id=`，当前认证账号固定 viewer/root。
   - 返回状态、版本、快照节点/边、主/替代路径、称谓、安全 reason、截断/cursor；ETag/304 与统一错误 envelope。
   - 复核 active membership/bridge/policy 后才序列化；无权上下文安全 404/disabled。
   - API/IDOR/ETag/分页和旧 graph endpoint 兼容测试。

6. **前端契约与 store**
   - 在 `types/api.ts` 增加与后端一致的枚举、联合类型和 runtime guard。
   - 新增 API 模块与 `personalFamilyView` Pinia store，按空间缓存、切换清理、401/logout 清理、非乐观更新。
   - 首版接入最小状态展示或仅保留可消费契约，不能在画布组件内请求或拼接数据。
   - 测试 schema guard、空间切换、stale/failed、masked/blocked 和服务端状态重载。

7. **推荐边界联调**
   - 为 `09-01-new-user-family-recommendations` 提供只读的 current PersonalFamilyView service/schema；推荐不能反向写视图或权限。
   - 仅加入合同测试，不实现该任务的触发点和推荐 UI。

## 文件范围预期

后端：

- `backend/app/models/personal_family_view.py`、`backend/app/models/personal_family_bridge.py`
- `backend/app/schemas/personal_family_view.py`、`backend/app/schemas/personal_family_bridge.py`
- `backend/app/services/personal_family_view.py`、`backend/app/services/personal_family_bridge.py`
- `backend/app/api/personal_family_view.py`、必要的 bridge API/deps 注册
- `backend/app/services/domain_events.py`、`steward.py`、`main.py` 或 config flag
- 新 Alembic migration 与 backend tests

前端：

- `frontend/src/api/personalFamilyView.ts`
- `frontend/src/stores/personalFamilyView.ts`
- `frontend/src/types/api.ts`
- 对应 store/API/schema 测试；只有需要展示状态时才触及 view/component

规范/工件：

- 任务 `prd.md`、`design.md`、`implement.md`
- 若实现中发现跨任务通用契约，更新对应 `.trellis/spec/`，不把新任务代码混入无关任务。

## 验证命令

窄范围：

```bash
cd backend && .venv/bin/python -m pytest -q tests/test_personal_family_view.py tests/test_personal_family_bridge.py
cd backend && .venv/bin/python -m mypy app/services/personal_family_view.py app/services/personal_family_bridge.py
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
cd frontend && npm run type-check && npm run lint
```

全范围：

```bash
cd backend && .venv/bin/python -m pytest -q --deselect tests/test_ownership_transfer.py::test_concurrent_double_accept_single_winner
cd backend && .venv/bin/python -m mypy app
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
cd frontend && npm run type-check && npm run lint && npm test && npm run build
```

迁移：使用临时 `DATA_DIR`，执行 `alembic upgrade head → downgrade <previous revision> → upgrade head`，不得污染默认 `backend/data/db/app.db`。

安全回归：

- 跨空间/无关分量/管理员身份不能枚举节点。
- 撤权、桥接 revoke、policy 收紧后下一次读取不返回旧内容。
- proposed/pending/disputed、private Session/Memory、模型文本不能进入正式投影。
- 同一视图重复事件和重算不产生重复行或版本竞争错误。
- 后端 Pydantic、前端 TypeScript/runtime guard、Store 和 API payload 字段一致。

## 最终开始前检查点

- [ ] PRD 已完成收敛，无阻塞产品决策。
- [ ] design.md/implement.md 与 PRD 的 bridge 语义一致：双方本人同意，管理员仅通知。
- [ ] `task.py validate 09-01-personal-family-view` 通过，manifest 不再是 seed-only。
- [ ] 明确与其他 active 任务的文件交叉范围，保留 unrelated WIP。
- [ ] 用户在最终规划摘要后明确批准，才执行 `task.py start`。
