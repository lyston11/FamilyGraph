# 新认领用户的个人家族初始化与亲属推荐：实施计划

## 实施顺序

1. **触发合同（后端命令层）**
   - `commands/members.py` `change_own_pin` 强制认领分支补发 `account.claimed`（payload `via: "pin_change"`）；`claim_and_confirm_own_identity` 既有事件补 `via` 字段。
   - 测试：两条路径事件断言（并入 `test_steward.py` 触发用例或 `test_identity_checklist.py` 邻近文件）。
   - 回滚点：独立提交，仅事件添加，可单独回退。

2. **认领事件 fan-out 收窄**
   - `steward.schedule_steward_job_for_event` 对 `account.claimed` 特判为"认领者 active 空间"；`_cause_for_event` 映射 `cause="claim"`。
   - 测试：每个 active 空间至多一个作业、无关空间不被触碰（现状全空间 fan-out 的回归防护）、重复事件幂等。
   - 回滚点：独立提交。

3. **推荐派生服务**
   - `services/family_recommendations.py`：血亲条目（spouse/partner 路径排除 + 冷却过滤 + 视图字段复用）、待确认线索（proposed facts）、确定性排序。
   - `steward.py` `PROJECTION_KEY_PREFIXES` 追加 `kinship_recommendation_dismissed:`；dismiss 写入函数。
   - 测试：design §7 排除矩阵与隐私断言。
   - 回滚点：服务层独立，未接 API 前不可达。

4. **API 端点**
   - `GET /api/family-recommendations?space_id=` 与 `POST /api/family-recommendations/dismiss`；schemas + 统一错误 envelope + 安全 404。
   - 测试：授权矩阵、stale/failed 空集 + 状态、只读消费（视图版本不变）。
   - 回滚点：路由独立。

5. **前端合同与最小展示**
   - types/api.ts 类型 + runtime guard → api 模块 → Pinia store（空间缓存/epoch/清理）→ 最小列表组件（非画布、候选不渲染为确认事实、stale 状态文案、"不再推荐"入口）。
   - 测试：decoder、store 清理、条件重取、渲染断言。
   - 回滚点：前端独立提交。

6. **收尾**
   - 若推荐边界语义需要固化，走 `trellis-update-spec` 更新 `.trellis/spec/backend/steward-action-card.md`（推荐投影与 ST-5 矩阵的关系说明），不改变既有卡片合同。

## 文件范围预期

后端：

- `backend/app/commands/members.py`（补发事件）
- `backend/app/services/steward.py`（fan-out 特判 + cause 映射 + 投影前缀白名单）
- `backend/app/services/family_recommendations.py`（新）
- `backend/app/api/family_recommendations.py`、`backend/app/schemas/family_recommendations.py`（新）
- `backend/app/main.py`（路由注册）
- `backend/tests/test_family_recommendations.py`（新）+ `test_steward.py`/`test_identity_checklist.py` 触发用例

前端：

- `frontend/src/api/familyRecommendations.ts`、`frontend/src/stores/familyRecommendations.ts`（新）
- `frontend/src/types/api.ts`
- 最小展示组件 + 挂载视图；对应 spec 测试

不触碰：`personal_family_view.py` 服务逻辑（只读消费）、迁移（无 schema 变更）、`recommendation_matrix.py`（ST-5 语义隔离）、ActionCard 状态机。

## 验证命令

窄范围：

```bash
cd backend && .venv/bin/python -m pytest -q tests/test_family_recommendations.py tests/test_steward.py tests/test_personal_family_view.py
cd backend && .venv/bin/python -m mypy app/services/family_recommendations.py app/api/family_recommendations.py
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
cd frontend && npm run type-check && npm run lint
```

全范围：

```bash
cd backend && .venv/bin/python -m pytest -q --deselect tests/test_ownership_transfer.py::test_concurrent_double_accept_single_winner
cd backend && .venv/bin/python -m mypy app
cd frontend && npm run type-check && npm run lint && npm test && npm run build
```

## 安全回归

- 无权空间统一 404 防枚举；`none`/masked 目标零泄漏（含 path_summary、proposed 线索目标）。
- 推荐链路不产生任何写放大：SourceFact/SpaceMember/视图行/卡片零写入断言。
- dismissed 键只按 `(space_id, account_id, target)` 生效，跨账号/跨空间不互串。
- `change_own_pin` 非强制改 PIN（普通改密）不触发认领事件。

## 最终开始前检查点

- [ ] PRD/design/implement 三件套齐备且相互一致。
- [ ] `task.py validate 09-01-new-user-family-recommendations` 通过，manifest 非 seed-only。
- [ ] 触发点决策（managed→claimed）与 notes.md 决策记录一致。
- [ ] 用户在最终规划摘要后明确批准，才执行 `task.py start`。
