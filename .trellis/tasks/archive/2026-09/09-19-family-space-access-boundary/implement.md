# 实施计划

## 规划与启动

- [x] 阅读最新 HANDOFF、旧 join authorization 任务、PFV/关系/错误规范。
- [x] 根据用户修订收敛规则：household 与 lineage 成员资格完全分离；家族树须另行申请并由该空间管理员审批；共同 household 复用。
- [x] 任务已启动并进入隔离 worktree（`../fg-09-19-family-space-access-boundary`）。

## 实现顺序

- [x] 1. 修正 `request_join_by_user`：目标只解析 target 拥有的 `household`；准入改为「与 target 同属至少一个 active lineage」；不再要求与目标空间成员的 confirmed 亲属链。
- [x] 2. 新增 `request_lineage_access` 命令与 `POST /spaces/lineage-access-requests`：家庭空间 active 成员对配对 lineage 发起独立 pending；要求已配对、本人尚非 lineage 成员、存在可审批的 active 管理员；只登记不激活。
- [x] 3. 在 `create_shared_household` 命令入口加写锁与「已有同一 household」复用检查；保留 ActionCard 的 `card_household_conflict` 复核。
- [x] 4. 删除已无人调用的 `shares_confirmed_kinship`（错误准入口径的残留原语）。
- [x] 5. 修正 dev seed：`朱元璋` 移出 `李氏家族` lineage 名册（只留 `李家` household）、`朱佛女` 加入 `朱氏皇族`；新增独立 confirmed `direct_sibling` 种子事实。
- [x] 6. 新增 0052 数据修正迁移：一次性删除旧清单授予的越权 lineage 成员行（insert-only 收敛不会删行）；无 schema 变更；downgrade 不逆向恢复并先履行父级拒绝合同。
- [x] 7. 前端：household 卡「家族树」入口在无访问权时改为独立申请（`requestLineageAccess`），不直接切空间。
- [x] 8. 定向回归：`tests/test_space_access_boundary.py`（读取 404、独立申请与自批拒绝、批准后可读、未配对 409、共同家庭复用/不合并/pending 不算）、`tests/test_seed_lineage_boundary_migration.py`、`tests/test_dev_seed.py`、`tests/test_m2c_flows.py`、`tests/test_notifications.py`、`frontend/src/views/__tests__/household-card.spec.ts`。

## 验证

已运行：

- `cd backend && .venv/bin/python -m pytest -q tests/` → 1789 passed, 3 skipped
- `cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .` → 通过
- `cd backend && .venv/bin/python -m mypy app` → 通过（207 files）
- `cd frontend && npm run lint && npm run type-check && npm test && npm run build` → 通过（773 tests）
- 隔离库演练：从生产 online backup 复制到临时 `DATA_DIR`，`alembic upgrade head` + 种子收敛后确认越权行消失、`朱佛女` 进入朱氏皇族、`direct_sibling` 事实存在、朱氏家族树含 31 节点且输出 `朱元璋—朱佛女` sibling 边；生产库保持 `0051`/92 成员/74 事实未变。

未运行（如实记录）：`./scripts/frontend-api-smoke.sh` 与生产部署/浏览器端到端验收——本任务未在本地起完整开发栈，交付后需按既有 runbook 部署并核对迁移版本。

## 回滚点

先回退前端申请入口与读取/命令授权，再回退 0052；不删除既有 membership/source fact，不用手工生产库修数据回滚。0052 的 downgrade 只回退版本，不恢复已删除的越权行。
