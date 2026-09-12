# Implement — Steward 个人亲属称谓计算与家族树投影闭环

## 开始条件

- [x] 阅读本任务 PRD/设计、父任务工件、backend/frontend 现行 spec。
- [x] 确认不覆盖其他未提交改动；使用临时 DATA_DIR。
- [x] 评审本计划并执行 `task.py start`；产品代码变更前完成边界核对。

## 有序步骤

1. [x] 建立截图关系的隔离 fixture，逐层断言 `Dm-Sf`、词典 seed、`resolve_term_or_structural`、PFV edge 和 API payload；定位实际落到 structural fallback 的原因。
2. [x] 修复最窄的断点：本轮确认运行时代码已有正确实现；补充旧 v1/graph 投影不得继续提供结构快照的回归，避免复制逻辑或引入前端推断。
3. [x] 增加个人/空间/locale/system 优先级、无词条 fallback、词典版本变化和旧投影重算回归（既有 terms/PFV 回归 + 本任务黄金用例）。
4. [x] 将黄金称谓验证纳入现有真实 API + maintenance 自动 tick E2E；确认 model assist 隔离。真实 Provider 证据仍单独分级。
5. [x] 运行 backend 定向/全量测试、frontend 定向/全量测试、lint/type-check/build，并记录证据等级。
6. [x] 更新父任务 findings、relationship intelligence/PFV/Steward spec 和 release evidence；通过 `task.py validate` 后完成最终交接。

## 重点文件候选

- `backend/app/services/relationship_resolver.py`
- `backend/app/services/terms.py`
- `backend/app/services/personal_family_view.py`
- `backend/app/services/steward.py`
- `backend/app/services/domain_events.py`
- `backend/app/models/term_registry.py`
- `backend/tests/test_relationship_resolver.py`
- `backend/tests/test_terms.py`
- `backend/tests/test_personal_family_view.py`
- `backend/scripts/steward_e2e.py`
- `frontend/src/composables/useFamilyTreeCanvas.ts`
- `frontend/src/views/FamilyTreeView.vue`
- `frontend/src/components/canvas/RelationshipDetailPanel.vue`

## 验证命令

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_relationship_resolver.py tests/test_terms.py tests/test_personal_family_view.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m mypy app
.venv/bin/python scripts/steward_e2e.py

cd ../frontend
npm run type-check
npm run lint
npm test -- --run
npm run build
```

真实 Provider 联调遵循 `release-observability` 的 Compose/runbook 约束，不能把 fake transport 结果标为真实 Provider。
